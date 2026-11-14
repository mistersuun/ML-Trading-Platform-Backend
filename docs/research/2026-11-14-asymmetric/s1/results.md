# P1 catalyst-gap bull call vertical: FROZEN SPEC v1 backtest (paper research only, no orders)

Script: `backtest.py` (repo venv). Outputs: `results.json` (every metric), `trades_full_primary.csv`, `equity_full_primary.csv`, `run.log`.
**Option prices are a Black-Scholes approximation** (IV = clip(1.25 x RV20, 0.15, 2.50), re-marked nightly). They are not real quotes.

## Verdict: REJECT (negative)
None of the frozen criteria pass except "HO mean > HO control-A mean", and that one rests on n=9 bets.

| criterion | result |
|---|---|
| IS mean >= control-A p95 (-42.5%) | FAIL: -61.0% (33rd percentile) |
| IS mean > 0 under stress A / stress E | FAIL: -60.3% / -61.2% |
| HO n >= 20 and HO mean > control-A mean | n = 9, so HO is inconclusive (-48.2% vs -59.9%) |
| survivor-excluded IS mean > its control | FAIL: -61.4% vs -56.4% |
| P(ruin, touch $500) < 5% | FAIL: 28.6% (P touch $600 halt = 55.5%) |

## Primary run ($1,000 per period; HO restarted at $1,000 on 2025-10-31)
| | IS | HO | Full |
|---|---|---|---|
| raw signals (rules 1-4) | 218 | 65 | 283 |
| bets taken | 15 | 9 | 15 |
| skips: halt / over budget / slots / 25% cap / rule 5 | 167 / 26 / 2 / 4 / 4 | 0 / 56 / 0 / 0 / 0 | 232 / 26 / 2 / 4 / 4 |
| hit rate | 6.7% | 0% | 6.7% |
| mean / median return per bet | -61.0% / -62.6% | -48.2% / -37.9% | -61.0% / -62.6% |
| P(>= +100%) / P(<= -80%) | 0% / 40% | 0% / 11% | 0% / 40% |
| avg win / avg loss ($) | +12.0 / -31.0 | n/a / -29.4 | +12.0 / -31.0 |
| total net P&L, total excluding top 5 ($) | -421.8, -374.2 | -264.6, -168.5 | -421.8, -374.2 |
| terminal equity | $578.20 | $736.72 | $578.20 |
| max drawdown (daily marks) | -42.2% | -26.6% | -42.2% |
| CAGR | -12.8% | -25.6% | -10.3% |

Every IS bet was placed in 2023. The account fell below the $600 halt in Aug 2023, so the full-period run made no entries after that (2022 had no bull-regime days with 250 bars).

## Stress runs (n bets, mean return per bet, terminal equity)
| run | IS | HO |
|---|---|---|
| primary | 15, -61.0%, $578 | 9, -48.2%, $737 |
| A: IV x1.5 at entry | 15, -60.3%, $578 | 8, -46.3%, $769 |
| B: IV x1.5 everywhere | 14, -60.3%, $594 | 8, -52.6%, $744 |
| C: IV x1.0 everywhere (optimistic) | 15, -58.0%, $585 | 10, -45.9%, $729 |
| D: half-spreads doubled | 9, -93.1%, $557 | 6, -84.5%, $712 |
| E: entries and exits one session late | 15, -61.2%, $560 | 9, -45.5%, $757 |
| F (extra): 5% leg half-spread, as in the generic harness text | 9, -91.6%, $582 | 6, -62.3%, $757 |

## Controls
- **A, matched random days** (1,000 draw sets, a full $1,000 engine run each). IS control mean -56.7% (p95 -42.5%); the strategy sits at the 33rd percentile on mean return and the 33rd on terminal equity. HO control mean -59.9%; the strategy is at the 93rd percentile on mean and the 97.5th on terminal, but n is 9. For the survivor-excluded IS subset the control mean is -56.4% and the strategy is at the 26th percentile.
- **B, non-catalyst gaps** (volume < 1.5x): IS -56.3% over 14 bets, so the strategy is 4.7 points worse. HO -52.6% over 12 bets, so the strategy is 4.4 points better.
- **C, bear regime**: IS -64.5% over 9 bets (strategy 3.5 points better). HO has 1 bet.
- **D, SPY buy-and-hold**: IS +45.6% (max DD -25.4%), HO +10.6% (max DD -9.1%), full +61.0%, CAGR 9.9%.

## Other cuts
- Survivor-excluded (drops 17 names): IS 13 bets, mean -61.4%, terminal $599. HO 7 bets, mean -48.3%, terminal $796.
- Price distribution: the median Close was $13.40 on traded names and $75.20 on raw events. 100% of traded names were under $50, against 40% of raw events. The 8% budget filter leaves only cheap names with 1-increment ($0.50 or $1 wide) spreads.
- Ruin bootstrap (10,000 paths, 21-session blocks, full-period length): P(touch $500) = 28.6%, P(touch $600) = 55.5%. Terminal p5/p50/p95 = $369/$582/$812. Resampling only the active period after the first entry gives P($500) = 11.9%.

## Why it fails (diagnostics, NOT part of the frozen decision)
Without account constraints, all 275 raw events priced with the primary model:
- **The underlying does move favourably after events.** 15-day stock return is +3.4% mean with a 56% hit rate, against +1.4% and 51% on control-pool days. This is survivor-biased.
- **The instrument and cost model destroy that edge.** The narrow 1-increment spread has mean -82% and a **0% hit rate** (pool: -65%). The 15%-wide spread has mean -18% (pool: -14%).
- The spec charges a half-spread of 3% of each leg's mid. Once a vertical is in the money, the two leg half-spreads exceed the spread's value: a deep-ITM APP 545/550 spread with the stock +30% marks at $0. Winners therefore cannot be harvested, and the take-profit rule (0.8 x width) almost never fires.
- **Diagnostic G** quotes the vertical as one combo (half-spread = 3% of the spread mid). It is still negative: IS 96 bets, mean -10.9%, terminal $595. HO 24 bets, mean -25.9%, terminal $690. Unconstrained, the wide spread on events averages -1.8% (37% hit rate, 4% at >= +100%) against -6.2% on the pool. Even with generous costs it is not an asymmetric-payoff edge.

## Implementation notes and deviations
- **Raw IS event count is 218, not the 228 in the feasibility check.** Dropping the >= 250-valid-bar rule gives 292, close to 293, so the feasibility count probably omitted that rule. I kept the rule as frozen.
- Universe = 135 names. GURU, GVIP and NANC are ETFs but are not on the spec's exclusion list, so they are included; none produced a signal.
- vol50 needs at least 45 of the 50 prior bars.
- A spread whose exit value is under $1.30 commission per lot is abandoned (proceeds $0).
- Marks are floored at 0, because the long leg is always the lower strike.
- When the 25% open-cost cap binds, lots are reduced to fit, and the bet is skipped only if not even 1 lot fits.
- Stress E: the contract is chosen at t, priced, sized and checked at the t+1 close; exit decisions at u are filled at the u+1 close.
- Bars look split-adjusted (SMCI around $13 in 2023). CVNA's early-2023 prices (~$2) look rescaled versus history, so strikes on adjusted prices are an approximation.
- Control A draws one bull day per raw event in the same symbol-year (no P1 signal within +/-5 sessions), without replacement. No symbol-year had too few candidate days.
