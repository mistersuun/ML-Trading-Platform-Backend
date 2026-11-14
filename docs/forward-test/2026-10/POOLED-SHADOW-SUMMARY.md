# Pooled shadow (D18): summary after the 2026-11-13 session

Shadow only. The pooled check never placed an order, an intent or an alert, and no pooled status is order-eligible.

## Verdict

**Nothing passed.** On every recorded night, 0 of 21 patterns were pooled-validated. Every pattern fails the PSR
gate, which sits early in the funnel.

The confirmation window that D18 asked for is now met in calendar terms: at least 20 forward shadow sessions over
at least 28 days. That window cannot confirm anything, though, for the reason in the first caveat below.

## Sessions accumulated

| Item | Value |
|---|---|
| Sessions with the pooled shadow on | 22 (2026-10-15 to 2026-11-13). Each nightly commit from 10-15 is labelled "pooled shadow on". |
| Calendar span | 29 days |
| Nightly payloads still on disk | 9 (as of 11-03 to 11-13; the 11-04 and 11-05 runs were catch-up runs on 11-06) |
| Payloads lost | 10-15 to 11-02, in the 2026-11-03 container reset. Only the commit labels survive; the snapshot DBs have been committed since 11-03. |
| Status of every stored run | `ok`, 38 to 45 seconds each, universe complete (17 symbols) |

## Pooled funnel over time

The funnel was identical on every stored night:

| Stage | Patterns left |
|---|---:|
| Tested | 21 |
| Enough pooled trades | 19 |
| Breadth | 19 |
| Out-of-sample mean positive | 18 |
| PSR | **0** |
| Null / Benjamini-Hochberg, DSR, concentration, cost and delay, capped replay, hold-out | 0 |
| Pooled validated | 0 |

Other values were also unchanged on every stored night:

- 22 trials in the ledger.
- Sharpe variance across trials: 0.00067.
- PBO 0.90; cross-sectional PBO 0.78.

The 10-15 session reported the same 0 pooled-validated. The exploratory pre-hold-out run in FINAL-REPORT.md gave
19 of 21 with enough trades and 0 passing PSR, with the best pooled Sharpe about 0.97 against about 2.4 required.

### Closest patterns (stored run of 2026-11-13; same values as 11-12)

| Pattern | Pooled trades | Annualised Sharpe | PSR | Null p | Gates failed |
|---|---:|---:|---:|---:|---|
| triple_ema | 239 | 1.00 | 0.946 | 0.062 | PSR, null/BH, DSR, hold-out |
| macd_crossover | 433 | 0.82 | 0.902 | 0.042 | PSR, null/BH, DSR, hold-out |
| ichimoku_cloud | 353 | 0.82 | 0.906 | 1.000 | PSR, null/BH, DSR, hold-out |
| donchian_breakout | 369 | 0.81 | 0.903 | 1.000 | PSR, null/BH, DSR, hold-out |
| ema_crossover | 224 | 0.78 | 0.898 | 0.037 | PSR, null/BH, DSR, hold-out |
| sma_200_trend | 158 | 0.60 | 0.829 | 0.006 | PSR, null/BH, DSR, hold-out |

These are long-only trend rules on 17 large US stocks and ETFs over 2022-11 to 2025-06. That stretch was a strong
bull market for exactly these names, so a modest positive Sharpe is what beta alone would give.

### Shadow signals per night (pooled patterns, not traded)

| Session | 11-03 | 11-04 | 11-05 | 11-06 | 11-09 | 11-10 | 11-11 | 11-12 | 11-13 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Signals | 26 | 45 | 8 | 23 | 36 | 11 | 32 | 8 | 11 |

## Honest caveats

1. **The nightly verdict cannot change with forward data, by construction.** The pooled evaluation's
   out-of-sample window ends on 2025-06-13, and its hold-out starts on 2025-07-01. New sessions add no trades to
   either. So "N nights of pooled results" repeats the same historical test N times. It is a stability check of the
   code and data, not a forward confirmation. The forward evidence would be the shadow signals, and those were
   never scored. This is review item 8 in `docs/reviews/2026-11-12-strategy-review.md`.
2. **The hold-out is not fresh.** The 2025-07-01 hold-out was viewable per symbol in the app before D18 (D18 a), so
   the pooled hold-out is an aggregate of bars that had already been seen.
3. **Pre-reset payloads are gone.** For 10-15 to 11-02, only the commit labels show the runs happened. The early
   funnels are not preserved, although the method and data were the same.
4. **Small, correlated universe.** There are 17 symbols, several of them overlapping index ETFs (SPY, QQQ, DIA,
   IWM), so the 17-symbol breadth overstates independence.
5. **The hold-out stage was never reached.** No pattern got past PSR, so the single per-pattern pooled hold-out read
   was never spent. That read is still available for a future pre-registered rule.

## What this means

- Neither the per-symbol path (0 of 340) nor the pooled path (0 of 21) found a validated technical pattern on this
  universe. That is consistent with the 2026-11-12 review: no family has evidence yet.
- Do not loosen the PSR or trade gates to get a pass (review, "do not do").
- If the pooled path is to be tested forward, the honest next step is review item 8: score the nightly pooled shadow
  signals forward, pre-registered (long-only, 15 bp costs, de-duplicated, measured against buy-and-hold), and judge
  them only at the scoreboard's fixed looks. That needs the owner's go-ahead. Until then the pooled shadow keeps
  running nightly at no risk.

## Update 2026-11-14

The owner approved the forward scoring. Its rules were frozen in `docs/pooled-forward/PREREG.md` before any forward
return was computed. It runs nightly, and only the looks on 2026-12-31, 2027-02-26 and 2027-04-15 decide.
