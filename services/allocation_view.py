"""Allocation proposal view (D1/D4): wraps allocation.propose_rebalance with prices and monthly closes from the
data layer, and shapes the result for the Allocation page. Advisory only: this module never touches an order path.

Also hosts what the Overview shares with it: the owner's holdings file, the asset-group map and the trend-sleeve
detail (13 month-end closes, 10-month average, 8/10/12-month votes).
"""
from __future__ import annotations

import inspect
import math
from datetime import date
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

import allocation
import config
from account import store as account_store
from account.model import AccountSnapshot
from api.errors import ApiError
from services import models as M
from services.common import finite
from services.providers import DataProvider

# Asset groups of the owner's portfolio. The first five are the US profile's; Canadian equity, Bonds, Gold & miners and
# Thematic & single names appear for CAD-listed holdings and the 'cad' profile; a sixth row 'Other' absorbs the rest.
GROUPS: list[tuple[str, str]] = [
    ("us_equity", "US equity"), ("canadian_equity", "Canadian equity"), ("intl_equity", "International equity"),
    ("treasuries", "Treasuries"), ("bonds", "Bonds"), ("tips_bills", "TIPS & T-bills"),
    ("gold_commodities", "Gold & commodities"), ("gold_miners", "Gold & miners"),
    ("thematic", "Thematic & single names"), ("other", "Other"),
]
GROUP_LABEL = dict(GROUPS)
MAX_GROUP_ROWS = 6
_BASE_GROUPS = {
    "us": ["us_equity", "intl_equity", "treasuries", "tips_bills", "gold_commodities"],
    "cad": ["us_equity", "canadian_equity", "intl_equity", "bonds", "tips_bills", "gold_miners"],
}
GROUP_OF = {
    "VTI": "us_equity", "SPY": "us_equity", "VNQ": "us_equity",
    "VEA": "intl_equity", "VWO": "intl_equity",
    "IEF": "treasuries", "TLT": "treasuries",
    "TIP": "tips_bills", "SGOV": "tips_bills",
    "GLDM": "gold_commodities", "GLD": "gold_commodities", "PDBC": "gold_commodities", "DBC": "gold_commodities",
}
# Canadian listings by ticker without the exchange suffix: (group, detail label). The detail shows next to a
# position (e.g. HXQ is US equity, Nasdaq); the group is what the allocation rows add up.
CA_CLASS = {
    "VFV": ("us_equity", "US equity"), "XUU": ("us_equity", "US equity"), "ZSP": ("us_equity", "US equity"),
    "VUN": ("us_equity", "US equity"),
    "HXQ": ("us_equity", "US equity (Nasdaq)"), "QQC": ("us_equity", "US equity (Nasdaq)"),
    "XEF": ("intl_equity", "International equity"), "VIU": ("intl_equity", "International equity"),
    "ZEA": ("intl_equity", "International equity"),
    "XEC": ("intl_equity", "International equity (EM)"), "VEE": ("intl_equity", "International equity (EM)"),
    "XIC": ("canadian_equity", "Canadian equity"), "VCN": ("canadian_equity", "Canadian equity"),
    "ZCN": ("canadian_equity", "Canadian equity"), "ZLB": ("canadian_equity", "Canadian equity"),
    "ZEB": ("canadian_equity", "Canadian equity"), "ZUT": ("canadian_equity", "Canadian equity"),
    "ZRE": ("canadian_equity", "Canadian equity"),
    "HBB": ("bonds", "Bonds"), "XBB": ("bonds", "Bonds"), "ZAG": ("bonds", "Bonds"), "VAB": ("bonds", "Bonds"),
    "ZFL": ("bonds", "Bonds"),
    "ZRR": ("tips_bills", "TIPS & T-bills"), "XRB": ("tips_bills", "TIPS & T-bills"),
    "CASH": ("tips_bills", "TIPS & T-bills"), "ZMMK": ("tips_bills", "TIPS & T-bills"),
    "XGD": ("gold_miners", "Gold & miners"), "CGL": ("gold_miners", "Gold & miners"),
    "CGL-C": ("gold_miners", "Gold & miners"), "KILO-B": ("gold_miners", "Gold & miners"),
    "CHPS": ("thematic", "Thematic & single names"), "COPP": ("thematic", "Thematic & single names"),
    "HURA": ("thematic", "Thematic & single names"), "CCO": ("thematic", "Thematic & single names"),
}
_CA_SUFFIXES = (".TO", ".V", ".NE")
ETF_NAME = {
    "VTI": "US total market", "VEA": "Developed ex-US", "VWO": "Emerging", "IEF": "Treasuries 7-10y",
    "TLT": "Treasuries 20y+", "TIP": "TIPS", "SGOV": "T-bills", "GLDM": "Gold", "PDBC": "Commodities",
    "SPY": "S&P 500", "GLD": "Gold", "DBC": "Commodities", "VNQ": "US real estate",
    "VFV.TO": "S&P 500 (CAD)", "XUU.TO": "US total market (CAD)", "XIC.TO": "Canada total market",
    "XEF.TO": "Developed ex-North America", "XEC.TO": "Emerging markets", "ZAG.TO": "Canadian bonds (intermediate)",
    "XBB.TO": "Canadian bonds", "ZFL.TO": "Canadian bonds (long)", "ZRR.TO": "Real return bonds",
    "XRB.TO": "Real return bonds", "CASH.TO": "T-bills / cash ETF", "ZMMK.TO": "Money market",
    "CGL-C.TO": "Gold (CAD hedged)", "KILO-B.TO": "Gold", "ZRE.TO": "Canadian real estate",
}


