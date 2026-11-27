import sys, math, numpy as np, pandas as pd
SCR='/tmp/claude-0/-home-user/302d0d00-5f48-57c3-82ae-87f9b6d48eb7/scratchpad/longshot'
sys.argv=['x',SCR]; sys.path.insert(0,'docs/research/2026-11-27-longshot'); sys.path.insert(0,'.')
import sim_spy as S
df=S.frame(); idx=list(df.index)
def run(h, call, k, wing=1.0, exit_pct=False, cap=None):
    out=[]
    orig=S.params
    for i,t in enumerate(idx):
        if i+h>=len(idx): continue
        s0,v9=df.c.iloc[i],df.v9.iloc[i]; T=h/252
        win=df.iloc[i+1:i+h+1]; path=win.c.values
        imp=S.params(h)[0]*v9/100*math.sqrt(T)
        K=s0*math.exp((k if call else -k)*imp); K=math.ceil(K) if call else math.floor(K)
        def pr(Sx,Kx,Tx,vx):
            a,sc,fl,cv,sp,pv=S.params(Tx*252); atm=a*vx/100
            kk=abs(math.log(Kx/Sx))/(atm*math.sqrt(Tx)) if Tx>0 else 0
            otm=(Kx>Sx)==call
            m=(max(fl,1-sc*kk+cv*kk*kk) if call else 1+sp*kk+pv*kk*kk) if otm else 1.0
            if otm and call: m*=wing
            return S.bs(Sx,Kx,Tx,atm*m,call)
        p=pr(s0,K,T,v9)
        if p<0.02: continue
        half=max(0.02,0.04*p); cost=p+half+0.0065
        intr=max(path[-1]-K,0) if call else max(K-path[-1],0)
        hx=max(0.02,0.04*intr) if exit_pct else half
        hold=max(intr-(hx if intr>0 else 0),0); tp=hold
        for j in range(h-1):
            v=pr(path[j],K,(h-j-1)/252,df.v9.iloc[i+j+1])
            v-= max(0.02,0.04*v) if exit_pct else half
            if v>=5*cost: tp=v; break
        out.append((t,tp/cost,hold/cost))
    R=pd.DataFrame(out,columns=['date','tp5','hold']); R['per']=np.where(R.date<'2025-01-01','disc','hold'); R['yr']=R.date.dt.year
    return R
base=run(5,True,1.0)
print('5d C1 base',base.groupby('per').tp5.mean().round(2).to_dict())
print('5d C1 exit-spread 4% of exit',run(5,True,1.0,exit_pct=True).groupby('per').tp5.mean().round(2).to_dict())
for w in (1.06,1.13,1.2):
    R=run(5,True,1.0,wing=w)
    print(f'5d C1 wing x{w}',R.groupby('per').tp5.mean().round(2).to_dict(),'2023-24:',round(R[R.yr.isin([2023,2024])].tp5.mean(),2))
print('1d C1 base',run(1,True,1.0).groupby('per').tp5.mean().round(2).to_dict(),' exit4%',run(1,True,1.0,exit_pct=True).groupby('per').tp5.mean().round(2).to_dict())
