# Asymmetric option bets: multi-agent research, 2026-11-14

**Question (owner, 2026-11-14):** can a disciplined process find "big payoff, capped loss" option bets, like the
screenshots of +200% days, and run them on a US$1,000 paper account?

**Method:** a 10-agent workflow, offline, on 5 years of daily bars (about 134 US stocks and ETFs). No IBKR calls and
no private files.

1. Three designers proposed 9 mechanical strategies, one designer per lens: catalyst, momentum and capitulation.
2. A skeptical judge froze 3 of them. Each spec named its controls, stress runs and a go/no-go rule before any
   backtest ran. The other 6 were rejected with reasons (`rejected.json`).
3. One agent backtested each frozen spec; another agent tried to refute each backtest by re-running it and
   re-implementing it independently.

Option prices are a **Black-Scholes approximation**: implied volatility = 1.25 × 20-day realized volatility, plus
modelled spreads and commissions. They are not real quotes.

## Result: all three are NO-GO, and every verifier confirmed the numbers exactly

| Strategy | Bets (in-sample / holdout) | Win rate | Mean per bet | US$1,000 became | SPY, same period |
|---|---:|---:|---:|---:|---:|
| S1 catalyst gap (5%+ gap, 3x volume, bull market) → call spread | 15 / 9 | 7% | −61% | US$578, then US$737 | US$1,456, then US$1,106 |
| S2 momentum leader, first pullback → call spread | 11 / 4 | 9% | −43% | US$761, then US$842 | US$1,456, then US$1,106 |
| S3 market-wide washout (over half the names oversold) → QQQ call spread | 6 / 1 | 0% | −61% | US$806, then US$984 | US$1,456, then US$1,106 |

"Then" is the holdout year (2025-11 to 2026-11), restarted at US$1,000.

## What we learned (the useful part)

1. **At US$1,000, cheap call spreads cannot pay +200%.**
   - A 4 to 8% budget per bet buys only $1-wide spreads.
   - The bid/ask spread and commissions eat 40 to 80% of each bet.
   - The payoff is capped at the spread width, so even a big move earns at most about +70%.
   - A small account can only be asymmetric with single long options, which have a higher chance of a 100% loss
     on each bet.
2. **The signals showed no edge at the signal level once costs and controls were included.**
   - Catalyst gaps: the stock drifted +3.4% over 15 days, against +1.4% on random days. This is probably inflated
     by survivor bias (the list of names was picked in 2026), and it did not survive option costs.
   - Washout days: no better than random days (−27.5% against −23.2% per bet at the signal level).
3. **Survivor bias is large here.**
   - Even random-day call purchases look positive on these names (+8% average), because the universe is today's
     winners.
   - Any "it works" result on this data needs a control, which is why every test above had one.
4. **Drawdown "governors" can lock a small account out for good.** Halving the bet after losses pushed the budget
   below the cheapest contract, so the accounts stopped trading.
5. **Split-adjusted prices understate past option costs.** For example, SMCI and NFLX before their splits look 10x
   cheaper than they really were. This made some old bets look affordable when they were not.

## What this does not show

- It does not show that the screenshots are fake. Those trades fit the market data, and some people do win big.
- What the tests suggest is that, with these rules and costs, the average bet loses. The wins come from a few lucky
  days that no rule here could pick out in advance.
- The live test of exactly that kind of bet runs in `docs/lottery-paper/` (accounts Y1 to Y3, from 2026-11-13).

## Files

- `s1/`, `s2/`, `s3/`: the frozen spec with the verifier's findings (`SPEC.md`), the backtest script and
  `results.md`.
- The scripts write their outputs to a scratch folder and read `state/external_bars/`. Re-running one needs those
  bars and a new output path.
