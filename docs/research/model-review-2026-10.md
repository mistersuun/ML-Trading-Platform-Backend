# Model review, October 2026: patterns, ML, pairs, what to copy, what to read

*Lead-quant synthesis, 2026-10-10. Not investment advice. Branch `claude/sharp-knuth-aa241v`. Five worker reviews feed this note (technical-pattern review, ML review, pairs review, copy-research note, techniques digest); their scripts and raw outputs are under the session scratchpad `review/` folder (`analyze_patterns.py`, `exit_variants.py`, `recompute_cv.py`, `pooled_experiments.py`, `pairs_analysis.py`, `universe_sweep.py`, `kalman_grid.py`, `power_kalman.py`, `dsr_bar.py`, and the CSV/JSON/MD files they wrote). Nothing in the repo was changed except this file.*

**Hold-out discipline statement.** Every empirical number in this note and in the five reviews was computed on `state/external_bars/<SYM>.json` frames cut to dates **strictly before `HOLDOUT_START = 2025-07-01`** immediately after loading (last bar used 2025-06-30). The hold-out store (`state/holdout_reads.sqlite`) and `state/forward/*` were not opened, `state/holdings*.csv` and `state/ibkr_snapshot*.json` were not read, `docs/forward-test/` was not touched, no trial registry or ledger was written (`trials=None` / pure helpers only), no model was stored, and no `scan`, `forward step` or `run-nightly` command was run. The only script I ran myself (`dsr_bar.py`) reads no bars at all; it evaluates the repo's own `stats.selection.expected_max_sharpe` / `psr` formulas. Everything below is a disclosed research LOOK at pre-hold-out data; none of it is confirmatory, and any rule change it motivates is a new trial under D18 §6.1.

---

## 1. Summary (10 lines)

1. **Nothing beats chance after the gates, and the gates are right.** On 672 OOS days (2022-10 to 2025-06), the best of 22 pooled patterns has annualised Sharpe 0.97 (`triple_ema`); the pooled DSR gate needs about 2.40 at N = 22, T = 672. Zero patterns pass PSR; zero pass BH on the cluster-preserving null.
2. **Before the gates, one family shows something:** the short-term mean-reversion rules (`rsi_divergence`, `bollinger_bounce`, `stochastic_cross`, `rsi_reversal`, `mean_rev_composite`, `rsi2_pullback`) beat a hold-matched random-entry baseline by +0.05 to +0.27 R per trade with 52-59% win rates versus 45-47% for random. Under the cluster null their p-values are 0.09 to 0.49: suggestive, not significant.
3. **Every trend and breakout rule's positive return is explained by drift:** random entries with the same hold in the same windows earn as much or more (`donchian` +0.172 vs random +0.121; `high52_breakout` +0.152 vs +0.145). Their Sharpe 0.5-0.85 is the market, not the rule.
4. **The ML layer adds nothing and says so itself:** 0 signals on 3,936 walk-forward OOS bars; CV log-loss beats the constant prior on 2 of 17 symbols by under 0.01 nats; OOS AUC above the no-skill bound on 0 of 17. The nightly "acc 0.54 / AUC 0.53" lines are the majority-class rate and null AUC.
5. **The pairs module has nothing to trade:** 0 of 6 configured pairs cointegrated (EG max-p 0.11-0.88), 0 tradable blocks and 0 trades in 10 walk-forward blocks per pair, 0 of 231 pairs valid in a full sweep; when forced to trade, 4 of 6 lose. It can never place an order under D3.
6. **The binding constraint is calendar length T, not strictness.** The required Sharpe scales like 1/sqrt(T): 2.40 at T = 672, 1.24 at 2,520 days (10 years), 0.87 at 5,040 (20 years). Adding patterns barely moves it (N 22 to 44: 2.40 to 2.57).
7. **The 22 patterns are really about 4 bets** (effective independent patterns 3.7 of 22 by participation ratio; `macd_hist_reversal` is an exact duplicate of `macd_crossover`). More generic-TA rules would add trials without adding hypotheses.
8. **Defects worth a fix PR:** ML thresholds chosen on the same tail the sigmoid was fitted on; the nightly ML "OOS" block sits on the hold-out; the ML label is a return the strategy does not trade; `keltner_reversion` ignores its `atr_mult` and builds a range channel; the per-symbol path gates cost but not delay while the pooled path gates both.
9. **The per-symbol order path is a dead funnel on 5-year bars:** no (symbol, pattern) reaches `MIN_TRADES_OOS = 30` (maximum 22). The forward paper test is exercising a path that cannot emit an order on this history; safe, but uninformative about discrimination.
10. **Best uses of effort, in order:** run the unchanged pooled shadow on 15-20 years of bars; retire-by-rule the duplicates and misnamed rules in one versioned commit; stop the nightly ML and pairs scans; fix the ML leak and labels only if ML is kept, and then only as a meta-labeller on pattern events; put real improvement work into the allocation layer, where the evidence is.

---

## 2. Where the models stand today

### 2.1 Technical patterns (pooled path, D18 geometry: 252-bar warm-up, 126-bar folds, 5 folds, 17 symbols, 22 patterns)

Counts, mean R and Sharpe reconcile exactly with `pooled-shadow-first-run.md`. R = net P&L divided by the 2 x ATR stop distance; "random" = each real trade re-drawn 2,000 times at a uniformly random entry inside its own fold window with the same realised hold, bracket and costs; "excess R" = real minus median random.

