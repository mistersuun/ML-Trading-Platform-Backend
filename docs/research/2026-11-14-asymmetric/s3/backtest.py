"""S3 / P3 FROZEN SPEC v1 (2026-11-14): breadth washout (share of universe with RSI(2) < 10 > 0.50)
-> 1-lot 2%-OTM bull call vertical on QQQ (or SPY when QQQ RV20 > 1.5 x SPY RV20), ~30 DTE Friday expiry.

PAPER RESEARCH ONLY. No orders, no broker calls, offline. Option prices are a Black-Scholes APPROXIMATION
(IV = clip(1.25 x RV20 of the traded ETF, 0.13, 1.00), r = 4%, q = 0), NOT historical option quotes.
Parameters are frozen; this script only implements the spec.
"""
import sys, json, math, time, os
from pathlib import Path
from collections import Counter, defaultdict
import numpy as np, pandas as pd
from scipy.special import ndtr

sys.path.insert(0, '/home/user/ML-Trading-Platform-Backend')
import external_bars

T0 = time.time()
OUT = Path('/tmp/claude-0/-home-user/302d0d00-5f48-57c3-82ae-87f9b6d48eb7/scratchpad/asym/s3')
OUT.mkdir(parents=True, exist_ok=True)
SEED = 20261114
rng = np.random.default_rng(SEED)
RATE = 0.04
H = 10                      # time exit: close of 10th trading day after t
LOCK = 10                   # signal lockout (sessions)
THR = 0.50                  # frozen breadth threshold
WIDTHS = (5, 4, 3, 2, 1)
COMM = 0.65                 # $ per contract per leg per side
NDRAW = int(os.environ.get('NDRAW', 1000))
NBOOT = 10000
ETF = {'SPY', 'QQQ', 'IWM', 'DIA', 'TQQQ', 'SGOV', 'EFA', 'IEF', 'GLD', 'DBC', 'SMH', 'QUAL', 'VTV', 'RSP'}
EXCL = {'SGOV', 'SEQ', 'SEQUX', 'CBRS', 'FFH', 'PSHD'}
IS_START, IS_END = pd.Timestamp('2021-11-05'), pd.Timestamp('2025-10-31')
HO_START, HO_END = pd.Timestamp('2025-11-03'), pd.Timestamp('2026-11-13')

# ---------------------------------------------------------------- data (SPY calendar only)
allsyms = sorted({p.name.split('.')[0] for p in Path('/home/user/ML-Trading-Platform-Backend/state/external_bars').glob('*.json')})
D = {}
for s in allsyms:
    if s in EXCL:
        continue
    try:
        D[s] = external_bars.load_bars(s, 'etf' if s in ETF else 'equity')
    except Exception as e:
        print('load fail', s, e)
cal = D['SPY'].index
N = len(cal)
print('breadth universe', len(D), 'sessions', N, cal[0].date(), cal[-1].date())


def rsi2(c):
    c = c.dropna()
    c = c[c > 0]
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


R = pd.DataFrame({s: rsi2(df['Close'].reindex(cal)) for s, df in D.items()}).reindex(cal)
breadth = (R < 10).sum(1) / R.notna().sum(1)

px = {}
for s in ('QQQ', 'SPY'):
    c = D[s]['Close'].reindex(cal)
    px[s] = dict(C=c.values.astype(float),
                 RV=(np.log(c).diff().rolling(20).std() * math.sqrt(252)).values)
dayn = (cal - pd.Timestamp('1970-01-01')).days.values
YEAR = cal.year.values


def period_of(t):
    if IS_START <= t <= IS_END:
        return 'IS'
    if HO_START <= t <= HO_END:
        return 'HO'
    return None


PER = np.array([period_of(t) for t in cal], dtype=object)
N_IS0 = int(np.searchsorted(cal, IS_START)); N_IS1 = int(np.searchsorted(cal, IS_END, 'right') - 1)
N_HO0 = int(np.searchsorted(cal, HO_START)); N_HO1 = int(np.searchsorted(cal, HO_END, 'right') - 1)
WIN = {'IS': (N_IS0, N_IS1), 'HO': (N_HO0, N_HO1), 'full': (N_IS0, N_HO1)}


def signals(thr):
    """Post-lockout signal sessions (any period). Lockout counts from the previous accepted signal."""
    out, last = [], -10 ** 9
    b = breadth.values
    for n in range(N):
        if np.isfinite(b[n]) and b[n] > thr and n - last >= LOCK:
            out.append(n); last = n
    return out


