"""
TEAM calendar -- calendar and streak effects.  PAPER research only, offline.

PRE-REGISTRATION (written before any outcome was computed; not changed afterwards)
-----------------------------------------------------------------------------
Assets (3): SPY, QQQ, EW = equal-weight daily close-to-close return of the stock
universe (skip list removed; split-like moves |r|>45% dropped as missing).
Outcome: close-to-close return(s) of the session(s) named below, in excess of the
SAME asset's unconditional mean daily return in the SAME sample period (x number of
days for multi-day windows). Hit rate = % of events with raw return > 0, shown beside
the unconditional hit rate.
Rules (18 per asset, 54 tests in total):
  R01-R05  Day of week Mon..Fri: return on that day.
  R06  Pre-Thanksgiving: cumulative return of the 3 sessions ending Wednesday before
       US Thanksgiving (close T-4 -> close Wed).  2021-2025 (5 events).
  R07  Black Friday half-day: return of the Friday session after Thanksgiving.
  R08  Last 3 sessions of the calendar month: each day's return.
  R09  First 3 sessions of the calendar month: each day's return.
  R10  Monthly opex week: each session of the Mon-Fri week containing the third
       Friday (if Friday is a holiday, the preceding session is expiry).
  R11  Day after monthly expiry: the next session's return.
  R12-R14  Next-day return after exactly-or-more 3, 4, 5 consecutive down closes
       (signal fires on the day the streak reaches >=k; next-day outcome).
  R15  Next-day return after >=3 consecutive up closes.
  R16  Next session after a Friday with return > +2%.
  R17  Next session after a Friday with return < -2%.
  R18  Turnaround Tuesday: Tuesday return when the prior session was a Monday
       with a negative return.
Discovery: 2021-11-05..2024-12-31.  Hold-out: 2025-01-01..2026-11-23.
Inference: each asset series is one value per date, so the t-statistic is already
date-level (EW averages stocks per date first).  Episodes = events >=10 sessions
apart (greedy).  Multiple testing: Benjamini-Hochberg (FDR 10%) and Holm (5%) over
the 54 discovery p-values.
"Holds" = discovery BH-significant AND hold-out mean has the same sign AND is at
least 50% of the discovery size.  Tradable needs |mean| >= 0.15% per trade, or a
hit rate >= 58% for next-day options bets.  Variants tried in total: 54 (no others).
"""
import sys, pathlib, math
import numpy as np, pandas as pd
from scipy import stats
sys.path.insert(0, '/home/user/ML-Trading-Platform-Backend')
import external_bars

REPO = pathlib.Path('/home/user/ML-Trading-Platform-Backend')
SKIP = set('DBC EFA FFH GLD GURU GVIP IEF NANC PSHD QUAL ROKU RSP SEQ SEQUX SMH VTV SGOV TQQQ'.split())
ETF = {'SPY', 'QQQ', 'IWM', 'DIA', 'TQQQ', 'SGOV'}
DISC = ('2021-11-05', '2024-12-31'); HOLD = ('2025-01-01', '2026-11-23')

syms = sorted({p.name.split('.')[0] for p in (REPO / 'state/external_bars').glob('*.json')})
closes = {}
for s in syms:
    if s in SKIP: continue
    try:
        d = external_bars.load_bars(s, 'etf' if s in ETF else 'equity')
    except Exception:
        continue
    if d is None or len(d) == 0: continue
    closes[s] = d['Close'].astype(float)
C = pd.DataFrame(closes).sort_index()
R = C.pct_change(fill_method=None)
split_mask = R.abs() > 0.45
n_split = int(split_mask.sum().sum())
split_list = [(c, str(i.date()), round(float(R.at[i, c]) * 100, 1)) for i, c in zip(*np.where(split_mask)) for i, c in [(R.index[i], R.columns[c])]]
R = R.mask(split_mask)
stocks = [c for c in R.columns if c not in ETF]
series = {'SPY': R['SPY'], 'QQQ': R['QQQ'], 'EW': R[stocks].mean(axis=1, skipna=True)}
idx = R.index[1:]
for k in series: series[k] = series[k].loc[idx]

print(f'Universe: {len(stocks)} stocks in EW (+SPY, QQQ); sessions {len(idx)} '
      f'{idx[0].date()}..{idx[-1].date()}')
print(f'Split-like moves dropped (|r|>45%): {n_split} -> {split_list}')

pos = pd.Series(np.arange(len(idx)), index=idx)
dow = idx.dayofweek

def period_mask(a, b):
    return (idx >= a) & (idx <= b)

