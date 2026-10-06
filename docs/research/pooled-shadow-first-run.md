# Pooled shadow first run (pre-hold-out OOS only)

- **Date:** 2026-10-06. **Status:** EXPLORATORY research record, shadow only, not evidence of an edge, no order eligibility. It was produced before the pre-registration was hashed and frozen (D19), so it is not confirmatory; the only confirmatory evidence is the forward shadow period (at least 20 sessions over 28 days) and the forward paper test.
- **Run:** `pooled_validation.evaluate_pooled` on `state/external_bars`, 17-symbol rule universe (hash `6eabbbfea159b2ee`), 21 patterns evaluated (20 registry minus retired `macd_hist_reversal`, plus the 2 pre-registered: `rsi2_pullback`, `high52_breakout`), pooled N = 22.
- **Data boundary:** every frame was cut to bars strictly before `HOLDOUT_START` (2025-07-01) *before* the call, and no hold-out store was passed. No hold-out bar was loaded into the evaluator and no hold-out read was made or recorded. Hold-out performance is not reported here.
- **Isolation (corrected in review round 1):** the pooled TRIAL LEDGER and hold-out store were temporary, so this run added nothing to the real pooled N ledger (the real `trials.sqlite` has no `pooled_*` ledger tables) and read no hold-out; the first real nightly shadow run registers the 22 trials (verified on an isolated forward copy: 22 rows, all `method_version` 2, n_trials = 22). It was NOT fully isolated, though: the call left `registry` at its default, so the real `state/trials.sqlite` holds run `pooled-20261006T171844711270` (21 trials, symbol `POOLED`, written 2026-10-06 17:18 to 17:19 UTC) and `data/trials/pooled-20261006T171844711270.parquet` exists. It has no effect on N (per-symbol queries filter by `run_id`, and the pooled N comes from the ledger tables) and none on the per-symbol trial counts. The owner may delete that run's rows and parquet; they were not removed here because the forward test is running. The code now refuses this: `evaluate_pooled` raises when a ledger or hold-out store is injected without an explicit `registry`, and `evaluate_pooled_research` forces the registry and component files off.
- **N and method versions:** v1 and v2 produce identical pooled return series (only the capped-replay gate differs), so N stays 22: this v1 run is a disclosed look at the data, not a separate strategy, and the live ledger never held v1 rows. When the forward week ends, the stray v1 registry rows and parquet named above should be removed.
- **Method version:** this run used pooling method version 1. Version 2 (review round 1) makes the capped replay's symbol exclusion causal. Nothing in the tables below depends on it: no pattern passes the PSR gate, so none reaches the capped-replay gate, and no capped figure is quoted. The run was not repeated on the real bars.
- **OOS region:** 2022-10-10 to 2025-06-13 for META only (672 days on the pooled axis; 252-bar warm-up lever, 126-bar test windows). META has only 766 pre-hold-out bars, the others 934. Folds are anchored at bar 0, so the other 16 symbols' OOS covers bars 252 to 882 (about 2022-10 to mid-April 2025) and the last 42 axis days come from META alone; T and T_eff overstate the cross-sectional coverage by those 42 days (630 days of full coverage). Right-aligned folds are a later method version (after the forward week).

## Funnel (patterns alive after each gate, cumulative)

| Stage | Alive |
|---|---|
| patterns tested | 21 |
| pooled min trades (>= 60 trades and >= 30 entry weeks) | 19 |
| breadth (>= 9 symbols with >= 3 trades) | 19 |
| OOS return > 0 | 17 |
| PSR > 0.95 (T_eff) | **0** |
| cluster-preserving null + BH | 0 |
| DSR p < 0.05 (N = 22) | 0 |
| concentration / LOSO / LOCO / fold | 0 |
| cost x2 and 1-bar delay | 0 |
| capped replay | 0 |
| hold-out (not read) | 0 |
| `pooled_oos_validated` / `pooled_deflated_validated` | 0 / 0 |

