"""S2 FROZEN SPEC v1 (2026-11-14): RS-leader pullback -> 1-lot bull call vertical.
PAPER RESEARCH ONLY. No orders, no broker calls. Option prices are a Black-Scholes APPROXIMATION
(IV = clip(1.25 x RV20, 0.15, 2.50)), not historical quotes.
"""
import sys, json, math, time
from pathlib import Path
from collections import Counter, defaultdict
import numpy as np, pandas as pd
from scipy.special import ndtr

sys.path.insert(0, '/home/user/ML-Trading-Platform-Backend')
import external_bars

T0 = time.time()
OUT = Path('/tmp/claude-0/-home-user/302d0d00-5f48-57c3-82ae-87f9b6d48eb7/scratchpad/asym/s2')
OUT.mkdir(parents=True, exist_ok=True)
SEED = 20261114
RATE = 0.04
H = 10
NDRAW = int(__import__("os").environ.get("NDRAW", 1000))
NBOOT = 10000
ETF = {'SPY', 'QQQ', 'IWM', 'DIA', 'TQQQ', 'SGOV', 'EFA', 'IEF', 'GLD', 'DBC', 'SMH', 'QUAL', 'VTV', 'RSP'}
EXCL = {'SGOV', 'IEF', 'SEQ', 'SEQUX', 'CBRS', 'GOOG', 'FFH', 'PSHD'}
SURV = {'PLTR', 'APP', 'HOOD', 'MSTR', 'RKLB', 'ASTS', 'OKLO', 'SMR', 'IONQ', 'CRWV', 'COIN', 'CVNA', 'ARM',
        'SOFI', 'HIMS', 'AFRM', 'UPST'}
IS_START, IS_END = pd.Timestamp('2021-11-05'), pd.Timestamp('2025-10-31')
HO_START, HO_END = pd.Timestamp('2025-11-03'), pd.Timestamp('2026-11-13')

# ---------------------------------------------------------------- data
syms = sorted({p.name.split('.')[0] for p in Path('/home/user/ML-Trading-Platform-Backend/state/external_bars').glob('*.json')} - EXCL)
D = {}
for s in syms:
    try:
        D[s] = external_bars.load_bars(s, 'etf' if s in ETF else 'equity')
    except Exception as e:
        print('load fail', s, e)
spy_df = D['SPY']
cal = spy_df.index
Cdf = pd.DataFrame({s: d['Close'] for s, d in D.items()}).reindex(cal)  # off-calendar bars dropped
Cdf = Cdf.where(Cdf > 0)
SYMS = list(Cdf.columns)
N, M = Cdf.shape
print('symbols', M, 'sessions', N, cal[0].date(), cal[-1].date())

nvalid = Cdf.notna().cumsum()
elig = (nvalid >= 252) & Cdf.notna()
R126 = Cdf / Cdf.shift(126) - 1
R126e = R126.where(elig)
RSP = R126e.rank(axis=1, pct=True)
SMA50 = Cdf.rolling(50).mean()
MX63 = Cdf.rolling(63).max()
athi = (Cdf == MX63).astype(float).where(MX63.notna(), 0.0)
rule3 = athi.shift(1).fillna(0).rolling(10).max() >= 1
rule4 = Cdf < Cdf.shift(1).rolling(5).min()
RV20 = np.log(Cdf).diff().rolling(20).std() * np.sqrt(252)
base12 = elig & (RSP >= 0.90) & (Cdf > SMA50) & RV20.notna()
rawsig = base12 & rule3 & rule4
matched_pool = base12 & ~rule4
all_pool = elig & RSP.notna() & RV20.notna()

Cff = Cdf.ffill().values
RVff = RV20.ffill().values
RSPv = RSP.values
R126v = R126.values
dayn = (cal - pd.Timestamp('1970-01-01')).days.values.astype(float)
cal_year = cal.year.values


def third_friday(y, m):
    d = pd.Timestamp(y, m, 1)
    off = (4 - d.weekday()) % 7
    return d + pd.Timedelta(days=off + 14)


def expiry_for(t):
    y, m = t.year, t.month
    for _ in range(6):
        e = third_friday(y, m)
        if (e - t).days >= 25:
            return e
        m += 1
        if m > 12:
            m, y = 1, y + 1
    raise RuntimeError


EXPD = np.array([(expiry_for(t) - pd.Timestamp('1970-01-01')).days for t in cal], dtype=float)


def period_of(n):
    t = cal[n]
    if IS_START <= t <= IS_END:
        return 'IS'
    if HO_START <= t <= HO_END:
        return 'HO'
    return None


PER = np.array([period_of(n) for n in range(N)], dtype=object)

# lockout on raw signals (per symbol, 10 sessions from previous accepted signal, traded or not)
raw_list = []  # (n, j)
rs_mask = rawsig.values
for j in range(M):
    last = -999
    for n in np.where(rs_mask[:, j])[0]:
        if n - last < 10:
            continue
        last = n
        if PER[n] is not None:
            raw_list.append((n, j))
