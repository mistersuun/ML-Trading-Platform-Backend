"""
TEAM MAGNITUDE - size of moves: tail frequencies, volatility clustering, straddle proxy.

PRE-REGISTRATION (written before looking at any outcome; not changed afterwards)
-------------------------------------------------------------------------------
Data: daily bars, universe = all files minus skip list; short histories kept (rolling windows
tolerate them). Split filter: drop any day with |close-to-close| > 45% (set return to NaN).
Returns: cc_t = C_t/C_{t-1}-1 ; oc_t = C_t/O_t-1.
Samples: discovery = event dates 2021-11-05..2024-12-31 ; hold-out = 2025-01-01..2026-11-23.

Definitions (all using information up to day t only):
  sigma60_t   = std of cc over the 60 sessions ending t-1 (min 40 obs)   -> shock yardstick
  typ_t       = mean |cc| over the 60 sessions ending t-1 (min 40)       -> "typical day"
  shock_k(t)  = |cc_t| > k * sigma60_t , k in {2, 3}   (two-sided)
  outcome     = 1{|cc_{t+1}| > m * typ_t}, m in {1, 1.5, 2}
  excess      = outcome - same stock's unconditional rate of that outcome in the same sample
  t-stat      = on date-level averages of excess (cluster by event date)
  episodes    = event dates at least 10 sessions apart (greedy)
Straddle proxy (bought at close t, expires close t+1):
  ewma10_t    = sqrt(EWMA(span=10) of cc^2), INCLUDING day t (the shock)
  implied_t   = 1.2 * ewma10_t (IV premium; 1.1 and 1.3 as sensitivity on the 3-sigma row only)
  cost_t      = 0.8 * implied_t ;  P&L_t = |cc_{t+1}| - cost_t  (% of underlying)
  hit         = P&L > 0 ; excess for straddle rows = P&L itself (zero is the break-even)
Pre-registered result rows (13): see ROWS below. Tradability: stock P&L > 0.15%/trade AND
same sign / rough size in hold-out AND >= 20 episodes.
Variant count: 13 pre-registered rows + descriptive tables (tail frequencies, persistence curve).
No additional variants were tried after seeing outcomes.
"""
import sys, pathlib, warnings
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, '/home/user/ML-Trading-Platform-Backend')
import external_bars

SKIP = set("DBC EFA FFH GLD GURU GVIP IEF NANC PSHD QUAL ROKU RSP SEQ SEQUX SMH VTV SGOV TQQQ".split())
ETF = set("SPY QQQ IWM DIA TQQQ SGOV".split())
DISC_END = pd.Timestamp('2024-12-31')
syms = sorted({p.name.split('.')[0] for p in
               pathlib.Path('/home/user/ML-Trading-Platform-Backend/state/external_bars').glob('*.json')})
syms = [s for s in syms if s not in SKIP]

def load(s):
    for ac in (['etf', 'equity'] if s in ETF else ['equity', 'etf']):
        try:
            d = external_bars.load_bars(s, ac)
            if d is not None and len(d) > 0:
                return d
        except Exception:
            pass
    return None

cc, oc = {}, {}
n_split = 0; split_list = []
for s in syms:
    d = load(s)
    if d is None or len(d) < 80:
        continue
    d = d.sort_index()
    r = d['Close'].pct_change()
    o = d['Close'] / d['Open'] - 1
    bad = r.abs() > 0.45
    if bad.any():
        n_split += int(bad.sum()); split_list += [(s, str(i.date()), round(v*100, 1)) for i, v in r[bad].items()]
        r[bad] = np.nan; o[bad] = np.nan
    cc[s] = r; oc[s] = o
CC = pd.DataFrame(cc).sort_index(); OC = pd.DataFrame(oc).sort_index()
dates = CC.index
print(f"Universe: {CC.shape[1]} symbols, {len(dates)} sessions {dates[0].date()}..{dates[-1].date()}")
print(f"Split-like moves dropped (|cc|>45%): {n_split} {split_list}")

# ---------------------------------------------------------------- (1) tail frequencies
print("\n=== (1) TAIL FREQUENCIES: % of sessions (count) ===")
TH = [0.02, 0.03, 0.04, 0.05, 0.08]
def tail_row(x):
    x = x.dropna(); n = len(x); out = []
    for t in TH:
        dn = (x <= -t).sum(); up = (x >= t).sum()
        out.append(f"{100*dn/n:5.1f}/{100*up/n:5.1f}")
    return n, out
hdr = "  ".join([f"{int(t*100)}% dn/up " for t in TH])
for kind, M in [('close-to-close', CC), ('open-to-close', OC)]:
    print(f"\n-- {kind} --   name   n   " + hdr)
    for s in ['SPY', 'QQQ', 'NVDA', 'TSLA', 'AMD']:
        n, o = tail_row(M[s]); print(f"   {s:6s} {n:5d}  " + "  ".join(o))
    n, o = tail_row(M.stack()); print(f"   POOLED {n:6d} " + "  ".join(o))
