"""Long-shot study, part 4: how big were earnings reactions, quarter by quarter, 2021-11..2026-11?
Reaction = close of the last session before the news (report day for AMC, day before for BMO) to the close of the first
session after it ('1d') and to the close of the 3rd session after it ('3d', about Friday for a Tuesday-evening report).
Dates: SEC 8-K Item 2.02 filing dates (EDGAR), timing from the acceptance time. usage: earnings_moves.py <earnings.csv>"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import external_bars as eb
E = pd.read_csv(sys.argv[1], parse_dates=['report_date'])
rows = []
for sym, g in E.groupby('symbol'):
    try:
        b = eb.load_bars(sym, 'equity')
    except Exception as e:
        print(sym, 'no bars', e); continue
    b.index = pd.to_datetime(b.index).tz_localize(None).normalize(); c = b.Close.astype(float); idx = c.index
    for _, r in g.iterrows():
        if r.timing not in ('AMC', 'BMO'):
            continue
        d = r.report_date
        cand = idx[idx <= d] if r.timing == "AMC" else idx[idx < d]
        pre = cand[-1] if len(cand) else None
        if pre is None: continue
        i = idx.get_loc(pre)
        if i + 3 >= len(idx): continue
        rows.append(dict(sym=sym, date=d.date(), m1=c.iloc[i + 1] / c.iloc[i] - 1, m3=c.iloc[i + 3] / c.iloc[i] - 1))
M = pd.DataFrame(rows)
M.to_csv('docs/research/2026-11-27-longshot/earnings_moves.csv', index=False)
pd.set_option('display.width', 200)
S = M.groupby('sym').agg(n=('m1', 'size'), mean_abs_1d=('m1', lambda x: x.abs().mean()),
                         up10_3d=('m3', lambda x: (x >= .10).mean()), up15_3d=('m3', lambda x: (x >= .15).mean()),
                         up20_3d=('m3', lambda x: (x >= .20).mean()), dn10_3d=('m3', lambda x: (x <= -.10).mean()),
                         dn15_3d=('m3', lambda x: (x <= -.15).mean()), dn20_3d=('m3', lambda x: (x <= -.20).mean()),
                         max_3d=('m3', 'max'), min_3d=('m3', 'min'))
print(S.round(3).to_string())
for s in ('CRWD', 'MRVL', 'CRM', 'SNOW'):
    print(s, ' '.join(f'{d}:{v:+.1%}' for d, v in zip(M[M.sym == s].date, M[M.sym == s].m3)))
