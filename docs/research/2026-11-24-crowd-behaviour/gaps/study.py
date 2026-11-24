"""
TEAM gaps -- Opening gaps: fade or follow?

PRE-REGISTRATION (written before any outcome was computed)
-----------------------------------------------------------
Definitions (day t):
  gap_t   = Open_t / Close_{t-1} - 1
  oc_t    = Close_t / Open_t - 1              (same-day reaction after the open)
  fwdK_t  = Close_{t+K} / Close_t - 1, K = 1, 3, 5
  sig_t   = std of close-to-close returns over t-20..t-1 (known before the open of t)
  z_t     = gap_t / sig_t
  prior_t = Close_{t-1}/Close_{t-2} - 1 , zprior = prior_t / sig_t
Excess return = raw return minus the SAME symbol's unconditional mean of that
  horizon inside the SAME sample period (discovery or hold-out). This removes
  the survivorship drift of 2026-popular names. Unconditional hit rate is
  P(raw return > 0) over all days of that symbol-sample.
Splits/bad prints: any |close-to-close| > 45% day is flagged; that day's gap/oc
  and any forward window that spans it are dropped.
Universes: SPY, QQQ (each alone) and STOCKS = all equities minus the skip list
  (ETFs IWM, DIA not used as stocks). Short-history names kept (sigma needs 20
  prior days; everything is per-symbol so short series are tolerated).
Inference: for stocks, average excess across symbols on each event date, then
  t = mean / (sd/sqrt(n_dates)) over dates (cluster by date). Episodes =
  event dates at least 10 sessions apart (greedy from the first date).
Samples: discovery 2021-11-05..2024-12-31, hold-out 2025-01-01..2026-11-23.
Success: same sign in hold-out, hold-out |mean| >= half of discovery |mean|,
  hold-out |t| >= 2, >= 20 episodes in each sample, and |mean| >= 0.15% (stocks
  round trip cost). Hit-rate bar for next-day option bets: >= 58%.

Pre-registered signal grid (ALL are printed, counted as variants):
  Gap buckets: z>+1, z>+2, z>+3, z<-1, z<-2, z<-3, gap>+2%, >+4%, >+8%,
               gap<-2%, <-4%, <-8%                                   (12)
  Q1: outcome = oc (same-day)                    -> 12 buckets x 3 universes
  Q2: outcome = fwd1, fwd3, fwd5, split by oc>0 / oc<0 on the gap day
                                                 -> 12 x 3 x 2 x 3 universes
  Q3: snap-back: gap z>+1 with zprior<-2 (up snap-back), gap z<-1 with
      zprior>+2 (down snap-back), vs. same-direction gap without the snap
      (zprior in the same direction or |zprior|<2); outcomes oc and fwd1
                                                 -> 4 x 2 x 3 universes
  Total variants = 36 + 216 + 24 = 276.
  Headline table (14 rows) fixed in advance: STOCKS z>+2 oc, z<-2 oc,
  >+4% oc, <-4% oc; STOCKS z>+2 & oc<0 fwd1, z>+2 & oc>0 fwd1, z<-2 & oc<0 fwd1,
  z<-2 & oc>0 fwd1; STOCKS snap-up oc, snap-down oc; SPY z>+1 oc, SPY z<-1 oc,
  QQQ z>+1 oc, QQQ z<-1 oc.
"""
import sys, pathlib, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/ML-Trading-Platform-Backend')
import external_bars

SKIP = set('DBC EFA FFH GLD GURU GVIP IEF NANC PSHD QUAL ROKU RSP SEQ SEQUX SMH VTV SGOV TQQQ'.split())
ETF = {'SPY', 'QQQ', 'IWM', 'DIA', 'TQQQ', 'SGOV'}
DISC_END = pd.Timestamp('2024-12-31')
syms = sorted({p.name.split('.')[0] for p in pathlib.Path('/home/user/ML-Trading-Platform-Backend/state/external_bars').glob('*.json')})
syms = [s for s in syms if s not in SKIP]

