"""Buy an 8%-OTM put with 2 sessions to expiry at every close; sell at the next open. Black-Scholes approximation."""
import sys, math
import numpy as np, pandas as pd
from scipy.stats import norm
sys.path.insert(0, '.')
import external_bars as e
def put(S, K, v, T):
    d1 = (math.log(S/K)+0.5*v*v*T)/(v*math.sqrt(T)); d2 = d1-v*math.sqrt(T)
    return K*norm.cdf(-d2)-S*norm.cdf(-d1)
out = {}
for s in ['NVDA','TSLA','AMD','META','AAPL','AMZN','MSFT','GOOG','PLTR','COIN','MSTR','SMCI']:
    df = e.load_bars(s, 'equity'); c, o = df.Close.astype(float), df.Open.astype(float)
    rv = np.log(c).diff().rolling(20).std()*math.sqrt(252)
    m = []
    for i in range(21, len(c)-1):
        S, v = c.iloc[i], rv.iloc[i]*1.25; K = 0.92*S
        p = put(S, K, v, 2/252); cost = p + max(0.01, 0.05*p)
        if cost < 0.02: cost = 0.02                       # quotes never go below a couple of cents
        pay = max(0.0, K - o.iloc[i+1])
        m.append(pay/cost)
    m = np.array(m)
    out[s] = dict(days=len(m), win=(m > 1).mean(), x10=(m >= 10).mean(), x40=(m >= 40).mean(), mean_mult=m.mean(), best=m.max())
r = pd.DataFrame(out).T
print(r.round(4).to_string())
allm = r.days.sum()
print('overall: days', int(allm), 'win', round((r.win*r.days).sum()/allm, 4), '>=10x', round((r.x10*r.days).sum()/allm, 4), '>=40x', round((r.x40*r.days).sum()/allm,4), 'avg multiple', round((r.mean_mult*r.days).sum()/allm, 3))
nv = e.load_bars('NVDA','equity'); print(nv.loc['2026-11-10':'2026-11-12', ['Open','Low','Close']])
