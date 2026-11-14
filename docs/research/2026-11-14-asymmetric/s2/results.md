# S2 backtest: RS-leader pullback -> 1-lot bull call vertical (FROZEN SPEC v1)

Paper research only, no orders. Option prices are a **Black-Scholes approximation** (IV = clip(1.25 x RV20, 0.15, 2.50), r=4%, half-spread max($0.05, 3% of mid) per leg per side, $0.65/contract/leg/side); no historical option quotes. Universe chosen in 2026 (survivor bias): compare with controls, not absolutes. First possible signal is ~Nov 2022 (252-bar eligibility on data starting 2021-11-05).

## Main results (primary run, $1,000 engine, HO restarted at $1,000)

| metric | IS | HO | full |
|---|---|---|---|
| raw signals (post-lockout) | 318 | 119 | 437 |
| distinct signal weeks | 115 | 40 | 155 |
| bets taken | 11 | 4 | 11 |
| skips | max_concurrent=3, one_per_night=6, over_budget=298 | one_per_night=1, over_budget=114 | max_concurrent=3, one_per_night=6, over_budget=417 |
| hit rate | +0.09 | +0.00 | +0.09 |
| mean net ret/bet | -0.431 | -0.614 | -0.431 |
| median net ret/bet | -0.635 | -0.615 | -0.635 |
| week-block 90% CI of mean | [-0.573, -0.256] | [-0.721, -0.508] | [-0.573, -0.257] |
| P(ret >= +100%) | +0.000 | +0.000 | +0.000 |
| P(ret <= -80%) | +0.091 | +0.000 | +0.091 |
| avg win / avg loss | +0.177 / -0.492 | n/a / -0.614 | +0.177 / -0.492 |
| take-profit hit rate | +0.00 | +0.00 | +0.00 |
| avg debit $ | +48.35 | +63.98 | +48.35 |
| net P&L $ | -238.74 | -158.29 | -238.74 |
| top-5 share of P&L (or total ex-top5 if total<=0) | -212.89 (total_ex_top5) | +0.00 (total_ex_top5) | -212.89 (total_ex_top5) |
| terminal equity $ | 761.26 | 841.71 | 761.26 |
| CAGR (nominal window) | -0.066 | -0.155 | -0.053 |
| max drawdown | -0.259 | -0.158 | -0.259 |
| P(ruin: touch $500), 21-session block bootstrap | +0.0182 | +0.0000 | +0.0205 |
| P(touch $600), block bootstrap | +0.0981 | +0.0060 | +0.0952 |
| P(<$100), bet-sequence bootstrap | +0.0000 | +0.0000 | +0.0000 |
| pctile vs matched (b): mean / terminal | 99.5 / 96.9 | 90.6 / 77.0 | 99.5 / 96.9 |
| matched (b) mean ret/bet avg, p95 | -0.615, -0.496 | -0.731, -0.598 | -0.615, -0.496 |
| matched (b) median terminal $ | 728.90 | 822.68 | 728.90 |
| pctile vs random 3% (a): mean / terminal | 100.0 / 100.0 | 80.4 / 100.0 | 100.0 / 100.0 |
| random (a) mean ret/bet avg; median terminal | -0.652; 602.54 | -0.696; 654.59 | -0.659; 594.07 |
| signal-level ($80 fixed budget) mean, n, fit rate | -0.529, 145, 0.46 | -0.670, 10, 0.08 | -0.538, 155, 0.35 |
| signal-level pctile vs (b) / (a) | 70.2 / 14.0 | 93.0 / 0.0 | 75.0 / 9.3 |
| signal-level control avg (b) / (a) | -0.543 / -0.519 | -0.764 / -0.558 | -0.554 / -0.527 |
| 10-day underlying ret: signals vs matched | +0.0331 vs +0.0312 | +0.0338 vs +0.0133 | +0.0333 vs +0.0263 |
| survivor-excl engine mean (n) vs matched | -0.407 (8) vs -0.632 | -0.614 (4) vs -0.722 | -0.407 (8) vs -0.632 |
| survivor-excl signal-level mean (n) vs matched, pctile | -0.524 (80) vs -0.538, 65.5 | -0.670 (10) vs -0.761, 90.6 | -0.540 (90) vs -0.558, 70.9 |
| diagnostic 1.05x long call mean / median (same bets) | +0.098 / +0.015 | -0.036 / -0.439 | +0.098 / +0.015 |
| SPY B&H $1,000 terminal / max DD | 1455.74 / -0.254 | 1106.02 / -0.091 | 1610.08 / -0.254 |

## Stress runs (strategy only)