SIG = {thr: signals(thr) for thr in (0.40, 0.50, 0.60)}
print('signals @0.50', len(SIG[0.5]), Counter(YEAR[n] for n in SIG[0.5]), [str(cal[n].date()) for n in SIG[0.5]])
print('HO max breadth', round(float(breadth.iloc[N_HO0:N_HO1 + 1].max()), 4),
      breadth.iloc[N_HO0:N_HO1 + 1].idxmax().date())


# ---------------------------------------------------------------- option model
def bs(S, K, T, v):
    if T <= 0:
        return max(S - K, 0.0)
    sq = v * math.sqrt(T)
    d1 = (math.log(S / K) + (RATE + 0.5 * v * v) * T) / sq
    return float(S * ndtr(d1) - K * math.exp(-RATE * T) * ndtr(d1 - sq))


def expiry_days(n):
    """Friday with calendar DTE closest to 30 (ties to the later one). Returns epoch-day of expiry."""
    t = cal[n]
    best = None
    for dte in range(1, 60):
        if (t + pd.Timedelta(days=dte)).weekday() == 4:
            key = (abs(dte - 30), -dte)
            if best is None or key < best[0]:
                best = (key, dte)
    return int(dayn[n] + best[1])


class Scen:
    def __init__(self, name, iv_entry=1.25, iv_after=1.25, hs_mult=1.0, delay=0, force_spy=False,
                 boiler_cost=False):
        self.name, self.iv_entry, self.iv_after, self.hs_mult = name, iv_entry, iv_after, hs_mult
        self.delay, self.force_spy, self.boiler_cost = delay, force_spy, boiler_cost


def half_spread(mid, sc):
    if sc.boiler_cost:          # generic task cost: max($0.05, 5% of price) per side
        return max(0.05, 0.05 * mid)
    return sc.hs_mult * max(0.02, 0.005 * mid)


def iv_at(sym, n, mult):
    rv = px[sym]['RV'][n]
    return float(np.clip(mult * rv, 0.13, 1.00))


def instrument(n, sc):
    if sc.force_spy:
        return 'SPY'
    q, s = px['QQQ']['RV'][n], px['SPY']['RV'][n]
    return 'SPY' if q > 1.5 * s else 'QQQ'


def leg_quotes(S, K, T, v, sc):
    m = bs(S, K, T, v)
    h = half_spread(m, sc)
    return m, max(m - h, 0.0), m + h


_cache = {}


def trade(n_sig, sc):
    """Precomputed trade for a decision at the close of n_sig (or n_sig+delay). Independent of equity except
    for the width choice, so all widths are computed. Returns None if no data for entry."""
    key = (n_sig, sc.name)
    if key in _cache:
        return _cache[key]
    ne = n_sig + sc.delay
    if ne >= N:
        _cache[key] = None; return None
    sym = instrument(ne, sc)
    C = px[sym]['C']
    S0 = C[ne]
    if not np.isfinite(S0) or not np.isfinite(px[sym]['RV'][ne]):
        _cache[key] = None; return None
    exp_d = expiry_days(ne)
    K1 = math.floor(1.02 * S0 + 0.5)
    T0e = (exp_d - dayn[ne]) / 365.0
    v0 = iv_at(sym, ne, sc.iv_entry)
    m1, b1, a1 = leg_quotes(S0, K1, T0e, v0, sc)
    nx_time = min(ne + H, N - 1)
    trunc = (ne + H) > N - 1
    res = dict(n=n_sig, ne=ne, sym=sym, S0=S0, K1=K1, exp=exp_d, dte=exp_d - dayn[ne], iv0=v0, trunc=trunc,
               u10_qqq=(px['QQQ']['C'][min(n_sig + H, N - 1)] / px['QQQ']['C'][n_sig] - 1), W={})
    for W in WIDTHS:
        K2 = K1 + W
        m2, b2, a2 = leg_quotes(S0, K2, T0e, v0, sc)
        debit = a1 - b2
        cost = 100 * debit + 2 * COMM
        tp_lvl = min(2.5 * debit, 0.85 * W)
        marks, nx, proceeds, reason = [], None, None, None
        for u in range(ne + 1, nx_time + 1):
            T = (exp_d - dayn[u]) / 365.0
            v = iv_at(sym, u, sc.iv_after)
            q1 = leg_quotes(C[u], K1, T, v, sc); q2 = leg_quotes(C[u], K2, T, v, sc)
            mark = q1[0] - q2[0]
            liq = q1[1] - q2[2]
            if (mark >= tp_lvl) or (u == nx_time):
                nx = u
                reason = 'tp' if mark >= tp_lvl else ('time' if not trunc else 'trunc')
                proceeds = 100 * liq - 2 * COMM if liq > 0 else 0.0  # spread abandoned if bid-side value <= 0
                marks.append(proceeds)
                break
            marks.append(100 * mark)
        res['W'][W] = dict(K2=K2, debit=debit, cost=cost, tp_lvl=tp_lvl, nx=nx, proceeds=proceeds, reason=reason,
                           marks=marks, mid0=m1 - m2)
    _cache[key] = res
    return res


