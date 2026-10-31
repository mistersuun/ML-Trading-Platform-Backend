# Cross-sectional ML ranker: what the single look showed (2026-10-31)

Read with [`backtest.md`](backtest.md). Rules were frozen in [`PREREG.md`](PREREG.md) and committed before the run.

## Verdict: not adopted

The ML ranker failed all three pre-registered conditions over 34 out-of-sample months (2023-12 to 2026-10, 109 to 124
stocks a month):

| Condition | Needed | Result |
|---|---|---|
| Ranking skill (mean monthly IC t-statistic) | 2.0 or more | 0.92 (mean IC +0.024, positive in 41% of months) |
| Sharpe of the ML top 20% vs the momentum top 20% | higher | 0.75 vs 1.26 |
| Monthly excess of the ML top 20% over the universe | above 0 | −0.41% (t −0.61) |

- **The ML picked worse than holding every stock equally.** Ridge did no better, so this is not a tuning problem
  of one model: the price features carry little information about next month's ranking in this universe and period.
- **The final model leaned mostly on short-term noise features** (last month's return, volatility, the biggest daily
  move): what a tree finds when there is no stable signal.
- **Plain 12-1 momentum looks strong (+2.5% a month over the universe, t 1.86) but is not trustworthy here.** The
  universe was chosen in 2026, partly *because* names like PLTR, APP and HOOD had already soared. A rule that buys the
  past year's winners is exactly the rule that hindsight selection flatters most. It also fell short of t = 2.

## Run notes

- A first run crashed in feature building (a 252-day return needs 253 closes; eligibility had required 252). It
  produced no output. The fix and a test were added, then the test ran once.
- No parameter was changed after the result.

## What this means for the platform

- Together with the day-ahead model (AUC about 0.5) and the 340-candidate signal search (0 passes), this is the third
  independent, pre-registered attempt to find a price-based predictive edge, and the third null result.
- The honest conclusion: with daily price data alone, on liquid US stocks, this platform cannot find a tradable
  prediction edge. More tuning on the same data would only manufacture one.
- What still has value: risk control on the real account (the Portfolio Pilot, D25), and the forward paper accounts,
  which test simple, well-documented rules out of sample without any fitting.
- An edge, if one exists for a small account, more likely needs **information that is not in the price history**:
  earnings surprises and revisions, insider buying, or event data. That would be a new, separately pre-registered
  study.
