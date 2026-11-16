"""Value of knowing direction: one-session options 0.5% and 2% OTM, right vs wrong direction (Black-Scholes approx)."""
import sys, math
import numpy as np, pandas as pd
from scipy.stats import norm
sys.path.insert(0, '.')
import external_bars as e
T = 1/252
def bs(S, K, v, call):
    d1 = (math.log(S/K)+0.5*v*v*T)/(v*math.sqrt(T)); d2 = d1-v*math.sqrt(T)
    return S*norm.cdf(d1)-K*norm.cdf(d2) if call else K*norm.cdf(-d2)-S*norm.cdf(-d1)
rows = []
for m in (0.005, 0.02):
    right, wrong = [], []
    for s in ['SPY','QQQ','NVDA','TSLA','AMD','META','AAPL','COIN','PLTR']:
        df = e.load_bars(s, 'etf' if s in ('SPY','QQQ') else 'equity'); c = df.Close.astype(float)
        rv = np.log(c).diff().rolling(20).std()*math.sqrt(252)
        for i in range(21, len(c)-1):
            S, S1, v = c.iloc[i], c.iloc[i+1], rv.iloc[i]*1.25
            for call in (True, False):
                K = S*(1+m) if call else S*(1-m)
                p = bs(S, K, v, call); cost = p + max(0.01, 0.03*p)
                mult = max(0.0, (S1-K) if call else (K-S1))/cost
                (right if (S1 >= S) == call else wrong).append(mult)
    R, W = np.mean(right), np.mean(wrong)
    for acc in (0.50, 0.52, 0.55, 0.60, 0.70, 1.00):
        rows.append(dict(otm=m, accuracy=acc, avg_return_per_bet=acc*R + (1-acc)*W - 1))
print(pd.DataFrame(rows).round(3).to_string(index=False))
