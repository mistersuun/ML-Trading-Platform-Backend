"""Long-shot study, part 2: do conditions known at entry raise the payoff of cheap SPY options above their cost?
Reads the trade table written by sim_spy.py. Conditions were fixed before looking at outcomes:
  event in window (FOMC / CPI / NFP / PCE), fear (VIX >= 25; VIX9D/VIX >= 1.05 i.e. inverted short end; VIX/VIX3M >= 1
  i.e. backwardation), shock (|z| >= 2 yesterday), drawdown (SPY >= 5% below its 20-day high), calm (VIX <= 15),
  day of week (Friday entry). For each cell: n, share of trades returning >= 5x and >= 10x, mean payoff per dollar
  (EV, 1.0 = break-even), and the same in the hold-out. A condition 'helps' only if EV and the 10x rate rise in BOTH periods.
usage: conditions.py <trades.csv>
"""
import sys
import pandas as pd

R = pd.read_csv(sys.argv[1], parse_dates=['date'])
R['any_event'] = R[['FOMC', 'CPI', 'NFP', 'PCE']].any(axis=1)
C = {
    'all': R.index == R.index,
    'event in window': R.any_event,
    'FOMC in window': R.FOMC,
    'CPI in window': R.CPI,
    'NFP in window': R.NFP,
    'no event': ~R.any_event,
    'VIX >= 25': R.vix >= 25,
    'VIX <= 15': R.vix <= 15,
    'VIX9D/VIX >= 1.05': R.term >= 1.05,
    'backwardation VIX/VIX3M >= 1': R.slope >= 1,
    '|z| >= 2 yesterday': R.z.abs() >= 2,
    'down >= 5% from 20d high': R.dd20 <= -0.05,
    'VIX up >= 10% today': R.dvix >= 0.10,
    'Friday entry': R.dow == 4,
}
cells = [(1, 'C', 1.0), (2, 'C', 1.0), (5, 'C', 1.0), (1, 'P', 1.0), (1, 'P', 2.0), (2, 'P', 2.0), (5, 'P', 2.0),
         (5, 'P', 3.0)]
out = []
for h, right, k in cells:
    base = (R.h == h) & (R.right == right) & (R.k == k)
    for name, m in C.items():
        row = {'cell': f'{h}d {right} {k:g}sd', 'condition': name}
        for per in ('disc', 'hold'):
            s = R[base & m & (R.period == per)]
            row[f'n_{per}'] = len(s)
            row[f'p10_{per}'] = (s.hold >= 10).mean() if len(s) else float('nan')
            row[f'ev_{per}'] = s.tp5.mean() if len(s) else float('nan')
        out.append(row)
T = pd.DataFrame(out)
pd.set_option('display.width', 230); pd.set_option('display.max_rows', 400)
print(T.round(3).to_string(index=False))
