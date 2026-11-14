"""FROZEN SPEC v1 (2026-11-14) -- P1 catalyst-gap bull call vertical. PAPER RESEARCH ONLY, NO ORDERS.

Offline backtest. Option prices are a BLACK-SCHOLES APPROXIMATION (IV = clip(mult x RV20, .15, 2.5)),
not real quotes. Nothing here talks to a broker.
"""
from __future__ import annotations

import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import ndtr

sys.path.insert(0, "/home/user/ML-Trading-Platform-Backend")
import external_bars  # noqa: E402

OUT = Path(__file__).resolve().parent
T0 = time.time()

# ----------------------------------------------------------------------------- constants (frozen)
ETFS = {"SPY", "QQQ", "IWM", "DIA", "TQQQ", "SGOV", "EFA", "IEF", "GLD", "DBC", "SMH", "QUAL", "VTV", "RSP"}
EXCL = {"SEQ", "SEQUX", "CBRS", "GOOG", "FFH", "PSHD"}
SURVIVORS = {"PLTR", "APP", "HOOD", "MSTR", "RKLB", "ASTS", "OKLO", "SMR", "IONQ", "CRWV", "COIN", "CVNA",
             "ARM", "SOFI", "HIMS", "AFRM", "UPST"}
R = 0.04
IS_START, IS_END = pd.Timestamp("2021-11-05"), pd.Timestamp("2025-10-31")
HO_START, HO_END = pd.Timestamp("2025-11-03"), pd.Timestamp("2026-11-13")
START_CASH = 1000.0
COMM = 0.65  # per contract per leg per side
SEED = 20261114
import os
N_CTRL = int(os.environ.get("N_CTRL", 1000))
N_BOOT = int(os.environ.get("N_BOOT", 10000))

# ----------------------------------------------------------------------------- data
syms_all = sorted({p.name.split(".")[0] for p in Path("/home/user/ML-Trading-Platform-Backend/state/external_bars").glob("*.json")})
UNIV = [s for s in syms_all if s not in ETFS and s not in EXCL]
spy = external_bars.load_bars("SPY", "etf")
spy = spy[(spy.index >= IS_START) & (spy.index <= HO_END)]
CAL = spy.index
ND = len(CAL)
spy_close = spy["Close"].values
spy_sma200 = pd.Series(spy_close).rolling(200, min_periods=200).mean().values
BULL = spy_close > spy_sma200  # NaN -> False
BEAR = spy_close < spy_sma200

# extended calendar for counting trading days to expiry beyond the data end (business days)
EXT = CAL.append(pd.bdate_range(CAL[-1] + pd.Timedelta(days=1), "2028-12-31"))
EXT_NP = EXT.values.astype("datetime64[D]")
CAL_NP = CAL.values.astype("datetime64[D]")

D = {}
for s in UNIV:
    df = external_bars.load_bars(s, "equity")
    df = df[~df.index.duplicated(keep="last")]
    n_off = int((~df.index.isin(CAL) & (df.index >= IS_START) & (df.index <= HO_END)).sum())
    df = df.reindex(CAL)  # drops off-calendar bars, NaN where missing
    o, h, l, c, v = (df[k].values.astype(float) for k in ("Open", "High", "Low", "Close", "Volume"))
    valid = ~np.isnan(c)
    nvalid = np.cumsum(valid)
    # RV20 on the valid-bar sequence
    cs = pd.Series(c[valid])
    lr = np.log(cs).diff()
    rv = lr.rolling(20, min_periods=20).std(ddof=1).values * math.sqrt(252)
    rv_full = np.full(ND, np.nan)
    rv_full[valid] = rv
    rv_ff = pd.Series(rv_full).ffill().values
    c_ff = pd.Series(c).ffill().values
    vol50 = pd.Series(v).shift(1).rolling(50, min_periods=45).mean().values
    c_prev = np.concatenate([[np.nan], c[:-1]])
    gap = o / c_prev - 1
    rng = h - l
    with np.errstate(invalid="ignore", divide="ignore"):
        clv = np.where(rng > 0, (c - l) / np.where(rng > 0, rng, 1), -1.0)
        vr = v / vol50
    D[s] = dict(o=o, h=h, l=l, c=c, v=v, valid=valid, nvalid=nvalid, rv=rv_ff, c_ff=c_ff, vol50=vol50,
                gap=gap, clv=clv, vr=vr, n_off=n_off)