# ---------------------------------------------------------------- engine (event based, max 1 open)
def engine(sig_list, sc, a, b, skip_years=()):
    """sig_list: decision sessions. Entries allowed for decision sessions in [a, b]. Exits are processed before
    entries at the same close, so a new signal on the exit session of the previous bet may be taken."""
    cash, hwm = 1000.0, 1000.0
    curve = {a: 1000.0}
    bets, skips, open_until = [], Counter(), -1
    for n in sig_list:
        if n < a or n > b:
            continue
        if YEAR[n] in skip_years:
            skips['left_out_year'] += 1; continue
        if n < open_until:
            skips['position_open'] += 1; continue
        eq = cash
        if eq < 600:
            skips['halted_below_600'] += 1; continue
        frac = 0.04 if eq < 0.85 * hwm else 0.08
        tr = trade(n, sc)
        if tr is None:
            skips['no_data'] += 1; continue
        W = next((w for w in WIDTHS if tr['W'][w]['cost'] <= frac * eq), None)
        if W is None:
            skips['over_budget_W1'] += 1; continue
        t = tr['W'][W]
        cash -= t['cost']
        curve[tr['ne']] = cash + 100 * t['mid0']  # marked at model mid right after the fill
        for k, mk in enumerate(t['marks']):
            u = tr['ne'] + 1 + k
            curve[u] = cash + mk
            hwm = max(hwm, cash + mk)
        cash += t['proceeds']
        open_until = t['nx']
        pnl = t['proceeds'] - t['cost']
        bets.append(dict(n=n, date=str(cal[n].date()), entry=str(cal[tr['ne']].date()), exit=str(cal[t['nx']].date()),
                         sym=tr['sym'], S0=round(tr['S0'], 2), K1=tr['K1'], W=W, dte=int(tr['dte']),
                         iv0=round(tr['iv0'], 3), breadth=round(float(breadth.iloc[n]), 3), debit=round(t['debit'], 3),
                         cost=round(t['cost'], 2), proceeds=round(t['proceeds'], 2), pnl=pnl, ret=pnl / t['cost'],
                         reason=t['reason'], days=t['nx'] - tr['ne'], frac=frac, eq_before=round(eq, 2),
                         qqq10=tr['u10_qqq'], trunc=tr['trunc']))
    end = max(b, max(curve))
    s = pd.Series(curve).sort_index()
    s = s.reindex(range(a, end + 1)).ffill()
    s.index = cal[a:end + 1]
    return dict(bets=bets, skips=skips, curve=s, terminal=float(s.iloc[-1]))


def max_dd(c):
    return float((c / c.cummax() - 1).min()) if len(c) else 0.0


def cagr(term, w):
    a, b = WIN[w]
    yrs = (cal[b] - cal[a]).days / 365.25
    return (term / 1000.0) ** (1 / yrs) - 1


def block_boot(curve, nb=NBOOT, blk=21):
    r = curve.pct_change().dropna().values
    L = len(r)
    if L < blk:
        return np.nan, np.nan
    nblk = int(np.ceil(L / blk))
    starts = rng.integers(0, L - blk + 1, size=(nb, nblk))
    idx = (starts[:, :, None] + np.arange(blk)[None, None, :]).reshape(nb, -1)[:, :L]
    mn = (1000 * np.cumprod(1 + r[idx], axis=1)).min(axis=1)
    return float((mn <= 500).mean()), float((mn <= 600).mean())


def bet_boot(pnls, nb=NBOOT, nbets=None):
    """Resample the dollar P&L sequence of bets (1 lot, so dollar P&L is a fair unit). P(<$100) and P(<=$500)."""
    p = np.asarray(pnls, float)
    if len(p) == 0:
        return np.nan, np.nan
    k = nbets or len(p)
    paths = 1000 + np.cumsum(rng.choice(p, size=(nb, k), replace=True), axis=1)
    mn = paths.min(axis=1)
    return float((mn < 100).mean()), float((mn <= 500).mean())


