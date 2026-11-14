# S3 / P3 backtest: breadth washout -> QQQ bull call vertical (FROZEN SPEC v1)

Paper research only, no orders. Option prices are a **Black-Scholes approximation** (IV = clip(1.25 x RV20 of the traded ETF, 0.13, 1.00), r=4%, q=0, re-marked nightly; half-spread max($0.02, 0.5% of leg mid) per leg per side; $0.65/contract/leg/side); no historical option quotes. Breadth universe was chosen in 2026 (survivor bias). $1,000 per window; HO restarts at $1,000.

## Primary run

| metric | IS | HO | full |
|---|---|---|---|
| signals (post-lockout) | 19 | 1 | 20 |
| bets taken | 6 | 1 | 6 |
| skips | over_budget_W1=13 | none | over_budget_W1=14 |
| hit rate | +0.0% | +0.0% | +0.0% |
| mean / median net ret per bet | -60.9% / -62.5% | -29.6% / -29.6% | -60.9% / -62.5% |
| bootstrap 90% CI of mean | [-74.5%, -47.7%] | [-29.6%, -29.6%] | [-74.2%, -48.0%] |
| P(>= +100%) / P(<= -80%) | 0.00 / 0.17 | 0.00 / 0.00 | 0.00 / 0.17 |
| avg win / avg loss | n/a / -60.9% | n/a / -29.6% | n/a / -60.9% |
| take-profit exits | +0.0% | +0.0% | +0.0% |
| avg cost $ | 53.16 | 52.38 | 53.16 |
| net P&L $ | -193.59 | -15.52 | -193.59 |
| top-5 share of P&L (total ex-top5 $) | n/a (-48.93) | n/a (0.00) | n/a (-48.93) |
| terminal equity $ / CAGR | 806.41 / -5.3% | 984.48 / -1.5% | 806.41 / -4.2% |
| max drawdown | -19.4% | -2.8% | -19.4% |
| P(touch $500) / P(touch $600), 21-session block bootstrap | 0.0000 / 0.0030 | 0.0000 / 0.0000 | 0.0002 / 0.0040 |
| bet-sequence bootstrap P(<$100): n bets / 100 bets, no governor or halt | 0.0000 / 1.0000 | 0.0000 / 1.0000 | 0.0000 / 1.0000 |
| control A mean avg / p95 | -37.7% / -24.2% | n/a | -37.6% / -24.9% |
| pctile vs control A: mean / terminal | 0.4 / 0.3 | n/a | 0.1 / 0.2 |
| control A median terminal $ | 877.92 | n/a | 876.75 |
| control B (every 3rd session): n, mean, terminal | 4, -74.1%, 848.70 | 6, -51.3%, 835.34 | 4, -74.1%, 848.70 |
| strategy minus control B mean | +13.2% | +21.7% | +13.2% |
| SPY B&H $1,000: terminal / max DD | 1,455.74 / -25.4% | 1,106.02 / -9.1% | 1,610.08 / -25.4% |
| QQQ 10d ret: events / control A / all days | -2.6% / +1.4% / +0.5% | n/a | -2.6% / +1.4% / +0.5% |

## Stress and robustness (IS unless noted)

| run | bets | hit | mean | median | P(>=+100%) | net P&L $ | terminal $ | max DD | HO bets | HO mean |
|---|---|---|---|---|---|---|---|---|---|---|
| primary | 6 | +0.0% | -60.9% | -62.5% | 0.00 | -193.59 | 806.41 | -19.4% | 1 | -29.6% |
| A_iv1.5_entry | 5 | +0.0% | -57.2% | -59.6% | 0.00 | -162.53 | 837.47 | -17.5% | 1 | -35.4% |
| B_iv1.5_all | 5 | +0.0% | -57.3% | -62.6% | 0.00 | -163.14 | 836.86 | -17.0% | 1 | -42.9% |
| C_iv1.0_all | 6 | +0.0% | -61.5% | -63.4% | 0.00 | -179.69 | 820.31 | -18.0% | 1 | -11.3% |
| D_spread_x2 | 4 | +0.0% | -71.6% | -71.2% | 0.00 | -177.80 | 822.20 | -18.1% | 1 | -70.8% |
| E_delay1 | 5 | +0.0% | -56.6% | -55.9% | 0.00 | -153.81 | 846.19 | -15.4% | 1 | -17.1% |
| F_always_SPY | 6 | +0.0% | -56.5% | -60.1% | 0.00 | -173.73 | 826.27 | -17.4% | 1 | +17.9% |
| H_boilerplate_cost | 0 | n/a | n/a | n/a | n/a | n/a | 1,000.00 | +0.0% | 0 | n/a |
| G_leave_2022_out | 10 | +40.0% | +0.1% | -13.5% | 0.00 | 3.99 | 1,003.99 | -14.3% | 1 | -29.6% |
| robust_thr0.40 | 4 | +0.0% | -79.4% | -82.2% | 0.00 | -158.76 | 841.24 | -15.9% | 6 | -22.2% |
| robust_thr0.60 | 5 | +0.0% | -51.0% | -39.6% | 0.00 | -138.66 | 861.34 | -15.1% | 0 | n/a |