# ----------------------------------------------------------------------------- signals
def signal_mask(s, kind):
    d = D[s]
    with np.errstate(invalid="ignore"):
        base = d["valid"] & (d["nvalid"] >= 250) & ~np.isnan(d["vol50"]) & ~np.isnan(d["gap"])
        r1 = d["gap"] >= 0.05
        r2 = d["vr"] >= 3.0
        r3 = d["clv"] >= 0.5
        if kind == "P1":
            return base & r1 & r2 & r3 & BULL
        if kind == "P1_123":
            return base & r1 & r2 & r3
        if kind == "B":  # non-catalyst gaps
            return base & r1 & r3 & BULL & (d["vr"] < 1.5)
        if kind == "C":  # bear regime
            return base & r1 & r2 & r3 & BEAR
    raise ValueError(kind)


def build_signals(kind, universe):
    out = []
    for s in universe:
        m = signal_mask(s, kind)
        for t in np.nonzero(m)[0]:
            out.append((int(t), s, float(D[s]["vr"][t])))
    return out


SIG = {k: build_signals(k, UNIV) for k in ("P1", "B", "C")}
P1_DAYS = {s: set(np.nonzero(signal_mask(s, "P1"))[0].tolist()) for s in UNIV}

# ----------------------------------------------------------------------------- expiries / strikes
def third_fridays():
    out = []
    for y in range(2021, 2029):
        for m in range(1, 13):
            d1 = pd.Timestamp(y, m, 1)
            fri = d1 + pd.Timedelta(days=(4 - d1.weekday()) % 7 + 14)
            if CAL[0] <= fri <= CAL[-1] and fri not in CAL:
                prior = CAL[CAL < fri]
                fri = prior[-1]
            out.append(fri)
    return pd.DatetimeIndex(out)


MONTHLIES = third_fridays()


def pick_expiry(t):
    dt = CAL[t]
    for e in MONTHLIES:
        if (e - dt).days >= 28:
            return e
    raise RuntimeError


def inc_for(c):
    return 0.5 if c < 25 else 1.0 if c < 100 else 2.5 if c < 250 else 5.0


def nearest(x, inc):
    return round(x / inc) * inc


def tdays_remaining(u, exp):
    """SPY trading days in (CAL[u], exp]; business days beyond the data end."""
    a = np.searchsorted(EXT_NP, CAL_NP[u], side="right")
    b = np.searchsorted(EXT_NP, np.datetime64(exp.date(), "D"), side="right")
    return b - a


# ----------------------------------------------------------------------------- Black-Scholes (approximation)
def bs_call(S, K, T, sig):
    S = np.asarray(S, float); K = np.asarray(K, float); T = np.asarray(T, float); sig = np.asarray(sig, float)
    Tp = np.maximum(T, 1e-9)
    sq = sig * np.sqrt(Tp)
    d1 = (np.log(S / K) + (R + 0.5 * sig * sig) * Tp) / sq
    d2 = d1 - sq
    px = S * ndtr(d1) - K * np.exp(-R * Tp) * ndtr(d2)
    return np.where(T <= 0, np.maximum(S - K, 0.0), px)


def half_spread(mid, spread_mult):
    return np.maximum(0.05, 0.03 * mid) * spread_mult


CFG = {
    "primary": dict(m_entry=1.25, m_after=1.25, spread=1.0, delay=0),
    "A_iv1.5_entry": dict(m_entry=1.5, m_after=1.25, spread=1.0, delay=0),
    "B_iv1.5_all": dict(m_entry=1.5, m_after=1.5, spread=1.0, delay=0),
    "C_iv1.0_all": dict(m_entry=1.0, m_after=1.0, spread=1.0, delay=0),
    "D_spread_x2": dict(m_entry=1.25, m_after=1.25, spread=2.0, delay=0),
    "E_delay1": dict(m_entry=1.25, m_after=1.25, spread=1.0, delay=1),
    # extra (not in the spec): the generic harness text's 5% half-spread instead of 3%
    "F_halfspread5pct": dict(m_entry=1.25, m_after=1.25, spread=5 / 3, delay=0),
    # DIAGNOSTIC ONLY (not frozen, not in the decision): the vertical quoted as one combo, half-spread
    # max($0.05, 3% of the SPREAD mid) per side instead of per leg. Shows how much of the result is the
    # per-leg cost model, which makes deep-ITM verticals unsellable (two large leg spreads > spread value).
    "G_diag_combo_quote": dict(m_entry=1.25, m_after=1.25, spread=1.0, delay=0, combo=True),
}


def leg_quotes(s, u, K, exp, mult, spread_mult):
    d = D[s]
    S = d["c_ff"][u]
    iv = min(max(mult * d["rv"][u], 0.15), 2.50)
    T = (exp - CAL[u]).days / 365.0
    mid = bs_call(S, K, T, iv)
    h = half_spread(mid, spread_mult)
    return mid, h


