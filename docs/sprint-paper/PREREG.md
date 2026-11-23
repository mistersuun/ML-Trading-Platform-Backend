# Y6 "Put sprint": US$1,000 to US$200,000 with intraday exits, pre-registration (frozen 2026-11-20)

The owner on 2026-11-20: "Try to run puts now, don't necessarily wait for expiry; the goal is to bring an account
to 200k from 1000, start fresh." Paper only. IBKR is read-only (`get_*` tools); nothing here places or prepares an
order. Frozen before the first check; a change needs a dated amendment at the bottom.

## What is different from Y1 to Y5

- **Fills use live quotes during the session**, at fixed check times. Buys fill at the ask and sells at the bid.
- **Positions are closed during the day**: at a profit target, at a stop, or at the last check. They are never held
  to expiry and never held overnight.
- The account runs until it reaches **US$200,000 (200x)** or is **busted** (cash below US$100 with no position).
  There are no refills.

## Check times

- **Regular checks:** 10:05, 11:05, 12:05, 13:05, 14:05 and 15:05 New York time.
- **Final check:** 15:50.
- **Half days** (close at 13:00): the 12:05 check is the final one.
- **Missed checks:** a check that does not run is skipped; nothing is back-filled. If the final check is missed, any
  open position is recorded as a total loss.

## Rules

- **Instrument.** SPY puts expiring **the same day**.
- **Strike.** The first whole-dollar strike at or below spot × 0.995 at the check (about 0.5% out of the money).
- **Entry.** At a regular check up to 14:05, with no open position, buy when both hold:
  - SPY's last price is below the prior session's close;
  - SPY's last price is below today's open.

  Otherwise wait for the next check.
- **Entries per day.** At most 2. No new entry that day after a stop.
- **Size.** Spend up to **50% of cash**:
  - contracts = floor(0.5 × cash / (ask × 100 + 0.65));
  - if that is 0, buy one contract when it costs at most 75% of cash;
  - otherwise no trade.
- **Take profit.** At any check, if the bid is at least **2.0 × the entry price**, sell everything at the bid. A
  new entry is allowed at the same check if the entry rule still holds: a fresh strike 0.5% below the new spot,
  sized on the new cash.
- **Stop.** At any check, if the bid is at most **0.5 × the entry price**, sell everything at the bid.
- **Final check.** Sell any open position at the bid (0 if there is no bid).
- **Commission.** US$0.65 per contract per side.
- **Target.** If equity (cash plus the position at the bid) reaches US$200,000 at a check, sell and stop.

## What is reported

`report.md`: equity, every trade with entry and exit times and prices, win rate, and US$1,000 in SPY from the
first check as the benchmark. The owner is told when the account doubles from its last reported level, is busted,
or reaches the target.

## Stated before the first check

- **The chance of reaching the target is small.**
  - Each trade either roughly doubles half the cash (account × 1.5) or halves it (account × 0.75).
  - A fairly priced option doubles before it halves about one time in three. Spreads and time decay make it a
    little less.
  - A simulation of this sizing gives the chance of reaching 200x before going bust as:

    | Win rate | Chance of 200x |
    |---|---|
    | 30% | about 0.03% |
    | 33% | about 0.4% |
    | 40% | about 17% |

  - The account is busted in almost every other path.
- **Hourly checks miss moves between checks.** The stop can fill well below 0.5x, and a spike can come and go
  unseen.
- **Puts only is a bet on the market falling.** In a rising market the entry rule rarely triggers and the account
  mostly waits.
- **The honest expected outcome is a bust.** The account exists to show the path, with real quotes.

## Amendment 2026-11-20, 11:10 New York (before the first trade)

**What was found.** At the 11:05 check the entry rule fired for the first time, and the option quote came back marked
*delayed*: this account's option quotes are 15 minutes old, while SPY itself is real-time. Buying at a 15-minute-old
ask on a real-time signal would be a look-ahead in the account's favour (at 11:05 the 701 put showed an ask of 1.14
from 10:51, when SPY was about 3 points higher). No trade had been recorded.

**The change.** Every check is priced **as of the option quote's own time**, about 15 minutes before the check.

- **Quote time.** The option quote's last-trade timestamp.
- **SPY for the entry rule and the strike.** The close of SPY's 1-minute bar that contains the quote time, not the
  real-time price.
- **Fills.** Buys at the delayed ask and sells at the delayed bid, as before.
- **Strike match.** The quoted put must be the strike the rule gives for SPY at the quote time. If it is not, the
  right strike is quoted once more; if that still does not match, there is no entry at this check.
- **Stale quote.** A quote whose time is missing or more than 25 minutes before the check gives no entry; an open
  position is held (at the final check it is sold at that bid regardless).

So the account acts on what was true at about 09:50, 10:50, 11:50, 12:50, 13:50, 14:50 and, for the final check,
15:35. Nothing else changes.

**Not back-filled.** The 10:05 check was done with the real-time price and found no entry; it stands as recorded.

## Amendment 2, written 2026-11-20 13:15 New York, effective from the 2026-11-23 session

**What was found.** At the 13:05 check the rule's strike (697) was not listed: after the morning's fall, today's
chain had only 695 and 700 below 701. The script needs the exact strike, so there was no re-entry. That stands for
the rest of 2026-11-20.

**The change, from 2026-11-23.** When the rule's strike is not listed, the nearest listed strike below it is used,
provided it is no more than 1% below SPY at the quote time. Otherwise there is no entry.

## Amendment 3, 2026-11-23 09:30 New York (owner's request; before the day's first check)

The owner on 2026-11-23: "And not just puts, right? Calls work as well. Same to Y6."

**The change, from the 2026-11-23 session.** The account also buys calls, as the mirror image of the put rule.

- **Entry.** When SPY (at the quote time) is above both the prior close and today's open, buy a same-day call.
  Below both still means a put. In between, wait.
- **Strike.** The first whole-dollar strike at or above SPY x 1.005.
- **Unlisted strike.** The nearest listed strike above it, if it is no more than 1% above SPY.
- **Everything else** (size, take profit, stop, final check, two entries a day) is the same for calls and puts.

The account's name stays "Put sprint" in the files. Its first day (2026-11-20, puts only) stands as recorded.
