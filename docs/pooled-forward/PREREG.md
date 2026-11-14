# Pooled shadow, forward scoring: pre-registration (frozen 2026-11-14)

The owner approved this on 2026-11-14 ("yes go ahead"), after
`docs/forward-test/2026-10/POOLED-SHADOW-SUMMARY.md` (review item 8 in `docs/reviews/2026-11-12-strategy-review.md`).
These rules were written **before any forward return of a pooled signal was computed**. Paper only: nothing here can
place an order, and a pooled signal stays shadow-only and order-ineligible whatever this test shows. The file is
frozen; a change needs a dated amendment at the bottom, made before the look it affects.

## Why

The nightly pooled verdict scores history only: its out-of-sample window ends on 2025-06-13. More nights cannot change
it. The only forward evidence is the signals the pooled patterns fire each night, and those were never scored. This
test scores them.

## Inputs

- **Signals.** `shadow_signals` from each nightly pooled payload (`state/forward/results/pooled/*.json`), recorded
  append-only into `docs/pooled-forward/signals.jsonl`, one row per session (`asof`). If a session has several
  payloads, the one generated last is used.
  - Recorded from the 2026-11-03 session. Earlier payloads were lost in the 2026-11-03 container reset; they are
    not reconstructed.
  - A signal flagged `stale` or `excluded` is dropped.
- **Prices.** Daily bars from `state/external_bars` for finished sessions only (`external_bars.closed_bars`, XNYS),
  the same data the forward step uses.

## The trade rule (fixed; never tuned)

- **Direction.** Long only: a pooled signal is a long entry.
- **Entry.** At the open of the next session after `signal_bar_date`.
- **Exit.** At the close of the 10th session, counting the entry session as the 1st. This is a fixed 10-session
  hold, chosen now as a simple proxy. It ignores each pattern's own stop and exit rules, so this tests the entry
  signal only.
- **Cost.** 15 bp per round trip, taken from the trade's return.
- **De-duplication.** One open trade per (pattern, symbol). A signal whose entry date falls inside an open trade of
  the same pattern and symbol (on or before that trade's exit date) is not a new trade. Different patterns on the
  same symbol and day are separate trades. The pooled statistic below clusters them, so they do not count as
  independent evidence.
- **Benchmark.** SPY over the trade's exact window, entry-session open to exit-session close.
  - Trade excess = trade net return − SPY return over the same window.
  - "Buy-and-hold relative" means this: the trade against holding SPY over the same days.
- **Control** (random timing on the same names). For each completed trade, the same symbol's average 10-session
  excess over SPY across **every** entry date in the test window. This removes "the stock simply went up". The
  signal must beat its own symbol's all-days average.

Only completed trades (exit session closed) are scored. Open trades are listed with their marks, without inference.

## The statistic and the pass rule

- **Clusters.** Trades are clustered by **entry week** (ISO year and week of the entry date). Overlapping 10-session
  windows within a week share market shocks.
- **Primary.** Mean trade excess, with a cluster-robust standard error: the mean of the week means, with their
  standard error over weeks. Then a one-sided t-test with df = weeks − 1.
- **Condition 2.** Mean (trade excess − its symbol's all-days control excess), tested the same way.
- **Condition 3.** Condition 2's mean is still above 0 with the 2 best entry weeks removed.
- **Size.** At least 60 de-duplicated completed trades and at least 10 entry weeks. Below that, the page shows "n/a"
  for inference.

**Pass** at a look only when the size condition holds and all of these hold:

1. the primary one-sided p is below 0.05 / 3 = 0.0167;
2. condition 2's one-sided p is below 0.0167;
3. condition 3's mean is above 0.

Per-pattern results are descriptive only, with no test: 21 patterns would need a multiplicity correction, and the
pooled design exists to avoid that.

## Looks

The same dates as the master scoreboard (`docs/scoreboard/PREREG.md`): **2026-12-31, 2027-02-26, 2027-04-15**. No
other look decides anything. The nightly page is informational.

- **A pass** promotes nothing by itself. It means the pooled path has forward evidence worth a pre-registered
  paper account; the graduation ladder (`docs/scoreboard/GRADUATION.md`) still applies.
- **A fail at the last look** ends the pooled path for this universe.

## Known weaknesses, stated now

- **Small, correlated universe.** 17 large US names and index ETFs; a broad market rally lifts most signals.
  Condition 2 handles the symbol drift, not the timing of a market-wide move.
- **Short window.** About 22 weeks to the last look; even a real edge may not reach significance (low power).
- **Entry test only.** The fixed 10-session hold is not the patterns' engine exit.
- **Missing early signals.** Signals from 10-15 to 11-02 are missing (lost payloads).
