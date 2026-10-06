# Proposal: pooled validation (pattern-level, pooled across the universe)

Status: **PROPOSED, NOT APPROVED.** This is a design for the owner to review. Nothing here is implemented. The
owner approved planning it, to be built **after** the October 2026 forward-test week. Decision entry: D18 in
`docs/decisions.md`.

Invariants this proposal keeps: paper only (`paper=True` hard-coded, no live mode), long-only, US equities/ETFs
only executable, every order through `execution.submit_intent`, IBKR read-only, the hold-out read once per
strategy version, and fail-closed behaviour on any store or registry failure.

---

## 1. Problem

`validation.evaluate_candidates` validates each **(symbol x pattern)** executable candidate on its own. With the
current settings that unit cannot reach the evidence bar:

- **Not enough OOS trades per candidate.** The bars are IBKR 5-year history (2021-10-08 onward; META from
  2022-06-09). `HOLDOUT_START = 2025-07-01` leaves 934 pre-hold-out bars. A 504-bar train window plus 126-bar test
  windows gives **3 folds = 378 OOS bars** per symbol (META: 2 folds, 252 bars). The most active pattern makes at
  most 19 OOS trades on any one symbol. `MIN_TRADES_OOS = 30` is therefore unreachable for every candidate.
  Forward test 2026-10-05: `tested=340, min_trades=0, ... orders=0`. A longer history alone would not fix this
  quickly: at about 6 to 13 trades per symbol-year for the active patterns, 30 trades per candidate needs 3 to 5
  OOS years per symbol.
- **The deflation is very harsh.** N is floored at `universe_trials()` = 39 watchlist symbols x 20 patterns =
  780, although only 17 symbols have data. With the stored `sharpe_var = 0.0027` and T = 378, the expected maximum
  Sharpe of 780 null trials is 0.166 per day (2.6 annualised). Clearing DSR p < 0.05 needs about **4.0 annualised
  Sharpe** on one symbol's OOS series.
- **BH over 340 to 780 hypotheses** needs p <= alpha/m for the best candidate, which is about 6.4e-5 at m = 780.

The result is that the platform almost never trades, however long the data is. Part of this is intended (D11:
"expect very few, possibly none"). Part of it is a design artefact. The question the platform should ask is
"does pattern P have an edge on US large caps and ETFs?", not "does pattern P have an edge on AAPL in particular?".
The per-symbol question has about 20 times less data, and the answer to it is mostly noise.

## 2. Proposal in one paragraph

Make the **unit of validation the pattern** (one executable variant per pattern), pooled across a
**pre-registered, hashed universe** (the executable `WATCHLIST["stocks"]`; optionally split by asset class).
Walk-forward stays per symbol and unchanged. Each symbol's OOS trades and daily mark-to-market returns are then
combined into one **equal-risk pooled daily return series per pattern**. Every gate (min trades, OOS PSR,
random-entry null, BH, DSR, cost stress, hold-out) runs on that pooled object. N and m count **patterns and
variants** honestly and cumulatively, not symbols. Dependence is handled in two ways: (a) PSR and DSR run on the
**date-level** pooled series, so T counts calendar days, not trades, and cross-sectional correlation is priced in;
(b) the null and the trade-count gate use **entry-week clusters**, not raw trades. Per-symbol sanity checks are
kept as gates (breadth, concentration, leave-one-symbol-out and leave-one-cluster-out). A pattern that passes may
trade on **any universe symbol where it fires**, risk-sized by the existing D5 rules. The new status
`pooled_deflated_validated` starts **shadow-only** (journaled, alert-only). It becomes order-eligible only by a
later, separate owner decision.

## 3. Unit of validation and the universe

- **Unit:** `(pattern, executable variant, universe)`. The executable variant is the one in
  `validation.executable_candidates`: long-only, stop `ATR_STOP_MULT` x ATR, target `TAKE_PROFIT_ATR_MULT` x ATR.
- **Universe = defined by rule, part of the strategy version.** The rule is: **all executable
  `WATCHLIST["stocks"]` symbols with usable data**, optionally split only by asset class. The sorted symbol list is
  hashed (`universe_hash`) into the pooled strategy version. Any other universe (a symbol added, dropped or
  swapped) needs **its own decision entry recording why it was chosen**, and counts as a **new trial for every
  pattern** (N += number of patterns, not +1), with its own pooled hold-out read subject to the read cap in
  section 10. Per-symbol OOS results are already stored and visible, so a universe that drops weak names could
  otherwise be picked with that knowledge and re-registered cheaply; a universe built from the previous
  universe's per-symbol OOS winners is refused (section 15, test 14).
- **Optional asset-class split** (`pattern x asset_class`, for example `stock` vs `etf`, from `instruments.py`).
  It doubles N and halves the data in each cell, so it is **off by default**. It may be turned on only as a
  pre-registered version, never after looking at results. Crypto, FX and futures may be pooled as separate
  **research-only families** with their own registry, BH and N. They are never order-eligible (D3).
