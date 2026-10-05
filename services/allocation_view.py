"""Allocation proposal view (D1/D4): wraps allocation.propose_rebalance with prices and monthly closes from the
data layer, and shapes the result for the Allocation page. Advisory only: this module never touches an order path.

Also hosts what the Overview shares with it: the owner's holdings file, the asset-group map and the trend-sleeve
detail (13 month-end closes, 10-month average, 8/10/12-month votes).
"""
from __future__ import annotations

import inspect
import math
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

import allocation
import config
from api.errors import ApiError
from services import models as M
from services.common import finite
from services.providers import DataProvider

# Five asset groups of the owner's portfolio (symbol -> group). Real estate counts as US equity.
GROUPS: list[tuple[str, str]] = [
    ("us_equity", "US equity"), ("intl_equity", "International equity"), ("treasuries", "Treasuries"),
    ("tips_bills", "TIPS & T-bills"), ("gold_commodities", "Gold & commodities"),
]
GROUP_LABEL = dict(GROUPS)
GROUP_OF = {
    "VTI": "us_equity", "SPY": "us_equity", "VNQ": "us_equity",
    "VEA": "intl_equity", "VWO": "intl_equity",
    "IEF": "treasuries", "TLT": "treasuries",
    "TIP": "tips_bills", "SGOV": "tips_bills",
    "GLDM": "gold_commodities", "GLD": "gold_commodities", "PDBC": "gold_commodities", "DBC": "gold_commodities",
}
ETF_NAME = {
    "VTI": "US total market", "VEA": "Developed ex-US", "VWO": "Emerging", "IEF": "Treasuries 7-10y",
    "TLT": "Treasuries 20y+", "TIP": "TIPS", "SGOV": "T-bills", "GLDM": "Gold", "PDBC": "Commodities",
    "SPY": "S&P 500", "GLD": "Gold", "DBC": "Commodities", "VNQ": "US real estate",
}
assert set(allocation.CORE_TARGETS) | set(allocation.TREND_UNIVERSE) <= set(GROUP_OF)

PRICE_HISTORY_DAYS = 900   # ~29 months: 13 month-ends plus the 10-month average warm-up, with slack
HOLDINGS_FORMAT = ("Create a CSV with a header `symbol,quantity` and one row per holding, e.g. `VTI,120`; a row with "
                   "symbol CASH holds dollars of cash, e.g. `CASH,4560`.")
_BAND_DEFAULTS = {k: v.default for k, v in inspect.signature(allocation.propose_rebalance).parameters.items()
                  if k in ("band_abs", "band_rel")}


# ---------------------------------------------------------------- holdings + prices
def load_owner_holdings(path=None) -> dict[str, float]:
    """The owner's holdings CSV (config.HOLDINGS_CSV). 404 ``no_holdings`` when absent, 422 ``bad_holdings`` when
    it cannot be parsed."""
    p = Path(path if path is not None else config.HOLDINGS_CSV)
    if not p.is_file():
        raise ApiError(f"No holdings file at {p}. {HOLDINGS_FORMAT} Set HOLDINGS_CSV to use another location.",
                       {"path": str(p), "format": "symbol,quantity"}, status_code=404, code="no_holdings")
    try:
        holdings = allocation.load_holdings(p)
    except (ValueError, OSError) as e:
        raise ApiError(f"Holdings file {p} is not valid: {e}. {HOLDINGS_FORMAT}", {"path": str(p)},
                       status_code=422, code="bad_holdings") from e
    if not any(q for q in holdings.values()):
        raise ApiError(f"Holdings file {p} has no positions. {HOLDINGS_FORMAT}", {"path": str(p)},
                       status_code=404, code="no_holdings")
    return holdings


