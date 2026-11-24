# Cell A, excess over same-date cross-sectional mean of all stocks (removes market-day effect)
exec(open('indep.py').read().split("STK=P[~P.sym.isin(ETF)]")[0])
STK=P[~P.sym.isin(ETF)].copy()
STK['cs1']=STK.f1-STK.groupby('date').f1.transform('mean')
m=(STK.gap<-.04)&(STK.oc>0)
for tag,dd in (('D',True),('H',False)):
    print('A xsec',tag,np.round(cl(STK[m&(STK.disc==dd)],'cs1'),2))
# leave-out the worst H episodes: drop April 2025
H=STK[m&(~STK.disc)]
print('A ownX H excl Apr-2025',np.round(cl(H[~((H.date>='2025-03-28')&(H.date<='2025-04-30'))],'x1'),2))
print('A ownX H excl Nov-2026',np.round(cl(H[H.date<'2026-11-01'],'x1'),2))
