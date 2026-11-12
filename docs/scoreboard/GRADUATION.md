# Graduation ladder: paper to real money (frozen 2026-11-12)

Companion to `PREREG.md`. Paper only today. Nothing here is advice; the owner decides every real order by hand.

## Stages

**Stage 0: paper.** Every arm today.

**Stage 1: tiny real.** Only if **all** of these hold:

1. The scoreboard **promoted** the arm to candidate at one of the fixed looks (2026-12-31, 2027-02-26, 2027-04-15).
2. Still positive after **3x the arm's own cost and slippage assumption** (from a replay, or a stated sensitivity written down first).
3. **At most 40%** of total P&L comes from the best 2 days.
4. It has lived through at least one **adverse regime**: a 5% or larger SPY drawdown inside its own window.
5. The owner signs off, in writing, with the date.

Tiny real means **at most 1% of the owner's account per strategy**, with a hard stop at **-30% of that allocation**. At the
stop the position is closed and the arm goes back to Stage 0.

**Stage 2: scale up.** Only after **6 more months at Stage 1** that still meet the same tests (the scoreboard test, 3x costs,
the 40% cap, the owner's sign-off). Any size above Stage 1 is a new written decision.

Going back: failing a test at any look moves the arm back one stage. Rules are never loosened to pass a test.

## Do-not-copy register (2026-11-12)

Tempting "leaders" on the window to 2026-11-11. None of them is evidence. Reasons from `docs/reviews/2026-11-12-strategy-review.md`.

| Leader | Headline | Why not |
|---|---|---|
| ORB SPY | +13.4% | Mean R t = 1.26. Three trend days carry everything; on the other 17 sessions it loses. About 20 independent days, and the QQQ and SPY books are one bet (correlation 0.89). The 10R target never fired. |
| Low volatility (quant) | +7.7% vs SPY -0.8% | Alpha t about 1.2; nearly all the excess is two days, one of them SPY -4.4% while it was flat. Best of five accounts, so luck of this size happens 10 to 20% of the time. |
| Fairfax | +25% | Best t 2.2 against a multiple-testing bar near 3.3 for 14 baskets; low-beta in a falling market; priced in CAD against a USD benchmark. |
| TCI | +10.6% | Same: one of 14 baskets, a low-beta tilt in a down tape, no multiple-testing credit. |
| Gap-and-go | +4.8% | 14 trades, t = 1.20; without the best 2 trades the mean is +0.04R. Bar is 60 trades. |

Also not to do: tune any rule on this window, count the two ORB books as 40 trades, retire or crown an options book on 5 closed
trades, or extend the test until something passes.
