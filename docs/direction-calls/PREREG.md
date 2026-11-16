# Next-day direction calls from news and sentiment: pre-registration (frozen 2026-11-16)

The owner asked on 2026-11-16 whether reading news and market sentiment lets the assistant predict direction well
enough to make short-dated option bets work. Paper only: calls are records, never orders. IBKR is read-only. Frozen
before the first call; a change needs a dated amendment at the bottom.

## What is called

- **Every session, every name.** No skipping, so there is no cherry-picking.
- **Fixed universe** of 10 names: **SPY, QQQ, NVDA, AAPL, MSFT, AMZN, META, TSLA, GOOGL, AMD.** All are topped up
  nightly.
- **The call.** For each name: `up` or `down` for the **next session's close against tonight's close**, plus a
  **confidence** from 0.50 to 1.00 (the probability of being right) and a one-line reason.
- **Inputs** the assistant may use:
  - tonight's prices and recent daily bars (`state/external_bars`);
  - IBKR read-only earnings previews and recaps, snapshots and option-implied volatility;
  - web search for news up to the time of the call.
  No other source is required. The method is the assistant's judgment, documented in the reasons.
- **Proof of timing.** Calls are saved as `docs/direction-calls/calls/<session>.json` and **committed and pushed
  before the next session's open** (9:30 New York time). The commit time proves they came first. A call file
  committed after the open is marked `late` and excluded from scoring.

## Scoring (`scripts/direction_calls.py score`)

- **Outcome.** `up` if the target close is above the call-night close, `down` if below. An unchanged close is
  excluded.
- **Accuracy.** The share of correct calls. Reported in total, per name, and per confidence band
  (0.50-0.59, 0.60-0.69, 0.70 or more).
- **Calibration.** The Brier score (lower is better), against 0.25 for always saying 50%.
- **Baselines**, on the same calls:
  - a coin flip (50%);
  - **momentum**: the direction of the call-night session's own move;
  - **always up**.
- **Significance.** One-sided binomial p-value against 50%. Calls on the same night are correlated (one market
  move moves them all), so the report also gives accuracy on **nights**: the majority call vs the majority outcome
  across the 10 names.

## Pass rule (decided only at these looks: 2026-12-31, 2027-02-26, 2027-04-15)

All of these must hold:

1. **Size.** At least 100 scored calls and at least 30 nights.
2. **Accuracy.** At least **60%**. That is about the break-even for one-session options, from the 2026-11-16
   analysis: about −15% per bet at 55% accuracy, about −8% at 60%, positive only near 60-65%.
3. **Significance.** One-sided binomial p < 0.01 against 50%.
4. **Beats the baselines.** Above both the momentum and always-up baselines.
5. **Nights.** Night-level accuracy above 50%.

A pass allows only a pre-registered **paper** options account driven by these calls. A fail means news-reading
judgment gives no tradable direction edge here. Either way, nothing changes the owner's real account.

## Stated expectations

Published research and this project's own results point to the same thing: daily direction is hard to predict, and
news is mostly priced in before an evening read. The expected result is roughly 50 to 55% accuracy, which is not
enough for options.
