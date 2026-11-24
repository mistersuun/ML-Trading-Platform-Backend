# Control: is the streak effect just "high-vol periods have positive next-day drift"? Compare to days with same trailing 5d cumulative drop but not a 4-streak, and vol-scaled outcomes.
import sys, math, numpy as np, pandas as pd
from scipy import stats
sys.path.insert(0,'/home/user/ML-Trading-Platform-Backend'); import external_bars
c=external_bars.load_bars('SPY','etf')['Close'].astype(float); r=(c/c.shift(1)-1).iloc[1:]
nxt=r.shift(-1); neg=(r<0).astype(int); st=neg.groupby((neg==0).cumsum()).cumsum()
vol=r.rolling(20).std().shift(0)  # trailing vol known at signal close
cum4=(1+r).rolling(4).apply(np.prod,raw=True)-1
for nm,(a,b) in {'disc':('2021-11-05','2024-12-31'),'hold':('2025-01-01','2026-11-23')}.items():
    m=(r.index>=a)&(r.index<=b)&nxt.notna(); mu=r[(r.index>=a)&(r.index<=b)].mean()
    s=m&(st>=4)
    z=((nxt-mu)/vol)[s]; t=z.mean()/(z.std()/math.sqrt(len(z)))
    # comparison: 4-day cum drop below the 4-streak median drop but NOT a 4-streak
    thr=cum4[s].median(); ctl=m&(st<4)&(cum4<=thr)
    x=(nxt-mu)[ctl]; tc=x.mean()/(x.std()/math.sqrt(len(x)))
    hits=int((nxt[s]>0).sum()); n=int(s.sum()); u=(r[(r.index>=a)&(r.index<=b)]>0).mean()
    print(nm,'vol-scaled mean z %.3f t %.2f'%(z.mean(),t),
          '| control (4d drop<=%.2f%%, no streak) n %d mean %.3f%% hit %.1f t %.2f'%(thr*100,len(x),x.mean()*100,(nxt[ctl]>0).mean()*100,tc),
          '| binom p hits %d/%d vs %.3f: %.3f'%(hits,n,u,stats.binomtest(hits,n,u,alternative='greater').pvalue))
    if nm=='hold':
        x=(nxt-mu)[s & ~((r.index>='2025-04-01')&(r.index<='2025-04-30'))]
        print('hold ex-April-2025: n %d mean %.3f%% t %.2f'%(len(x),x.mean()*100,x.mean()/(x.std()/math.sqrt(len(x)))))
