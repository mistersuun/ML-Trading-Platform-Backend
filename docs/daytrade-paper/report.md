# Day-trading paper test: 5-minute opening range breakout (as of 2026-10-20)

Paper only, rules in `PREREG.md` (frozen 2026-10-15). Forward record from 2026-10-15. Warm-up replay of the pre-freeze sessions is in `warmup.md` and is not counted here. Not advice.

| Book | Start | Equity | Return | Buy-and-hold | Trades |
|---|---:|---:|---:|---:|---:|
| QQQ ORB | $25,000 | $26,272.43 | +5.09% | $24,966.60 | 4 |
| SPY ORB | $25,000 | $26,042.56 | +4.17% | $25,089.40 | 4 |

## QQQ

4 trades, win rate 25%, mean +1.32R (t = 0.61), total P&L +1,272.43, max drawdown -1.5%

| Date | Side | Shares | Entry | Exit | Exit time | Reason | R | P&L |
|---|---|---:|---:|---:|---|---|---:|---:|
| 2026-10-20 | short | 144 | 713.69 | 715.59 | 09:55 | stop | -1.04 | -276.02 |
| 2026-10-19 | short | 148 | 719.38 | 720.29 | 10:10 | stop | -0.51 | -137.27 |
| 2026-10-16 | short | 136 | 724.14 | 709.93 | 15:55 | end of day | +7.80 | +1,930.10 |
| 2026-10-15 | short | 139 | 717.75 | 719.49 | 09:35 | stop | -0.98 | -244.38 |

## SPY

4 trades, win rate 25%, mean +1.06R (t = 0.72), total P&L +1,042.56, max drawdown -0.6%

| Date | Side | Shares | Entry | Exit | Exit time | Reason | R | P&L |
|---|---|---:|---:|---:|---|---|---:|---:|
| 2026-10-20 | short | 136 | 765.43 | 766.00 | 09:35 | stop | -0.30 | -78.59 |
| 2026-10-19 | long | 136 | 767.43 | 766.86 | 09:35 | stop | -0.30 | -78.63 |
| 2026-10-16 | short | 128 | 772.18 | 761.59 | 14:50 | target (10R) | +5.45 | +1,354.30 |
| 2026-10-15 | short | 130 | 765.55 | 766.73 | 09:35 | stop | -0.62 | -154.52 |

A real edge needs at least 60 trades and t above about 2. Costs: 1 bp slippage per side, US$0.005 per share (US$1 minimum) commission; no borrow fee on shorts.