n_split = 0
split_list = []
frames = {}
for s in syms:
    df = external_bars.load_bars(s, 'etf' if s in ETF else 'equity')
    df = df[~df.index.duplicated()].sort_index()
    df = df[(df.Open > 0) & (df.Close > 0)]
    c, o = df.Close, df.Open
    cc = c / c.shift(1) - 1
    bad = cc.abs() > 0.45
    n_split += int(bad.sum())
    split_list += [(s, str(d.date()), round(float(cc[d]) * 100, 1)) for d in cc.index[bad]]
    ccc = cc.where(~bad)
    out = pd.DataFrame(index=df.index)
    out['gap'] = (o / c.shift(1) - 1).where(~bad)
    out['oc'] = (c / o - 1)
    out['sig'] = ccc.shift(1).rolling(20, min_periods=15).std()
    out['z'] = out.gap / out.sig
    out['zprior'] = ccc.shift(1) / out.sig
    badc = bad.astype(int)
    for K in (1, 3, 5):
        f = c.shift(-K) / c - 1
        spans_bad = badc[::-1].rolling(K, min_periods=1).sum()[::-1].shift(-1).fillna(0) > 0
        out[f'fwd{K}'] = f.where(~spans_bad)
    out['sym'] = s
    out['disc'] = out.index <= DISC_END
    frames[s] = out

panel = pd.concat(frames.values())
panel.index.name = 'date'
panel = panel.reset_index()
panel = panel.dropna(subset=['z'])
# excess over own unconditional mean per symbol x sample
for col in ('oc', 'fwd1', 'fwd3', 'fwd5'):
    panel[col + '_x'] = panel[col] - panel.groupby(['sym', 'disc'])[col].transform('mean')

STOCKS = [s for s in syms if s not in ETF]
UNIV = {'STOCKS': panel[panel.sym.isin(STOCKS)], 'SPY': panel[panel.sym == 'SPY'], 'QQQ': panel[panel.sym == 'QQQ']}

BUCKETS = {
    'z>+1': lambda d: d.z > 1, 'z>+2': lambda d: d.z > 2, 'z>+3': lambda d: d.z > 3,
    'z<-1': lambda d: d.z < -1, 'z<-2': lambda d: d.z < -2, 'z<-3': lambda d: d.z < -3,
    'gap>+2%': lambda d: d.gap > .02, 'gap>+4%': lambda d: d.gap > .04, 'gap>+8%': lambda d: d.gap > .08,
    'gap<-2%': lambda d: d.gap < -.02, 'gap<-4%': lambda d: d.gap < -.04, 'gap<-8%': lambda d: d.gap < -.08,
}
SNAP = {
    'snapUP(z>+1,zprior<-2)': lambda d: (d.z > 1) & (d.zprior < -2),
    'gapUP_noSnap(z>+1,zprior>=-2)': lambda d: (d.z > 1) & (d.zprior >= -2),
    'snapDN(z<-1,zprior>+2)': lambda d: (d.z < -1) & (d.zprior > 2),
    'gapDN_noSnap(z<-1,zprior<=+2)': lambda d: (d.z < -1) & (d.zprior <= 2),
}


def episodes(dates):
    dates = sorted(set(dates))
    if not dates:
        return 0
    allx = sorted(panel.date.unique())
    pos = {d: i for i, d in enumerate(allx)}
    n, last = 0, -10**9
    for d in dates:
        if pos[d] - last >= 10:
            n += 1
            last = pos[d]
    return n


def stats(d, col):
    d = d.dropna(subset=[col])
    if len(d) == 0:
        return dict(n_obs=0, n_dates=0, n_ep=0, mean=np.nan, t=np.nan, hit=np.nan)
    by = d.groupby('date')[col + '_x'].mean()
    n = len(by)
    m = by.mean()
    t = m / (by.std(ddof=1) / np.sqrt(n)) if n > 2 and by.std() > 0 else np.nan
    return dict(n_obs=len(d), n_dates=n, n_ep=episodes(by.index), mean=m * 100, t=t,
                hit=(d[col] > 0).mean() * 100)


def uncond_hit(u, col, disc):
    x = u[u.disc == disc][col].dropna()
    return (x > 0).mean() * 100


rows = []
nvar = 0


