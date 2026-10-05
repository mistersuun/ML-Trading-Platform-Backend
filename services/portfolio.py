"""Owner portfolio overview (GET /api/portfolio/overview): the owner's account priced with the data layer.

The account is the latest read-only IBKR snapshot (account_snapshots, D14) when there is one, else the holdings CSV.
Positions are priced from the bar store in their own currency and converted to the base currency (CAD by default)
with the snapshot's FX rates, or with the yfinance '<CCY><BASE>=X' history for the series. Net worth is the broker's
net liquidation; a negative cash balance is a margin loan and stays constant in the net-worth series.

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
from services import account_view
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
NET_METHOD = (" Growth of 100 and drawdown are on NET worth: constant current holdings plus a constant cash "
              "balance{loan}.")


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
    inp = av.owner_inputs(holdings)
    snap, base = inp.snapshot, inp.base
    days = config.RESEARCH_LOOKBACK_DAYS if rng == "ALL" else _RANGE_DAYS[rng]
    closes = av.Closes(provider, max(days, av.PRICE_HISTORY_DAYS))
    warnings: list[str] = []

    cash = inp.cash
    qty = {s: float(q) for s, q in inp.holdings.items() if s != allocation.CASH_KEY and q}
    series, unpriced, fx_notes = {}, [], []
    for s in sorted(qty):
        c = closes.get(s)
        if c is None:
            unpriced.append(s)
            continue
        ccy = av.native_currency(s, inp) if snap is not None else inp.currency.get(s, base)
        if ccy != base:
            rate = av.fx_rate_now(closes, ccy, inp)
            hist = closes.get(f"{ccy}{base}=X")
            if rate is None:
                warnings.append(f"No {ccy}{base}=X rate: {s} is left out of the total and the charts.")
                unpriced.append(s)
                continue
            if hist is not None:
                fx = hist.reindex(c.index.union(hist.index)).ffill().bfill().reindex(c.index)
                if f"{ccy}{base}=X" not in fx_notes:
                    fx_notes.append(f"{ccy}{base}=X")
            else:
                fx = pd.Series(rate, index=c.index)
                warnings.append(f"No {ccy}{base}=X history: {s} is converted at the constant rate {rate:.4f}.")
            c = c * fx
            if snap is not None and snap.fx(ccy):
                c = c.copy()
                c.iloc[-1] = float(closes.get(s).iloc[-1]) * rate        # today at the broker's rate
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
    net_worth = snap.net_liquidation if snap is not None else invested + cash
    total = net_worth
    prev_total = sum(qty[s] * prev_px[s] for s in series) + cash
    day = invested - sum(qty[s] * prev_px[s] for s in series)
    as_of = max((c.index[-1] for c in series.values()), default=pd.Timestamp(date.today()))

    prof = allocation.resolve_profile(inp.holdings)
    core = set(prof.core)
    trend = set(prof.trend_universe) - core         # VEA and IEF sit in both lists: they count as core
    acct = {"core": sum(v for s, v in value.items() if s in core),
            "trend": sum(v for s, v in value.items() if s in trend)}
    acct["other"] = sum(v for sym, v in value.items() if sym not in core | trend)
    labels = {"core": "Core portfolio", "trend": "Trend sleeve", "cash": "Cash", "other": "Other holdings"}
    accounts = [M.AccountValue(key=k, label=labels[k], value=v, share=v / total if total > 0 else None)
                for k, v in (("core", acct["core"]), ("trend", acct["trend"]), ("cash", cash), ("other", acct["other"]))
                if k in ("core", "trend", "cash") or v > 0.005]
    if acct["trend"] or acct["core"]:
        warnings.append("Core / trend split is by symbol (the CSV has no account column): VEA and IEF count as "
                        "core, SPY, GLD, DBC and VNQ as trend." if prof.name == "us" else
                        "Core / trend split is by symbol (the account has no sleeve column): the profile's core "
                        "listings count as core, its other trend listings as trend.")

    # ---- account block (net worth, margin loan, leverage, headroom)
    if snap is not None:
        block = account_view.block_from_snapshot(snap)
        block.positions_value = finite(invested)         # priced from the bar store, like the series
        positions_value = invested
    else:
        positions_value = invested
        block = account_view.block_from_values("holdings_csv", None, base, net_worth, invested, cash,
                                               n_positions=len(series))
    warnings.extend(block.warnings)
    if snap is not None and snap.unvalued_positions():
        warnings.append("Not valued (not shares): " + ", ".join(sorted({f"{p.broker_symbol or p.symbol} "
                        f"({p.sec_type})" for p in snap.unvalued_positions()})) + ". They count in the broker's "
                        "net liquidation but not in the holdings below.")
    if snap is not None and base != "USD":
        warnings.append("The benchmark (SPY/IEF) is in US dollars and is not converted to " + base + ".")

    # ---- history: growth of 100, drawdown, benchmark (on NET worth: positions + the constant cash balance)
    points: list[M.SeriesPoint] = []
    period_return = bench_return = max_dd = None
    px = pd.concat(series, axis=1).sort_index().ffill(limit=5).dropna() if series else None
    if px is not None and px.empty:
        warnings.append("The holdings share no common price history; no chart is available.")
    elif px is not None:
        starts = {s: series[s].index[0] for s in series}
        limiter = max(starts, key=starts.get)
        if rng == "ALL" and starts[limiter] > min(starts.values()) + pd.Timedelta(days=7):
            warnings.append(f"Chart starts {px.index[0].date().isoformat()}, when {limiter} began trading.")
        vals = (px * pd.Series(qty)[px.columns]).sum(axis=1) + cash
        vals = vals.iloc[_range_base(px.index, rng):]
        if not (vals > 0).all():
            warnings.append("Net worth is not positive over the chart range, so growth of 100 is undefined and no "
                            "chart is shown.")
        else:
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
    monthly = av.monthly_frame(closes, prof.trend_universe)
    scores, _ = allocation.trend_signals(monthly if len(monthly.columns) else None, assets=list(prof.trend_universe))
    target = av.group_targets(allocation.target_weights(scores, prof))
    mapped = {s: v for s, v in value.items() if av.group_key(s)}
    other = sorted(s for s in value if not av.group_key(s))
    if other:
        warnings.append("Not in any asset group (left out of the allocation): " + ", ".join(other) + ".")
    groups = av.group_rows(target, av.group_shares(mapped), profile=prof.name)
    classified = sum(mapped.values())
    for g in groups:                                  # value in base currency: the share is over classified symbols
        g.value = finite(g.now * classified)
    unclassified = finite(sum(v for s, v in value.items() if not av.group_key(s)))

    method = METHOD.format(bench=_benchmark_label()) + NET_METHOD.format(
        loan=f" (a margin loan of {-cash:,.0f} {base}, held constant)" if cash < 0 else "")
    if fx_notes:
        method += " Positions in other currencies are converted to " + base + " with " + ", ".join(fx_notes) + "."
    if snap is not None:
        method += " Net worth is the broker's net liquidation."
    return M.OverviewResponse(
        as_of=as_of.date().isoformat(), range=rng, total_value=total, day_change=finite(day),
        day_change_pct=finite(day / prev_total) if prev_total > 0 else None, cash=cash, accounts=accounts,
        paper_sleeve=_paper_sleeve(), period_return=period_return, benchmark_return=bench_return,
        max_drawdown=max_dd, series=points, method=method,
        benchmark_label=_benchmark_label(), allocation=groups, allocation_basis=av.GROUPS_BASIS,
        unpriced=unpriced, warnings=warnings, base_currency=base, source=inp.source, net_worth=finite(net_worth),
        positions_value=finite(positions_value), margin_loan=finite(max(-cash, 0.0)), leverage=block.leverage,
        margin_headroom=block.margin_headroom, account=block, unclassified_value=unclassified,
        other_value=finite(net_worth - (positions_value + cash)) if snap is not None else None)


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
