"""Independent re-check of contagion S6 (leader |z|>3 -> within-group laggards minus leaders, next-day c2c).
PRE-REGISTRATION (reviewer): I recompute S6 exactly as defined (z = r / std of 60 prior sessions, min 40,
|z|>3 for leaders NVDA AAPL TSLA COIN BABA CRM; followers = rest of group; split by median of day-0
excess vs SPY; outcome = next-day c2c excess vs SPY, NOT demeaned (a spread needs no demeaning)).
Checks (fixed in advance, 5 variants): (a) S6 as defined; (b) same spread on ALL days (unconditional
within-group reversal) and on non-event days; (c) placebo: event dates shifted +20 sessions;
(d) placebo: event dates shuffled within calendar year (200 draws); (e) next-day open-to-close version
(removes close-price bounce). t-stats on date-level values. Disc <=2024-12-31, hold-out after.
"""
import sys, pathlib
import numpy as np, pandas as pd
sys.path.insert(0, '/home/user/ML-Trading-Platform-Backend')
import external_bars

SKIP = set('DBC EFA FFH GLD GURU GVIP IEF NANC PSHD QUAL ROKU RSP SEQ SEQUX SMH VTV SGOV TQQQ'.split())
ETF = {'SPY', 'QQQ', 'IWM', 'DIA'}
G = {'NVDA': 'AMD AVGO MU TSM ARM SMCI MRVL QCOM INTC LRCX AMAT KLAC ANET DELL',
     'AAPL': 'MSFT AMZN GOOGL META', 'TSLA': 'RIVN LCID NIO F GM', 'COIN': 'MSTR MARA RIOT HOOD',
     'BABA': 'JD BIDU PDD', 'CRM': 'ADBE SNOW DDOG NET MDB ZS CRWD PANW PLTR'}
need = set(G) | {x for v in G.values() for x in v.split()} | {'SPY'}
C, O = {}, {}
for s in sorted(need):
    d = external_bars.load_bars(s, 'etf' if s in ETF else 'equity')
    C[s], O[s] = d['Close'], d['Open']
C = pd.DataFrame(C).sort_index(); O = pd.DataFrame(O).sort_index()
r = np.log(C).diff()                       # log returns, independent choice
big = r.abs() > np.log(1.45)
print('dropped |move|>45% in this subset:', int(big.sum().sum()))
r = r.mask(big)
oc = np.log(C / O).mask(lambda x: x.abs() > np.log(1.45))
ex = r.sub(r['SPY'], axis=0); exo = oc.sub(oc['SPY'], axis=0)
nxt = ex.shift(-1); nxto = exo.shift(-1)
idx = r.index; DISC = pd.Timestamp('2024-12-31')

def spread_series(L, dates, fut):
    fol = G[L].split(); out = {}
    for d in dates:
        x0 = ex.loc[d, fol].dropna(); f = fut.loc[d, fol]
        if len(x0) < 2: continue
        med = x0.median()
        lo = x0[x0 <= med].index; hi = x0[x0 > med].index
        v = f[lo].mean() - f[hi].mean()          # losers minus winners (sign-free; equals their signed S6)
        if np.isfinite(v): out[d] = v
    return pd.Series(out, dtype=float)

def events(L):
    z = r[L] / r[L].rolling(60, min_periods=40).std().shift(1)
    return z[z.abs() > 3].index

def pool(dmap, fut):
    parts = [spread_series(L, ds, fut) for L, ds in dmap.items()]
    s = pd.concat(parts)
    return s.groupby(level=0).mean().sort_index()

def stat(s, lab):
    out = []
    for b, m in (('disc', s.index <= DISC), ('hold', s.index > DISC)):
        x = s[m]; n = len(x)
        t = x.mean() / (x.std(ddof=1) / np.sqrt(n)) if n > 2 else np.nan
        out.append(f'{b}: n={n} mean={100*x.mean():+.3f}% t={t:+.2f} hit={100*(x>0).mean():.0f}% med={100*x.median():+.3f}%')
    print(f'{lab:46s} | ' + ' | '.join(out))
    return s

ev = {L: events(L) for L in G}
print('event dates per leader:', {L: len(v) for L, v in ev.items()})
a = stat(pool(ev, nxt), '(a) S6 leader shock, next c2c')
idxs = set(a.index); print('  distinct dates', len(a))
ep, last = 0, -99
for d in a.index:
    i = idx.get_loc(d)
    if i - last >= 10: ep += 1; last = i
print('  episodes', ep)
# trimmed: drop top/bottom 5% dates
q = a.quantile([.05, .95]); stat(a[(a >= q.iloc[0]) & (a <= q.iloc[1])], '    (a) winsor-trim 5/95')
# (b) unconditional
alld = {L: idx[60:-1] for L in G}
u = stat(pool(alld, nxt), '(b) same spread, ALL days')
non = {L: idx[60:-1].difference(ev[L]) for L in G}
stat(pool(non, nxt), '(b) same spread, NON-event days')
# difference event vs all, per leader-date, using date-level: event minus unconditional mean in block
# (c) shift +20 sessions
sh = {L: idx[[min(idx.get_loc(d) + 20, len(idx) - 2) for d in v]] for L, v in ev.items()}
stat(pool(sh, nxt), '(c) placebo: events shifted +20 sessions')
# (d) shuffle within year
rng = np.random.default_rng(7); means = {'disc': [], 'hold': []}; ts = {'disc': [], 'hold': []}
yrs = pd.Series(idx.year, index=idx)
for k in range(200):
    dm = {}
    for L, v in ev.items():
        pick = []
        for d in v:
            cand = idx[(yrs == d.year).values][60 if d.year == idx[0].year else 0:]
            cand = cand[cand < idx[-1]]
            pick.append(cand[rng.integers(len(cand))])
        dm[L] = pd.DatetimeIndex(sorted(set(pick)))
    s = pool(dm, nxt)
    for b, m in (('disc', s.index <= DISC), ('hold', s.index > DISC)):
        x = s[m]; means[b].append(x.mean()); ts[b].append(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))
for b in means:
    print(f'(d) shuffle-in-year {b}: mean of placebo means={100*np.mean(means[b]):+.3f}%  '
          f'5-95% [{100*np.percentile(means[b],5):+.3f},{100*np.percentile(means[b],95):+.3f}]  '
          f'share t>=1.56/2.55: {np.mean(np.array(ts[b]) >= (1.56 if b=="disc" else 2.55)):.2f}')
# (e) open-to-close next day
stat(pool(ev, nxto), '(e) S6 leader shock, next o2c (no bounce)')
stat(pool(alld, nxto), '(e) same o2c spread, ALL days')
# 3-day: sum of next 3 days excess
n3 = ex.shift(-1) + ex.shift(-2) + ex.shift(-3)
stat(pool(ev, n3), '(f) S7 leader shock, next 3d c2c')
stat(pool(alld, n3), '(f) same 3d spread, ALL days')
# per-leader breakdown of S6
for L in G:
    stat(spread_series(L, ev[L], nxt), f'    by leader {L}')
# implementable: excess of event-day spread over all-days spread
