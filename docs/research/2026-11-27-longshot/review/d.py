import pandas as pd, numpy as np
R=pd.read_csv('/tmp/claude-0/-home-user/302d0d00-5f48-57c3-82ae-87f9b6d48eb7/scratchpad/longshot/spy_trades_pm1.0.csv',parse_dates=['date'])
R['ev']=R[['FOMC','CPI','NFP','PCE']].any(axis=1)
dates=sorted(R.date.unique()); pos={d:i for i,d in enumerate(dates)}
R['exp']=[dates[pos[d]+h] if pos[d]+h<len(dates) else pd.NaT for d,h in zip(R.date,R.h)]
R['exists']=(R.exp>=pd.Timestamp('2022-11-14'))|(R.exp.dt.dayofweek.isin([0,2,4]))
for h in (1,2,5):
    s=R[(R.h==h)&(R.right=='C')&(R.k==1.0)&(R.vix>=25)]
    for per in ('disc','hold'):
        x=s[s.period==per]
        print(f'{h}d C1 VIX>=25 {per}: EV {x.tp5.mean():.2f} n={len(x)} | no-event {x[~x.ev].tp5.mean():.2f} n={(~x.ev).sum()} | event {x[x.ev].tp5.mean():.2f} | listed-expiry only {x[x.exists].tp5.mean():.2f} n={x.exists.sum()}')
# all-days event split
for h in (1,5):
    s=R[(R.h==h)&(R.right=='C')&(R.k==1.0)]
    print(h,'d C1 all: event',s[s.ev].groupby('period').tp5.mean().round(2).to_dict(),'no event',s[~s.ev].groupby('period').tp5.mean().round(2).to_dict())
# chain bootstrap sensitivity
rng=np.random.default_rng(1)
def nonover(s,h):
    keep=[];nxt=pd.Timestamp('1900')
    for i,row in s.sort_values('date').iterrows():
        if row.date>=nxt:
            keep.append(i); j=pos[row.date]+h; nxt=dates[j] if j<len(dates) else pd.Timestamp('2100')
    return s.loc[keep]
def c3(m,n=400000):
    sims=rng.choice(m,size=(n,3)); return (sims.prod(1)>=1000).mean()
for h,lab,mask in ((1,'VIX>=25',R.vix>=25),(1,'all',R.vix>0),(5,'all',R.vix>0)):
    x=nonover(R[(R.h==h)&(R.right=='C')&(R.k==1.0)&mask],h)
    m=x.tp5.values; mh=x.hold.values
    top=x.nlargest(1,'tp5')
    print(f'{h}d {lab}: tickets {len(x)} c3 tp5 1/{1/max(c3(m),1e-9):,.0f}  c3 hold 1/{1/max(c3(mh),1e-9):,.0f}  ex-top1({top.date.iloc[0].date()} {top.tp5.iloc[0]:.1f}x) 1/{1/max(c3(x.drop(top.index).tp5.values),1e-9):,.0f}  analytic p10^3=1/{1/max((m>=10).mean()**3,1e-12):,.0f}')
    if lab=='VIX>=25':
        print('   >=10x dates:',[(d.date(),round(v,1)) for d,v in zip(x.date,x.tp5) if v>=10])
