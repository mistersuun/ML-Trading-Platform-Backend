# Day-trading paper test: 5-minute opening range breakout (as of 2026-10-19)

Paper only, rules in `PREREG.md` (frozen 2026-10-15). Forward record from 2026-10-15. Warm-up replay of the pre-freeze sessions is in `warmup.md` and is not counted here. Not advice.

| Book | Start | Equity | Return | Buy-and-hold | Trades |
|---|---:|---:|---:|---:|---:|
| QQQ ORB | $25,000 | $26,548.45 | +6.19% | $24,989.91 | 3 |
| SPY ORB | $25,000 | $26,121.15 | +4.48% | $25,009.79 | 3 |

## QQQ

3 trades, win rate 33%, mean +2.10R (t = 0.74), total P&L +1,548.45, max drawdown -1.0%

| Date | Side | Shares | Entry | Exit | Exit time | Reason | R | P&L |
|---|---|---:|---:|---:|---|---|---:|---:|
| 2026-10-19 | short | 148 | 719.38 | 720.29 | 10:10 | stop | -0.51 | -137.27 |
| 2026-10-16 | short | 136 | 724.14 | 709.93 | 15:55 | end of day | +7.80 | +1,930.10 |
| 2026-10-15 | short | 139 | 717.75 | 719.49 | 09:35 | stop | -0.98 | -244.38 |

## SPY

3 trades, win rate 33%, mean +1.51R (t = 0.77), total P&L +1,121.15, max drawdown -0.6%

| Date | Side | Shares | Entry | Exit | Exit time | Reason | R | P&L |
|---|---|---:|---:|---:|---|---|---:|---:|
| 2026-10-19 | long | 136 | 767.43 | 766.86 | 09:35 | stop | -0.30 | -78.63 |
| 2026-10-16 | short | 128 | 772.18 | 761.59 | 14:50 | target (10R) | +5.45 | +1,354.30 |
| 2026-10-15 | short | 130 | 765.55 | 766.73 | 09:35 | stop | -0.62 | -154.52 |

A real edge needs at least 60 trades and t above about 2. Costs: 1 bp slippage per side, US$0.005 per share (US$1 minimum) commission; no borrow fee on shorts.