def entry_debit(s, f, K1, K2, exp, cfg):
    """per-share debit to open at close f: ask(K1) - bid(K2)."""
    mid, h = leg_quotes(s, f, np.array([K1, K2]), exp, cfg["m_entry"], cfg["spread"])
    if cfg.get("combo"):
        sm = mid[0] - mid[1]
        return float(sm + half_spread(sm, cfg["spread"]))
    return float((mid[0] + h[0]) - max(mid[1] - h[1], 0.0))


# path cache: (sym, t, K1, K2, cfgname) -> dict
PATHS = {}


def trade_path(s, t, K1, K2, exp, cfgname):
    key = (s, t, K1, K2, cfgname)
    if key in PATHS:
        return PATHS[key]
    cfg = CFG[cfgname]
    dly = cfg["delay"]
    f = t + dly
    d = D[s]
    debit = entry_debit(s, f, K1, K2, exp, cfg)
    width = K2 - K1
    low_t = d["l"][t]
    # mark every close after f until exit
    us = np.arange(f + 1, ND)
    marks_all = None
    exit_dec = None
    reason = None
    if len(us):
        S = d["c_ff"][us]
        iv = np.clip(cfg["m_after"] * d["rv"][us], 0.15, 2.50)
        T = np.array([(exp - CAL[u]).days / 365.0 for u in us])
        m1 = bs_call(S, K1, T, iv); m2 = bs_call(S, K2, T, iv)
        h1 = half_spread(m1, cfg["spread"]); h2 = half_spread(m2, cfg["spread"])
        marks_all = np.maximum(np.maximum(m1 - h1, 0.0) - (m2 + h2), 0.0)  # exit side, floored at 0 (abandon)
        if cfg.get("combo"):
            sm = m1 - m2
            marks_all = np.maximum(sm - half_spread(sm, cfg["spread"]), 0.0)
        cl = d["c"][us]
        for i, u in enumerate(us):
            mk = marks_all[i]
            if mk >= 2.5 * debit or mk >= 0.80 * width:
                reason = "take_profit"
            elif (not np.isnan(cl[i])) and cl[i] < low_t:
                reason = "thesis_stop"
            elif u >= t + 15:
                reason = "time_stop"
            elif tdays_remaining(u, exp) <= 5:
                reason = "expiry_guard"
            if reason:
                exit_dec = u
                break
    if exit_dec is None:
        x = ND - 1
        reason = "open_at_end"
    else:
        x = exit_dec + dly
        if x > ND - 1:
            x = ND - 1
            reason = reason + "_open_at_end"
    if marks_all is None or x == f:
        marks = np.array([max(debit, 0)])  # degenerate
        exit_val = marks[-1]
    else:
        marks = marks_all[: x - f]  # marks for days f+1..x
        exit_val = float(marks[-1])
    p = dict(f=f, x=x, debit=debit, width=width, marks=marks, exit_val=exit_val, reason=reason)
    PATHS[key] = p
    return p


# ----------------------------------------------------------------------------- engine
def mark_entry_val(s, f, K1, K2, exp, cfg):
    """exit-side value at the fill close (used for equity mark on the fill day)."""
    mid, h = leg_quotes(s, f, np.array([K1, K2]), exp, cfg["m_entry"], cfg["spread"])
    if cfg.get("combo"):
        sm = mid[0] - mid[1]
        return max(sm - half_spread(sm, cfg["spread"]), 0.0)
    return max(max(mid[0] - h[0], 0.0) - (mid[1] + h[1]), 0.0)


