# Options paper test: pre-registered rules (frozen 2026-10-15)

Owner request, 2026-10-15: "try it yourself and keep me updated", after a discussion of buying calls and puts with a
small account ("some go from $500 to $10k"). This is a **paper-only** experiment:
- No orders go anywhere; IBKR is used read-only for quotes (`get_option_parameters`, `get_option_data`,
  `get_price_snapshot`).
- Fills are simulated by `scripts/options_paper.py` from those quotes.

The rules below are fixed before the first trade. They are not changed after seeing results. If a change is needed,
it is written as a dated amendment below, and later results are reported separately from that date on.

## Book L: "$500 lottery account" (buying calls and puts)

The question it answers: does a small account that buys cheap directional options on a simple trend signal grow, or
does it bleed like the retail data says?

| Rule | Value |
|---|---|
| Start | US$500 cash, never topped up |
| Underlyings | QQQ, SPY (most liquid US option chains, tight spreads) |
| Signal (on the session close, using daily closes) | **Call** when close > 50-day SMA and close ≥ highest close of the prior 20 sessions. **Put** when close < 50-day SMA and close ≤ lowest close of the prior 20 sessions. |
| Priority | QQQ before SPY; at most 1 new position per day; at most 2 open; never 2 on the same underlying |
| Expiry | Listed expiry with 21 to 45 calendar days left, closest to 30 |
| Size | Budget = 30% of book equity (capped at cash). Quantity = whole contracts affordable at ask + commission; 0 means skip |
| Strike | Out-of-the-money only. Among contracts whose cost fits the budget, the one with the largest absolute delta. Needs bid > 0 and spread ≤ max(US$0.05, 20% of mid) |
| Exits (checked every session) | Bid ≥ 2× entry price: take profit. Bid ≤ 0.4× entry price: stop at −60%. 5 days or fewer to expiry: close |
| Benchmark | US$500 in QQQ, bought at the first session's close |
| End | Equity below US$50, or the 2027-04-15 review |

## Book P: "put writer" (selling cash-secured SPY puts)

The question it answers: does collecting the option insurance premium pay, net of the occasional big loss? Comparable
public index: Cboe PUT.

| Rule | Value |
|---|---|
| Start | US$100,000 cash |
| Underlying | SPY |
| Entry | Whenever no put is open: sell 1 put (or more, while cash covers strike × 100 per contract) |
| Expiry | Listed expiry with 30 to 45 days left, closest to 37 |
| Strike | Absolute delta closest to 0.20; needs bid > 0 |
| Exits | Ask ≤ 50% of the entry credit: buy back (take profit). 7 days or fewer to expiry: buy back. No stop loss: the cash-secured put is held through drawdowns, which is the strategy's real risk |
| Benchmark | US$100,000 in SPY, bought at the first session's close |
| Review | 2027-01-15 and 2027-04-15 |

## Common rules

- **Fills:** buy at the ask, sell at the bid, plus US$0.65 commission per contract.
- **Quotes:** taken after the session close (IBKR delayed or frozen snapshot), so the decision and the fill share
  the closing quote.
  - Caveat: a real order would fill the next morning, possibly at a different price.
  - This is less conservative than the stock simulator's next-open fill, though crossing the spread offsets part of it.
- **Expired positions:** a position past its expiry with no quote settles at intrinsic value against the
  underlying's close.
- **Valuation:** positions are marked at the mid price.
  - Book P's cash earns nothing here.
  - Caveat: a real cash-secured account would earn roughly the T-bill rate on its collateral, about 4% a year. The
    report shows this as a note; it is not added to the results.
- **No early assignment, dividends or pin risk.** These are modelled as not happening; the 5-day and 7-day time exits
  make them rare.
- **What gets reported:** equity against the benchmark, the trade list, win rate, and average win and loss.
  - No significance claims before at least 30 closed trades per book.
  - Book L will likely have fewer than that even by April. Its result is an illustration, not evidence.
- **Out of scope:** no strategy order on the stock side, and nothing here feeds allocation or real trades.

## Amendments

None.
