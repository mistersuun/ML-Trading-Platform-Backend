"""Long-shot study, part 3: (a) independent episodes behind the best condition, (b) what a fixed US$100 per signal would
have done historically, (c) the chance of a 3- or 4-step all-in chain reaching 1,000x / 625x, by bootstrap from the
empirical payoff multiples. usage: chain.py <trades.csv>"""
import sys
import numpy as np, pandas as pd
R = pd.read_csv(sys.argv[1], parse_dates=['date'])
rng = np.random.default_rng(7)
for h in (1, 2, 5):
    for label, m in (('all days', R.vix > 0), ('VIX >= 25', R.vix >= 25)):
        s = R[(R.h == h) & (R.right == 'C') & (R.k == 1.0) & m].sort_values('date').reset_index(drop=True)
        # non-overlapping: take a signal only after the previous ticket expired
        keep, nxt = [], pd.Timestamp('1900-01-01')
        dates = sorted(R.date.unique())
        pos = {d: i for i, d in enumerate(dates)}
        for i, row in s.iterrows():
            if row.date >= nxt:
                keep.append(i); j = pos[row.date] + h
                nxt = dates[j] if j < len(dates) else pd.Timestamp('2100-01-01')
        x = s.loc[keep]
        pnl = (x.tp5 - 1) * 100
        mult = x.tp5.values
        p10, p5 = (mult >= 10).mean(), (mult >= 5).mean()
        sims = rng.choice(mult, size=(400000, 4))
        c3 = (np.prod(sims[:, :3], axis=1) >= 1000).mean()
        c4 = (np.prod(sims, axis=1) >= 625).mean()
        yrs = x.groupby(x.date.dt.year).tp5.agg(['size', lambda v: ((v - 1) * 100).sum()])
        print(f'{h}d 1sd call, {label}: tickets {len(x)}, >=5x {p5:.1%}, >=10x {p10:.1%}, mean {mult.mean():.2f}x, '
              f'median {np.median(mult):.2f}x, US$100 each -> total P&L {pnl.sum():+,.0f}; '
              f'chain 3 steps to 1,000x {c3:.3%} (1 in {1/max(c3,1e-9):,.0f}); 4 steps to 625x {c4:.3%}')
        print('      by year (tickets, P&L):', ' '.join(f'{y}:{int(a)}/{b:+,.0f}' for y, (a, b) in yrs.iterrows()))
