# Y7 "Mover sprint": follow the day's biggest mover, pre-registration (frozen 2026-11-23)

The owner on 2026-11-23, with screenshots of a trader rolling same-day NVDA puts down strike by strike on
2026-11-20: "Don't just look at SPY, see what people are doing. I know it's a gamble, but from news and events we can
somewhat know what the markets will do." Paper only. IBKR is read-only (`get_*` tools); nothing here places or
prepares an order. Frozen before the first check; a change needs a dated amendment at the bottom.

Y6 (SPY puts only) keeps running unchanged, so the two can be compared.

## What the account does

It reacts to what is already moving. It does not predict.

- **Start and end.** US$1,000. It runs until US$200,000 or until it is busted (cash below US$100 with no position
  and no order). No refills.
- **Names.** SPY, QQQ, NVDA, AAPL, MSFT, AMZN, META, TSLA, GOOGL, AMD.
- **Check times.** Regular checks at 10:05, 11:05, 12:05, 13:05, 14:05 and 15:05 New York time; a final check at
  15:50; a fill check 17 to 40 minutes after any order. On a half day the 12:05 check is the final one.

## Picking the mover (regular checks up to 14:05, no position and no order)

- **A mover** is a name whose real-time price is beyond both today's open and the prior close in the same
  direction, by at least one "typical day" measured from the prior close.
- **Typical day**, fixed here: SPY 1.0%, QQQ 1.3%, AAPL 1.7%, MSFT 1.7%, AMZN 2.0%, GOOGL 2.0%, META 2.3%,
  NVDA 3.0%, TSLA 3.5%, AMD 3.5%.
- **Ranking.** Movers are ranked by their move divided by their typical day. Down means puts, up means calls.
- **Contract.** The nearest listed expiry (the same day when there is one). The strike is at least half a typical
  day out of the money from the real-time price.
- **Affordability.** Of the first four listed strikes from that level outward, the nearest one whose delayed ask
  fits the size rule. If none fits, the second-ranked mover is tried the same way. If that fails too, no order.

## Orders, fills and size

- **Why fills are deferred.** Option quotes on this connection are 15 minutes delayed. An order decided on a
  real-time price must not fill on an older quote.
- **Fill.** The order fills at the ask of a quote requested 17 to 40 minutes after the order, which shows the market
  just after the decision. An order with no fill check in that window is cancelled.
- **Size at the fill.** contracts = floor(0.5 × cash / (ask × 100 + 0.65)). If that is 0, one contract is bought
  when it costs at most 75% of cash. Otherwise the order is cancelled.
- **Entries per day.** At most 2 fills. No new order that day after a stop.

## Exits

- **Take profit.** At a regular check, the delayed bid is at least 2.0 × the entry price: sell at that bid. A new
  order is allowed at the same check.
- **Stop.** The delayed bid is at most 0.5 × the entry price: sell at that bid.
- **Final check.** Sell at the delayed bid (0 if there is none). Nothing is held overnight, even when the option
  has days left.
- **Missed final check.** An open position is recorded as a total loss.
- **Commission.** US$0.65 per contract per side.

## Stated before the first check

- **What the screenshots show.** The trader started with clips of about US$10,000 to US$40,000, not US$1,000, and
  rolled puts from the 140 strike down to 125 on a day NVDA fell 14%. Pages 4 to 8 of 8 show about US$810,000
  bought and US$1,204,000 sold. The three most recent pages are missing and the last puts bought (about US$288,000)
  show only one sale, so the day's result cannot be read from them.
- **That method follows a trend; it does not forecast.** Each put was bought after the stock had already fallen.
  This account copies that part.
- **Forecasting from news has not worked here so far.** The nightly direction calls, which use news and base
  rates, are 12 of 40. Calling the same direction as the previous day would have scored 26 of 40 in the same
  stretch.
- **Days like 2026-11-20 are rare.** On an ordinary day a 1-typical-day move often reverses, and each reversal
  costs about half the stake. The sizing is the same as Y6, so the odds are of the same order: a fraction of a
  percent to reach 200x unless the win rate is well above one in three.
- **Hourly checks and delayed quotes** miss fast moves. Stock options are also thinner than SPY's: spreads are
  wider and the delayed bid can be far from a real fill.
- **The honest expected outcome is a bust.**