def ci_mean(x, nb=NBOOT):
    x = np.asarray(x, float)
    if len(x) == 0:
        return (np.nan, np.nan)
    m = x[rng.integers(0, len(x), size=(nb, len(x)))].mean(1)
    return (float(np.percentile(m, 5)), float(np.percentile(m, 95)))


def stats(bets):
    r = np.array([b['ret'] for b in bets]); p = np.array([b['pnl'] for b in bets])
    if len(r) == 0:
        return dict(n=0)
    w, l = r[r > 0], r[r <= 0]
    tot = p.sum(); top5 = np.sort(p)[::-1][:5].sum()
    return dict(n=len(r), hit=float((r > 0).mean()), mean=float(r.mean()), median=float(np.median(r)),
                p_ge100=float((r >= 1.0).mean()), p_le_m80=float((r <= -0.8).mean()),
                avg_win=float(w.mean()) if len(w) else np.nan, avg_loss=float(l.mean()) if len(l) else np.nan,
                pnl=float(tot), top5_share=float(top5 / tot) if tot > 0 else np.nan,
                total_ex_top5=float(tot - top5), tp_rate=float(np.mean([b['reason'] == 'tp' for b in bets])),
                avg_cost=float(np.mean([b['cost'] for b in bets])), max_ret=float(r.max()), min_ret=float(r.min()))


# ---------------------------------------------------------------- scenarios
SC = {
    'primary': Scen('primary'),
    'A_iv1.5_entry': Scen('A', iv_entry=1.5, iv_after=1.25),
    'B_iv1.5_all': Scen('B', iv_entry=1.5, iv_after=1.5),
    'C_iv1.0_all': Scen('C', iv_entry=1.0, iv_after=1.0),
    'D_spread_x2': Scen('D', hs_mult=2.0),
    'E_delay1': Scen('E', delay=1),
    'F_always_SPY': Scen('F', force_spy=True),
    'H_boilerplate_cost': Scen('H', boiler_cost=True),
}
RES = {}
for k, sc in SC.items():
    RES[k] = {w: engine(SIG[THR], sc, *WIN[w]) for w in ('IS', 'HO', 'full')}
RES['G_leave_2022_out'] = {w: engine(SIG[THR], SC['primary'], *WIN[w], skip_years=(2022,)) for w in ('IS', 'HO', 'full')}
for thr in (0.40, 0.60):
    RES[f'robust_thr{thr:.2f}'] = {w: engine(SIG[thr], SC['primary'], *WIN[w]) for w in ('IS', 'HO', 'full')}
print('scenarios done', round(time.time() - T0, 1), 's')

prim = RES['primary']
for b in prim['full']['bets']:
    print(b['date'], b['sym'], b['S0'], b['K1'], 'W', b['W'], 'cost', b['cost'], 'proc', b['proceeds'],
          'ret %+.2f' % b['ret'], b['reason'], b['days'])

# ---------------------------------------------------------------- control A: matched random days
raw_hits = np.where(breadth.values > THR)[0]
near = np.zeros(N, bool)
for n in raw_hits:
    near[max(0, n - LOCK):min(N, n + LOCK + 1)] = True
for n in SIG[THR]:
    near[max(0, n - LOCK):min(N, n + LOCK + 1)] = True


def ctrl_pool(w):
    a, b = WIN[w]
    ok = [n for n in range(a, b + 1) if not near[n] and np.isfinite(px['QQQ']['RV'][n]) and n + H <= N - 1]
    by = defaultdict(list)
    for n in ok:
        by[YEAR[n]].append(n)
    return by


def draw_set(by, quota):
    """Per-year quotas, non-overlapping (spacing >= H sessions) so every drawn day can be entered."""
    for _ in range(200):
        chosen = []
        okall = True
        for y, k in quota.items():
            pool = np.array(by.get(y, []))
            if k == 0:
                continue
            got = []
            for n in rng.permutation(pool):
                if all(abs(n - m) >= H for m in chosen + got):
                    got.append(int(n))
                    if len(got) == k:
                        break
            if len(got) < k:
                okall = False; break
            chosen += got
        if okall:
            return sorted(chosen)
    raise RuntimeError('cannot draw matched set')