def run_engine(signals, cfgname="primary", start_idx=0, entry_lo=IS_START, entry_hi=HO_END, universe=None,
               lows=None, curve=True):
    """signals: list of (t, sym, rank_key). Returns dict with bets, skips, equity curve."""
    cfg = CFG[cfgname]
    dly = cfg["delay"]
    lo_i = np.searchsorted(CAL_NP, np.datetime64(entry_lo.date(), "D"))
    hi_i = np.searchsorted(CAL_NP, np.datetime64(entry_hi.date(), "D"), side="right") - 1
    by_day = defaultdict(list)
    n_raw = 0
    for (t, s, rk) in signals:
        if universe is not None and s not in universe:
            continue
        if t < lo_i or t > hi_i or t < start_idx:
            continue
        n_raw += 1
        by_day[t + dly].append((t, s, rk))
    cash = START_CASH
    hwm = START_CASH
    open_pos = []  # dicts
    bets = []
    skips = Counter()
    eq = np.full(ND, np.nan)
    last_needed = max(by_day) if by_day else start_idx
    u = start_idx
    while u < ND:
        # 1) exits at this close
        if open_pos:
            keep = []
            for p in open_pos:
                if p["path"]["x"] == u and not p["path"]["reason"].endswith("open_at_end"):
                    val = p["path"]["exit_val"]
                    proceeds = p["n"] * max(100 * val - 2 * COMM, 0.0)  # abandon if not worth the commission
                    cash += proceeds
                    p["pnl"] = proceeds - p["cost_total"]
                    p["exit_day"] = u
                    bets.append(p)
                else:
                    keep.append(p)
            open_pos = keep
        # 2) mark
        mtm = 0.0
        for p in open_pos:
            k = u - p["path"]["f"]
            mtm += p["n"] * 100 * (p["fill_mark"] if k == 0 else p["path"]["marks"][k - 1])
        equity = cash + mtm
        # 3) entries
        if u in by_day:
            cands = sorted(by_day[u], key=lambda z: -z[2])
            for (t, s, rk) in cands:
                if u >= ND:
                    skips["no_next_session"] += 1
                    continue
                if any(p["sym"] == s for p in open_pos):
                    skips["already_open(rule5)"] += 1
                    continue
                if equity < 600:
                    skips["halt"] += 1
                    continue
                if len(open_pos) >= 4:
                    skips["slots"] += 1
                    continue
                d = D[s]
                C = d["c"][t]
                inc = inc_for(C)
                K1 = nearest(C, inc)
                K2 = nearest(1.15 * C, inc)
                if K2 <= K1:
                    K2 = K1 + inc
                exp = pick_expiry(t)
                cap8 = 0.08 * equity
                chosen = None
                while True:
                    deb = entry_debit(s, u, K1, K2, exp, cfg)
                    cost = 100 * deb + 2 * COMM
                    if cost <= cap8 and deb > 0:
                        chosen = (K2, deb, cost)
                        break
                    if K2 - inc <= K1 + 1e-9:
                        break
                    K2 = round((K2 - inc) / inc) * inc
                if chosen is None:
                    skips["over_budget"] += 1
                    continue
                K2, deb, cost = chosen
                risk = 0.04 * (0.5 if equity < 0.85 * hwm else 1.0)
                n = int(math.floor(risk * equity / cost))
                if n == 0 and cost <= cap8:
                    n = 1
                open_cost = sum(p["cost_total"] for p in open_pos)
                room = 0.25 * equity - open_cost
                n_fit = int(math.floor(room / cost)) if room > 0 else 0
                if n_fit < 1:
                    skips["concurrency_cap"] += 1
                    continue
                if n_fit < n:
                    skips["lots_reduced_for_cap(info)"] += 1
                    n = n_fit
                path = trade_path(s, t, K1, K2, exp, cfgname)
                fill_mark = mark_entry_val(s, u, K1, K2, exp, cfg)
                cost_total = n * cost
                cash -= cost_total
                p = dict(sym=s, t=t, f=u, K1=K1, K2=K2, exp=exp, n=n, cost_lot=cost, cost_total=cost_total,
                         debit=deb, close_t=C, path=path, fill_mark=fill_mark, vr=rk, eq_at_entry=equity)
                open_pos.append(p)
                equity = cash + mtm + n * 100 * fill_mark  # re-mark
                mtm += n * 100 * fill_mark
        eq[u] = equity
        hwm = max(hwm, equity)
        if not curve and not open_pos and u >= last_needed:
            break
        u += 1
    skips["no_next_session"] += len(by_day.get(ND, []))
    # positions still open at the data end: mark at last close
    for p in open_pos:
        k = ND - 1 - p["path"]["f"]
        val = p["fill_mark"] if k == 0 else p["path"]["marks"][min(k, len(p["path"]["marks"])) - 1]
        p["pnl"] = p["n"] * max(100 * val - 2 * COMM, 0.0) - p["cost_total"]
        p["exit_day"] = ND - 1
        p["open_at_end"] = True
        bets.append(p)
    if not curve:
        final = cash + sum(p["n"] * 100 * (p["fill_mark"] if ND - 1 == p["path"]["f"] else
                                            p["path"]["marks"][min(ND - 1 - p["path"]["f"], len(p["path"]["marks"])) - 1])
                           for p in open_pos)
    else:
        final = float(pd.Series(eq).ffill().values[-1])
    for b in bets:
        b["ret"] = b["pnl"] / b["cost_total"]
    return dict(bets=bets, skips=skips, eq=eq, final=final, n_raw=n_raw, start_idx=start_idx)


