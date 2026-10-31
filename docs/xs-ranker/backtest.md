# Cross-sectional ML ranker: walk-forward test (single look, rules frozen in PREREG.md before this was run)

Data: 124 US stocks, daily IBKR bars 2021-11-01 to 2026-10-30. Eligible stocks per month: 109 to 124 (median 119). Scored months (out of sample): 34, 2023-12-29 to 2026-09-30 (each is the holding month that starts on that month-end).

## Verdict: NOT adopted

- FAIL ML mean monthly IC t-statistic 0.92 (needs 2.0 or more)
- FAIL ML top 20% Sharpe 0.75 vs momentum top 20% 1.26
- FAIL ML top 20% mean monthly excess over the universe -0.41%

## Ranking skill (monthly rank IC: Spearman correlation of score and next-month return)

| Score | Mean IC | t-statistic | Months positive | Months |
|---|---:|---:|---:|---:|
| ML top 20% (LightGBM) | +0.024 | 0.92 | 41% | 34 |
| Ridge top 20% | +0.009 | 0.24 | 53% | 34 |
| Momentum top 20% (12-1) | +0.031 | 0.80 | 53% | 34 |

## Portfolios (monthly rebalance at the month-end close, 10 bp per side on traded weight)

| Portfolio | CAGR | Volatility | Sharpe | Max drawdown | Monthly excess vs universe | Turnover / month | Final (start = 1) |
|---|---:|---:|---:|---:|---:|---:|---:|
| ML top 20% (LightGBM) | +19.3% | 28.8% | 0.75 | -19.1% | -0.41% (t -0.61) | 64% | 1.65 |
| Ridge top 20% | +18.7% | 29.1% | 0.73 | -20.7% | -0.44% (t -0.61) | 51% | 1.62 |
| Momentum top 20% (12-1) | +59.3% | 44.9% | 1.26 | -23.0% | +2.51% (t 1.86) | 24% | 3.74 |
| Universe, equal weight | +27.6% | 20.9% | 1.28 | -12.5% | n/a | 5% | 1.99 |
| SPY | +17.8% | 11.8% | 1.45 | -8.0% | -0.79% (t -1.44) | n/a | 1.59 |

The universe was picked in 2026 and leans to stocks that had already risen, so absolute returns here are inflated for every line. Judge the ML against the universe and momentum lines, not against SPY.

## What the final model leans on (LightGBM split counts)

| Feature | Splits |
|---|---:|
| r21 | 407 |
| vol63 | 367 |
| max21 | 352 |
| vol_trend | 339 |
| r63 | 336 |
| beta | 330 |
| vol21 | 312 |
| r252 | 297 |
| dist_high | 297 |
| r126 | 296 |
| mom_12_1 | 290 |
| idio_vol | 289 |
| dollar_vol | 288 |

## Month by month (net returns)

| Month start | ML | Ridge | Momentum | Universe | ML IC |
|---|---:|---:|---:|---:|---:|
| 2023-12-29 | +1.3% | +2.1% | -1.2% | -1.7% | +0.27 |
| 2024-01-31 | +10.3% | +4.9% | +27.4% | +10.0% | -0.04 |
| 2024-02-29 | +0.7% | +0.5% | +5.3% | +3.0% | -0.03 |
| 2024-03-28 | -5.8% | -7.0% | -10.7% | -5.6% | -0.12 |
| 2024-04-30 | +0.1% | +2.0% | +6.9% | +6.5% | -0.18 |
| 2024-05-31 | -0.2% | +1.8% | +2.7% | +3.1% | -0.02 |
| 2024-06-28 | -3.9% | -2.2% | -2.9% | -0.0% | -0.06 |
| 2024-07-31 | +2.0% | +1.4% | +1.9% | +1.9% | +0.02 |
| 2024-08-30 | +5.4% | +6.6% | +7.9% | +4.2% | +0.01 |
| 2024-09-30 | +5.3% | +1.3% | +3.2% | +1.4% | +0.10 |
| 2024-10-31 | +15.4% | +16.4% | +31.6% | +14.9% | -0.02 |
| 2024-11-29 | -5.0% | -0.5% | -6.3% | -4.8% | -0.02 |
| 2024-12-31 | +8.9% | +6.8% | +12.4% | +6.4% | +0.20 |
| 2025-01-31 | -5.4% | -4.7% | -10.9% | -3.4% | -0.01 |
| 2025-02-28 | -12.3% | -16.6% | -13.6% | -9.0% | -0.29 |
| 2025-03-31 | +1.6% | +4.6% | +10.9% | +1.2% | +0.23 |
| 2025-04-30 | +7.7% | +12.9% | +19.8% | +11.3% | +0.09 |
| 2025-05-30 | +15.9% | +13.3% | +16.2% | +10.1% | +0.29 |
| 2025-06-30 | +4.1% | +1.1% | +11.0% | +3.5% | -0.03 |
| 2025-07-31 | +0.7% | -0.0% | -0.1% | +1.6% | -0.02 |
| 2025-08-29 | +6.1% | +9.8% | +11.4% | +7.3% | -0.03 |
| 2025-09-30 | +7.3% | +7.2% | +5.0% | +3.6% | +0.23 |
| 2025-10-31 | -8.5% | -11.5% | -12.8% | -4.9% | -0.03 |
| 2025-11-28 | +0.8% | +4.9% | +1.8% | -0.0% | +0.15 |
| 2025-12-31 | -2.2% | -3.3% | +9.2% | +0.9% | -0.06 |
| 2026-01-30 | -3.0% | -3.9% | -7.3% | -3.6% | -0.07 |
| 2026-02-27 | -5.9% | -4.3% | -8.1% | -5.4% | -0.06 |
| 2026-03-31 | +22.5% | +17.0% | +30.3% | +13.2% | +0.28 |
| 2026-04-30 | +16.4% | +17.0% | +25.6% | +12.9% | +0.23 |
| 2026-05-29 | +0.6% | +4.1% | +10.0% | -0.4% | +0.13 |
| 2026-06-30 | -10.1% | -7.7% | -20.2% | -3.2% | -0.08 |
| 2026-07-31 | +1.2% | +1.4% | +6.2% | +5.2% | -0.21 |
| 2026-08-31 | +4.0% | +2.5% | +10.3% | -1.1% | +0.21 |
| 2026-09-30 | -14.5% | -17.4% | -12.1% | -3.3% | -0.22 |