def run_ctrlA(w, sc=SC['primary'], ref=None):
    ref = ref or prim[w]
    quota = Counter(YEAR[b['n']] for b in ref['bets'])
    by = ctrl_pool(w)
    means, terms, nb, u10 = [], [], [], []
    for k in range(NDRAW):
        ds = draw_set(by, quota)
        e = engine(ds, sc, *WIN[w])
        rs = [b['ret'] for b in e['bets']]
        means.append(np.mean(rs) if rs else np.nan); terms.append(e['terminal']); nb.append(len(rs))
        u10 += [px['QQQ']['C'][n + H] / px['QQQ']['C'][n] - 1 for n in ds]
    return dict(mean=np.array(means), term=np.array(terms), n=np.array(nb), u10=np.array(u10), quota=dict(quota),
                pool=sum(len(v) for v in by.values()))


def pctile(x, arr):
    arr = arr[np.isfinite(arr)]
    if not np.isfinite(x) or len(arr) == 0:
        return np.nan
    return float(100 * ((arr < x).mean() + 0.5 * (arr == x).mean()))


CA = {w: run_ctrlA(w) for w in ('IS', 'full') if prim[w]['bets']}
CA_stressA = run_ctrlA('IS', SC['A_iv1.5_entry'], RES['A_iv1.5_entry']['IS'])
print('control A done', round(time.time() - T0, 1), 's')


# ---------------------------------------------------------------- DIAGNOSTIC (not in the decision rule):
# signal level, every IS signal traded as 1 lot with W chosen at a fixed $80 budget (8% of $1,000), no governor,
# vs matched days with per-year quotas equal to the signal counts. Removes the equity path dependency.
def sig_level_rets(ns, sc=SC['primary'], budget=80.0):
    out = []
    for n in ns:
        tr = trade(n, sc)
        if tr is None:
            continue
        W = next((w for w in WIDTHS if tr['W'][w]['cost'] <= budget), None)
        if W is None:
            continue
        t = tr['W'][W]
        out.append((t['proceeds'] - t['cost']) / t['cost'])
    return np.array(out)


def sig_level_diag(sc, w='IS'):
    a, b = WIN[w]
    ns = [n for n in SIG[THR] if a <= n <= b]
    r = sig_level_rets(ns, sc)
    quota = Counter(YEAR[n] for n in ns)
    by = ctrl_pool(w)
    cm = np.array([sig_level_rets(draw_set(by, quota), sc).mean() for _ in range(NDRAW)])
    return dict(n=len(r), mean=float(r.mean()), median=float(np.median(r)), hit=float((r > 0).mean()),
                p_ge100=float((r >= 1).mean()), ctrl_mean=float(cm.mean()), ctrl_p95=float(np.percentile(cm, 95)),
                pct=pctile(float(r.mean()), cm), ci90=ci_mean(r), rets=[round(float(x), 3) for x in r])


SIGDIAG = {'primary': sig_level_diag(SC['primary']), 'A_iv1.5_entry': sig_level_diag(SC['A_iv1.5_entry'])}
print('signal-level diag', {k: (v['n'], round(v['mean'], 3), round(v['ctrl_mean'], 3), v['pct']) for k, v in SIGDIAG.items()})

# ---------------------------------------------------------------- control B: systematic every 3rd session
CB = {}
for w in ('IS', 'HO', 'full'):
    a, b = WIN[w]
    CB[w] = engine(list(range(a, b + 1, 3)), SC['primary'], a, b)

# ---------------------------------------------------------------- control C: SPY buy & hold
spyc = pd.Series(D['SPY']['Close'].reindex(cal).values, index=cal)


def spy_bh(a, b):
    c = 1000 * spyc.iloc[a:b + 1] / spyc.iloc[a]
    return float(c.iloc[-1]), max_dd(c)


SPYBH = {'IS': spy_bh(N_IS0, N_IS1), 'HO': spy_bh(N_IS1, N_HO1), 'full': spy_bh(N_IS0, N_HO1)}

# QQQ unconditional 10-day return (IS, all days)
qc = px['QQQ']['C']
uncond = {w: float(np.nanmean([qc[n + H] / qc[n] - 1 for n in range(WIN[w][0], min(WIN[w][1], N - 1 - H) + 1)]))
          for w in WIN}

