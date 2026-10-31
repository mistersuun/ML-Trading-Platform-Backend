# Cross-sectional ML stock ranker: pre-registered rules and test (frozen 2026-10-31, before any result)

## Why this

The owner asked (2026-10-31) what would help the most and make money. The evidence so far:

- **Day-ahead direction on 17 large caps has no edge.** The ML direction model scores AUC 0.49 to 0.52, and 0 of 340
  signal candidates passed in 12 forward sessions. That is the hardest place to predict.
- **Where machine learning has published, replicated evidence in equities:** ranking stocks against each other over
  the next month, using price-based characteristics (momentum, short-term reversal, volatility, liquidity, distance
  from the 52-week high). Gu, Kelly & Xiu (2020, RFS), "Empirical Asset Pricing via Machine Learning", find tree models
  and neural nets roughly double the out-of-sample Sharpe ratio of linear models on US stocks. Much of the gain sits in
  small, illiquid stocks and shrinks after costs, so a modest result is the honest expectation here.
- So the ML is pointed at the question it can answer (which stocks will do better than the others next month), not at
  the one it cannot (will SPY be up tomorrow).

## Data

- About 125 US stocks the platform already tracks (the 17-symbol watchlist stocks, the investor-portfolio stocks, the
  quant-account stocks and the 72 gap-and-go names), daily IBKR bars, `FIVE_YEARS` (to 2026-10-30). Saved verbatim in
  git-ignored `state/xs_bars/`. SPY for beta.
- **Known bias:** the list was chosen in 2026, and many names (PLTR, APP, HOOD, ...) were added *because* they had
  become popular. Absolute returns of anything on this universe are inflated. The test therefore judges the model
  **against the same universe**: equal weight and plain momentum on the same stocks, the same dates and the same costs.
- Prices are split-adjusted, not dividend-adjusted.

## The rules (frozen)

### Dates

- Rebalance on the last trading day of each month (SPY calendar). Holding period: to the next month-end.
- A stock is eligible on a date if it has at least 252 daily bars up to that date, a close of at least US$5, and a
  21-day average dollar volume of at least US$10M.

### Features (each computed from data up to and including the rebalance close)

1. Returns over 21, 63, 126 and 252 days.
2. 12-1 momentum: the return from day −252 to day −21.
3. Volatility of daily returns over 21 and 63 days.
4. The largest daily return over 21 days.
5. Distance from the 252-day high (close / max close − 1).
6. Log 21-day average dollar volume.
7. Volume trend: 21-day average volume / 126-day average volume.
8. Beta to SPY and idiosyncratic volatility, from 252 days of daily returns.

On each date every feature is converted to a cross-sectional percentile rank in [0, 1] (missing values set to 0.5).

### Target

The stock's return from this month-end close to the next month-end close, converted to a cross-sectional percentile
rank on that date.

### Models (walk-forward, no peeking)

- **ML:** LightGBM regressor, 300 trees, learning rate 0.03, 15 leaves, at least 100 rows per leaf, row and feature
  subsampling 0.8, seed 7.
- **Linear:** ridge regression (alpha 1.0) on the same ranked features, for comparison.
- On each rebalance date the models are refitted on every earlier (date, stock) row whose target month has fully
  closed. The most recent month's target is not known yet and is excluded (a one-month purge).
- The first prediction needs at least 12 months of training dates. Earlier months are not scored. (Set to 12, not 24,
  before any run on real data: with 5 years of bars and a 1-year feature warm-up, 24 would leave only about 22 scored
  months.)

### Portfolios (all on the same eligible stocks and dates)

- **ML top 20%:** the top fifth of eligible stocks by ML score, equal weight.
- **Ridge top 20%:** the same with the ridge score.
- **Momentum top 20%:** the top fifth by 12-1 momentum (the classic rule, no ML).
- **Universe:** all eligible stocks, equal weight.
- **SPY** for reference.
- Costs: 10 bp per side on traded weight (turnover).

### Reported

- Monthly rank information coefficient (Spearman correlation of score and next-month return): mean, t-statistic and
  share of months positive, for ML, ridge and momentum.
- For each portfolio: CAGR, volatility, Sharpe ratio (rf = 0), max drawdown, average monthly turnover, and the
  monthly return in excess of the universe (mean and t-statistic).
- Feature importance of the final ML model.

## What counts as useful (decided now)

The ML ranker is **adopted as a paper account** only if, out of sample and after costs:

1. its mean monthly IC has a t-statistic of 2.0 or more, **and**
2. the ML top 20% beats the momentum top 20% on Sharpe ratio, **and**
3. the ML top 20% beats the universe on mean monthly excess return.

Otherwise it is not adopted, and the report says so. Nothing in this file is changed after the result is seen; a
change is a new, disclosed version. About 34 scored months are expected, which is a small sample: a pass would
justify a forward paper test, not real money.

## Amendments

None.
