# Y4: weekly US$700 "moonshot" ticket, pre-registration (frozen 2026-11-14)

The owner (2026-11-14): "Some people go from $700 to $1 million in a week... things like these I want you to test and
make." Paper only. IBKR is read-only. Rules frozen before the first ticket.

## The rule

- **When.** Every week, at the close of its **first session** (usually Monday), a **fresh US$700 ticket** is
  spent. Tickets never roll into each other: each week is one independent attempt, so the record counts how often
  the attempt works.
- **Universe.** TSLA, NVDA, AMD, MSTR, COIN, PLTR, SMCI and HOOD: volatile, with weekly options.
- **Name.** The universe name with the highest 5-session return to that close (momentum, as in the posts).
- **Contract.** The call about **3% out of the money**:
  - strike = the first listed strike ≥ close × 1.03, on a grid of US$0.50 below US$25, US$1 below US$200,
    US$2.50 below US$500, US$5 above;
  - expiring at the **week's last session** (usually Friday).
- **Size and price.** All-in: as many contracts as US$700 buys at the closing ask, plus US$0.65 per contract. The
  remainder is kept.
- **Exit.** Held to expiry and settled at intrinsic value on that close (no intraday selling).
- **No ticket** that week if there is no ask, or one contract costs more than US$700. That US$700 is recorded as
  returned.
- **Reported.** Tickets, total staked, total returned, and the count of weeks at 2x, 10x and 1,429x
  (US$700 → US$1M).

## Expectations, stated first

`docs/research/2026-11-14-asymmetric/moonshot_sim.py` is a Black-Scholes approximation over 5 years, 9 volatile names
and SPY/QQQ, rolling all-in one-session options for 5 sessions.

- Mechanical rules (no foresight): **0 of 13,338 weeks** turned US$700 into US$1M, and about 99% of weeks ended
  below 10% of the stake.
- Even knowing each day's direction in advance: 3 of 6,669 weeks did it, all in COIN.

A single weekly call needs about a 1,429x move, so the expected record is mostly zeros, an occasional 2x to 10x, and no
US$1M week. If one happens, it will be in the report.
