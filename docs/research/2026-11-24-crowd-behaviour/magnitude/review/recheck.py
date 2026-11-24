"""
Independent re-check of team 'magnitude' (reviewer code, written separately from study.py).
PRE-REGISTRATION (reviewer, fixed before running):
  Replicate ONE row: 3-sigma two-sided shock (|r_t| > 3 * std of r over t-60..t-1, min 40 obs),
  outcome 1{|r_t+1| > 1.5 * mean|r| over t-60..t-1}; excess over same stock's unconditional rate
  within each sample; t on date-level means. Also the 2-sigma version of the same outcome.
  Placebos (2): (a) shock mask shifted forward 20 sessions (signal = shock 20 sessions ago);
               (b) shock dates shuffled within the same stock and calendar year (seed 7).
  Extra checks (3): (c) split events into market-wide dates (>=10% of names shock) vs idiosyncratic;
               (d) excess measured against matched baseline: same stock, same trailing-vol quintile;
               (e) straddle cost sensitivity: EWMA10 vol through t-1 (excluding shock) x1.2.
  Variant count: 2 replications + 2 placebos + 3 checks = 7.
"""
import sys, pathlib, warnings
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, '/home/user/ML-Trading-Platform-Backend')
import external_bars

SKIP = set("DBC EFA FFH GLD GURU GVIP IEF NANC PSHD QUAL ROKU RSP SEQ SEQUX SMH VTV SGOV TQQQ".split())
ETF = {"SPY", "QQQ", "IWM", "DIA"}
root = pathlib.Path('/home/user/ML-Trading-Platform-Backend/state/external_bars')
syms = sorted({p.name.split('.')[0] for p in root.glob('*.json')} - SKIP)
closes = {}
for s in syms:
    try:
        d = external_bars.load_bars(s, 'etf' if s in ETF else 'equity')
    except Exception:
        d = None
    if d is None or len(d) == 0:
        continue
    closes[s] = d['Close'].astype(float).sort_index()
C = pd.DataFrame(closes).sort_index()
R = np.log(C).diff()                       # log returns (different from their simple returns)
simple = np.exp(R) - 1
nsplit = int((simple.abs() > 0.45).sum().sum())
R = R.mask(simple.abs() > 0.45)
print(f"names {C.shape[1]}, sessions {len(C)}, split-like dropped {nsplit}")

A = R.abs().values
T, N = A.shape
dates = R.index
# trailing stats via explicit loop over windows (independent implementation)
sig = np.full((T, N), np.nan); typ = np.full((T, N), np.nan)
Rv = R.values
for t in range(60, T):
    w = Rv[t-60:t]
    cnt = np.sum(~np.isnan(w), 0)
    sd = np.nanstd(w, 0, ddof=1); mu = np.nanmean(np.abs(w), 0)
    ok = cnt >= 40
    sig[t, ok] = sd[ok]; typ[t, ok] = mu[ok]
nxt = np.vstack([A[1:], np.full((1, N), np.nan)])
z = A / sig
valid = ~np.isnan(z) & ~np.isnan(nxt) & ~np.isnan(typ)
disc = (dates <= '2024-12-31')
bound = np.where(disc)[0].max()

def eps_count(idx):
    n, last = 0, -10**9
    for p in sorted(set(idx)):
        if p - last >= 10: n += 1; last = p
    return n

def test(ev, outcome, label, base=None):
    out = outcome.astype(float); out[~valid] = np.nan
    if base is None:
        base = np.full((T, N), np.nan)
        for m in (disc, ~disc):
            base[m] = np.nanmean(out[m], 0)
    ex = out - base
    row = [label]
    for nm, m in (('disc', disc), ('hold', ~disc)):
        e = ev & valid & m[:, None]
        v = np.where(e, ex, np.nan)
        keep = e.any(1)
        dm = np.nanmean(v[keep], 1)
        n = len(dm); mu = dm.mean(); t = mu / (dm.std(ddof=1) / np.sqrt(n))
        hit = np.nanmean(np.where(e, out, np.nan))
        row.append(f"{nm}: dates {n:4d} eps {eps_count(np.where(keep)[0]):3d} obs {int(e.sum()):5d} "
                   f"hit {100*hit:5.1f}% excess {100*mu:+6.2f}pp t {t:5.2f}")
    print(" | ".join(row))