# ----------------------------------------------------------------------------- metrics
def bet_stats(bets):
    if not bets:
        return dict(n=0)
    r = np.array([b["ret"] for b in bets]); pnl = np.array([b["pnl"] for b in bets])
    wins = pnl[pnl > 0]; losses = pnl[pnl <= 0]
    tot = pnl.sum()
    top5 = np.sort(pnl)[::-1][:5].sum()
    out = dict(n=len(bets), hit=float((pnl > 0).mean()), mean_ret=float(r.mean()), median_ret=float(np.median(r)),
               p_ge_100=float((r >= 1.0).mean()), p_le_m80=float((r <= -0.8).mean()),
               avg_win_usd=float(wins.mean()) if len(wins) else 0.0, avg_loss_usd=float(losses.mean()) if len(losses) else 0.0,
               avg_win_ret=float(r[pnl > 0].mean()) if len(wins) else 0.0, avg_loss_ret=float(r[pnl <= 0].mean()) if len(losses) else 0.0,
               total_pnl=float(tot), total_ex_top5=float(tot - top5),
               top5_share=float(top5 / tot) if tot > 0 else None,
               reasons=dict(Counter(b["path"]["reason"] for b in bets)),
               open_at_end=sum(1 for b in bets if b.get("open_at_end")))
    return out


def curve_stats(eq, start_idx, end_date=None):
    s = pd.Series(eq, index=CAL).iloc[start_idx:].ffill()
    if end_date is not None:
        s = s[s.index <= end_date]
    s = s.dropna()
    dd = (s / s.cummax() - 1).min()
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    cagr = (s.iloc[-1] / START_CASH) ** (1 / yrs) - 1 if yrs > 0 and s.iloc[-1] > 0 else -1.0
    return dict(terminal=float(s.iloc[-1]), max_dd=float(dd), cagr=float(cagr), start=str(s.index[0].date()),
                end=str(s.index[-1].date()), min_equity=float(s.min()))


def idx_of(ts, side="left"):
    return int(np.searchsorted(CAL_NP, np.datetime64(ts.date(), "D"), side=side))


HO_START_IDX = idx_of(HO_START) - 1  # restart $1,000 at the 2025-10-31 close


def run_period(signals, cfgname, period, universe=None, curve=True):
    if period == "IS":
        r = run_engine(signals, cfgname, 0, IS_START, IS_END, universe, curve=curve)
    elif period == "HO":
        r = run_engine(signals, cfgname, HO_START_IDX, HO_START, HO_END, universe, curve=curve)
    else:
        r = run_engine(signals, cfgname, 0, IS_START, HO_END, universe, curve=curve)
    return r


def summarize(r, period):
    out = dict(raw_signals=r["n_raw"], bets=len(r["bets"]), skips=dict(r["skips"]))
    out.update(bet_stats(r["bets"]))
    end = None
    if period == "IS":
        # curve until the last IS position is closed
        last_exit = max([b["exit_day"] for b in r["bets"]], default=idx_of(IS_END, "right") - 1)
        end = CAL[max(last_exit, idx_of(IS_END, "right") - 1)]
    out.update(curve_stats(r["eq"], r["start_idx"], end))
    return out


# ----------------------------------------------------------------------------- main runs
results = {"meta": {}}
results["meta"]["universe_n"] = len(UNIV)
results["meta"]["universe"] = UNIV
results["meta"]["off_calendar_bars_dropped"] = {s: D[s]["n_off"] for s in UNIV if D[s]["n_off"]}
p1 = SIG["P1"]
results["meta"]["raw_P1_events"] = dict(
    total=len(p1), IS=sum(1 for t, s, _ in p1 if CAL[t] <= IS_END), HO=sum(1 for t, s, _ in p1 if CAL[t] >= HO_START))
results["meta"]["first_bull_day"] = str(CAL[np.argmax(BULL)].date())

RUNS = {}
for cfgname in CFG:
    for per in ("IS", "HO", "FULL"):
        RUNS[(cfgname, per)] = run_period(p1, cfgname, per)
results["strategy"] = {f"{c}|{p}": summarize(RUNS[(c, p)], p) for (c, p) in RUNS}
print(f"[{time.time()-T0:.0f}s] strategy runs done", flush=True)

# by calendar year (full primary run, by entry year)
full_bets = RUNS[("primary", "FULL")]["bets"]
by_year = {}
for y in sorted({CAL[b["t"]].year for b in full_bets}):
    bb = [b for b in full_bets if CAL[b["t"]].year == y]
    st = bet_stats(bb)
    by_year[y] = {k: st[k] for k in ("n", "hit", "mean_ret", "median_ret", "total_pnl", "p_ge_100")}