- **Missing symbols.** A universe symbol with no usable bars (data-quality failure, stale) has no OOS trades.
  It is **not dropped silently**. If more than `POOLED_MAX_MISSING` (default 10%, so 1 of 17) of the universe is
  missing, the run is not pooled-validatable (`pooled_universe_incomplete`). The fallback is **alert-only**: the
  last stored pooled verdict may be shown for display with its data age (and a maximum age, after which it is not
  shown), but an incomplete universe is **never order-eligible**, whatever the stored verdict says. The missing
  symbols are recorded in the funnel and in the hold-out read key. Below the threshold a missing symbol is also
  recorded (never dropped silently) and the run is flagged `pooled_universe_partial`; the missing set is part of
  the read key, so a later complete run is a new, separately counted evaluation.

## 4. How OOS trades and returns pool

### 4.1 Per symbol (unchanged)
For each symbol s, `walk_forward(df_s, [cand])` runs exactly as it does today. With one candidate it runs in
`continuous` mode, so there is no selection and every fold's test slice comes from one continuous run. This gives
`oos_returns_s` (dated, test windows only) and `oos_trades_s`. Nothing at or after `HOLDOUT_START` is touched.

### 4.2 Equal-risk weighting
Execution sizes positions by risk: 0.5% of the sleeve to a 2 x ATR stop. The pooled statistic should weight the
same way, so that a high-volatility name (TSLA, AMD) does not dominate a low-volatility one (DIA).

- **Trade level:** each trade's result is expressed in **R-multiples**:
  `R = pnl_pct / stop_frac`, where `stop_frac = ATR_STOP_MULT x ATR_signal / close_signal` (the same anchor that
  execution and the null use). Pooled mean trade = mean R over all pooled trades.
- **Date level (the series that PSR, DSR, PBO and the hold-out use):**
  `r_pool[t] = sum over open trades i on date t of w_i x r_i[t]`, with `w_i = RISK_PER_TRADE_PCT / stop_frac_i`
  and `r_i[t]` the trade's daily mark-to-market return (net of costs, as the engine computes it). This is the
  **uncapped equal-risk sleeve**: what the sleeve would earn if every signal were taken at equal risk. Flat days
  are 0, as in today's per-symbol series. The engine's returns use `MAX_POSITION_SIZE_PCT` sizing, so the
  implementation rescales each symbol's position-level returns by `w_i / MAX_POSITION_SIZE_PCT` while the trade is
  open. The engine itself does not change.
- **Capped replay (implementation check):** the same trades are replayed through the D5 caps (8 positions, 2 per
  cluster, 5 new orders a day, 10% per symbol, 4% heat, 100% gross). Ties are broken by a **pre-registered,
  non-informative order** (symbol alphabetical). The capped replay is what execution would actually do; see the
  gate in section 8. This matters: OOS diagnostics show a mean of **4 to 6 concurrent pooled trades and a maximum
  of 16** for the active patterns, so the caps will bind on busy days.

### 4.3 Dependence: overlapping trades and cross-sectional correlation
Pooled trades are **not** independent. A long-only pattern that fires on 10 symbols in the same week mostly bets
on the market that week. The design does not treat the 260 MACD trades as 260 observations.

1. **PSR and DSR on the date-level series.** T is the number of OOS calendar days (378), not the number of trades.
   Correlated positions do not diversify, which shows up as a lower Sharpe of `r_pool`. This handles cross-sectional
   correlation without modelling it.
2. **Serial correlation.** Overlapping multi-day holds make `r_pool` autocorrelated. The PSR's
   `sqrt(T-1)` assumes iid observations, so it is replaced by `T_eff = T / (1 + 2 sum_{k=1..q} (1-k/(q+1)) rho_k)`
   (Bartlett). The bandwidth `q` is set **per pattern** from its maximum observed hold (floored at
   `POOLED_HAC_LAGS`, default 10), or by Newey-West automatic bandwidth, so slow patterns (`sma_200_trend`,
   `adx_trend`) whose holds exceed 10 bars do not get an overstated T_eff. This is the Lo (2002) style correction.
   T_eff is never allowed to exceed T. PSR, DSR and the `1/T_eff` variance floor below all use T_eff.
3. **Cluster counting for the trade gate.** A cluster is one **ISO entry week** across all symbols. The
   min-trades gate counts clusters as well as raw trades (section 5).
4. **Bootstrap confidence interval (reported, not gated).** A stationary block bootstrap (Politis-Romano, mean
   block 10 days, 2,000 draws, seeded) of `r_pool` gives a CI for the pooled Sharpe. A cluster bootstrap over entry
   weeks gives a CI for the mean R. These show the uncertainty; the gates use the analytic PSR/DSR with T_eff and
   the cluster-preserving null.

## 5. Gates on the pooled object

Thresholds below are proposed defaults. Each would be a named config constant, fixed before the first pooled run.