# ---------- calendar event dates (independent of outcomes) ----------
def thanksgiving(y):
    d = pd.Timestamp(y, 11, 1); first_thu = d + pd.Timedelta(days=(3 - d.dayofweek) % 7)
    return first_thu + pd.Timedelta(weeks=3)

tg_pre, tg_fri = [], []
for y in range(2021, 2026):
    t = thanksgiving(y)
    wed = idx[idx < t][-1]; fri = idx[idx > t][0]
    tg_pre.append(wed); tg_fri.append(fri)

ym = pd.Series(idx.to_period('M'), index=idx)
grp = pd.Series(np.arange(len(idx)), index=idx).groupby(ym.values)
first_month, last_month = ym.iloc[0], ym.iloc[-1]
last3, first3 = [], []
for m, g in grp:
    dates = g.index
    if m != last_month: last3 += list(dates[-3:])     # incomplete final month excluded
    if m != first_month: first3 += list(dates[:3])    # incomplete first month excluded

opex_days, opex_week, day_after = [], [], []
for m in pd.period_range(idx[0].to_period('M'), idx[-1].to_period('M'), freq='M'):
    d = pd.Timestamp(m.year, m.month, 1)
    third_fri = d + pd.Timedelta(days=(4 - d.dayofweek) % 7) + pd.Timedelta(weeks=2)
    prior = idx[idx <= third_fri]
    if len(prior) == 0 or (third_fri - prior[-1]).days > 3: continue
    exp = prior[-1]
    if exp < idx[0]: continue
    monday = third_fri - pd.Timedelta(days=4)
    opex_week += list(idx[(idx >= monday) & (idx <= third_fri)])
    nxt = idx[idx > exp]
    if len(nxt): day_after.append(nxt[0])
    opex_days.append(exp)

# ---------- streak / conditional events (signal on asset's own returns) ----------
def streak_events(r, k, down=True):
    sgn = (r < 0) if down else (r > 0)
    run = sgn.astype(int).groupby((~sgn).cumsum()).cumsum()
    sig = run >= k
    nxt = sig.shift(1, fill_value=False)  # outcome day = day after signal
    return list(r.index[nxt.values])

def after_friday(r, thr_up):
    fri = (r.index.dayofweek == 4) & ((r > 0.02) if thr_up else (r < -0.02)).values
    return list(r.index[np.roll(fri, 1) & (np.arange(len(r)) > 0)])

def turnaround_tue(r):
    out = []
    for i in range(1, len(r)):
        if r.index[i].dayofweek == 1 and r.index[i - 1].dayofweek == 0 and r.iloc[i - 1] < 0:
            out.append(r.index[i])
    return out

def episodes(dates):
    p = sorted(pos.loc[dates].values); n = 0; last = -99
    for x in p:
        if x - last >= 10: n += 1; last = x
    return n

def evaluate(r, ev_dates, ndays=1):
    """ev_dates = dates whose return (or the ndays-window ending there) is the outcome."""
    rows = {}
    for lab, (a, b) in (('disc', DISC), ('hold', HOLD)):
        m = period_mask(a, b); rs = r[m]; mu = rs.mean()
        uncond_hit = (rs > 0).mean() * 100 if ndays == 1 else \
            (rs.rolling(ndays).sum() > 0).mean() * 100
        ev = [d for d in ev_dates if a <= str(d.date()) <= b]
        if ndays == 1:
            vals = r.loc[ev].dropna()
        else:
            cum = (1 + r).rolling(ndays).apply(np.prod, raw=True) - 1
            vals = cum.loc[ev].dropna()
        ex = vals - ndays * mu
        n = len(ex)
        t = ex.mean() / (ex.std(ddof=1) / math.sqrt(n)) if n > 2 and ex.std() > 0 else np.nan
        p = 2 * stats.t.sf(abs(t), n - 1) if n > 2 and not np.isnan(t) else 1.0
        rows[lab] = dict(n=n, ep=episodes(list(vals.index)) if n else 0, mean=ex.mean() * 100 if n else np.nan,
                         raw=vals.mean() * 100 if n else np.nan, hit=(vals > 0).mean() * 100 if n else np.nan,
                         uhit=uncond_hit, umean=ndays * mu * 100, t=t, p=p)
    return rows

