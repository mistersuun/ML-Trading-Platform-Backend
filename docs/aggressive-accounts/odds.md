# Aggressive accounts: odds of turning US$1,000 into US$10k-US$50k

Two steps for each stock/ETF strategy: (1) the actual rules run over the daily history on file with real costs; (2) 10,000 three-year paths are resampled from that history in one-month blocks, to estimate how often such a strategy reaches 10x or 50x and how often it loses 80% on the way. The history is mostly a bull market with a hindsight-chosen stock list, so these odds are optimistic.

| Strategy | History window | Total | CAGR | Worst drop | SPY same window | 3y: hit 10x at some point | 3y: end above 10x | 3y: hit 50x | 3y: lose 80% at some point | 3y median ending value of US$1,000 | 3y 25th to 75th pct |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 3x Nasdaq trend (TQQQ above the 200-day line, else T-bills) | 2022-07-27 to 2026-10-15 | +433% | +49% | -37% | +92% | 9.7% | 7.1% | 0.0% | 0.0% | $3,515 | $2,156 to $5,744 |
| All-in top momentum stock | 2023-10-23 to 2026-10-15 | +668% | +99% | -39% | +84% | 49.7% | 42.9% | 3.4% | 0.0% | $8,429 | $4,632 to $15,780 |
| SPY buy-and-hold (reference) | 2022-07-27 to 2026-10-15 | +92% | +17% | -19% | +92% | 0.0% | 0.0% | 0.0% | 0.0% | $1,574 | $1,329 to $1,851 |

## Same, in a normal market

The history above is an unusually strong market (SPY about +21% a year). Re-running the resampling after removing that extra market return (scaled by each strategy's beta, SPY back to about 8% a year):

| Strategy | 3y: hit 10x | 3y: hit 50x | 3y: lose 80% at some point | 3y: end below US$1,000 | 3y median ending value |
|---|---:|---:|---:|---:|---:|
| 3x Nasdaq trend (TQQQ above the 200-day line, else T-bills) | 1.9% | 0.0% | 0.2% | 17% | $2,023 |
| All-in top momentum stock | 14.4% | 0.1% | 0.4% | 9% | $3,187 |
| SPY buy-and-hold (reference) | 0.0% | 0.0% | 0.0% | 25% | $1,185 |

Even this is optimistic for the all-in momentum account: its stock list is today's winners (it could only ever pick NVDA, AVGO, LLY and the like), which a real investor in 2023 could not know.

What it takes: 10x in 3 years is about +115% a year; 50x is about +270% a year, every year. Even the best hedge funds compound at 20-40% a year. The live accounts test whether any of this shows up.

The 3x Nasdaq history before 2022-10-20 uses synthetic TQQQ (3x QQQ daily, minus fees and financing; checked against real TQQQ since 2022-10: correlation 0.9996, total return within a few percent).
