"""
TEAM "shocks": large one-day moves -> bounce, fade or drift?   (PAPER research only)

PRE-REGISTRATION (written before any outcome was computed; not changed afterwards)
-------------------------------------------------------------------------------
Return r_t = Close_t / Close_{t-1} - 1 (split-adjusted, see below).
Sigma_t = std of r over the 20 sessions t-20..t-1 (today excluded), needs 20 obs.
z_t = r_t / sigma_t.  VolRatio_t = Volume_t / mean(Volume t-20..t-1).
Entry at close of shock day t; forward return F_h = Close_{t+h}/Close_t - 1, h in {1,3,5,10,20}.
Excess (stocks) = F_h(stock) - F_h(SPY).  Unconditional benchmark = same excess averaged
over ALL stock-days of the same sample (shows the survivorship drift of the universe).
Index signals: excess = F_h(index) - the index's unconditional mean F_h in that sample.
Signals (20 in total, each x 5 horizons = 100 cells; all reported in results.txt):
  S1-S3  DOWN z <= -2.5, -3, -4        S4-S6  UP z >= +2.5, +3, +4
  S7     r <= -10%                     S8     r >= +10%
  S9/S10 DOWN z<=-3 with VolRatio >= 2 / < 2   S11/S12 UP z>=+3 with VolRatio >= 2 / < 2
  S13-S16 SPY r <= -2%, >= +2%, <= -3%, >= +3%
  S17-S20 QQQ r <= -2%, >= +2%, <= -3%, >= +3%
Trade direction: down shock -> BUY ("buy the panic"); up shock -> SELL/short ("sell the euphoria").
Per-trade expectancy = direction * excess.  Next-day hit = direction*F_1(raw) > 0.
Statistics: each event date -> average excess over stocks firing that date; t-stat over dates
(cluster by date).  Episodes = event dates greedily kept if >= 10 sessions after the previous kept one.
Samples: discovery 2021-11-05..2024-12-31; hold-out 2025-01-01..2026-11-23.
"Holds" = same sign in hold-out (net of the unconditional benchmark) AND hold-out size >= 50% of
discovery size.  "Tradable" additionally needs hold-out per-trade edge >= 0.15%, hold-out t >= 2
and >= 20 hold-out episodes.  Table horizon fixed in advance: 1d and 5d.
Splits: |r| > 45% is treated as a split if Close ratio is within 6% of 2,3,4,5,8,10,15,20,1/2..1/20
(prices/volumes before it are adjusted); other |r|>45% days are dropped.
Variants tried: exactly this one specification (1 run of the pre-registered grid).
"""
import sys, pathlib, warnings
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, '/home/user/ML-Trading-Platform-Backend')
import external_bars

SKIP = set("DBC EFA FFH GLD GURU GVIP IEF NANC PSHD QUAL ROKU RSP SEQ SEQUX SMH VTV SGOV TQQQ".split())
ETF = {"SPY", "QQQ", "IWM", "DIA", "TQQQ", "SGOV"}
H = [1, 3, 5, 10, 20]
DISC_END = pd.Timestamp("2024-12-31")
syms = sorted({p.name.split('.')[0] for p in
               pathlib.Path('/home/user/ML-Trading-Platform-Backend/state/external_bars').glob('*.json')})
syms = [s for s in syms if s not in SKIP]

RATIOS = [2, 3, 4, 5, 8, 10, 15, 20]
n_split, n_drop = 0, 0
closes, vols = {}, {}
for s in syms:
    df = external_bars.load_bars(s, 'etf' if s in ETF else 'equity')
    df = df[~df.index.duplicated()].sort_index()
    c = df['Close'].astype(float).copy(); v = df['Volume'].astype(float).copy()
    r = c / c.shift(1)
    bad = []
    for d in r.index[(r - 1).abs() > 0.45]:
        x = r.loc[d]; hit = None
        for k in RATIOS:
            if abs(x * k - 1) < 0.06: hit = 1.0 / k   # price fell ~k-fold -> forward split
            if abs(x / k - 1) < 0.06: hit = float(k)  # reverse split
        if hit is not None:
            n_split += 1
            pos = c.index.get_loc(d)
            c.iloc[:pos] *= hit; v.iloc[:pos] /= hit
        else:
            n_drop += 1; bad.append(d)
    closes[s] = c; vols[s] = v
    closes[s + "__bad"] = pd.Series(1.0, index=bad) if bad else pd.Series(dtype=float)