o15 = nxt > 1.5 * typ
print("\n== replication ==")
unc = np.nanmean(np.where(valid, o15, np.nan)); print(f"unconditional P(|next|>1.5x typ) = {100*unc:.1f}%")
test(z > 3, o15, "3sig 1.5x")
test(z > 2, o15, "2sig 1.5x")
test(z > 3, nxt > 1.0 * typ, "3sig 1.0x")

print("\n== placebo (a): shock 20 sessions ago ==")
ev3 = (z > 3) & valid
sh = np.zeros_like(ev3); sh[20:] = ev3[:-20]
test(sh, o15, "3sig lag20")
sh1 = np.zeros_like(ev3); sh1[5:] = ev3[:-5]
test(sh1, o15, "3sig lag5 (descriptive)")

print("\n== placebo (b): shock dates shuffled within stock-year ==")
rng = np.random.default_rng(7)
years = dates.year.values
shuf = np.zeros_like(ev3)
for j in range(N):
    for y in np.unique(years):
        idx = np.where((years == y) & valid[:, j])[0]
        k = int(ev3[idx, j].sum())
        if k: shuf[rng.choice(idx, k, replace=False), j] = True
test(shuf, o15, "3sig shuffled")

print("\n== check (c): market-wide vs idiosyncratic shock dates ==")
breadth = (ev3.sum(1) / np.maximum(valid.sum(1), 1))
mw = breadth >= 0.10
print(f"market-wide 3sig dates: {mw.sum()} (stock-events {int(ev3[mw].sum())}), idio stock-events {int(ev3[~mw].sum())}")
test(ev3 & mw[:, None], o15, "3sig market-wide")
test(ev3 & ~mw[:, None], o15, "3sig idiosyncratic")
# SPY alone
j = list(R.columns).index('SPY'); spy_ev = np.zeros_like(ev3); spy_ev[:, j] = (z[:, j] > 2) & valid[:, j]
test(spy_ev, o15, "SPY 2sig")

print("\n== check (d): matched baseline = same stock, same sample, same trailing-vol quintile ==")
# quintile of sig relative to stock's own sig history (known only ex post -> descriptive control)
q = np.full((T, N), np.nan)
for j in range(N):
    s = pd.Series(sig[:, j]); q[:, j] = pd.qcut(s.rank(method='first'), 5, labels=False) if s.notna().sum() > 50 else np.nan
out = o15.astype(float); out[~valid] = np.nan
base = np.full((T, N), np.nan)
for m in (disc, ~disc):
    for j in range(N):
        for qq in range(5):
            sel = m & (q[:, j] == qq)
            if sel.any(): base[sel, j] = np.nanmean(out[sel, j])
test(ev3, o15, "3sig vs vol-quintile base", base=base)

print("\n== check (e): straddle proxy, cost model sensitivity ==")
R2 = np.nan_to_num(Rv ** 2, nan=0.0)
ew = pd.DataFrame(np.where(np.isnan(Rv), np.nan, Rv ** 2)).ewm(span=10, min_periods=10).mean().values
ew_in = np.sqrt(ew); ew_ex = np.vstack([np.full((1, N), np.nan), ew_in[:-1]])
nxt_s = np.vstack([simple.abs().values[1:], np.full((1, N), np.nan)])
for lbl, vol in (("EWMA incl shock (theirs)", ew_in), ("EWMA excl shock", ew_ex), ("trailing sigma60 excl shock", sig)):
    for prem in (1.0, 1.2):
        cost = 0.8 * prem * vol
        for ev, nm in ((valid, 'all'), (ev3, '3sig')):
            e = ev & valid & ~np.isnan(cost)
            pnl = np.where(e, nxt_s - cost, np.nan)
            res = []
            for sm, m in (('disc', disc), ('hold', ~disc)):
                keep = (e & m[:, None]).any(1)
                dm = np.nanmean(np.where(m[:, None], pnl, np.nan)[keep], 1)
                res.append(f"{sm} {100*dm.mean():+.2f}% (t {dm.mean()/(dm.std(ddof=1)/np.sqrt(len(dm))):.1f})")
            print(f"  {lbl:28s} x{prem}: {nm:4s} " + "  ".join(res))
