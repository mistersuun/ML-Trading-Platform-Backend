# Pre-registration: literature-defined patterns for the pooled (D18) evaluation

- **Date:** 2026-10-06
- **Status:** PRE-REGISTRATION, recorded as run in decision D19 (2026-10-06), which freezes the SHA-256 of this file (section 7.6; the hash lives in `docs/decisions.md`, not here, since a file cannot contain its own hash). The two rules are implemented in `patterns_prereg.py` and evaluated ONLY by the pooled shadow path (section 9). Order eligibility and any move into the per-symbol scan still need a separate owner decision. See section 10 for the round-1 corrections that make this text match the build.
- **Depends on:** D18 pooled validation (`docs/proposals/pooled-validation.md`), approved by the owner on 2026-10-06 as shadow only.
- **Evidence behind it:** [`strategy-evidence.md`](strategy-evidence.md).

This document is written **before any test of these rules' returns on the platform's 17-symbol universe**. The one exception is disclosed in section 1. It fixes, in advance:

- the exact rules,
- the parameters, all taken from the literature with no tuning,
- the universe, the entry, the exit and the holding period,
- the hypotheses,
- how each pattern counts as a trial.

After results are seen, any change to any element below is a **new trial** that is counted in N. It is never an edit to this one.

Invariants are unchanged:

- paper only (`paper=True` hard-coded, no live mode)
- long-only
- US equities and ETFs only executable
- every order goes through `execution.submit_intent`
- IBKR is read-only
- the per-symbol validation path, `services/forward.py` and `docs/forward-test/2026-10/` are **not touched**

---

## 0. Summary

| | |
|---|---|
| New scanner patterns pre-registered | **2**: `rsi2_pullback` (P1) and `high52_breakout` (P2) |
| Candidates considered and not registered | 6: IBS, 3 down closes, turn of the month, volume shock, monthly 10-month-SMA cross, cross-sectional momentum (section 4) |
| Allocation-layer overlays (separate proposals, **not** added to the scanner) | 3: O1 keep the trend sleeve unchanged; O2 turn-of-month rebalance timing; O3 vol cap on the trend sleeve (deferred, off by default) |
| Pooled patterns evaluated | 20 - 1 (retired `macd_hist_reversal`) + 2 = **21** |
| Pooled N for the DSR (D18 §6.1: retired patterns stay in N) | **22** |
| Required pooled annualised Sharpe (V = 1/T, normal returns, before the T_eff penalty) | **252-bar warm-up, as adopted (D18 c), gate as built (DSR p < 0.025): about 2.48 at T = 630 and 2.40 at the measured T = 672, N = 22** (2.27 and 2.20 are the p < 0.05 figures, not the gate), and higher once T_eff < T. For the 504-bar construction only: 2.91 at N = 20, 2.94 at N = 22 (T = 378) |
| Prior probability that either new pattern reaches `pooled_deflated_validated` | Low (well under 10% each). Expected outcome: shadow-only. |

## 1. Disclosure of prior contact with the data

Pre-registration is only as clean as what was seen beforehand, so everything known is listed here.

1. **Event counts (no returns)** for P1 and P2 were computed on `state/external_bars` for all 17 symbols, **before `HOLDOUT_START`** only. These are counts of trades, entry weeks and symbols. Counts are not performance, and no rule or parameter was chosen from them, except that rules unable to pass the D18 count gates *by construction* were dropped (section 4). That is a feasibility criterion, not a performance one.
2. **A return check on SPY.** A research worker backtested RSI(2) (with and without the 200-day filter), IBS, a 200-day SMA filter, Donchian 55/20 and vol targeting on **IBKR SPY daily bars from 2022-07 to 2026-10**.
   - The check used close-to-close fills, price-only data and 1 bp costs.
   - The window **overlaps both the pre-hold-out OOS region and the hold-out (2025-07-01 onward)** for one of the 17 universe symbols (SPY).
   - What this pre-registration does about it:
     - P1 uses the **published** Connors-Alvarez parameters (2, 10, 200, 5), not values chosen from that check.
     - P1 keeps the published 200-day filter, even though the unfiltered variant had the higher CAGR in that check. The choice follows the publication.
     - P2 was not part of that check.
   - Under D18 §10 the pooled hold-out read of P1 is therefore **not fully clean on SPY** (1 of 17 symbols). The owner's hold-out attestation for the pooled build must include this item. Under D18 §10.3, the **forward paper window after this registration date** is the clean confirmatory test for both patterns.
