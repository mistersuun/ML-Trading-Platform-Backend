# Portfolio Pilot: what the single look showed (2026-10-28)

Read together with [`backtest.md`](backtest.md). Rules were frozen in [`PREREG.md`](PREREG.md) before the one run.

## Verdict on the pre-registered test

- **The Pilot (S3: trend gate + 15% volatility target) is not adopted.**
  - It had a smaller max drawdown than S1 (−43% vs −65%).
  - But its 18-year return fell far short: +4.4% a year vs +8.8% for S1, which fails the "within 2 points" condition.
  - Its Sharpe ratio was also lower (0.35 vs 0.45).
- **Where it lost:** in the rebound after every crash. Leverage averaged 0.52x in 2009 and the Pilot made +18%, against +49% for the same mix unlevered. In 2020 it made −5% against +22%.
  - Volatility targeting cuts exposure after volatility spikes, which is exactly when V-shaped recoveries start.
  - This matches the out-of-sample critique by Cederburg, O'Doherty, Wang & Yan (2020, JFE), "On the performance of volatility-managed portfolios".
- **The satellite trend gate alone (S2 vs S1) cost 1.4 points a year** through whipsaws. It helped only in the 2011-2015 commodity bust.

## The owner's own mix (S0)

S0, the account's current mix at its current leverage, was run on the same data. Its numbers are kept in the
git-ignored `state/pilot/backtest-private.md`, because they describe the owner's real holdings. The committed
comparison of the rules themselves:

| | Return / yr | Max drawdown | Worst 12 months |
|---|---:|---:|---:|
| S&P 500 alone, no leverage | +9.8% | −52% | −47% |
| Strategic mix at 1.45x | +8.8% | −65% | −60% |
| Strategic mix, no leverage | +7.9% | −50% | −45% |

- **Leverage at 3.58% added about 1 point a year of price return on the diversified mix**, a bit more with dividends, for 15 points of extra drawdown.

Caveats:
- **Price return only.** Dividends of roughly 1.5 to 2% a year are missing from every line. That penalises the levered lines slightly more, by about (L − 1) × 2% a year.
- **Proxies.** The test uses US proxies in USD, with single stocks standing in for uranium (CCJ) and copper (FCX). Bonds enter only in 2018 and semis in 2017 (the data starts there).

## What is useful, and what was built

- **The research value is the comparison above.** Over 18 years, adding resource satellites, leverage and timing rules did not beat a plain broad index on a risk-adjusted basis. This is consistent with decades of evidence.
- **The tool keeps the account on a policy the owner chooses** (`pilot.py` + `scripts/pilot_report.py`). The default policy is the static strategic mix at 1.45x, which is S1, the best tested option that keeps leverage, the owner's stated preference (2026-10-22). Each week it:
  - reads the account read-only;
  - shows drift by sleeve, leverage and margin cushion;
  - runs a one-day stress test (equities −20%, satellites −30%);
  - raises alerts: one position above 25% of net value, a cushion below 45%, or a stress loss above 30%;
  - lists whole-share trades, which the owner places by hand.
- **Leverage is the owner's choice,** capped at 1.5x. The backtest gives the trade-off: 1.0x had −50% max drawdown and Sharpe 0.51; 1.45x had −65% and 0.45.
- **The "trend" and "pilot" modes remain for comparison only.**
