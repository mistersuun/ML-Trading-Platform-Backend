# Short-dated SPY option "lottery" accounts: pre-registration (frozen 2026-11-14)

The owner asked on 2026-11-14 whether three US$1,000 accounts can repeat the posts and screenshots of short-dated SPY
call wins: for example, 312 SPY 755 calls expiring the same day, bought for about US$0.32 and sold for US$1.00 on
2026-11-09. Paper only. IBKR is read-only (`get_option_parameters`, `get_option_data`, `get_price_snapshot`). No order
exists anywhere. Rules are frozen here before the first trade; a change needs a dated amendment at the bottom.

## Instrument and timing (all three accounts)

- **Contract.** SPY options expiring on the **next** NYSE session (one session to expiry).
- **Entry.** Bought after the close at the ask of the closing snapshot.
- **Exit.** Held to expiry and settled at **intrinsic value at the next session's close**:
  - calls: max(0, SPY close − strike);
  - puts: max(0, strike − SPY close).
  A worthless option costs nothing more. An in-the-money one pays US$0.65 per contract, like a sale.
- **Why one session and not same-day options.** Intraday option prices for expired contracts cannot be fetched after
  the fact. The next session's contract bought at the close is the same bet, placed the evening before instead of
  the morning of.
- **No intraday selling.** The screenshot's exit near the day's high is not something a fixed rule can know in
  advance. Holding to the close is the honest, mechanical version.
- **Commission.** US$0.65 per contract on entry.
- **Size.** Contracts = floor(stake / (ask × 100 + 0.65)). If that is 0, buy one contract when it costs at most 40% of
  equity (a near-the-money SPY option costs about US$130 to US$300, more than a 20% stake of US$1,000); otherwise
  no trade.
- **No trade** if the quote has no ask, or the expiry is missing from the chain.

## The three accounts

| Account | Direction | Strike | Stake per trade |
|---|---|---|---|
| **Y1 "calls every day"** | Always a call | The first whole-dollar strike at or above SPY close × 1.003 | 20% of equity |
| **Y2 "follow the trend"** | Call if SPY's close ≥ its 20-session average, else put | Call: the first whole-dollar strike ≥ close × 1.003. Put: the last ≤ close × 0.997 | 20% of equity |
| **Y3 "YOLO roll"** | As Y2 | 0.5% out of the money: call ≥ close × 1.005, put ≤ close × 0.995 | **100% of equity**, every session, rolling winnings, as in the posts |

- Each account starts with US$1,000.
- **Busted.** An account is busted, permanently, when its equity is below US$100 (a tenth of the start, too little for an all-in roll). There are no top-ups: the posts
  never show the refills.
- **Benchmark.** US$1,000 in SPY, bought at the first entry close.

## What is reported and decided

- Nightly: equity, trades, wins and losses, best trade, and the benchmark (`report.md`).
- Review on 2027-04-15, with the other aggressive accounts. No statistical pass rule: these accounts exist to show
  what the strategy does, not to graduate. They are not in the master scoreboard's family (that file is frozen).

## Expectations, stated before the first trade

- A one-session SPY option slightly out of the money pays off only on a move larger than its price. Option prices
  usually include a volatility premium and a bid/ask spread.
- So the expected result of every trade is a loss. The edge is negative, by roughly the premium.
- Y1 and Y2 should bleed slowly with occasional spikes.
- Y3 loses everything on its first losing session. Its chance of surviving n sessions is roughly 0.3 to the
  power n.
- The most likely outcome is that Y3 is busted within days, and Y1 and Y2 trail SPY.
- The posts are real outcomes but selected ones: the many accounts that went to zero do not post.
