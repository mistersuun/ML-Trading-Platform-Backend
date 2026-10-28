# Day-trading paper test: 5-minute opening range breakout (as of 2026-10-28)

Paper only, rules in `PREREG.md` (frozen 2026-10-15). Forward record from 2026-10-15. Warm-up replay of the pre-freeze sessions is in `warmup.md` and is not counted here. Not advice.

| Book | Start | Equity | Return | Buy-and-hold | Trades |
|---|---:|---:|---:|---:|---:|
| QQQ ORB | $25,000 | $25,765.20 | +3.06% | $25,245.63 | 10 |
| SPY ORB | $25,000 | $25,800.58 | +3.20% | $25,196.41 | 10 |

## QQQ

10 trades, win rate 20%, mean +0.34R (t = 0.38), total P&L +765.20, max drawdown -4.0%

| Date | Side | Shares | Entry | Exit | Exit time | Reason | R | P&L |
|---|---|---:|---:|---:|---|---|---:|---:|
| 2026-10-28 | long | 143 | 724.27 | 723.10 | 09:35 | stop | -0.66 | -169.99 |
| 2026-10-27 | long | 122 | 716.75 | 714.54 | 09:50 | stop | -1.04 | -272.00 |
| 2026-10-26 | long | 131 | 707.52 | 712.05 | 15:55 | end of day | +2.31 | +591.17 |
| 2026-10-23 | long | 146 | 702.45 | 701.26 | 09:35 | stop | -0.68 | -175.79 |
| 2026-10-22 | long | 145 | 714.22 | 712.38 | 09:45 | stop | -1.03 | -269.19 |
| 2026-10-21 | long | 145 | 722.41 | 720.97 | 09:35 | stop | -0.81 | -211.43 |
| 2026-10-20 | short | 144 | 713.69 | 715.59 | 09:55 | stop | -1.04 | -276.02 |
| 2026-10-19 | short | 148 | 719.38 | 720.29 | 10:10 | stop | -0.51 | -137.27 |
| 2026-10-16 | short | 136 | 724.14 | 709.93 | 15:55 | end of day | +7.80 | +1,930.10 |
| 2026-10-15 | short | 139 | 717.75 | 719.49 | 09:35 | stop | -0.98 | -244.38 |

## SPY

10 trades, win rate 20%, mean +0.33R (t = 0.56), total P&L +800.58, max drawdown -2.2%

| Date | Side | Shares | Entry | Exit | Exit time | Reason | R | P&L |
|---|---|---:|---:|---:|---|---|---:|---:|
| 2026-10-28 | short | 134 | 771.26 | 771.78 | 12:50 | stop | -0.27 | -70.92 |
| 2026-10-27 | long | 135 | 767.60 | 767.01 | 09:45 | stop | -0.31 | -80.77 |
| 2026-10-26 | long | 134 | 762.77 | 765.21 | 15:55 | end of day | +1.27 | +325.93 |
| 2026-10-23 | long | 135 | 760.07 | 759.64 | 09:35 | stop | -0.23 | -58.97 |
| 2026-10-22 | long | 134 | 767.34 | 765.87 | 09:45 | stop | -0.77 | -198.09 |
| 2026-10-21 | short | 135 | 770.14 | 771.31 | 09:45 | stop | -0.61 | -159.16 |
| 2026-10-20 | short | 136 | 765.43 | 766.00 | 09:35 | stop | -0.30 | -78.59 |
| 2026-10-19 | long | 136 | 767.43 | 766.86 | 09:35 | stop | -0.30 | -78.63 |
| 2026-10-16 | short | 128 | 772.18 | 761.59 | 14:50 | target (10R) | +5.45 | +1,354.30 |
| 2026-10-15 | short | 130 | 765.55 | 766.73 | 09:35 | stop | -0.62 | -154.52 |

A real edge needs at least 60 trades and t above about 2. Costs: 1 bp slippage per side, US$0.005 per share (US$1 minimum) commission; no borrow fee on shorts.
