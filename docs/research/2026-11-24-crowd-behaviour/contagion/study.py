"""TEAM contagion: do shocks in a leader / SPY / a group diffuse into related stocks the next day?

PRE-REGISTRATION (written before any outcome was computed; not changed afterwards)
-------------------------------------------------------------------------------
Groups (fixed): SEMI, MEGA, EV, CRYPTO, CHINA, SOFT as in the task.
Leaders (fixed): SEMI=NVDA, MEGA=AAPL (largest at sample start), EV=TSLA, CRYPTO=COIN,
                 CHINA=BABA, SOFT=CRM (largest software name at sample start).
Shock sigma: z_t = r_t / std(r_{t-60..t-1}) (60 prior sessions, min 40), close-to-close.
Return measure: excess_i = r_i - r_SPY, then demeaned by the stock's own mean excess within
  the same sample block (discovery / hold-out) -> removes survivorship drift.
  "Signed" = multiplied by sign of the day-0 shock, so >0 means CONTINUATION, <0 reversal.
Horizons: next-day open-to-close (o2c, t+1), next-day close-to-close (c2c, t+1),
  next 3 days close-to-close (t+1..t+3 compounded, minus SPY, demeaned per-day*3).
Signals (14 rows, all reported):
 S1-S3  leader |z|>3 -> followers (rest of group) signed excess: o2c1, c2c1, c2c3
 S4     leader up-shock only  -> followers c2c1 (signed)
 S5     leader down-shock only -> followers c2c1 (signed)
 S6-S7  leader |z|>3 -> under-reacting followers (bottom half by signed day-0 excess
        on that date) minus the other followers: c2c1, c2c3  (>0 = catch-up)
 S8-S9  SPY |z|>2 -> laggards (bottom quintile of signed day-0 return among all stocks)
        minus the cross-sectional mean: c2c1, c2c3 (>0 = laggards catch up)
 S10    SPY |z|>2 -> SPY's own signed next-day c2c (context row)
 S11-S13 group median return |z|>3 (z on the median series' own 60d std) -> members signed
        excess o2c1, c2c1, c2c3
 S14    group shock -> under-reacting members minus other members c2c1
Inference: average across stocks per event date first, then t = mean/(sd/sqrt(n_dates)).
  Event dates per signal pooled across groups (a date hit by 2 groups gives 2 date-group obs,
  then averaged to one per date). Episodes = event dates >=10 sessions apart (greedy).
Hit rate = share of event dates whose date-level average is >0. Unconditional hit rate =
  share of ALL dates where the same date-level average of the UNSIGNED measure is >0.
"Holds out of sample" = discovery |t|>=2 AND same sign in hold-out AND hold-out |mean| >=
  50% of discovery |mean|. Tradable needs |mean| >= 0.15% per trade in BOTH blocks.
Discovery 2021-11-05..2024-12-31, hold-out 2025-01-01..2026-11-23.
Variants tried in total: 14 (this one specification; no re-runs with other thresholds).
"""
import sys, pathlib
import numpy as np, pandas as pd
sys.path.insert(0, '/home/user/ML-Trading-Platform-Backend')
import external_bars

SKIP = set('DBC EFA FFH GLD GURU GVIP IEF NANC PSHD QUAL ROKU RSP SEQ SEQUX SMH VTV SGOV TQQQ'.split())
ETF = {'SPY', 'QQQ', 'IWM', 'DIA', 'TQQQ', 'SGOV'}
GROUPS = {
    'SEMI': 'NVDA AMD AVGO MU TSM ARM SMCI MRVL QCOM INTC LRCX AMAT KLAC ANET DELL'.split(),
    'MEGA': 'AAPL MSFT AMZN GOOGL META'.split(),
    'EV': 'TSLA RIVN LCID NIO F GM'.split(),
    'CRYPTO': 'COIN MSTR MARA RIOT HOOD'.split(),
    'CHINA': 'BABA JD BIDU PDD'.split(),
    'SOFT': 'CRM ADBE SNOW DDOG NET MDB ZS CRWD PANW PLTR'.split(),
}
LEADER = {'SEMI': 'NVDA', 'MEGA': 'AAPL', 'EV': 'TSLA', 'CRYPTO': 'COIN', 'CHINA': 'BABA', 'SOFT': 'CRM'}
DISC_END = pd.Timestamp('2024-12-31')