| Gate | Definition | Proposed threshold | Replaces |
|---|---|---|---|
| `pooled_min_trades` | raw pooled OOS trades **and** distinct entry-week clusters | trades >= 60 **and** clusters >= 30 | `MIN_TRADES_OOS` per candidate |
| `breadth` | symbols with >= 3 OOS trades | >= max(8, ceil(0.5 x K)) | none |
| `oos_positive` | pooled OOS total return (equal-risk series) | > 0 | same |
| `psr` | PSR(SR > 0) of `r_pool` with T_eff | > `OOS_PSR_MIN` (0.95) | same, per candidate |
| `null` + `bh` | cluster-preserving random-entry null p (section 7), BH across all pooled trials | BH at `FDR_ALPHA` | same, but m = patterns |
| `dsr` | DSR of `r_pool` (T_eff, own skew and kurtosis) against SR0(N, V) | p < `DSR_P_MAX` | same, but N = pooled trials |
| `concentration` | section 9 | all pass | none |
| `cost_stress` | pooled return at `COST_STRESS_MULT` x costs | > 0 | same |
| `delay_stress` | pooled return with signals delayed 1 bar | > 0 (**gated** when pooled; recorded today) | recorded only |
| `capped_replay` | capped replay pooled OOS return (section 4.2) | > 0, and Sharpe >= 50% of the uncapped Sharpe | none |
| `holdout` | pooled hold-out (read once, section 10) | return >= 0 **and** pooled hold-out trades >= 20 | per-candidate hold-out >= 0 |

**Why the delay stress becomes a gate.** Per symbol, a 1-bar delay result was too noisy to gate. Pooled, it is
estimated on 10 to 20 times more trades. A pattern whose pooled edge does not survive one bar of delay is not
something execution can capture.

**Why the hold-out needs a minimum number of trades.** D11 records that a flat hold-out (zero trades) passes
`>= 0`. Pooled, a real pattern should make dozens of trades over the hold-out. At the pre-hold-out rates, the active
patterns would make about 100 to 200 over the 2025-07 to 2026-10 hold-out (this is an estimate from the OOS rate,
not a hold-out read), so requiring 20 costs nothing for a pattern that fires.

**`V` (the cross-trial Sharpe variance).** With about 20 pooled trials, the empirical variance across trials is
itself noisy. It is floored at `max(V_empirical, 1/T_eff)`, the sampling variance of a per-period Sharpe estimate
under the null, and at the last full nightly pooled run's V (the D15 floor idea, kept).

## 6. Trial counting (N), the registry and BH

### 6.1 What counts as a trial
One pooled trial = one `(pattern, variant params, asset_class split, universe_hash, pooling-method version)`
evaluated **under the current `HOLDOUT_START`**. Honest counting rules:

- **N is cumulative per hold-out date, not per run.** Re-running the same version nightly on one more day of data
  is the same trial and does not add to N. A **new version** of any of its parts adds 1 and keeps the old ones,
  because they were looked at. Examples: a pattern edit, a new exit multiple, a universe change, an asset-class
  split. This fixes the known D15 gap ("N still does not accumulate across separate nightly runs") at the
  pooled level, where it matters because N is small.
- **Parameter variants count.** If robustness grids (WS4.3) or exit grids are ever used to **choose** what goes
  to the gate, every grid point evaluated counts in N. The 3 to 9 points per pattern give about 100 trials for
  15 patterns. If they are only a necessary-evidence check on the pre-registered centre, they are reported but do
  not count. This must be decided per version and recorded, never after the fact.
- **Retired patterns stay in N.** A pattern retired or pruned after pooled results exist remains in the cumulative
  N for the current `HOLDOUT_START`; retiring the weakest patterns must not lower N after the data was seen.
  Pruning may only use criteria that do not depend on performance (identical signals, trade-count floors),
  recorded **before the first pooled run**.
- **Per-symbol and pooled are two families.** While both stay eligible, alpha is split between the two families
  (each tested at half `FDR_ALPHA` / `DSR_P_MAX`), unless per-symbol eligibility is retired when pooled goes live.
- **Floor:** N >= `pooled_universe_trials()` = registered patterns x registered variants x asset-class splits (20
  today). This is the Phase 4 universe-N floor idea moved to the pattern axis: a narrowed run (`scan --pattern P`)
  is deflated with the full pattern count, never with N = 1.
- **Duplicates are counted, and flagged.** Finding while estimating (section 13): `macd_crossover` and
  `macd_hist_reversal` produce **identical signals** on all 17 symbols before the hold-out. Both are counted (the
  conservative choice). The registry flags trials whose pooled series are identical
  (`duplicate_of`). Retiring one of them is a separate pattern-library decision that would have to be made
  before the first pooled run, using only the identical-signal criterion (not performance), and the retired
  pattern stays in N (above).

### 6.2 Registry changes
- Keep `trials/registry.py` as it is for per-symbol runs.
- Add a **pooled family**: `TrialRegistry(run_id="pooled-<utc>", unit="pooled")`. Each trial records the pooled
  `r_pool` series as one column in `data/trials/<run_id>.parquet`. The per-symbol component series are written to
  `data/trials/<run_id>.components.parquet` (date x (pattern, symbol)) for the leave-one-out checks and the
  cross-sectional CSCV.