def _ca_key(symbol: str) -> Optional[str]:
    sym = symbol.upper()
    for suf in _CA_SUFFIXES:
        if sym.endswith(suf):
            return sym[: -len(suf)]
    return None


def classify(symbol: str) -> Optional[tuple[str, str]]:
    """(group key, detail label) of a symbol, or None when it is in no group."""
    sym = symbol.upper()
    if sym in GROUP_OF:
        g = GROUP_OF[sym]
        return g, GROUP_LABEL[g]
    base = _ca_key(sym)
    if base is not None:
        return CA_CLASS.get(base)
    return None


def group_key(symbol: str) -> Optional[str]:
    c = classify(symbol)
    return c[0] if c else None


_ALL_TARGET_SYMBOLS = (set(allocation.CORE_TARGETS) | set(allocation.TREND_UNIVERSE)
                       | set(allocation.CAD_CORE_TARGETS) | set(allocation.CAD_TREND_UNIVERSE)
                       | {a for _, alts in allocation.CAD_ALTERNATES for a in alts})
assert all(group_key(x) for x in _ALL_TARGET_SYMBOLS), [x for x in _ALL_TARGET_SYMBOLS if not group_key(x)]

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


@dataclass
class OwnerInputs:
    """The owner's account as the views need it: quantities, listing currencies, base-currency cash (negative =
    margin loan) and, when the latest broker snapshot is the source, that snapshot (with its FX rates)."""
    holdings: dict[str, float]
    currency: dict[str, str]
    base: str
    snapshot: Optional[AccountSnapshot] = None
    source: str = "holdings_csv"

    @property
    def cash(self) -> float:
        return float(self.holdings.get(allocation.CASH_KEY, 0.0))


def owner_inputs(holdings: Optional[dict] = None, path=None) -> OwnerInputs:
    """Latest IBKR snapshot when one exists, else the holdings CSV (config.HOLDINGS_CSV). Explicit `holdings`
    (tests, the CLI) bypass both. CASH may be negative; a CSV `currency` column defaults to BASE_CURRENCY."""
    base = config.BASE_CURRENCY
    if holdings is not None:
        return OwnerInputs(dict(holdings), {}, base)
    snap = account_store.latest_snapshot() if path is None else None
    if snap is not None and snap.valued_positions():
        return OwnerInputs(snap.holdings(), snap.currencies(), snap.base_currency, snap, "ibkr")
    p = Path(path if path is not None else config.HOLDINGS_CSV)
    load_owner_holdings(p)                                   # raises the 404 / 422 ApiErrors
    h, ccy = allocation.load_holdings_ex(p, default_currency=base, infer=True)
    return OwnerInputs(h, ccy, base)


def native_currency(symbol: str, inp: OwnerInputs) -> str:
    """Listing currency: from the account when held; else CAD for .TO/.V/.NE and USD for a plain US ticker (on both
    the broker and the CSV path), so a US price is never mistaken for a base-currency price."""
    c = inp.currency.get(symbol)
    if c:
        return c
    return allocation.infer_listing_currency(symbol, inp.base)