raw_list.sort()
print('raw signals after lockout', len(raw_list), Counter(PER[n] for n, j in raw_list))


def rank_key(n, j):
    r = R126v[n, j]
    return (-RSPv[n, j], -(r if np.isfinite(r) else -9))


def inc_of(S):
    return 0.5 if S < 25 else (1.0 if S < 100 else (2.5 if S < 250 else 5.0))


def nearest(x, inc):
    return math.floor(x / inc + 0.5) * inc


def bs(S, K, T, v):
    S, K, T, v = np.broadcast_arrays(np.asarray(S, float), np.asarray(K, float), np.asarray(T, float), np.asarray(v, float))
    out = np.maximum(S - K, 0.0)
    m = T > 0
    if m.any():
        sq = v[m] * np.sqrt(T[m])
        d1 = (np.log(S[m] / K[m]) + (RATE + 0.5 * v[m] ** 2) * T[m]) / sq
        out[m] = S[m] * ndtr(d1) - K[m] * np.exp(-RATE * T[m]) * ndtr(d1 - sq)
    return out


class Scen:
    def __init__(self, name, iv_entry=1.25, iv_after=1.25, hs_mult=1.0, delay=0):
        self.name, self.iv_entry, self.iv_after, self.hs_mult, self.delay = name, iv_entry, iv_after, hs_mult, delay


SCEN = {
    'primary': Scen('primary'),
    'A_iv1.5entry': Scen('A_iv1.5entry', iv_entry=1.5),
    'B_iv1.5all': Scen('B_iv1.5all', 1.5, 1.5),
    'C_iv1.0all': Scen('C_iv1.0all', 1.0, 1.0),
    'D_spread2x': Scen('D_spread2x', hs_mult=2.0),
    'E_delay1': Scen('E_delay1', delay=1),
}
COMM = 0.65


def build(n, j, sc):
    """Price the trade for signal night n, symbol j. Returns dict with per-rung arrays (rung 0 = widest K2)."""
    d = sc.delay
    avail = min(H + d, N - 1 - n)
    if avail < d + 1 and n + d > N - 1:
        return None
    if n + d > N - 1:
        return None
    off = np.arange(avail + 1)
    S = Cff[n + off, j]
    S0 = S[0]
    T = (EXPD[n] - dayn[n + off]) / 365.0
    rv = RVff[n + off, j]
    iv = np.clip(rv * sc.iv_after, 0.15, 2.50)
    iv[d] = np.clip(rv[d] * sc.iv_entry, 0.15, 2.50)
    inc = inc_of(S0)
    K1 = nearest(S0, inc)
    K2 = nearest(1.10 * S0, inc)
    if K2 <= K1 + 1e-9:
        K2 = K1 + inc
    nr = int(round((K2 - K1) / inc))
    K2s = K1 + inc * np.arange(nr, 0, -1)
    Kc = nearest(1.05 * S0, inc)
    hs = lambda x: np.maximum(0.05 * sc.hs_mult, 0.03 * sc.hs_mult * x)
    mid1 = bs(S, K1, T, iv)
    mid2 = bs(S[None, :], K2s[:, None], T[None, :], iv[None, :])
    midc = bs(S, Kc, T, iv)
    ask1, bid1 = mid1 + hs(mid1), np.maximum(mid1 - hs(mid1), 0)
    ask2, bid2 = mid2 + hs(mid2), np.maximum(mid2 - hs(mid2), 0)
    askc, bidc = midc + hs(midc), np.maximum(midc - hs(midc), 0)
    debit = ask1[d] - bid2[:, d]
    cost = 100 * debit + 2 * COMM
    width = K2s - K1
    mark = mid1[None, :] - mid2
    thr = np.minimum(4.0 * debit, 0.8 * width)
    hi = min(H, avail)
    R = len(K2s)
    fill = np.empty(R, int); proc = np.empty(R); tp = np.zeros(R, bool); trunc = np.zeros(R, bool)
    for r in range(R):
        kdec = H
        if hi >= d + 1:
            hit = np.where(mark[r, d + 1:hi + 1] >= thr[r])[0]
            if len(hit):
                kdec = d + 1 + hit[0]; tp[r] = True
        f = kdec + d
        if f > avail:
            f = avail; trunc[r] = True
        fill[r] = f
        proc[r] = max(100 * (bid1[f] - ask2[r, f]) - 2 * COMM, 0.0)
    marks = [100 * mark[r, d:fill[r]] for r in range(R)]
    # diagnostic: unconstrained 1-lot ~1.05 x Close long call, same expiry/exits (TP at 4x debit, 10-day time exit)
    dc = askc[d]; costc = 100 * dc + COMM
    kdec = H
    if hi >= d + 1:
        hit = np.where(midc[d + 1:hi + 1] >= 4.0 * dc)[0]
        if len(hit):
            kdec = d + 1 + hit[0]
    f = min(kdec + d, avail)
    procc = max(100 * bidc[f] - COMM, 0.0)
    u10 = S[min(H, avail)] / S0 - 1
    return dict(n=n, j=j, d=d, cost=cost, fill=fill, proc=proc, marks=marks, tp=tp, trunc=trunc, K1=K1, K2s=K2s,
                width=width, ret_call=procc / costc - 1, cost_call=costc, u10=u10, S0=S0, inc=inc)