Stress A vs its own matched control A: control mean -40.0%, strategy percentile 5.0.

## Diagnostic (not part of the decision): signal level, fixed $80 budget, no governor

| run | n | hit | mean | median | 90% CI | matched-day control mean / p95 | pctile |
|---|---|---|---|---|---|---|---|
| primary | 19 | +26.3% | -27.5% | -38.4% | [-47.1%, -6.2%] | -23.2% / -9.6% | 31.4 |
| A_iv1.5_entry | 19 | +21.1% | -36.1% | -42.1% | [-52.8%, -18.3%] | -30.3% / -17.8% | 20.9 |

Per-signal returns (primary): -38%, -70%, -68%, -57%, -38%, -94%, -45%, +2%, -88%, +49%, +83%, -38%, +44%, -2%, -31%, +76%, -41%, -94%, -72%

## Frozen decision

- IS_mean_ge_ctrlA_p95: False
- IS_mean_gt0_stressA: False
- IS_mean_gt0_stressG: True
- P_ruin_lt_5pct: True
- GO_live_paper: False
- HO: INCONCLUSIVE: 1 HO signal(s) at the frozen 0.50 threshold (spec expected 0; 2025-11-17 breadth = 71/141 = 0.5035 under the written universe); live adoption needs >= 10 forward paper events

## Bets (primary, full window)

| signal | sym | S | K1 | W | cost $ | proceeds $ | ret | exit | days | breadth |
|---|---|---|---|---|---|---|---|---|---|---|
| 2022-01-19 | QQQ | 366.48 | 374 | 1 | 49.83 | 30.91 | -38% | time | 10 | 0.573 |
| 2022-02-22 | SPY | 429.57 | 438 | 1 | 51.73 | 15.46 | -70% | time | 10 | 0.565 |
| 2022-04-22 | QQQ | 325.4 | 332 | 1 | 50.61 | 16.23 | -68% | time | 10 | 0.534 |
| 2022-05-09 | QQQ | 297.15 | 303 | 1 | 59.28 | 25.47 | -57% | time | 10 | 0.629 |
| 2022-06-10 | QQQ | 288.84 | 295 | 1 | 55.36 | 34.08 | -38% | time | 10 | 0.707 |
| 2022-08-22 | QQQ | 314.36 | 321 | 1 | 52.12 | 3.19 | -94% | time | 10 | 0.714 |

## HO diagnostic at threshold 0.40 (non-decisive)

| signal | sym | W | cost $ | proceeds $ | ret | exit | breadth |
|---|---|---|---|---|---|---|---|
| 2025-11-17 | QQQ | 1 | 52.38 | 36.86 | -30% | time | 0.504 |
| 2025-12-31 | SPY | 2 | 66.7 | 76.23 | +14% | time | 0.468 |
| 2026-01-20 | QQQ | 2 | 75.74 | 77.98 | +3% | time | 0.411 |
| 2026-02-05 | QQQ | 1 | 47.1 | 37.91 | -19% | time | 0.404 |
| 2026-03-12 | QQQ | 1 | 48.9 | 4.03 | -92% | time | 0.411 |
| 2026-03-27 | QQQ | 1 | 49.36 | 44.65 | -10% | tp | 0.411 |

## Notes

- Entries at the decision close at the model ask/bid; exits at the model bid/ask at the close of the exit session. Exits are processed before entries on the same close.
- Take-profit trigger uses the model mid of the spread; proceeds use the bid-side value. A spread whose bid-side value is <= 0 at the time exit is treated as abandoned (proceeds $0, no exit commission).
- Expiry is calendar Friday nearest 30 DTE (holiday Fridays not shifted).
- Control A: per-calendar-year quotas equal to the strategy's bets, days with no breadth>0.50 day within +/-10 sessions, non-overlapping, same structure/budget/exits; percentile = share of draws below (+half ties).
- Stress H uses the generic task cost (half-spread max($0.05, 5% of leg price) per side), report only.
- Equity is marked at model mid while a bet is open.
- GOVERNOR LOCKOUT: after the first 6 bets (all 2022, all losers) equity is ~$806 < 0.85 x HWM, so the budget is 4% (~$32) and even a $1-wide spread (~$50) does not fit; every later signal is skipped and equity can never recover (no bets, no gains). The primary IS result is therefore 6 bets, not ~19. The signal-level diagnostic removes this.
- Spec discrepancies: (1) with the written universe (141 symbols) 2025-11-17 breadth is 71/141 = 0.5035 > 0.50, so HO has 1 signal, not 0 (the spec claims exactly 0.50; including FFH/PSHD gives 71/143 = 0.4965). The written rule was followed. (2) In 2022 W=$1 was chosen (W=$2 cost > $80 at 2022 IV), not the expected W=$2.
- Per-leg half-spread of 0.5% of leg mid makes deep-ITM exits costly (e.g. 2026-03-27 at thr 0.40: TP on mid 0.86 but bid-side proceeds 0.48/share). Under stress H costs the $1-wide spread costs more than its width, so 0 bets.
- Runtime 7.0 s; seed 20261114; 1000 control draw sets; 10000 bootstrap paths.
