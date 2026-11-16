# Five quant paper accounts: final report (2026-10-15 to the 2026-11-16 close)

Paper only. US$1,000 each, rules frozen in `PREREG.md`. Fills started at the 2026-10-15 open; the test ended at the
2026-11-16 close (23 sessions). SPY from the same first open: **−3.43%**. Every number below comes from the committed
journal (`journal.jsonl`) and the master scoreboard (`docs/scoreboard/scoreboard.md`, 2026-11-16 build).

## Result

| Account | Final equity | Return | vs SPY | Expected one-month excess, 10th to 90th percentile (`backtest.md`) | Trades | Costs |
|---|---:|---:|---:|---|---:|---:|
| Low volatility | US$1,096.38 | +9.64% | **+13.07 pts** | −6.3% to +4.4% | 6 | US$2.60 |
| Short-term mean reversion (RSI-2) | US$996.16 | −0.38% | +3.05 pts | −5.2% to +3.9% | 10 | US$4.75 |
| 52-week-high proximity | US$992.97 | −0.70% | +2.73 pts | −5.4% to +4.1% | 9 | US$4.05 |
| Trend following | US$972.64 | −2.74% | +0.70 pts | −4.3% to +3.6% | 4 | US$1.90 |
| Cross-sectional momentum (12-1) | US$945.72 | −5.43% | −1.99 pts | −6.3% to +9.8% | 7 | US$3.15 |

Four of five beat SPY in a falling month. That is mostly what their design predicts: low beta, cash or bonds when the
trend turns, and RSI-2 sat in cash.

## What it shows and what it does not

- **Low volatility is the only result outside its normal one-month range.**
  - On the scoreboard its daily beta is 0.01, alpha is 41 bp a day, and the Newey-West t-statistic is 2.48.
  - Two days carry 65% of its P&L, though. The block-bootstrap confidence interval without the best 2 days spans
    zero (−81 to +133 bp a day).
  - Its Holm-adjusted p is 0.171. That is not significant against the 0.0167 bar, and today is not a pre-registered
    look.
  - One plausible explanation is luck in a down tape: staples and BRK.B held up while tech fell. A real defensive
    effect is the other, and one month cannot tell them apart.
- **Momentum lost.** It held the strongest names of the past year (AMD, CSCO, LLY, MRK, XOM) into a rotation, which
  is the classic momentum-crash setting. One month says nothing about the long-run premium.
- **Costs mattered at US$1,000.** The US$0.35 minimum commission took US$1.90 to US$4.75 per account, 0.2% to 0.5%
  of equity in one month.
- **No ranking is justified.** The PREREG said in advance that one month cannot rank these rules, and the numbers
  agree.

## Verified issues during the month (all fixed or logged)

- The journals are now append-only, with the 11-04 and 11-05 rows backfilled. Replay differences are logged to
  `revisions.jsonl`; all are cent-level (at most US$0.13), from vendor bar revisions after the 11-03 re-fetch.
- A missing close is now marked at the last close, not at 0. This was latent and never triggered.

## What happens next

- The live test window has ended. The nightly routine no longer steps these accounts, so their journals stop at
  2026-11-16.
- Their scoreboard arms stay as they are. They will be evaluated at the 2026-12-31 look on this window only, and they
  can never reach the 60-independent-day size bar.
- If the owner wants a longer test of low volatility, the honest way is a new pre-registered run starting fresh, not
  extending this one now that its result is known.
- None of this is a recommendation to put real money into any of the five rules (`docs/scoreboard/GRADUATION.md`).