def run(uname, label, mask_fn, col, extra=None):
    global nvar
    nvar += 1
    u = UNIV[uname]
    sel = u[mask_fn(u)]
    if extra == 'up':
        sel = sel[sel.oc > 0]
    elif extra == 'dn':
        sel = sel[sel.oc < 0]
    r = {'univ': uname, 'signal': label + ({'up': ' & closeAboveOpen', 'dn': ' & closeBelowOpen'}.get(extra, '')), 'h': col}
    for tag, disc in (('D', True), ('H', False)):
        s = stats(sel[sel.disc == disc], col)
        for k, v in s.items():
            r[tag + '_' + k] = v
        r[tag + '_uhit'] = uncond_hit(u, col, disc)
    mD, mH, tH = r['D_mean'], r['H_mean'], r['H_t']
    r['holds'] = bool(np.isfinite(mD) and np.isfinite(mH) and np.sign(mD) == np.sign(mH)
                      and abs(mH) >= 0.5 * abs(mD) and np.isfinite(tH) and abs(tH) >= 2
                      and r['D_n_ep'] >= 20 and r['H_n_ep'] >= 20
                      and min(abs(mD), abs(mH)) >= 0.15)
    rows.append(r)
    return r


for un in UNIV:
    for b, fn in BUCKETS.items():
        run(un, b, fn, 'oc')
for un in UNIV:
    for b, fn in BUCKETS.items():
        for ex in ('up', 'dn'):
            for col in ('fwd1', 'fwd3', 'fwd5'):
                run(un, b, fn, col, ex)
for un in UNIV:
    for b, fn in SNAP.items():
        for col in ('oc', 'fwd1'):
            run(un, b, fn, col)

R = pd.DataFrame(rows)
pd.set_option('display.width', 250, 'display.max_rows', 400, 'display.max_columns', 30)
fmt = lambda x: f'{x:7.2f}' if isinstance(x, float) else str(x)

print('=' * 100)
print('TEAM gaps: opening gaps, fade or follow')
print(f'symbols used: {len(syms)} (stocks {len(STOCKS)}); split-like |cc|>45% days dropped: {n_split}')
for x in split_list:
    print('   dropped', x)
print(f'panel rows with valid sigma: {len(panel)}; variants tested: {nvar}')
print('excess = return minus same symbol unconditional mean in same sample; t clustered by date')
print('D = discovery 2021-11..2024-12, H = hold-out 2025-01..2026-11-23')
print()

cols = ['univ', 'signal', 'h', 'D_n_dates', 'D_n_ep', 'D_mean', 'D_t', 'D_hit', 'D_uhit',
        'H_n_dates', 'H_n_ep', 'H_mean', 'H_t', 'H_hit', 'H_uhit', 'holds']
print('FULL GRID (mean in % excess, hit = % raw>0, uhit = unconditional % raw>0)')
print(R[cols].to_string(index=False, float_format=lambda v: f'{v:.2f}'))

HEAD = [('STOCKS', 'z>+2', 'oc'), ('STOCKS', 'z<-2', 'oc'), ('STOCKS', 'gap>+4%', 'oc'), ('STOCKS', 'gap<-4%', 'oc'),
        ('STOCKS', 'z>+2 & closeBelowOpen', 'fwd1'), ('STOCKS', 'z>+2 & closeAboveOpen', 'fwd1'),
        ('STOCKS', 'z<-2 & closeBelowOpen', 'fwd1'), ('STOCKS', 'z<-2 & closeAboveOpen', 'fwd1'),
        ('STOCKS', 'snapUP(z>+1,zprior<-2)', 'oc'), ('STOCKS', 'snapDN(z<-1,zprior>+2)', 'oc'),
        ('SPY', 'z>+1', 'oc'), ('SPY', 'z<-1', 'oc'), ('QQQ', 'z>+1', 'oc'), ('QQQ', 'z<-1', 'oc')]
print()
print('HEADLINE (pre-registered 14 rows)')
H = pd.concat([R[(R.univ == a) & (R.signal == b) & (R.h == c)] for a, b, c in HEAD])
print(H[cols].to_string(index=False, float_format=lambda v: f'{v:.2f}'))

print()
print('ROWS PASSING ALL HOLD-OUT CRITERIA:')
P = R[R.holds]
print(P[cols].to_string(index=False, float_format=lambda v: f'{v:.2f}') if len(P) else '  none')

# yesterday context
print()
print('CONTEXT 2026-11-23:')
for s in ('SPY', 'QQQ', 'NVDA'):
    x = panel[(panel.sym == s) & (panel.date == pd.Timestamp('2026-11-23'))]
    if len(x):
        x = x.iloc[0]
        print(f'  {s}: gap {x.gap*100:.2f}%  z {x.z:.2f}  zprior {x.zprior:.2f}  open->close {x.oc*100:.2f}%')
