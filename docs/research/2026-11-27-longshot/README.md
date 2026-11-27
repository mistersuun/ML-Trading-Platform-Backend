# Can cheap options be chained to 5-10x? A long-shot study with adversarial review (2026-11-27)

The owner on 2026-11-27: with enough calendar checking, data analysis, correlations and fear indicators there should be
a way to make 3-4 consecutive 5-10x option bets work. Paper research only; nothing here places or recommends a trade.

## Data and method

- SPY daily bars (2021-11-29 to 2026-11-25) and CBOE VIX, VIX9D and VIX3M daily closes from IBKR.
- Macro release dates (FOMC, CPI, NFP, PCE) from federalreserve.gov, bls.gov and bea.gov (`events.csv`).
- Earnings dates for 16 large companies from SEC 8-K Item 2.02 filings (`earnings.csv`).
- Option prices: Black-Scholes on VIX9D with a smile fitted to 33 real SPY quotes of 2026-11-24/25/27 (`calibrate.py`, rms
  error 7% for 1-session and 5% for 5-session options). Real quotes showed 1-session far-out calls cost 2-6x a flat-smile
  model: lottery-ticket demand.
- Trades: buy at the close a call or put 1-3 implied sigmas out of the money expiring in 1, 2 or 5 sessions; hold to
  expiry, or sell at 5x if reached at a close. Spread and commission charged. Discovery 2021-11..2024-12, hold-out
  2025-01..2026-11.
- Conditions fixed in advance: a release in the window, VIX >= 25, inverted short end (VIX9D/VIX >= 1.05), backwardation
  (VIX/VIX3M >= 1), a 2-sigma day, SPY >= 5% below its 20-day high, VIX up >= 10%, Friday entry.
- One adversarial reviewer (larger model) re-ran everything (`review/`). Where study and review disagree, the review stands.

## Verdicts

| Finding | Verdict |
|---|---|
| 1-session SPY puts lose on average (about 0.3-0.5 back per dollar) | survives |
| 1-session SPY calls lose on average (0.66-0.83 per dollar) | weakened: at weekday prices about break-even (~1.0) |
| 5-session 1-sigma calls return 1.1-1.8 per dollar | **refuted**: pricing artifact (the smile was fitted one week after a volatility spike, when call wings are cheapest); 6-20% dearer wings take it to 0.5-1.0, in line with published evidence that OTM index calls lose on average |
| Calls bought on fear days (VIX >= 25) beat their cost | weakened; 1-2 session versions refuted (excluding the top 5 dates: 0.16-0.71 per dollar; 2025-04-08 alone is 0.61 of the 1-session hold-out); 78% of signal days are 2022 |
| Release days (FOMC/CPI/NFP) raise long-shot payoffs | no: up in discovery, gone in the hold-out |
| Any put condition beats 1 in both periods | no |
| A 3-step all-in chain to 1,000x | about 1 in 4,000 to 1 in 12,000, before model-price error; the best signal's 1-in-1,900 relies on one 39x ticket (2022-11-09, day before CPI) |
| Earnings tickets for MRVL/CRM/SNOW (12-04 expiry) replayed over 20 past quarters | 0.32-0.83 back per dollar, except SNOW 305C at 1.32, which comes from one +60% quarter; weakened further because reports issued with acquisitions (the largest moves, e.g. CRWD 2026-11-23, ADBE 2022-09-15) were missing from the date list |

## Errors found by the review

- Three releases on market holidays were dropped instead of moved to the next session.
- The exit half-spread reused the entry half-spread (EV overstated by 0.02-0.05).
- SPY had no Tuesday/Thursday expiries before 2022-11-14: about 40% of 2022 one-session trades were not tradable.
- 5-session windows overlap: block standard error 0.21-0.23 (naive 0.13). The hold-out 1.12 is not above 1.
- The earnings list kept only after-close reports; before-open reports, often with acquisitions, were dropped.

## Conclusion

Nothing found raises the chance of a 5-10x option enough to pay for its price in both periods once real prices and the
reviewer's exclusions are applied. Very short-dated far-out options are priced above their payoffs (buyers pay for the
lottery). A chain of three 10x bets stays around 1 in several thousand. What the data does support: fear spells (high VIX)
are when big up-moves happen, so a small fixed stake on longer-dated calls in those spells is the least-bad long shot; it is
not a reliable edge.

## Files

`calibrate.py`, `sim_spy.py`, `conditions.py`, `chain.py`, `earnings_moves.py`; outputs `results_*.txt`; inputs
`events.csv`, `earnings.csv`, `calib.json`; the review's scripts in `review/`. The trade table and the IBKR index histories
stay in the scratch folder.
