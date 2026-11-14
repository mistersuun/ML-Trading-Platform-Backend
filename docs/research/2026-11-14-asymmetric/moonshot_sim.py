"""How often does an all-in roll of one-session options turn $700 into $1M in 5 sessions? Black-Scholes approximation."""
import sys, math
import numpy as np, pandas as pd
from scipy.stats import norm
sys.path.insert(0, '.')
import external_bars as e
SYMS = ['SPY','QQQ','TSLA','NVDA','AMD','MSTR','COIN','PLTR','SMCI']
T = 1/252
def bs(S, K, vol, call):
    if vol <= 0: return max(0.0, (S-K) if call else (K-S))
    d1 = (math.log(S/K) + 0.5*vol*vol*T)/(vol*math.sqrt(T)); d2 = d1 - vol*math.sqrt(T)
    return S*norm.cdf(d1)-K*norm.cdf(d2) if call else K*norm.cdf(-d2)-S*norm.cdf(-d1)
rows = []
for s in SYMS:
    df = e.load_bars(s, 'etf' if s in ('SPY','QQQ') else 'equity')
    c = df['Close'].astype(float)
    rv = np.log(c).diff().rolling(20).std()*math.sqrt(252)
    for m in (0.005, 0.01, 0.02):
        for mode in ('calls', 'follow', 'hindsight'):
            mult = []
            for i in range(21, len(c)-1):
                S, S1, v = c.iloc[i], c.iloc[i+1], rv.iloc[i]*1.25
                if mode == 'calls': call = True
                elif mode == 'follow': call = c.iloc[i] >= c.iloc[i-1]
                else: call = S1 >= S                      # perfect foresight of direction (upper bound)
                K = S*(1+m) if call else S*(1-m)
                px = bs(S, K, v, call)
                cost = px + max(0.01, 0.03*px)            # half-spread
                pay = max(0.0, (S1-K) if call else (K-S1))
                mult.append(pay/cost if cost > 0.005 else np.nan)
            mult = pd.Series(mult).dropna().values
            # non-overlapping 5-session all-in rolls (commissions ignored: they only make it worse)
            weeks = [np.prod(mult[j:j+5]) for j in range(0, len(mult)-5, 5)]
            w = np.array(weeks)
            rows.append(dict(sym=s, otm=m, mode=mode, days=len(mult), day_win=(mult>1).mean(), best_day=mult.max(),
                             weeks=len(w), busted=(w<0.1).mean(), x2=(w>=2).mean(), x10=(w>=10).mean(),
                             x1428=(w>=1000000/700).mean(), best_week=w.max()))
r = pd.DataFrame(rows)
pd.set_option('display.width', 200)
print(r.round(4).to_string(index=False))
g = r.groupby('mode')[['busted','x2','x10','x1428']].mean().round(4); print(g)
print('weeks reaching 1428x:', int((r.x1428*r.weeks).sum()), 'of', int(r.weeks.sum()))