class Store:
    def __init__(self, sc):
        self.sc = sc; self.cache = {}

    def get(self, n, j):
        k = (n, j)
        if k not in self.cache:
            self.cache[k] = build(n, j, self.sc)
        return self.cache[k]


def pick_rung(tr, budget):
    ok = np.where(tr['cost'] <= budget + 1e-9)[0]
    return int(ok[0]) if len(ok) else None


def engine(cands, store, n_start, n_last_entry, keep_bets=True):
    """cands: dict night -> list of (n,j) pre-sorted by rank. Returns dict."""
    cash = 1000.0; hwm = 1000.0; openp = []; curve = []; bets = []; skips = Counter()
    n = n_start
    while n <= N - 1:
        if n > n_last_entry and not openp:
            break
        for p in openp:
            if p['ie'] == n and not p['paid']:
                cash -= p['cost']; p['paid'] = True
        still = []
        for p in openp:
            if p['ix'] == n:
                cash += p['proc']; bets.append(p)
            else:
                still.append(p)
        openp = still
        eq = cash + sum(p['marks'][n - p['ie']] for p in openp if p['ie'] <= n)
        hwm = max(hwm, eq)
        if n <= n_last_entry and n in cands:
            budget = min(0.08 * eq, 80.0)
            if eq < 0.85 * hwm:
                budget *= 0.5
            entered = False
            for (sn, sj) in cands[n]:
                if entered:
                    skips['one_per_night'] += 1; continue
                if eq < 600:
                    skips['governor_halt'] += 1; continue
                if len(openp) >= 3:
                    skips['max_concurrent'] += 1; continue
                tr = store.get(sn, sj)
                if tr is None:
                    skips['no_data'] += 1; continue
                r = pick_rung(tr, budget)
                if r is None:
                    skips['over_budget'] += 1; continue
                ie = sn + tr['d']; ix = sn + tr['fill'][r]
                p = dict(n=sn, j=sj, ie=ie, ix=ix, cost=tr['cost'][r], proc=tr['proc'][r], marks=tr['marks'][r],
                         rung=r, tp=bool(tr['tp'][r]), trunc=bool(tr['trunc'][r]), width=tr['width'][r], K1=tr['K1'],
                         K2=tr['K2s'][r], ret_call=tr['ret_call'], u10=tr['u10'], S0=tr['S0'], budget=budget, paid=False)
                if ie == n:
                    cash -= p['cost']; p['paid'] = True
                if ix == ie:  # cannot happen (fill >= d+1) but guard
                    cash += p['proc']; bets.append(p)
                else:
                    openp.append(p)
                entered = True
                skips['entered'] += 1
        eq2 = cash + sum(p['marks'][n - p['ie']] for p in openp if p['ie'] <= n)
        curve.append((n, eq2))
        n += 1
    for p in openp:  # should not happen: truncated trades exit at the last bar
        cash += p['proc']; bets.append(p)
    for b in bets:
        b['pnl'] = b['proc'] - b['cost']; b['ret'] = b['pnl'] / b['cost']
    curve = pd.Series([e for _, e in curve], index=[cal[k] for k, _ in curve])
    return dict(bets=bets, curve=curve, skips=skips, terminal=float(curve.iloc[-1]) if len(curve) else 1000.0)


def cands_from(pairs):
    c = defaultdict(list)
    for n, j in pairs:
        c[n].append((n, j))
    for n in c:
        c[n].sort(key=lambda x: rank_key(*x))
    return c


def nidx(ts, side):
    return int(np.searchsorted(cal.values, np.datetime64(ts), side=side)) - (1 if side == 'right' else 0)


N_IS0, N_IS1 = nidx(IS_START, 'left'), nidx(IS_END, 'right')
N_HO0, N_HO1 = nidx(HO_START, 'left'), nidx(HO_END, 'right')
WIN = {'IS': (N_IS0, N_IS1), 'HO': (N_HO0, N_HO1), 'full': (N_IS0, N_HO1)}


def in_win(pairs, w):
    a, b = WIN[w]
    return [(n, j) for n, j in pairs if a <= n <= b]


def max_dd(curve):
    if len(curve) == 0:
        return 0.0
    return float((curve / curve.cummax() - 1).min())


def cagr(term, w):
    a, b = WIN[w]
    yrs = (cal[b] - cal[a]).days / 365.25
    return (term / 1000.0) ** (1 / yrs) - 1


