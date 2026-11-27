"""Long-shot study, part 1 (SPY): how often do cheap SPY options pay 5x / 10x, and does anything known in advance
raise that above what the option costs?

Entry at the close of day t, expiry h trading sessions later (h = 1, 2, 5). Strike k sigmas out of the money, where one
sigma = 0.92 * VIX9D * sqrt(h/252) * spot. Prices: Black-Scholes, time in trading days, with a volatility smile fitted
separately for 1-session and 5-session SPY options to 33 real IBKR quotes of 2026-11-24/25/27 (calibrate.py,
rms log error 7% and 5%); 2-4 session tenors interpolate the two fits linearly. Real quotes showed that 1-session wings
cost 2-6x a flat-smile model: lottery-ticket demand. Costs: half-spread max(0.02, 4%) on entry and exit, US$0.65 a
contract. Payoff 'hold' = intrinsic at the close of t+h; 'tp5' = sold at the first intermediate close where the
re-priced value reaches 5x the cost. Closes only. Discovery 2021-11-29..2024-12-31, hold-out 2025-01-01..2026-11-25.
Paper research only.  usage: sim_spy.py <scratch dir with VIX.json VIX9D.json VIX3M.json events.csv> [price_mult]
"""
import json, sys, math
from pathlib import Path
import numpy as np, pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import external_bars as eb  # noqa: E402

SCR = Path(sys.argv[1]); PM = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
CAL = json.load(open(SCR / 'calib.json'))


def params(tau):
    w = min(max((tau - 1) / 4, 0), 1)
    return [(1 - w) * a + w * b for a, b in zip(CAL['1'], CAL['5'])]


def ind(name):
    d = json.load(open(SCR / f'{name}.json'))
    s = pd.Series(d['close'], index=pd.to_datetime([t[:10] for t in d['time']]))
    return s[~s.index.duplicated()]


def bs(S, K, T, v, call):
    if T <= 0:
        return max(S - K, 0.0) if call else max(K - S, 0.0)
    d1 = (math.log(S / K) + 0.5 * v * v * T) / (v * math.sqrt(T)); d2 = d1 - v * math.sqrt(T)
    return S * norm.cdf(d1) - K * norm.cdf(d2) if call else K * norm.cdf(-d2) - S * norm.cdf(-d1)


def price(S, K, T, v9, call):
    a, sc, fl, cv, sp, pv = params(T * 252)
    atm = a * v9 / 100
    k = abs(math.log(K / S)) / (atm * math.sqrt(T))
    otm = (K > S) == call
    m = (max(fl, 1 - sc * k + cv * k * k) if call else 1 + sp * k + pv * k * k) if otm else 1.0
    return bs(S, K, T, atm * m, call)


def frame():
    spy = eb.load_bars('SPY', 'etf')
    spy.index = pd.to_datetime(spy.index).tz_localize(None).normalize()
    df = pd.DataFrame({'c': spy['Close'].astype(float)}).join(
        pd.DataFrame({'v9': ind('VIX9D'), 'vx': ind('VIX'), 'v3': ind('VIX3M')}), how='inner').dropna()
    df = df[df.index >= '2021-11-29']
    r = df.c.pct_change()
    df['z'] = r / r.rolling(20).std().shift(1)
    df['dd20'] = df.c / df.c.rolling(20).max() - 1
    df['term'] = df.v9 / df.vx
    df['slope'] = df.vx / df.v3
    df['dvix'] = df.vx.pct_change()
    ev = pd.read_csv(SCR / 'events.csv')
    ev['date'] = pd.to_datetime(ev['date'])
    idx = df.index
    for name in ('FOMC', 'CPI', 'NFP', 'PCE'):
        days = set(ev.loc[ev.event == name, 'date'])
        # session on which the release lands: 08:30 releases land on that day's session; FOMC 14:00 too
        df[name] = [d in days for d in idx]
    return df


def main():
    df = frame(); idx = list(df.index); rows = []
    for i, t in enumerate(idx):
        for h in (1, 2, 5):
            if i + h >= len(idx):
                continue
            S, v9 = df.c.iloc[i], df.v9.iloc[i]; T = h / 252
            win = df.iloc[i + 1:i + h + 1]
            evw = {n: bool(win[n].any()) for n in ('FOMC', 'CPI', 'NFP', 'PCE')}
            imp = params(h)[0] * v9 / 100 * math.sqrt(T)
            mv = math.log(win.c.iloc[-1] / S) / imp
            for call in (True, False):
                for k in (1.0, 1.5, 2.0, 2.5, 3.0):
                    K = S * math.exp((k if call else -k) * imp)
                    K = math.ceil(K) if call else math.floor(K)
                    p = price(S, K, T, v9, call) * PM
                    if p < 0.02:
                        continue
                    half = max(0.02, 0.04 * p)
                    cost = p + half + 0.0065
                    path = win.c.values
                    intr = max(path[-1] - K, 0) if call else max(K - path[-1], 0)
                    hold = max(intr - (half if intr > 0 else 0), 0)
                    tp = hold
                    for j in range(h - 1):
                        val = price(path[j], K, (h - j - 1) / 252, df.v9.iloc[i + j + 1], call) * PM - half
                        if val >= 5 * cost:
                            tp = val; break
                    rows.append(dict(date=t, h=h, right='C' if call else 'P', k=k, price=p, cost=cost,
                                     hold=hold / cost, tp5=tp / cost, mv=mv, z=df.z.iloc[i], vix=df.vx.iloc[i],
                                     term=df.term.iloc[i], slope=df.slope.iloc[i], dvix=df.dvix.iloc[i],
                                     dd20=df.dd20.iloc[i], dow=t.dayofweek, **evw))
    R = pd.DataFrame(rows)
    R['period'] = np.where(R.date < '2025-01-01', 'disc', 'hold')
    R.to_csv(SCR / f'spy_trades_pm{PM}.csv', index=False)
    return R


if __name__ == '__main__':
    R = main()
    pd.set_option('display.width', 220)
    T = R.groupby(['h', 'right', 'k', 'period']).agg(
        n=('hold', 'size'), med_price=('price', 'median'), p5=('hold', lambda x: (x >= 5).mean()),
        p10=('hold', lambda x: (x >= 10).mean()), ev_hold=('hold', 'mean'), ev_tp5=('tp5', 'mean'))
    print(T.round(3).to_string())