results["by_year_full_primary"] = by_year

# price distribution
def qtiles(x):
    x = np.asarray(x, float)
    return {q: float(np.percentile(x, q)) for q in (10, 25, 50, 75, 90)} if len(x) else {}

results["price_dist"] = dict(
    traded_full_primary=qtiles([b["close_t"] for b in full_bets]),
    raw_events=qtiles([D[s]["c"][t] for t, s, _ in p1]),
    traded_share_under_50=float(np.mean([b["close_t"] < 50 for b in full_bets])) if full_bets else None,
    raw_share_under_50=float(np.mean([D[s]["c"][t] < 50 for t, s, _ in p1])))

# survivor-excluded
UNIV_NS = set(UNIV) - SURVIVORS
results["survivor_excluded"] = {per: summarize(run_period(p1, "primary", per, UNIV_NS), per) for per in ("IS", "HO", "FULL")}

# ----------------------------------------------------------------------------- controls B, C
for k in ("B", "C"):
    results[f"control_{k}"] = {per: summarize(run_period(SIG[k], "primary", per), per) for per in ("IS", "HO", "FULL")}
print(f"[{time.time()-T0:.0f}s] controls B/C done", flush=True)

# ----------------------------------------------------------------------------- control A: matched random days
rng = np.random.default_rng(SEED)
# candidate pool per (symbol, year): bull, >=250 valid bars, valid bar, vol50 available, no P1 signal within +/-5
pool = {}
for s in UNIV:
    d = D[s]
    ok = d["valid"] & (d["nvalid"] >= 250) & BULL & ~np.isnan(d["vol50"]) & ~np.isnan(d["rv"])
    near = np.zeros(ND, bool)
    for t in P1_DAYS[s]:
        near[max(0, t - 5): t + 6] = True
    ok &= ~near
    idx = np.nonzero(ok)[0]
    for y in np.unique(CAL[idx].year):
        pool[(s, int(y))] = idx[CAL[idx].year == y]
events_by_sy = Counter((s, CAL[t].year) for t, s, _ in p1)
missing_pool = {f"{k[0]}-{k[1]}": v for k, v in events_by_sy.items() if len(pool.get(k, [])) < v}
results["meta"]["controlA_symyears_short_pool"] = missing_pool


def draw_set():
    sig = []
    for (s, y), n in events_by_sy.items():
        cand = pool.get((s, y), np.array([], int))
        if len(cand) == 0:
            continue
        pick = rng.choice(cand, size=min(n, len(cand)), replace=False)
        for t in pick:
            sig.append((int(t), s, float(D[s]["vr"][t])))
    return sig


ctrlA = {per: dict(mean=[], term=[], n=[]) for per in ("IS", "HO", "FULL", "IS_ns")}
for i in range(N_CTRL):
    sig = draw_set()
    for per in ("IS", "HO", "FULL"):
        r = run_period(sig, "primary", per, curve=False)
        rs = [b["ret"] for b in r["bets"]]
        ctrlA[per]["mean"].append(np.mean(rs) if rs else np.nan)
        ctrlA[per]["term"].append(r["final"])
        ctrlA[per]["n"].append(len(rs))
    r = run_period(sig, "primary", "IS", UNIV_NS, curve=False)
    rs = [b["ret"] for b in r["bets"]]
    ctrlA["IS_ns"]["mean"].append(np.mean(rs) if rs else np.nan)
    ctrlA["IS_ns"]["term"].append(r["final"])
    ctrlA["IS_ns"]["n"].append(len(rs))
    if (i + 1) % 100 == 0:
        print(f"[{time.time()-T0:.0f}s] control A draw {i+1}", flush=True)


def pctile(x, v):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    return float(((x < v).sum() + 0.5 * (x == v).sum()) / len(x) * 100)


CA = {}
for per in ("IS", "HO", "FULL", "IS_ns"):
    strat = results["survivor_excluded"]["IS"] if per == "IS_ns" else results["strategy"][f"primary|{per}"]
    m = np.asarray(ctrlA[per]["mean"], float); tm = np.asarray(ctrlA[per]["term"], float)
    CA[per] = dict(ctrl_mean_of_means=float(np.nanmean(m)), ctrl_p95_mean=float(np.nanpercentile(m, 95)),
                   ctrl_p50_mean=float(np.nanpercentile(m, 50)), ctrl_median_terminal=float(np.median(tm)),
                   ctrl_p95_terminal=float(np.percentile(tm, 95)), ctrl_mean_n_bets=float(np.mean(ctrlA[per]["n"])),
                   strat_mean=strat.get("mean_ret"), strat_terminal=strat["terminal"],
                   pct_mean=pctile(m, strat.get("mean_ret", np.nan)), pct_terminal=pctile(tm, strat["terminal"]))
