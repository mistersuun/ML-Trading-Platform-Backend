"""
TEAM breadth -- market-wide fear: breadth washouts and relief rallies.

PRE-REGISTRATION (written before any outcome was computed; not changed afterwards)
------------------------------------------------------------------------------
Gauges (computed each session over the stock universe, ETFs excluded from breadth):
  dn2  = share of stocks with close-to-close return <= -2%
  up2  = share of stocks with close-to-close return >= +2%
  bot  = share of stocks closing in bottom 20% of the day's high-low range
  low20= share of stocks closing at/below their prior 20-session lowest close
  spy5 = SPY 5-session return
  vr   = SPY realized vol 5d / 60d (std of daily returns)
Signals (event day t; outcomes measured close(t) -> close(t+h)):
  S1 W60       dn2 >= 0.60
  S2 W40       dn2 >= 0.40
  S3 DBL40     dn2 >= 0.40 today and on another day within the prior 2 sessions (2 washouts in 3)
  S4 BOT70     bot >= 0.70
  S5 LOW30     low20 >= 0.30
  S6 SPY5_4    spy5 <= -4%
  S7 VR2       vr >= 2.0
  S8 R60       up2 >= 0.60   (relief day, unconditional)
  S9 RAW       relief after washout: up2 >= 0.40 today AND some day in the prior 5 sessions had dn2 >= 0.40
                (the 2026-11-20 washout / 2026-11-23 relief pattern)
Targets: SPY, QQQ, equal-weight universe (EW); horizons 1,3,5,10 sessions.
Excess = conditional mean forward return minus the same target's unconditional mean forward return
         over all days of the same sample (discovery or hold-out).
Primary test (decided in advance): EW excess at h=5 for S1-S7 (bounce, sign +) and for S8-S9 ("holds" = sign +).
t-statistics: one observation per episode (event dates grouped when < 10 sessions apart; episode value =
mean over its event dates), Newey-free because episodes are >=10 sessions apart and h<=10.
Discovery 2021-11-05..2024-12-31, hold-out 2025-01-01..2026-11-23. Holds = same sign in hold-out, hold-out
excess at least 1/3 of discovery size, and >= 20 total episodes to be called non-weak.
Variant count: 9 signals x 3 targets x 4 horizons = 108 cells; no other thresholds were tried.
"""
import sys, pathlib, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/ML-Trading-Platform-Backend')
import external_bars

SKIP = set('DBC EFA FFH GLD GURU GVIP IEF NANC PSHD QUAL ROKU RSP SEQ SEQUX SMH VTV SGOV TQQQ'.split())
ETF = {'SPY', 'QQQ', 'IWM', 'DIA', 'TQQQ', 'SGOV'}
syms = sorted({p.name.split('.')[0] for p in pathlib.Path('/home/user/ML-Trading-Platform-Backend/state/external_bars').glob('*.json')})
syms = [s for s in syms if s not in SKIP]

C, H, L = {}, {}, {}
for s in syms:
    try:
        d = external_bars.load_bars(s, 'etf' if s in ETF else 'equity')
    except Exception as e:
        print('load fail', s, e); continue
    d = d[~d.index.duplicated()].sort_index()
    C[s], H[s], L[s] = d['Close'], d['High'], d['Low']
C = pd.DataFrame(C); H = pd.DataFrame(H); L = pd.DataFrame(L)
cal = C['SPY'].dropna().index
C, H, L = C.reindex(cal), H.reindex(cal), L.reindex(cal)

ret = C.pct_change()
split = ret.abs() > 0.45
nsplit = int(split.sum().sum())
print(f'Split-like moves (|1d|>45%) dropped: {nsplit} -> ' +
      ', '.join(f'{s}:{d.date()}' for d, s in zip(*np.where(split.values)) for d, s in [(cal[d], C.columns[s])]))
# null the return on split days; also break forward windows that cross them
ret = ret.mask(split)

stocks = [s for s in C.columns if s not in ETF]
print(f'Universe: {len(stocks)} stocks for breadth; sessions {len(cal)} {cal[0].date()}..{cal[-1].date()}')

R = ret[stocks]
valid = R.notna()
n = valid.sum(1).replace(0, np.nan)
dn2 = (R <= -0.02).sum(1) / n
up2 = (R >= 0.02).sum(1) / n
rng = (H[stocks] - L[stocks])
pos = (C[stocks] - L[stocks]) / rng.where(rng > 0)
bot = (pos <= 0.2).where(pos.notna() & valid).sum(1) / n
prevlow = C[stocks].shift(1).rolling(20, min_periods=20).min()
low20 = ((C[stocks] <= prevlow) & prevlow.notna() & valid).sum(1) / (prevlow.notna() & valid).sum(1)
spy = C['SPY']
spy5 = spy / spy.shift(5) - 1
sr = spy.pct_change()
vr = sr.rolling(5).std() / sr.rolling(60).std()

