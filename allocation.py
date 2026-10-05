"""Allocation proposals for the strategic core and the trend sleeve (decisions D1, D4).

This module only COMPUTES a proposal (target weights, drift, whole-share trades, trend
signals) and renders it as text. It never places orders and deliberately imports nothing
from execution / brokers / paper_trader: the owner places any trades manually.
Prices and holdings are inputs; fetching them is the caller's job.

Targets (docs/research/portfolio-recommendations.md section 2): 90% core + 10% trend.
Per-symbol target = CORE_PCT * core weight + TREND_SLEEVE_PCT * trend weight, where each
trend asset gets (1/N) * score of the sleeve and the unheld remainder sits in T-bills.

Two target profiles (config.ALLOCATION_PROFILE): 'us' (the D4 core in US tickers) and 'cad' (a
CAD-listed mapping, approved in docs/decisions.md D14, the default). Proposals never use margin and never allocate the
loan to the sleeves: buys are funded by own cash + sale proceeds + contributions left after repaying the loan.
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


@dataclass(frozen=True)
class Profile:
    name: str
    core: dict
    trend_universe: tuple
    cash_symbol: str
    proposed: bool = False                       # True: weights await owner approval (D14)
    alternates: tuple = ()                       # ((primary, (alternate, ...)), ...) interchangeable listings


# CAD-listed mapping of the D4 core (D14, approved by the owner 2026-10-05).
# Canadian home equity takes 10 points from US equity; the 0-5% commodities slot has no CAD-listed equivalent, so
# its 5 points go to US equity (32 - 10 + 5 = 27).
CAD_CORE_TARGETS = {
    "VFV.TO": 0.27,      # US equity (alt XUU.TO)
    "XIC.TO": 0.10,      # Canadian equity (new: D4 had none)
    "XEF.TO": 0.18,      # developed ex-North America
    "XEC.TO": 0.05,      # emerging markets
    "ZAG.TO": 0.13,      # intermediate bonds (alt XBB.TO)
    "ZFL.TO": 0.07,      # long bonds
    "ZRR.TO": 0.07,      # TIPS-equivalent real-return bonds (alt XRB.TO)
    "CASH.TO": 0.08,     # T-bills (alt ZMMK.TO)
    "CGL-C.TO": 0.05,    # gold, CAD hedged (IBKR symbol CGL.C; alt KILO-B.TO)
}
CAD_TREND_UNIVERSE = ("VFV.TO", "XEF.TO", "XBB.TO", "CGL-C.TO", "ZRE.TO")   # SPY, VEA, IEF, GLD, VNQ; no DBC equivalent
CAD_ALTERNATES = (("VFV.TO", ("XUU.TO",)), ("ZAG.TO", ("XBB.TO",)), ("ZRR.TO", ("XRB.TO",)),
                  ("CASH.TO", ("ZMMK.TO",)), ("CGL-C.TO", ("KILO-B.TO",)))
assert abs(sum(CAD_CORE_TARGETS.values()) - 1.0) < 1e-9

PROFILES = {
    "us": Profile("us", CORE_TARGETS, tuple(TREND_UNIVERSE), CASH_SYMBOL),
    "cad": Profile("cad", CAD_CORE_TARGETS, CAD_TREND_UNIVERSE, "CASH.TO",
                   alternates=CAD_ALTERNATES),
}


def active_profile(name: str | None = None) -> Profile:
    import config
    return PROFILES.get((name or config.ALLOCATION_PROFILE or "us").lower(), PROFILES["us"])


def resolve_profile(holdings: dict | None, profile: Profile | None = None) -> Profile:
    """Swap a primary listing for its interchangeable alternate when only the alternate is held (VFV -> XUU ...)."""
    prof = profile or active_profile()
    if not prof.alternates or not holdings:
        return prof
    swap = {}
    for primary, alts in prof.alternates:
        if not holdings.get(primary):
            for a in alts:
                if holdings.get(a):
                    swap[primary] = a
                    break
    if not swap:
        return prof
    core = {swap.get(k, k): v for k, v in prof.core.items()}
    return Profile(prof.name, core, tuple(swap.get(a, a) for a in prof.trend_universe),
                   swap.get(prof.cash_symbol, prof.cash_symbol), prof.proposed, prof.alternates)


def infer_listing_currency(symbol: str, base: str) -> str:
    """Listing currency of a symbol with no stated currency: .TO/.V/.NE -> CAD, a plain US ticker -> USD, any other
    exchange suffix -> the base currency."""
    sym = symbol.upper()
    if sym.endswith((".TO", ".V", ".NE")):
        return "CAD"
    return base if "." in sym else "USD"


def load_holdings_ex(csv_path, default_currency: str | None = None, infer: bool = False
                     ) -> tuple[dict[str, float], dict[str, str]]:
    """Read ``symbol,quantity[,currency]`` rows -> (holdings, currency per symbol).

    A row with symbol CASH holds cash in the base currency and MAY BE NEGATIVE (a margin loan); every other
    quantity must be finite and >= 0. The optional ``currency`` column (default: `default_currency`, else
    config.BASE_CURRENCY) is the listing currency of a position. With ``infer=True`` a blank cell is inferred from
    the symbol instead (infer_listing_currency: a plain US ticker is USD, not the base currency).
    """
    import config
    base = (default_currency or config.BASE_CURRENCY).upper()
    out: dict[str, float] = {}
    ccy: dict[str, str] = {}
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
            explicit = (row.get("currency") or "").upper()
            cur = explicit or (infer_listing_currency(sym, base) if infer and sym != CASH_KEY else base)
            if sym == CASH_KEY:
                if not math.isfinite(qty):
                    raise ValueError("quantity for CASH must be a finite number")
                if cur != base:
                    raise ValueError(f"the CASH row is in the base currency ({base}), not {cur}")
            else:
                if not math.isfinite(qty) or qty < 0:
                    raise ValueError(f"quantity for {sym} must be a finite number >= 0")
                ccy[sym] = cur
            out[sym] = out.get(sym, 0.0) + qty
    return out, ccy


def load_holdings(csv_path) -> dict[str, float]:
    """Read ``symbol,quantity[,currency]`` rows. A row with symbol CASH holds cash in the base currency and may be
    negative (margin loan); other quantities must be >= 0. See load_holdings_ex for the currencies."""
    return load_holdings_ex(csv_path)[0]


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


def target_weights(scores: dict[str, float] | None = None, profile: Profile | None = None) -> dict[str, float]:
    """Combined per-symbol target (sums to 1). Assets with no score are treated as invested."""
    scores = scores or {}
    prof = profile or active_profile()
    w = {s: CORE_PCT * x for s, x in prof.core.items()}
    each = TREND_SLEEVE_PCT / len(prof.trend_universe)
    for a in prof.trend_universe:
        sc = scores.get(a, 1.0)
        w[a] = w.get(a, 0.0) + each * sc
        w[prof.cash_symbol] = w.get(prof.cash_symbol, 0.0) + each * (1.0 - sc)
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
    total_value: float          # net value: managed + unmanaged + cash (negative cash = loan), before contributions
    cash: float
    contributions: float
    leftover_cash: float
    trend_scores: dict[str, float]
    signal_month: str | None
    unmanaged: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    margin_loan: float = 0.0            # max(-cash, 0): borrowed money, never spent by a proposal
    leverage: float | None = None       # the account's own gross / net when given, else positions / net value
    profile: str = "us"
    unmanaged_value: float = 0.0        # value of holdings outside the target list, held fixed
    managed_value: float = 0.0          # managed positions + max(cash, 0): what the targets apply to (the loan is not allocated)
    currency: str = ""
    contribution_to_loan: float = 0.0   # part of the contribution that repays the margin loan (before any buy)
    margin_loan_after: float = 0.0      # the loan once that part is applied

    @property
    def trades(self) -> list[SleeveRow]:
        return [r for r in self.rows if r.trade_shares]

    def render(self) -> str:
        L = ["ALLOCATION PROPOSAL (advisory only - no orders are placed)",
             f"Net value ${self.total_value:,.2f}  managed sleeve ${self.managed_value:,.2f}  "
             f"unmanaged ${self.unmanaged_value:,.2f}  cash ${self.cash:,.2f}  "
             f"contributions ${self.contributions:,.2f}"]
        L.append(f"{'Symbol':<7}{'Price':>9}{'Shares':>9}{'Value':>12}{'Wt%':>7}{'Tgt%':>7}"
                 f"{'Drift':>7}  Band  Trade")
        for r in self.rows:
            t = f"{'BUY' if r.trade_shares > 0 else 'SELL'} {abs(r.trade_shares)} (${abs(r.trade_value):,.0f})" \
                if r.trade_shares else "-"
            L.append(f"{r.symbol:<7}{r.price:>9.2f}{r.shares:>9.2f}{r.value:>12,.2f}"
                     f"{r.weight * 100:>7.1f}{r.target * 100:>7.1f}{r.drift * 100:>+7.1f}  "
                     f"{'OUT' if r.out_of_band else 'ok':<5} {t}")
        if self.contribution_to_loan > 0:
            L.append(f"Apply ${self.contribution_to_loan:,.2f} to the margin loan "
                     f"(${self.margin_loan_after:,.2f} remaining)")
        L.append(f"Cash left after trades: ${self.leftover_cash:,.2f}"
                 + (f"  (margin loan after repayment: ${self.margin_loan_after:,.2f}, reported separately)"
                    if self.margin_loan_after > 0 else ""))
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
                      min_trade_pct: float = 0.02, as_of: date | None = None,
                      profile: Profile | None = None, unmanaged_value: float | None = None,
                      account_leverage: float | None = None, currency: str = "") -> Proposal:
    """Propose whole-share trades toward target. Never places orders.

    A symbol is out of band when |drift| > max(band_abs, band_rel * target). Sells happen
    only for symbols out of band (sold down to target). Buys are funded by cash +
    contributions + sell proceeds and go to underweight symbols, largest deficit first.
    Trades under ``min_trade_pct`` of value are skipped (buys funded by contributions are
    exempt). ``holdings['CASH']`` is dollars of cash and may be negative (margin loan): a proposal never uses
    margin, so buys are funded by max(cash, 0) + contributions + sale proceeds beyond the loan.

    Net value = managed positions + unmanaged value + cash (negative cash = margin loan). Unmanaged holdings (not in
    the target list) are held fixed: ``unmanaged_value`` is their value in the same currency as ``prices`` (None:
    unknown, treated as 0). The loan is NOT allocated to the sleeves: targets apply to the managed sleeve = managed
    positions + max(cash, 0). Trades are self-funded: band-driven sells fund buys inside the sleeve, and buys may use
    only own cash + sale proceeds + the part of the contribution left after repaying the loan (contribution_to_loan =
    min(contribution, loan)). The platform never proposes sells to pay the loan down. With nothing to allocate (no
    managed holdings, no own cash, no spare contribution) there is nothing to rebalance: no trades and a note.
    ``account_leverage`` is the broker's gross / net; without it leverage is (managed + unmanaged) / net value.
    """
    if contributions < 0:
        raise ValueError("contributions must be >= 0")
    prof = resolve_profile(holdings, profile)
    scores, sig_month = trend_signals(monthly_closes, as_of, assets=list(prof.trend_universe))
    targets = target_weights(scores, prof)
    notes = []
    if monthly_closes is not None:
        miss = [a for a in prof.trend_universe if a not in scores]
        if miss:
            notes.append("no usable trend data for " + ", ".join(miss) + " (assumed invested)")

    cash = float(holdings.get(CASH_KEY, 0.0))
    own_cash = max(cash, 0.0)
    loan = max(-cash, 0.0)
    to_loan = min(contributions, loan)           # contributions repay the margin loan first
    avail_contrib = contributions - to_loan
    shares = {s: float(q) for s, q in holdings.items() if s != CASH_KEY and s in targets}
    unmanaged = sorted(s for s, q in holdings.items() if s != CASH_KEY and s not in targets and q)
    missing = [s for s in targets if s not in prices]
    bad = [s for s in targets if s in prices and not (math.isfinite(prices[s]) and prices[s] > 0)]
    if missing or bad:
        raise ValueError(f"missing/invalid prices for: {sorted(set(missing) | set(bad))}")

    value = {s: shares.get(s, 0.0) * prices[s] for s in targets}
    managed = sum(value.values())
    unmanaged_v = max(float(unmanaged_value or 0.0), 0.0)
    sleeve = managed + own_cash                  # what the targets apply to: the loan is NOT allocated to it
    net = sleeve - loan + unmanaged_v            # net value: the whole account, loan included
    live = sleeve > 0
    post = sleeve + avail_contrib
    rows = {s: SleeveRow(s, prices[s], shares.get(s, 0.0), value[s], value[s] / sleeve if live else 0.0,
                         targets[s], (value[s] / sleeve if live else 0.0) - targets[s],
                         live and abs(value[s] / sleeve - targets[s]) > max(band_abs, band_rel * targets[s]))
            for s in targets}

    cur = f"{currency} " if currency else ""
    if account_leverage is not None and math.isfinite(account_leverage):
        leverage = float(account_leverage)
    else:
        leverage = (managed + unmanaged_v) / net if loan > 0 and net > 0 else None
    tradable = post > 0
    if not tradable:
        notes.append("Nothing to rebalance: there are no managed holdings, no own cash and no contribution "
                     "left after the margin loan, so no trades are proposed. Unmanaged holdings are left alone.")
    if loan > 0:
        notes.insert(0, f"Reduce margin loan first: {cur}{loan:,.2f} is borrowed"
                        + (f" ({leverage:.2f}x leverage)" if leverage else "")
                        + ". This platform never proposes sales to pay it down (your decision); "
                          "contributions repay it before anything is bought, and trades here are self-funded.")
    if to_loan > 0:
        notes.append(f"Apply {cur}{to_loan:,.2f} to the margin loan"
                     + (f" ({cur}{loan - to_loan:,.2f} would remain)." if loan - to_loan > 0 else " (repaid in full)."))
    min_trade = min_trade_pct * post
    avail = own_cash + avail_contrib
    proceeds = 0.0
    # Sells: only out-of-band overweights, down to target, whole shares (never more than held).
    for r in rows.values():
        excess = r.value - r.target * post
        if r.out_of_band and excess > 0:
            n = min(int(round(excess / r.price)), int(math.floor(r.shares)))
            if n > 0 and n * r.price >= min_trade:
                r.trade_shares, r.trade_value = -n, -n * r.price
                proceeds += n * r.price
    avail += proceeds                       # band-driven sells fund buys inside the managed sleeve
    # Buys: proportional to deficits, whole shares, then single-share top-ups.
    deficit = {s: r.target * post - r.value for s, r in rows.items()
               if r.trade_shares == 0 and r.value < r.target * post}
    tot_def = sum(deficit.values())
    buys = {s: 0 for s in deficit}
    if tradable and tot_def > 0 and avail > 0:
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
        if n and (avail_contrib > 0 or n * prices[s] >= min_trade):
            rows[s].trade_shares, rows[s].trade_value = n, n * prices[s]
    leftover = own_cash + avail_contrib - sum(r.trade_value for r in rows.values())
    ordered = sorted(rows.values(), key=lambda r: -r.target)
    return Proposal(ordered, net, cash, contributions, leftover, scores, sig_month,
                    unmanaged, notes, margin_loan=loan, leverage=leverage, profile=prof.name,
                    unmanaged_value=unmanaged_v, managed_value=sleeve, currency=currency,
                    contribution_to_loan=to_loan, margin_loan_after=loan - to_loan)