syms = sorted({p.name.split('.')[0] for p in
               pathlib.Path('/home/user/ML-Trading-Platform-Backend/state/external_bars').glob('*.json')})
syms = [s for s in syms if s not in SKIP]
O, C = {}, {}
for s in syms:
    d = external_bars.load_bars(s, 'etf' if s in ETF else 'equity')
    O[s], C[s] = d['Open'], d['Close']
O = pd.DataFrame(O).sort_index(); C = pd.DataFrame(C).sort_index()
R = C.pct_change(fill_method=None)
split_mask = R.abs() > 0.45
n_split = int(split_mask.sum().sum())
split_list = [(R.columns[j], str(R.index[i].date()), round(float(R.iat[i, j]), 3))
              for i, j in zip(*np.where(split_mask.values))]
R = R.mask(split_mask)
OC = C / O - 1
OC = OC.mask(OC.abs() > 0.45)

spy = R['SPY']; spy_oc = OC['SPY']
stocks = [s for s in syms if s not in ETF]
X1 = R[stocks].sub(spy, axis=0)                       # c2c t excess
XO = OC[stocks].sub(spy_oc, axis=0)                   # o2c t excess
X3 = sum(X1.shift(-k) for k in (1, 2, 3))             # sum of excess t+1..t+3 (additive approx)
block = pd.Series(np.where(R.index <= DISC_END, 'disc', 'hold'), index=R.index)

def demean(df):
    out = df.copy()
    for b in ('disc', 'hold'):
        m = block == b
        out.loc[m] = df.loc[m] - df.loc[m].mean()
    return out
F_oc1 = demean(XO).shift(-1)
F_c1 = demean(X1).shift(-1)
F_c3 = demean(X1.shift(-1) + X1.shift(-2) + X1.shift(-3))  # demean the 3d sum within block

def zscore(r):
    sd = r.rolling(60, min_periods=40).std().shift(1)
    return r / sd

def episodes(dates):
    idx = [R.index.get_loc(d) for d in sorted(dates)]
    n, last = 0, -999
    for i in idx:
        if i - last >= 10:
            n += 1; last = i
    return n

def summarize(name, horizon, ser, uncond):
    """ser: date-level Series (signed), uncond: date-level Series over all dates (unsigned)."""
    ser = ser.dropna()
    row = {'signal': name, 'horizon': horizon, 'n_event_dates': int(len(ser)),
           'n_episodes': episodes(ser.index)}
    for b, lab in (('disc', 'discovery'), ('hold', 'holdout')):
        s = ser[block.reindex(ser.index) == b]
        n = len(s)
        row[f'{lab}_n'] = n
        row[f'{lab}_mean_excess_pct'] = round(100 * s.mean(), 3) if n else np.nan
        row[f'{lab}_t'] = round(s.mean() / (s.std(ddof=1) / np.sqrt(n)), 2) if n > 2 else np.nan
        row[f'{lab}_hit_rate_pct'] = round(100 * (s > 0).mean(), 1) if n else np.nan
    u = uncond.dropna()
    row['unconditional_hit_rate_pct'] = round(100 * (u > 0).mean(), 1) if len(u) else np.nan
    row['unconditional_mean_pct'] = round(100 * u.mean(), 3) if len(u) else np.nan
    dm, hm, dt = row['discovery_mean_excess_pct'], row['holdout_mean_excess_pct'], row['discovery_t']
    row['holds_out_of_sample'] = bool(abs(dt) >= 2 and np.sign(dm) == np.sign(hm) and abs(hm) >= 0.5 * abs(dm))
    row['tradable'] = bool(row['holds_out_of_sample'] and min(abs(dm), abs(hm)) >= 0.15)
    return row