Nothing passes: every pattern stops at or before the PSR gate. Advisory only: PBO 0.87, cross-sectional PBO 0.68 (both high, i.e. in-sample-best patterns do not stay best). Cross-trial Sharpe variance used: 0.00056.

## Per-pattern pooled OOS statistics

Mean R is per trade in units of the stop distance; return and Sharpe (annualised) are of the pooled equal-risk daily series. Cost and delay columns are pooled return under 2x costs and a 1-bar entry delay.

| Pattern | Trades | Entry weeks | Breadth | Mean R | Pooled return | Sharpe | PSR | Null p | BH p | Return, 2x cost | Return, 1-bar delay | First failed gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| adx_trend | 69 | 44 | 12/17 | -0.163 | -0.056 | -0.58 | 0.18 | 0.96 | 1.00 | -0.080 | -0.045 | oos_positive |
| atr_breakout | 172 | 72 | 17/17 | 0.215 | 0.183 | 0.85 | 0.92 | 0.39 | 0.63 | 0.123 | 0.213 | psr |
| bollinger_bounce | 242 | 72 | 17/17 | 0.160 | 0.166 | 0.56 | 0.82 | 0.09 | 0.55 | 0.085 | 0.240 | psr |
| bollinger_squeeze | 177 | 76 | 17/17 | 0.078 | 0.069 | 0.31 | 0.69 | 0.56 | 0.69 | -0.004 | 0.047 | psr |
| confluence | 25 | 19 | 3/17 | -0.029 | -0.006 | -0.10 | 0.45 | 0.42 | 0.63 | -0.013 | -0.001 | pooled_min_trades |
| donchian_breakout | 367 | 104 | 17/17 | 0.172 | 0.307 | 0.78 | 0.90 | 0.79 | 0.87 | 0.162 | 0.245 | psr |
| ema_crossover | 221 | 89 | 17/17 | 0.136 | 0.151 | 0.63 | 0.85 | 0.25 | 0.55 | 0.074 | 0.200 | psr |
| high52_breakout **(new)** | 179 | 68 | 16/17 | 0.152 | 0.136 | 0.55 | 0.81 | 0.72 | 0.84 | 0.055 | 0.129 | psr |
| ichimoku_cloud | 343 | 110 | 17/17 | 0.123 | 0.211 | 0.70 | 0.87 | 0.17 | 0.55 | 0.086 | 0.259 | psr |
| inside_bar_breakout | 332 | 110 | 17/17 | 0.089 | 0.158 | 0.55 | 0.76 | 0.37 | 0.63 | 0.038 | 0.241 | psr |
| keltner_reversion | 365 | 111 | 17/17 | -0.007 | -0.012 | -0.04 | 0.47 | 0.44 | 0.63 | -0.134 | 0.122 | oos_positive |
| macd_crossover | 424 | 114 | 17/17 | 0.093 | 0.212 | 0.67 | 0.86 | 0.22 | 0.55 | 0.053 | 0.161 | psr |
| mean_rev_composite | 145 | 47 | 17/17 | 0.099 | 0.032 | 0.15 | 0.59 | 0.46 | 0.63 | -0.014 | 0.126 | psr |
| rsi2_pullback **(new)** | 387 | 96 | 17/17 | 0.021 | 0.040 | 0.20 | 0.63 | 0.12 | 0.55 | -0.115 | -0.026 | psr |
| rsi_divergence | 183 | 67 | 17/17 | 0.227 | 0.181 | 0.69 | 0.87 | 0.15 | 0.55 | 0.121 | 0.208 | psr |
| rsi_reversal | 90 | 35 | 14/17 | 0.120 | 0.012 | 0.07 | 0.54 | 0.49 | 0.64 | -0.013 | 0.080 | psr |
| sma_200_trend | 149 | 78 | 17/17 | 0.090 | 0.068 | 0.55 | 0.80 | 0.10 | 0.55 | 0.018 | 0.055 | psr |
| stochastic_cross | 358 | 91 | 17/17 | 0.116 | 0.190 | 0.47 | 0.78 | 0.11 | 0.55 | 0.068 | 0.202 | psr |
| trend_momentum_combo | 288 | 92 | 17/17 | 0.073 | 0.084 | 0.27 | 0.65 | 0.23 | 0.55 | -0.018 | 0.243 | psr |
| triple_ema | 234 | 101 | 17/17 | 0.220 | 0.258 | 0.97 | 0.94 | 0.21 | 0.55 | 0.173 | 0.255 | psr |
| volume_breakout | 54 | 32 | 10/17 | 0.261 | 0.070 | 0.75 | 0.89 | 0.41 | 0.63 | 0.051 | 0.040 | pooled_min_trades |