3. No other return, Sharpe or hold-out figure for P1 or P2 on any platform symbol has been computed or seen.

## 2. Fixed elements shared by both patterns

These are taken unchanged from the current executable variant and from the D18 design. They are restated here so the trial is fully specified.

| Element | Fixed value |
|---|---|
| Universe | D18 rule: all executable `WATCHLIST["stocks"]` symbols with usable data. Today these are the 17 symbols AAPL, MSFT, NVDA, TSLA, AMZN, META, GOOG, AMD, SPY, QQQ, IWM, DIA, JPM, GS, BAC, XOM, CVX, hashed into `universe_hash`. No asset-class split. |
| Bars | IBKR daily OHLCV from `state/external_bars`, the same loader the pooled build uses. Only bars before `HOLDOUT_START = 2025-07-01` go into OOS. |
| Signal convention | `patterns.py` convention: `df -> df` with a `signal` column (+1 buy, -1 exit, 0 none). Everything is causal: element t uses only bars <= t. NaN comparisons evaluate to False (no signal). |
| Execution | `config.EXECUTION_MODE = "next_open"`: a signal on bar t fills at the open of t+1. Long-only (`allow_short=False`): -1 only closes a long, and a same-side signal while long is ignored. |
| Exits | Executable variant (`validation.executable_candidates`): stop at signal close - `ATR_STOP_MULT` (2.0) x ATR(20), target at signal close + `TAKE_PROFIT_ATR_MULT` (3.0) x ATR(20), plus any -1 signal exit defined below. **No exit grid and no time stop.** |
| Costs | `COMMISSION_PCT = 0.001` and `SLIPPAGE_PCT = 0.0005` per side (about 0.3% per round trip). The cost stress is 2x. |
| Walk-forward / OOS | D18(c), as built: a 252-bar warm-up (`POOLED_WARMUP_BARS`) + 126-bar test windows, step 126 (5 folds, 630 OOS bars per symbol on 5 years of data; 672 pooled days on the union calendar). The 504-bar alternative was not used. P1 and P2 use the same construction as every other pooled pattern. With one candidate per pattern, walk-forward runs `continuous` and nothing is fitted. |
| Gates | Exactly the D18 §5 pooled gates with the thresholds fixed at build: trades >= 60 and entry-week clusters >= 30; breadth >= max(8, ceil(K/2)) = 9 symbols with >= 3 trades; OOS > 0; PSR > 0.95 with T_eff; cluster-preserving null + BH at `FDR_ALPHA / 2` = 0.025 padded to m = 22; DSR p < `DSR_P_MAX / 2` = 0.025 against the pooled N (the other half of each alpha stays with the per-symbol family); concentration / LOSO / LOCO / leave-one-fold-out; cost x2 > 0; 1-bar delay > 0; capped replay (the symbol exclusion is causal from method version 2); hold-out read once (return >= 0, trades >= 20). |
| Status on success | `pooled_deflated_validated`, which is **shadow-only** under D18. Order eligibility is a later, separate owner decision. |
| Code location | A **separate research registry** module (for example `patterns_prereg.py` exposing `PREREG_PATTERNS`), read only by the pooled evaluator. **Not** `patterns.PATTERN_REGISTRY`, so `universe_trials()` (39 x 20 = 780), the `patterns.py` module hash and the per-symbol `strategy_version` stay byte-identical. |
| Library | `ta` as pinned in the repo. RSI is `ta.momentum.rsi(close, window=2, fillna=False)` (Wilder smoothing: `ewm(alpha=1/window, adjust=False)`, `min_periods=window`). |