# ---------------------------------------------------------------- assemble
SUM = {}
for name, r in RES.items():
    SUM[name] = {}
    for w in ('IS', 'HO', 'full'):
        e = r[w]
        st = stats(e['bets'])
        a, b = WIN[w]
        nsig = sum(1 for n in (SIG[float(name[-4:])] if name.startswith('robust') else SIG[THR]) if a <= n <= b)
        d = dict(signals=nsig, skips=dict(e['skips']), terminal=e['terminal'], max_dd=max_dd(e['curve']),
                 cagr=cagr(e['terminal'], w), **st)
        if name == 'primary':
            d['ci90_mean'] = ci_mean([x['ret'] for x in e['bets']])
            d['p_touch500'], d['p_touch600'] = block_boot(e['curve'])
            d['betboot_lt100'], d['betboot_le500'] = bet_boot([x['pnl'] for x in e['bets']])
            # longer horizon: 100 bets (~25 years at 4/yr) to stress the bet-sequence ruin estimate
            d['betboot100_lt100'], d['betboot100_le500'] = bet_boot([x['pnl'] for x in e['bets']], nbets=100)
            d['qqq10_events'] = float(np.mean([x['qqq10'] for x in e['bets']])) if e['bets'] else np.nan
            d['qqq10_uncond'] = uncond[w]
            if w in CA:
                c = CA[w]
                d['ctrlA'] = dict(mean_avg=float(np.nanmean(c['mean'])), mean_p95=float(np.nanpercentile(c['mean'], 95)),
                                  term_median=float(np.median(c['term'])), pct_mean=pctile(st['mean'], c['mean']),
                                  pct_term=pctile(e['terminal'], c['term']), qqq10=float(c['u10'].mean()),
                                  quota=c['quota'], pool=c['pool'], nbets_avg=float(c['n'].mean()))
            cb = stats(CB[w]['bets'])
            d['ctrlB'] = dict(n=cb.get('n', 0), mean=cb.get('mean', np.nan), terminal=CB[w]['terminal'],
                              max_dd=max_dd(CB[w]['curve']), hit=cb.get('hit', np.nan),
                              diff_mean=(st.get('mean', np.nan) - cb.get('mean', np.nan)))
            d['spy_bh'] = dict(terminal=SPYBH[w][0], max_dd=SPYBH[w][1])
        SUM[name][w] = d

sA = stats(RES['A_iv1.5_entry']['IS']['bets'])
SUM['A_iv1.5_entry']['IS']['ctrlA_under_A'] = dict(mean_avg=float(np.nanmean(CA_stressA['mean'])),
                                                    pct_mean=pctile(sA['mean'], CA_stressA['mean']))
P = SUM['primary']['IS']
DEC = {
    'IS_mean_ge_ctrlA_p95': bool(P['mean'] >= P['ctrlA']['mean_p95']),
    'IS_mean_gt0_stressA': bool(SUM['A_iv1.5_entry']['IS'].get('mean', -1) > 0),
    'IS_mean_gt0_stressG': bool(SUM['G_leave_2022_out']['IS'].get('mean', -1) > 0),
    'P_ruin_lt_5pct': bool(P['p_touch500'] < 0.05),
}
DEC['GO_live_paper'] = all(DEC.values())
DEC['HO'] = f'INCONCLUSIVE: {SUM["primary"]["HO"]["signals"]} HO signal(s) at the frozen 0.50 threshold (spec expected 0; 2025-11-17 breadth = 71/141 = 0.5035 under the written universe); live adoption needs >= 10 forward paper events'
print(json.dumps(DEC, indent=1))

# HO diagnostic @ 0.40
HO40 = RES['robust_thr0.40']['HO']['bets']


def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else round(float(o), 5)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


res = dict(summary=SUM, decision=DEC, signal_level_diag=SIGDIAG, bets_primary_full=prim['full']['bets'], ho_diag_thr040=HO40,
           bets_stressA_IS=RES['A_iv1.5_entry']['IS']['bets'], runtime_s=round(time.time() - T0, 1),
           seed=SEED, ndraw=NDRAW, signals_050=[str(cal[n].date()) for n in SIG[THR]])
(OUT / 'results.json').write_text(json.dumps(clean(res), indent=1))
pd.DataFrame(prim['full']['bets']).to_csv(OUT / 'bets.csv', index=False)
prim['full']['curve'].to_csv(OUT / 'equity.csv', header=['equity'])