spy = CC['SPY'].dropna()
print(f"\nSPY: days <= -8%: {(spy<=-0.08).sum()}, <= -5%: {(spy<=-0.05).sum()}, <= -4%: {(spy<=-0.04).sum()}")
w = spy.nsmallest(5); print("SPY 5 worst days:", ", ".join(f"{i.date()} {v*100:.2f}%" for i, v in w.items()))
b = spy.nlargest(3); print("SPY 3 best days:", ", ".join(f"{i.date()} {v*100:.2f}%" for i, v in b.items()))
spy5 = (1+spy).rolling(5).apply(np.prod, raw=True) - 1
print(f"SPY worst 5-session return: {spy5.min()*100:.2f}% ending {spy5.idxmin().date()}; "
      f"5-session windows <= -8%: {(spy5<=-0.08).sum()}")
print(f"SPY last two sessions: " + ", ".join(f"{i.date()} {v*100:+.2f}%" for i, v in spy.tail(2).items()))

# ---------------------------------------------------------------- features
sig60 = CC.rolling(60, min_periods=40).std().shift(1)
typ = CC.abs().rolling(60, min_periods=40).mean().shift(1)
ewma10 = np.sqrt((CC**2).ewm(span=10, min_periods=10).mean())
nxt = CC.abs().shift(-1)
z = CC.abs() / sig60
valid = z.notna() & nxt.notna() & typ.notna() & ewma10.notna()
disc_mask = pd.Series(dates <= DISC_END, index=dates)

def episodes(ev_dates):
    pos = np.searchsorted(dates, np.array(sorted(set(ev_dates))))
    n, last = 0, -10**9
    for p in pos:
        if p - last >= 10: n += 1; last = p
    return n

def date_stats(vals, ev):
    """vals, ev: DataFrames; returns per-sample (mean%, t, hit% placeholder, n_dates, n_eps)."""
    res = {}
    for name, m in [('disc', disc_mask), ('hold', ~disc_mask)]:
        e = ev & valid & pd.DataFrame(np.repeat(m.values[:, None], ev.shape[1], 1), index=dates, columns=ev.columns)
        v = vals.where(e)
        dm = v.mean(axis=1).dropna()
        n = len(dm)
        mean = dm.mean() if n else np.nan
        t = mean / (dm.std(ddof=1)/np.sqrt(n)) if n > 2 and dm.std() > 0 else np.nan
        res[name] = dict(mean=mean, t=t, n_dates=n, eps=episodes(dm.index), mask=e, n_obs=int(e.values.sum()))
    return res

ROWS = []
def add(sig, hor, res, cond_hit, unc_hit, scale=100):
    allm = res['disc']['mask'] | res['hold']['mask']
    dd = allm.any(axis=1); ev_dates = dates[dd.values]
    dm, hm = res['disc']['mean'], res['hold']['mean']
    holds = (np.sign(dm) == np.sign(hm)) and abs(hm) >= 0.5*abs(dm) and res['hold']['eps'] >= 20
    ROWS.append(dict(signal=sig, horizon=hor, n_event_dates=len(ev_dates), n_episodes=episodes(ev_dates),
                     disc_mean=dm*scale, disc_t=res['disc']['t'], hold_mean=hm*scale, hold_t=res['hold']['t'],
                     disc_hit=cond_hit[0], hold_hit=cond_hit[1], unc_hit=unc_hit, holds=bool(holds),
                     d_eps=res['disc']['eps'], h_eps=res['hold']['eps'], n_obs=res['disc']['n_obs']+res['hold']['n_obs']))

def rate(ind, mask):
    return 100*np.nanmean(ind.where(mask).values.astype(float))

# ---------------------------------------------------------------- (2) volatility clustering
for k in [2, 3]:
    ev = z > k
    for mlt in [1, 1.5, 2]:
        ind = (nxt > mlt*typ).astype(float).where(valid)
        # same-stock unconditional rate, per sample
        unc_d = ind[disc_mask.values].mean(); unc_h = ind[~disc_mask.values].mean()
        unc = pd.DataFrame(np.where(disc_mask.values[:, None], unc_d.values[None, :], unc_h.values[None, :]),
                           index=dates, columns=ind.columns)
        res = date_stats(ind - unc, ev)
        ch = (rate(ind, res['disc']['mask']), rate(ind, res['hold']['mask']))
        add(f"{k}-sigma shock (pooled stocks): P(|next| > {mlt}x typical)", "t+1 abs move", res, ch,
            100*np.nanmean(ind.values))

# SPY-only clustering row
ev_spy = pd.DataFrame(False, index=dates, columns=CC.columns); ev_spy['SPY'] = z['SPY'] > 2
ind = (nxt > 1.5*typ).astype(float).where(valid)
unc_d = ind[disc_mask.values].mean(); unc_h = ind[~disc_mask.values].mean()
unc = pd.DataFrame(np.where(disc_mask.values[:, None], unc_d.values[None, :], unc_h.values[None, :]), index=dates, columns=ind.columns)
res = date_stats(ind - unc, ev_spy)
add("SPY 2-sigma shock: P(|next| > 1.5x typical)", "t+1 abs move", res,
    (rate(ind, res['disc']['mask']), rate(ind, res['hold']['mask'])), 100*np.nanmean(ind['SPY'].values))