rows = []
# ---------------- (1) leader shocks ----------------
def leader_events(direction=None):
    """returns dict horizon -> list of (date, value) date-group obs; plus laggard spreads."""
    out = {'oc1': [], 'c1': [], 'c3': [], 'lag1': [], 'lag3': []}
    unc = {'oc1': [], 'c1': [], 'c3': [], 'lag1': [], 'lag3': []}
    for g, mem in GROUPS.items():
        L = LEADER[g]; fol = [m for m in mem if m != L and m in stocks]
        z = zscore(R[L])
        ev = z[(z.abs() > 3)].index
        if direction == 'up': ev = z[z > 3].index
        if direction == 'down': ev = z[z < -3].index
        for d in ev:
            sg = np.sign(z[d])
            for k, F in (('oc1', F_oc1), ('c1', F_c1), ('c3', F_c3)):
                v = (sg * F.loc[d, fol]).mean()
                out[k].append((d, v))
            day0 = (sg * X1.loc[d, fol]).dropna()
            if len(day0) >= 2:
                med = day0.median()
                lag = day0[day0 <= med].index; lead = day0[day0 > med].index
                for k, F in (('lag1', F_c1), ('lag3', F_c3)):
                    v = sg * (F.loc[d, lag].mean() - F.loc[d, lead].mean())
                    out[k].append((d, v))
        for k, F in (('oc1', F_oc1), ('c1', F_c1), ('c3', F_c3)):
            unc[k].append(F[fol].mean(axis=1))
    agg = {k: pd.Series(dict(pd.DataFrame(v, columns=['d', 'v']).groupby('d')['v'].mean())) if v else pd.Series(dtype=float)
           for k, v in out.items()}
    uagg = {k: pd.concat(v, axis=1).mean(axis=1) for k, v in unc.items() if v}
    return agg, uagg

a, ua = leader_events()
rows.append(summarize('S1 leader |z|>3 -> followers', 'next-day o2c', a['oc1'], ua['oc1']))
rows.append(summarize('S2 leader |z|>3 -> followers', 'next-day c2c', a['c1'], ua['c1']))
rows.append(summarize('S3 leader |z|>3 -> followers', 'next-3-day c2c', a['c3'], ua['c3']))
au, _ = leader_events('up'); ad, _ = leader_events('down')
rows.append(summarize('S4 leader z>+3 (up only) -> followers', 'next-day c2c', au['c1'], ua['c1']))
rows.append(summarize('S5 leader z<-3 (down only) -> followers', 'next-day c2c', ad['c1'], ua['c1']))
zero = pd.Series(np.nan, index=R.index)  # spreads: no natural unsigned baseline -> NaN
rows.append(summarize('S6 leader shock: under-reacting minus other followers', 'next-day c2c', a['lag1'], zero))
rows.append(summarize('S7 leader shock: under-reacting minus other followers', 'next-3-day c2c', a['lag3'], zero))

# ---------------- (2) SPY shocks -> laggards ----------------
zs = zscore(spy)
ev = zs[zs.abs() > 2].index
s8, s9, s10 = {}, {}, {}
spy_dm = spy.shift(-1) - spy.shift(-1).groupby(block).transform('mean')
for d in ev:
    sg = np.sign(zs[d])
    day0 = (sg * R.loc[d, stocks]).dropna()
    if len(day0) < 20: continue
    q = day0.quantile(0.2); lag = day0[day0 <= q].index
    univ = day0.index
    s8[d] = sg * (F_c1.loc[d, lag].mean() - F_c1.loc[d, univ].mean())
    s9[d] = sg * (F_c3.loc[d, lag].mean() - F_c3.loc[d, univ].mean())
    s10[d] = sg * spy_dm.loc[d]
rows.append(summarize('S8 SPY |z|>2: bottom-quintile laggards minus universe', 'next-day c2c', pd.Series(s8), zero))
rows.append(summarize('S9 SPY |z|>2: bottom-quintile laggards minus universe', 'next-3-day c2c', pd.Series(s9), zero))
rows.append(summarize('S10 SPY |z|>2 -> SPY itself (demeaned)', 'next-day c2c', pd.Series(s10), spy_dm))