# ---------------------------------------------------------------- results.md
def f(x, d=3, pct=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return 'n/a'
    return f'{100 * x:+.1f}%' if pct else f'{x:,.{d}f}'


L = ['# S3 / P3 backtest: breadth washout -> QQQ bull call vertical (FROZEN SPEC v1)', '',
     'Paper research only, no orders. Option prices are a **Black-Scholes approximation** '
     '(IV = clip(1.25 x RV20 of the traded ETF, 0.13, 1.00), r=4%, q=0, re-marked nightly; half-spread '
     'max($0.02, 0.5% of leg mid) per leg per side; $0.65/contract/leg/side); no historical option quotes. '
     'Breadth universe was chosen in 2026 (survivor bias). $1,000 per window; HO restarts at $1,000.', '',
     '## Primary run', '', '| metric | IS | HO | full |', '|---|---|---|---|']


def row(lbl, fn):
    vals = []
    for w in ('IS', 'HO', 'full'):
        try:
            vals.append(fn(SUM['primary'][w]))
        except Exception:
            vals.append('n/a')
    L.append(f'| {lbl} | ' + ' | '.join(vals) + ' |')


row('signals (post-lockout)', lambda s: str(s['signals']))
row('bets taken', lambda s: str(s.get('n', 0)))
row('skips', lambda s: ', '.join(f'{k}={v}' for k, v in s['skips'].items()) or 'none')
row('hit rate', lambda s: f(s['hit'], pct=True))
row('mean / median net ret per bet', lambda s: f'{f(s["mean"], pct=True)} / {f(s["median"], pct=True)}')
row('bootstrap 90% CI of mean', lambda s: f'[{f(s["ci90_mean"][0], pct=True)}, {f(s["ci90_mean"][1], pct=True)}]')
row('P(>= +100%) / P(<= -80%)', lambda s: f'{f(s["p_ge100"], 2)} / {f(s["p_le_m80"], 2)}')
row('avg win / avg loss', lambda s: f'{f(s["avg_win"], pct=True)} / {f(s["avg_loss"], pct=True)}')
row('take-profit exits', lambda s: f(s['tp_rate'], pct=True))
row('avg cost $', lambda s: f(s['avg_cost'], 2))
row('net P&L $', lambda s: f(s['pnl'], 2))
row('top-5 share of P&L (total ex-top5 $)', lambda s: f'{f(s["top5_share"], 2)} ({f(s["total_ex_top5"], 2)})')
row('terminal equity $ / CAGR', lambda s: f'{f(s["terminal"], 2)} / {f(s["cagr"], pct=True)}')
row('max drawdown', lambda s: f(s['max_dd'], pct=True))
row('P(touch $500) / P(touch $600), 21-session block bootstrap', lambda s: f'{f(s["p_touch500"], 4)} / {f(s["p_touch600"], 4)}')
row('bet-sequence bootstrap P(<$100): n bets / 100 bets, no governor or halt', lambda s: f'{f(s["betboot_lt100"], 4)} / {f(s["betboot100_lt100"], 4)}')
row('control A mean avg / p95', lambda s: f'{f(s["ctrlA"]["mean_avg"], pct=True)} / {f(s["ctrlA"]["mean_p95"], pct=True)}')
row('pctile vs control A: mean / terminal', lambda s: f'{f(s["ctrlA"]["pct_mean"], 1)} / {f(s["ctrlA"]["pct_term"], 1)}')
row('control A median terminal $', lambda s: f(s['ctrlA']['term_median'], 2))
row('control B (every 3rd session): n, mean, terminal', lambda s: f'{s["ctrlB"]["n"]}, {f(s["ctrlB"]["mean"], pct=True)}, {f(s["ctrlB"]["terminal"], 2)}')
row('strategy minus control B mean', lambda s: f(s['ctrlB']['diff_mean'], pct=True))
row('SPY B&H $1,000: terminal / max DD', lambda s: f'{f(s["spy_bh"]["terminal"], 2)} / {f(s["spy_bh"]["max_dd"], pct=True)}')
row('QQQ 10d ret: events / control A / all days', lambda s: f'{f(s["qqq10_events"], pct=True)} / {f(s["ctrlA"]["qqq10"], pct=True)} / {f(s["qqq10_uncond"], pct=True)}')

L += ['', '## Stress and robustness (IS unless noted)', '',
      '| run | bets | hit | mean | median | P(>=+100%) | net P&L $ | terminal $ | max DD | HO bets | HO mean |', '|---|---|---|---|---|---|---|---|---|---|---|']
for name in SUM:
    s, h = SUM[name]['IS'], SUM[name]['HO']
    L.append(f'| {name} | {s.get("n", 0)} | {f(s.get("hit"), pct=True)} | {f(s.get("mean"), pct=True)} | '
             f'{f(s.get("median"), pct=True)} | {f(s.get("p_ge100"), 2)} | {f(s.get("pnl"), 2)} | {f(s["terminal"], 2)} | '
             f'{f(s["max_dd"], pct=True)} | {h.get("n", 0)} | {f(h.get("mean"), pct=True)} |')
L += ['', f'Stress A vs its own matched control A: control mean {f(SUM["A_iv1.5_entry"]["IS"]["ctrlA_under_A"]["mean_avg"], pct=True)}, '
      f'strategy percentile {f(SUM["A_iv1.5_entry"]["IS"]["ctrlA_under_A"]["pct_mean"], 1)}.', '',
      '## Diagnostic (not part of the decision): signal level, fixed $80 budget, no governor', '',
      '| run | n | hit | mean | median | 90% CI | matched-day control mean / p95 | pctile |', '|---|---|---|---|---|---|---|---|']
for k, v in SIGDIAG.items():
    L.append(f'| {k} | {v["n"]} | {f(v["hit"], pct=True)} | {f(v["mean"], pct=True)} | {f(v["median"], pct=True)} | '
             f'[{f(v["ci90"][0], pct=True)}, {f(v["ci90"][1], pct=True)}] | {f(v["ctrl_mean"], pct=True)} / {f(v["ctrl_p95"], pct=True)} | {f(v["pct"], 1)} |')
L += ['', 'Per-signal returns (primary): ' + ', '.join(f'{100 * x:+.0f}%' for x in SIGDIAG['primary']['rets']), '',
      '## Frozen decision', '']
for k, v in DEC.items():
    L.append(f'- {k}: {v}')
L += ['', '## Bets (primary, full window)', '',
      '| signal | sym | S | K1 | W | cost $ | proceeds $ | ret | exit | days | breadth |', '|---|---|---|---|---|---|---|---|---|---|---|']
for b in prim['full']['bets']:
    L.append(f'| {b["date"]} | {b["sym"]} | {b["S0"]} | {b["K1"]} | {b["W"]} | {b["cost"]} | {b["proceeds"]} | '
             f'{100 * b["ret"]:+.0f}% | {b["reason"]} | {b["days"]} | {b["breadth"]} |')
L += ['', '## HO diagnostic at threshold 0.40 (non-decisive)', '',
      '| signal | sym | W | cost $ | proceeds $ | ret | exit | breadth |', '|---|---|---|---|---|---|---|---|']
for b in HO40:
    L.append(f'| {b["date"]} | {b["sym"]} | {b["W"]} | {b["cost"]} | {b["proceeds"]} | {100 * b["ret"]:+.0f}% | {b["reason"]} | {b["breadth"]} |')
L += ['', '## Notes', '',
      '- Entries at the decision close at the model ask/bid; exits at the model bid/ask at the close of the exit session. '
      'Exits are processed before entries on the same close.',
      '- Take-profit trigger uses the model mid of the spread; proceeds use the bid-side value. A spread whose bid-side value '
      'is <= 0 at the time exit is treated as abandoned (proceeds $0, no exit commission).',
      '- Expiry is calendar Friday nearest 30 DTE (holiday Fridays not shifted).',
      '- Control A: per-calendar-year quotas equal to the strategy\'s bets, days with no breadth>0.50 day within +/-10 '
      'sessions, non-overlapping, same structure/budget/exits; percentile = share of draws below (+half ties).',
      '- Stress H uses the generic task cost (half-spread max($0.05, 5% of leg price) per side), report only.',
      '- Equity is marked at model mid while a bet is open.',
      '- GOVERNOR LOCKOUT: after the first 6 bets (all 2022, all losers) equity is ~$806 < 0.85 x HWM, so the budget is '
      '4% (~$32) and even a $1-wide spread (~$50) does not fit; every later signal is skipped and equity can never recover '
      '(no bets, no gains). The primary IS result is therefore 6 bets, not ~19. The signal-level diagnostic removes this.',
      '- Spec discrepancies: (1) with the written universe (141 symbols) 2025-11-17 breadth is 71/141 = 0.5035 > 0.50, so HO has '
      '1 signal, not 0 (the spec claims exactly 0.50; including FFH/PSHD gives 71/143 = 0.4965). The written rule was followed. '
      '(2) In 2022 W=$1 was chosen (W=$2 cost > $80 at 2022 IV), not the expected W=$2.',
      '- Per-leg half-spread of 0.5% of leg mid makes deep-ITM exits costly (e.g. 2026-03-27 at thr 0.40: TP on mid 0.86 '
      'but bid-side proceeds 0.48/share). Under stress H costs the $1-wide spread costs more than its width, so 0 bets.',
      f'- Runtime {res["runtime_s"]} s; seed {SEED}; {NDRAW} control draw sets; {NBOOT} bootstrap paths.']
(OUT / 'results.md').write_text('\n'.join(L) + '\n')
print('done', round(time.time() - T0, 1), 's')
