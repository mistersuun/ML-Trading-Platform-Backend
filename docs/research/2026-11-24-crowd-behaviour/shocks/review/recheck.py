# Independent re-check of team "shocks" headline: UP z>=3 -> 5d excess vs SPY (short side loses?)
# Pre-registered in this review: signal z>=3 (prior-20 std, today excluded); h=1,3,5; excess = stock - SPY,
# net of (a) universe pooled uncond excess and (b) that stock's own same-sample mean excess.
# Cluster by date. Placebos: shift signal +20 sessions; shuffle event dates within calendar year (200 reps).
# Variant: entry at next open. Variants counted: 2 benchmarks x 3 horizons x 2 entries + 2 placebos.
import sys, pathlib, numpy as np, pandas as pd, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, '/home/user/ML-Trading-Platform-Backend'); import external_bars
SKIP=set("DBC EFA FFH GLD GURU GVIP IEF NANC PSHD QUAL ROKU RSP SEQ SEQUX SMH VTV SGOV TQQQ".split())
ETF={"SPY","QQQ","IWM","DIA","TQQQ","SGOV"}
syms=sorted({p.name.split('.')[0] for p in pathlib.Path('/home/user/ML-Trading-Platform-Backend/state/external_bars').glob('*.json')})
syms=[s for s in syms if s not in SKIP]
spy=external_bars.load_bars('SPY','etf'); spy=spy[~spy.index.duplicated()].sort_index()
cal=spy.index
recs=[]; ndrop=0; ngap=0
for s in syms:
    if s in ETF: continue
    d=external_bars.load_bars(s,'equity'); d=d[~d.index.duplicated()].sort_index()
    d=d.reindex(cal)  # align to SPY calendar, no fill
    c=d['Close'].astype(float); o=d['Open'].astype(float)
    r=c/c.shift(1)-1   # NaN across gaps (no forward fill)
    ngap+= int((c.isna() & c.shift(1).notna() & c.shift(-1).notna()).sum())
    big=r.abs()>0.45; ndrop+=int(big.sum()); r[big]=np.nan
    sig=r.rolling(20,min_periods=20).std().shift(1)
    z=r/sig
    out=pd.DataFrame({'z':z},index=cal)
    for h in (1,3,5):
        out[f'ex{h}']=(c.shift(-h)/c-1)-(spy.Close.shift(-h)/spy.Close-1)
        out[f'exo{h}']=(c.shift(-h)/o.shift(-1)-1)-(spy.Close.shift(-h)/spy.Open.shift(-1)-1)
    out['f1']=c.shift(-1)/c-1
    out['sym']=s; recs.append(out)
P=pd.concat(recs); P.index.name='date'; P=P.reset_index()
P['hold']=P.date>'2024-12-31'
for h in (1,3,5):
    P[f'own{h}']=P[f'ex{h}']-P.groupby(['sym','hold'])[f'ex{h}'].transform('mean')
    P[f'uni{h}']=P[f'ex{h}']-P.groupby('hold')[f'ex{h}'].transform('mean')
    P[f'uno{h}']=P[f'exo{h}']-P.groupby('hold')[f'exo{h}'].transform('mean')
print(f"stocks={P.sym.nunique()} dropped |r|>45%: {ndrop}; isolated missing bars: {ngap}")
def episodes(dates):
    pos=np.sort(cal.get_indexer(pd.DatetimeIndex(dates).unique())); n=0; last=-99
    for p in pos:
        if p-last>=10: n+=1; last=p
    return n
def stat(ev,col):
    e=ev.dropna(subset=[col]); dm=e.groupby('date')[col].mean()
    n=len(dm); mu=dm.mean(); t=mu/(dm.std(ddof=1)/np.sqrt(n)) if n>2 else np.nan
    return n,episodes(dm.index),mu*100,t,len(e)
def report(mask,label):
    ev=P[mask]
    for col in ['uni1','uni3','uni5','own5','uno5']:
        line=f"{label:26s} {col:5s}"
        for part,pm in (('DISC',~ev.hold),('HOLD',ev.hold)):
            n,ep,mu,t,nt=stat(ev[pm],col); line+=f" | {part} {n:4d}d {ep:3d}ep {nt:5d}tr long-excess={mu:+.2f}% t={t:+.2f}"
        print(line)
    for part,pm in (('DISC',~ev.hold),('HOLD',ev.hold)):
        e=ev[pm].dropna(subset=['f1'])
        dh=e.groupby('date')['f1'].apply(lambda x:(x>0).mean()).mean()*100
        print(f"   {part} next-day UP rate pooled={(e.f1>0).mean()*100:.1f}% date-avg={dh:.1f}%  (uncond {(P[P.hold==(part=='HOLD')].f1.dropna()>0).mean()*100:.1f}%)")
print("\n== Headline: UP z>=3 (long-side excess; team's 'short' edge is minus this) ==")
report(P.z>=3,"UP z>=3")
report(P.z<=-3,"DOWN z<=-3")
# per-year breakdown, 5d uni
ev=P[P.z>=3].dropna(subset=['uni5']); dm=ev.groupby('date').uni5.mean()
print("\nUP z>=3 5d date-mean excess by year:", {y:round(v*100,2) for y,v in dm.groupby(dm.index.year).mean().items()},
      "n dates:",dict(dm.groupby(dm.index.year).size()))
# concentration: drop top 5 dates
for part,pm in (('DISC',dm.index<='2024-12-31'),('HOLD',dm.index>'2024-12-31')):
    x=dm[pm].sort_values(); x2=x.iloc[:-5]
    print(f"  {part}: mean {x.mean()*100:+.2f}%, median {x.median()*100:+.2f}%, drop top-5 dates {x2.mean()*100:+.2f}% t={x2.mean()/(x2.std()/np.sqrt(len(x2))):.2f}")
# Placebo 1: shift signal 20 sessions later (event "fires" 20 sessions after a real shock)
P=P.sort_values(['sym','date'])
P['zs']=P.groupby('sym').z.shift(20)
print("\n== Placebo: signal shifted +20 sessions ==")
report(P.zs>=3,"UP z>=3 shifted+20")
# Placebo 2: shuffle event dates within calendar year per stock (keep count per stock-year)
rng=np.random.default_rng(7); P['yr']=P.date.dt.year
real=P[P.z>=3]
res={'DISC':[],'HOLD':[]}
grp={k:g.index.values for k,g in P.dropna(subset=['uni5']).groupby(['sym','yr'])}
cnt=real.dropna(subset=['uni5']).groupby(['sym','yr']).size()
for rep in range(200):
    pick=np.concatenate([rng.choice(grp[k],size=min(n,len(grp[k])),replace=False) for k,n in cnt.items() if k in grp])
    e=P.loc[pick]
    for part,pm in (('DISC',~e.hold),('HOLD',e.hold)):
        res[part].append(e[pm].groupby('date').uni5.mean().mean()*100)
for part in res:
    a=np.array(res[part]); real_mu=stat(real[real.hold==(part=='HOLD')],'uni5')[2]
    print(f"Shuffle-within-year placebo 5d {part}: placebo mean {a.mean():+.2f}% sd {a.std():.2f}%  real {real_mu:+.2f}%  p(placebo>=real)={(a>=real_mu).mean():.3f}")
