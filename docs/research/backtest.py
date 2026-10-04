import pandas as pd, numpy as np
px=pd.read_csv('data/monthly_closes_raw.csv',index_col=0); px.index=pd.PeriodIndex(px.index,freq='M')
dv=pd.read_csv('data/dividends_exdate.csv'); dv['m']=pd.to_datetime(dv.ex_date.astype(str)).dt.to_period('M')
div=dv.groupby(['m','ticker']).dividend_usd.sum().unstack().reindex(px.index).fillna(0)
for t in px.columns:
    if t not in div: div[t]=0.0
px=px.iloc[:-1]; div=div.iloc[:-1]   # drop partial Oct-2026 bar
prev=px.shift(1)
tr=((px+div[px.columns])/prev-1).iloc[1:]          # total return (div at ex-date, not reinvested intra-month)
po=(px/prev-1).iloc[1:]                              # price-only
def fixed(w,r): 
    return sum(r[k]*v for k,v in w.items())         # monthly rebalanced
ivy=['SPY','VEA','IEF','VNQ','DBC']
def faber(r):
    out=[]; sma=px.rolling(10).mean()
    for i,m in enumerate(r.index):
        pm=m-1; ret=0
        for a in ivy:
            on = True if np.isnan(sma.loc[pm,a]) else px.loc[pm,a]>sma.loc[pm,a]   # warm-up: invested
            ret+=0.2*(r.loc[m,a] if on else r.loc[m,'SHY'])
        out.append(ret)
    return pd.Series(out,index=r.index)
def dual(r):
    cum=(1+tr).cumprod(); cum.loc[px.index[0]]=1.0; cum=cum.sort_index()
    out=[]
    for m in r.index:
        pm=m-1; p12=pm-12
        if p12 not in cum.index: a='SPY'                         # warm-up: SPY
        else:
            s,v,h=[cum.loc[pm,x]/cum.loc[p12,x]-1 for x in ('SPY','VEA','SHY')]
            w='SPY' if s>=v else 'VEA'; a=w if max(s,v)>=h else 'IEF'
        out.append(r.loc[m,a])
    return pd.Series(out,index=r.index)
P={'100% SPY':{'SPY':1},'60/40 SPY/IEF':{'SPY':.6,'IEF':.4},'Three-fund 48/32/20':{'SPY':.48,'VEA':.32,'IEF':.2},
'All Weather retail':{'SPY':.3,'TLT':.4,'IEF':.15,'GLD':.075,'DBC':.075},'Permanent (SHY for cash)':{'SPY':.25,'TLT':.25,'SHY':.25,'GLD':.25},
'Golden Butterfly (VBR)':{'SPY':.2,'VBR':.2,'TLT':.2,'SHY':.2,'GLD':.2},'Ivy 5 equal weight':{k:.2 for k in ivy}}
def build(r):
    d={k:fixed(w,r) for k,w in P.items()}; d['Faber GTAA Ivy5']=faber(r); d['Dual momentum']=dual(r); return pd.DataFrame(d)
def stats(s):
    n=len(s); eq=(1+s).cumprod(); eq=pd.concat([pd.Series([1.0]),eq.reset_index(drop=True)])
    return dict(cagr=eq.iloc[-1]**(12/n)-1,vol=s.std()*12**.5,mdd=(eq/eq.cummax()-1).min(),sharpe=s.mean()/s.std()*12**.5,
                r2022=(1+s[s.index.year==2022]).prod()-1)
R=build(tr); Rp=build(po)
st=pd.DataFrame({k:stats(R[k]) for k in R}).T; sp=pd.DataFrame({k:stats(Rp[k]) for k in Rp}).T
out=st.join(sp,rsuffix='_priceonly'); out.to_csv('data/portfolio_results.csv'); R.to_csv('data/portfolio_monthly_returns_TR.csv')
pd.set_option('display.width',250); print((out*100).round(2))
# signal-live window only for timing strategies (from Nov 2022, 12m signals live)
w=R.loc['2022-11':]; print('\nCommon window Nov22-Sep26'); print((pd.DataFrame({k:stats(w[k]) for k in w}).T*100).round(2))
print('\nyield est (TTM div/price) at Sep26:')
for t in ['TLT','IEF','SHY','TIP','SPY']:
    print(t, round(div[t].loc['2025-10':'2026-09'].sum()/px[t].iloc[-1]*100,2))
for t in ['TLT','IEF','SHY','TIP']:
    print(t,'avg annual div drag (TR cagr - PO cagr) pts', round((stats(tr[t])['cagr']-stats(po[t])['cagr'])*100,2))
# correlations
rr=po[['SPY','TLT','IEF','GLD']]
for a,b in [('2021-11','2022-12'),('2023-01','2026-09'),('2021-11','2026-09')]:
    c=rr.loc[a:b].corr(); print(a,b,'SPY-TLT',round(c.loc['SPY','TLT'],2),'SPY-IEF',round(c.loc['SPY','IEF'],2),'SPY-GLD',round(c.loc['SPY','GLD'],2),'n',len(rr.loc[a:b]))
c=tr[['SPY','VEA','TLT','IEF','GLD','DBC','VNQ']].corr().round(2); print(c)
c=tr['SPY'].rolling(12).corr(tr['TLT']); print(c.dropna().iloc[[0,12,24,36,-1]].round(2))
print('2022 asset TR:',((1+tr.loc['2022'] ).prod()-1).round(3).to_dict())
