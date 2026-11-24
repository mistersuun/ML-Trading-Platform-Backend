"""
Independent review of team breadth, strongest effect S5 LOW30 (>=30% of stocks close at/below prior 20d low close).
PRE-REGISTRATION (reviewer): re-test exactly their S5 threshold (0.30), targets SPY & EW, h=5 (and 1,3,10 reported),
same discovery/hold-out split. Inference: (a) pooled event dates, Newey-West HAC t with lag h (cluster by date +
overlap), (b) their episode weighting, (c) a CAUSAL down-weighting (weight = 1 / #event dates in the trailing 10
sessions incl. today) that uses no future information, (d) one bet per non-overlapping window (first eligible date,
then skip h sessions). Placebos: signal shifted +20 sessions; 500 random circular shifts within each sample.
Variants tried by reviewer: 4 weighting schemes x 2 targets x 2 samples at h=5 + placebos. No thresholds changed.
"""
import sys, pathlib, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/ML-Trading-Platform-Backend')
import external_bars

SKIP = set('DBC EFA FFH GLD GURU GVIP IEF NANC PSHD QUAL ROKU RSP SEQ SEQUX SMH VTV SGOV TQQQ'.split())
ETF = {'SPY', 'QQQ', 'IWM', 'DIA', 'TQQQ', 'SGOV'}
syms = sorted({p.name.split('.')[0] for p in pathlib.Path('/home/user/ML-Trading-Platform-Backend/state/external_bars').glob('*.json')} - SKIP)
close = {}
for s in syms:
    d = external_bars.load_bars(s, 'etf' if s in ETF else 'equity')
    d = d[~d.index.duplicated(keep='last')].sort_index()
    close[s] = d['Close'].astype(float)
P = pd.DataFrame(close)
cal = P['SPY'].dropna().index
P = P.reindex(cal)
r = P / P.shift(1) - 1
bad = r.abs() > 0.45
print('dropped |1d|>45% moves:', int(bad.sum().sum()))
r = r.where(~bad)
stk = [c for c in P.columns if c not in ETF]

# low20 share, using a split-safe price path: rebuild price from cleaned returns per stock
lp = np.log1p(r[stk]).cumsum()               # log price index from cleaned returns (NaN days break nothing: cumsum skips)
lp = lp.where(r[stk].notna())
prior_min = lp.shift(1).rolling(20, min_periods=20).min()
ok = lp.notna() & prior_min.notna()
low20 = ((lp <= prior_min + 1e-12) & ok).sum(1) / ok.sum(1)
sig = (low20 >= 0.30)

def fwd(x, h):
    l = np.log1p(x)
    return np.expm1(sum(l.shift(-k) for k in range(1, h + 1)))
T = {}
for h in (1, 3, 5, 10):
    T[('SPY', h)] = fwd(r['SPY'], h)
    T[('EW', h)] = fwd(r[stk], h).mean(1)   # EW of compounded stock returns; stocks with NaN drop

disc = cal < '2025-01-01'; hold = ~disc
PER = {'disc': pd.Series(disc, cal), 'hold': pd.Series(hold, cal)}

def nw_t(x, lag):
    x = np.asarray(x) - 0; n = len(x); m = x.mean(); e = x - m
    g0 = (e @ e) / n
    s = g0 + 2 * sum((1 - k / (lag + 1)) * (e[k:] @ e[:-k]) / n for k in range(1, min(lag, n - 1) + 1))
    return m / np.sqrt(s / n)

def episodes(dates):
    pos = {d: i for i, d in enumerate(cal)}; eps = []; last = None
    for d in dates:
        i = pos[d]
        if last is None or i - last >= 10: eps.append([d])
        else: eps[-1].append(d)
        last = i
    return eps

def evaluate(mask, tg, h, per, quiet=False):
    p = PER[per]; f = T[(tg, h)]
    base = f[p].dropna(); mu = base.mean()
    ev = f[mask & p].dropna(); x = ev - mu
    out = {}
    # (a) pooled dates; HAC on the full daily series of (signal*excess) is equivalent-ish; use event-date regression
    # on full sample: y_t = a + b*sig_t, NW SE with lag h
    y = f[p].dropna(); s = mask.reindex(y.index).astype(float)
    X = np.column_stack([np.ones(len(y)), s.values]); beta = np.linalg.lstsq(X, y.values, rcond=None)[0]
    u = y.values - X @ beta; n = len(y)
    Xu = X * u[:, None]; S = Xu.T @ Xu
    for k in range(1, h + 1):
        w = 1 - k / (h + 1); G = Xu[k:].T @ Xu[:-k]; S += w * (G + G.T)
    XtXi = np.linalg.inv(X.T @ X); V = XtXi @ S @ XtXi
    out['pooled_ex'] = x.mean() * 100
    out['pooled_beta_t'] = beta[1] / np.sqrt(V[1, 1])   # cond minus non-event mean, HAC
    out['n_dates'] = len(ev)
    eps = episodes(list(ev.index)); out['n_ep'] = len(eps)
    epv = np.array([x[e].mean() for e in eps])
    out['ep_ex'] = epv.mean() * 100
    out['ep_t'] = epv.mean() / (epv.std(ddof=1) / np.sqrt(len(epv))) if len(epv) > 2 else np.nan
    # (c) causal weights
    cnt = mask.astype(int).rolling(10, min_periods=1).sum()
    w = 1 / cnt.reindex(ev.index)
    out['causal_ex'] = (w * x).sum() / w.sum() * 100
    # (d) non-overlapping bets
    pos = {d: i for i, d in enumerate(cal)}; nxt = -1; xs = []
    for d in ev.index:
        if pos[d] >= nxt: xs.append(x[d]); nxt = pos[d] + h
    xs = np.array(xs)
    out['nonov_n'] = len(xs); out['nonov_ex'] = xs.mean() * 100
    out['nonov_t'] = xs.mean() / (xs.std(ddof=1) / np.sqrt(len(xs)))
    out['hit'] = (ev > 0).mean() * 100; out['uhit'] = (base > 0).mean() * 100
    return out

