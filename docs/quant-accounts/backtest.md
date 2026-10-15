# Quant accounts: historical analysis (before the live month)

Same simulator, rules and costs as the live accounts (5 bp slippage per side, IBKR-style commissions with a US$0.35 minimum, fractional shares, US$1,000 start). Each strategy starts once its 12-month lookback is available, so start dates differ; SPY over the same window is shown on each row.

| Strategy | Window | Total | CAGR | Vol | Sharpe | Max DD | Beta | SPY total (same window) | Months beating SPY | 1-month excess, 10th to 90th pct | Costs |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| Trend following (time-series momentum) | 2023-10-23 to 2026-10-15 | +52.9% | +15.4% | 8.9% | 1.66 | -6.8% | 0.39 | +83.0% | 40% of 35 | -4.3% to +3.6% | $19.24 |
| Cross-sectional momentum (12-1) | 2023-10-23 to 2026-10-15 | +187.0% | +42.8% | 30.6% | 1.32 | -28.7% | 1.56 | +83.0% | 49% of 35 | -6.3% to +9.8% | $63.76 |
| Low volatility | 2023-10-23 to 2026-10-15 | +40.9% | +12.3% | 12.1% | 1.02 | -9.9% | 0.18 | +83.0% | 40% of 35 | -6.3% to +4.4% | $14.66 |
| 52-week-high proximity | 2023-10-23 to 2026-10-15 | +25.1% | +7.9% | 17.0% | 0.53 | -17.5% | 0.63 | +83.0% | 37% of 35 | -5.4% to +4.1% | $120.15 |
| Short-term mean reversion (RSI-2) | 2022-10-10 to 2026-10-15 | +11.9% | +2.9% | 4.5% | 0.65 | -4.2% | 0.11 | +113.8% | 26% of 47 | -5.2% to +3.9% | $154.44 |

How to read this:
- About 3 to 4 years of daily data, mostly a strong, tech-led bull market, so large-cap growth and momentum look good and defensive rules look weak. Published evidence spans decades (see PREREG.md).
- The stock universe is 32 of today's large caps, chosen with hindsight (survivorship bias): it flatters every stock strategy.
- The '1-month excess' range is the key calibration for the live test: it shows how far one month's result can land from SPY by chance alone.