# ---------------------------------------------------------------- (3) straddle proxy
def straddle(ev, prem, label):
    cost = 0.8*prem*ewma10
    pnl = (nxt - cost).where(valid)
    hit = (pnl > 0).astype(float).where(valid)
    res = date_stats(pnl, ev)
    unc_hit = 100*np.nanmean(hit.values) if ev.shape[1] > 1 and ev.values.sum() > 1000 else None
    allv = valid & ev.any(axis=0).values[None, :]  # baseline over the same names
    unc_hit = 100*np.nanmean(hit.where(valid.loc[:, ev.any(axis=0)]).values) if True else None
    add(label, "buy close t, settle close t+1", res,
        (rate(hit, res['disc']['mask']), rate(hit, res['hold']['mask'])), unc_hit)
    return pnl, cost

allday = valid.copy()
straddle(allday, 1.2, "Straddle every day, all stocks (IV=1.2x EWMA10) - baseline")
straddle(z > 2, 1.2, "Straddle after 2-sigma shock, stocks (IV=1.2x)")
straddle((z > 2) & (CC < 0), 1.2, "Straddle after 2-sigma DOWN shock, stocks (IV=1.2x)")
straddle(z > 3, 1.1, "Straddle after 3-sigma shock, stocks (IV=1.1x)")
straddle(z > 3, 1.3, "Straddle after 3-sigma shock, stocks (IV=1.3x)")
straddle(ev_spy, 1.2, "Straddle SPY after SPY 2-sigma shock (IV=1.2x)")

print("\n=== PRE-REGISTERED RESULT ROWS (excess/P&L in % ; t clustered by date) ===")
print("clustering rows: excess = pct-points of probability over same-stock unconditional rate;"
      " straddle rows: mean P&L in % of underlying (break-even 0)")
for r in ROWS:
    print(f"\n* {r['signal']}  [{r['horizon']}]")
    print(f"   event dates {r['n_event_dates']}, episodes {r['n_episodes']} (disc {r['d_eps']}, hold {r['h_eps']}), stock-events {r['n_obs']}")
    print(f"   discovery: excess {r['disc_mean']:+.2f}  t {r['disc_t']:.2f}  hit {r['disc_hit']:.1f}% | "
          f"hold-out: excess {r['hold_mean']:+.2f}  t {r['hold_t']:.2f}  hit {r['hold_hit']:.1f}% | "
          f"unconditional hit {r['unc_hit']:.1f}% | holds: {r['holds']}")

# ---------------------------------------------------------------- persistence
print("\n=== PERSISTENCE: mean |cc_{t+h}| / typical_t after a 2-sigma shock vs all days (pooled, date-clustered) ===")
base = (CC.abs().shift(-1) / typ).where(valid)
base_mean = np.nanmean(base.values)
ev = (z > 2) & valid
line = []; elevated_until = None
for h in range(1, 21):
    rat = (CC.abs().shift(-h) / typ).where(ev)
    dm = rat.mean(axis=1).dropna().mean()
    line.append(f"h{h}:{dm:.2f}")
    if elevated_until is None and dm < 1.10*base_mean: elevated_until = h
print(f"unconditional ratio {base_mean:.2f}"); print("  ".join(line))
print(f"First horizon where ratio < 1.10x unconditional: h={elevated_until}")
for k in [2, 3]:
    ev = (z > k) & valid
    for nm, m in [('disc', disc_mask), ('hold', ~disc_mask)]:
        r1 = (CC.abs().shift(-1)/typ).where(ev & m.values[:, None]).mean(axis=1).dropna().mean()
        r5 = (CC.abs().shift(-5)/typ).where(ev & m.values[:, None]).mean(axis=1).dropna().mean()
        print(f"  {k}-sigma {nm}: h1 ratio {r1:.2f}, h5 ratio {r5:.2f}")

# ---------------------------------------------------------------- straddle diagnostics
print("\n=== STRADDLE DIAGNOSTIC: next |move| / cost (IV=1.2x) ===")
cost = 0.8*1.2*ewma10
for nm, ev in [('all days', valid), ('2-sigma', (z > 2) & valid), ('3-sigma', (z > 3) & valid)]:
    rr = (nxt / cost).where(ev)
    print(f"  {nm:9s}: mean |next|/cost {np.nanmean(rr.values):.2f}, median {np.nanmedian(rr.values):.2f}; "
          f"mean cost {100*np.nanmean(cost.where(ev).values):.2f}% , mean |next| {100*np.nanmean(nxt.where(ev).values):.2f}%")
# what IV premium would make the 2-sigma straddle break even (date-clustered mean)
ev = (z > 2) & valid
for prem in [0.8, 0.9, 1.0, 1.1, 1.2]:
    p = (nxt - 0.8*prem*ewma10).where(ev).mean(axis=1).dropna().mean()
    print(f"  2-sigma, IV premium {prem:.1f}: date-clustered mean P&L {100*p:+.3f}%")
print("(premium sweep above is descriptive only, not a tested signal)")