def fx_rate_now(closes: "Closes", ccy: str, inp: OwnerInputs) -> Optional[float]:
    """Base units per 1 `ccy`: the snapshot's rate, else the latest yfinance '<CCY><BASE>=X' close."""
    if ccy == inp.base:
        return 1.0
    if inp.snapshot is not None:
        r = inp.snapshot.fx(ccy)
        if r:
            return float(r)
    h = closes.get(f"{ccy}{inp.base}=X")
    return float(h.iloc[-1]) if h is not None and len(h) else None


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
def trend_detail(monthly: Optional[pd.DataFrame], scores: dict[str, float], as_of: Optional[date] = None,
                 universe=None) -> list[M.TrendAsset]:
    """Per trend asset: last 13 completed month-end closes with the 10-month average, the 8/10/12 votes (same
    rule as allocation.trend_signals), score, state and the weight in the whole portfolio."""
    universe = list(universe or allocation.TREND_UNIVERSE)
    each = allocation.TREND_SLEEVE_PCT / len(universe)
    df = allocation._completed_closes(monthly, as_of) if monthly is not None and len(monthly.columns) else None
    out = []
    for a in universe:
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
        out[group_key(s) or "other"] += w
    return out


def group_shares(values: dict[str, float]) -> dict[str, float]:
    """Share of the summed value per group (symbols outside every group ignored)."""
    out = {k: 0.0 for k, _ in GROUPS}
    for s, v in values.items():
        g = group_key(s)
        if g:
            out[g] += v
    tot = sum(out.values())
    return {k: (v / tot if tot > 0 else 0.0) for k, v in out.items()}


GROUPS_BASIS = ("Share of invested value (cash excluded); targets are core 90% + trend sleeve 10% at today's "
                "trend signals. Groups beyond six are folded into Other.")


def group_rows(target: dict[str, float], now: dict[str, float], after: Optional[dict[str, float]] = None,
               profile: Optional[str] = None) -> list[M.GroupWeight]:
    """Rows for the profile's base groups plus any group with a target or a holding. When that is more than
    MAX_GROUP_ROWS, the groups with the smallest max(now, target) are folded into 'Other'."""
    base = _BASE_GROUPS.get(profile or config.ALLOCATION_PROFILE, _BASE_GROUPS["us"])
    eps = 1e-9
    keys = [k for k, _ in GROUPS if k in base or target.get(k, 0.0) > eps or now.get(k, 0.0) > eps
            or (after or {}).get(k, 0.0) > eps]
    if len(keys) > MAX_GROUP_ROWS:
        rest = [k for k in keys if k != "other"]
        rest.sort(key=lambda k: (-max(now.get(k, 0.0), target.get(k, 0.0), (after or {}).get(k, 0.0)),
                                 [g for g, _ in GROUPS].index(k)))
        keep = set(rest[:MAX_GROUP_ROWS - 1])
        folded = [k for k in rest if k not in keep]
        for d in (target, now, after):
            if d is not None:
                d["other"] = d.get("other", 0.0) + sum(d.get(k, 0.0) for k in folded)
        keys = [k for k, _ in GROUPS if k in keep or k == "other"]
    return [M.GroupWeight(key=k, label=GROUP_LABEL[k], target=target.get(k, 0.0), now=now.get(k, 0.0),
                          drift=now.get(k, 0.0) - target.get(k, 0.0), after=None if after is None else after.get(k, 0.0))
            for k in keys if k != "other" or target.get("other", 0.0) > eps or now.get("other", 0.0) > eps
            or (after or {}).get("other", 0.0) > eps]


# ---------------------------------------------------------------- proposal
def _unmanaged_value(inp: OwnerInputs, closes: "Closes", managed: set) -> tuple[float, list[str]]:
    """(base-currency value of the held symbols outside the target list, symbols that could not be valued).
    The broker's own market value is preferred; otherwise last close x FX."""
    value, unvalued = 0.0, []
    broker: dict[str, float] = {}
    if inp.snapshot is not None:
        for p in inp.snapshot.valued_positions():
            rate = inp.snapshot.fx(p.currency)
            if p.quantity and p.market_value is not None and rate:
                broker[p.symbol] = broker.get(p.symbol, 0.0) + p.market_value * rate
    for s, q in inp.holdings.items():
        if s == allocation.CASH_KEY or not q or s in managed:
            continue
        if s in broker:
            value += broker[s]
            continue
        c = closes.get(s)
        rate = fx_rate_now(closes, native_currency(s, inp), inp) if c is not None else None
        if c is None or rate is None:
            unvalued.append(s)
            continue
        value += float(q) * float(c.iloc[-1]) * rate
    return value, sorted(unvalued)