def block_boot_ruin(curve, rng, nb=NBOOT, blk=21):
    r = curve.pct_change().dropna().values
    L = len(r)
    if L < blk:
        return np.nan, np.nan
    nblk = int(np.ceil(L / blk))
    starts = rng.integers(0, L - blk + 1, size=(nb, nblk))
    idx = (starts[:, :, None] + np.arange(blk)[None, None, :]).reshape(nb, -1)[:, :L]
    paths = 1000 * np.cumprod(1 + r[idx], axis=1)
    mn = paths.min(axis=1)
    return float((mn <= 500).mean()), float((mn <= 600).mean())


def bet_boot_ruin(pnls, rng, nb=NBOOT):
    pnls = np.asarray(pnls)
    if len(pnls) == 0:
        return np.nan, np.nan
    s = rng.choice(pnls, size=(nb, len(pnls)), replace=True)
    paths = 1000 + np.cumsum(s, axis=1)
    mn = np.minimum(paths.min(axis=1), 1000)
    return float((mn < 100).mean()), float((mn <= 500).mean())


def week_boot_ci(bets, rng, nb=NBOOT):
    if not bets:
        return (np.nan, np.nan), 0
    wk = pd.Series([b['ret'] for b in bets], index=[cal[b['n']].to_period('W-FRI') for b in bets])
    groups = [g.values for _, g in wk.groupby(level=0)]
    G = len(groups)
    sums = np.array([g.sum() for g in groups]); cnts = np.array([len(g) for g in groups])
    pick = rng.integers(0, G, size=(nb, G))
    means = sums[pick].sum(1) / cnts[pick].sum(1)
    return (float(np.percentile(means, 5)), float(np.percentile(means, 95))), G


def bet_stats(bets):
    if not bets:
        return dict(n=0)
    r = np.array([b['ret'] for b in bets]); pnl = np.array([b['pnl'] for b in bets])
    w = r[r > 0]; l = r[r <= 0]
    tot = pnl.sum(); srt = np.sort(pnl)[::-1]
    if tot > 0:
        top5 = float(srt[:5].sum() / tot); top5_label = 'share'
    else:
        top5 = float(srt[5:].sum()); top5_label = 'total_ex_top5'
    return dict(n=len(r), hit=float((r > 0).mean()), mean=float(r.mean()), median=float(np.median(r)),
                p_ge100=float((r >= 1).mean()), p_le_m80=float((r <= -0.8).mean()),
                avg_win=float(w.mean()) if len(w) else np.nan, avg_loss=float(l.mean()) if len(l) else np.nan,
                win_loss_ratio=float(w.mean() / abs(l.mean())) if len(w) and len(l) and l.mean() != 0 else np.nan,
                pnl_total=float(tot), top5=top5, top5_label=top5_label, avg_cost=float(np.mean([b['cost'] for b in bets])),
                tp_rate=float(np.mean([b['tp'] for b in bets])),
                call_diag_mean=float(np.mean([b['ret_call'] for b in bets])),
                call_diag_median=float(np.median([b['ret_call'] for b in bets])),
                u10_mean=float(np.mean([b['u10'] for b in bets])))


def pctile(x, ctrl):
    ctrl = np.asarray([c for c in ctrl if np.isfinite(c)])
    if len(ctrl) == 0 or not np.isfinite(x):
        return np.nan
    return float(100 * ((ctrl < x).mean() + 0.5 * (ctrl == x).mean()))


# ---------------------------------------------------------------- primary + stress runs on the strategy
rng = np.random.default_rng(SEED)
stores = {k: Store(v) for k, v in SCEN.items()}
RES = {}
for sk, st in stores.items():
    RES[sk] = {}
    for w in ('IS', 'HO', 'full'):
        pr = in_win(raw_list, w)
        RES[sk][w] = engine(cands_from(pr), st, WIN[w][0], WIN[w][1])
print('strategy runs done', round(time.time() - T0, 1), 's')
prim = stores['primary']

# signal-level (fixed $80 budget = $1,000 equity, no governor, no caps) per-signal returns
_SLC = {}


def sig_level(pairs, st, budget=80.0):
    out = []
    for n, j in pairs:
        key = (st.sc.name, n, j, budget)
        if key not in _SLC:
            tr = st.get(n, j)
            r = None if tr is None else pick_rung(tr, budget)
            _SLC[key] = None if r is None else (tr['proc'][r] - tr['cost'][r]) / tr['cost'][r]
        v = _SLC[key]
        if v is not None:
            out.append(v)
    return np.array(out)


# ---------------------------------------------------------------- controls
def bucket(pairs):
    b = defaultdict(list)
    for n, j in pairs:
        b[(PER[n], cal_year[n])].append((n, j))
    return b


