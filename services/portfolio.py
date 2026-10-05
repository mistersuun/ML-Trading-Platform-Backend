"""Owner portfolio overview (GET /api/portfolio/overview): the real holdings CSV priced with the data layer.

Method (stated in the response): today's holdings are held CONSTANT and applied to historical prices, so the
series answers "how would what I own today have done", not "what did my account do" (the platform has no
transaction history). The benchmark is a monthly-rebalanced SPY/IEF mix from adjusted closes (total return).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

import allocation
import config
from api.errors import ApiError
from services import allocation_view as av
from services import models as M
from services import riskview
from services.common import finite
from services.providers import DataProvider
from state import db as state_db

RANGES = ("1M", "3M", "YTD", "1Y", "ALL")
_RANGE_DAYS = {"1M": 60, "3M": 130, "YTD": 400, "1Y": 400}
MAX_POINTS = 800
METHOD = ("Constant current holdings applied to historical prices: it shows how what you own today would have "
          "performed, not your account's history (no transactions are recorded). Prices are dividend-adjusted "
          "closes, so returns are total returns. The benchmark is the monthly-rebalanced "
          "{bench}, also total return.")


def _benchmark_label() -> str:
    w = config.BENCHMARK_WEIGHTS
    return "/".join(f"{k} {v:.0%}" for k, v in w.items())


def _range_base(index: pd.DatetimeIndex, rng: str) -> int:
    """Position of the base date of `rng` in `index` (the last bar on/before the range start; the first bar when
    history is shorter)."""
    last = index[-1]
    if rng == "ALL":
        return 0
    if rng == "YTD":
        start = pd.Timestamp(year=last.year, month=1, day=1)
        pos = int(index.searchsorted(start, side="left")) - 1       # last bar of the previous year
    else:
        months = {"1M": 1, "3M": 3, "1Y": 12}[rng]
        start = last - pd.DateOffset(months=months)
        pos = int(index.searchsorted(start, side="right")) - 1
    return max(pos, 0)


def _rebalanced_benchmark(prices: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Growth of 100 of a mix rebalanced to `weights` at every month change (rows of `prices` are days)."""
    px = prices[list(weights)].to_numpy(dtype=float)
    w = np.array([weights[c] for c in prices[list(weights)].columns])
    months = prices.index.to_period("M")
    out = np.empty(len(px))
    out[0] = 100.0
    anchor_val, anchor_px = 100.0, px[0]
    for i in range(1, len(px)):
        if months[i] != months[i - 1]:                      # rebalance at the previous close
            anchor_val, anchor_px = out[i - 1], px[i - 1]
        out[i] = anchor_val * float(np.dot(w, px[i] / anchor_px))
    return pd.Series(out, index=prices.index)


def _thin(n: int) -> list[int]:
    if n <= MAX_POINTS:
        return list(range(n))
    step = n / (MAX_POINTS - 1)
    idx = sorted({min(n - 1, int(round(i * step))) for i in range(MAX_POINTS - 1)} | {n - 1})
    return idx