def proposal(provider: DataProvider, contribution: float = 0.0, holdings: Optional[dict] = None
             ) -> M.ProposalResponse:
    inp = owner_inputs(holdings)
    prof = allocation.resolve_profile(inp.holdings)
    closes = Closes(provider)
    symbols = sorted(set(prof.core) | set(prof.trend_universe))
    prices, last_dates = {}, []
    for s in symbols:
        c = closes.get(s)
        rate = fx_rate_now(closes, native_currency(s, inp), inp) if c is not None else None
        if c is not None and rate is not None:
            prices[s] = float(c.iloc[-1]) * rate          # base currency
            last_dates.append(c.index[-1])
    monthly = monthly_frame(closes, prof.trend_universe)
    unmanaged_value, unvalued = _unmanaged_value(inp, closes, set(symbols))
    try:
        prop = allocation.propose_rebalance(inp.holdings, prices, monthly if len(monthly.columns) else None,
                                            contributions=contribution, profile=prof,
                                            unmanaged_value=unmanaged_value,
                                            account_leverage=inp.snapshot.leverage if inp.snapshot else None,
                                            currency=inp.base)
    except ValueError as e:
        missing = sorted(s for s in symbols if s not in prices)
        if missing:
            raise ApiError(f"No usable prices for {', '.join(missing)}; cannot propose trades.",
                           {"symbols": missing}, status_code=502, code="missing_prices") from e
        raise ApiError(str(e), None, status_code=422, code="invalid_proposal_input") from e
    if unvalued:
        prop.notes.append("No price for the unmanaged holding(s) " + ", ".join(unvalued)
                          + ": left out of the net value.")

    band_abs, band_rel = _BAND_DEFAULTS["band_abs"], _BAND_DEFAULTS["band_rel"]
    rows = []
    for r in prop.rows:
        rows.append(M.ProposalRow(
            symbol=r.symbol, name=ETF_NAME.get(r.symbol, r.symbol), group=group_key(r.symbol) or "other",
            price=r.price, shares=r.shares, value=r.value, target=r.target, current=r.weight, drift=r.drift,
            band=max(band_abs, band_rel * r.target), outside=r.out_of_band,
            action="buy" if r.trade_shares > 0 else ("sell" if r.trade_shares < 0 else "hold"),
            trade_shares=r.trade_shares, trade_value=r.trade_value))
    trades = [M.ProposalTrade(symbol=r.symbol, action="buy" if r.trade_shares > 0 else "sell",
                              shares=abs(r.trade_shares), amount=abs(r.trade_value))
              for r in prop.trades]
    values = {r.symbol: r.value for r in prop.rows}
    after = {r.symbol: r.value + r.trade_value for r in prop.rows}
    groups = group_rows(group_targets({r.symbol: r.target for r in prop.rows}), group_shares(values),
                        group_shares(after), prof.name)
    trend = trend_detail(monthly if len(monthly.columns) else None, prop.trend_scores,
                         universe=prof.trend_universe)
    as_of = max(last_dates).date().isoformat() if last_dates else date.today().isoformat()
    return M.ProposalResponse(
        as_of=as_of, signal_month=prop.signal_month, total_value=prop.total_value, cash=prop.cash,
        contribution=prop.contributions, cash_after=prop.leftover_cash, rows=rows, trades=trades, groups=groups,
        groups_basis=GROUPS_BASIS, trend=trend, unmanaged=prop.unmanaged, notes=prop.notes,
        advisory="Advisory only: nothing is ordered. Place any trades yourself in IBKR (D1).",
        profile=prof.name, margin_loan=prop.margin_loan or None, leverage=prop.leverage, base_currency=inp.base,
        managed_value=prop.managed_value, unmanaged_value=prop.unmanaged_value,
        contribution_to_loan=prop.contribution_to_loan or None,
        margin_loan_after=prop.margin_loan_after if prop.margin_loan else None)
