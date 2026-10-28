# Portfolio Pilot: pre-registered rules and test plan (frozen 2026-10-28, before any backtest number)

## Why this, and not more trading signals

The owner asked (2026-10-28) for the ML trader to be made useful. The evidence so far:

- **Short-term signals show no edge.** 0 of 340 symbol × pattern candidates passed the gates on any of 9 forward sessions. The ML direction model scores AUC 0.49 to 0.52, which is a coin flip. Short-term prediction of large-cap prices from daily bars is the hardest place to find an edge. More tuning there produces overfitting, not money.
- **The owner's money is at risk somewhere else.** The real account (IBKR, CAD) is levered, with a margin loan at 3.58% that the owner keeps by choice, and concentrated in resource ETFs (gold miners, gold, uranium, copper) that move together. The exact figures are private and kept in git-ignored `state/pilot/`.
- **What has a long, replicated evidence base** is controlling how much risk is held, not predicting returns:
  - **Volatility forecasting works.** Moreira & Muir (2017), "Volatility-Managed Portfolios"; Harvey et al. (2018), "The Impact of Volatility Targeting". Volatility clusters and is forecastable; returns are not. Scaling exposure by forecast volatility cut equity drawdowns and left tails over a century of data.
  - **Trend filters on commodity-like assets avoid most of their long bear markets.** Moskowitz, Ooi & Pedersen (2012); Hurst, Ooi & Pedersen (2017); Faber (2007). Examples: gold miners −80% in 2011-2015, uranium −90% in 2007-2016.
  - **Leverage is only defensible on a diversified, risk-controlled portfolio.** Asness, Frazzini & Pedersen (2012), "Leverage Aversion and Risk Parity".

The Pilot therefore uses statistics and ML where they are known to work, forecasting risk, and applies it to the account the owner actually owns. It never places orders (D1); it proposes trades that the owner places by hand.

## The rules (frozen)

### Sleeves and strategic weights

Fractions of an unlevered portfolio that sums to 100%. A sleeve is held through the owner's TSX ETFs. The proxy is used only for the long-history test.

| Sleeve | Group | Weight | Held through (TSX) | Long-history proxy (US) |
|---|---|---:|---|---|
| US broad equity | core | 26% | VFV / XUU | SPY |
| Nasdaq-100 | core | 8% | HXQ | QQQ |
| International developed | core | 14% | XEF | EFA |
| Canadian equity (banks, low vol) | core | 12% | ZEB / ZLB | EWC |
| Utilities (defensive) | core | 4% | ZUT | XLU |
| Bonds | core | 8% | HBB | IEF |
| Gold miners | satellite | 8% | XGD | GDX |
| Gold bullion | satellite | 6% | CGL.C | GLD |
| Uranium | satellite | 6% | CCO / HURA | CCJ |
| Copper | satellite | 3% | COPP | FCX |
| Semiconductors | satellite | 5% | CHPS | SMH |

Core totals 72% and satellites 28%. The weights follow the core/satellite split proposed to the owner on 2026-10-22, rounded. They were not optimised on any return.

### Rule 1: trend gate on the satellites

- Each week, on the last completed weekly close, each satellite gets a score of 0, 1/3, 2/3 or 1. The score is the average of three votes:
  - the close is above its 40-week average (about 200 trading days);
  - the 26-week return is positive;
  - the 52-week return is positive.
- A satellite's weight is its strategic weight × its score.
- Weight freed by the gate is spread over the core sleeves in proportion to their weights. It is not held as cash, because the owner's goal is long-term growth.
- Core sleeves are not trend-gated. They are always held.
- A sleeve with fewer bars than the longest lookback (52 weeks, or 252 days) counts as having **no data yet**: its strategic weight is spread pro rata over the sleeves that have data. The same applies to the covariance window.

### Rule 2: volatility target, which sets the leverage

- Forecast covariance: an exponentially weighted (EWMA) covariance of weekly returns with a 13-week half-life, annualised × 52. In daily use: daily returns, a 63-day half-life, × 252.
- Portfolio forecast volatility: σ = √(wᵀ Σ w).
- Leverage L = clip(**15%** / σ, **0.5**, **1.5**).
  - 15% is about the long-run volatility of the S&P 500. The aim is to hold about as much risk as an S&P investor, but diversified, with leverage added when markets are calm and removed when they are not.
  - The 1.5 cap keeps the account about 40% away from a margin call even at full leverage.
- Below 1.0, the rest is held as cash.

### Rule 3: when to trade

- Signals and leverage are recomputed weekly.
- The book is rebalanced to target only when any sleeve's actual weight differs from target by more than **3 points of net value**, or the actual leverage differs from target by more than **0.10**.
- Proposals are rounded to whole shares, and trades under C$100 are skipped.

### Costs and financing in the test

- **Trading cost:** 10 bp on every traded dollar.
- **Borrowing:** the borrowed amount, (L − 1) × equity, pays **3.58% a year** (the owner's rate) every week, for the whole history. This is conservative for 2009-2021, when rates were near zero.
- **Cash:** cash below L = 1 earns nothing.

## The test (one look)

1. **Long history:** weekly bars of the proxies over about 19 years (IBKR limit of 1,000 weekly bars), starting once SPY has 52 weeks of history; sleeves whose proxy is younger enter when they have 52 weeks of their own. This includes 2008, the 2011-2015 commodity bust, 2018, 2020 and 2022. A sleeve whose proxy has no data yet has its weight spread over the others pro rata, and the report says so.
2. **Recent, real instruments:** daily bars of the actual TSX ETFs over about 4 years (IBKR limit of 1,000 daily bars). The same rules run daily; a sleeve without history yet is handled as in 1.

Strategies compared, all on the same data:

| Code | Strategy |
|---|---|
| S0 | **The account as held on 2026-10-22**: those weights, mapped to the same proxies, at its current leverage held constant, rebalanced monthly (private input; results only in the git-ignored copy) |
| S1 | The strategic weights, static, at a constant 1.45× leverage, rebalanced monthly |
| S2 | S1 plus the trend gate (rule 1) |
| S3 | **The Pilot**: S2 plus the volatility target (rules 2 and 3) |
| B1 | SPY (or VFV) unlevered |
| B2 | The strategic weights unlevered, monthly rebalance |

Reported for each: CAGR, annual volatility, Sharpe ratio (rf = 0), max drawdown, worst 12 months, average and maximum leverage, the lowest equity/gross ratio (margin cushion, with a margin call assumed at 30%), turnover and costs.

What would count as useful:
- S3 has a smaller max drawdown and worst 12 months than S0 and S1, with a CAGR within about 2 points of S1 or better.
- If S3 instead loses on both return and drawdown, it is not adopted, and the report says so.
- No parameter in this file is changed after the result is seen. A change would be a new, disclosed version.

Known limits:
- The long history uses US proxies in USD. The owner holds CAD-listed versions, so currency effects are not modelled.
- CCJ, a single stock, stands in for uranium, and FCX, a single stock, for copper.
- The four-year sample of the real ETFs is short.
- Neither test is a forecast.

## Amendments

None.