bad_days = {s: set(closes.pop(s + "__bad").index) for s in syms}
C = pd.DataFrame(closes); V = pd.DataFrame(vols)
idx = C.index
R = C.pct_change()
for s in syms:
    for d in bad_days[s]:
        R.loc[d, s] = np.nan
SIG = R.rolling(20, min_periods=20).std().shift(1)
Z = R / SIG
VR = V / V.rolling(20, min_periods=20).mean().shift(1)
F = {h: C.shift(-h) / C - 1 for h in H}
EX = {h: F[h].sub(F[h]['SPY'], axis=0) for h in H}
stocks = [s for s in syms if s not in ETF]

def sample_mask(dates, part):
    return dates <= DISC_END if part == "disc" else dates > DISC_END

def episodes(dates):
    pos = sorted(idx.get_indexer(dates)); n, last = 0, -10**9
    for p in pos:
        if p - last >= 10: n += 1; last = p
    return n

def evaluate(mask, direction, h, part, index_sym=None):
    """mask: DataFrame bool (stocks) or Series bool (index). returns dict."""
    if index_sym is None:
        m = mask[stocks] & EX[h][stocks].notna()
        m = m & pd.DataFrame(np.repeat(sample_mask(idx, part)[:, None], len(stocks), 1), index=idx, columns=stocks)
        ex = EX[h][stocks].where(m)
        uncond_ex = EX[h][stocks].where(pd.DataFrame(np.repeat(sample_mask(idx, part)[:, None], len(stocks), 1),
                                                     index=idx, columns=stocks)).stack().mean()
        date_mean = ex.mean(axis=1).dropna()
        f1 = F[1][stocks].where(m & F[1][stocks].notna()).stack()
        allf1 = F[1][stocks].where(pd.DataFrame(np.repeat(sample_mask(idx, part)[:, None], len(stocks), 1),
                                                index=idx, columns=stocks)).stack()
        n_trades = int(m.values.sum())
    else:
        sm = pd.Series(sample_mask(idx, part), index=idx)
        f = F[h][index_sym]
        uncond_raw = f[sm].mean()
        m = mask & sm & f.notna()
        date_mean = (f[m] - uncond_raw).dropna()
        uncond_ex = 0.0
        f1 = F[1][index_sym][m & F[1][index_sym].notna()]
        allf1 = F[1][index_sym][sm]
        n_trades = int(m.sum())
    nd = len(date_mean)
    mu = date_mean.mean() if nd else np.nan
    t = mu / (date_mean.std(ddof=1) / np.sqrt(nd)) if nd > 2 and date_mean.std() > 0 else np.nan
    trade_edge = direction * (mu - uncond_ex) if nd else np.nan   # per-trade expectancy net of benchmark
    t_edge = direction * ((date_mean - uncond_ex).mean() /
                          ((date_mean - uncond_ex).std(ddof=1) / np.sqrt(nd))) if nd > 2 else np.nan
    hit = (direction * f1 > 0).mean() * 100 if len(f1) else np.nan
    uhit = (direction * allf1 > 0).mean() * 100
    return dict(nd=nd, ep=episodes(date_mean.index) if nd else 0, n=n_trades, ex=mu * 100,
                uex=uncond_ex * 100, edge=trade_edge * 100, t=t_edge, hit=hit, uhit=uhit)