print('\nS5 LOW30 dates disc/hold:', int((sig & PER['disc']).sum()), int((sig & PER['hold']).sum()))
for tg in ('SPY', 'EW'):
    for h in (1, 3, 5, 10):
        for per in ('disc', 'hold'):
            o = evaluate(sig, tg, h, per)
            print(f"{tg} h={h:2d} {per}: dates {o['n_dates']:3d} ep {o['n_ep']:2d} | pooled {o['pooled_ex']:+.2f}% HAC t {o['pooled_beta_t']:+.2f} | "
                  f"episode-wt {o['ep_ex']:+.2f}% t {o['ep_t']:+.2f} | causal-wt {o['causal_ex']:+.2f}% | non-overlap n={o['nonov_n']} {o['nonov_ex']:+.2f}% t {o['nonov_t']:+.2f} | hit {o['hit']:.0f} vs {o['uhit']:.0f}")

# --- look-ahead in episode weighting: single-date episodes are defined by NO event in the next 9 sessions
print('\nLook-ahead check (SPY h=5): excess by episode length')
for per in ('disc', 'hold'):
    f = T[('SPY', 5)]; mu = f[PER[per]].dropna().mean()
    ev = f[sig & PER[per]].dropna()
    eps = episodes(list(ev.index))
    single = [e[0] for e in eps if len(e) == 1]; multi = [d for e in eps if len(e) > 1 for d in e]
    last = [e[-1] for e in eps]; notlast = [d for e in eps for d in e[:-1]]
    print(f"  {per}: single-date eps n={len(single)} ex {(f[single]-mu).mean()*100:+.2f}% | dates in multi-date eps n={len(multi)} ex {(f[multi]-mu).mean()*100:+.2f}%"
          f" | LAST date of each ep {(f[last]-mu).mean()*100:+.2f}% vs other dates {(f[notlast]-mu).mean()*100:+.2f}%")

# --- placebos
print('\nPlacebo 1: signal shifted +20 sessions (SPY/EW h=5)')
sh = sig.shift(20, fill_value=False)
for tg in ('SPY', 'EW'):
    for per in ('disc', 'hold'):
        o = evaluate(sh, tg, 5, per)
        print(f"  {tg} {per}: pooled {o['pooled_ex']:+.2f}% (HAC t {o['pooled_beta_t']:+.2f}) episode-wt {o['ep_ex']:+.2f}% t {o['ep_t']:+.2f} ep {o['n_ep']}")

print('\nPlacebo 2: 500 random circular shifts of the signal within each sample (preserves clustering); SPY h=5')
rng = np.random.default_rng(0)
for tg in ('SPY', 'EW'):
    for per in ('disc', 'hold'):
        p = PER[per]; idx = np.where(p.values)[0]; sv = sig.values[idx]
        real = evaluate(sig, tg, 5, per)
        epx, plx, ept = [], [], []
        for _ in range(500):
            k = rng.integers(20, len(idx) - 20)
            m = np.zeros(len(cal), bool); m[idx] = np.roll(sv, k)
            o = evaluate(pd.Series(m, cal), tg, 5, per)
            epx.append(o['ep_ex']); plx.append(o['pooled_ex']); ept.append(o['ep_t'])
        epx, plx, ept = map(np.array, (epx, plx, ept))
        print(f"  {tg} {per}: real episode-wt {real['ep_ex']:+.2f} (t {real['ep_t']:+.2f}); placebo episode-wt mean {epx.mean():+.2f}, "
              f"share>=real {np.mean(epx>=real['ep_ex']):.2f}, share placebo t>2 {np.mean(ept>2):.2f} | real pooled {real['pooled_ex']:+.2f}; "
              f"placebo pooled mean {plx.mean():+.2f}, share>=real {np.mean(plx>=real['pooled_ex']):.2f}")

print('\nGauge last 4 sessions:', {str(d.date()): round(float(low20[d]), 2) for d in cal[-4:]})
