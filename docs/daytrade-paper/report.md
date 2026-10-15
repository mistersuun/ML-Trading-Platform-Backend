# Day-trading paper test: 5-minute opening range breakout (as of 2026-10-15)

Paper only, rules in `PREREG.md` (frozen 2026-10-15). Forward record from 2026-10-15. Warm-up replay of the pre-freeze sessions is in `warmup.md` and is not counted here. Not advice.

| Book | Start | Equity | Return | Buy-and-hold | Trades |
|---|---:|---:|---:|---:|---:|
| QQQ ORB | $25,000 | $24,755.62 | -0.98% | $25,330.87 | 1 |
| SPY ORB | $25,000 | $24,845.48 | -0.62% | $25,179.45 | 1 |

## QQQ

1 trades, win rate 0%, mean -0.98R (t = nan), total P&L -244.38, max drawdown -1.0%

| Date | Side | Shares | Entry | Exit | Exit time | Reason | R | P&L |
|---|---|---:|---:|---:|---|---|---:|---:|
| 2026-10-15 | short | 139 | 717.75 | 719.49 | 09:35 | stop | -0.98 | -244.38 |

## SPY

1 trades, win rate 0%, mean -0.62R (t = nan), total P&L -154.52, max drawdown -0.6%

| Date | Side | Shares | Entry | Exit | Exit time | Reason | R | P&L |
|---|---|---:|---:|---:|---|---|---:|---:|
| 2026-10-15 | short | 130 | 765.55 | 766.73 | 09:35 | stop | -0.62 | -154.52 |

A real edge needs at least 60 trades and t above about 2. Costs: 1 bp slippage per side, US$0.005 per share (US$1 minimum) commission; no borrow fee on shorts.