# ---------------- (3) group-median shocks ----------------
out = {'oc1': [], 'c1': [], 'c3': [], 'lag1': []}; unc = {'oc1': [], 'c1': [], 'c3': []}
for g, mem in GROUPS.items():
    mem = [m for m in mem if m in stocks]
    gm = R[mem].median(axis=1, skipna=True).where(R[mem].notna().sum(axis=1) >= 3)
    z = zscore(gm)
    for d in z[z.abs() > 3].index:
        sg = np.sign(z[d])
        for k, F in (('oc1', F_oc1), ('c1', F_c1), ('c3', F_c3)):
            out[k].append((d, (sg * F.loc[d, mem]).mean()))
        day0 = (sg * X1.loc[d, mem]).dropna()
        med = day0.median(); lag = day0[day0 <= med].index; lead = day0[day0 > med].index
        out['lag1'].append((d, sg * (F_c1.loc[d, lag].mean() - F_c1.loc[d, lead].mean())))
    for k, F in (('oc1', F_oc1), ('c1', F_c1), ('c3', F_c3)):
        unc[k].append(F[mem].mean(axis=1))
g_agg = {k: pd.DataFrame(v, columns=['d', 'v']).groupby('d')['v'].mean() for k, v in out.items()}
g_unc = {k: pd.concat(v, axis=1).mean(axis=1) for k, v in unc.items()}
rows.append(summarize('S11 group median |z|>3 -> members', 'next-day o2c', g_agg['oc1'], g_unc['oc1']))
rows.append(summarize('S12 group median |z|>3 -> members', 'next-day c2c', g_agg['c1'], g_unc['c1']))
rows.append(summarize('S13 group median |z|>3 -> members', 'next-3-day c2c', g_agg['c3'], g_unc['c3']))
rows.append(summarize('S14 group shock: under-reacting minus other members', 'next-day c2c', g_agg['lag1'], zero))

# ---------------- print ----------------
pd.set_option('display.width', 250); pd.set_option('display.max_columns', 30); pd.set_option('display.max_colwidth', 60)
print('Universe: %d stocks + SPY etc; sessions %d (%s..%s)' % (len(stocks), len(R), R.index[0].date(), R.index[-1].date()))
print('Split-like moves |c2c|>45%% dropped: %d -> %s' % (n_split, split_list))
print('Signed measures: >0 = continuation in shock direction (S6-S9,S14: >0 = laggards catch up).')
print('Excess = stock minus SPY, demeaned by the stock\'s own mean within each block. Percent units.\n')
T = pd.DataFrame(rows)
cols = ['signal', 'horizon', 'n_event_dates', 'n_episodes', 'discovery_n', 'discovery_mean_excess_pct', 'discovery_t',
        'discovery_hit_rate_pct', 'holdout_n', 'holdout_mean_excess_pct', 'holdout_t', 'holdout_hit_rate_pct',
        'unconditional_hit_rate_pct', 'holds_out_of_sample', 'tradable']
print(T[cols].to_string(index=False))
print('\n(unconditional hit rate for spread rows S6-S9,S14 is not defined: NaN; their null is 50%)')
print('Note: the >45% filter caught earnings/squeeze jumps (SNAP, GME, ASTS, MRNA...) not only splits; all dropped (conservative).')

# context: what fraction of leader-shock event dates are also SPY |z|>2 days (market-wide)?
lead_dates = set(a['c1'].dropna().index)
print('\nLeader-shock event dates also SPY |z|>2 days: %d of %d' % (len(lead_dates & set(ev)), len(lead_dates)))
# context: most recent events (no outcomes for last day)
print('Most recent leader shocks:', [(g, str(d.date()), round(float(zscore(R[LEADER[g]])[d]), 1))
                                     for g in GROUPS for d in zscore(R[LEADER[g]]).pipe(lambda z: z[z.abs() > 3]).index[-2:]])
print('Most recent SPY |z|>2 days:', [str(d.date()) for d in ev[-4:]])
T.to_csv(pathlib.Path(__file__).with_name('results_table.csv'), index=False)
