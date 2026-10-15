# Five quant paper accounts: pre-registered rules (frozen 2026-10-15)

Owner request, 2026-10-15: "run 5 accounts starting with 1000 each for one month with each different quant strategies
backed by analysis". **Paper only**: daily IBKR bars (read-only), fills simulated by `scripts/quant_accounts.py`.

## Common rules

| Rule | Value |
|---|---|
| Accounts | 5 × US$1,000, fractional shares, long only, no leverage |
| Live window | First decision at the close of 2026-10-15, first fills at the 2026-10-16 open. The test ends at the close of 2026-11-16 |
| Timing | Decide on a session's close, fill at the next session's open |
| Rebalancing | Monthly strategies decide on the first day and on each month's last session (so they trade on 10-16 and 11-02) |
| Rebalance band | A trade smaller than 2% of equity is skipped, unless it opens or closes a position |
| Costs | 5 bp slippage per side. Commission US$0.0035 per share, minimum US$0.35, capped at 1% of the trade value (IBKR Pro tiered style). On US$1,000 accounts the minimum matters, which is part of the lesson |
| Benchmark | SPY from the same first open |
| Stock universe | 32 US large caps: AAPL ABBV ADBE AMD AMZN AVGO BAC BRK.B COST CRM CSCO CVX GOOG GS HD JNJ JPM KO LLY META MRK MSFT NFLX NVDA ORCL PEP PG TSLA UNH V WMT XOM |

## The five strategies and their evidence

| # | Account | Rule | Evidence | Prior (D24 scale) |
|---|---|---|---|---|
| 1 | Trend following | SPY, EFA, IEF, GLD, DBC get 20% each when their 12-month price return is positive; any sleeve that is "off" goes to T-bills (SGOV). Monthly | Moskowitz, Ooi & Pedersen (2012), "Time Series Momentum": 58 futures, 1985-2009. Hurst, Ooi & Pedersen (2017), "A Century of Evidence on Trend-Following": positive in every decade since 1880, best in crises. Faber (2007) on tactical asset allocation. Weak spell in the 2010s | 0.8 |
| 2 | Cross-sectional momentum | Top 5 of the universe by return from 12 months ago to 1 month ago, equal weight. Monthly | Jegadeesh & Titman (1993): about 1% a month for past winners minus losers. Asness, Moskowitz & Pedersen (2013), "Value and Momentum Everywhere". Main risk: sharp crashes after bear-market rebounds (Daniel & Moskowitz 2016) | 0.8 |
| 3 | Low volatility | The 6 stocks with the lowest 1-year daily volatility, equal weight. Monthly | Blitz & van Vliet (2007), "The Volatility Effect". Baker, Bradley & Wurgler (2011). Frazzini & Pedersen (2014), "Betting Against Beta": similar return with less risk over long periods. Lags in strong bull markets | 0.7 |
| 4 | 52-week-high proximity | The 5 stocks whose close is nearest their 52-week high, equal weight. Monthly | George & Hwang (2004), "The 52-Week High and Momentum Investing": nearness to the 52-week high predicts returns, explains much of momentum, and does not reverse in the long run | 0.6 |
| 5 | Short-term mean reversion | SPY, QQQ, IWM, DIA. Buy a 25% slice when the close is above its 200-day average and RSI(2) is below 10; sell when the close is above its 5-day average. Checked daily | Jegadeesh (1990) and Lehmann (1990) on short-term reversal. Connors & Alvarez (2009), the practitioner RSI(2) rule. Edges like this shrink after publication (McLean & Pontiff 2016: anomaly returns are about 58% lower after publication) | 0.15 |

The "backed by analysis" part is in `backtest.md`. The same code, rules and costs run over the roughly 3 to 4 years of
daily history on file, before the live month starts. It reports total return, Sharpe, max drawdown and beta for each
strategy. Above all, it reports **how widely a single month lands around SPY by chance**, which is what the live month
should be judged against.

## What one month can and cannot show

- **It can show** each strategy's mechanics running live: entries, rebalances, the cost drag of a minimum commission
  on a US$1,000 account, and how differently five rule sets behave in the same market.
- **It cannot rank** the strategies.
  - Monthly results from these rules spread several percentage points around SPY by chance (see `backtest.md`).
  - The published edges are worth a few percent a year: a fraction of one month's noise.
- **Biases in the history:**
  - The universe is today's large caps (survivorship bias).
  - The window is a strong, tech-led bull market. This flatters momentum and 52-week-high rules and hurts low
    volatility and trend.

## Amendments

None.