RULES = ['R01 Mon', 'R02 Tue', 'R03 Wed', 'R04 Thu', 'R05 Fri', 'R06 Pre-Thanksgiving 3d',
         'R07 Black Friday', 'R08 Last 3 of month', 'R09 First 3 of month', 'R10 Opex week',
         'R11 Day after opex', 'R12 After >=3 down', 'R13 After >=4 down', 'R14 After >=5 down',
         'R15 After >=3 up', 'R16 Mon after Fri>+2%', 'R17 Mon after Fri<-2%', 'R18 Turnaround Tue']

results = []
for a, r in series.items():
    evs = [list(idx[dow == k]) for k in range(5)]
    evs += [tg_pre, tg_fri, last3, first3, opex_week, day_after]
    evs += [streak_events(r, k) for k in (3, 4, 5)] + [streak_events(r, 3, down=False)]
    evs += [after_friday(r, True), after_friday(r, False), turnaround_tue(r)]
    for name, ev in zip(RULES, evs):
        res = evaluate(r, ev, ndays=3 if name.startswith('R06') else 1)
        results.append(dict(asset=a, rule=name, n_all=len(ev), ep_all=episodes(ev), **{
            f'{lab}_{k}': v for lab in ('disc', 'hold') for k, v in res[lab].items()}))
T = pd.DataFrame(results)
m = len(T)
# BH and Holm on discovery p-values
p = T['disc_p'].values; order = np.argsort(p)
bh = np.empty(m); prev = 1.0
for rank in range(m, 0, -1):
    i = order[rank - 1]; prev = min(prev, p[i] * m / rank); bh[i] = prev
T['disc_q_bh'] = bh
holm = np.empty(m); run = 0.0
for rank, i in enumerate(order):
    run = max(run, min(1.0, p[i] * (m - rank))); holm[i] = run
T['disc_p_holm'] = holm
T['same_sign'] = np.sign(T.disc_mean) == np.sign(T.hold_mean)
T['holds'] = (T.disc_q_bh < 0.10) & T.same_sign & (T.hold_mean.abs() >= 0.5 * T.disc_mean.abs())
T['holds_raw'] = (T.disc_p < 0.05) & T.same_sign & (T.hold_mean.abs() >= 0.5 * T.disc_mean.abs()) & (T.hold_p < 0.10)

pd.set_option('display.width', 250); pd.set_option('display.max_rows', 200)
print(f'\nTests: {m} (18 rules x 3 assets). Excess = vs same asset unconditional mean in that period, % per event.')
print('Unconditional daily mean % (disc/hold): ' + ', '.join(
    f"{a}: {series[a][period_mask(*DISC)].mean()*100:.3f}/{series[a][period_mask(*HOLD)].mean()*100:.3f}" for a in series))
cols = ['asset', 'rule', 'n_all', 'ep_all', 'disc_n', 'disc_ep', 'hold_ep', 'disc_mean', 'disc_hit', 'disc_uhit', 'disc_t', 'disc_q_bh',
        'hold_n', 'hold_mean', 'hold_hit', 'hold_uhit', 'hold_t', 'same_sign', 'holds', 'holds_raw']
print(T[cols].round(3).to_string(index=False))
print(f"\nBH q<0.10 in discovery: {int((T.disc_q_bh<0.10).sum())};  Holm p<0.05: {int((T.disc_p_holm<0.05).sum())}")
print(f"Pre-registered 'holds': {int(T.holds.sum())};  lenient (raw p<.05 disc, p<.10 hold, same sign): {int(T.holds_raw.sum())}")
print(f"Same sign disc vs hold: {int(T.same_sign.sum())}/{m} (coin-flip expectation {m/2:.0f})")
print('\nTop 8 by |discovery t|:')
print(T.reindex(T.disc_t.abs().sort_values(ascending=False).index)[cols].head(8).round(3).to_string(index=False))

# Thanksgiving detail (tiny sample)
print('\nThanksgiving per-year (raw %): pre-3d cum / Black Friday')
for a, r in series.items():
    cum = ((1 + r).rolling(3).apply(np.prod, raw=True) - 1)
    print(f"  {a}: " + '; '.join(f"{w.year}: {cum.loc[w]*100:+.2f} / {r.loc[f]*100:+.2f}" for w, f in zip(tg_pre, tg_fri)))
# Today's context
print(f"\nLast 5 sessions SPY %: {', '.join(f'{d.date()} {v*100:+.2f}' for d, v in series['SPY'].tail(5).items())}")
print(f"Last 5 sessions QQQ %: {', '.join(f'{d.date()} {v*100:+.2f}' for d, v in series['QQQ'].tail(5).items())}")
T.to_csv(pathlib.Path(__file__).with_name('results_table.csv'), index=False)
