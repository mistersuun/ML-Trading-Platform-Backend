# Y5 "Manager's book": research-desk options account, pre-registration (frozen 2026-11-17)

The owner asked on 2026-11-17 for the assistant to "act as the expert manager overseer" with research agents bringing
back information "for you to decide on strategies", and for trades that can be closed for a profit and replaced, not
only held to expiry. Paper only. IBKR is read-only (`get_*` and `search_*` tools); nothing here places or prepares an
order. Frozen before the first decision; a change needs a dated amendment at the bottom.

## How a decision is made (every session, after the close)

1. **Research desk** (subagents; the manager never trades without their reports):
   - **Scouts**, run on the smaller model:
     - news and calendar: macro events, earnings in the next 5 sessions, company news for the universe;
     - price action: trend, momentum, volatility and key levels from the daily bars;
     - options pricing: implied volatility against realized, the implied move, and what the candidate contracts
       cost.
   - **Analysts**, run on the larger model: one argues **for** each candidate trade, one tries to **refute** it (a
     red team).
2. **The manager** (the main session) reads the reports and decides: open, close or hold, or do nothing. "No trade"
   is always allowed and is recorded.
3. Each decision is written to `decisions/<session>.json` with its thesis and the scouts' and analysts' main
   points. It is committed **before the next open**.

## What may be traded

- **Universe.** SPY, QQQ, NVDA, AAPL, MSFT, AMZN, META, TSLA, GOOGL, AMD: the direction-call universe, topped up
  nightly.
- **Instruments.** **Long calls or long puts only**, so the risk is defined: the most a trade can lose is its cost.
  Expiry is 1 to 45 days out.
- **Fills.**
  - Buys fill at the closing **ask**. Sells fill at the closing **bid**. US$0.65 per contract per side.
  - An option held to expiry settles at intrinsic value on the underlying's close.
- **Take-profit orders.** Each trade may carry a limit sell price, set at entry or changed on a later night.
  - It fills at that price if the option's **session high** on a later day, from IBKR's intraday option bars,
    reaches at least that price. This is how a resting limit order works. The session high is recorded in the
    quotes file.
  - If no intraday bars are available, the order did not fill.
- **Stops are nightly.** A stop is only checked at the close and fills at the closing bid.

## Risk rules (enforced by `scripts/manager_book.py`; a violating order is rejected)

| Rule | Limit |
|---|---|
| Start | US$1,000 |
| Cost of one new trade | At most **20% of equity**. Exception: one contract may be bought if it costs at most 30% of equity |
| Total cost of open trades | At most **50% of equity** after the new trade |
| Open positions | At most 3, at most 1 per underlying |
| New trades per night | At most 2 |
| Halt | No new trades while equity is below US$400 |
| Never | Selling options, spreads, margin, averaging down into a losing position |

## What is reported and judged

- **Nightly `report.md`.** Equity against US$1,000 in SPY, each trade with its thesis and outcome, the hit rate, the
  average win and loss, and the same numbers for Y2 (the mechanical trend account) as a comparison.
- **Looks.** 2026-12-31, 2027-02-26 and 2027-04-15, the same as the scoreboard.
- **What would count as evidence.** At least 30 closed trades, equity above both SPY and Y2, and a positive result
  without the best 2 trades. Until then this is a demonstration, not a track record.
- **Real money.** Nothing here places or recommends a real trade. The owner decides and acts alone.

## Stated before the first decision

- Discretionary judgment on news has not shown an edge anywhere in this project. The direction-call test starts
  from a coin flip, and the multi-agent backtests of 2026-11-14 were all negative.
- The honest expectation is a result near that of buying options at random: a slow loss, with spikes.
- The risk rules exist so that the account survives long enough to find out.
