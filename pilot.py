"""Portfolio Pilot: a risk-controlled core + trend-gated satellites, with leverage set by a volatility target.

Rules are frozen in docs/pilot/PREREG.md (decision D25). The single-look backtest (docs/pilot/backtest.md) rejected the
vol-target rule, so proposals default to the static strategic mix at a fixed, owner-chosen leverage (DEFAULT_POLICY);
the "trend" and "pilot" modes stay available for comparison only. This module only COMPUTES: target weights, trend
scores, the forecast-volatility leverage, a backtest simulator and a whole-share rebalance proposal for the owner's
account. It never places orders and imports nothing from execution, brokers or paper_trader (D1): the owner places
every trade by hand.

Frequency-agnostic: pass a price frame (index = bar dates, one column per sleeve) and a `Freq` (weekly for the
long-history test, daily for the owner's TSX ETFs). Signals at bar t use closes up to and including t; trades are
made at that close.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Sleeve:
    key: str
    group: str            # "core" or "satellite"
    weight: float
    tsx: tuple            # owner's TSX tickers that hold this sleeve (first = default for new buys)
    proxy: str            # US proxy for the long-history test


SLEEVES: tuple[Sleeve, ...] = (
    Sleeve("us_equity", "core", 0.26, ("VFV", "XUU"), "SPY"),
    Sleeve("nasdaq", "core", 0.08, ("HXQ",), "QQQ"),
    Sleeve("intl", "core", 0.14, ("XEF",), "EFA"),
    Sleeve("canada", "core", 0.12, ("ZEB", "ZLB"), "EWC"),
    Sleeve("utilities", "core", 0.04, ("ZUT",), "XLU"),
    Sleeve("bonds", "core", 0.08, ("HBB",), "IEF"),
    Sleeve("gold_miners", "satellite", 0.08, ("XGD",), "GDX"),
    Sleeve("gold", "satellite", 0.06, ("CGL",), "GLD"),
    Sleeve("uranium", "satellite", 0.06, ("CCO", "HURA"), "CCJ"),
    Sleeve("copper", "satellite", 0.03, ("COPP",), "FCX"),
    Sleeve("semis", "satellite", 0.05, ("CHPS",), "SMH"),
)
assert abs(sum(s.weight for s in SLEEVES) - 1.0) < 1e-9

VOL_TARGET, LEV_MIN, LEV_MAX = 0.15, 0.5, 1.5
DRIFT_BAND, LEV_BAND = 0.03, 0.10
COST = 0.0010                 # per traded dollar
BORROW_RATE = 0.0358          # owner's margin rate, per year
MIN_TRADE = 100.0             # C$, proposals only


@dataclass(frozen=True)
class Freq:
    name: str
    per_year: int
    sma: int                  # trend: close above this SMA
    mid: int                  # trend: return over this many bars > 0
    long: int                 # trend: return over this many bars > 0 (and minimum history)
    halflife: int             # EWMA covariance half-life in bars


WEEKLY = Freq("weekly", 52, 40, 26, 52, 13)
DAILY = Freq("daily", 252, 200, 126, 252, 63)

SLEEVE_BY_TSX = {t: s.key for s in SLEEVES for t in s.tsx}


# ------------------------------------------------------------------------------------------------- signals
def trend_score(close: pd.Series, f: Freq) -> Optional[float]:
    """Average of three votes (above the SMA, mid-horizon return > 0, long-horizon return > 0); None if fewer bars
    than the long lookback (+1 for the return)."""
    c = close.dropna()
    if len(c) < f.long + 1:
        return None
    last = c.iloc[-1]
    votes = (last > c.iloc[-f.sma:].mean(), last > c.iloc[-1 - f.mid], last > c.iloc[-1 - f.long])
    return sum(bool(v) for v in votes) / 3.0


def available(prices: pd.DataFrame, f: Freq) -> list[str]:
    """Sleeves with at least f.long + 1 bars of history at the last row."""
    return [k for k in prices.columns if prices[k].dropna().shape[0] >= f.long + 1 and pd.notna(prices[k].iloc[-1])]


def target_weights(prices: pd.DataFrame, f: Freq, gate: bool = True,
                   sleeves: tuple[Sleeve, ...] = SLEEVES) -> tuple[dict[str, float], dict[str, Optional[float]]]:
    """Unlevered target weights (sum 1) at the last row of `prices`, and the satellite trend scores.
    Sleeves without enough history are dropped and their weight spread pro rata; weight freed by the trend gate
    goes to the core sleeves pro rata."""
    have = set(available(prices, f))
    base = {s.key: s.weight for s in sleeves if s.key in have}
    if not base:
        return {}, {}
    tot = sum(base.values())
    base = {k: v / tot for k, v in base.items()}
    scores: dict[str, Optional[float]] = {}
    w = dict(base)
    if gate:
        freed = 0.0
        for s in sleeves:
            if s.group != "satellite" or s.key not in w:
                continue
            sc = trend_score(prices[s.key], f)
            scores[s.key] = sc
            new = w[s.key] * (sc if sc is not None else 1.0)
            freed += w[s.key] - new
            w[s.key] = new
        core = [s.key for s in sleeves if s.group == "core" and s.key in w]
        core_tot = sum(base[k] for k in core)
        if core_tot > 0:
            for k in core:
                w[k] += freed * base[k] / core_tot
    return w, scores


def ewma_cov(returns: pd.DataFrame, f: Freq) -> pd.DataFrame:
    """Annualised EWMA covariance of the last row (RiskMetrics-style, half-life in bars)."""
    r = returns.dropna(how="all").fillna(0.0)
    cov = r.ewm(halflife=f.halflife, min_periods=max(10, f.halflife)).cov()
    last = cov.index.get_level_values(0)[-1]
    return cov.loc[last] * f.per_year


def forecast_vol(weights: dict[str, float], cov: pd.DataFrame) -> float:
    keys = [k for k in weights if k in cov.index]
    w = np.array([weights[k] for k in keys])
    c = cov.loc[keys, keys].to_numpy()
    return float(math.sqrt(max(w @ c @ w, 0.0)))


def target_leverage(weights: dict[str, float], prices: pd.DataFrame, f: Freq) -> tuple[float, float]:
    """(leverage, forecast vol of the unlevered target)."""
    keys = list(weights)
    rets = prices[keys].pct_change(fill_method=None)
    vol = forecast_vol(weights, ewma_cov(rets, f))
    if vol <= 0 or not math.isfinite(vol):
        return 1.0, float("nan")
    return float(min(max(VOL_TARGET / vol, LEV_MIN), LEV_MAX)), vol


# ---------------------------------------------------------------------------------------------- simulator
@dataclass
class SimResult:
    name: str
    equity: pd.Series
    leverage: pd.Series
    cushion: pd.Series
    traded: float
    costs: float
    financing: float
    notes: list = field(default_factory=list)


def simulate(prices: pd.DataFrame, f: Freq, name: str, mode: str, start, fixed_weights: Optional[dict] = None,
             fixed_lev: float = 1.0) -> SimResult:
    """mode: 'static' (fixed_weights, fixed_lev, monthly), 'trend' (gated weights, fixed_lev, monthly),
    'pilot' (gated weights, vol-target leverage, checked every bar for weekly data or every week for daily data,
    traded only outside the bands). Equity starts at 1."""
    idx = prices.index[prices.index >= pd.Timestamp(start)]
    hold = {k: 0.0 for k in prices.columns}
    cash, eq0 = 1.0, 1.0
    eqs, levs, cush = {}, {}, {}
    traded = costs = fin = 0.0
    last_period = None
    for i, t in enumerate(idx):
        pos = prices.index.get_loc(t)
        if i > 0:
            prev = prices.index[pos - 1]
            for k in hold:
                p0, p1 = prices[k].iloc[pos - 1], prices[k].iloc[pos]
                if hold[k] and pd.notna(p0) and pd.notna(p1) and p0 > 0:
                    hold[k] *= p1 / p0
            if cash < 0:
                days = (t - prev).days
                charge = -cash * BORROW_RATE * days / 365.0
                cash -= charge
                fin += charge
        gross = sum(hold.values())
        equity = gross + cash
        if equity <= 0:
            eqs[t], levs[t], cush[t] = 0.0, float("nan"), 0.0
            break
        hist = prices.iloc[: pos + 1]
        period = (t.year, t.month) if mode != "pilot" else (t.isocalendar()[0], t.isocalendar()[1])
        decide = period != last_period
        if decide:
            last_period = period
            if mode == "static":
                have = [k for k in fixed_weights if pd.notna(prices[k].iloc[pos])]
                tot = sum(fixed_weights[k] for k in have)
                w = {k: fixed_weights[k] / tot for k in have}
                lev = fixed_lev
            else:
                w, _ = target_weights(hist, f, gate=True)
                lev = fixed_lev if mode == "trend" else target_leverage(w, hist, f)[0]
            tgt = {k: equity * w.get(k, 0.0) * lev for k in hold}
            if mode == "pilot" and gross > 0:
                drift = max(abs(hold[k] - tgt[k]) / equity for k in hold)
                if drift <= DRIFT_BAND and abs(gross / equity - lev) <= LEV_BAND:
                    tgt = None
            if tgt is not None:
                trade = sum(abs(tgt[k] - hold[k]) for k in hold)
                cost = COST * trade
                cash -= sum(tgt[k] - hold[k] for k in hold) + cost
                hold = dict(tgt)
                traded += trade
                costs += cost
        gross = sum(hold.values())
        equity = gross + cash
        eqs[t] = equity / eq0
        levs[t] = gross / equity if equity > 0 else float("nan")
        cush[t] = equity / gross if gross > 0 else 1.0
    return SimResult(name, pd.Series(eqs), pd.Series(levs), pd.Series(cush), traded, costs, fin)


def metrics(r: SimResult, f: Freq) -> dict:
    e = r.equity
    rets = e.pct_change().dropna()
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 and e.iloc[-1] > 0 else -1.0
    vol = rets.std() * math.sqrt(f.per_year)
    dd = (e / e.cummax() - 1).min()
    w12 = (e / e.shift(f.per_year) - 1).min()
    return {"name": r.name, "cagr": cagr, "vol": vol, "sharpe": rets.mean() / rets.std() * math.sqrt(f.per_year)
            if rets.std() > 0 else float("nan"), "max_dd": dd, "worst_12m": w12, "avg_lev": r.leverage.mean(),
            "max_lev": r.leverage.max(), "min_cushion": r.cushion.min(),
            "turnover": r.traded / yrs if yrs > 0 else float("nan"), "costs": r.costs, "financing": r.financing,
            "final": e.iloc[-1], "years": yrs}


# --------------------------------------------------------------------------------------------- proposal
def sleeve_prices(ticker_closes: dict[str, pd.Series], sleeves: tuple[Sleeve, ...] = SLEEVES) -> pd.DataFrame:
    """One price series per sleeve from the owner's TSX tickers: the equal-weight blend of the daily returns of the
    member tickers that have data on each day, chained from 1."""
    out = {}
    for s in sleeves:
        cols = [ticker_closes[t] for t in s.tsx if t in ticker_closes]
        if not cols:
            continue
        df = pd.concat(cols, axis=1)
        r = df.pct_change(fill_method=None).mean(axis=1, skipna=True)
        first = df.apply(lambda c: c.first_valid_index()).min()
        r = r[r.index > first]
        out[s.key] = pd.concat([pd.Series([1.0], index=[first]), (1 + r.fillna(0)).cumprod()])
    return pd.DataFrame(out).sort_index()


@dataclass
class Proposal:
    asof: str
    nlv: float
    cash: float
    weights: dict
    scores: dict
    leverage: float
    forecast_vol: float
    current_leverage: float
    rows: list
    unmanaged: list
    stress: dict
    rebalance_needed: bool
    policy: dict = field(default_factory=dict)
    alerts: list = field(default_factory=list)


# Policy after the D25 single look: the vol-target ("pilot") rule failed its pre-registered test and is NOT the
# default. The default is the static strategic mix at a fixed leverage chosen by the owner (S1 in the backtest).
DEFAULT_POLICY = {"mode": "static", "leverage": 1.45}
CUSHION_ALERT, STRESS_ALERT = 0.45, 0.30   # an unlevered all-equity book loses ~20-25% in the same shock


def propose(positions: dict[str, dict], cash: float, ticker_closes: dict[str, pd.Series], f: Freq = DAILY,
            asof: Optional[str] = None, policy: Optional[dict] = None) -> Proposal:
    """positions: {TSX symbol: {"qty": float, "price": float}} (CAD); cash: CAD cash (negative = margin loan).
    policy: {"mode": "static"|"trend"|"pilot", "leverage": float} (leverage ignored for "pilot", capped at LEV_MAX).
    Returns target weights, leverage, whole-share trades per ticker, unmanaged symbols and two stress tests."""
    pol = {**DEFAULT_POLICY, **(policy or {})}
    prices = sleeve_prices(ticker_closes)
    w, scores = target_weights(prices, f, gate=pol["mode"] in ("trend", "pilot"))
    lev_vt, vol = target_leverage(w, prices, f)
    lev = lev_vt if pol["mode"] == "pilot" else min(float(pol["leverage"]), LEV_MAX)
    value = {sym: p["qty"] * p["price"] for sym, p in positions.items()}
    nlv = sum(value.values()) + cash
    gross = sum(v for s, v in value.items() if s in SLEEVE_BY_TSX)
    unmanaged = sorted(s for s in positions if s not in SLEEVE_BY_TSX)
    rows, drift_max = [], 0.0
    for s in SLEEVES:
        tgt_sleeve = nlv * w.get(s.key, 0.0) * lev
        held = {t: value.get(t, 0.0) for t in s.tsx if value.get(t, 0.0) > 0}
        now_sleeve = sum(held.values())
        drift_max = max(drift_max, abs(now_sleeve - tgt_sleeve) / nlv)
        split = {t: v / now_sleeve for t, v in held.items()} if now_sleeve > 0 else {s.tsx[0]: 1.0}
        for t, share in split.items():
            price = positions.get(t, {}).get("price") or (float(ticker_closes[t].dropna().iloc[-1])
                                                          if t in ticker_closes else None)
            tgt = tgt_sleeve * share
            now = value.get(t, 0.0)
            delta = tgt - now
            units = int(delta / price) if price else 0
            action = "hold" if abs(delta) < MIN_TRADE or units == 0 else ("buy" if units > 0 else "sell")
            rows.append({"sleeve": s.key, "group": s.group, "ticker": t, "price": price, "now": round(now, 2),
                         "target": round(tgt, 2), "units": units if action != "hold" else 0, "action": action,
                         "score": scores.get(s.key)})
    cur_lev = (gross + sum(value[s] for s in unmanaged)) / nlv if nlv > 0 else float("nan")
    need = drift_max > DRIFT_BAND or abs(gross / nlv - lev) > LEV_BAND if nlv > 0 else False
    # Stress tests on the proposed book (unmanaged positions included as equity-like at -20%).
    after = {r["ticker"]: r["now"] + (r["units"] * (r["price"] or 0)) for r in rows}
    loan_after = cash - sum(r["units"] * (r["price"] or 0) for r in rows)
    shock_a = {"core": -0.20, "satellite": -0.30}
    grp = {t: next(s.group for s in SLEEVES if t in s.tsx) for t in after}
    hit_after = sum(v * shock_a[grp[t]] for t, v in after.items()) + sum(value[s] * -0.20 for s in unmanaged)
    hit_now = sum(value[t] * shock_a[grp.get(t, "core")] for t in value)
    stress = {"equities -20%, satellites -30%: now": hit_now / nlv, "same shock: after proposal": hit_after / nlv,
              "loan after proposal": loan_after}
    cushion_now = nlv / (gross + sum(value[s] for s in unmanaged)) if gross else 1.0
    alerts = []
    if cushion_now < CUSHION_ALERT:
        alerts.append(f"margin cushion {cushion_now:.0%} is below {CUSHION_ALERT:.0%}")
    if -stress["equities -20%, satellites -30%: now"] > STRESS_ALERT:
        alerts.append(f"a -20%/-30% shock would cost {-stress['equities -20%, satellites -30%: now']:.0%} of net value "
                      f"(alert above {STRESS_ALERT:.0%})")
    top = max(value.items(), key=lambda kv: kv[1]) if value else None
    if top and nlv > 0 and top[1] / nlv > 0.25:
        alerts.append(f"{top[0]} is {top[1] / nlv:.0%} of net value (one position above 25%)")
    return Proposal(asof or str(prices.index[-1].date()), nlv, cash, w, scores, lev, vol, cur_lev, rows, unmanaged,
                    stress, need, pol, alerts)


def render(p: Proposal) -> str:
    lines = [f"# Portfolio Pilot proposal ({p.asof})", "",
             "Advisory only (D1, D25): nothing is sent to any broker; you place trades by hand. Rules: docs/pilot/PREREG.md.",
             "", f"Net value C${p.nlv:,.0f}, loan C${-min(p.cash, 0):,.0f}, leverage now {p.current_leverage:.2f}x.",
             (f"Forecast volatility of the target mix {p.forecast_vol:.1%} a year, so target leverage "
              f"**{p.leverage:.2f}x** (15% target, capped at 0.5x to 1.5x)." if p.policy.get("mode") == "pilot" else
              f"Policy: {p.policy.get('mode')} strategic mix at **{p.leverage:.2f}x** leverage (your choice; hard cap "
              f"{LEV_MAX}x). Forecast volatility of the mix unlevered {p.forecast_vol:.1%}, levered "
              f"{p.forecast_vol * p.leverage:.1%} a year."),
             f"Rebalance needed: **{'yes' if p.rebalance_needed else 'no'}** (a sleeve off by more than 3 points of net "
             "value, or leverage off by more than 0.10).", "",
             "| Sleeve | Trend score | Ticker | Now | Target | Trade |", "|---|---:|---|---:|---:|---|"]
    for r in p.rows:
        sc = "" if r["score"] is None else f"{r['score']:.2f}"
        tr = "-" if r["action"] == "hold" else f"{r['action']} {abs(r['units'])} @ ~{r['price']:.2f}"
        lines.append(f"| {r['sleeve']} | {sc} | {r['ticker']} | {r['now']:,.0f} | {r['target']:,.0f} | {tr} |")
    if p.alerts:
        lines += ["", "**Risk alerts:** " + "; ".join(p.alerts) + "."]
    if p.unmanaged:
        lines += ["", "Not managed by the Pilot (left as is): " + ", ".join(p.unmanaged)]
    lines += ["", "Stress test (one-day shock, share of net value):"]
    lines += [f"- {k}: {v:+.1%}" if "loan" not in k else f"- {k}: C${-min(v, 0):,.0f}" for k, v in p.stress.items()]
    lines += ["", "Sell orders first, then buys; limit orders near the last price."
              + (" Satellites with a trend score below 1 are partly or fully parked in the core until their trend "
                 "recovers." if p.policy.get("mode") in ("trend", "pilot") else ""), ""]
    return "\n".join(lines)