- Add a **cumulative ledger** `pooled_trials_ledger(holdout_start, version, pattern, params_hash, universe_hash,
  asset_class, method_version, first_run_id, first_seen, duplicate_of)` with primary key
  `(holdout_start, version)`. N for the gate = `COUNT(*) WHERE holdout_start = current`, floored as in 6.1.
- **Storage without a state-DB migration.** As with `holdout_reads.sqlite` and D17's JSONL decision, the ledger
  lives in `trials.sqlite` and is created with a schema owned by the registry module (`CREATE TABLE IF NOT EXISTS`).
  It is **not** migration 008: a new migration would make every existing state DB fail closed until migrated.
  The `trials` table gains no column; the unit is told apart by the `run_id` prefix and a `unit` field in the
  pooled ledger.
- **Fail closed:** if the ledger cannot be read or written, N cannot be established and nothing pooled validates
  (`pooled_trials_unavailable`).

### 6.3 BH
One BH family per run over **all pooled trials of the executable family** (m = about 20), on the
cluster-preserving null p-values (section 7). The tests are positively dependent (patterns share symbols and
market moves); BH is valid under positive regression dependence (PRDS). Benjamini-Yekutieli is not needed. The
`adaptive_null` resolution rule stays, with m = pooled family size (alpha/m = 0.0025, so 1,000 draws suffice at
the base level and contenders go to 10,000).

## 7. Random-entry null, pooled

Today: each real OOS trade is re-drawn with a random entry inside its own fold window, keeping its symbol,
direction, holding period and exits. The statistic is the mean net `pnl_pct`.

Pooled version:
- **Statistic:** pooled mean **R** per trade (equal-risk), over all symbols.
- **Cluster-preserving draws.** Real trades in the same entry-week cluster share market exposure. If the null drew
  them independently, the variance of the null mean would be too small and p-values too small. Instead each
  **cluster** draws one random calendar offset (uniform over offsets that keep every member inside its own fold
  window. Offsets are **restricted to those that fit every member**; if more than about 5% of cluster members
  cannot fit any shared offset, the null fails closed (`pooled_null_unfit`) rather than letting them draw
  independently, which would leak back to the independent null this design rejects). All members of the cluster shift by that
  offset on their own symbol's bars, so the null keeps the cross-sectional co-movement of the real trades.
- **Long-only drift is controlled.** The null trades are long too, so a pattern that only harvests market drift
  (a strongly rising 2021-2025 sample) does not beat its null. This is the gate that separates timing from beta.
  The PSR and DSR alone do not control beta; that is why the null/BH gate and the DSR gate are both required.
- **Calibration requirement:** on a synthetic universe with a common factor (17 symbols, pairwise correlation 0.6,
  pure noise patterns), the null p-values must be uniform (KS test over seeds, slow test). A test with independent
  draws should fail this check, which shows that the cluster preservation is needed.

## 8. Cost and delay stress

The same fold choices are replayed per symbol (`_replay`, unchanged) with 2x costs and with a 1-bar delay. Then
the results are pooled exactly as in section 4. Gates are in the table in section 5: cost > 0 and delay > 0.
Optionally, a liquidity-tiered cost model (ETFs vs single stocks) can be adopted **as a version**. It is not part
of this proposal.

## 9. Per-symbol sanity checks (gating) and which symbols trade

### 9.1 Concentration gates (at pattern level)
A pooled edge that really lives in one name or one sector is a per-symbol edge with an inflated sample. All of
these must pass:

- **Max symbol share:** no symbol contributes more than `POOLED_MAX_SYMBOL_SHARE` (25%) of the pooled
  **absolute** R-P&L. Absolute, so that a big loser cannot offset a big winner.
- **Leave-one-symbol-out (LOSO):** pooled mean R > 0 with any single symbol removed.
- **Leave-one-cluster-out (LOCO):** pooled mean R > 0 with any `config.CLUSTERS` group removed. This matters
  because `mega_tech` is 8 of the 17 symbols.
- **Leave-one-fold-out:** pooled mean R > 0 with any single 126-bar OOS window removed, so that one regime cannot
  carry the result.
- **Breadth:** at least max(8, ceil(K/2)) symbols with 3 or more OOS trades (section 5).

### 9.2 Which symbols trade once a pattern passes
- **All universe symbols where the pattern fires** on the last closed bar (`latest_signal(max_staleness_bars=0)`,
  as today), risk-sized by `execution` (0.5% risk to 2 x ATR) under every D5 cap. Ties under the caps use the same
  pre-registered order as the capped replay.