## 3. Pre-registered patterns

### P1: `rsi2_pullback` (Connors & Alvarez, 2008)

**Hypothesis (H1).** On US large caps and index ETFs in a long-term uptrend (close above the 200-day SMA), a 2-day oversold reading (RSI(2) < 10) is followed by a short-horizon rebound. The rebound must be large enough that, pooled across the universe at equal risk and with next-open execution and costs, the pattern:

- beats a long-only random-entry null (timing, not market drift), and
- clears the D18 deflated-Sharpe gate at N = 22.

**Source and parameters (fixed, published).**

- Connors & Alvarez, *Short Term Trading Strategies That Work* (2008): RSI period 2, entry threshold 10, trend filter SMA 200, exit on a close above SMA 5.
- Connors also quotes a threshold of 5. Only **10** is registered. The threshold-5 variant is **not** tested, and testing it later would be a new trial.

**Exact rule.**

```python
def rsi2_pullback(df):
    out = df.copy()
    c = out["Close"]
    sma200 = c.rolling(200, min_periods=200).mean()
    sma5 = c.rolling(5, min_periods=5).mean()
    rsi2 = ta.momentum.rsi(c, window=2, fillna=False)
    entry = (c > sma200) & (rsi2 < 10)
    exit_ = (c > sma5) & ~entry          # entry wins if both hold on the same bar
    out["signal"] = 0
    out.loc[entry, "signal"] = 1
    out.loc[exit_, "signal"] = -1
    return out
```

- **Entry:** the open of the bar after a +1. Buys at the close, as in Connors, are not available on this platform. This is a known handicap and is accepted, not tuned around.
- **Exit:** whichever comes first:
  - the open after the first bar whose close is above SMA(5) (-1),
  - the 2 x ATR(20) stop,
  - the 3 x ATR(20) target.

  Connors reports that stops hurt this rule. The stop is kept anyway because execution always submits it, and the platform validates what it can trade.
- **Holding period:** typically 1-5 trading days. There is no time stop.
- **Universe:** the D18 universe (17 symbols).
- **Expected events (counts only, pre-hold-out, computed without ATR exits):**

  | OOS construction | Trades | Entry weeks | Symbols with >= 3 trades |
  |---|---|---|---|
  | 504-bar train (pre-build estimate) | about 229 | 67 | 17 |
  | 252-bar warm-up | about 343 | 101 | 17 |

  Extrapolating the rate gives roughly 150 hold-out trades. That is an estimate, not a read.
- **A-priori expectation.**
  - Pooled mean R per trade > 0: plausible (about even odds).
  - Beating the long null after BH: unlikely.
  - Passing the DSR at a required Sharpe of about 2.9: very unlikely.
  - Main risks: the gated 1-bar delay stress (the bounce is often over by day 2), next-open entry, and 0.3% costs against a per-trade edge of a few tenths of a percent.

### P2: `high52_breakout` (Huddart, Lang & Yetman, 2009; George & Hwang, 2004)

**Hypothesis (H2).** When a US large cap or index ETF closes above its highest close of the prior 252 trading days, returns over the following days to weeks are positive beyond drift: anchoring on the 52-week high leads to under-reaction and continuation. Pooled at equal risk, with next-open execution and costs, the pattern beats the long-only random-entry null and clears the D18 gates at N = 22.

**Source and parameters (fixed, published).**

- The window of 252 trading days is the 52-week high used by both papers.
- The reference price is the **closing** price, as in George & Hwang's CRSP daily prices.
- The entry is the crossing event studied by Huddart, Lang & Yetman (2009).
- There are no other parameters.
- The cross-sectional ranking form of George & Hwang is **not** what is tested. This is the time-series crossing form, and the hypothesis is weaker for it.

