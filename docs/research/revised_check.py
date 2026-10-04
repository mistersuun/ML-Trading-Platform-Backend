# In-sample sanity check of the critic's revised core (Oct-2021..Sep-2026, IBKR total return). NOT evidence of robustness.
import numpy as np, pandas as pd, backtest as b
r=b.tr
P={'60/40':{'SPY':.6,'IEF':.4},
 'Opus core':{'SPY':.35,'VEA':.15,'IEF':.15,'TIP':.10,'SHY':.10,'GLD':.10,'DBC':.05},
 'Revised core (critic)':{'SPY':.32,'VEA':.18,'VWO':.05,'IEF':.13,'TLT':.07,'TIP':.07,'SHY':.08,'GLD':.05,'DBC':.05},
 'Revised core, no gold/commod':{'SPY':.32,'VEA':.18,'VWO':.05,'IEF':.16,'TLT':.09,'TIP':.07,'SHY':.13},
 '100% SPY':{'SPY':1}}
rf=r['SHY']
rows=[]
for n,w in P.items():
    s=b.fixed(w,r); ex=s-rf; v=b.stats(s)
    v['excess_sharpe_vs_SHY']=round(ex.mean()/ex.std()*np.sqrt(12),2); v['name']=n; rows.append(v)
rev=b.fixed(P['Revised core (critic)'],r); core=b.fixed(P['Opus core'],r)
print(pd.DataFrame(rows).set_index('name').to_string())
d=rev-b.fixed(P['60/40'],r); print('revised minus 60/40 monthly diff t =',round(d.mean()/d.std()*np.sqrt(len(d)),2))