def clean_close(df: Optional[pd.DataFrame]) -> Optional[pd.Series]:
    """Close series with a tz-naive, day-normalised, unique, ascending index (None when there is no usable data)."""
    if df is None or df.empty or "Close" not in df:
        return None
    s = df["Close"].astype(float).dropna()
    s = s[s > 0]
    if s.empty:
        return None
    idx = pd.DatetimeIndex(s.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    s = pd.Series(s.to_numpy(), index=idx.normalize())
    return s[~s.index.duplicated(keep="last")].sort_index()


class Closes:
    """Memoised close series per symbol through the data provider (one fetch per symbol per request)."""

    def __init__(self, provider: DataProvider, days: int = PRICE_HISTORY_DAYS):
        self.provider, self.days, self._c = provider, days, {}

    def get(self, symbol: str, days: Optional[int] = None) -> Optional[pd.Series]:
        want = max(days or 0, self.days)
        hit = self._c.get(symbol)
        if hit is None or hit[0] < want:
            try:
                s = clean_close(self.provider.ohlcv(symbol, want))
            except Exception:
                s = None
            hit = self._c[symbol] = (want, s)
        return hit[1]


def monthly_frame(closes: Closes, symbols) -> pd.DataFrame:
    cols = {}
    for s in symbols:
        c = closes.get(s)
        if c is not None:
            cols[s] = c.resample("ME").last().dropna()
    return pd.DataFrame(cols)


# ---------------------------------------------------------------- trend sleeve detail
def trend_detail(monthly: Optional[pd.DataFrame], scores: dict[str, float], as_of: Optional[date] = None
                 ) -> list[M.TrendAsset]:
    """Per trend asset: last 13 completed month-end closes with the 10-month average, the 8/10/12 votes (same
    rule as allocation.trend_signals), score, state and the weight in the whole portfolio."""
    each = allocation.TREND_SLEEVE_PCT / len(allocation.TREND_UNIVERSE)
    df = allocation._completed_closes(monthly, as_of) if monthly is not None and len(monthly.columns) else None
    out = []
    for a in allocation.TREND_UNIVERSE:
        name = ETF_NAME.get(a, a)
        if a not in scores or df is None or a not in df.columns:
            out.append(M.TrendAsset(symbol=a, name=name, months=[], votes=[], score=1.0, state="unknown",
                                    weight=each, data_available=False))
            continue
        s = df[a].dropna()
        ma = s.rolling(10).mean()
        months = [M.TrendMonth(month=str(p), close=float(s.loc[p]), average=finite(ma.loc[p]))
                  for p in s.index[-13:]]
        votes = []
        for L in allocation.TREND_LOOKBACKS:
            if len(s) >= L:
                avg = float(s.iloc[-L:].mean())
                votes.append(M.TrendVote(lookback=L, above=float(s.iloc[-1]) > avg, average=avg))
            else:   # warming up: counts as invested, like trend_signals
                votes.append(M.TrendVote(lookback=L, above=True, average=None))
        score = scores[a]
        state = "held" if score >= 1 - 1e-9 else ("tbills" if score <= 1e-9 else "partial")
        out.append(M.TrendAsset(symbol=a, name=name, months=months, votes=votes, score=score, state=state,
                                weight=each * score))
    return out


# ---------------------------------------------------------------- group shares
def group_targets(targets: dict[str, float]) -> dict[str, float]:
    out = {k: 0.0 for k, _ in GROUPS}
    for s, w in targets.items():
        out[GROUP_OF[s]] += w
    return out


def group_shares(values: dict[str, float]) -> dict[str, float]:
    """Share of the summed value per group (symbols outside GROUP_OF ignored)."""
    out = {k: 0.0 for k, _ in GROUPS}
    for s, v in values.items():
        if s in GROUP_OF:
            out[GROUP_OF[s]] += v
    tot = sum(out.values())
    return {k: (v / tot if tot > 0 else 0.0) for k, v in out.items()}


GROUPS_BASIS = ("Share of invested value (cash excluded); targets are core 90% + trend sleeve 10% at today's "
                "trend signals.")


def group_rows(target: dict[str, float], now: dict[str, float], after: Optional[dict[str, float]] = None
               ) -> list[M.GroupWeight]:
    return [M.GroupWeight(key=k, label=label, target=target[k], now=now[k], drift=now[k] - target[k],
                          after=None if after is None else after[k]) for k, label in GROUPS]


# ---------------------------------------------------------------- proposal
def proposal(provider: DataProvider, contribution: float = 0.0, holdings: Optional[dict] = None
             ) -> M.ProposalResponse:
    holdings = holdings if holdings is not None else load_owner_holdings()
    closes = Closes(provider)
    symbols = sorted(set(allocation.CORE_TARGETS) | set(allocation.TREND_UNIVERSE))
    prices, last_dates = {}, []
    for s in symbols:
        c = closes.get(s)
        if c is not None:
            prices[s] = float(c.iloc[-1])
            last_dates.append(c.index[-1])
    monthly = monthly_frame(closes, allocation.TREND_UNIVERSE)
    try:
        prop = allocation.propose_rebalance(holdings, prices, monthly if len(monthly.columns) else None,
                                            contributions=contribution)
    except ValueError as e:
        missing = sorted(s for s in symbols if s not in prices)
        if missing:
            raise ApiError(f"No usable prices for {', '.join(missing)}; cannot propose trades.",
                           {"symbols": missing}, status_code=502, code="missing_prices") from e
        raise ApiError(str(e), None, status_code=422, code="empty_portfolio") from e

    band_abs, band_rel = _BAND_DEFAULTS["band_abs"], _BAND_DEFAULTS["band_rel"]
    rows = []
    for r in prop.rows:
        rows.append(M.ProposalRow(
            symbol=r.symbol, name=ETF_NAME.get(r.symbol, r.symbol), group=GROUP_OF[r.symbol], price=r.price,
            shares=r.shares, value=r.value, target=r.target, current=r.weight, drift=r.drift,
            band=max(band_abs, band_rel * r.target), outside=r.out_of_band,
            action="buy" if r.trade_shares > 0 else ("sell" if r.trade_shares < 0 else "hold"),
            trade_shares=r.trade_shares, trade_value=r.trade_value))
    trades = [M.ProposalTrade(symbol=r.symbol, action="buy" if r.trade_shares > 0 else "sell",
                              shares=abs(r.trade_shares), amount=abs(r.trade_value))
              for r in prop.trades]
    values = {r.symbol: r.value for r in prop.rows}
    after = {r.symbol: r.value + r.trade_value for r in prop.rows}
    groups = group_rows(group_targets({r.symbol: r.target for r in prop.rows}), group_shares(values),
                        group_shares(after))
    trend = trend_detail(monthly if len(monthly.columns) else None, prop.trend_scores)
    as_of = max(last_dates).date().isoformat() if last_dates else date.today().isoformat()
    return M.ProposalResponse(
        as_of=as_of, signal_month=prop.signal_month, total_value=prop.total_value, cash=prop.cash,
        contribution=prop.contributions, cash_after=prop.leftover_cash, rows=rows, trades=trades, groups=groups,
        groups_basis=GROUPS_BASIS, trend=trend, unmanaged=prop.unmanaged, notes=prop.notes,
        advisory="Advisory only: nothing is ordered. Place any trades yourself in IBKR (D1).")
