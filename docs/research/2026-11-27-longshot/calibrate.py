"""Fit the option-price model separately for 1-session and 5/6-session SPY options to real IBKR quotes (mid prices)
recorded on 2026-11-24, 11-25 and 11-27. Writes calib.json. IV = a*VIX9D at the money; OTM calls x max(fl, 1 - sc k + cv k^2);
OTM puts x (1 + sp k + pv k^2); k = |ln K/S| / (a VIX9D sqrt(T)); T in trading days / 252.
The 1-session fit uses Friday-to-Monday quotes (a weekend in between), so it may price weekday 1-session options a little
dear; 11-24 weekday quotes are included near the money."""
import json, math, sys
from scipy.stats import norm
from scipy.optimize import minimize
SCR = sys.argv[1]
J = json.load(open(f'{SCR}/VIX9D.json')); v9 = {t[:10]: c for t, c in zip(J['time'], J['close'])}
d, S = '2026-11-27', 728.09
Q1 = [('2026-11-24', 718.78, 721, 1, 1, 2.585), ('2026-11-24', 718.78, 716, 1, 0, 3.37), ('2026-11-24', 718.78, 715, 1, 0, 3.01)]
Q1 += [(d, S, K, 1, 1, m) for K, m in [(728, 4.43), (732, 2.44), (735, 1.405), (738, .74), (740, .465), (742, .285), (745, .14), (748, .075)]]
Q1 += [(d, S, K, 1, 0, m) for K, m in [(728, 4.22), (724, 2.80), (720, 1.825)]]
Q5 = [('2026-11-25', 716.70, 717, 6, 1, 12.0), ('2026-11-25', 716.70, 738, 6, 1, 2.60), ('2026-11-25', 716.70, 740, 6, 1, 2.11),
      ('2026-11-25', 716.70, 742, 6, 1, 1.685), ('2026-11-25', 716.70, 680, 6, 0, 3.095)]
Q5 += [(d, S, K, 5, 1, m) for K, m in [(750, .965), (752, .71), (755, .43), (758, .255), (760, .18), (762, .13), (765, .075)]]
Q5 += [(d, S, K, 5, 0, m) for K, m in [(710, 3.365), (705, 2.59), (700, 1.98), (695, 1.535), (690, 1.19)]]


def bs(S, K, T, v, call):
    d1 = (math.log(S / K) + .5 * v * v * T) / (v * math.sqrt(T)); d2 = d1 - v * math.sqrt(T)
    return S * norm.cdf(d1) - K * norm.cdf(d2) if call else K * norm.cdf(-d2) - S * norm.cdf(-d1)


def iv_mult(p, k, call):
    a, sc, fl, cv, sp, pv = p
    return max(fl, 1 - sc * k + cv * k * k) if call else 1 + sp * k + pv * k * k


def model(p, q):
    dd, S0, K, td, call, m = q; T = td / 252; atm = p[0] * v9[dd] / 100
    k = abs(math.log(K / S0)) / (atm * math.sqrt(T)); otm = (K > S0) == bool(call)
    return bs(S0, K, T, atm * (iv_mult(p, k, call) if otm else 1), call)


out = {}
for name, Q in (('1', Q1), ('5', Q5)):
    loss = lambda p: sum((math.log(max(model(p, q), 1e-6)) - math.log(q[5])) ** 2 for q in Q)
    r = minimize(loss, [0.95, 0.3, 0.6, 0.05, 0.15, 0.0], method='Nelder-Mead', options={'maxiter': 20000, 'xatol': 1e-4})
    out[name] = [round(x, 4) for x in r.x]
    print('tenor', name, out[name], 'rms log err', round(math.sqrt(r.fun / len(Q)), 3))
    for q in Q:
        print('  ', q[0], q[3], 'C' if q[4] else 'P', q[2], q[5], round(model(r.x, q), 3))
json.dump(out, open(f'{SCR}/calib.json', 'w'))
