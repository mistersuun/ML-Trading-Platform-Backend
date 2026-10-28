# Portfolio Pilot backtest (single look, rules frozen in PREREG.md before this was run)

Strategies (S0, the owner's own mix, is reported only in the git-ignored private copy): S1 = the strategic core/satellite mix at 1.45x; S2 = S1 with the satellite trend gate; S3 = the Pilot (trend gate + leverage from a 15% volatility target, 0.5x to 1.5x, traded only outside the bands); B1 = the S&P 500 alone, no leverage; B2 = the strategic mix, no leverage. Costs 10 bp per traded dollar; the loan pays 3.58% a year for the whole period; cash earns nothing. Margin cushion = equity / positions (a margin call is assumed near 30%).

## 1. Long history: weekly US proxies, 2008-02-29 to 2026-10-23

| Strategy | CAGR | Volatility | Sharpe | Max drawdown | Worst 12 months | Avg / max leverage | Lowest margin cushion | Turnover / yr | Costs + interest (x start) | Final (start = 1) |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| S1 restructured static (1.45x) | +8.8% | 26.4% | 0.45 | -65.4% | -60.2% | 1.45 / 1.70 | 59% | 1.10x | 0.49 | 4.85 |
| S2 S1 + trend gate (1.45x) | +7.4% | 25.8% | 0.41 | -69.2% | -65.2% | 1.45 / 1.67 | 60% | 2.14x | 0.46 | 3.80 |
| **S3 Pilot** (trend gate + vol target) | +4.4% | 15.6% | 0.35 | -43.3% | -39.1% | 1.03 / 1.53 | 65% | 2.81x | 0.16 | 2.22 |
| B1 SPY only, no leverage | +9.8% | 18.2% | 0.60 | -51.7% | -46.9% | 1.00 / 1.00 | 100% | 0.05x | 0.00 | 5.67 |
| B2 strategic weights, no leverage | +7.9% | 18.0% | 0.51 | -49.9% | -44.7% | 1.00 / 1.00 | 100% | 0.69x | 0.01 | 4.14 |

Sleeves enter once their proxy has 52 weeks of history: canada 2000-04-28, uranium 2007-02-23, copper 2007-04-20, us_equity 2008-02-22, gold_miners 2008-03-21, nasdaq 2008-04-11, utilities 2008-04-11, intl 2008-09-19, gold 2009-05-22, semis 2017-05-05, bonds 2018-08-03. Before that, their weight is spread over the other sleeves (so bonds are absent before 2018 and semis before 2017).

### Crisis windows (total return over the window)

| Period | S1 | S2 | S3 Pilot | B1 | B2 |
|---|---:|---:|---:|---:|---:|
| 2008 crash (2008-05 to 2009-03) | -59% | -64% | -39% | -46% | -44% |
| Commodity bust (2011-09 to 2015-12) | -8% | +22% | +16% | +75% | +0% |
| Late 2018 | -14% | -18% | -17% | -15% | -10% |
| COVID crash (2020-02 to 2020-03) | -30% | -30% | -25% | -25% | -21% |
| 2022 bear market | -34% | -35% | -23% | -25% | -23% |
| Bull run 2023-2025 | +142% | +114% | +73% | +78% | +93% |

## 2. Your actual TSX ETFs, daily, 2023-11-07 to 2026-10-27

| Strategy | CAGR | Volatility | Sharpe | Max drawdown | Worst 12 months | Avg / max leverage | Lowest margin cushion | Turnover / yr | Costs + interest (x start) | Final (start = 1) |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| S1 restructured static (1.45x) | +35.4% | 20.1% | 1.61 | -18.6% | 0.7% | 1.45 / 1.53 | 66% | 1.61x | 0.09 | 2.46 |
| S2 S1 + trend gate (1.45x) | +33.0% | 19.1% | 1.60 | -16.6% | 2.4% | 1.45 / 1.52 | 66% | 2.37x | 0.09 | 2.33 |
| **S3 Pilot** (trend gate + vol target) | +25.5% | 16.1% | 1.50 | -16.9% | 0.6% | 1.24 / 1.56 | 64% | 3.83x | 0.05 | 1.96 |
| B1 VFV only, no leverage | +21.6% | 14.8% | 1.41 | -19.6% | -0.0% | 1.00 / 1.00 | 100% | 0.34x | 0.00 | 1.79 |
| B2 strategic weights, no leverage | +24.9% | 13.8% | 1.69 | -12.9% | 2.0% | 1.00 / 1.00 | 100% | 0.93x | 0.00 | 1.94 |

Short sample (about 3 years after the 1-year warm-up), mostly a bull market: an implementation check, not evidence.