vr2 = VR >= 2
SIGNALS = [
    ("S1 DOWN z<=-2.5", Z <= -2.5, +1, None), ("S2 DOWN z<=-3", Z <= -3, +1, None),
    ("S3 DOWN z<=-4", Z <= -4, +1, None), ("S4 UP z>=2.5", Z >= 2.5, -1, None),
    ("S5 UP z>=3", Z >= 3, -1, None), ("S6 UP z>=4", Z >= 4, -1, None),
    ("S7 r<=-10%", R <= -0.10, +1, None), ("S8 r>=+10%", R >= 0.10, -1, None),
    ("S9 DOWN z<=-3 vol>=2x", (Z <= -3) & vr2, +1, None), ("S10 DOWN z<=-3 vol<2x", (Z <= -3) & (VR < 2), +1, None),
    ("S11 UP z>=3 vol>=2x", (Z >= 3) & vr2, -1, None), ("S12 UP z>=3 vol<2x", (Z >= 3) & (VR < 2), -1, None),
    ("S13 SPY r<=-2%", R['SPY'] <= -0.02, +1, 'SPY'), ("S14 SPY r>=+2%", R['SPY'] >= 0.02, -1, 'SPY'),
    ("S15 SPY r<=-3%", R['SPY'] <= -0.03, +1, 'SPY'), ("S16 SPY r>=+3%", R['SPY'] >= 0.03, -1, 'SPY'),
    ("S17 QQQ r<=-2%", R['QQQ'] <= -0.02, +1, 'QQQ'), ("S18 QQQ r>=+2%", R['QQQ'] >= 0.02, -1, 'QQQ'),
    ("S19 QQQ r<=-3%", R['QQQ'] <= -0.03, +1, 'QQQ'), ("S20 QQQ r>=+3%", R['QQQ'] >= 0.03, -1, 'QQQ'),
]

print(f"Universe: {len(syms)} series ({len(stocks)} stocks + ETFs {sorted(set(syms) & ETF)}), "
      f"{len(idx)} sessions {idx[0].date()}..{idx[-1].date()}")
print(f"Split handling: {n_split} split-like moves (>|45%|) back-adjusted, {n_drop} other >|45%| days dropped")
print("Columns: dates/episodes/trades | mean excess% (date-avg) | uncond excess% | per-trade edge% "
      "(direction*(excess-uncond)) | t(edge, clustered by date) | next-day hit% vs unconditional hit%")
print("Direction: DOWN -> buy, UP -> short.  Index excess is vs the index's own sample mean.\n")
rows = []
for name, mask, d, isym in SIGNALS:
    for h in H:
        rd = evaluate(mask, d, h, "disc", isym); rh = evaluate(mask, d, h, "hold", isym)
        holds = (np.sign(rd['edge']) == np.sign(rh['edge'])) and abs(rh['edge']) >= 0.5 * abs(rd['edge']) \
                and rd['nd'] > 0 and rh['nd'] > 0
        trad = holds and rh['edge'] >= 0.15 and (rh['t'] or 0) >= 2 and rh['ep'] >= 20
        rows.append((name, h, rd, rh, holds, trad))
        if h == 1:
            print(f"--- {name}")
        print(f"  h={h:>2}  DISC {rd['nd']:>4}d/{rd['ep']:>3}ep/{rd['n']:>5}tr ex={rd['ex']:+6.2f} unc={rd['uex']:+5.2f} "
              f"edge={rd['edge']:+6.2f} t={rd['t']:+5.2f} hit={rd['hit']:5.1f}/{rd['uhit']:4.1f} | "
              f"HOLD {rh['nd']:>4}d/{rh['ep']:>3}ep/{rh['n']:>5}tr ex={rh['ex']:+6.2f} unc={rh['uex']:+5.2f} "
              f"edge={rh['edge']:+6.2f} t={rh['t']:+5.2f} hit={rh['hit']:5.1f}/{rh['uhit']:4.1f} "
              f"{'HOLDS' if holds else '-'}{' TRADABLE' if trad else ''}")

print(f"\nCells tested: {len(rows)} (20 signals x 5 horizons).  Holding cells: {sum(r[4] for r in rows)}; "
      f"tradable cells: {sum(r[5] for r in rows)}")
print("Holding cells:", [f"{r[0]} h{r[1]}" for r in rows if r[4]])
print("Tradable cells:", [f"{r[0]} h{r[1]}" for r in rows if r[5]])
# Full-sample (both periods) next-day hit rates for the headline signals
print("\nExpected false positives at |t|>=2 by chance among 100 cells x 2 samples: ~9")
