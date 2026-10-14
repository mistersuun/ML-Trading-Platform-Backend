# Forward paper test, 2026-10-05 to 2026-10-14: final report

The platform ran its full nightly pipeline for 8 consecutive US sessions against real IBKR closing prices. Orders went to a simulated paper broker holding a fictional US$100k: no Alpaca, no real money, and IBKR read-only throughout. This report covers what was tested, what broke, what was fixed, and what the numbers can and cannot tell us.

## 1. Summary

1. **The setup works end to end.**
   - 8 of 8 sessions ran with status `ok`, with no crashes in the committed journal.
   - The account matched the broker after every session.
   - The risk state stayed clean all week: multiplier 1.0, no halts.
2. **No strategy traded.** 0 of 340 symbol × pattern candidates passed the gates on any night; every one stopped at the 30 out-of-sample trades gate.
   - The cause is structural: there is too little out-of-sample data per symbol (see `model-review-2026-10.md`), not a bug.
   - The pooled check (D18) is the approved fix, and it runs in shadow mode only.
3. **The plumbing test exercised every leg of the order path**:
   - next-open fill with slippage;
   - live bracket legs;
   - cancelling the legs before the close;
   - an exit at the next open;
   - reconciliation every night.

   Result: −US$1.62 on one SPY share. That number is meaningless as a return, and the lifecycle worked as designed.
4. **Shadow signals:** 137 one-day scores (mean −0.02%, 53% hit rate) and 59 five-day scores (mean +0.82%, 59% hit rate). The signals are heavily clustered and duplicated, so this is **not** evidence of an edge.
5. **Investor copy portfolios:** value-tilted copies beat SPY and tech-heavy copies lagged. Over 6 sessions this is a sector move, not demonstrated skill (section 5).
6. **Nine setup or data problems were found during the week**; seven are fixed, one is not ours to fix, and one needs your action (section 3).

## 2. The plumbing test (one SPY share, labelled, not a strategy)

| Date | Event |
|---|---|
| 2026-10-06 | Entry submitted through `execution.submit_intent`, passing every risk check. Bracket: stop 755.72, take-profit 814.15. |
| 2026-10-07 | Filled at the open, 776.16 (5 bp simulated slippage). Bracket legs active. Reconciliation OK. |
| 2026-10-08 to 2026-10-12 | Held. Neither leg touched. Marked to market nightly. Reconciliation OK. |
| 2026-10-13 | Close submitted. The protective legs were cancelled first (2 of 2), then the sell. |
| 2026-10-14 | Sold at the open, 774.54. Position flat. Reconciliation OK. P&L −1.62. |

Each step behaved as the D5 rules require. It was placed once and is excluded from every strategy statistic.

## 3. Problems found and what happened to them

| # | Problem | Status |
|---|---|---|
| 1 | The journal's risk check read stale equity (`equity_not_updated`) | Fixed before day 1 |
| 2 | Missing-data warnings for 22 optional crypto, FX and futures symbols flooded the journal | Fixed (collapsed into one line) |
| 3 | Look-ahead and rerun holes in the harness: an order could fill before its decision bar, and a same-date rerun duplicated journal rows | Fixed before the test, from review findings |
| 4 | The ingest script reported a crash when a valid top-up met an old bad bar (GURU, 2024-12-18) | Fixed (`5147de0`); GURU's history still needs a full re-fetch |
| 5 | The runbook was missing contract IDs for BRK.B, MKL and L | Fixed (`776368d`) |
| 6 | A CI time bomb: a briefing test compared a fixed-date call with the real clock, and failed from 2026-10-07 | Fixed (`a9d7d35`) |
| 7 | GitGuardian flagged an invented test token | Fixed in the code (`f4649a2`); **you need to mark incident 37877772 as a false positive** |
| 8 | SEQUX's 3-for-1 stock distribution looked like a −75% loss | Fixed with a split adjustment (`dddb557`). SEQUX converts into the SEQ ETF around 2026-10-19, and the tracker needs to switch to it |
| 9 | The scheduled trigger for 2026-10-07 reached the session about 13 hours late | Not ours to fix; it was caught up the next morning before the open. A host machine with its own scheduler would avoid it |