raw_b = bucket(raw_list)
mp = matched_pool.values; ap = all_pool.values
mp_pairs = [(n, j) for n, j in zip(*np.where(mp)) if PER[n] is not None]
mp_b = bucket(mp_pairs)
mp_b_ns = bucket([(n, j) for n, j in mp_pairs if SYMS[j] not in SURV])
raw_ns = [(n, j) for n, j in raw_list if SYMS[j] not in SURV]
raw_b_ns = bucket(raw_ns)
ap_pairs = np.array([(n, j) for n, j in zip(*np.where(ap)) if PER[n] is not None])
print('pools: matched', len(mp_pairs), 'all-valid', len(ap_pairs), 'buckets', {k: len(v) for k, v in raw_b.items()})


def run_ctrl(draw_fn, ndraw, label):
    rec = {w: dict(mean=[], term=[], n=[], mean_ns=[], dd=[]) for w in ('IS', 'HO', 'full')}
    sig = {w: [] for w in ('IS', 'HO', 'full')}
    yr = defaultdict(list); u10 = {w: [] for w in ('IS', 'HO', 'full')}
    for k in range(ndraw):
        pairs = draw_fn()
        for w in ('IS', 'HO', 'full'):
            pw = in_win(pairs, w)
            e = engine(cands_from(pw), prim, WIN[w][0], WIN[w][1])
            rs = [b['ret'] for b in e['bets']]
            rec[w]['mean'].append(np.mean(rs) if rs else np.nan)
            rec[w]['n'].append(len(rs)); rec[w]['term'].append(e['terminal']); rec[w]['dd'].append(max_dd(e['curve']))
            rns = [b['ret'] for b in e['bets'] if SYMS[b['j']] not in SURV]
            rec[w]['mean_ns'].append(np.mean(rns) if rns else np.nan)
            sl = sig_level(pw, prim)
            sig[w].append(sl.mean() if len(sl) else np.nan)
            if w == 'full':
                for b in e['bets']:
                    yr[cal_year[b['n']]].append(b['ret'])
            u10[w].append(np.mean([prim.get(n, j)['u10'] for n, j in pw if prim.get(n, j) is not None]) if label == 'b' and pw else np.nan)
        if (k + 1) % 100 == 0:
            print(label, k + 1, round(time.time() - T0, 1), 's', flush=True)
    return rec, sig, yr, u10


def draw_matched():
    out = []
    for key, lst in raw_b.items():
        pool = mp_b.get(key, [])
        m = min(len(lst), len(pool))
        if m:
            idx = rng.choice(len(pool), size=m, replace=False)
            out += [pool[i] for i in idx]
    return out


def draw_random3():
    sel = rng.random(len(ap_pairs)) < 0.03
    return [tuple(x) for x in ap_pairs[sel]]


ctrl_b, ctrl_b_sig, ctrl_b_yr, ctrl_b_u10 = run_ctrl(draw_matched, NDRAW, 'b')
ctrl_a, ctrl_a_sig, ctrl_a_yr, _ = run_ctrl(draw_random3, NDRAW, 'a')

# survivor-excluded signal-level matched control
ns_sig = {w: [] for w in ('IS', 'HO', 'full')}
for k in range(NDRAW):
    out = []
    for key, lst in raw_b_ns.items():
        pool = mp_b_ns.get(key, [])
        m = min(len(lst), len(pool))
        if m:
            idx = rng.choice(len(pool), size=m, replace=False)
            out += [pool[i] for i in idx]
    for w in ('IS', 'HO', 'full'):
        sl = sig_level(in_win(out, w), prim)
        ns_sig[w].append(sl.mean() if len(sl) else np.nan)
print('controls done', round(time.time() - T0, 1), 's')

# ---------------------------------------------------------------- SPY buy & hold
spyc = spy_df['Close'].reindex(cal)
def spy_bh(a, b):
    s = spyc.iloc[a:b + 1]
    c = 1000 * s / s.iloc[0]
    return float(c.iloc[-1]), max_dd(c)
SPY = {'IS': spy_bh(N_IS0, N_IS1), 'HO': spy_bh(N_IS1, N_HO1), 'full': spy_bh(N_IS0, N_HO1)}