| run | IS n | IS mean | IS terminal | HO n | HO mean | HO terminal | full mean | full maxDD |
|---|---|---|---|---|---|---|---|---|
| primary | 11 | -0.431 | 761.26 | 4 | -0.614 | 841.71 | -0.431 | -0.259 |
| A_iv1.5entry | 11 | -0.441 | 746.06 | 4 | -0.635 | 826.79 | -0.441 | -0.272 |
| B_iv1.5all | 11 | -0.515 | 740.33 | 4 | -0.669 | 817.67 | -0.515 | -0.270 |
| C_iv1.0all | 13 | -0.360 | 775.50 | 5 | -0.602 | 813.80 | -0.360 | -0.262 |
| D_spread2x | 5 | -0.707 | 793.68 | 3 | -0.887 | 816.72 | -0.707 | -0.206 |
| E_delay1 | 19 | -0.553 | 622.13 | 6 | -0.437 | 848.03 | -0.553 | -0.384 |

## By entry year (full-period engine)

| year | raw | bets | mean | median | hit | P&L $ | matched (b) mean | random (a) mean |
|---|---|---|---|---|---|---|---|---|
| 2022 | 19 | 7 | -0.428 | -0.635 | +0.14 | -186.00 | -0.607 | -0.625 |
| 2023 | 103 | 4 | -0.437 | -0.463 | +0.00 | -52.74 | -0.610 | -0.673 |

2022 sub-period (Nov-Dec 2022 only, the first eligible months): n=7, mean=-0.428, hit=+0.14, P&L=$-186.00

## Frozen decision rule

- IS_mean_ge_p95_matched: **PASS**
- IS_mean_gt0_stressA: **FAIL**
- IS_mean_gt0_stressE: **FAIL**
- survivor_excl_IS_gt_matched(engine): **PASS**
- survivor_excl_IS_gt_matched(signal-level): **PASS** (reported, not used: engine version is the rule)
- HO_mean_gt_matched_and_n>=20: **FAIL**
- P_ruin_lt_5pct(IS block bootstrap): **PASS**

**Overall: NO-GO (do not adopt)**

## Notes

- Equity = cash + open spreads at model mid. Exits sell at model bid/ask; a spread whose exit proceeds after costs would be negative is abandoned at $0 (max loss = debit).
- Matched control (b): per (period, calendar year) bucket, the same number of random (symbol, day) pairs as raw strategy signals, drawn from days with RSpct>=0.90 and Close>SMA50 but NOT Close<min(prior 5 closes); fed through the identical engine. Control (a): Bernoulli 3% of all eligible symbol-days.
- Signal-level rows price every raw signal at a fixed $80 budget (no caps, no governor), to separate signal quality from engine path effects.
- HO trades entered in the last 10 sessions are closed at the last bar (2026-11-13).
- Bet-sequence bootstrap P(<$100) resamples dollar P&L of bets; it ignores the governor.
- Runtime 105.7 s; seed 20261114; 1000 control draw sets; 10000 bootstrap paths.

## Reading the result (added after the run; nothing was tuned)

- **The engine starves itself.** After the first losses in Nov-Dec 2022, equity stays below 0.85 x HWM, so the governor halves the budget to about $30-40. Almost no signalled name's 1-increment vertical fits that, so the last bet is in June 2023 (IS 298/318 signals skipped as over budget). The "full" run is the IS run, with no bets after 2023-06. The HO run, restarted at $1,000, takes only 4 bets because HO names are mostly above $100 (signal-level fit rate at $80 is 8%).
- **Friction dominates.** With a 3%-of-mid half-spread on each leg of a 1-increment spread, the modelled round trip costs about 40% of the debit before the underlying moves. The median bet returns -64% while the 10-day underlying return is +3.3%. No bet reached the take-profit. Matched and random controls lose almost as much (-0.55 to -0.73 per bet). The strategy's high percentile against them comes from 11 bets, all deeply negative.
- **Signal edge in the underlying is small in IS.** The 10-day return is +3.3% for signals vs +3.1% for the matched control. In HO it is +3.4% vs +1.3%, but that is 119 signals in 40 weeks. The diagnostic 1.05x naked call shows +10% mean / +1.5% median on the same IS bets, so most of the loss comes from the vertical's structure and costs.
- **Split-adjusted history:** bars are split-adjusted (for example NFLX traded near $260 in 2022, but the bars show $26). Early-period strikes, increments and affordability therefore do not match the contracts that really traded, which biases affordability upward in 2022-2023.
- The 2022 sub-period (Nov-Dec 2022 only): 7 bets, mean -43%, P&L -$186. This is most of the IS loss.