IBKR also rate-limits at about 30 requests a minute. The fetches handled this by pacing and retrying.

## 4. Strategy results

- **Funnel, every night:** 340 tested, 0 with at least 30 out-of-sample trades, 0 orders. N = 780, advisory PBO 0.59.
- **Pooled check (D18, shadow):** in the exploratory pre-hold-out run, 19 of 21 patterns had enough trades, but 0 passed the PSR check. The best pooled Sharpe was about 0.97 against a required 2.4 at T = 672.
- **Confirmation needs more sessions.** D18 requires at least 20 forward shadow sessions spread over at least 28 days, and this week supplied 8. **The nightly forward runs stop with this report, so the confirmation clock stops too** unless runs continue (section 7).

## 5. Investor copy portfolios (6 sessions, 2026-10-06 close to 2026-10-14 close; four added on 2026-10-12)

| Portfolio | Since start | vs SPY |
|---|---:|---:|
| Fairfax (FFH.TO, CAD) | +3.52% | +4.74 |
| Listed vehicles (BRK.B, PSHD, MKL, L) | +2.74% | +3.96 |
| Consensus best ideas | +1.76% | +2.98 |
| TCI copy | +1.73% | +2.95 |
| Berkshire copy | +1.46% | +2.67 |
| Li Lu copy | +1.29% | +2.51 |
| Punch Card copy (from 10-12) | +1.03% | +2.25 |
| Sequoia NAV (from 10-12, split-adjusted) | +0.47% | +1.68 |
| Fundsmith (from 10-12) | −0.23% | +0.99 |
| Pershing copy | −1.07% | +0.15 |
| SPY | −1.22% | 0 |
| Copycat ETF basket | −2.69% | −1.47 |
| QQQ | −4.04% | −2.82 |
| Altimeter copy (from 10-12) | −4.20% | −2.98 |
| Tepper copy | −5.49% | −4.27 |

**How to read this.** The ranking is almost exactly the ranking of tech exposure. In a week when QQQ fell 4% on high yields, oil above $100 and doubts about AI revenue, anything light on tech beat SPY, and the tech-heavy copies (Tepper, Altimeter, the ETF basket) lost. It is one shared factor bet, not many independent wins. A 5-stock portfolio's 6-day gap to SPY of ±4% is within normal noise. Fair tests need style-matched benchmarks (VTV for value copies, RSP equal weight, QQQ or SMH for tech copies) and months of data.

## 6. Honest caveats

- **Eight sessions is far too short to judge any strategy or manager.** The value of this week is operational: the pipeline, the risk path, the data handling and the order lifecycle are proven.
- **No dividends.** The simulated broker charges no commission; slippage is 5 bp.
- **Prices:** IBKR daily bars are split-adjusted but not dividend-adjusted.
- **Holdings data:** investor holdings come from secondary sources and were not checked against SEC filings. The activist 13D and insider baskets are defined but empty, because SEC access is blocked from the container.
- **Shadow signals overlap:** several patterns flag the same symbol on the same day, so the effective sample is roughly half the raw count.

## 7. Next steps

1. **D20 clean-up commit**, which starts now that the freeze has lifted. Order of work:
   - ML threshold leak, hold-out exposure and label fixes;
   - weekly ML and pairs health check;
   - pairs validity fix;
   - pattern fixes and retirements, and per-pattern hashes;
   - 20-bar time stop (one version bump, folding in D23's risk fixes);
   - slow-test marker.
2. **Long-history run on your machine** (D22): this is the single change most likely to let a pattern pass.
3. **Owner decisions:**
   - Keep the nightly forward runs going, so the D18 pooled shadow can accumulate its ≥20 sessions over ≥28 days? Recommended: yes, until a host machine takes over.
   - Add style-matched benchmarks to the investor tracker? Recommended: yes; it's a small change.
   - Mark GitGuardian incident 37877772 as a false positive.