# ---------------------------------------------------------------- assemble metrics
summary = {}
for w in ('IS', 'HO', 'full'):
    e = RES['primary'][w]
    bets = e['bets']
    st = bet_stats(bets)
    ci, nweeks = week_boot_ci(bets, rng)
    pr500, pr600 = block_boot_ruin(e['curve'], rng)
    pb100, pb500 = bet_boot_ruin([b['pnl'] for b in bets], rng)
    raw_w = in_win(raw_list, w)
    sl = sig_level(raw_w, prim)
    raw_weeks = len({cal[n].to_period('W-FRI') for n, j in raw_w})
    u10_raw = np.mean([prim.get(n, j)['u10'] for n, j in raw_w if prim.get(n, j) is not None])
    ns_bets = [b for b in bets if SYMS[b['j']] not in SURV]
    sl_ns = sig_level(in_win(raw_ns, w), prim)
    summary[w] = dict(
        raw_signals=len(raw_w), raw_signal_weeks=raw_weeks, bets=st.get('n', 0), skips=dict(e['skips']), stats=st,
        week_ci90=ci, bet_weeks=nweeks, terminal=e['terminal'], max_dd=max_dd(e['curve']), cagr=cagr(e['terminal'], w),
        p_ruin_touch500=pr500, p_touch600=pr600, p_bet_boot_lt100=pb100, p_bet_boot_le500=pb500,
        pct_b_mean=pctile(st.get('mean', np.nan), ctrl_b[w]['mean']), pct_b_term=pctile(e['terminal'], ctrl_b[w]['term']),
        b_mean_avg=float(np.nanmean(ctrl_b[w]['mean'])), b_mean_p95=float(np.nanpercentile(ctrl_b[w]['mean'], 95)),
        b_term_med=float(np.median(ctrl_b[w]['term'])), b_n_avg=float(np.mean(ctrl_b[w]['n'])),
        pct_a_mean=pctile(st.get('mean', np.nan), ctrl_a[w]['mean']), pct_a_term=pctile(e['terminal'], ctrl_a[w]['term']),
        a_mean_avg=float(np.nanmean(ctrl_a[w]['mean'])), a_term_med=float(np.median(ctrl_a[w]['term'])),
        sig_level_mean=float(sl.mean()) if len(sl) else np.nan, sig_level_n=len(sl), sig_level_fit_rate=len(sl) / max(len(raw_w), 1),
        sig_pct_b=pctile(sl.mean() if len(sl) else np.nan, ctrl_b_sig[w]), sig_b_avg=float(np.nanmean(ctrl_b_sig[w])),
        sig_pct_a=pctile(sl.mean() if len(sl) else np.nan, ctrl_a_sig[w]), sig_a_avg=float(np.nanmean(ctrl_a_sig[w])),
        u10_raw=float(u10_raw), u10_matched=float(np.nanmean(ctrl_b_u10[w])),
        ns_engine_mean=float(np.mean([b['ret'] for b in ns_bets])) if ns_bets else np.nan, ns_engine_n=len(ns_bets),
        ns_engine_ctrl=float(np.nanmean(ctrl_b[w]['mean_ns'])),
        ns_sig_mean=float(sl_ns.mean()) if len(sl_ns) else np.nan, ns_sig_n=len(sl_ns), ns_sig_ctrl=float(np.nanmean(ns_sig[w])),
        ns_sig_pct=pctile(sl_ns.mean() if len(sl_ns) else np.nan, ns_sig[w]),
        spy_terminal=SPY[w][0], spy_dd=SPY[w][1])

stress = {}
for sk in SCEN:
    stress[sk] = {}
    for w in ('IS', 'HO', 'full'):
        e = RES[sk][w]; st = bet_stats(e['bets'])
        stress[sk][w] = dict(n=st.get('n', 0), mean=st.get('mean', np.nan), median=st.get('median', np.nan), hit=st.get('hit', np.nan),
                             terminal=e['terminal'], max_dd=max_dd(e['curve']), skips=dict(e['skips']))

# by year (full engine, entry year) + matched control by year
by_year = {}
fb = RES['primary']['full']['bets']
for y in sorted({cal_year[b['n']] for b in fb}):
    bb = [b for b in fb if cal_year[b['n']] == y]
    st = bet_stats(bb)
    by_year[int(y)] = dict(n=st['n'], mean=st['mean'], median=st['median'], hit=st['hit'], pnl=st['pnl_total'],
                           ctrl_b_mean=float(np.mean(ctrl_b_yr[y])) if ctrl_b_yr[y] else np.nan,
                           ctrl_a_mean=float(np.mean(ctrl_a_yr[y])) if ctrl_a_yr[y] else np.nan,
                           raw=sum(1 for n, j in raw_list if cal_year[n] == y))

# decision
S_IS = summary['IS']; S_HO = summary['HO']
dec = {
    'IS_mean_ge_p95_matched': S_IS['stats'].get('mean', -9) >= S_IS['b_mean_p95'],
    'IS_mean_gt0_stressA': stress['A_iv1.5entry']['IS']['mean'] > 0,
    'IS_mean_gt0_stressE': stress['E_delay1']['IS']['mean'] > 0,
    'survivor_excl_IS_gt_matched(engine)': S_IS['ns_engine_mean'] > S_IS['ns_engine_ctrl'],
    'survivor_excl_IS_gt_matched(signal-level)': S_IS['ns_sig_mean'] > S_IS['ns_sig_ctrl'],
    'HO_mean_gt_matched_and_n>=20': (S_HO['stats'].get('mean', -9) > S_HO['b_mean_avg']) and S_HO['bets'] >= 20,
    'P_ruin_lt_5pct(IS block bootstrap)': S_IS['p_ruin_touch500'] < 0.05,
}
dec_pass = all(v for k, v in dec.items() if k != 'survivor_excl_IS_gt_matched(signal-level)')

