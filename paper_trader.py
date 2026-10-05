"""
Paper Trading — Alpaca integration for simulated live execution.
Bridges the gap between backtesting and real money.
"""

import logging
import math
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
    if not trading_allowed():
        logger.warning("Paper trading is disabled in config")
        return None

    client = _get_client()
    if client is None:
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
    if not trading_allowed():
        logger.warning("close_position blocked: trading is disabled")
        return None
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


def trading_allowed() -> bool:
    return config.TRADING_MODE == "paper" and bool(config.PAPER_TRADE_ENABLED)


MIN_PRICE = 1.0          # prices below this are treated as bad data
MAX_ORDER_QTY = 10_000   # absolute share ceiling regardless of config/price


def _symbol_rejected(symbol: str) -> bool:
    s = str(symbol).upper()
    return (not s) or "=" in s or "^" in s or s.endswith("-USD")


def execute_signal(
    symbol: str, direction: int, confidence: float = 1.0,
    current_price: float = 0.0,
) -> Optional[dict]:
    """
    Execute a trading signal via paper trading (emergency interlocks, WS0.4).
    direction: 1 = buy, -1 = sell (close/reduce a long only; shorts are off).
    Fails closed: any lookup error or invalid input -> no order.
    """
    if not trading_allowed():
        logger.info(f"dry-run: {symbol} dir={direction} (TRADING_MODE={config.TRADING_MODE}, "
                    f"PAPER_TRADE_ENABLED={config.PAPER_TRADE_ENABLED}) - no order sent")
        return None

    if _symbol_rejected(symbol):
        logger.warning(f"Rejected non-equity symbol for broker: {symbol}")
        return None
    if direction not in (1, -1):
        return None
    try:
        confidence = float(confidence)
        current_price = float(current_price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(confidence) or not math.isfinite(current_price) or current_price < MIN_PRICE:
        logger.warning(f"Rejected invalid confidence/price for {symbol}")
        return None
    confidence = min(1.0, max(0.0, confidence))

    client = _get_client()
    if client is None:
        return None
    try:
        acct = client.get_account()
        equity = float(acct.equity)
        positions = client.get_all_positions()
        open_orders = client.get_orders()
    except Exception as e:
        logger.error(f"Broker lookup failed, failing closed: {e}")
        return None
    if not math.isfinite(equity) or equity <= 0:
        return None

    held = 0.0
    for p in positions:
        if p.symbol == symbol and "short" not in str(p.side).lower():
            held += float(p.qty)

    max_value = min(config.PAPER_TRADE_MAX_ORDER_VALUE, equity * config.MAX_POSITION_SIZE_PCT)
    qty = int(min(MAX_ORDER_QTY, math.floor(max_value * confidence / current_price)))
    if qty <= 0:
        logger.info(f"Rejected {symbol}: computed qty 0")
        return None

    if direction == 1:
        if held > 0:
            logger.info(f"Skip BUY {symbol}: long position already held")
            return None
        for o in open_orders:
            if o.symbol == symbol and "buy" in str(o.side).lower():
                logger.info(f"Skip BUY {symbol}: open buy order exists")
                return None
        side = "buy"
    else:
        if held <= 0:
            logger.info(f"Rejected SELL {symbol}: no long position (shorts off)")
            return None
        pending_sells = 0.0
        for o in open_orders:
            if o.symbol == symbol and "sell" in str(o.side).lower():
                try:
                    pending_sells += float(o.qty)
                except (TypeError, ValueError):
                    logger.info(f"Rejected SELL {symbol}: open sell order with unknown qty")
                    return None
        available = held - pending_sells
        if available <= 0:
            logger.info(f"Rejected SELL {symbol}: open sell orders already cover the position")
            return None
        qty = int(min(qty, math.floor(available)))
        if qty <= 0:
            return None
        side = "sell"

    return submit_order(symbol, qty, side)
