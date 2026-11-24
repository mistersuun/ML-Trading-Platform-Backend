# Independent re-computation (reviewer). Pre-set before outcomes: replicate two cells
# (A) STOCKS gap<-4% & C>O on gap day -> next-day C/C ; (B) QQQ gap z>+1 & C<O -> 3-day C/C.
# Excess two ways: minus own full-period... no: minus own same-sample mean, and minus SPY same window.
# Placebos: signal shifted +20 sessions; event dates shuffled within calendar year (200 draws).
import sys, pathlib, numpy as np, pandas as pd
sys.path.insert(0,'/home/user/ML-Trading-Platform-Backend'); import external_bars
SKIP=set('DBC EFA FFH GLD GURU GVIP IEF NANC PSHD QUAL ROKU RSP SEQ SEQUX SMH VTV SGOV TQQQ'.split())
ETF={'SPY','QQQ','IWM','DIA','TQQQ','SGOV'}
syms=sorted({p.name.split('.')[0] for p in pathlib.Path('/home/user/ML-Trading-Platform-Backend/state/external_bars').glob('*.json')})
syms=[s for s in syms if s not in SKIP]
def load(s):
    d=external_bars.load_bars(s,'etf' if s in ETF else 'equity'); d=d[~d.index.duplicated()].sort_index(); return d[(d.Open>0)&(d.Close>0)]
spy=load('SPY'); spyC=spy.Close
rows=[]; ndrop=0
# NVDA split check
nv=load('NVDA'); print('NVDA close 2024-06-06..12:', nv.Close.loc['2024-06-06':'2024-06-12'].round(2).tolist())
for s in syms:
    d=load(s); C=d.Close.values; O=d.Open.values; idx=d.index; n=len(C)
    r=np.full(n,np.nan); r[1:]=C[1:]/C[:-1]-1
    bad=np.abs(r)>0.45; ndrop+=bad.sum(); rc=np.where(bad,np.nan,r)
    for t in range(21,n):
        w=rc[t-20:t]; w=w[~np.isnan(w)]
        if len(w)<15 or bad[t]: continue
        sig=w.std(ddof=1); gap=O[t]/C[t-1]-1; oc=C[t]/O[t]-1
        rec=dict(date=idx[t],sym=s,gap=gap,z=gap/sig,oc=oc,i=t)
        for K in (1,3,5):
            if t+K<n and not bad[t+1:t+K+1].any():
                rec[f'f{K}']=C[t+K]/C[t]-1
                if idx[t] in spyC.index and idx[t+K] in spyC.index:
                    rec[f's{K}']=rec[f'f{K}']-(spyC[idx[t+K]]/spyC[idx[t]]-1)
        rows.append(rec)
P=pd.DataFrame(rows); P['disc']=P.date<=pd.Timestamp('2024-12-31')
for K in (1,3,5): P[f'x{K}']=P[f'f{K}']-P.groupby(['sym','disc'])[f'f{K}'].transform('mean')
print('split-like days',ndrop,'rows',len(P))
alld=np.array(sorted(P.date.unique())); pos={d:i for i,d in enumerate(alld)}
def ep(ds):
    n,last=0,-99
    for d in sorted(set(ds)):
        if pos[d]-last>=10: n+=1; last=pos[d]
    return n
def cl(sel,col):
    sel=sel.dropna(subset=[col]); by=sel.groupby('date')[col].mean()
    if len(by)<3: return (len(by),np.nan,np.nan,np.nan)
    return (len(by),ep(by.index),by.mean()*100,by.mean()/(by.std(ddof=1)/np.sqrt(len(by))))
STK=P[~P.sym.isin(ETF)]
cells={'A STOCKS gap<-4% & C>O fwd1':(STK,lambda d:(d.gap<-.04)&(d.oc>0),1),
       'B QQQ z>+1 & C<O fwd3':(P[P.sym=='QQQ'],lambda d:(d.z>1)&(d.oc<0),3),
       'C SPY z>+1 & C<O fwd3':(P[P.sym=='SPY'],lambda d:(d.z>1)&(d.oc<0),3)}
for name,(U,f,K) in cells.items():
    m=f(U)
    for tag,dd in (('D',True),('H',False)):
        S=U[m&(U.disc==dd)]; A=U[U.disc==dd]
        print(name,tag,'ownX(dates,ep,mean%,t)=',np.round(cl(S,f'x{K}'),2),' vsSPY=',np.round(cl(S,f's{K}'),2),
              ' hit',round((S[f'f{K}']>0).mean()*100,1),'uncond hit',round((A[f'f{K}']>0).mean()*100,1),
              ' uncond vsSPY mean%',round(A[f's{K}'].mean()*100,3))
    # placebo 1: shift signal 20 sessions later within each symbol
    U2=U.copy(); U2['m']=m.values
    U2['m20']=U2.groupby('sym')['m'].shift(20).fillna(False).astype(bool)
    for tag,dd in (('D',True),('H',False)):
        print('   placebo shift+20',tag,np.round(cl(U2[U2.m20&(U2.disc==dd)],f'x{K}'),2))
    # placebo 2: shuffle event dates within year (keep per-date counts), 200 draws
    rng=np.random.default_rng(0); ev=U2[U2.m]
    for tag,dd in (('D',True),('H',False)):
        real=cl(U2[U2.m&(U2.disc==dd)],f'x{K}')[2]
        sims=[]
        A=U2[U2.disc==dd]; bydate=A.groupby('date')
        dates_by_year={y:g.date.unique() for y,g in A.groupby(A.date.dt.year)}
        evd=ev[ev.disc==dd].groupby('date').size()
        datemean=A.groupby('date')[f'x{K}'].mean()
        for _ in range(200):
            nd=[rng.choice(dates_by_year[d.year]) for d in evd.index]
            sims.append(datemean.reindex(nd).mean()*100)
        sims=np.array(sims)
        print(f'   placebo shuffle-in-year {tag}: real {real:.2f}  sim mean {np.nanmean(sims):.2f} sd {np.nanstd(sims):.2f}  p(|sim|>=|real|) {np.mean(np.abs(sims)>=abs(real)):.3f}')
