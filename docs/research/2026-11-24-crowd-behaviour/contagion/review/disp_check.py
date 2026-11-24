# Added check (variant 6): is S6 just "high within-group dispersion -> reversal"? Baseline = non-event days whose
# follower dispersion (std of day-0 excess) is >= the median dispersion of that leader's event days.
exec(open('recheck.py').read().split("ev = {L: events(L)")[0])
ev = {L: events(L) for L in G}
disp = {L: ex[G[L].split()].std(axis=1) for L in G}
hd = {}
for L in G:
    thr = disp[L].loc[ev[L]].median()
    hd[L] = idx[60:-1][(disp[L].loc[idx[60:-1]] >= thr).values].difference(ev[L])
print({L: len(v) for L, v in hd.items()})
stat(pool(hd, nxt), 'high-dispersion NON-event days, next c2c')
stat(pool(hd, nxto), 'high-dispersion NON-event days, next o2c')
s = pool(ev, nxt)
for b, m in (('disc', s.index <= DISC), ('hold', s.index > DISC)):
    x = s[m]; e = 0; last = -99
    for d in x.index:
        i = idx.get_loc(d)
        if i - last >= 10: e += 1; last = i
    print(b, 'episodes', e)