def overview(provider: DataProvider, rng: str = "1Y", holdings: Optional[dict] = None) -> M.OverviewResponse:
    if rng not in RANGES:
        raise ApiError(f"range must be one of {', '.join(RANGES)}", {"range": rng}, status_code=422,
                       code="validation_error")
    holdings = holdings if holdings is not None else av.load_owner_holdings()
    days = config.RESEARCH_LOOKBACK_DAYS if rng == "ALL" else _RANGE_DAYS[rng]
    closes = av.Closes(provider, max(days, av.PRICE_HISTORY_DAYS))
    warnings: list[str] = []

    cash = float(holdings.get(allocation.CASH_KEY, 0.0))
    qty = {s: float(q) for s, q in holdings.items() if s != allocation.CASH_KEY and q}
    series, unpriced = {}, []
    for s in sorted(qty):
        c = closes.get(s)
        if c is None:
            unpriced.append(s)
        else:
            series[s] = c
    if unpriced:
        warnings.append("No price data for " + ", ".join(unpriced) + ": left out of the total and the charts.")
    if not series and cash <= 0:
        raise ApiError("None of the holdings could be priced.", {"symbols": unpriced}, status_code=404,
                       code="no_data")

    # ---- today's value, day change, accounts
    last_px = {s: float(c.iloc[-1]) for s, c in series.items()}
    prev_px = {s: float(c.iloc[-2]) if len(c) > 1 else float(c.iloc[-1]) for s, c in series.items()}
    value = {s: qty[s] * last_px[s] for s in series}
    invested = sum(value.values())
    total = invested + cash
    prev_total = sum(qty[s] * prev_px[s] for s in series) + cash
    day = total - prev_total
    as_of = max((c.index[-1] for c in series.values()), default=pd.Timestamp(date.today()))

    core = set(allocation.CORE_TARGETS)
    trend = set(allocation.TREND_UNIVERSE) - core   # VEA and IEF sit in both lists: they count as core
    acct = {"core": sum(v for s, v in value.items() if s in core),
            "trend": sum(v for s, v in value.items() if s in trend)}
    acct["other"] = sum(v for sym, v in value.items() if sym not in core | trend)
    labels = {"core": "Core portfolio", "trend": "Trend sleeve", "cash": "Cash", "other": "Other holdings"}
    accounts = [M.AccountValue(key=k, label=labels[k], value=v, share=v / total if total > 0 else None)
                for k, v in (("core", acct["core"]), ("trend", acct["trend"]), ("cash", cash), ("other", acct["other"]))
                if k in ("core", "trend", "cash") or v > 0.005]
    if acct["trend"] or acct["core"]:
        warnings.append("Core / trend split is by symbol (the CSV has no account column): VEA and IEF count as "
                        "core, SPY, GLD, DBC and VNQ as trend.")

    # ---- history: growth of 100, drawdown, benchmark
    points: list[M.SeriesPoint] = []
    period_return = bench_return = max_dd = None
    if series:
        px = pd.concat(series, axis=1).sort_index().ffill(limit=5).dropna()
        if px.empty:
            warnings.append("The holdings share no common price history; no chart is available.")
        else:
            starts = {s: series[s].index[0] for s in series}
            limiter = max(starts, key=starts.get)
            if rng == "ALL" and starts[limiter] > min(starts.values()) + pd.Timedelta(days=7):
                warnings.append(f"Chart starts {px.index[0].date().isoformat()}, when {limiter} began trading.")
            vals = (px * pd.Series(qty)[px.columns]).sum(axis=1) + cash
            base = _range_base(px.index, rng)
            vals = vals.iloc[base:]
            growth = vals / vals.iloc[0] * 100.0
            dd = growth / growth.cummax() - 1.0
            bench = None
            bw = config.BENCHMARK_WEIGHTS
            bpx = {s: closes.get(s) for s in bw}
            if all(v is not None for v in bpx.values()):
                bf = pd.concat(bpx, axis=1).sort_index().ffill(limit=5).reindex(growth.index).ffill(limit=5)
                if not bf.isna().any().any():
                    bench = _rebalanced_benchmark(bf, bw)
            if bench is None:
                warnings.append("Benchmark data (" + ", ".join(bw) + ") is unavailable for this range.")
            period_return = finite(growth.iloc[-1] / 100.0 - 1.0)
            bench_return = finite(bench.iloc[-1] / 100.0 - 1.0) if bench is not None else None
            max_dd = finite(dd.min())
            for i in _thin(len(growth)):
                points.append(M.SeriesPoint(
                    date=growth.index[i].date().isoformat(), portfolio=float(growth.iloc[i]),
                    benchmark=finite(bench.iloc[i]) if bench is not None else None,
                    drawdown=finite(dd.iloc[i])))

    # ---- allocation by group (core + trend at today's trend signals)
    monthly = av.monthly_frame(closes, allocation.TREND_UNIVERSE)
    scores, _ = allocation.trend_signals(monthly if len(monthly.columns) else None)
    target = av.group_targets(allocation.target_weights(scores))
    mapped = {s: v for s, v in value.items() if s in av.GROUP_OF}
    other = sorted(s for s in value if s not in av.GROUP_OF)
    if other:
        warnings.append("Not in any asset group (left out of the allocation): " + ", ".join(other) + ".")
    groups = av.group_rows(target, av.group_shares(mapped))

    return M.OverviewResponse(
        as_of=as_of.date().isoformat(), range=rng, total_value=total, day_change=finite(day),
        day_change_pct=finite(day / prev_total) if prev_total > 0 else None, cash=cash, accounts=accounts,
        paper_sleeve=_paper_sleeve(), period_return=period_return, benchmark_return=bench_return,
        max_drawdown=max_dd, series=points, method=METHOD.format(bench=_benchmark_label()),
        benchmark_label=_benchmark_label(), allocation=groups, allocation_basis=av.GROUPS_BASIS,
        unpriced=unpriced, warnings=warnings)


def _paper_sleeve() -> M.PaperSleeve:
    note = "Paper money on Alpaca: not real money and not part of the portfolio total."
    try:
        conn = state_db.require_initialized()
    except Exception:
        return M.PaperSleeve(note=note + " The risk state is not initialised (python main.py risk init).")
    try:
        snap = riskview.sleeve_snapshot(conn)
    except Exception:
        return M.PaperSleeve(note=note + " The risk state could not be read.")
    finally:
        conn.close()
    return M.PaperSleeve(value=snap["value"], peak=snap["peak"], drawdown=snap["drawdown"], as_of=snap["as_of"],
                         note=note)
