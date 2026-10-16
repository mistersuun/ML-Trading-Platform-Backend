# Aggressive paper accounts: pre-registered rules (frozen 2026-10-16, before the open)

Owner request, 2026-10-16: "I don't want safe, I want to try big returns from a $1,000 account, for example get to
10-50k". **Paper only.**
- Daily and option quotes come from IBKR (read-only).
- Fills are simulated.
- Nothing here is a recommendation to put real money in.

## Three accounts, US$1,000 each

| Account | Rule | Why it can make big multiples | How it fails |
|---|---|---|---|
| **A. 3× Nasdaq trend** | 100% in TQQQ while QQQ closes above its 200-day average, otherwise 100% in T-bills (SGOV). Checked every session; decide at the close, fill at the next open | 3× leverage on the Nasdaq-100 in up-trends. The 200-day filter tries to sidestep the long bear markets that wreck leveraged funds (Gayed & Bilello 2016, "Leverage for the Long Run") | Whipsaws: sharp drops before the filter reacts (a 3× fund can fall 30% in days, as in March 2020), and repeated in/out losses in choppy markets. Leveraged-ETF decay in sideways markets |
| **B. All-in momentum stock** | 100% in the single stock of the 32-stock universe with the best 12-1 month return. Rebalanced monthly (decide on the month's last session, fill at the next open) | One big winner can multiply the account (the NVDA kind of year). Momentum has the best academic record of the stock factors | No diversification: a single stock can halve on one earnings report. Momentum crashes |
| **C. Momentum calls (Book M in `scripts/options_paper.py`)** | Up to 2 open positions, one per stock, at most one new entry per session. Each entry spends up to 50% of the book's equity on whole contracts | Options leverage on the strongest stocks: a 3× take-profit per trade, with compounding | Most such options expire worth much less; paying the spread and time decay; −60% stops |

Account C's rules in full:
- **Stock:** the strongest stock by 12-1 month momentum among the top 5 that has an affordable call; one already held is skipped.
- **Contract:** a call with 60 to 120 days left (closest to 90), out of the money, delta closest to 0.35.
- **Spread limit:** spread ≤ max(US$0.10, 25% of mid).
- **Exits:**
  - bid ≥ 3× the entry price: take profit;
  - bid ≤ 0.4× the entry price: stop at −60%;
  - 30 days or fewer to expiry: close.

All three use the same cost model as the other paper accounts:
- **Stocks and ETFs:** 5 bp slippage, IBKR-style commissions, fractional shares.
- **Options:** buy at the ask, sell at the bid, US$0.65 per contract.

Account C uses closing quotes, with the same caveat as Books L and P.

## Dates

- **Accounts A and B:** first decision on the 2026-10-15 close, first fills at the 2026-10-16 open.
- **Account C:** first evaluated at the 2026-10-16 close.
- **Review:** 2027-04-15 (six months). No rule changes before then; any amendment is dated here and reported separately.

## Odds, worked out before starting (`odds.md`)

The rules were run over the daily history on file with real costs. Then 10,000 three-year paths were resampled from it:

| Account | Chance of hitting 10× within 3 years (that history) | Same, in a normal market (SPY about 8% a year) | Chance of hitting 50× | Median value of US$1,000 after 3 years (normal market) |
|---|---:|---:|---:|---:|
| A. 3× Nasdaq trend | 9.7% | 1.9% | ~0% | US$2,023 |
| B. All-in momentum stock | 49.7% | 14.4% | 0.1% to 3.4% | US$3,187 |
| SPY (reference) | 0% | 0% | 0% | US$1,185 |

Account B's odds are inflated, because its stock list is today's winners (survivorship bias). There is no option-price
history on file, so Account C has no historical estimate. Published data on retail option buyers shows losses on
average.

Honest expectation:
- **Account A** is the most likely of the three to grow a lot without blowing up.
- **Account B** has the widest range of outcomes.
- **Account C** is the most likely to lose most of its money, and also the one with the fastest upside.
- 50× is a lottery outcome for all three.

## Amendments

None.
