# Gap-and-go day-trading paper account: pre-registered rules (frozen 2026-10-16, before the open)

Owner request, 2026-10-16: "yes build with 5000". The account trades the way successful retail day traders say they
trade: only "in-play" stocks that gap on news, breaking out of the opening range, with tight stops.
- **Paper only.** IBKR is read-only (daily and 5-minute bars); fills are simulated by `scripts/gapgo_paper.py`.

## Rules

| Rule | Value |
|---|---|
| Start | US$5,000, long only |
| Universe | About 130 liquid US stocks: the platform's stocks plus about 70 high-beta names (PLTR, COIN, MSTR, HOOD, SMCI, ARM, SHOP, CRWD, …; full list in `universe_added.json`). No full-market scanner is available read-only, so true small-cap gappers are missed |
| Candidate | Gap at the open of at least +4% over the prior close. Prior close ≥ US$5. 20-day average dollar volume ≥ US$20M. Ranked by gap size |
| Confirmation (by 09:35) | The first 5-minute bar is green (close above open), and its volume is at least 5% of the stock's 20-day average daily volume |
| Entry | Buy stop at the first bar's high + US$0.01. It must trigger by the 10:25-10:30 bar; the fill is at the trigger, or at the bar's open if the bar opens above it, plus slippage |
| Stop | The first bar's low. R = entry minus stop |
| Size | Risk 1% of equity per trade (shares = 1% of equity ÷ R), capped at 50% of equity in notional |
| Exits | Sell half at +2R and move the stop to breakeven. The rest is stopped or sold at the close. If one bar touches both the stop and the target, the stop counts first |
| Limits | At most 2 trades a day, taken in gap order; one trade per stock |
| Costs | 10 bp slippage per side (more than for SPY, since these stocks move fast). US$0.005 per share, US$1 minimum per order |
| Benchmark | SPY buy-and-hold, and the account's own R statistics |
| Review | After 60 trades, and on 2027-04-15 |

## Evidence and expectation

- **The practitioner case:** gap-and-go and opening-range breakouts on in-play stocks are what full-time momentum day
  traders describe. Zarattini, Barbon & Aziz (2024, SSRN) report a strong opening-range breakout result on "stocks in
  play" (high relative volume at the open), net of costs. We have not verified it.
- **The base rates** (Barber et al. on Taiwan; Chague et al. on Brazil) show almost all individual day traders lose.
- **Prior (D24 scale):** 0.15.

A real edge needs at least 60 trades with mean R above 0 and t above about 2.

The 12 sessions of 5-minute data available before the freeze are replayed once (`warmup.md`). They are labelled as a
warm-up, are not part of the record, and were run after these rules were written.

## Amendments

None.
