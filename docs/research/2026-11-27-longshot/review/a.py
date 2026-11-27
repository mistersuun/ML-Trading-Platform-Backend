import pandas as pd, numpy as np
R=pd.read_csv('/tmp/claude-0/-home-user/302d0d00-5f48-57c3-82ae-87f9b6d48eb7/scratchpad/longshot/spy_trades_pm1.0.csv',parse_dates=['date'])
dates=sorted(R.date.unique())
# VIX spells
V=R.drop_duplicates('date').set_index('date').vix.sort_index()
hi=(V>=25)
spell=(hi!=hi.shift()).cumsum()
sp=spell[hi]
R['spell']=R.date.map(sp)
print('VIX>=25 days',hi.sum(),'spells',sp.nunique())
print(sp.reset_index().groupby('vix').date.agg(['min','max','size']).to_string())
for h in (1,2,5):
  for right,k in (('C',1.0),('C',1.5)):
    s=R[(R.h==h)&(R.right==right)&(R.k==k)&(R.vix>=25)]
    for per in ('disc','hold'):
        x=s[s.period==per]
        if not len(x): continue
        top=x.nlargest(5,'tp5')
        ex=x.drop(top.index)
        g=x.groupby('spell').tp5.agg(['size','mean'])
        print(f'{h}d {right}{k} {per} n={len(x)} EV={x.tp5.mean():.2f} ex-top5={ex.tp5.mean():.2f} top5={[(d.strftime("%y-%m-%d"),round(v,1)) for d,v in zip(top.date,top.tp5)]}  spells={len(g)} spellEV_mean={g["mean"].mean():.2f} spells>1={(g["mean"]>1).sum()}  ex-best-spell={x[x.spell!=g["mean"].mul(g["size"]).idxmax()].tp5.mean():.2f}')
