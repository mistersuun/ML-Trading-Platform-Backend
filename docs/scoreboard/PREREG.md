# Master scoreboard: pre-registration (frozen 2026-11-12)

Written on 2026-11-12 **before any scoreboard number was computed**. Motivation: `docs/reviews/2026-11-12-strategy-review.md`
(no family showed evidence yet; about 30 arms are being watched, so one of them will look good by luck).
Paper only; every account is simulated. This file is frozen: a change needs a dated amendment at the bottom, made before
the look it affects.

## Rule zero

No rule of any arm may be tuned on its forward data. This scoreboard reads journals and prices and **never changes an arm's
rules**, sizing or universe. It only reports.

## Arms and benchmarks

The benchmark is fixed here, now. SPY unless the arm defines its own or is QQQ-based. Returns are daily, close to close,
over the arm's **own** forward window (first day after its recorded start to the last available date).

| Arm (id) | Source | Benchmark |
|---|---|---|
| ORB QQQ (`dt_qqq`), ORB SPY (`dt_spy`) | `daytrade-paper/journal.jsonl` | QQQ for `dt_qqq`, SPY for `dt_spy` (journal `bench_value`) |
| Gap-and-go (`gapgo`) | `gapgo-paper/journal.jsonl`, `state.json` | SPY |
| Quant: trend, momentum, lowvol, high52, rsi2 (`q_*`) | `quant-accounts/journal.jsonl` | SPY |
| Aggressive: 3x Nasdaq trend (`ag_lev_trend`), top-1 momentum (`ag_top1_mom`) | `aggressive-accounts/journal.jsonl` | SPY |
| Options Book L (`opt_L`), Book P (`opt_P`), Book M (`opt_M`) | `options-paper/journal.jsonl` | the book's own `bench_value`: QQQ, SPY, QQQ |
| Forward-test account (`ft_account`) | `forward-test/2026-10/journal.jsonl` (`day` rows) | SPY |
| Investor baskets (14 with returns): appaloosa, berkshire, consensus, etfs, fairfax, himalaya, pershing, tci, vehicles, altimeter, fundsmith, punchcard, sequoia, plus the QQQ and SPY controls (`inv_*`) | `forward-test/2026-10/investor-tracking.jsonl` (last row's `daily_returns`) | SPY for all, from each basket's own first return day (not the tracker's SPY-from-10-06). The tracker's style benchmarks (RSP, VTV, QQQ, SMH, QUAL) are not used: picking among them after the fact is a free choice. |
| Activist 13D and insider-cluster baskets | same file | definition only, no returns: listed as "n/a" until they have data |

About 30 arms in all. **Not part of the family test** (shown, never promotable): the `inv_spy` and `inv_qqq` controls (a control
cannot beat itself or be a strategy) and `ft_account` (a cash account that held one SPY share as a plumbing test).

Caveats stated now so they are not discovered later: Fairfax is priced in CAD and compared with a USD benchmark; the quant and
aggressive accounts fill at the open after the start close while the benchmark runs close to close, so their first day is an
approximation; a day that is missing from an arm's journal is handled as below.

## The statistic

For each arm, the daily **excess return** = arm daily return minus benchmark daily return, over the arm's own window.
The arm series is built from its journal equity; the benchmark from `state/external_bars` closes (or the journal `bench_value`
where named above). Reported for every arm:

1. Total return, benchmark return, excess (difference of totals; total return is final equity over the stated start).
2. Mean daily excess, with a **day-block bootstrap** 95% CI: circular blocks of length 5, 10,000 resamples, fixed seed
   20261112, percentile interval.
3. The same mean and CI with the **best 2 days removed** ("ex-top-2"): the two days with the largest excess return are dropped,
   then the same bootstrap.
4. Beta and alpha from OLS of arm daily return on benchmark daily return, with a Newey-West (Bartlett, lag 5) t-statistic for alpha.
5. **Effective number of independent days**: n * (1 - r1) / (1 + r1) with r1 the lag-1 autocorrelation of the excess series
   (r1 below 0 counts as 0, result capped at n).
6. Share of total P&L (in currency) from the best 2 days. "n/a" if total P&L is not positive.

Fewer than 10 return days: all inference is shown as "n/a". Nothing is imputed.

**Missing dates.** If a journal date is missing, the return is taken between the dates that exist and the benchmark over the
same two dates; that return is flagged "gap" and counted in the table. If the benchmark lacks a date, that date is dropped
from the comparison in the same way. Gap days are left out of correlations.

## Clusters

Arms whose daily **returns** (the arm's own, not excess) correlate above **0.7** (on days both have a clean return, at least 10
common days; linked pairs join into one cluster by chaining) count as **one bet** in the family test. The cluster list is
reported. Gap days are left out of the correlation.

## Family-wise test and decision rule

- One-sided p-value for "mean daily excess > 0": day-block bootstrap null (resamples re-centred at zero, same blocks and seed),
  p = (1 + count(resampled mean >= observed)) / (10,000 + 1).
- A cluster's p-value = min(1, cluster size times the smallest member p-value). Holm step-down adjustment across clusters of
  testable arms. Alpha 0.05 family-wise in total, split equally over the 3 looks: **0.05 / 3 = 0.01667 per look**.
- An arm is **promoted to candidate** at a look only if **all three** hold:
  1. its cluster's Holm-adjusted p-value is below 0.05 / 3;
  2. its ex-top-2 CI lower bound is above 0;
  3. it has at least **60 independent trading days** (trade-based arms: ORB books, gap-and-go, options books: at least
     **60 trades**, closed trades for options).
- **Looks are fixed: 2026-12-31, 2027-02-26, 2027-04-15.** At most three. No other date counts, there is no optional stopping,
  and between looks the scoreboard is informational only (it carries a banner saying so). Failing a look is not final; passing
  one is not "real money" (see `GRADUATION.md`).
- Arms that start later, or are added later, are tested from their own start on the looks that follow, in the same family.
  Adding an arm needs a dated amendment here; its alpha is not borrowed from the others.

## Amendments

- 2026-11-12, same day, before any look and before any decision: while the scoreboard was being built, the clustering rule's
  wording in an early draft said "excess returns"; it was set to each arm's own daily returns (as specified in the task
  that commissioned this file) after a preview run of the code had been seen. No arm's status depends on it today (no
  arm is near the threshold), but the change is recorded here because it was made after numbers were visible.