## The two pre-registered patterns

- **`rsi2_pullback`:** 387 trades, 96 entry weeks, all 17 symbols; mean R 0.021, Sharpe 0.20, PSR 0.63, null p 0.12 (BH 0.55). It fails PSR, BH, the concentration checks (flagged `pooled_concentrated`; the largest single-symbol share is NVDA at 20%, under the 25% cap, so the flag comes from a leave-out check), the cost stress (negative at 2x costs, as the pre-registration predicted given about 0.3% round-trip cost), the 1-bar delay (negative, the rebound is gone by the next bar) and the capped replay. Typical hold is short (max 9 bars).
- **`high52_breakout`:** 179 trades, 68 entry weeks, 16 symbols; mean R 0.152, Sharpe 0.55, PSR 0.81, null p 0.72 (BH 0.84). Positive but indistinguishable from the long-only random-entry null (the sample is a rising market); it fails PSR and BH. Its bootstrap Sharpe interval is wide (about -0.56 to 1.63). IWM and XOM have a full-sample own t-stat below -2, which is the forward-eligibility exclusion. (Version 1 also dropped their trades from the capped replay, which looked ahead; version 2 applies the rule only from trades that had already exited.)
- Both are therefore `unvalidated`, as the pre-registration's a-priori expectation said (the pooled test as built, DSR p < 0.025, needs about 2.4 to 2.5 annualised Sharpe at N = 22 and T of about 630 to 672 days with V = 1/T (2.2 to 2.3 is the p < 0.05 figure), and more once T_eff is below T; 2.9 is the 504-bar construction's figure. The best observed is 0.97, triple_ema).

## Caveats (read before citing anything above)

1. **Not confirmation.** This is one look at about 2.7 years of in-sample-for-this-purpose OOS bars, a single market regime (mostly rising), with 17 highly correlated symbols (8 mega-cap tech). Effective independent observations are far fewer than the trade counts suggest; the reported T_eff (about 430 to 672 of 672 days) should be read with that in mind.
2. **Observed event counts differ from the pre-registration estimates** (P1: 387 vs about 343 on the 252-bar construction; P2: 179 vs about 87). The estimates were computed without ATR exits and, for P2, with a different (High-based, filtered) definition and were explicitly lower bounds. Counts are not a reason to change either rule. The rules were not changed.
3. **The funnel is incomplete beyond PSR by construction:** only gates up to the first failure matter for a failed pattern; later gate columns are not decision-relevant and the nightly path also needs the hold-out read for any survivor, which none reached.
4. **Registered after contact?** The disclosure in pre-registration section 1 (SPY return check, event counts) stands. The results above were seen after registration, so any change to P1/P2 is a new trial counted in N.
5. **Per-symbol "firing tonight" signals** exist in the stored payload (shadow only, no orders) but are not discussed: they are not evidence.
6. **Hold-out remains unread for pooled purposes.** When the real nightly shadow run reaches a survivor, it will do the single allowed read; none of today's patterns survives to that gate.
7. Paper-only, long-only, IBKR read-only, all orders via `execution.submit_intent`: unchanged. Pooled statuses are not order-eligible.