results["control_A"] = CA
print(f"[{time.time()-T0:.0f}s] control A done", flush=True)

# ----------------------------------------------------------------------------- bootstrap ruin
eqf = pd.Series(RUNS[("primary", "FULL")]["eq"]).ffill().values
dr = eqf[1:] / eqf[:-1] - 1
L = len(dr)
nb = int(math.ceil(L / 21))
starts_max = L - 21
brng = np.random.default_rng(SEED)
touch500 = 0; touch600 = 0; terms = []
for i in range(N_BOOT):
    st = brng.integers(0, starts_max + 1, nb)
    path = np.concatenate([dr[s0:s0 + 21] for s0 in st])[:L]
    e = START_CASH * np.cumprod(1 + path)
    touch500 += e.min() <= 500
    touch600 += e.min() <= 600
    terms.append(e[-1])
first_entry = min(b["f"] for b in full_bets)
dr2 = dr[first_entry:]
L2 = len(dr2); nb2 = int(math.ceil(L2 / 21)); t5b = 0; t6b = 0
for i in range(N_BOOT):
    st = brng.integers(0, L2 - 21 + 1, nb2)
    path = np.concatenate([dr2[s0:s0 + 21] for s0 in st])[:L]  # resample active-period returns to full length
    e = START_CASH * np.cumprod(1 + path)
    t5b += e.min() <= 500; t6b += e.min() <= 600
results["ruin"] = dict(p_touch_500=touch500 / N_BOOT, p_touch_600=touch600 / N_BOOT,
                       boot_terminal_p5=float(np.percentile(terms, 5)), boot_terminal_p50=float(np.percentile(terms, 50)),
                       boot_terminal_p95=float(np.percentile(terms, 95)),
                       active_only_p_touch_500=t5b / N_BOOT, active_only_p_touch_600=t6b / N_BOOT)

# ----------------------------------------------------------------------------- SPY buy and hold
def spy_bh(a, b):
    s = pd.Series(spy_close, index=CAL)
    s = s[(s.index >= a) & (s.index <= b)]
    e = START_CASH * s / s.iloc[0]
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    return dict(ret=float(e.iloc[-1] / START_CASH - 1), terminal=float(e.iloc[-1]),
                max_dd=float((e / e.cummax() - 1).min()), cagr=float((e.iloc[-1] / START_CASH) ** (1 / yrs) - 1))

results["spy_bh"] = dict(IS=spy_bh(IS_START, IS_END), HO=spy_bh(IS_END, HO_END), FULL=spy_bh(IS_START, HO_END))

# ----------------------------------------------------------------------------- diagnostics (NOT part of the frozen decision)
# Per-event trade returns with no account constraints (budget, slots, halt), primary pricing, for every raw event,
# vs. every eligible control-pool day. Two instruments: the intended 15%-wide spread and the 1-increment spread.
def unconstrained(t, s, cfgname="primary"):
    d = D[s]; C = d["c"][t]; inc = inc_for(C); K1 = nearest(C, inc); K2 = nearest(1.15 * C, inc)
    if K2 <= K1:
        K2 = K1 + inc
    exp = pick_expiry(t)
    out = {}
    for lab, k2 in (("wide15", K2), ("narrow1", K1 + inc)):
        pth = trade_path(s, t, K1, k2, exp, cfgname)
        cost = 100 * pth["debit"] + 2 * COMM
        out[lab] = (max(100 * pth["exit_val"] - 2 * COMM, 0) - cost) / cost
    pth = trade_path(s, t, K1, K1 + inc, exp, cfgname)
    out["stock_ret_to_exit"] = d["c_ff"][pth["x"]] / C - 1
    out["stock_ret_15d"] = d["c_ff"][min(t + 15, ND - 1)] / C - 1
    return out


diag = {}
ev = [unconstrained(t, s) for t, s, _ in p1 if t + 16 < ND]
pool_days = [(int(t), s) for (s, y), arr in pool.items() for t in arr if t + 16 < ND]
pr = np.random.default_rng(SEED).choice(len(pool_days), size=min(3000, len(pool_days)), replace=False)
cv = [unconstrained(*pool_days[i]) for i in pr]
for lab in ("wide15", "narrow1", "stock_ret_to_exit", "stock_ret_15d"):
    a = np.array([e[lab] for e in ev]); b = np.array([e[lab] for e in cv])
    diag[lab] = dict(events_n=len(a), events_mean=float(a.mean()), events_median=float(np.median(a)),
                     events_hit=float((a > 0).mean()), events_p_ge_100=float((a >= 1).mean()),
                     pool_n=len(b), pool_mean=float(b.mean()), pool_median=float(np.median(b)), pool_hit=float((b > 0).mean()))
