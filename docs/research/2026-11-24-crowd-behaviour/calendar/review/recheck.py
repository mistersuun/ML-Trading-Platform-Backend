# Independent recheck of SPY next-day after >=4 down closes. Pre-reg: k in {3,4,5}, assets SPY QQQ, as in study.
import sys, math, numpy as np, pandas as pd
from scipy import stats
sys.path.insert(0,'/home/user/ML-Trading-Platform-Backend'); import external_bars
def load(s): 
    d=external_bars.load_bars(s,'etf'); c=d['Close'].astype(float); c=c[~c.index.duplicated()].sort_index(); return c
out={}
for s in ['SPY','QQQ']:
    c=load(s); r=c/c.shift(1)-1; r=r.iloc[1:]
    print(s,len(r),r.index[0].date(),r.index[-1].date(),'|r|>45%:',int((r.abs()>.45).sum()))
    out[s]=r
spy=out['SPY']
print('last 8 SPY %:',[(str(d.date()),round(v*100,2)) for d,v in spy.tail(8).items()])
def streak(r):
    n=np.zeros(len(r),int); 
    for i,v in enumerate(r.values):
        n[i]=(n[i-1]+1 if i>0 else 1) if v<0 else 0
    return pd.Series(n,r.index)
def ttest(x):
    x=np.asarray(x); n=len(x); t=x.mean()/(x.std(ddof=1)/math.sqrt(n)); return t, 2*stats.t.sf(abs(t),n-1)
def eps(posi):
    k=0; last=-99
    for p in posi:
        if p-last>=10: k+=1; last=p
    return k
def run(r, k, shift=0, label=''):
    st=streak(r); sig=(st>=k).values
    if shift: sig=np.roll(sig,shift); sig[:shift]=False
    res={}
    for nm,(a,b) in {'disc':('2021-11-05','2024-12-31'),'hold':('2025-01-01','2026-11-23')}.items():
        nxt=r.shift(-1)  # next-day return, aligned to signal day
        m=(r.index>=a)&(r.index<=b)
        mu=r[m].mean()
        sel=sig & m & nxt.notna().values
        x=(nxt[sel]-mu)
        pos=np.where(sel)[0]
        t,p=ttest(x) if len(x)>2 else (np.nan,np.nan)
        res[nm]=dict(n=len(x),ep=eps(pos),mean=round(x.mean()*100,3),hit=round((nxt[sel]>0).mean()*100,1),uhit=round((r[m]>0).mean()*100,1),t=round(t,2),p=round(p,4))
    return res
for s in ['SPY','QQQ']:
    for k in (3,4,5): print(s,k,run(out[s],k))
# Placebo 1: shift signal by 20 sessions
for sh in (20,40,60): print('placebo shift',sh,'SPY k4',run(spy,4,shift=sh))
# Placebo 2: shuffle event dates within same calendar year (keep count per year), 2000 draws
rng=np.random.default_rng(0)
st=streak(spy); nxt=spy.shift(-1)
for nm,(a,b) in {'disc':('2021-11-05','2024-12-31'),'hold':('2025-01-01','2026-11-23')}.items():
    m=(spy.index>=a)&(spy.index<=b)&nxt.notna().values
    mu=spy[(spy.index>=a)&(spy.index<=b)].mean()
    sig=(st>=4).values&m
    obs=(nxt[sig]-mu).mean()
    yrs=spy.index.year
    draws=[]
    for _ in range(2000):
        tot=[]
        for y in np.unique(yrs[sig]):
            cand=np.where(m&(yrs==y))[0]; kk=int((sig&(yrs==y)).sum())
            tot+=list(rng.choice(cand,kk,replace=False))
        draws.append((nxt.iloc[tot]-mu).mean())
    draws=np.array(draws)
    print(nm,'obs %.3f%%'%(obs*100),'shuffle mean %.3f%% sd %.3f%%'%(draws.mean()*100,draws.std()*100),'p(one-sided)=%.3f'%((draws>=obs).mean()))
# Control: next day after ANY down day of size >= the typical 4-streak cumulative? Simple: after a down day (k=1) and k=2
for k in (1,2): print('SPY k',k,run(spy,k))
# Sensitivity: drop biggest single outcome per sample
st=streak(spy)
for nm,(a,b) in {'disc':('2021-11-05','2024-12-31'),'hold':('2025-01-01','2026-11-23')}.items():
    m=(spy.index>=a)&(spy.index<=b); mu=spy[m].mean()
    sel=(st>=4).values&m&nxt.notna().values
    x=(nxt[sel]-mu).sort_values()
    print(nm,'top3 outcomes %',[ (str(d.date()),round(v*100,2)) for d,v in x.tail(3).items()],
          'mean ex top1 %.3f ex top2 %.3f'%(x.iloc[:-1].mean()*100,x.iloc[:-2].mean()*100),
          'median %.3f'%(x.median()*100))
    # episode-level: first trade per episode only (streak first reaching 4)
    first=(st==4).values&m&nxt.notna().values
    y=nxt[first]-mu; t,p=ttest(y)
    print(nm,'first-day-of-streak-only n',len(y),'mean %.3f'%(y.mean()*100),'hit %.1f'%((nxt[first]>0).mean()*100),'t %.2f'%t)
