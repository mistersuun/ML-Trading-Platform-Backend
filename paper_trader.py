"""
DEPRECATED shim (WS1.3). Orders go through execution.submit_intent, the only path to a broker.

Kept only so main.py keeps working until the Integrate worker rewires it:
  * execute_signal() builds an OrderIntent and calls execution.submit_intent. Callers that do not
    supply ATR / validation_status get BUYs rejected ('atr_invalid') and validation 'unvalidated',
    i.e. nothing is ever sent in Phase 1.
  * get_account() / get_positions() are lenient display helpers (they swallow errors and return
    None / []) built on execution.AlpacaBroker; no raw TradingClient is exposed.
  * submit_order() / close_position() no longer talk to the broker at all.
This module must not import brokers.* (tests/test_ast_boundaries.py); it goes through execution.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import config
import execution
from risk_manager import RiskManager

logger = logging.getLogger(__name__)

_client = None  # alpaca TradingClient (or a FakeBroker in tests); only ever handed to AlpacaBroker


def _get_broker():
    """The Broker adapter used for every display read. No raw client is ever exposed to callers."""
    return execution.AlpacaBroker(_client)  # builds the paper=True client lazily when _client is None


def trading_allowed() -> bool:
    return config.TRADING_MODE == "paper" and bool(config.PAPER_TRADE_ENABLED)


def get_account() -> Optional[dict]:
    """Paper account info for display (None on any error)."""
    try:
        return _get_broker().get_account()
    except Exception as e:
        logger.error(f"Get account failed: {type(e).__name__}")
        return None


def get_positions() -> list[dict]:
    """Open paper positions for display ([] on any error; never use this to decide an order)."""
    try:
        out = []
        for p in _get_broker().get_positions():
            cost = p.qty * p.avg_entry_price
            out.append({
                "symbol": p.symbol, "qty": p.qty, "side": p.side, "market_value": p.market_value,
                "unrealized_pl": p.market_value - cost,
                "unrealized_plpc": (p.market_value - cost) / cost if cost else 0.0,
                "current_price": p.market_value / p.qty if p.qty else 0.0,
                "avg_entry_price": p.avg_entry_price,
            })
        return out
    except Exception as e:
        logger.error(f"Get positions failed: {type(e).__name__}")
        return []


def submit_order(*args, **kwargs) -> None:
    """Removed: raw order submission bypassed the chokepoint. Always returns None."""
    logger.warning("paper_trader.submit_order is removed; use execution.submit_intent")
    return None


def close_position(symbol: str) -> None:
    """Removed (see submit_order). Exits go through execution.submit_intent with direction=-1."""
    logger.warning("paper_trader.close_position is removed; use execution.submit_intent")
    return None


def execute_signal(
    symbol: str, direction: int, confidence: float = 1.0, current_price: float = 0.0,
    *, atr: Optional[float] = None, strategy_key: str = "legacy",
    signal_bar_date: Optional[str] = None, validation_status: str = "unvalidated",
) -> Optional[dict]:
    """Route a signal through execution.submit_intent. Returns the Decision as a dict only when an
    order was submitted, else None. direction: 1 buy, -1 sell (reduce a long; shorts are off)."""
    if not trading_allowed():
        logger.info(f"dry-run: {symbol} dir={direction} (TRADING_MODE={config.TRADING_MODE}, "
                    f"PAPER_TRADE_ENABLED={config.PAPER_TRADE_ENABLED}) - no order sent")
        return None
    bar = signal_bar_date or datetime.now(timezone.utc).date().isoformat()
    try:
        intent = execution.OrderIntent(
            symbol=str(symbol), direction=direction, signal_bar_date=bar, strategy_key=strategy_key,
            confidence=float(confidence), validation_status=validation_status,
            atr=None if atr is None else float(atr), price=float(current_price))
    except (TypeError, ValueError):
        return None
    broker = _get_broker()
    try:  # no session here: refresh the equity reading so the risk gate does not fail closed
        rm = RiskManager()
        execution.refresh_sleeve_equity(rm, broker)
    except Exception as e:
        logger.error(f"execute_signal {symbol}: equity refresh failed ({type(e).__name__}); no order")
        return None
    d = execution.submit_intent(intent, broker=broker, risk_manager=rm)
    logger.info(f"execute_signal {symbol}: {d.status} {d.reasons}")
    if d.status != "submitted":
        return None
    return {"symbol": symbol, "id": d.broker_id, "qty": d.qty, "client_order_id": d.client_order_id,
            "stop_price": d.stop_price, "limit_price": d.limit_price, "status": d.status}