# 2022 sub-period
b22 = [b for b in fb if cal_year[b['n']] == 2022]
sub2022 = bet_stats(b22)

# save bets and curves
rows = []
for w in ('IS', 'HO'):
    for b in RES['primary'][w]['bets']:
        rows.append(dict(period=w, sym=SYMS[b['j']], signal=cal[b['n']].date(), entry=cal[b['ie']].date(), exit=cal[b['ix']].date(),
                         S0=round(b['S0'], 2), K1=b['K1'], K2=b['K2'], width=b['width'], cost=round(b['cost'], 2), proceeds=round(b['proc'], 2),
                         pnl=round(b['pnl'], 2), ret=round(b['ret'], 4), tp=b['tp'], trunc=b['trunc'], budget=round(b['budget'], 2),
                         call_diag_ret=round(b['ret_call'], 4), u10=round(b['u10'], 4)))
pd.DataFrame(rows).to_csv(OUT / 'bets.csv', index=False)
pd.DataFrame({w: RES['primary'][w]['curve'] for w in ('IS', 'HO', 'full')}).to_csv(OUT / 'equity.csv')


def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else round(float(o), 5)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


res = dict(summary=summary, stress=stress, by_year=by_year, sub2022=sub2022, decision=dec, decision_pass=dec_pass,
           raw_counts={k: len(in_win(raw_list, k)) for k in WIN}, runtime_s=round(time.time() - T0, 1))
(OUT / 'results.json').write_text(json.dumps(clean(res), indent=1, default=str))

# ---------------------------------------------------------------- results.md
f = lambda x, p=3: ('n/a' if x is None or (isinstance(x, float) and not np.isfinite(x)) else (f'{x:+.{p}f}' if isinstance(x, float) else str(x)))
L = ['# S2 backtest: RS-leader pullback -> 1-lot bull call vertical (FROZEN SPEC v1)', '',
     'Paper research only, no orders. Option prices are a **Black-Scholes approximation** (IV = clip(1.25 x RV20, 0.15, 2.50), r=4%, '
     'half-spread max($0.05, 3% of mid) per leg per side, $0.65/contract/leg/side); no historical option quotes. Universe chosen in 2026 '
     '(survivor bias): compare with controls, not absolutes. First possible signal is ~Nov 2022 (252-bar eligibility on data starting 2021-11-05).', '']
L += ['## Main results (primary run, $1,000 engine, HO restarted at $1,000)', '',
      '| metric | IS | HO | full |', '|---|---|---|---|']
def row(name, fn):
    L.append(f'| {name} | ' + ' | '.join(fn(summary[w]) for w in ('IS', 'HO', 'full')) + ' |')
