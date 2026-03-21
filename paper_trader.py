"""
Paper Trading — Alpaca integration for simulated live execution.
Bridges the gap between backtesting and real money.
"""

import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)

# Lazy import — only needed if paper trading is enabled
_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    if not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
        logger.error("Alpaca credentials not configured")
        return None

    try:
        from alpaca.trading.client import TradingClient
        _client = TradingClient(
            config.ALPACA_API_KEY,
            config.ALPACA_SECRET_KEY,
            paper=True,
        )
        logger.info("Alpaca paper trading client initialized")
        return _client
    except ImportError:
        logger.error("alpaca-py not installed — run: pip install alpaca-py")
        return None
    except Exception as e:
        logger.error(f"Alpaca client init failed: {e}")
        return None


def get_account() -> Optional[dict]:
    """Get paper trading account info."""
    client = _get_client()
    if client is None:
        return None
    try:
        acct = client.get_account()
        return {
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "portfolio_value": float(acct.portfolio_value),
            "day_trade_count": acct.daytrade_count,
        }
    except Exception as e:
        logger.error(f"Get account failed: {e}")
        return None


def submit_order(
    symbol: str,
    qty: int,
    side: str,  # "buy" or "sell"
    order_type: str = "market",
    time_in_force: str = "day",
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
) -> Optional[dict]:
    """
    Submit a paper trade order to Alpaca.
    """
    client = _get_client()
    if client is None:
        return None

    if not config.PAPER_TRADE_ENABLED:
        logger.warning("Paper trading is disabled in config")
        return None

    try:
        from alpaca.trading.requests import (
            MarketOrderRequest, LimitOrderRequest, StopLimitOrderRequest
        )
        from alpaca.trading.enums import OrderSide, TimeInForce

        side_enum = OrderSide.BUY if side == "buy" else OrderSide.SELL
        tif = TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC

        if order_type == "market":
            req = MarketOrderRequest(
                symbol=symbol, qty=qty, side=side_enum, time_in_force=tif
            )
        elif order_type == "limit" and limit_price:
            req = LimitOrderRequest(
                symbol=symbol, qty=qty, side=side_enum,
                time_in_force=tif, limit_price=limit_price
            )
        else:
            req = MarketOrderRequest(
                symbol=symbol, qty=qty, side=side_enum, time_in_force=tif
            )

        order = client.submit_order(req)
        logger.info(f"📝 Paper order submitted: {side} {qty} {symbol} ({order_type})")

        return {
            "id": str(order.id),
            "symbol": order.symbol,
            "qty": str(order.qty),
            "side": str(order.side),
            "type": str(order.type),
            "status": str(order.status),
        }

    except Exception as e:
        logger.error(f"Order submission failed: {e}")
        return None


def get_positions() -> list[dict]:
    """Get all open paper trading positions."""
    client = _get_client()
    if client is None:
        return []
    try:
        positions = client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "side": str(p.side),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
                "current_price": float(p.current_price),
                "avg_entry_price": float(p.avg_entry_price),
            }
            for p in positions
        ]
    except Exception as e:
        logger.error(f"Get positions failed: {e}")
        return []


def close_position(symbol: str) -> Optional[dict]:
    """Close a specific paper trading position."""
    client = _get_client()
    if client is None:
        return None
    try:
        order = client.close_position(symbol)
        logger.info(f"📝 Closed position: {symbol}")
        return {"symbol": symbol, "status": "closed"}
    except Exception as e:
        logger.error(f"Close position failed for {symbol}: {e}")
        return None


def execute_signal(
    symbol: str, direction: int, confidence: float = 1.0,
    current_price: float = 0.0,
) -> Optional[dict]:
    """
    Execute a trading signal via paper trading.
    direction: 1 = buy, -1 = sell/short
    """
    if not config.PAPER_TRADE_ENABLED:
        return None

    acct = get_account()
    if acct is None:
        return None

    # Position sizing
    max_value = min(
        config.PAPER_TRADE_MAX_ORDER_VALUE,
        acct["buying_power"] * config.MAX_POSITION_SIZE_PCT
    )

    if current_price <= 0:
        return None

    qty = max(1, int(max_value * max(0.5, confidence) / current_price))
    side = "buy" if direction == 1 else "sell"

    return submit_order(symbol, qty, side)
