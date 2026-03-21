"""
Risk Manager — position sizing, portfolio-level risk controls, and correlation checks.
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Portfolio-level risk management.
    Enforces position limits, correlation constraints, and drawdown halts.
    """

    def __init__(self, capital: float = config.BACKTEST_INITIAL_CAPITAL):
        self.initial_capital = capital
        self.current_capital = capital
        self.peak_capital = capital
        self.open_positions: dict[str, dict] = {}
        self.trade_history: list[dict] = []
        self.halted = False

    @property
    def current_drawdown(self) -> float:
        if self.peak_capital == 0:
            return 0
        return (self.current_capital - self.peak_capital) / self.peak_capital

    def update_capital(self, new_capital: float):
        self.current_capital = new_capital
        self.peak_capital = max(self.peak_capital, new_capital)

        # Check drawdown halt
        if self.current_drawdown < -config.MAX_DRAWDOWN_HALT_PCT:
            self.halted = True
            logger.warning(
                f"⚠️ TRADING HALTED — drawdown {self.current_drawdown:.1%} "
                f"exceeds limit {config.MAX_DRAWDOWN_HALT_PCT:.1%}"
            )

    def can_trade(self) -> tuple[bool, str]:
        """Check if we're allowed to take a new trade."""
        if self.halted:
            return False, "Trading halted due to max drawdown"

        total_exposure = sum(p.get("size_pct", 0) for p in self.open_positions.values())
        if total_exposure >= config.MAX_PORTFOLIO_RISK_PCT:
            return False, f"Max portfolio exposure reached ({total_exposure:.1%})"

        return True, "OK"

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        signal_confidence: float = 1.0,
    ) -> dict:
        """
        Calculate position size using fixed-fractional risk model.
        Adjusts for signal confidence.
        """
        can, reason = self.can_trade()
        if not can:
            return {"shares": 0, "reason": reason}

        # Base position size (% of capital)
        base_pct = config.MAX_POSITION_SIZE_PCT

        # Scale by confidence (0.5 to 1.0 range)
        confidence_scalar = max(0.5, min(1.0, signal_confidence))
        adjusted_pct = base_pct * confidence_scalar

        # Risk per share
        risk_per_share = abs(entry_price - stop_loss_price)
        if risk_per_share == 0:
            risk_per_share = entry_price * config.STOP_LOSS_PCT

        # Total $ to risk
        risk_budget = self.current_capital * adjusted_pct

        # Number of shares
        shares = int(risk_budget / entry_price)
        dollar_value = shares * entry_price

        return {
            "shares": shares,
            "dollar_value": dollar_value,
            "position_pct": dollar_value / self.current_capital if self.current_capital > 0 else 0,
            "risk_per_share": risk_per_share,
            "total_risk": shares * risk_per_share,
            "confidence_scalar": confidence_scalar,
        }

    def check_correlation(
        self,
        symbol: str,
        data: dict[str, pd.DataFrame],
        threshold: float = 0.7,
    ) -> tuple[bool, list[str]]:
        """
        Check if a new position would be too correlated with existing positions.
        Returns (is_ok, list of correlated symbols).
        """
        if symbol not in data:
            return True, []

        correlated = []
        new_returns = data[symbol]["Close"].pct_change().dropna()

        for open_sym in self.open_positions:
            if open_sym not in data:
                continue

            open_returns = data[open_sym]["Close"].pct_change().dropna()
            common = new_returns.index.intersection(open_returns.index)

            if len(common) < 30:
                continue

            corr = new_returns.loc[common].corr(open_returns.loc[common])
            if abs(corr) > threshold:
                correlated.append(open_sym)

        if len(correlated) >= config.MAX_CORRELATED_POSITIONS:
            return False, correlated

        return True, correlated

    def register_position(self, symbol: str, size_pct: float, direction: int):
        self.open_positions[symbol] = {
            "size_pct": size_pct,
            "direction": direction,
        }

    def close_position(self, symbol: str, pnl: float):
        if symbol in self.open_positions:
            del self.open_positions[symbol]
        self.update_capital(self.current_capital + pnl)
        self.trade_history.append({"symbol": symbol, "pnl": pnl})

    def get_portfolio_summary(self) -> dict:
        return {
            "capital": self.current_capital,
            "drawdown": self.current_drawdown,
            "open_positions": len(self.open_positions),
            "total_exposure": sum(p["size_pct"] for p in self.open_positions.values()),
            "halted": self.halted,
            "total_trades": len(self.trade_history),
            "total_pnl": sum(t["pnl"] for t in self.trade_history),
        }
