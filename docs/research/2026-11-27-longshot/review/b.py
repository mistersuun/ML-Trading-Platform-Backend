import pandas as pd, numpy as np, math
from scipy.stats import norm
R=pd.read_csv('/tmp/claude-0/-home-user/302d0d00-5f48-57c3-82ae-87f9b6d48eb7/scratchpad/longshot/spy_trades_pm1.0.csv',parse_dates=['date'])
def bs(S,K,T,v):
    d1=(math.log(S/K)+.5*v*v*T)/(v*math.sqrt(T)); return S*norm.cdf(d1)-K*norm.cdf(d1-v*math.sqrt(T))
# price ratio vs call-wing/ATM ratio for 5d k=1, VIX9D=13
S=100; T=5/252; atm=0.918*0.13; K=S*math.exp(atm*math.sqrt(T))
base=bs(S,K,T,atm*0.752)
for r in (0.752,0.80,0.85,0.90,0.95,1.0):
    print('wing/ATM',r,'price x',round(bs(S,K,T,atm*r)/base,2))
for h in (1,5):
  x=R[(R.h==h)&(R.right=='C')&(R.k==1.0)].copy(); x['yr']=x.date.dt.year
  g=x.groupby('yr').agg(n=('tp5','size'),EV=('tp5','mean'),ev_hold=('hold','mean'),p_itm=('mv',lambda m:(m>1).mean()),mv_mean=('mv','mean'),mv_sd=('mv','std'),vix=('vix','median'))
  print(h,'d 1sd call by year\n',g.round(3).to_string())
x=R[(R.h==5)&(R.right=='C')&(R.k==1.0)]
# cluster SE (non-overlapping blocks of 5 sessions) for EV
for per in ('disc','hold'):
    s=x[x.period==per].sort_values('date').reset_index(drop=True)
    blk=s.index//5
    bm=s.groupby(blk).tp5.mean()
    print(per,'5d C1 EV',round(s.tp5.mean(),2),'naive se',round(s.tp5.std()/np.sqrt(len(s)),2),'block se',round(bm.std()/np.sqrt(len(bm)),2))
    # each of 5 non-overlapping offsets
    print('   offsets EV:',[round(s.iloc[o::5].tp5.mean(),2) for o in range(5)])
    top=s.nlargest(10,'tp5'); print('   top10',[(d.strftime('%y-%m-%d'),round(v,1)) for d,v in zip(top.date,top.tp5)],'ex-top10',round(s.drop(top.index).tp5.mean(),2))