| Family | Pattern | Trades | Win% | Mean R | Sharpe | Random mean R | Excess R | p, indep. draws | p, cluster null (platform) |
|---|---|---|---|---|---|---|---|---|---|
| MR | rsi_divergence | 183 | 52 | +0.227 | 0.69 | -0.040 | **+0.267** | 0.000 | 0.15 |
| MR | rsi_reversal | 90 | 48 | +0.120 | 0.07 | -0.085 | +0.206 | 0.026 | 0.49 |
| MR | bollinger_bounce | 242 | 52 | +0.160 | 0.56 | -0.034 | +0.194 | 0.001 | 0.09 |
| MR | mean_rev_composite | 145 | 49 | +0.099 | 0.15 | -0.056 | +0.155 | 0.022 | 0.46 |
| MR | stochastic_cross | 358 | 53 | +0.116 | 0.47 | -0.019 | +0.135 | 0.001 | 0.11 |
| MR (prereg) | rsi2_pullback | 387 | 59 | +0.021 | 0.20 | -0.033 | +0.054 | 0.043 | 0.12 |
| trend | triple_ema | 234 | 51 | +0.220 | **0.97** | +0.090 | +0.130 | 0.017 | 0.21 |
| breakout | atr_breakout | 172 | 50 | +0.215 | 0.85 | +0.122 | +0.093 | 0.103 | 0.39 |
| breakout | donchian_breakout | 367 | 49 | +0.172 | 0.78 | +0.121 | +0.051 | 0.153 | 0.79 |
| breakout (prereg) | high52_breakout | 179 | 50 | +0.152 | 0.55 | +0.145 | +0.007 | 0.467 | 0.72 |
| MR | keltner_reversion | 365 | 49 | -0.007 | -0.04 | -0.006 | -0.001 | 0.506 | 0.44 |
| trend | adx_trend | 69 | 30 | -0.163 | -0.58 | +0.044 | -0.207 | 0.974 | 0.96 |

(Full 22-row table, exit variants, regime split and concurrency: scratch `patterns.md`, `table_main.md`, `table_exits.md`, `table_regime.md`.)

**Does anything beat chance pre-gate?** Only the MR family, and only against the anti-conservative independent-draw null. The independent p-values (0.000-0.04) versus the cluster-preserving p-values (0.09-0.49) show how much of the apparent significance is 8 mega-caps entering in the same week. The honest statement is: the MR rules have the right sign under every exit variant tested (time-only, stop-only, bracket plus time stop), while the trend/breakout rules never beat their random match under any exit. That is a consistent qualitative signal, below significance, in one mostly rising regime.

Other facts that shape the programme:

- **Exits do not separate the families; entries do.** No exit variant turns a trend/breakout rule into one that beats random. Stop/target tuning moves level and baseline together; excess R is insensitive. Do not spend trials on exit multipliers.
- **A SPY-200-SMA regime filter would remove the MR edge** (bollinger_bounce +0.29 R below the SMA vs +0.10 above; stochastic +0.28 vs +0.06; rsi_divergence +0.38 vs +0.16). The pre-registration's refusal to add one is right.
- **Concurrency:** broad patterns run 4-7 open positions a day and peak at 15-17 against `MAX_OPEN_POSITIONS = 8` and 2 per cluster; the capped replay will drop a large share of exactly the correlated trades.
- **Per-symbol path:** maximum OOS trades for any (symbol, pattern) is 22 (`rsi2_pullback` on one name), typical 3-15, versus `MIN_TRADES_OOS = 30`. Nothing can graduate there on 5-year bars.
- **Redundancy:** pooled daily-series correlation averages 0.45 off-diagonal (ichimoku/triple_ema 0.88, bollinger_bounce/rsi_divergence 0.86); effective independent patterns 3.7. PBO 0.87 (advisory) says in-sample-best does not stay best.
- **Causality check:** 75 random prefix truncations x 22 patterns x 3 symbols, 0 mismatches. Next-open entry and signal-bar ATR anchor match what `execution` submits.

### 2.2 ML (`ml/`, `ml_patterns.py`, `features.py`; 65 features, 1-bar close-to-close label, logreg / LightGBM / RF+LGBM vote, purged CV, walk-forward with re-selection)