**Exact rule.**

```python
def high52_breakout(df):
    out = df.copy()
    c = out["Close"]
    prior_high = c.rolling(252, min_periods=252).max().shift(1)   # max close of bars t-252 .. t-1
    above = c > prior_high
    entry = above & ~above.shift(1, fill_value=False)             # first close above (a crossing)
    out["signal"] = 0
    out.loc[entry, "signal"] = 1                                  # no signal exit
    return out
```

- **Entry:** the open of the bar after the crossing.
- **Exit:** only the executable 2 x ATR(20) stop or 3 x ATR(20) target. There is no -1 signal exit, because neither paper defines one, and inventing one would be a free parameter.
- **Holding period:** until the stop or the target is hit, typically days to a few weeks. There is no time stop. The HAC bandwidth follows D18 §4.3 (the pattern's maximum observed hold).
- **Universe:** the D18 universe (17 symbols).
- **Expected events (counts only, pre-hold-out):** these were computed with a High-based 52-week high and a "first in 20 days" filter. The registered close-based crossing rule fires at least as often, so the counts below are approximate lower bounds.

  | OOS construction | Trades | Entry weeks | Symbols with >= 3 trades |
  |---|---|---|---|
  | 504-bar train (pre-build estimate) | about 67 | 38 | 12 |
  | 252-bar warm-up | about 87 | 55 | 15 |

  Both clear the count gates only narrowly. If the build's actual count is below a gate, the pattern **fails that gate**. The rule is not loosened.
- **A-priori expectation.**
  - Huddart-Lang-Yetman measured about +0.18% gross in the next week for large caps. That is below our round-trip cost, so the case rests on a longer continuation captured by the 3 x ATR target.
  - Pooled mean R > 0: plausible.
  - Passing the DSR: very unlikely.
  - Additional risks: breadth (12-15 symbols), and the fact that it is beta-heavy in a rising sample. The long-only null controls for that.

## 4. Considered and not registered (reasons fixed before any test)

| Candidate | Reason it is not registered (none is performance-based) |
|---|---|
| IBS < 0.2, 1-day hold (Pagonidis 2014) | The documented edge is close-to-close. The engine enters at the next open, and a 1-day effect cannot survive the **gated 1-bar delay stress** or about 0.3% round-trip costs at a 2x stress. It may be re-proposed only if execution ever supports market-on-close orders (a separate decision). |
| 3 consecutive down closes above SMA200 (Connors family) | Its events largely overlap P1 (same hypothesis family). Registering both would add a trial without adding a hypothesis. |
| Turn of the month as a scanner signal | All symbols fire in the same week: about 21 entry weeks on the 504-bar construction, which fails the 30-cluster gate **by construction**. It moves to the allocation layer as O2. |
| Volume shock (> 3x 50-day average volume on an up day) | 19 pre-hold-out events on 2 symbols, so it cannot pass the count or breadth gates. The documented effect is cross-sectional. |
| Monthly close crossing the 10-month SMA | 32 events on 3 symbols. It is already the allocation trend sleeve. |
| Cross-sectional 12-1 momentum or 52-week-high rank | It needs a broad universe. With 13 stocks (8 mega-tech) it is a sector bet, and only about 18 monthly rebalances fall out of sample. |

Rejected outright (see `strategy-evidence.md` §2-3): overnight drift, pre-FOMC drift, PEAD, same-month seasonality, sector rotation, Donchian 55/20 on ETFs, single-stock short-term reversal, candlesticks and other generic technical patterns.

## 5. Trial count implications

| Item | Count |
|---|---|
| Current pooled family (`PATTERN_REGISTRY`) | 20 |
| Retired before the first pooled run: `macd_hist_reversal` (identical signals to `macd_crossover` on all 17 symbols; a non-performance criterion under D18 §6.1). Recorded as D18(d) in `decisions.md`. | -1 evaluated |
| New pre-registered patterns (P1, P2) | +2 |
| **Patterns evaluated per pooled run** | **21** |
| **BH family size m (the p-values are padded with 1 up to the registered count)** | **22**, at `FDR_ALPHA / 2` = 0.025, so the best candidate needs a p-value of about 0.025 / 22 = 0.00114 (the earlier text of this row said m = 21 and alpha/m = 0.0024 = 0.05/21: corrected, section 10) |
| **Pooled N for the DSR** (retired patterns stay in N under D18 §6.1; floor = `pooled_universe_trials()`) | **22** |
| Per-symbol `universe_trials()` | **unchanged at 780** (the new patterns are not in `PATTERN_REGISTRY`) |

Required pooled annualised Sharpe for a one-sided DSR p < 0.05 (the build gates at 0.025, so its bar is higher: at N = 22 about 2.48 at T = 630 and 2.40 at T = 672). This uses the D18 §13 method: per-day SR0 from the expected maximum of N null trials with V = 1/T, normal returns, before the T_eff penalty.

| N | T = 378 (504-bar train) | T = 630 (252-bar warm-up) |
|---|---|---|
| 20 (today) | 2.91 | 2.25 |
| 21 | 2.93 | 2.26 |
| **22 (this registration)** | **2.94** | **2.27** |
| 26 (if all 6 "considered" rules had also been added) | 3.00 | 2.32 |
| 40 (if exit or parameter variants were counted) | 3.15 | 2.43 |

Each extra pattern raises the bar only a little. The bigger costs are the BH family size and a weaker overall prior. That is why only 2 patterns are registered.

## 6. Allocation-layer overlays (separate proposals, NOT added to the scanner)

These change no scanner pattern and are not evaluated by D18. They do not enter the pooled N. Each one needs its own decision entry. None may be chosen by comparing variants on the 5-year ETF bars: their evidence is the long published samples, and any confirmation should use long mutual-fund or index proxy history, as the portfolio review already recommends.

### O1: Keep the trend sleeve exactly as built

- **Rule (unchanged):** `allocation.trend_signals`. For each of SPY, VEA, IEF, GLD, DBC and VNQ, the score is the share of 8/10/12-month SMA votes with the month-end close above the SMA. The sleeve is 10%, the rest goes to cash, and it rebalances monthly.
- **Evidence:** the best-evidenced family (grade A for TSMOM, B for the long-only ETF form). Its value is drawdown control at a return cost; in the repo's 5-year check it lowered excess Sharpe from 0.68 to 0.64.
- **Proposal:** freeze the lookbacks, universe and sleeve size. Do not re-tune them on 5-year data. Any change is a new decision with a stated reason not based on recent performance.

### O2: Turn-of-month timing for allocation rebalances

- **Rule:** monthly core and trend rebalance proposals are generated after the last completed month (as now) and **executed on trading day +1 of the new month, and no later than trading day +3**.
- **Parameters:** a window of day -1 to day +3 (McConnell & Xu 2008). No other parameters.
- **Evidence:** grade C+. The effect is small, and post-1990 evidence conflicts. This is a free scheduling convention, not a return source.
- **Note:** the 21:47 UTC forward job and its schedule are **not** changed by this. O2 applies to the human-approved monthly rebalance workflow only.

### O3: Volatility cap on the trend sleeve (deferred, off by default)

- **Rule:** for each trend asset i, weight_i = (TREND_SLEEVE_PCT / 6) x min(1, 0.10 / sigma_i). Here sigma_i is the annualised realised volatility of daily returns over 63 trading days at the signal month-end. The remainder goes to cash. No leverage (cap 1x).
- **Parameters:** 10% target volatility and a 63-day window. Both are taken from the TSMOM literature's per-asset scaling convention, scaled down to long-only, and neither is tuned.
- **Evidence:** mixed. Kim, Tse & Wald find that most TSMOM alpha comes from vol scaling. Cederburg et al. (2020) find no systematic out-of-sample gain from vol management.
- **Recommendation:** **do not adopt now.** The rule is recorded here only so that, if it is ever proposed, the parameters are the ones fixed today.

Explicitly **not** proposed:

- A market-regime filter on scanner entries, for example "no new long entries when SPY is below its 10-month SMA". That would change the object D18 validates, and would have to be a new pooled trial per pattern.
- GEM, HAA, VAA or BAA as a replacement trend sleeve.
- A low-volatility tilt in the core.

## 7. Locking rules

1. **No parameter, rule, exit or universe change after any result is seen** without registering a **new trial**. That means a new version, N += 1 per pattern affected, and the D18 §10 read cap (one pooled hold-out read per pattern per `HOLDOUT_START`; a re-read only with a disclosed, Bonferroni-counted decision entry).
2. **Bug fixes.** A code defect that makes the implementation differ from the rule in section 3 may be fixed without a new trial **only if** the fix restores the rule exactly as written here. The fix and the before/after diff are recorded in the decision entry. Any other change is a new trial.
3. **Report everything.** Both patterns' full pooled funnels are reported, whether they pass or fail, including the gate where each one stops. A failed pattern is not quietly removed, and it stays in N.
4. **No selection between P1 and P2 on results.** Both are evaluated under the same gates and BH family. Neither may be dropped, or have its universe narrowed, after its pooled OOS or hold-out has been seen (D18 §10, "candidate set frozen").
5. **The confirmatory window** is forward paper data after 2026-10-06. Its minimum length or trade count and its stated power are set in the D18 phase-2 decision entry before it is read, as D18 §12.3 requires. Until that minimum is reached the result is "not yet informative", never "passed".
6. **Integrity.** On approval, the SHA-256 of this file is recorded in the decision entry, and the implemented functions are tested against the code blocks above with a fixture test.

## 8. Timing and scope

- **Not this week.** Nothing is added to `patterns.py`, `PATTERN_REGISTRY`, `validation.py`, `services/forward.py` or `docs/forward-test/2026-10/` while the forward test runs nightly at 21:47 UTC. The per-symbol gates, trial counts, results and journal stay exactly as they are.
- **Build order after the forward week:**
  1. Owner approval of D18.
  2. The `macd_hist_reversal` retirement decision.
  3. The OOS-construction decision (504-bar train or 252-bar warm-up).
  4. This registration, approved in its own decision entry.
  5. Implementation of `PREREG_PATTERNS` in a separate module.
  6. The first pooled run, shadow-only.
- **The owner decides:** whether to approve this registration as written, or to drop P2 (lowering N to 21) before any test. Dropping P2 after a test is not allowed.
- **What actually happened (disclosed in round 1).** The order above was not followed: the first pooled run (`pooled-shadow-first-run.md`) evaluated BOTH patterns before the registration was approved, so the option to drop P2 before any test has **lapsed**. D19 records the registration as run: 21 patterns evaluated, N floor 22, BH m = 22. D18 was approved first (2026-10-06), then the retirement D18(d) and the 252-bar construction D18(c) were decided, then the patterns were implemented and run, and this registration is frozen after that.

## 9. Implementation record (2026-10-06)

- `patterns_prereg.py` holds `rsi2_pullback` and `high52_breakout` (the section 3 code blocks, verbatim) in `PREREG_PATTERNS`. `pooled_validation.pooled_registry()` = `PATTERN_REGISTRY` + `PREREG_PATTERNS`; only the pooled path reads it. `pooled_pattern_names()` returns 21 and `pooled_universe_trials()` returns 22 (retired `macd_hist_reversal` stays in N). `patterns.PATTERN_REGISTRY`, `universe_trials()` (780), the per-symbol scan and the forward test are unchanged.
- `tests/test_patterns_prereg.py` tests each rule on synthetic bars (exact signal dates, no look-ahead by prefix truncation, next-open entry through the walk-forward engine) and that the code is AST-identical to the blocks in this document.
- A first pooled shadow run on the pre-hold-out bars is reported in `pooled-shadow-first-run.md`. It is not confirmation, and it was seen after registration: changing either rule now is a new trial.

### How and when they could join the legacy per-symbol scan (owner decision, NOT before the forward week ends)

1. Wait until the forward test week is over (docs/forward-test/2026-10 frozen) and D18 shadow has accrued its 4 forward weeks, so the per-symbol gates, trial counts and journal of the current test are never disturbed.
2. Only if the owner wants per-symbol coverage: add the two functions to `patterns.PATTERN_REGISTRY` in one reviewed commit with its own decision entry. This changes `universe_trials()` from 39 x 20 = 780 to 39 x 22 = 858, the `patterns.py` hash and every per-symbol `strategy_version`, which is a new per-symbol trial set (a new forward-test baseline). `tests/test_golden_patterns.py` must get golden entries, and `PARAM_GRIDS` stays empty for them (fixed parameters).
3. Per-symbol evaluation is expected to fail the count gates (P2 has about 6 to 21 OOS trades per symbol), so the pooled path remains the intended evaluation unit.

## 10. Round-1 corrections to this document (2026-10-06), a disclosed bug-fix-to-doc under section 7.2

No rule, parameter, exit or universe changed. Where this document differed from the code, the code was the more conservative one, and the DOCUMENT was corrected to match what was built and run:

| Where | Said | Built and run |
|---|---|---|
| Status line | D18 proposed, not approved | D18 approved by the owner 2026-10-06, shadow only |
| Section 2, OOS construction | 504 train + 3 x 126, or 252 warm-up | 252-bar warm-up (D18 c) |
| Section 2, gates; section 5 | BH m = 21, alpha/m = 0.0024; DSR p < 0.05 | BH padded to m = 22 at `FDR_ALPHA / 2` = 0.025 (best candidate 0.00114); DSR p < `DSR_P_MAX / 2` = 0.025 |
| Section 5 | retirement not recorded | D18(d) |
| Section 8 | owner may drop P2 before any test | lapsed: P2 was evaluated in the first run |
| Capped replay (section 2 gates) | exclusion not specified | method version 2: a symbol is excluded at an entry date only from trades that had already exited (at least 3, one-sided t below -2); the full-sample exclusion is for forward eligibility only. This is a new pooled trial version (N grows by the patterns evaluated) |

The pooled N floor stays 22 (20 registered + 2 here). Round 2 correction: a method-version bump does NOT add trials today. The live ledger never held v1 rows, the first real run writes 22 rows, all method version 2 (verified on an isolated forward copy, n_trials = 22), and v1 and v2 give identical pooled return series (only the capped-replay gate differs). N = 22 is the defensible count; the v1 first run is a disclosed look, not a separate strategy. A later method version that CHANGES the return series (for example right-aligned folds) is a new trial version and N grows by the patterns evaluated.

**Round-2 process disclosure.** This document was hashed and frozen after the first pooled run had been seen; sections 2, 5 and 8 were edited after those results (the round-1 table above), and the 504-bar versus 252-bar construction choice is recorded as made before that run but carries no committed timestamp. Only the section 3 rule code is provably unchanged (`test_code_matches_the_preregistration_document`). The first-run report is therefore exploratory; the confirmatory evidence is the forward shadow period only. Round-2 edits (the required-Sharpe row, the event-count labels, the section 10 N note, this paragraph) change no rule; the hash in D19 was updated. This document and the D19 hash should be committed in their own commit before the next pooled result is stored. D19 was confirmed by the owner on 2026-10-10 with the explicit line "confirm D19" (recorded in D19; status line only, no rule changed).
