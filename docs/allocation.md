# Allocation proposals and Investment Policy Statement

`allocation.py` turns your holdings and current prices into an advisory rebalance
proposal for the strategic core (90%) and the trend sleeve (10%). It implements
decisions D1 and D4 and the design in `docs/research/portfolio-recommendations.md` section 2.

## What it does

- Computes per-symbol current weight, target, drift and whether the symbol is outside its band
  (outside = drift larger than the larger of 5 points absolute or 25% of the target).
- Proposes whole-share trades. Sells only for symbols outside the band, down to target.
  Buys go to underweight symbols, funded by cash, contributions and sell proceeds.
  Trades under 2% of portfolio value are skipped (buys funded by new contributions excepted).
- Computes the trend signal for SPY, VEA, IEF, GLD, DBC, VNQ: the average of the 8/10/12-month
  SMA votes, from the last completed month only. An asset whose signal is off moves its slice
  (1/6 of 10%, scaled by the score) into T-bills (SGOV).
- `Proposal.render()` gives plain text suitable for printing or for an alert.

## What it does NOT do

- It never places orders and imports nothing from execution, brokers or paper_trader.
  You place every trade by hand, in your own account.
- No shorts, margin, leverage, crypto, forex or futures. No stops or automatic sells on the core.
- It does not fetch prices. The caller supplies `prices` and (optionally) month-end closes.
  Without `monthly_closes` the trend sleeve is assumed fully invested and the output says so.
- Holdings not in the target list are listed as unmanaged and never sold.

## Use

holdings.csv (`CASH` is dollars, other rows are share counts):

    symbol,quantity
    VTI,120
    IEF,60
    CASH,1500

```python
import allocation as al
h = al.load_holdings("holdings.csv")
p = al.propose_rebalance(h, prices, monthly_closes, contributions=500.0)
print(p.render())
```

Cadence: check quarterly (monthly if you are adding contributions; spend contributions on
underweights first). Run it within the first week of a month so the signal month is complete.

## Long-history check (owner-run)

`scripts/long_history_backtest.py` backtests core, core+trend, 60/40 and 100% equity on proxy
series you supply (see the script docstring for the CSV format and proxy names) and writes a
markdown report with excess Sharpe and the 2000-02, 2008, 2013, 2020, 2022 and 1980-2000
windows. `--synthetic` runs it on seeded fake data as a smoke test only. Do this before
trusting any drawdown expectation.

## Investment Policy Statement (template)

Fill in once, sign, and re-read before any change.

1. **Purpose.** Long-term growth with limited drawdowns; horizon of at least 10 years.
2. **Targets.** Core 90%: US total market 32, developed ex-US 18, emerging 5, intermediate
   Treasuries 13, long Treasuries 7, TIPS 7, T-bills 8, gold 5, commodities 0-5 (listed 5)
   as percent of the core. Trend sleeve 10% of the portfolio in SPY, VEA, IEF, GLD, DBC, VNQ
   (long-only, T-bills when off). Platform signal satellite: 0% until the graduation tests pass.
3. **Bands.** Trade a sleeve only when it is off target by more than the larger of 5 points
   absolute or 25% relative. Minimum trade 2 points. Use contributions and dividends first.
4. **Review schedule.** Quarterly allocation check; annual review of this document;
   trend signals read at month-end only.
5. **Drawdown rule.** At -20% from peak a review alert is raised. It is a prompt to re-read
   this IPS, not an instruction to sell.
6. **No-override rule.** I do not change targets, skip a rebalance, or trade outside the bands
   because of news, a forecast or a bad month. Any change waits 30 days after it is written down.
7. **Expected tracking error.** Versus SPY expect 3-6 points per year of difference, with
   some years trailing by that much. Underperforming SPY alone is not a reason to change.
8. **Conditions for changing the policy.** Only: (a) a life change (horizon, income, need for
   cash), (b) a structural change in an instrument (fund closure, fee or tax change),
   (c) new long-history or out-of-sample evidence reviewed at the annual review, or
   (d) a signal-satellite graduation under the documented tests. Record date, reason and
   the evidence for each change.
9. **Tax location.** Trend, TIPS, commodities and REITs in tax-advantaged accounts where
   possible; watch wash sales between similar funds.