# forward returns (log-sum of daily simple returns -> compounded); EW = mean of stocks' compounded fwd ret
HZ = [1, 3, 5, 10]
def fwd(series_ret, h):
    lr = np.log1p(series_ret)
    f = sum(lr.shift(-k) for k in range(1, h + 1))
    return np.expm1(f)
FW = {}
for h in HZ:
    FW[('SPY', h)] = fwd(ret['SPY'], h)
    FW[('QQQ', h)] = fwd(ret['QQQ'], h)
    FW[('EW', h)] = fwd(R, h).mean(1)  # stocks with a NaN (split) inside window drop out

d = lambda x: (dn2.rolling(x).max())
w40 = dn2 >= 0.40
sig = {
    'S1 W60 (>=60% down2)': dn2 >= 0.60,
    'S2 W40 (>=40% down2)': w40,
    'S3 DBL40 (2 W40 in 3d)': w40 & (w40.shift(1, fill_value=False) | w40.shift(2, fill_value=False)),
    'S4 BOT70 (>=70% bottom-range)': bot >= 0.70,
    'S5 LOW30 (>=30% at 20d low)': low20 >= 0.30,
    'S6 SPY5 <= -4%': spy5 <= -0.04,
    'S7 VR2 (vol5/vol60>=2)': vr >= 2.0,
    'S8 R60 (>=60% up2)': up2 >= 0.60,
    'S9 RAW (up2>=40% <=5d after W40)': (up2 >= 0.40) & pd.concat([w40.shift(k, fill_value=False) for k in range(1, 6)], axis=1).any(axis=1),
}
SPLIT_DATE = pd.Timestamp('2025-01-01')
periods = {'disc': cal < SPLIT_DATE, 'hold': cal >= SPLIT_DATE}

def episodes(dates):
    eps, last = [], None
    idx = {dt: i for i, dt in enumerate(cal)}
    for dt in dates:
        i = idx[dt]
        if last is None or i - last >= 10:
            eps.append([dt])
        else:
            eps[-1].append(dt)
        last = i
    return eps

def stats(mask, target, h, per):
    m = mask & periods[per]
    fr = FW[(target, h)]
    base = fr[periods[per]].dropna()
    ev = fr[m].dropna()
    if len(ev) == 0:
        return dict(n=0, ne=0, ex=np.nan, pex=np.nan, t=np.nan, hit=np.nan, uhit=(base > 0).mean() * 100)
    eps = episodes(list(ev.index))
    epv = np.array([ev[e].mean() for e in eps]) - base.mean()
    t = epv.mean() / (epv.std(ddof=1) / np.sqrt(len(epv))) if len(epv) > 2 else np.nan
    # ex = episode-weighted excess (consistent with t); pex = pooled-date excess, for reference
    return dict(n=len(ev), ne=len(eps), ex=epv.mean() * 100, pex=(ev.mean() - base.mean()) * 100, t=t,
                hit=(ev > 0).mean() * 100, uhit=(base > 0).mean() * 100)

print('\nEvent counts (all dates / episodes, full sample):')
for k, m in sig.items():
    dts = list(m[m].index)
    print(f'  {k:36s} dates={len(dts):3d} episodes={len(episodes(dts)):3d}  last={dts[-1].date() if dts else None}')

print('\nToday-context gauges:')
for dt in cal[-5:]:
    print(f'  {dt.date()} dn2={dn2[dt]:.2f} up2={up2[dt]:.2f} bot={bot[dt]:.2f} low20={low20[dt]:.2f} '
          f'spy5={spy5[dt]*100:+.1f}% vr={vr[dt]:.2f} SPY1d={sr[dt]*100:+.2f}%')