| Measure (17 symbols, pre-hold-out) | Result |
|---|---|
| Daily lag-1 autocorrelation of returns | -0.044 to +0.094; linear predictability of tomorrow's sign about 1% of variance at best |
| Round-trip cost / one-day std | 7-30% (SPY 26%, DIA 30%): a 1-day model needs a large conditional edge on the days it trades |
| Purged-CV log-loss vs constant prior | beats prior on 2/17 (SPY 0.681 vs 0.688, QQQ 0.687 vs 0.688), both inside one fold std |
| CV accuracy minus majority class | mean -0.006 (the "0.54 accuracy" is the base rate) |
| Walk-forward OOS (234 bars/symbol) AUC above the 95% no-skill bound | 0/17 |
| Walk-forward OOS signals emitted | **0 of 3,936 bars** (`no_skill` on 15 symbols, `band` on the rest) |
| Pooled model (17 names stacked, date-blocked purged CV) vs label-shift null | h=1: 0.496-0.509 vs null 0.503-0.506; h=5: 0.500-0.522 vs 0.497-0.512; h=10 LGBM 0.536 vs null 0.520 on 3 folds (range 0.49-0.61) |
| Log-loss vs prior, every real model | worse than the prior (pooled by 0.004-0.046 nats, per-symbol by 0.08-0.44: over-confident on ~500 rows x 65 collinear features) |
| What the model keys on | volatility-regime features (`vol_ratio_63d` rank 1.0 in every fold, skew, kurtosis, bb_width, ADX): "calm up, turbulent down" in a rising sample, not a direction signal |
| Meta-labelling sketch (secondary on a trend rule's 5,920 events) | AUC 0.47-0.49, tercile spread flips sign across folds |

Verdict: the plumbing (purging, embargo, baseline-first selection, prior gate, drift guard, canaries) is better than most production ML-for-trading code, and it is what keeps the model out of the market. The task is the problem: next-day direction on single names from single-name technical transforms. The h=10 pooled LGBM result (+0.016 AUC over its null on 3 folds) is the only glimmer and is consistent with the Gu-Kelly-Xiu scale of ML edges (monthly R^2 well under 1% with 30k stocks); it is not exploitable per-name.

### 2.3 Pairs (`pairs_trading.py`; EG both orderings, BH over the scan, rolling-OLS z, walk-forward 252/63 blocks, frozen-z stop)

| Measure (pre-hold-out, 2021-10 to 2025-06, 934 bars) | Result |
|---|---|
| Configured equity pairs cointegrated (EG max-p, BH) | 0 of 6 (raw 0.11-0.88; adjusted 0.68-0.88). Johansen rejects only XOM/CVX, whose half-life is 231 bars (out of band) |
| Rolling 252-bar formation windows that pass the tradable rule | 0.0-2.2% per pair (below the 5% a pure null would give); SPY/QQQ and AMD/NVDA never |
| `walk_forward_pairs`, default config | 10 blocks per pair, **0 tradable, 0 trades, OOS P&L 0.0000** for all six |
| Forced to trade (gate off) | 4 of 6 negative; SPY/QQQ Sharpe -2.2; 72% of exits are `stop_z` |
| 231-pair sweep over all 22 symbols with >= 600 bars | 9 raw p < 0.05 (3.9%), 0 after BH, 0 valid; 64 of 2,268 blocks tradable (2.8%); 145 OOS trades, mean -0.68%, 30% win rate |
| Causal Kalman hedge ratio, 12-point (delta, Ve) grid | negative mean return at every setting that trades |
| Power of the EG gate at 252 bars (simulated OU) | 0.91 at half-life 5, 0.20 at 20, 0.08 at 40: a genuine pair with this universe's 18-50 bar half-lives is rejected 4 times in 5 |

Verdict: correct code applied to a universe with no cointegration. The gate keeps a losing rule set out of the market, which is its best possible outcome. Under D3 a valid pair could not be executed anyway.

### 2.4 The trade-off the owner has to hold in mind: more patterns and models raise the DSR bar, but only a little; history lowers it a lot

Computed with the repo's `expected_max_sharpe` and `psr` (V = 1/T, normal returns, no T_eff penalty; scratch `dsr_bar.txt`). Required annualised pooled Sharpe for the gate as built (one-sided DSR p < 0.025):

| N trials \ T (OOS days) | 672 (today) | 1,260 (5 y) | 2,520 (10 y) | 5,040 (20 y) |
|---|---|---|---|---|
| 1 (no selection) | 1.20 | 0.88 | 0.62 | 0.44 |
| **22 (today)** | **2.40** | 1.75 | 1.24 | 0.87 |
| 23 (one consolidated rule added) | 2.41 | 1.76 | 1.24 | 0.88 |
| 30 | 2.48 | 1.81 | 1.28 | 0.90 |
| 44 (one exit or universe version bump: N += 22) | 2.57 | 1.88 | 1.33 | 0.94 |
| 66 (two bumps) | 2.67 | 1.94 | 1.37 | 0.97 |
| 100 | 2.76 | 2.01 | 1.42 | 1.00 |

Reading: each version bump (a time stop, a new universe, a method change that alters the return series) costs N += 22 and raises the bar by about 0.1-0.2 Sharpe; each doubling of history lowers it by about 30%. The best observed Sharpe is 0.97. On today's T no realistic daily rule on 17 correlated names clears 2.4; on 20 years, the bar (0.87-0.97) is in the range the MR family showed here, before the expected decay from including pre-2015 data. So: spend trials sparingly, but do not refuse a bump that is needed for the result to mean something (a time stop raises T_eff and makes the capped replay viable); and get the history first, because every bump is cheaper once T is long. Two bumps paid at once (one commit, one version) cost the same N as one.

---

## 3. Bugs and defects, severity-ordered (fix-PR candidates)

"Verified" = I read the source this session; "reported" = from the worker review, not re-read by me. None of these should be fixed during the forward week (pre-registration §7: nothing in `patterns.py`, `validation.py`, `services/forward.py` moves while the nightly forward test runs).

| # | Severity | Where | Defect | Status |
|---|---|---|---|---|
| 1 | major | `ml/select.py::fit_pipeline` -> `ml/calibrate.py::choose_thresholds` | Thresholds are searched (29 cuts x 2 sides, t >= 2) on the SAME time-ordered tail (`val` slice) the Platt sigmoid was fitted on, 120-180 rows. In-sample calibration plus 58 correlated looks; the t >= 2 bar is not a 5% test. D15 calls it "conservative, not leaky"; it is leaky. Fix: sigmoid on tail A, thresholds on a later tail B or on the purged-CV OOS predictions (`cv_predictions` already exist); add a null test (label-shifted data must abstain in >= 95% of seeds). | verified |
| 2 | major | `services/ml.py::predict` (`include_holdout=True` default), `services/scan.py::scan_ml` on the 8-year research frame | `oos_start` = 75% of the frame = 2025-07-09 on 1,252 bars, so the nightly ML "OOS" block IS the 2025-07-01 hold-out, evaluated, displayed and alerted on every night. D18(a) already treats the hold-out as "possibly seen" for this reason. Fix: `include_holdout=False` default (D18(a) open item), and run `scan_ml` on pre-hold-out bars only or not nightly. | verified |
| 3 | major | `ml/labels.py` (`ML_HORIZON_BARS = 1`, dead zone 0) vs `engine.py` (`EXECUTION_MODE = "next_open"`, ATR bracket) | The label and the EV-after-cost threshold are Close[t] -> Close[t+1]; the trade is Open[t+1] -> bracket/signal exit. The overnight gap is inside the label and outside the trade; stop/target exits are outside the label. The gate decides on a return the strategy cannot earn. Fix: label = the engine's own bracket outcome (triple barrier with the real stop/target/max hold), `PurgedTimeSeriesSplit.horizon` = max hold. | verified (config), effect reported |
| 4 | major | `pairs_trading.py::scan_all_pairs` / `is_valid_pair`, `PAIRS_OOS_REQUIRE_TRADABLE = False` | Pair validity rests on a full-sample Engle-Granger on whatever history the caller requested (nightly: ~280 bars), not on the OOS blocks the walk-forward trades. The gate the nightly reports is not the gate the OOS engine applies (D15 admits "not yet fully out of sample"). Fix: `PAIRS_OOS_REQUIRE_TRADABLE = True`, validity from the latest block's formation statistics, change the 500-bar test fixture's half-life to 5-8 bars. Moot if the module is retired from the nightly (programme item 3). | verified |
| 5 | moderate | `patterns.py::keltner_mean_reversion` | `atr_mult` is accepted and never passed; `ta.KeltnerChannel` is built with its default `original_version=True`, i.e. SMA(typical price) +/- 2 x SMA(High - Low), Chester Keltner's range channel, not an ATR channel; `window_atr` is ignored in that mode. Signals are causal; the band is simply not what the name and docstring promise. Fix: pass `multiplier=atr_mult, original_version=False` (new version, hold-out re-read) or rename it `keltner_range_reversion`. | verified |
| 6 | moderate | `patterns.py::macd_histogram_reversal` | Identical buy/sell series to `macd_crossover` on all 17 symbols (hist = MACD - signal, so a sign change is the cross; both use 12/26/9). Retired from the pooled set (D18 d) but still in the per-symbol family: 39 of 780 trials are exact duplicates and count in that N. Fix: retire from `PATTERN_REGISTRY` by the same non-performance rule. | verified |
| 7 | moderate | `validation.py::_decide` vs `pooled_validation.py::_cost_delay_ok` | Per-symbol path gates `cost_ok` and never reads `delay_ok` (computed at line 687, reported, not gated; D11 says "recorded, not gated"); the pooled path gates the 1-bar delay. For `rsi2_pullback` the delay is the stress that kills it (-0.026). Design inconsistency rather than a coding slip: either gate it per symbol or say in the docstring and funnel that it is advisory. | verified |
| 8 | moderate | `patterns.py` naming/definition | `rsi_divergence` is not a divergence: it is "close within 1% of the prior 20-bar low AND RSI(14) < 40 AND RSI above its prior 20-bar minimum", a 20-day-low pullback filter. It is the best rule in the set and should be named for what it is. `ichimoku_cloud` uses `ta.IchimokuIndicator` without the 26-bar forward displacement and no Chikou check (causal, but not the textbook rule). `confluence` has breadth 3/17 and 25 trades: cannot pass count gates by construction. `bollinger_squeeze` has a hidden 120-bar quantile window not in `PARAM_GRIDS`. | verified (ichimoku call), rest reported |
| 9 | moderate | process: strategy version = hash of the whole `patterns.py` module (D12 addendum) | Any edit to `patterns.py` changes every pattern's version and grants every pattern a fresh hold-out read with `HOLDOUT_START` unmoved (D15 records one accepted re-read already). Fix: per-pattern source hash (function source + its exits/costs), so unrelated edits do not re-read everything. | verified (policy text), code path reported |
| 10 | minor | `ml/select.py::build` (logreg C = 0.1), `features.py` | The "baseline" logreg is 0.03-0.07 nats WORSE than the prior on 15/17 symbols (DIA 0.754, GS 0.752 vs 0.69), so challengers are compared to an overfitted incumbent and `no_skill` is decided on it; C = 1e-3 pooled cuts excess log-loss to 0.004. Exact duplicate features (`ret_5d`==`roc_5`, `ret_10d`==`roc_10`, `ret_21d`==`roc_21`) and 16 more pairs with rho > 0.95. `_importance` is permutation importance on the calibration tail (in-sample for the sigmoid) and feeds `top_features` in alerts. | reported |
| 11 | minor | `ml/splits.py`, `ml_patterns.py::_select` | With 500-730 usable rows only 2-4 of 5 folds exist; "one fold std" is the std of 2-3 numbers; `chosen` flips logreg/lgbm between adjacent retrain blocks (NVDA, QQQ). | reported |
| 12 | minor | `pairs_trading.py::_run_block`, `walk_forward_pairs`, `config.PAIRS` | z lookback `clamp(2 x hl, 20, 120)` and `max_hold = 3 x hl` re-derived from a half-life with +/-25% error on clean OU, far worse on real data; a `time_stop` blocks the rest of the block while `coint_break` only blocks while unhealthy; the final partial block (up to 62 bars) is never traded so the alert's "latest block" can be a quarter stale while `current_zscore` is today's 60-bar OLS; docstrings say "10 pairs" while the nightly BH family is 6 (crypto/FX legs fail `InsufficientData`, futures are `RESEARCH_ONLY_PAIRS`). | reported |
| 13 | minor | `features.py` | `pct_change()` with pandas 2.3 default pad-fill makes a missing close a zero return and a stale indicator; no cross-sectional, calendar, or gap/intraday-split features (the only families with a documented daily-horizon prior). | reported |
| 14 | housekeeping | `state/trials.sqlite`, `data/trials/` | Stray v1 pooled registry run `pooled-20261006T171844711270` (21 rows, symbol `POOLED`) and its parquet, disclosed in the first-run report; remove after the forward week. | reported (already disclosed in D19) |

Design facts that are not bugs but matter for interpretation: `MIN_TRADES_OOS = 30` per symbol is unreachable on 5-year bars (dead per-symbol funnel); PSR > 0.95 is implied by the DSR (harmless redundancy); the null's hold for stop/target exits uses max(real hold, median signal-exit hold), which for patterns with almost no signal exits (`rsi_divergence` 3%, `high52` 0%) is estimated from a handful of trades or falls back to 10 (matching the realised hold directly, as the review did, is simpler; it would be a method-version change); robustness (WS4.3) executable-variant verdict is miscalibrated (17.8% noise pass) and stays advisory.

---

## 4. Ranked improvement programme (8 items)

Each item: what / why / how to validate without peeking / effort / honest prior / owner decision needed. "After the forward week" means after `docs/forward-test/2026-10` is frozen and D18's shadow period has its sessions (pre-registration §8.1).

### 1. Run the unchanged pooled shadow on 15-20 years of daily bars (owner's machine)
- **What:** same 22 rules, same gates, same `HOLDOUT_START`, same method version, on yfinance-length history (D18 b allows it; the code is length-agnostic). Prefer an ETF-weighted universe (SPY, QQQ, IWM, DIA, sector ETFs, TLT/IEF, GLD) for the long run, because the 13 single names are today's winners (survivorship). If the universe differs from the registered 17, it must be pre-registered under a new hash first (D18 choice 4) and costs N += 22 once; fold item 7 into the same registration so the cost is paid once.
- **Why:** T is the binding constraint. T 672 -> 4,000-5,000 moves the required Sharpe from 2.40 to about 0.9-1.0; nothing else on this list moves it as much.
- **Validation without peeking:** register the universe and the run plan in a decision entry before any bar is loaded; one look; hold-out cut identical; report the full funnel, not a shortlist; no rule changes after the look without a new trial.
- **Effort:** small (data pull on the owner's machine, one run, one decision entry).
- **Prior:** the MR family's 0.5-0.7 Sharpe here probably halves pre-2015; 15-25% that any pattern reaches `pooled_deflated_validated`, up from under 5% now. Even a null result is informative: it would end the "the data are too short" explanation.
- **Owner decision:** yes (runs outside the container; universe registration).

### 2. Retire-by-rule cleanup, one versioned commit after the forward week
- **What:** fix or rename `keltner_reversion` (bug 5); retire `macd_hist_reversal` and `confluence` from the per-symbol family (bugs 6, 8) by the non-performance rule D18 §6.1 already uses; rename `rsi_divergence` to what it is; document the undisplaced Ichimoku cloud; expose `bollinger_squeeze`'s quantile window; introduce a per-pattern source hash (bug 9) in the same commit so this is the last whole-module re-read.
- **Why:** cleaner N, honest names, and the per-pattern hash stops unrelated edits from spending hold-out reads. No return effect expected.
- **Validation without peeking:** golden tests pass; default signals of every untouched pattern byte-identical (the D15 precedent); one disclosed hold-out re-read recorded in a decision entry; no pooled numbers looked at before the commit.
- **Effort:** small.
- **Prior:** no return effect; statistics and alerts get cleaner.
- **Owner decision:** yes (a disclosed hold-out re-read and a retirement list are owner calls under D12/D15).

### 3. Stop running the ML and pairs scans nightly (keep the code, demote to a weekly health check)
- **What:** drop `scan_ml` and `scan_pairs` from `run-nightly` (or run weekly on pre-hold-out bars with `include_holdout=False`), keep the API routes and tests, record a decision entry. Default `include_holdout=False` on `/api/ml/predict` and `/api/backtest/run` (the D18(a) open item).
- **Why:** both produce nothing (0 ML signals, 0 pairs trades) while costing a reported ~25 minutes of nightly runtime for ML (unverified by me), one BH family and one trial registry per night for pairs, and nightly hold-out exposure for ML (bug 2). Pairs cannot be executed under D3 regardless.
- **Validation without peeking:** none needed; it removes exposure. The weekly health check should report the `no_skill` verdict and the pairs cointegration table as monitoring.
- **Effort:** trivial.
- **Prior:** no loss of information; the forward journal and per-symbol funnel are untouched.
- **Owner decision:** yes (changes the nightly composition recorded in D13/D15).

### 4. If ML is kept at all: fix the leak and the label, then run ONE experiment, meta-labelling on pattern events
- **What:** (a) sigmoid on tail A, thresholds on tail B or purged-CV OOS predictions (bug 1), plus a conformal-style abstain band instead of the 29-cut search; (b) label = the engine's own bracket outcome on the pattern's actual trades (bug 3), purge gap = max hold; (c) then a pooled, shallow secondary model (depth 2, 50-100 trees, `min_child_samples` >= 100, duplicates dropped) that only SIZES or SKIPS a pre-registered pattern's signal, never opens one. Each pattern it is attached to is a new trial (N += 1 per pattern; D18 §6.1).
- **Why:** it is the only ML configuration here that sits on something with a positive pooled Sharpe, and the label fix removes the structural reason the ML can never agree with execution.
- **Validation without peeking:** pre-register the feature list and the accept rule; null tests first (29-cut grid on label-shifted data must abstain >= 95% of seeds; the AR(1) positive control must still trade); event-level leave-one-fold-out with the pooled fold structure; metric = net R of filtered vs unfiltered trades with the cluster bootstrap already in `stats/pooled.py`; must beat the within-pattern label-shift null on >= 2 of 3 blocks AND improve the pooled DSR; forward period is the only confirmation.
- **Effort:** medium (a week of work plus tests).
- **Prior:** 80% "no improvement"; 10-20% relative lift in mean R on about a quarter of trades is the optimistic case. The meta-labelling sketch on a trend rule gave AUC 0.47-0.49, so the honest expectation is failure; the value is a definitive answer.
- **Owner decision:** yes (keep-or-retire ML, and the pre-registration).

### 5. One pre-registered consolidated "short-term pullback in uptrend" rule, replacing six overlapping MR variants
- **What:** e.g. close below the 20-day lower Bollinger band OR RSI(3) < 15, with close > SMA(200); exit on close > SMA(5) or a 10-bar time stop; stop only, no target. Written as a pre-registration with the same discipline as `preregistration-2026-10.md` (frozen hash, counts-only disclosure), counted as one new trial (N 22 -> 23: required Sharpe 2.40 -> 2.41 at T = 672; 0.87 -> 0.88 at 20 years).
- **Why:** the MR family is the only one that beats its random match under every exit, and its six members are 0.6-0.86 correlated: one rule, tested once, says what six near-duplicates say with six times the multiplicity. Literature grade C (Connors-Alvarez, IBS), replicated by practitioners, decayed since 2010.
- **Validation without peeking:** text and code frozen before any return is computed; counts-only feasibility check as in the existing pre-registration; first look on the long-history run (item 1); forward shadow as confirmation; it does NOT replace the six existing rules in N (retired patterns stay in N).
- **Effort:** small to medium.
- **Prior:** 30-40% to clear BH against the cluster null on 20-year data; 10-15% to clear the DSR; the 1-bar delay stress is the likely killer (the rebound is gone by the next open, as `rsi2_pullback` showed).
- **Owner decision:** yes (new pre-registration).

### 6. Add a 20-bar time stop to the executable bracket (exit version bump, N += 22)
- **What:** keep stop 2 x ATR / target 3 x ATR, add a time exit at 20 bars (about twice the median hold; do not tune the number). This changes the return series, so it is a new trial version: N += 22, required Sharpe 2.40 -> 2.57 at T = 672 (1.24 -> 1.33 at 10 years).
- **Why:** today 2:3 brackets with no time stop let trades run 60-100 bars (max 102 for `mean_rev_composite`), which sets the HAC bandwidth, shrinks T_eff for the PSR gate, and occupies the 8 slots the capped replay has. The exit-variant table shows -0.02 to +0.07 R versus built for most patterns, so mean R is roughly neutral while T_eff and the capped replay improve.
- **Validation without peeking:** method/exit version bump recorded; pre-hold-out replay (one look) plus forward shadow; judge by T_eff, max concurrent positions and capped-replay Sharpe ratio, not by return. Pay this bump in the same commit as item 2 so there is one re-read and one version.
- **Effort:** small.
- **Prior:** neutral on mean R, positive on T_eff; low risk. It is the one version bump worth its 0.17 of DSR bar.
- **Owner decision:** yes (version bump, N += 22).

### 7. Widen the pooled universe with low-correlation executable ETFs
- **What:** register a second pooled universe (XLE, XLU, XLV, XLP, XLF, TLT, IEF, GLD, EEM, EFA plus the current 17) under a new hash; evaluate the same rules; N += 22 for the new universe (fold into item 1's registration to pay once).
- **Why:** the current universe is the single biggest reason the null and the real series are hard to tell apart: 8 mega-caps entering in the same week. More entry-week clusters, more breadth, a less destructive capped replay.
- **Validation without peeking:** pre-register the universe; judge power by cluster count and breadth, not by returns; one look.
- **Effort:** small (data) to medium (universe registration tests).
- **Prior:** better statistics, no expected edge gain.
- **Owner decision:** yes (D18 choice 4).

### 8. Put the real improvement work into the allocation layer (not the scanner)
- **What:** (a) handcrafted core weights (Carver: group by correlation, equal risk within and across groups, heavy shrinkage of any Sharpe tilt) as the next core review; (b) the pre-registered O3 vol cap on the trend sleeve (weight = min(1, target/realised), never levering up); (c) advisory diagnostics in the pooled funnel: CSCV OOS path spread (5th-95th percentile), max drawdown and realised vol of the pooled series, vol-tercile attribution; (d) optionally a low-turnover factor-ETF tilt (momentum, quality, min-vol) tested on 20+ years of index proxies.
- **Why:** this is where the evidence is A/B grade (vol scaling for market and momentum survives costs; handcrafting is transparent and robust; drawdown and vol predict live behaviour better than Sharpe, Wiecki et al.). The scanner is where the evidence is C/D/F.
- **Validation without peeking:** long index history only (`scripts/long_history_backtest.py` exists), fixed parameters chosen once, gate on drawdown and worst-month improvement, not return; new core version = decision entry, not a tune.
- **Effort:** medium, spread over months, monthly cadence.
- **Prior:** 2-5 points less drawdown on the sleeve at roughly unchanged return; perhaps 0.5-1 point a year gross on the equity slice from a factor tilt with wide uncertainty. No alpha claim.
- **Owner decision:** yes (D4 core design).

**Explicitly NOT on the list:** a SPY-200-SMA regime filter on scanner entries (would remove the MR edge), stop/target multiplier tuning (excess R insensitive), Kalman pairs (grid negative everywhere), HMM/jump-model regime timing (D grade; the 200-day filter already does the job), probability-scaled/Kelly sizing (mis-calibrated p would scale the wrong trades), fractional differentiation, deep learning on daily bars, per-symbol selection by Hurst/half-life (forbidden by D18 and a new universe trial).

---

## 5. Accounts and signals worth adding to shadow tracking

Already tracked per `famous-investors.md` and the forward-test JSON: Berkshire, TCI, Himalaya, Pershing, a consensus basket, NANC, GURU, GVIP and the listed vehicles (BRK.B, PSH, FFH.TO, MKL), with Akre/Appaloosa/Duquesne/Baupost as watchlists. All figures below come from web-search summaries of fund letters, filings and trackers; `sec.gov`, `dataroma.com` and `equibles.com` could not be fetched directly, so **every weight and return is unverified until re-read from EDGAR**. Measure every clone from the filing date at the next open, price-return, against SPY plus a style control.

| Rank | Candidate | Type | Evidence | Lag | Benchmark | Prior on beating benchmark |
|---|---|---|---|---|---|---|
| 1 | **13D activist event basket** (named activist list: Elliott, Starboard, Third Point, Pershing, Trian, ValueAct, Jana, Ancora, Sachem Head, Engaged, Engine No.1, Politan, Icahn, Land & Buildings, Legion; cap > $1bn, ADV > $5m; buy next open after SC 13D, hold 60 trading days or until a 13D/A stake cut; equal weight; cap 20 names) | event signal | ~7% abnormal return in (-20, +20) days, Brav-Jiang-Partnoy-Thomas 2008 (2001-06); ~5% in Brav-Jiang-Kim 2015; no 5-year reversal, Bebchuk-Brav-Jiang 2015. **Caveat:** deHaan-Larcker-McClure 2019 find value-weighted long-run returns zero and the gains in micro-caps (~$22m); the live 13D Activist Fund (DDDIX) has done 11.8%/yr since Dec 2011, below the S&P. | 5 business days since Feb 2024 (was 10 calendar), so more of the jump is visible after filing | IWM, SPY, DDDIX | medium for a few points a year before costs; keep holds short |
| 2 | **Altimeter top-5 clone** (CIK 1541617; NVDA 19%, CBRS 16%, META 8%, TSM 7%, CRWV 6% as of Q2 2026 per aggregators) | 13F clone | top 2 = 35%, top 5 ~56%; strong 2023-26; behaves like levered SOXX/QQQ | 45 days | SMH, QQQ | medium-low; mainly tests whether Gerstner adds anything over SMH |
| 3 | **Punch Card clone** (CIK 1631664; 5 names: BRK 42%, CROX 27%, PDD 12%, PYPL 11% [one tracker says PYPL exited: conflict], SGOV) | 13F clone | near-zero turnover, no scale decay, no verifiable return series | 45 days | SPY | medium-low; the test is cheap |
| 4 | **Sequoia Fund** (SEQUX NAV plus Ruane Cunniff 13F top-10) | listed vehicle + clone | 13.5%/yr since 1970 vs 11.5% S&P; 2025 +22.1% vs +17.9%; but 10-yr 11.0% vs 14.8% (Valeant era) | NAV daily; 13F 45 days | SPY | medium; also tests daily-NAV fund vs 45-day-late clone |
| 5 | **Fundsmith LLP top-10** (CIK 1569205; MAR, SYK, WAT, V, UBER ...) | 13F clone as a quality-factor control | 13.8%/yr since 2010 but 5 straight years behind MSCI World | 45 days | SPY, QUAL | low as alpha; useful as a quality benchmark the consensus basket lacks |
| 6 | **Insider cluster-buy feature / basket** (`insider_cluster_30d`: 3+ distinct non-routine code-P buyers in 30 days; cap $300m-$5bn; ADV > $2m; hold 3-6 months; equal weight) | Form 4 signal | opportunistic buys ~82 bp/month VW, Cohen-Malloy-Pomorski 2012; clusters > 2% next month; but Oenschlager-Mollenhoff 2025: USD returns negative once position size is capped by liquidity; both insider ETFs (NFO, KNOW) closed | 2 business days | IWM | medium in %, low in $; small/mid caps are outside the D3 tradable universe, so shadow only |
| 7 | **Lone Pine top-10 idea feed** (CIK 1061165) | 13F watchlist | 2024 +36%; 2025 figures conflict; high turnover, shorts invisible | 45 days | QQQ | low as a clone; theme detector only |
| 8 | **Burry "public calls" ledger** (Cassandra Unchained posts, logged on publication date) | self-reported positions | replaces the deregistered Scion 13F; no audited P&L | 0 days | SPY, SOXX | low; contrarian/sentiment input only |

Also worth wiring: the pooled shadow signals (`shadow_signals` in `results/pooled/latest.json`) into the forward journal's 1-day / 5-day scoring once `services/forward.py` is unfrozen (D18 "not done, on purpose").

**Rejected after review, with the reason:** Congress trackers beyond NANC (Chen-Sacerdote NBER 2026: legislators underperform or match; a 17,859-disclosure test trails SPY by ~5.7 points on buys); eToro/Collective2/Darwinex/Composer leaderboards (popularity-driven copying, survivor snapshots, not IBKR-reachable); Nansen/Arkham "smart money" (labels are trailing-PnL survivors, no predictive evidence); GURU/ALFA/QAI replication (lag SPY by 1-6 points or are bond-like; ALFA liquidated 2022); Viking (90 names), Coatue/Tiger, Third Point, Greenlight, Elliott as clones; Giverny and Polen (3-5 weak years).

---

## 6. Reading list (the ten that matter most)

1. **Bailey & López de Prado, "The Deflated Sharpe Ratio" (2014)** and **"The Probability of Backtest Overfitting" (2015, with Borwein & Zhu)**: the exact math of the gate (`stats/selection.py` reproduces its known answers); why N and T, not strictness, decide what can pass.
2. **Harvey, Liu & Zhu, "... and the Cross-Section of Expected Returns" (RFS 2016)**: why t > 3 is the modern bar and why a 0.75 Sharpe becomes 0.32 after 200 tests; the frame for "more patterns raise the bar".
3. **Sullivan, Timmermann & White (1999) and Bajgrowicz & Scaillet (2012)**: thousands of technical rules tested with data-snooping corrections; none survive. The 20 registry patterns are that family.
4. **López de Prado, *Advances in Financial Machine Learning* (2018), ch. 3, 4, 7, 12**: triple-barrier labels (fixes bug 3 by construction), meta-labelling (item 4), purged CV with embargo (already built), CPCV path distributions (the advisory addition in item 8c).
5. **Carver, *Systematic Trading* (2015) and the pysystemtrade / qoppac handcrafting posts**: forecast scaling and combination across many weak rules, vol targeting, diversification multiplier, handcrafted weights; the cleanest public treatment of "many weak correlated rules into one sleeve" (item 8a).
6. **Wiecki, Campbell, Lent & Stauth, "All That Glitters Is Not Gold" (JoI 2016)**: 888 live Quantopian algorithms; backtest Sharpe explains almost none of live Sharpe (R^2 < 0.025), drawdown and vol do better. The reason the forward test, not the backtest, is the evidence.
7. **Do & Faff, "Does Simple Pairs Trading Still Work?" (FAJ 2010)** and **Krauss, "Statistical Arbitrage Pairs Trading Strategies: Review and Outlook" (2017)**: the decay of the Gatev baseline after costs and the dominance of non-converging pairs, which the 231-pair sweep reproduced.
8. **Avellaneda & Lee, "Statistical Arbitrage in the US Equities Market" (QF 2010)**: what a rebuilt stat-arb would look like (sector-ETF / PCA residual OU, s-score thresholds, kappa filter); Sharpe 1.1-1.4 in 1997-2007, decaying since; long/short, so D3-blocked.
9. **Gu, Kelly & Xiu, "Empirical Asset Pricing via Machine Learning" (RFS 2020)**: how small a credible ML edge is (monthly OOS R^2 well under 1% with 30,000 stocks); calibrates expectations for anything ML-shaped here.
10. **Brav, Jiang, Partnoy & Thomas (JF 2008) together with deHaan, Larcker & McClure (RAST 2019)**: the 13D announcement effect and its strongest rebuttal, both of which the activist basket (section 5, rank 1) must be scored against.

Honourable mentions, one line each: Connors & Alvarez, *Short Term Trading Strategies That Work* (2008), the RSI(2)/IBS family behind item 5, grade C; Cohen, Malloy & Pomorski, "Decoding Inside Information" (JF 2012), the routine-vs-opportunistic insider split behind rank 6; Cederburg, O'Doherty, Wang & Yan (JFE 2020), the real-time caveat on vol-managed portfolios that scopes O3 to a cap; Quantpedia, "Designing Robust Trend-Following System", why short-lookback breakouts on ETFs need an underlying Sharpe above 2 to pay costs.

---

## 7. What to stop doing

1. **Stop running the ML scan nightly on a frame whose OOS block is the hold-out.** It emits nothing, costs the most runtime, and burns the only unseen data (bug 2).
2. **Stop running the pairs scan nightly.** Zero tradable blocks in 3.7 years, zero executable under D3, one BH family and one registry per night for nothing.
3. **Stop reading the nightly "AAPL acc 0.54 / AUC 0.53" and "SPY/QQQ NOT valid (p=0.11)" lines as information.** The first is the base rate; the second is the same verdict every night on a non-cointegrated pair.
4. **Stop treating the first-run pooled Sharpes (0.5-0.97) as evidence of an edge.** Random entries with the same hold earn the trend/breakout numbers; the MR excess is real-signed but below significance in one regime.
5. **Stop adding generic-TA patterns to the registry.** The family already tests about four hypotheses with 22 trials; a 23rd EMA variant adds N without a new hypothesis. Add only a pre-registered rule with an a-priori reason (item 5).
6. **Stop tuning exits.** Stop/target multipliers move level and baseline together; excess R is insensitive. The one exit change worth a version bump is a time stop (item 6).
7. **Stop considering a SPY-200 regime filter on scanner entries.** It would remove the only pre-gate edge (MR below the SMA).
8. **Stop editing `patterns.py` for unrelated reasons** until the per-pattern hash exists; every edit re-reads every pattern's hold-out.
9. **Stop expecting the per-symbol order path to graduate anything on 5-year bars.** It cannot (`MIN_TRADES_OOS = 30`, maximum 22). Its forward test is a safety check of the plumbing, not a test of discrimination.
10. **Stop researching Congress trackers, copy-trading leaderboards, crypto wallet labels and bulk 13F replication.** The evidence is uniformly negative or absent; NANC and GVIP as benchmarks are enough.

---

### Appendix: what was measured and where

| Review | Scope | Scratch files (session `review/` folder) |
|---|---|---|
| Patterns | 22 rules, pooled D18 geometry, hold-matched random baselines (2,000 draws/trade), 4 exit variants, SPY-200 regime split, concurrency, redundancy, causality | `analyze_patterns.py`, `exit_variants.py`, `pattern_stats.csv`, `exit_variants.csv`, `regime_split.csv`, `concurrency.csv`, `per_symbol_counts.csv`, `signal_corr.csv`, `series_corr.csv`, `redundancy.json`, `patterns.md`, `table_*.md` |
| ML | code audit of `ml/`, `ml_patterns.py`, `features.py`; per-symbol purged CV + walk-forward with the platform's own pipeline; pooled vs per-symbol by horizon with a label-shift null; importance stability; meta-labelling sketch | `recompute_cv.py`, `per_symbol_cv.csv`, `pooled_experiments.py`, `pooled_results.csv`, `autocorr.csv`, `pooled.log`, `ml_static.md`, `ml_empirical.md`, `ml.md` |
| Pairs | 6 configured pairs full-window EG/Johansen, rolling formation windows, `walk_forward_pairs`, forced trading, 231-pair sweep, Kalman grid, EG power simulation | `pairs_analysis.py`, `pairs_analysis.json`, `universe_sweep.py`, `universe_sweep.json`, `kalman_grid.py`, `power_kalman.py`, `pairs.md` |
| Copy research | 13F filers, 13D, Form 4, Congress, copy-trading, crypto wallets, replication ETFs (web search only; sec.gov not fetchable) | `copy-research.md` |
| Techniques | López de Prado, Bailey, Harvey, AQR/Ilmanen/Pedersen, Carver, Chan, Quantopian/Allocate Smartly/Quantpedia, vol targeting, regime models | `techniques.md` |
| This note | DSR required-Sharpe table from the repo's formulas (no bars read) | `dsr_bar.py`, `dsr_bar.txt` |
