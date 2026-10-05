"""Event-driven backtest engine (WS2.2).

Execution model (decision D11)
------------------------------
* ``execution="next_open"`` (default, ``config.EXECUTION_MODE``): a signal on bar t is acted on at
  the OPEN of bar t+1. A signal on the last bar is never executed. ``execution="close"`` is an
  explicit opt-in: the signal fills at the close of bar t (look-ahead-prone; characterisation only).
* A position is sized once at entry: notional = cash * ``config.MAX_POSITION_SIZE_PCT``;
  units = notional / entry fill. Entry stop/target levels are set from the entry FILL price.
* Per bar the order of events is:
    1. gap check at the open for a position carried in from the previous bar
       (long: open <= stop -> fill at the open; open >= target -> fill at the open;
        short mirrored). The stop is tested before the target.
    2. (next_open) the pending signal from the previous bar executes at this open; an opposite
       signal is a FLIP = close fill then open fill, both at this open; a same-side signal is
       ignored; when flat a signal opens a position.
    3. intrabar check using this bar's high/low (including the entry bar, after the entry at the
       open): stop level fills at the stop (long: min(open, stop)); target fills at max(open,
       target) i.e. the open only when it gaps through. PESSIMISTIC TIE-BREAK: if one bar's range
       touches both stop and target, the stop is assumed to have been hit first.
    4. (close mode) the bar's own signal executes at the close.
* Every fill (entry, signal exit, stop, target, end-of-data close) pays slippage (adverse, as a
  fraction of price) and commission (fraction of the fill's notional).
* Equity at each bar close = cash + open position marked to market at that bar's close.
  ``len(equity) == len(df)``; the final open position is closed at the last close (end_of_data)
  and the last equity point reflects that close-out.
* ``allow_short=False`` (long-only, the only variant execution can trade, D3): a -1 signal closes a long
  and never opens a short.
* ``atr_stop_mult`` / ``atr_target_mult`` (the executable exits, D5/D10): the stop is
  ``ref - mult * ATR(period)`` and the target ``ref + mult * ATR``, where ``ref`` is the CLOSE of the signal
  bar and ATR is measured at the signal bar. This is exactly what execution submits after the close
  (bracket levels from the last close and the latest ATR, filled at the next open). An entry whose
  ATR is not finite is skipped (execution rejects ``atr_invalid``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

import config

EXECUTION_MODES = ("next_open", "close")


def atr_series(df: pd.DataFrame, period: int = config.ATR_PERIOD) -> np.ndarray:
    """Causal simple-mean ATR; element t equals ``signals.latest_atr(df.iloc[:t+1])`` for t >= period."""
    prev = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()],
                   axis=1).max(axis=1)
    return tr.rolling(period).mean().to_numpy(dtype=float)


@dataclass
class Trade:
    entry_date: pd.Timestamp
    exit_date: Optional[pd.Timestamp] = None
    direction: int = 1
    entry_price: float = 0.0   # fill price including slippage
    exit_price: float = 0.0    # fill price including slippage
    pnl_pct: float = 0.0       # net of costs, as a fraction of position notional
    pnl_abs: float = 0.0       # net cash P&L
    exit_reason: str = ""
    bars_held: int = 0         # exit_index - entry_index
    entry_index: int = -1
    exit_index: int = -1
    notional: float = 0.0
    capital_before: float = 0.0  # account cash when the trade was opened


@dataclass
class EngineResult:
    trades: list[Trade]
    equity: pd.Series          # indexed like df, len == len(df)
    positions: pd.Series       # direction held at each bar close (-1/0/1)
    execution: str
    initial_capital: float


def run_backtest(
    df: pd.DataFrame,
    signal: Optional[pd.Series] = None,
    *,
    execution: Optional[str] = None,
    stop_loss: Optional[float] = config.STOP_LOSS_PCT,
    take_profit: Optional[float] = config.TAKE_PROFIT_PCT,
    commission: float = config.COMMISSION_PCT,
    slippage: float = config.SLIPPAGE_PCT,
    initial_capital: float = config.BACKTEST_INITIAL_CAPITAL,
    position_pct: float = config.MAX_POSITION_SIZE_PCT,
    allow_short: bool = True,
    atr_stop_mult: Optional[float] = None,
    atr_target_mult: Optional[float] = None,
    atr_period: int = config.ATR_PERIOD,
) -> EngineResult:
    """Run the engine over ``df`` (Open/High/Low/Close + ``signal`` column or ``signal`` arg).

    ``stop_loss``/``take_profit`` of None (or <= 0) disable that exit. ``atr_stop_mult`` /
    ``atr_target_mult`` (> 0) replace the percentage exit of the same kind with an ATR-based level
    anchored on the signal bar's close (see module docstring).
    """
    mode = execution or config.EXECUTION_MODE
    if mode not in EXECUTION_MODES:
        raise ValueError(f"execution must be one of {EXECUTION_MODES}, got {mode!r}")
    n = len(df)
    o = df["Open"].to_numpy(dtype=float)
    h = df["High"].to_numpy(dtype=float)
    lo = df["Low"].to_numpy(dtype=float)
    c = df["Close"].to_numpy(dtype=float)
    raw = df["signal"] if signal is None else signal
    sig = np.nan_to_num(pd.Series(raw).to_numpy(dtype=float), nan=0.0).astype(int)
    sig = np.sign(sig)
    sl = stop_loss if stop_loss and stop_loss > 0 else None
    tp = take_profit if take_profit and take_profit > 0 else None
    idx = df.index
    asm = atr_stop_mult if atr_stop_mult and atr_stop_mult > 0 else None
    atm = atr_target_mult if atr_target_mult and atr_target_mult > 0 else None
    atr = atr_series(df, atr_period) if (asm or atm) else None

    cash = float(initial_capital)
    trades: list[Trade] = []
    equity = np.empty(n)
    held = np.zeros(n, dtype=int)
    pos: Optional[dict] = None

    def fill(price: float, side: int) -> float:  # side +1 buy, -1 sell
        return price * (1.0 + slippage * side)

    def open_pos(direction: int, price: float, i: int) -> None:
        nonlocal pos, cash
        f = fill(price, direction)
        stop = None if sl is None else f * (1 - sl * direction)
        target = None if tp is None else f * (1 + tp * direction)
        if atr is not None:
            j = i - 1 if mode == "next_open" else i   # the signal bar
            a, ref = (atr[j], c[j]) if j >= 0 else (float("nan"), float("nan"))
            if not np.isfinite(a) or a <= 0:
                return                                  # execution would reject this entry (atr_invalid)
            if asm:
                stop = ref - direction * asm * a
            if atm:
                target = ref + direction * atm * a
        notional = cash * position_pct
        units = notional / f
        cash -= commission * notional
        pos = dict(d=direction, f=f, units=units, i=i, notional=notional, cap=cash + commission * notional,
                   stop=stop, target=target)

    def close_pos(price: float, i: int, reason: str) -> None:
        nonlocal pos, cash
        p = pos
        f = fill(price, -p["d"])
        gross = p["d"] * p["units"] * (f - p["f"])
        cost = commission * p["notional"] + commission * p["units"] * f
        net = gross - commission * p["units"] * f  # entry commission already paid out of cash
        cash += net
        pnl_abs = gross - cost
        trades.append(Trade(
            entry_date=idx[p["i"]], exit_date=idx[i], direction=p["d"], entry_price=p["f"], exit_price=f,
            pnl_pct=pnl_abs / p["notional"] if p["notional"] else 0.0, pnl_abs=pnl_abs, exit_reason=reason,
            bars_held=i - p["i"], entry_index=p["i"], exit_index=i, notional=p["notional"],
            capital_before=p["cap"]))
        pos = None

    def gap_exit(i: int) -> bool:
        d = pos["d"]
        if pos["stop"] is not None and ((o[i] <= pos["stop"]) if d == 1 else (o[i] >= pos["stop"])):
            close_pos(o[i], i, "stop_loss")
            return True
        if pos["target"] is not None and ((o[i] >= pos["target"]) if d == 1 else (o[i] <= pos["target"])):
            close_pos(o[i], i, "take_profit")
            return True
        return False

    def intrabar_exit(i: int) -> bool:
        d = pos["d"]
        if pos["stop"] is not None and ((lo[i] <= pos["stop"]) if d == 1 else (h[i] >= pos["stop"])):
            # stop first on a tie (pessimistic); an entry that opens beyond its stop fills at the open
            close_pos(min(pos["stop"], o[i]) if d == 1 else max(pos["stop"], o[i]), i, "stop_loss")
            return True
        if pos["target"] is not None and ((h[i] >= pos["target"]) if d == 1 else (lo[i] <= pos["target"])):
            close_pos(max(pos["target"], o[i]) if d == 1 else min(pos["target"], o[i]), i, "take_profit")
            return True
        return False

    def act(s: int, price: float, i: int) -> None:
        if s == 0:
            return
        if pos is not None and pos["d"] == -s:
            close_pos(price, i, "signal")      # flip = close fill + open fill at the same price
        if pos is None and (allow_short or s == 1):
            open_pos(s, price, i)

    for i in range(n):
        if pos is not None:
            gap_exit(i)
        if mode == "next_open" and i > 0:
            act(int(sig[i - 1]), o[i], i)
        if pos is not None:
            intrabar_exit(i)
        if mode == "close" and i < n - 1:
            act(int(sig[i]), c[i], i)
        if i == n - 1 and pos is not None:
            close_pos(c[i], i, "end_of_data")
        if pos is None:
            equity[i] = cash
        else:
            equity[i] = cash + pos["d"] * pos["units"] * (c[i] - pos["f"])
            held[i] = pos["d"]

    return EngineResult(trades=trades, equity=pd.Series(equity, index=idx), positions=pd.Series(held, index=idx),
                        execution=mode, initial_capital=float(initial_capital))