print('\nFULL GRID (108 cells). ex = episode-weighted excess over same-sample unconditional mean, % ; t on episode means')
print('(note added after first run: the first run printed pooled-date excess next to an episode-level t; switched ex to the')
print(' episode-weighted mean so sign and t agree. Thresholds/signals unchanged.)')
rows = []
hdr = f"{'signal':36s} {'tgt':3s} h | {'D n/ep':>8s} {'D ex%':>6s} {'D t':>5s} {'D hit':>5s} | {'H n/ep':>8s} {'H ex%':>6s} {'H t':>5s} {'H hit':>5s} {'uhitH':>5s} | holds"
print(hdr)
for k, m in sig.items():
    for tg in ['SPY', 'QQQ', 'EW']:
        for h in HZ:
            a, b = stats(m, tg, h, 'disc'), stats(m, tg, h, 'hold')
            ep_tot = a['ne'] + b['ne']
            holds = (a['n'] > 0 and b['n'] > 0 and np.sign(a['ex']) == np.sign(b['ex'])
                     and abs(b['ex']) >= abs(a['ex']) / 3)
            rows.append(dict(sig=k, tg=tg, h=h, a=a, b=b, holds=holds, ep=ep_tot))
            print(f"{k:36s} {tg:3s} {h:2d} | {a['n']:3d}/{a['ne']:<3d} {a['ex']:6.2f} {a['t']:5.2f} {a['hit']:5.1f} | "
                  f"{b['n']:3d}/{b['ne']:<3d} {b['ex']:6.2f} {b['t']:5.2f} {b['hit']:5.1f} {b['uhit']:5.1f} | "
                  f"{'Y' if holds else '-'}{' weak' if ep_tot < 20 else ''}  pooledD {a['pex']:+.2f} pooledH {b['pex']:+.2f}")

print('\nPRIMARY (pre-registered) cells: EW h=5 and SPY h=1 (for options hit-rate)')
for r in rows:
    if (r['tg'] == 'EW' and r['h'] == 5) or (r['tg'] == 'SPY' and r['h'] == 1):
        a, b = r['a'], r['b']
        print(f"  {r['sig']:36s} {r['tg']} h={r['h']}: disc {a['ex']:+.2f}% (t {a['t']:.2f}, hit {a['hit']:.0f}%) "
              f"hold {b['ex']:+.2f}% (t {b['t']:.2f}, hit {b['hit']:.0f}% vs uncond {b['uhit']:.0f}%) "
              f"episodes {a['ne']}+{b['ne']} holds={r['holds']}")

# Relief-after-washout follow-up: what happened after each S9 event (all dates, SPY 5d)
print('\nS9 relief-after-washout events, SPY fwd 1d/5d/10d (%):')
for dt in sig['S9 RAW (up2>=40% <=5d after W40)'][lambda x: x].index:
    f = [FW[('SPY', h)][dt] * 100 for h in (1, 5, 10)]
    print(f"  {dt.date()} up2={up2[dt]:.2f}  " + ' '.join('  n/a ' if np.isnan(v) else f'{v:+6.2f}' for v in f))

# ---------------------------------------------------------------------------
# POST-HOC DIAGNOSTICS (added after seeing the grid; counted as extra variants)
# D1: trade only the FIRST event date of each episode (one non-overlapping bet per episode), 9 signals x {SPY,EW} x h=5
# D2: S5 LOW30 with April-2025 episode removed (single-crash sensitivity)
# Total variants examined: 108 grid + 18 (D1) + 2 (D2) = 128
print('\nD1 first-date-of-episode only, excess % (hit %) at h=5; t across episodes')
def first_stats(mask, tg, h, per, drop=None):
    m = mask & periods[per]
    fr = FW[(tg, h)]; base = fr[periods[per]].dropna()
    ev = fr[m].dropna()
    firsts = [e[0] for e in episodes(list(ev.index))]
    if drop is not None:
        firsts = [f for f in firsts if not (drop[0] <= f <= drop[1])]
    x = (fr[firsts] - base.mean()).values * 100
    t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 else np.nan
    return len(x), x.mean() if len(x) else np.nan, t, (fr[firsts] > 0).mean() * 100 if len(x) else np.nan
for k, m in sig.items():
    out = []
    for tg in ['SPY', 'EW']:
        for per in ['disc', 'hold']:
            ne, ex, t, hit = first_stats(m, tg, 5, per)
            out.append(f"{tg} {per} n={ne:2d} {ex:+5.2f} (t {t:4.1f}, hit {hit:3.0f})")
    print(f"  {k:36s} " + ' | '.join(out))
print('\nD2 S5 LOW30 excluding 2025-04-01..2025-04-30:')
for tg in ['SPY', 'EW']:
    m = sig['S5 LOW30 (>=30% at 20d low)']
    m2 = m & ~((cal >= '2025-04-01') & (cal <= '2025-04-30'))
    b = stats(pd.Series(m2, index=cal), tg, 5, 'hold')
    print(f"  {tg} h=5 hold: episodes {b['ne']} ex {b['ex']:+.2f}% pooled {b['pex']:+.2f}% t {b['t']:.2f} hit {b['hit']:.0f}%")
