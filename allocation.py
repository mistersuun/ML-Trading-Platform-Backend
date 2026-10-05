"""Allocation proposals for the strategic core and the trend sleeve (decisions D1, D4).

This module only COMPUTES a proposal (target weights, drift, whole-share trades, trend
signals) and renders it as text. It never places orders and deliberately imports nothing
from execution / brokers / paper_trader: the owner places any trades manually.
Prices and holdings are inputs; fetching them is the caller's job.

Targets (docs/research/portfolio-recommendations.md section 2): 90% core + 10% trend.
Per-symbol target = CORE_PCT * core weight + TREND_SLEEVE_PCT * trend weight, where each
trend asset gets (1/6) * score of the sleeve and the unheld remainder sits in T-bills.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

CASH_KEY = "CASH"
CASH_SYMBOL = "SGOV"          # where the trend sleeve parks assets whose signal is "off"
CORE_PCT = 0.90
TREND_SLEEVE_PCT = 0.10
# PDBC is the optional 0-5% commodities sleeve; 5% is the top of the range.
CORE_TARGETS = {"VTI": 0.32, "VEA": 0.18, "VWO": 0.05, "IEF": 0.13, "TLT": 0.07,
                "TIP": 0.07, "SGOV": 0.08, "GLDM": 0.05, "PDBC": 0.05}
TREND_UNIVERSE = ["SPY", "VEA", "IEF", "GLD", "DBC", "VNQ"]
TREND_LOOKBACKS = (8, 10, 12)

assert abs(sum(CORE_TARGETS.values()) - 1.0) < 1e-9
assert abs(CORE_PCT + TREND_SLEEVE_PCT - 1.0) < 1e-9


def load_holdings(csv_path) -> dict[str, float]:
    """Read ``symbol,quantity`` rows. A row with symbol CASH holds dollars of cash."""
    out: dict[str, float] = {}
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            sym = row.get("symbol", "").upper()
            if not sym:
                continue
            try:
                qty = float(row.get("quantity", ""))
            except ValueError:
                raise ValueError(f"bad quantity for {sym!r}: {row.get('quantity')!r}")
            if not math.isfinite(qty) or qty < 0:
                raise ValueError(f"quantity for {sym} must be a finite number >= 0")
            out[sym] = out.get(sym, 0.0) + qty
    return out


def _completed_closes(monthly_closes: pd.DataFrame, as_of: date | None) -> pd.DataFrame:
    """Month-end closes, one row per month, only months strictly before ``as_of``'s month."""
    df = monthly_closes.copy()
    idx = df.index
    df.index = idx if isinstance(idx, pd.PeriodIndex) else pd.DatetimeIndex(idx).to_period("M")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    cur = pd.Period(as_of or date.today(), freq="M")
    return df[df.index < cur]


def trend_signals(monthly_closes: pd.DataFrame | None, as_of: date | None = None,
                  assets=TREND_UNIVERSE, lookbacks=TREND_LOOKBACKS) -> tuple[dict[str, float], str | None]:
    """Score in [0, 1] per asset = share of 8/10/12-month SMA votes with close > SMA.

    Same rule as portfolio_backtester.trend_sleeve (SMA includes the signal month; a vote
    whose SMA is still warming up counts as invested). Uses only months completed before
    ``as_of`` (default today), so the current partial month and anything later are ignored.
    Returns (scores, signal_month); assets without data are omitted.
    """
    if monthly_closes is None:
        return {}, None
    df = _completed_closes(monthly_closes, as_of)
    if df.empty:
        return {}, None
    scores: dict[str, float] = {}
    for a in assets:
        if a not in df.columns:
            continue
        s = df[a].dropna()
        if s.empty or s.index[-1] != df.index[-1]:
            continue   # stale or missing for the signal month
        votes = [(float(s.iloc[-1]) > float(s.iloc[-L:].mean())) if len(s) >= L else True
                 for L in lookbacks]
        scores[a] = sum(votes) / len(lookbacks)
    return scores, str(df.index[-1])


def target_weights(scores: dict[str, float] | None = None) -> dict[str, float]:
    """Combined per-symbol target (sums to 1). Assets with no score are treated as invested."""
    scores = scores or {}
    w = {s: CORE_PCT * x for s, x in CORE_TARGETS.items()}
    each = TREND_SLEEVE_PCT / len(TREND_UNIVERSE)
    for a in TREND_UNIVERSE:
        sc = scores.get(a, 1.0)
        w[a] = w.get(a, 0.0) + each * sc
        w[CASH_SYMBOL] = w.get(CASH_SYMBOL, 0.0) + each * (1.0 - sc)
    return w


@dataclass
class SleeveRow:
    symbol: str
    price: float
    shares: float
    value: float
    weight: float           # current, share of managed value (before contributions)
    target: float
    drift: float            # weight - target
    out_of_band: bool
    trade_shares: int = 0   # + buy / - sell
    trade_value: float = 0.0