row('raw signals (post-lockout)', lambda s: str(s['raw_signals']))
row('distinct signal weeks', lambda s: str(s['raw_signal_weeks']))
row('bets taken', lambda s: str(s['bets']))
row('skips', lambda s: ', '.join(f'{k}={v}' for k, v in sorted(s['skips'].items()) if k != 'entered'))
row('hit rate', lambda s: f(s['stats'].get('hit'), 2))
row('mean net ret/bet', lambda s: f(s['stats'].get('mean')))
row('median net ret/bet', lambda s: f(s['stats'].get('median')))
row('week-block 90% CI of mean', lambda s: f"[{f(s['week_ci90'][0])}, {f(s['week_ci90'][1])}]")
row('P(ret >= +100%)', lambda s: f(s['stats'].get('p_ge100'), 3))
row('P(ret <= -80%)', lambda s: f(s['stats'].get('p_le_m80'), 3))
row('avg win / avg loss', lambda s: f"{f(s['stats'].get('avg_win'))} / {f(s['stats'].get('avg_loss'))}")
row('take-profit hit rate', lambda s: f(s['stats'].get('tp_rate'), 2))
row('avg debit $', lambda s: f(s['stats'].get('avg_cost'), 2))
row('net P&L $', lambda s: f(s['stats'].get('pnl_total'), 2))
row('top-5 share of P&L (or total ex-top5 if total<=0)', lambda s: f"{f(s['stats'].get('top5'), 2)} ({s['stats'].get('top5_label')})")
row('terminal equity $', lambda s: f'{s["terminal"]:.2f}')
row('CAGR (nominal window)', lambda s: f(s['cagr']))
row('max drawdown', lambda s: f(s['max_dd']))
row('P(ruin: touch $500), 21-session block bootstrap', lambda s: f(s['p_ruin_touch500'], 4))
row('P(touch $600), block bootstrap', lambda s: f(s['p_touch600'], 4))
row('P(<$100), bet-sequence bootstrap', lambda s: f(s['p_bet_boot_lt100'], 4))
row('pctile vs matched (b): mean / terminal', lambda s: f"{s['pct_b_mean']:.1f} / {s['pct_b_term']:.1f}")
row('matched (b) mean ret/bet avg, p95', lambda s: f"{f(s['b_mean_avg'])}, {f(s['b_mean_p95'])}")
row('matched (b) median terminal $', lambda s: f"{s['b_term_med']:.2f}")
row('pctile vs random 3% (a): mean / terminal', lambda s: f"{s['pct_a_mean']:.1f} / {s['pct_a_term']:.1f}")
row('random (a) mean ret/bet avg; median terminal', lambda s: f"{f(s['a_mean_avg'])}; {s['a_term_med']:.2f}")
row('signal-level ($80 fixed budget) mean, n, fit rate', lambda s: f"{f(s['sig_level_mean'])}, {s['sig_level_n']}, {s['sig_level_fit_rate']:.2f}")
row('signal-level pctile vs (b) / (a)', lambda s: f"{s['sig_pct_b']:.1f} / {s['sig_pct_a']:.1f}")
row('signal-level control avg (b) / (a)', lambda s: f"{f(s['sig_b_avg'])} / {f(s['sig_a_avg'])}")
row('10-day underlying ret: signals vs matched', lambda s: f"{f(s['u10_raw'], 4)} vs {f(s['u10_matched'], 4)}")
row('survivor-excl engine mean (n) vs matched', lambda s: f"{f(s['ns_engine_mean'])} ({s['ns_engine_n']}) vs {f(s['ns_engine_ctrl'])}")
row('survivor-excl signal-level mean (n) vs matched, pctile', lambda s: f"{f(s['ns_sig_mean'])} ({s['ns_sig_n']}) vs {f(s['ns_sig_ctrl'])}, {s['ns_sig_pct']:.1f}")
row('diagnostic 1.05x long call mean / median (same bets)', lambda s: f"{f(s['stats'].get('call_diag_mean'))} / {f(s['stats'].get('call_diag_median'))}")
row('SPY B&H $1,000 terminal / max DD', lambda s: f"{s['spy_terminal']:.2f} / {f(s['spy_dd'])}")
L += ['', '## Stress runs (strategy only)', '', '| run | IS n | IS mean | IS terminal | HO n | HO mean | HO terminal | full mean | full maxDD |', '|---|---|---|---|---|---|---|---|---|']
for sk, v in stress.items():
    L.append(f"| {sk} | {v['IS']['n']} | {f(v['IS']['mean'])} | {v['IS']['terminal']:.2f} | {v['HO']['n']} | {f(v['HO']['mean'])} | {v['HO']['terminal']:.2f} | {f(v['full']['mean'])} | {f(v['full']['max_dd'])} |")
L += ['', '## By entry year (full-period engine)', '', '| year | raw | bets | mean | median | hit | P&L $ | matched (b) mean | random (a) mean |', '|---|---|---|---|---|---|---|---|---|']
for y, v in by_year.items():
    L.append(f"| {y} | {v['raw']} | {v['n']} | {f(v['mean'])} | {f(v['median'])} | {f(v['hit'], 2)} | {f(v['pnl'], 2)} | {f(v['ctrl_b_mean'])} | {f(v['ctrl_a_mean'])} |")
L += ['', f"2022 sub-period (Nov-Dec 2022 only, the first eligible months): n={sub2022.get('n', 0)}, mean={f(sub2022.get('mean'))}, "
      f"hit={f(sub2022.get('hit'), 2)}, P&L=${f(sub2022.get('pnl_total'), 2)}", '']
L += ['## Frozen decision rule', '']
for k, v in dec.items():
    L.append(f'- {k}: **{"PASS" if v else "FAIL"}**' + (' (reported, not used: engine version is the rule)' if 'signal-level' in k else ''))
L += ['', f'**Overall: {"GO to live paper" if dec_pass else "NO-GO (do not adopt)"}**', '',
      '## Notes', '',
      '- Equity = cash + open spreads at model mid. Exits sell at model bid/ask; a spread whose exit proceeds after costs would be negative is abandoned at $0 (max loss = debit).',
      '- Matched control (b): per (period, calendar year) bucket, the same number of random (symbol, day) pairs as raw strategy signals, drawn from days with RSpct>=0.90 and Close>SMA50 but NOT Close<min(prior 5 closes); fed through the identical engine. Control (a): Bernoulli 3% of all eligible symbol-days.',
      '- Signal-level rows price every raw signal at a fixed $80 budget (no caps, no governor), to separate signal quality from engine path effects.',
      '- HO trades entered in the last 10 sessions are closed at the last bar (2026-11-13).',
      '- Bet-sequence bootstrap P(<$100) resamples dollar P&L of bets; it ignores the governor.',
      f'- Runtime {res["runtime_s"]} s; seed {SEED}; {NDRAW} control draw sets; {NBOOT} bootstrap paths.']
(OUT / 'results.md').write_text('\n'.join(L) + '\n')
print('\n'.join(L))