results["diagnostic_unconstrained"] = diag
evg = [unconstrained(t, s, "G_diag_combo_quote") for t, s, _ in p1 if t + 16 < ND]
cvg = [unconstrained(*pool_days[i], "G_diag_combo_quote") for i in pr]
diagg = {}
for lab in ("wide15", "narrow1"):
    a = np.array([e[lab] for e in evg]); b = np.array([e[lab] for e in cvg])
    diagg[lab] = dict(events_n=len(a), events_mean=float(a.mean()), events_median=float(np.median(a)),
                      events_hit=float((a > 0).mean()), events_p_ge_100=float((a >= 1).mean()),
                      pool_n=len(b), pool_mean=float(b.mean()), pool_median=float(np.median(b)), pool_hit=float((b > 0).mean()))
results["diagnostic_unconstrained_combo_quote"] = diagg

# ----------------------------------------------------------------------------- decision (frozen)
S = results["strategy"]
crit = {
    "IS_mean_ge_ctrlA_p95": S["primary|IS"]["mean_ret"] >= CA["IS"]["ctrl_p95_mean"],
    "IS_mean_pos_stressA": S["A_iv1.5_entry|IS"]["mean_ret"] > 0,
    "IS_mean_pos_stressE": S["E_delay1|IS"]["mean_ret"] > 0,
    "HO_n_ge_20": S["primary|HO"]["n"] >= 20,
    "HO_mean_gt_ctrlA_mean": S["primary|HO"].get("mean_ret", -9) > CA["HO"]["ctrl_mean_of_means"],
    "survivor_excl_IS_mean_gt_ctrl": results["survivor_excluded"]["IS"]["mean_ret"] > CA["IS_ns"]["ctrl_mean_of_means"],
    "ruin_lt_5pct": results["ruin"]["p_touch_500"] < 0.05,
}
others = all(v for k, v in crit.items() if not k.startswith("HO_"))
if not others:
    decision = "REJECT"
elif not crit["HO_n_ge_20"]:
    decision = "HO inconclusive -> paper only"
elif crit["HO_mean_gt_ctrlA_mean"]:
    decision = "GO to live paper"
else:
    decision = "REJECT"
results["decision"] = dict(criteria=crit, decision=decision)

# trade list
trades = pd.DataFrame([dict(sym=b["sym"], signal=str(CAL[b["t"]].date()), fill=str(CAL[b["f"]].date()),
                            exit=str(CAL[b["exit_day"]].date()), reason=b["path"]["reason"], close_t=round(b["close_t"], 2),
                            K1=b["K1"], K2=b["K2"], expiry=str(b["exp"].date()), lots=b["n"], debit_ps=round(b["debit"], 3),
                            cost=round(b["cost_total"], 2), exit_val_ps=round(b["path"]["exit_val"], 3),
                            pnl=round(b["pnl"], 2), ret=round(b["ret"], 4), eq_at_entry=round(b["eq_at_entry"], 2))
                       for b in sorted(full_bets, key=lambda z: z["f"])])
trades.to_csv(OUT / "trades_full_primary.csv", index=False)
pd.Series(RUNS[("primary", "FULL")]["eq"], index=CAL).ffill().to_csv(OUT / "equity_full_primary.csv", header=["equity"])


def jdefault(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return str(o)


(OUT / "results.json").write_text(json.dumps(results, indent=1, default=jdefault))
print(json.dumps({k: results[k] for k in ("decision", "ruin", "control_A", "spy_bh", "diagnostic_unconstrained", "diagnostic_unconstrained_combo_quote")}, indent=1, default=jdefault))
for k in ("primary|IS", "primary|HO", "primary|FULL"):
    print(k, json.dumps({kk: S[k].get(kk) for kk in ("raw_signals", "bets", "skips", "hit", "mean_ret", "median_ret",
                                                       "p_ge_100", "p_le_m80", "top5_share", "total_pnl", "terminal",
                                                       "max_dd", "cagr", "reasons")}, default=jdefault))
for c in CFG:
    print(c, {p: (S[f"{c}|{p}"].get("n"), round(S[f"{c}|{p}"].get("mean_ret") or 0, 3), round(S[f"{c}|{p}"]["terminal"], 1)) for p in ("IS", "HO", "FULL")})
print(f"[{time.time()-T0:.0f}s] done")