@dataclass
class Proposal:
    rows: list[SleeveRow]
    total_value: float          # holdings + cash, before contributions
    cash: float
    contributions: float
    leftover_cash: float
    trend_scores: dict[str, float]
    signal_month: str | None
    unmanaged: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def trades(self) -> list[SleeveRow]:
        return [r for r in self.rows if r.trade_shares]

    def render(self) -> str:
        L = ["ALLOCATION PROPOSAL (advisory only - no orders are placed)",
             f"Managed value ${self.total_value:,.2f}  cash ${self.cash:,.2f}  "
             f"contributions ${self.contributions:,.2f}"]
        L.append(f"{'Symbol':<7}{'Price':>9}{'Shares':>9}{'Value':>12}{'Wt%':>7}{'Tgt%':>7}"
                 f"{'Drift':>7}  Band  Trade")
        for r in self.rows:
            t = f"{'BUY' if r.trade_shares > 0 else 'SELL'} {abs(r.trade_shares)} (${abs(r.trade_value):,.0f})" \
                if r.trade_shares else "-"
            L.append(f"{r.symbol:<7}{r.price:>9.2f}{r.shares:>9.2f}{r.value:>12,.2f}"
                     f"{r.weight * 100:>7.1f}{r.target * 100:>7.1f}{r.drift * 100:>+7.1f}  "
                     f"{'OUT' if r.out_of_band else 'ok':<5} {t}")
        L.append(f"Cash left after trades: ${self.leftover_cash:,.2f}")
        if self.trend_scores:
            sig = ", ".join(f"{a} {s:.2f}" for a, s in self.trend_scores.items())
            L.append(f"Trend signals (month {self.signal_month}): {sig}")
        else:
            L.append("Trend signals: unavailable (trend sleeve assumed fully invested)")
        if self.unmanaged:
            L.append("Unmanaged holdings (ignored, never sold): " + ", ".join(self.unmanaged))
        L += [f"Note: {n}" for n in self.notes]
        return "\n".join(L)


def propose_rebalance(holdings: dict[str, float], prices: dict[str, float],
                      monthly_closes: pd.DataFrame | None = None, contributions: float = 0.0,
                      band_abs: float = 0.05, band_rel: float = 0.25,
                      min_trade_pct: float = 0.02, as_of: date | None = None) -> Proposal:
    """Propose whole-share trades toward target. Never places orders.

    A symbol is out of band when |drift| > max(band_abs, band_rel * target). Sells happen
    only for symbols out of band (sold down to target). Buys are funded by cash +
    contributions + sell proceeds and go to underweight symbols, largest deficit first.
    Trades under ``min_trade_pct`` of value are skipped (buys funded by contributions are
    exempt). ``holdings['CASH']`` is dollars of cash.
    """
    if contributions < 0:
        raise ValueError("contributions must be >= 0")
    scores, sig_month = trend_signals(monthly_closes, as_of)
    targets = target_weights(scores)
    notes = []
    if monthly_closes is not None:
        miss = [a for a in TREND_UNIVERSE if a not in scores]
        if miss:
            notes.append("no usable trend data for " + ", ".join(miss) + " (assumed invested)")

    cash = float(holdings.get(CASH_KEY, 0.0))
    shares = {s: float(q) for s, q in holdings.items() if s != CASH_KEY and s in targets}
    unmanaged = sorted(s for s, q in holdings.items() if s != CASH_KEY and s not in targets and q)
    missing = [s for s in targets if s not in prices]
    bad = [s for s in targets if s in prices and not (math.isfinite(prices[s]) and prices[s] > 0)]
    if missing or bad:
        raise ValueError(f"missing/invalid prices for: {sorted(set(missing) | set(bad))}")

    value = {s: shares.get(s, 0.0) * prices[s] for s in targets}
    total = sum(value.values()) + cash
    if total <= 0:
        raise ValueError("portfolio value is zero")
    post = total + contributions
    rows = {s: SleeveRow(s, prices[s], shares.get(s, 0.0), value[s], value[s] / total,
                         targets[s], value[s] / total - targets[s],
                         abs(value[s] / total - targets[s]) > max(band_abs, band_rel * targets[s]))
            for s in targets}

    min_trade = min_trade_pct * post
    avail = cash + contributions
    # Sells: only out-of-band overweights, down to target, whole shares (never more than held).
    for r in rows.values():
        excess = r.value - r.target * post
        if r.out_of_band and excess > 0:
            n = min(int(round(excess / r.price)), int(math.floor(r.shares)))
            if n > 0 and n * r.price >= min_trade:
                r.trade_shares, r.trade_value = -n, -n * r.price
                avail += n * r.price
    # Buys: proportional to deficits, whole shares, then single-share top-ups.
    deficit = {s: r.target * post - r.value for s, r in rows.items()
               if r.trade_shares == 0 and r.value < r.target * post}
    tot_def = sum(deficit.values())
    buys = {s: 0 for s in deficit}
    if tot_def > 0 and avail > 0:
        spend = min(avail, tot_def)
        for s, d in deficit.items():
            buys[s] = int(math.floor(spend * d / tot_def / prices[s]))
        left = avail - sum(buys[s] * prices[s] for s in buys)
        while True:
            cand = [s for s in deficit if prices[s] <= left
                    and deficit[s] - buys[s] * prices[s] >= prices[s] / 2]
            if not cand:
                break
            s = max(cand, key=lambda x: deficit[x] - buys[x] * prices[x])
            buys[s] += 1
            left -= prices[s]
    for s, n in buys.items():
        if n and (contributions > 0 or n * prices[s] >= min_trade):
            rows[s].trade_shares, rows[s].trade_value = n, n * prices[s]
    leftover = avail - sum(r.trade_value for r in rows.values())
    ordered = sorted(rows.values(), key=lambda r: -r.target)
    return Proposal(ordered, total, cash, contributions, leftover, scores, sig_month,
                    unmanaged, notes)