- **Excluded symbols** (a pre-registered rule, recorded on the intent): a symbol with no complete OOS fold, and a
  symbol whose own OOS mean R is **strongly negative** (one-sided t < -2 on that symbol's trades). The bar is
  deliberately weak: excluding mildly negative symbols would be per-symbol selection by the back door.
  **The same exclusion rule is applied inside the capped replay** (section 4.2), so what is validated is what is
  traded.
- **The intent carries** `validation_unit="pooled"`, `pooled_version` and `universe_hash`. `execution.submit_intent`
  gains one check: a pooled intent whose symbol is not in that version's universe, or whose version does not
  match the latest stored pooled verdict, is rejected (`pooled_symbol_not_in_universe` / `pooled_version_mismatch`).
  This is the only change to the chokepoint. It adds a rejection and opens no new path.
- A symbol **outside the universe** where the pattern fires is alert-only. It never inherits the pooled status.

## 10. Hold-out: read once per pooled pattern version

- **Pooled strategy version** = hash of: the per-symbol `strategy_version` inputs (pattern source,
  `patterns.py`/`features.py` hashes, exits, costs, execution mode, `HOLDOUT_START`), plus `universe_hash`,
  `asset_class` split, the weighting rule, the concentration and gate constants, and `POOLING_METHOD_VERSION`.
- **Read once, capped.** The first pooled hold-out evaluation per version is stored in `holdout_reads.sqlite`
  under the key `pooled|<pattern>|<cand.key>|<pooled_version_hash>`, where the key uses the **full pooled version
  hash** (pattern source, gate constants, weighting rule, `POOLING_METHOD_VERSION`, universe hash and the missing
  symbol set), not only the universe hash (a new key namespace, so no schema change). Later runs of the same
  version gate on the stored read (`holdout_frozen`). A store failure fails closed (`holdout_store_unavailable`),
  as in D12. Because a fresh version would otherwise get a fresh read of the same hold-out bars, the ledger also
  keeps a **per-`HOLDOUT_START` count of pooled hold-out reads**: at most **one read per pattern per
  `HOLDOUT_START`**. A later version of that pattern must move `HOLDOUT_START` or accept a disclosed re-read in a
  decision entry (the re-read is counted, with a Bonferroni correction over all reads of that pattern).
- **The existing hold-out exposure is wider than the stored reads.** The per-symbol hold-out for every
  (symbol, pattern) of the current versions has already been **computed and stored**. It is also shown by the
  candidate GET route and by any report that displays a candidate. In addition, `POST /api/backtest/run`
  (`routes/backtest_routes.py`) and the ML routes (`routes/ml_routes.py`) default to `include_holdout=True`, so any
  ad-hoc backtest of any symbol and pattern shows hold-out performance, and **those views are not written to
  `holdout_reads.sqlite`**. The ledger therefore cannot support an attestation on its own.
  **Prerequisite of the build:** default `include_holdout` to `False` on those routes, and record every
  hold-out-including evaluation (symbol, pattern, timestamp) in a log from then on. The pooled hold-out is an aggregate of the same
  bars, so it is not fresh data. Before the first pooled run:
  1. The owner attests whether any hold-out numbers (stored per-symbol reads, the candidate route, **and ad-hoc
     backtest or ML views**) were looked at and used to choose patterns, exits or the universe. The forward-test funnel never reached a reported candidate (`min_trades = 0`), which lowers
     but does not rule out exposure.
  2. If they were, either accept the re-read with a written disclosure (the D15 precedent of an accepted re-read),
     or move `HOLDOUT_START` forward (a new decision entry). Moving it forward costs OOS length that pooling needs.
     The proposal recommends attest-and-disclose **only if the exposure can be bounded**. If it cannot be ruled out
     (the ad-hoc views are unlogged), prefer moving `HOLDOUT_START`, or treating the forward window (from
     2026-10-05) as the only confirmatory test.
  3. In either case, the **forward paper period after 2026-10-05** is the clean confirmatory window. A pooled
     pattern that becomes eligible is re-checked on it (section 12, phase 3).
- **Once a pattern has been read, its pooled candidate set is frozen.** Pooling "all registered patterns" is the
  pre-registration. Removing a pattern from the pool after seeing its pooled hold-out is not allowed without a
  new `HOLDOUT_START`.

## 11. PBO / CSCV, adapted (advisory, as in D15)

- **Time CSCV** on the (date x pooled trial) matrix, with N about 20 columns, T = 378 and S = 16 (so L = 23 rows
  per block). This is the same code as today (`selection.pbo_cscv`) on the pooled matrix.
- **Cross-sectional CSCV (new, advisory):** split the K symbols into two halves (all C(17, 8) = 24,310 splits, or
  2,000 seeded ones). Choose the best pattern by pooled Sharpe on half A and rank it on half B. `pbo_xs` is the
  share with logit <= 0. It answers the pooled-specific question: "does the winning pattern win on the other
  symbols too?"
- Both are reported next to the gate as "advisory". Neither is a status input. Making `pbo_xs` a gate is a
  possible later decision once its noise rate is measured.

## 12. Funnel, statuses, rollout

### 12.1 Pattern-level funnel
```
patterns_tested -> pooled_min_trades -> breadth -> oos_positive -> psr -> null_bh -> dsr
  -> concentration -> cost_delay -> capped_replay -> holdout -> pooled_validated
  plus: n_trials (cumulative pooled N), sharpe_var (V used), pbo, pbo_xs, run_id, universe_hash, unit="pooled"
execution funnel: firing_tonight (symbol x pattern of pooled_validated patterns) -> intents -> risk_approved -> orders
```

### 12.2 Statuses
- `pooled_oos_validated`: every pooled gate except the DSR. Alert-only.
- `pooled_deflated_validated`: every gate. **Not** in `ORDER_ELIGIBLE_STATUSES` at first.
- The per-symbol `deflated_validated` path is unchanged and stays eligible during the transition. It is
  essentially never reached, but it stays.

### 12.3 Rollout phases
1. **Build, shadow only** (after the forward week). Pooled validation runs nightly next to the per-symbol run.
   Both funnels are stored and journaled. Every pooled pattern's firings are recorded as shadow signals
   (`kind = pooled_pattern`) and scored by the existing 1-day / 5-day shadow scoring.
2. **Owner review.** After at least 4 weeks of shadow runs with zero breaches, plus the calibration and power
   tests passing, the owner decides whether to add `pooled_deflated_validated` to `ORDER_ELIGIBLE_STATUSES`.
   That is a new decision entry, not this one.
3. **Confirmatory.** An eligible pattern stays eligible only while its forward paper returns (since 2026-10-05)
   are not significantly worse than its pooled OOS. The D6 "divergence within tolerance" check is applied per
   pattern. A few forward weeks and a 4-week shadow period have almost no statistical power, so they are not
   treated as checks on their own: the forward confirmatory test is **pre-registered with a minimum length or
   trade count and a stated power** (recorded in the decision entry before phase 2), and until that minimum is
   reached the result is "not yet informative", never "passed".

## 13. Expected effect, estimated from current data

Computed on `state/external_bars` for the 17 executable watchlist symbols, executable variant, walk-forward before
`HOLDOUT_START` only. **Trade counts and dates only: no OOS returns or Sharpe were computed for this estimate, and
nothing at or after the hold-out was evaluated.** Returns were left out on purpose, so this proposal is not itself
a selection step. Scripts were run from the scratchpad and are not committed.

| pattern | pooled OOS trades | max per symbol | symbols with trades | top-symbol share | entry-week clusters | mean / max concurrent |
|---|---|---|---|---|---|---|
| macd_crossover | 260 | 19 | 17 | 7.3% | 71 | 5.5 / 16 |
| macd_hist_reversal (identical signals) | 260 | 19 | 17 | 7.3% | 71 | 5.5 / 16 |
| keltner_reversion | 229 | 18 | 17 | 7.9% | 66 | 4.2 / 14 |
| donchian_breakout | 222 | 19 | 17 | 8.6% | 68 | 6.3 / 15 |
| inside_bar_breakout | 219 | 18 | 17 | 8.2% | 68 | 5.5 / 14 |
| stochastic_cross | 219 | 18 | 17 | 8.2% | 54 | 4.6 / 15 |
| trend_momentum_combo | 208 | 16 | 17 | 7.7% | 62 | 5.0 / 15 |
| ichimoku_cloud | 207 | 17 | 17 | 8.2% | 65 | 5.4 / 13 |
| bollinger_bounce | 155 | 14 | 17 | 9.0% | 46 | 4.0 / 13 |
| triple_ema | 145 | 11 | 17 | 7.6% | 60 | 5.2 / 12 |
| ema_crossover | 132 | 13 | 17 | 9.8% | 53 | 4.2 / 12 |
| rsi_divergence | 115 | 11 | 17 | 9.6% | 40 | 3.1 / 10 |
| atr_breakout | 102 | 9 | 17 | 8.8% | 40 | 3.5 / 11 |
| mean_rev_composite | 99 | 9 | 17 | 9.1% | 29 | 3.7 / 12 |
| bollinger_squeeze | 82 | 8 | 17 | 9.8% | 39 | 3.4 / 8 |
| sma_200_trend | 75 | 12 | 16 | 16.0% | 44 | 1.8 / 8 |
| rsi_reversal | 63 | 7 | 17 | 11.1% | 20 | 3.0 / 12 |
| adx_trend | 52 | 7 | 17 | 13.5% | 32 | 2.4 / 5 |
| volume_breakout | 36 | 5 | 14 | 13.9% | 20 | 2.0 / 7 |
| confluence | 15 | 3 | 11 | 20.0% | 12 | 1.2 / 3 |

Reading the table:
- **The trade gate stops binding.** Per symbol: 0 of 340 candidates reach 30 OOS trades (the maximum is 19).
  Pooled: 19 of 20 patterns have >= 30 raw trades, and **15 of 20** (14 distinct) pass the proposed
  "trades >= 60 and clusters >= 30" gate. `mean_rev_composite` (29 clusters), `rsi_reversal` (20 clusters),
  `adx_trend` (52 trades), `volume_breakout` and `confluence` fail.
- **Clustering is real.** 260 MACD trades fall into only 71 entry weeks, with about 5.5 positions open at once on
  average. Counting trades as independent would overstate the evidence by about 3 to 4 times. That is why the
  gates use date-level series, cluster counts and a cluster-preserving null.
- **The DSR gets easier but stays strict; T becomes the binding constraint.** For a per-day Sharpe with T = 378
  and DSR p < 0.05 (normal returns, before the T_eff penalty):

  | setting | SR0 (annualised) | required pooled Sharpe (annualised) |
  |---|---|---|
  | today: N = 780, V = 0.0027 | 2.63 | **3.97** |
  | pooled: N = 20, V = 1/T | 1.55 | **2.90** |
  | pooled, N = 40 (variants counted) | 1.79 | 3.13 |

  Pooling helps only through diversification. If a pattern has a real edge that is partly independent across
  names, the pooled series has a higher Sharpe than any single symbol (roughly growing with the square root of the
  effective breadth). It cannot make 378 days look like more than 378 days. **Expect still very few or no
  `pooled_deflated_validated` patterns on 5-year bars.** That is consistent with D11's intent; the difference is
  that the reason would now be "not enough calendar time", not "the unit is too small".
- **Levers on T (outside this proposal, each a separate decision):**
  1. *Longer history.* The research window is 8 years, but IBKR files hold 5. Fetching longer IBKR history
     (read-only `get_price_history`) would add about 3 OOS years per symbol.
  2. *Warm-up-only OOS for fixed candidates.* With one candidate there is no selection: `walk_forward` runs
     `continuous` and the 504-bar "train" window only discards data. With a 252-bar warm-up instead, OOS grows to
     5 folds = 630 bars, pooled MACD trades go from 260 to 424, and the required pooled Sharpe falls to about 2.2
     to 2.6. The caveat: the pattern defaults were chosen by people who knew market history, so "never fitted on
     this data" is a weaker claim than true walk-forward. It needs its own decision.
  3. *Fewer trials.* Retire duplicate or dead patterns before the first pooled run (for example one of the two
     MACD patterns, and `confluence`). This must be decided blind to pooled results.

## 14. Migration and backward compatibility

- **Stored results.** `results/funnel/latest.json` keeps its keys. It gains `unit: "per_symbol"`, and a missing
  `unit` means per-symbol (results stored before this change). The pooled funnel goes to a new result kind,
  `results/pooled/latest.json`: per-pattern verdicts, gates, the per-symbol contribution table, LOSO/LOCO, `pbo_xs`
  and the version hashes. It is served by `GET /api/results/pooled/latest` with the same 30 h stale flag.
- **API and UI.** `LatestTechnicalResult` gets an optional `pooled_funnel` (null when absent; the D15 rule "no
  invented stage" applies). `TechnicalCandidate` gets optional `validation_unit`, `pooled_status`,
  `pooled_version`. `openapi.json` snapshot, `scripts/export_openapi.py --check` and the frontend `schema.d.ts` are
  updated together. The React funnel shows the per-symbol funnel unchanged and a second "Pooled (pattern)" funnel
  next to it, plus a per-pattern drill-down with the symbol contribution bars.
- **Trials.** Existing per-symbol runs (`tech-*`) load unchanged. Pooled runs are `pooled-*`. The ledger is a new
  table in `trials.sqlite`, created by the registry, with no state-DB migration.
- **Hold-out store.** No schema change; a new key namespace. Old per-symbol reads remain and are still used by the
  per-symbol path.
- **Forward journal.** Day lines gain `pooled_funnel` and pooled shadow signals. `forward report` tolerates their
  absence in older lines.
- **Robustness and briefing.** No change. The briefing may receive the pooled funnel as one more input (text only,
  D17 boundaries unchanged).

## 15. Tests

Unit / contract:
1. **Pooling arithmetic.** Equal-risk weights and R conversion against hand-computed fixtures; date alignment
   across symbols with different histories (META); flat days are 0; nothing at or after `HOLDOUT_START` enters
   the OOS series (an assertion on every trade's entry index, as in the estimate script).
2. **T_eff.** Equals T on iid input. Strictly smaller on an MA(5) process. Never larger than T.
3. **Cluster-preserving null.** Members of a cluster share an offset; every draw stays in its own fold window.
4. **Concentration gates.** A planted edge in one symbol only gives `pooled_concentrated`. An edge only in
   `mega_tech` fails LOCO. A one-fold edge fails leave-one-fold-out.
5. **Capped replay.** The D5 caps applied in the pre-registered tie order give deterministic trade sets.
6. **Hold-out read-once.** A second run reuses the stored pooled read. Changing the universe, pattern source,
   gate constant or method version is a new version and a new read. A store that cannot be read or written gives
   `holdout_store_unavailable` and nothing validates.
7. **Ledger.** N accumulates across versions under one `HOLDOUT_START`. The same version on new data does not add
   to N. An unreadable ledger fails closed. Duplicate series get `duplicate_of`.
8. **Execution.** A pooled intent for a symbol outside the universe, or with a stale version, is rejected. A
   pooled status that is not in `ORDER_ELIGIBLE_STATUSES` is rejected with `not_validated`. The existing invariant
   tests (paper=True, long-only, single chokepoint, IBKR read-only AST bans) are untouched and must still pass.
9. **Back-compat.** An old funnel without `unit` renders; the openapi snapshot is updated; the per-symbol path
   produces byte-identical `results_to_json` before and after (golden).

Statistical (slow):
10. **Calibration.** Correlated pure-noise universe (17 symbols, common factor with rho = 0.6, 20 noise patterns,
    200 seeds): `pooled_deflated_validated` rate <= 5% (expected far lower), and null p-values uniform (KS). The
    same harness with independent null draws must show anti-conservative p-values (this proves the cluster design
    is needed).
11. **Power.** A planted broad, modest edge (+0.3 R per trade on 60% of symbols) is found pooled and not per
    symbol. A planted single-symbol edge is not found pooled (concentration).

**Narrowing cannot bypass the pooled gate** (the Phase 4 universe-N floor idea, moved to patterns and universe):
12. A run on any strict subset of the registered universe returns `pooled_universe_incomplete`, or reads the stored
    nightly verdict. It never produces a fresh eligible status: a property test over random subsets (hypothesis).
13. A run with a subset of patterns is deflated with N >= `pooled_universe_trials()` and uses the nightly V floor.
    With no nightly V it is `no_nightly_variance` (as D15).
14. A changed universe (a symbol added or swapped) changes `universe_hash`, so it is a new version, a new hold-out
    read and N += number of patterns, and it cannot reuse an old eligible verdict. A re-registered universe made
    from the previous universe's per-symbol OOS winners is refused or penalised in N.
14b. **Read cap ledger test.** A second pooled hold-out read of the same pattern under one `HOLDOUT_START` (a new
    version hash) is refused unless a disclosed re-read is recorded; the per-`HOLDOUT_START` read count is correct.
14c. An incomplete universe never yields an order-eligible status, whatever the stored pooled verdict; retired
    patterns stay in N.
15. **Mutation tests (the suite must catch each mutant):** each of the following is applied with `monkeypatch` in a
    meta-test that runs the narrowing tests and asserts that at least one fails.
    (a) `pooled_universe_trials` returns the run's own count;
    (b) `POOLED_MAX_MISSING = 1.0`;
    (c) `universe_hash` is left out of the version;
    (d) the null draws trades independently instead of by cluster;
    (e) T_eff is replaced by T;
    (f) the LOSO check is removed;
    (g) the execution universe check is removed.
    A mutant that survives means a test is missing, and the build fails.

## 16. Risks, and what would make us reject this

Risks:
- **Heterogeneous edges get averaged into an illusion.** Mitigation: the concentration, LOSO, LOCO and
  leave-one-fold-out gates, plus the cross-sectional PBO.
- **Dependence is under-modelled** (common shocks beyond weekly clusters, regime-long co-movement). Mitigation:
  date-level PSR/DSR with T_eff, the cluster null, and calibration test 10. Residual risk remains in the
  hold-out and in the forward check.
- **Beta disguised as timing** (long-only on a rising sample). Mitigation: the long random-entry null is a hard
  gate, and both null/BH and DSR are required.
- **Validated is not the same as executed** when the D5 caps bind (up to 16 concurrent signals against 8
  positions and 2 per cluster). Mitigation: the capped-replay gate and a pre-registered tie order.
- **Hold-out contamination from the per-symbol reads** (section 10). Mitigation: owner attestation, disclosure,
  and the forward period as the confirmatory window.
- **Complexity and new failure modes.** More gates and more stored state. Mitigation: everything fails closed,
  and the per-symbol path stays unchanged as the reference.
- **Expectation risk.** Section 13 shows the binding constraint moves to T. Pooling alone will probably still
  produce zero eligible patterns on 5-year bars. Pooling is the right unit, but it is not, by itself, a way to
  start trading.

Reject (or send back) if any of these holds:
1. Calibration test 10 shows a false-positive rate above alpha, or non-uniform null p-values, that cannot be fixed
   by the cluster design.
2. The power test 11 does not find the planted broad edge. Pooling would then add complexity without adding power.
3. Any mutant in test 15 survives and the gap cannot be closed.
4. The owner cannot attest the cleanliness of the hold-out and does not accept moving `HOLDOUT_START`.
5. In shadow, the capped replay regularly diverges from the uncapped pooled series (Sharpe ratio below 50%) for the
   patterns that pass. The thing being validated would then not be the thing being traded.
6. In shadow, the pooled shadow signals of passing patterns score clearly worse forward than their pooled OOS
   implies. That would be evidence of overfitting or leakage that the gates missed.

## 17. Out of scope

- No change to the per-symbol gates, `ORDER_ELIGIBLE_STATUSES`, the D5 caps or the engine.
- No implementation, no new config constants yet. The names above are placeholders for the build.
- The T levers in section 13 (longer history, warm-up-only OOS, pattern pruning) are each a separate decision.
- ML, pairs and the research-only asset classes stay alert-only.
