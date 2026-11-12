# Strategy review, 2026-11-12 (data to the 2026-11-11 close)

A multi-agent review: six reviewers (one per strategy family), six adversarial verifiers who recomputed the numbers
and tried to refute every claim, three brainstorm lenses (statistician, risk manager, engineer) and one judge.
All agents were read-only and never touched IBKR or private account files. The overseer spot-checked the key code
findings before this summary.

## Are there clear winners? No.

Every family came back **no evidence yet**, and every verifier agreed.

| Family | Headline | Why it is not a winner |
|---|---|---|
| Day trading, opening-range breakout | SPY +13.4%, QQQ +4.6% | Mean R t-stats 1.26 (SPY) and 0.46 (QQQ). Three trend days (10-16, 11-05, 11-09) carry everything; on the other 17 sessions both books lose money. QQQ and SPY are one bet (correlation 0.89), so there are about 20 independent days, not 40. The 12-session warm-up was negative. The 10R target never fired: the rule is in practice "stop or hold to the close". |
| Gap-and-go | +4.8% | 14 trades, t = 1.20; without the best 2 trades the mean is +0.04R. The pre-registered bar is 60 trades. |
| Quant accounts | Low volatility +7.7% vs SPY -0.8% | Daily alpha t about 1.2; about all of the excess comes from two days, one of them SPY -4.4% while the account was flat. Best of five accounts, so luck of this size happens 10 to 20% of the time. The other four are within 1.3 points of SPY. |
| Options | P +0.9%, L -39.7%, M -40.3% | 5 closed trades in total against a pre-registered 30 per book. P is short-volatility carry in a falling market. |
| Aggressive | 3x Nasdaq -28%, all-in AMD +1% | One round trip each. The TQQQ loss is about a 0.5th-percentile path in its own backtest (whipsaw at the 200-day line). |
| ML signals and xs-ranker | Flat, 0 of 340 pass | Partly structural (see below). The best pooled pattern fails the deflated-Sharpe gate (p = 0.63). |
| Investor copy portfolios | Fairfax +25%, TCI +10.6% | Best t 2.2 against a multiple-testing bar of about 3.3 for 14 baskets; low-beta baskets look good by construction in a down tape; Fairfax is in CAD against a USD benchmark. |

## Verified bugs and the action taken

| Bug | Effect on recorded results | Action |
|---|---|---|
| `external_bars.symbol_files` sorted all tags by name, so old named seed files (`.gapgo`, `.investor`) overrode newer dated top-ups on the same date | None today: no seed files exist since the 2026-11-03 reset | **Fixed** (seeds first, dated top-ups in date order), with a regression test |
| `pilot_report.load_daily`: the base `.D.json` sorted after dated top-ups and won on duplicate dates | None today: only base files exist | **Fixed**, with a test |
| Options: a quote with no bid crashed the step (`float(None)`) | None (no such quote yet) | **Fixed**: held at the last mark, with a test |
| Options: the nightly plan is computed before exits, so a book that exits tonight lacks entry quotes | Happened on 11-04 and 11-09; 11-09 was redone by hand | Open: needs a plan change (procedural, rules unchanged) |
| Options: quotes without a delta are skipped or ranked last | Minor | Open |
| Day trading: R is P&L divided by 1% of equity, not by the risk actually taken when the 4x leverage cap binds (11 of 20 QQQ and 18 of 20 SPY trades) | Labelling only; P&L is right | Open: add an own-risk R column |
| Gap-and-go: after the half exit the breakeven stop is not re-tested on the same bar | Mildly optimistic | Open: rule amendment needed (frozen rule) |
| Quant and aggressive journals are re-simulated each run and miss 11-04/11-05 rows; they read `load_bars`, not `closed_bars` | Drawdown from the journal is understated; replay curves are right | Open |
| Quant: a missing close is marked at 0 | Latent (no such close now) | Open |
| ML: the per-symbol trade gate (30 out-of-sample trades) cannot be reached (the maximum is about 21) | Explains part of the 0-of-340 result | Open. **Not** to be loosened; see "do not do" |
| ML: pooled shadow signals are never forward-scored | The pooled path is not being tested forward | Open: proposal 8 |
| Investor tracker: baskets that started on 10-12 are compared with SPY from 10-06; Fairfax is not converted from CAD | Presentation | Open |
| Pilot: the stress alert fires even at the target allocation (33% vs the 30% line) | The alert never clears | Open: threshold vs target to reconcile |

## Ranked next steps (judge, top 12)

1. Pre-registered master scoreboard: every arm with its own benchmark, day-clustered confidence intervals, results
   without the top 2 days, beta-adjusted alpha, and a family-wise (Holm) rule, with dated looks.
2. Graduation ladder and a do-not-copy list: what a strategy must show (60+ independent trades, positive at 3x
   slippage, at most 40% of P&L from 2 days, one bad regime survived) before even tiny real money.
3. Append-only, hash-stamped journals; backfill 11-04/11-05; `closed_bars` in the forward runners; a replay check.
4. Power and futility table per arm, with at most 3 pre-set looks.
5. Random-baseline and placebo controls (random ORB direction, random baskets), low volatility first.
6. Bar-file precedence fixes and ingest/intraday validation gates. **Done in part (this commit).**
7. Replay of kill-switch and sizing rules on the existing curves, as a diagnostic only.
8. Forward-score the pooled shadow (long-only, 15 bp costs, de-duplicated, buy-and-hold-relative).
9. Tests for silent-distortion paths and an own-risk R column.
10. Options procedural fixes (exit then re-enter, missing-delta quotes) with a dated PREREG amendment.
11. Investor tracker reporting fixes (own-inception SPY, Fairfax in USD, style benchmarks).
12. A longer, point-in-time, stress-inclusive re-test of the surviving rules, run once.

## Do not do

- Put real money into low volatility, ORB SPY, Fairfax, TCI or any other "leader" on this window.
- Tune anything on these 4 weeks: ORB stops or target, low-vol lookback, options budgets or deltas, a TQQQ
  hysteresis band.
- Count the two ORB books as 40 trades, or quote the 10R target as tested.
- Loosen the 30-trade gate just so something passes; use the pooled path with a pre-registered rule instead.
- Retire Books L and M or crown Book P on 5 closed trades.
- Extend tests until something passes (optional stopping); fix the looks in advance.
- Change the data layer mid-study without a side-by-side replay diff.
