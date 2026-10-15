# Day-trading paper test: pre-registered rules (frozen 2026-10-15)

Owner request, 2026-10-15: "try day trading as well". **Paper only.**
- No orders go anywhere; IBKR is read-only (`get_price_history`, 5-minute bars, regular hours).
- Fills are simulated by `scripts/daytrade_paper.py` from those bars.

## Why this strategy

The rule is the 5-minute **opening range breakout (ORB)** on QQQ as published by Zarattini & Aziz (2023), "Can Day
Trading Really Be Profitable? Evidence of Sustainable Long-term Profits from Opening Range Breakout (ORB) Day Trading
Strategy vs. Benchmark in the US Stock Market" (SSRN). The paper reports large outperformance over 2016-2023. We have
**not** verified those results.

It was picked because:
- it is the best-known published day-trading rule with fully mechanical entries and exits;
- it trades the most liquid ETF, so costs are realistic;
- it needs no tuning by us.

SPY runs as a second, independent book with identical rules, to see whether a result holds beyond one ticker.

Prior expectation (D24 scale): published practitioner rule, **prior 0.15** that it has a real edge after costs.

## Rules (one book each for QQQ and SPY)

| Rule | Value |
|---|---|
| Start | US$25,000 each (the US pattern-day-trader minimum) |
| Opening range | First 5-minute bar of the regular session (09:30-09:35 New York time) |
| Direction | Long if that bar closes above its open; short if it closes below; no trade if equal |
| Entry | Open of the second bar (09:35), plus 1 basis point of slippage against us |
| Stop | Long: low of the first bar. Short: its high. Risk R = distance from entry to stop. No trade if R ≤ 0 |
| Target | Entry ± 10R |
| Size | Shares = whole number, risking 1% of the book's equity at the stop; capped at 4× equity in notional (intraday margin) |
| Exit | Bars are checked in order from the entry bar. Stop or target, filled at that level, or at the bar's open if the bar opened beyond it. If one bar touches both, the stop is assumed first (conservative). Otherwise exit at the close of the last bar (15:55-16:00) |
| Costs | 1 bp slippage per side; commission US$0.005 per share, minimum US$1 per order (IBKR fixed pricing); no borrow fee on shorts |
| One trade per day per book | Never re-entered the same day |
| Benchmark | Buy-and-hold of the same ETF over the same sessions |
| Review | After 60 sessions, and on 2027-04-15 |

## What will be reported

- Equity of each book against its benchmark.
- Every trade: R multiple, P&L and exit reason.
- Win rate, average R, and a t-statistic of mean R per trade.
- Max drawdown.

A real edge needs at least 60 trades and a t-statistic above about 2; anything less is an illustration. The roughly
12 sessions of bars available before the freeze (2026-09-29 to 2026-10-14) are replayed once as a **warm-up and
plumbing check**. They are reported separately, not counted in the forward record, and the rules above were written
before that replay was run.

## Amendments

None.
