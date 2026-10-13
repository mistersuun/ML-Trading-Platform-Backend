# Robustness roadmap: ML Trading Platform

_Research run 2026-10-04. Eight Sonnet researchers covered the areas; one Opus agent wrote the plan and a second Opus agent reviewed it adversarially. Nothing in the repos has been changed yet._

> **Coverage note:** the first backend-engineering research run returned nothing. It was re-run separately and is in **Appendix B**, along with the places where it disagrees with the plan.

## 1. Reviewer-adjusted recommendation

The diagnosis holds up. Nearly every code citation I checked is correct, give or take a line or two: main.py:123 confidence=win_rate*profit_factor, paper_trader.py:185 max(1, ...) with no ceiling, backtester.py:247/296-297/310-311 and the walk-forward that discards its training slice, the ML P(up) passed for SELL orders, the NaN round-trip in routes/helpers.py, and pattern_routes.py:124 round(fraction, 2). The library and SDK versions are also real on PyPI and npm today: anthropic 1.11.0 with messages.parse(output_format=...), sklearn 1.9.1, statsmodels 0.15.0, alpaca-py 0.44.0, xgboost 3.4.1 needing Python >=3.12, vectorbt 1.1.1 needing pandas>=3, ta 0.11.0 unchanged, and vitest/msw needing Node 22.12.

The sequencing and the test design are where it is weak:
- **Bug-pin tests can pass for the wrong reason.** Many are written against interfaces that do not exist yet (entry_index, Bars/meta, compute_spread_stats). Without `raises=`, a strict xfail counts an AttributeError as the expected failure.
- **Real-data results change with no frozen baseline.** Data correctness (WS2.1) and the engine rewrite (WS2.2) change results at the same time, and no real-data snapshot or before/after metrics diff exists until the Phase 3 bar store. Changes to real-data results would be silent and impossible to attribute.
- **Paper orders pass ~780 hypotheses with no multiple-testing correction.** From Phase 2 to Phase 4, paper orders are gated on oos_validated with no correction across the ~780 hypotheses per scan.
- **The "locked" hold-out is not locked.** In a daily re-scan it is re-read every run.
- **One pairs acceptance test is self-contradictory.** PR-1 claims returns do not change under a +1000 price shift.
- **It adds live trading.** The current code hard-codes paper=True, so WS1.6's live mode is new risk, not robustness.
- **The executive summary overstates the urgency.** PAPER_TRADE_ENABLED already defaults to false and --paper is opt-in.
- **The vectorbt rationale cites dead code.** backtest_pattern is never called.

It is also far too large for one owner: 38 workstreams, with DSR, PBO, SPA, StepM and BH all at once, a jobs framework, OpenAPI codegen, circuit breakers, an LLM orchestrator and judge evals, Kalman filters and triple-barrier labels. It is also missing survivorship bias in the hand-picked watchlist, a heartbeat or dead-man alert, fail-closed handling when state is missing or a backup is needed, and monitoring of paper-vs-backtest divergence.

Recommendation: keep Phases 0–2 with the fixes below, fold a cheap multiple-testing correction into Phase 2, drop live mode, and cut Phases 3–5 by about half.

### Amendments the reviewer requires

- **[high] Strict-xfail bug tests can pass for the wrong reason and do not truly pin current behaviour**
  - Evidence: WS0.3 asserts things like bars_held == exit_index - entry_index (BT-4), Bars meta (DATA-*), compute_spread_stats truncation (PR-2) and len(equity)==len(df). The Trade dataclass in backtester.py has no entry_index or exit_index, and data_fetcher.fetch_ohlcv returns a bare DataFrame. pytest.mark.xfail without raises= treats an AttributeError or ImportError as the expected failure. So the test 'xfails' because the API is missing, not because of the bug, and it gets rewritten when the API lands. That defeats pinning.
  - Fix: Require `raises=AssertionError` on every bug xfail. Write each pin only against today's public interfaces (classic_backtest, execute_signal, json_response, analyze_pair). Where the fix changes the interface, write a characterization test of the current wrong value (e.g. assert bars_held == i - prev.bars_held today) plus a separate new-interface test added with the fix.
- **[high] Results change silently because data and engine changes land together with no frozen real-data baseline**
  - Evidence: WS2.1 switches source and adjustment (drops Alpha Vantage, moves to Alpaca adjustment=all and a feed choice, drops the in-progress bar, normalises the index). WS2.2 rewrites timing, fills and equity. Both run in Phase 2 in parallel. The only frozen data in Phase 0 is synthetic (WS0.2), and the parquet bar store does not arrive until WS3.3. Nobody can tell which change moved which Sharpe, or catch an unintended shift.
  - Fix: In Phase 0, snapshot real bars for the whole watchlist to committed or archived parquet with a hash. Add a `make metrics-report` that writes a per-(symbol, pattern) metrics table. Every Phase 2 PR attaches a before/after diff on the frozen snapshot. Data changes are evaluated with the old engine, and engine changes with the old data.
- **[high] Paper orders become eligible in Phases 2-3 with no multiple-testing control**
  - Evidence: The key decisions say orders require oos_validated from Phase 2. DSR, SPA and BH only arrive in Phase 4, after the Phase 3 service-layer refactor. With ~780 (symbol, pattern) hypotheses per scan, per-hypothesis gates at about 5% will still let dozens of false positives through over repeated daily scans.
  - Fix: Put a Benjamini-Hochberg or Holm correction across the random-entry-null p-values of all candidates in a run into WS2.3. That is a few lines and needs no trial registry. Or keep orders disabled until Phase 4.
- **[high] 'Locked hold-out' is incoherent for a system that re-scans daily**
  - Evidence: WS2.3: 'locked hold-out is the last 20% of history and is never used for selection'. The gate oos_validated still requires 'hold-out is not negative', evaluated in every daily scan. The hold-out window rolls forward and is consulted on every run, so it acts as a selection set. With LOOKBACK_DAYS=730 (config.py), the hold-out is about 100 bars, and >=30 OOS trades is mostly infeasible for daily patterns.
  - Fix: Fix a calendar cut-off date for the hold-out and record it in the ADR. Report hold-out results but use them only once per strategy version. Raise the history length (e.g. 8-10y where the source allows it). State the expected number of OOS trades per pattern before choosing MIN_TRADES=30.
- **[high] Plan introduces a live-trading path, which is new risk rather than robustness**
  - Evidence: paper_trader.py:28-32 hard-codes TradingClient(..., paper=True), and ALPACA_BASE_URL is unused. No live path exists today. WS1.6 adds TRADING_MODE=live, live keys and LIVE_TRADING_CONFIRM in Phase 1, before any result is validated.
  - Fix: Remove live mode from this roadmap. Keep the paper=True hard-code and add a test asserting it. Make live a separate, later decision gated on months of paper-vs-backtest agreement.
- **[medium] PR-1 acceptance test is self-contradictory**
  - Evidence: WS2.6 acceptance: 'return 5% on gross, unchanged when both prices are shifted by +1000'. A correct return-on-gross engine is invariant to multiplying prices, not to adding a constant. A from 1100 to 1110 is a 0.9% move, so the correct implementation fails this test. P&L in dollars at fixed share counts is additive-shift invariant, but returns on gross are not.
  - Fix: Split it in two. (a) Multiplying both price series by k leaves returns unchanged. (b) Dollar P&L at fixed share quantities is unchanged by an additive shift. Also assert P&L is never divided by the spread level (pairs_trading.py:271).
- **[medium] Emergency interlock WS0.4 still allows oversell and duplicate buys**
  - Evidence: WS0.4 rejects -1 only when no long position is held. If a long exists, it sells the size computed from the notional cap, not min(qty, held). That can flip the position short. Duplicate buys across runs and patterns stay open until WS1.3, since main.py calls execute_signal once per triggered pattern (main.py:122-124). Positions opened in Phase 0 have no protective stop (market orders only), while the backtest assumes a 2% stop.
  - Fix: In WS0.4: cap the sell qty at the held qty, skip a BUY when a long or an open buy order already exists for the symbol (one get_positions/get_orders call), and fail closed if those calls error. get_positions currently returns [] on error (paper_trader.py:141-143).
- **[medium] Dedup key and aggregation disagree**
  - Evidence: WS1.3 builds the ledger key as sha1(strategy|symbol|side|bar_date). The acceptance and ORD-2 require one order per (symbol, bar) across patterns and modes. scan_technical and scan_ml are separate loops (main.py:277-283), so a technical and an ML BUY on the same bar get different keys. Also, the ledger row is inserted before submission, so a cleanly rejected order permanently blocks a retry for that bar unless the status is handled.
  - Fix: Key on symbol|side|bar_date, aggregated across all modes before calling the chokepoint. Store status and allow re-submission only from 'rejected_retryable'.
- **[medium] Live bracket prices are not referenced to the same price as the backtest**
  - Evidence: WS2.2 sets stops relative to the next-open entry fill. WS1.6 computes bracket stop and TP from the price at signal time (last close or quote) and submits a market DAY order that fills at the next open after any gap. Live risk and the backtest then diverge on every gapped entry.
  - Fix: Use OTO or a post-fill stop placement relative to the actual fill price. Or make the engine model stops relative to the signal close. Then add a test of whichever convention is chosen.
- **[medium] Several acceptance tests do not prove the property they claim**
  - Evidence: (1) WS0.1: 're-locking gives identical top-level versions' depends on PyPI release timing, not correctness. `uv run pip check` fails because uv venvs have no pip; it should be `uv pip check`. (2) The golden pattern test pins signal counts, not positions, so an off-by-one shift keeps counts identical. (3) BT-5 'random-walk Sharpe about 0' cannot detect same-bar-close entry bias, which has no effect on a martingale. (4) WS3.1 'grep for classic_backtest in dashboard.py' is easy to game. (5) WS3.6 exempts /health, but the route is /api/health (server.py:71).
  - Fix: Pin a hash of the per-bar signal Series, not counts. Replace BT-5 with an explicit lookahead canary: a signal built from close[t+1] must not be profitable under next_open timing, and must be under 'close' with a peeking signal. Use an import-graph test for dashboard.py. Correct the path to /api/health. Drop the re-lock test.
- **[low] WS1.3 depends on WS1.5 but the dependency is not declared**
  - Evidence: WS1.3 check 4 is 'validation_status eligibility', and main.py aggregates by 'highest validation tier'. validation_status is introduced in WS1.5, which in turn 'merges into WS1.3's main.py edits'.
  - Fix: Declare WS1.5 before WS1.3, or merge them into one workstream for one worker.
- **[low] Pairs weights described as dollar-neutral are beta-weighted**
  - Evidence: WS2.6: 'Two-leg dollar-neutral daily engine' with w_a=1/(1+|b|) and w_b=|b|/(1+|b|). These are dollar-neutral only when |b|=1.
  - Fix: Choose and document one convention (beta-neutral on log prices, or dollar-neutral) and test the stated property.
- **[low] Python 3.12 target cannot be verified where the workers run**
  - Evidence: The sandbox has Python 3.11.15. xgboost is the only dependency requiring >=3.12, and the plan drops it. sklearn 1.9.1 and pandas 2.3 support 3.11.
  - Fix: Target 3.11 (or a 3.11/3.12 CI matrix), or have WS0.1 install 3.12 via `uv python install` and verify that works through the proxy.
- **[low] LLM refusal fallback is not available on the endpoint the plan names**
  - Evidence: In anthropic 1.11.0, `fallbacks` exists only on client.beta.messages.create/parse (resources/beta/messages/messages.py:1251), not on client.messages.parse, which is what WS1.8 specifies.
  - Fix: Use client.beta.messages.parse with the beta, or drop the fallback in WS1.8. Record the actual served model in llm_calls for cost tracking.

### Missing from the plan

- Survivorship and selection bias: the watchlist is hand-picked surviving winners (NVDA, META, TSLA, SOL, AVAX, DOGE in config.py). No validation step accounts for it, and DSR does not fix it. At minimum, document it and add a few delisted or laggard symbols or a broad index control.
- Fail-closed handling when state is missing: if state/trading.db is deleted or corrupted, the halt flag and ledger vanish and trading resumes. Require `risk init`, and refuse paper mode when the DB or migration version is missing. Back up the SQLite file daily (sqlite3 .backup) and the parquet/model directories.
- Heartbeat or dead-man's-switch alert when a scheduled run does not complete or the scheduler dies. The runs table exists, but nothing alerts on absence.
- Paper-vs-backtest divergence monitoring: compare realised paper fills, slippage and trade outcomes with what the engine predicted for the same bars. This is the main live robustness signal and is absent.
- Signal-driven exits for live positions: the backtest exits on an opposite signal, but live only has brackets. Nothing manages time stops or signal exits for open paper positions, so live behaviour is not the backtested strategy.
- Alpaca feed choice and its effect on volume: free accounts default to IEX, which has partial volume and IEX-only OHLC. Volume patterns, volume regimes and ML volume features change with the feed. Specify feed=sip with the 15-minute delay clamp, and say so in the source pin.
- Scheduler timezone and DST: `schedule` runs in local time. Define the run time relative to the exchange close plus the data delay. Define crypto's 'completed session' boundary (UTC vs Alpaca's daily bar boundary). routes/pattern_routes uses tz-naive pd.Timestamp.now() for days_ago.
- Corporate actions on open broker orders: what happens to bracket stop and limit legs across a split or dividend, and whether Alpaca adjusts or cancels them. Reconciliation should detect it.
- Data licensing beyond yfinance: Alpaca market-data terms on redistribution if the frontend is ever exposed. Currently only raised as an owner question.
- Secrets: rotation and a one-time history scan. The git history has 1 commit and .env is ignored, but the plan never says to verify that or run secret scanning in CI.
- A total effort and calendar estimate and a stop/kill criterion per phase for a single owner.

### Probably overkill for a single owner

- Phase 4 runs DSR, PBO via CSCV, SPA, StepM and BH together. For one owner, pick one significance gate (DSR or SPA) plus BH, and keep PBO as advisory.
- WS3.4 background jobs: SQLite jobs table, ThreadPoolExecutor, orphan recovery, cooperative cancellation, and a useJob hook with URL-resumed polling. A CLI-generated nightly result file served read-only by the API would remove the 300s requests at a fraction of the cost.
- WS3.3 HTTP layer: token bucket, persisted daily quota and circuit breaker for 39 daily symbols. tenacity retries plus the parquet cache are enough.
- WS3.2 OpenAPI codegen with a committed openapi.json, snapshot test and CI drift gate across two repos, in addition to WS2.7's hand fixes. Do one or the other, not both phases.
- WS5.1/WS5.5: an LLM eval harness with an LLM judge, budgets, an llm_calls table, an Opus orchestrator with Sonnet workers, and a Briefing schema with evidence_refs, all for an advisory text summary.
- WS5.4 Kalman hedge mode, triple-barrier labels, uniqueness weights and meta-labelling before a single strategy has shown OOS edge.
- WS5.2 partial Plotly bundles plus Playwright smoke tests, and WS5.5 the Databento futures comparison, when futures are research-only anyway.
- WS1.6 separate live keys, live confirmation and live mode (see problems). Drop them entirely.
- The backtesting.py cross-check requiring the same trade count and return within 1%. Its sizing, whole-share units, SL/TP tie rules and commission model differ, so matching it will consume time. Limit it to signal-exit-only strategies or drop it in favour of closed-form fixtures.

### Claims and sources to treat with caution

- 'vectorbt overlay overwrites Sortino and Calmar (backtester.py:330-345)': backtest_pattern (backtester.py:340) is never called anywhere. grep shows only classic_backtest is used by main.py and the routes, so the overlay affects no reported number. The rationale for removing vectorbt should rest on the pandas>=3 requirement and its lack of use, not on corrupted metrics.
- 'Nothing stops an unbounded or duplicated paper order': overstated. PAPER_TRADE_ENABLED defaults to false (config.py:166), is checked in both execute_signal and submit_order, and main.py needs --paper. It is accurate only once the owner opts in. Phase 0's 'TRADING_MODE defaults to off' therefore duplicates an existing control.
- 'Dashboard shows 0.0% for small returns': partly right. Dashboard.tsx:103 prints the rounded fraction with toFixed(1)%, so values are 100x too small (0.153 rounds to 0.15 and shows as '0.1%' or '0.2%'), not only small returns.
- Line citations are off by a line here and there (execute_signal at main.py:227, not 228; the overlay starts at 340). Harmless, but the 'confirmed' list should match the code.
- The claude-sonnet-4-20250514 retirement date conflicts between tracks (2026-06-15 vs TBD). The plan correctly defers it to a runtime check rather than asserting either.
- Low-quality or marketing sources cited as research: luxalgo.com library pages, qveris.ai, apicostcalc.com, and fynance docs. Several Alpaca and Telegram facts were 'seen via search snippets only' or 'not fetched', including the bracket and crypto order rules the order path depends on. Verify those against the official Alpaca docs before WS1.6.
- 'Is the repo on GitHub?' is asked of the owner, but both repos already have GitHub origins (github.com/mistersuun/ML-Trading-Platform-Backend and -Frontend), so CI via GitHub Actions can be assumed.
- Verified correct (no action needed): anthropic 1.11.0 messages.parse(output_format=...) and output_config.effort in {low, medium, high, xhigh, max}; scikit-learn 1.9.1; statsmodels 0.15.0; alpaca-py 0.44.0; xgboost 3.4.1 requires Python >=3.12; vectorbt 1.1.1 requires pandas>=3.0.3; pandas latest 2.3.x is 2.3.3; ta 0.11.0; exchange-calendars 4.13.2; yfinance 1.7.0; npm openapi-typescript 7.13.0, openapi-fetch 0.17.0, @tanstack/react-query 5.104.1, vitest 5.0.3 (Node ^22.12), msw 3.0.2, plotly.js 4.1.1.

## 2. Plan (architect draft)

This plan makes the trading platform correct and safe first, then builds the structure underneath it, then adds features. It has six phases, and each phase can ship on its own. Every workstream has an ID, its dependencies, the files it touches and a concrete acceptance test, so parallel workers can pick them up.

Two things in the code are most urgent.

**1. Nothing stops an unbounded or duplicated paper order.** I confirmed this in the code:
- `confidence = win_rate*profit_factor` (main.py:123). Profit factor is about 1e10 when a backtest has no losing trades, because `gross_loss = 1e-10` (backtester.py:296-297).
- The size has a floor of `max(1, ...)` and no ceiling (paper_trader.py:185).
- The ML path passes P(up) as the confidence even for SELL signals (main.py:228 calls `execute_signal(..., recent_conf, ...)`, where `ml_confidence = prob_up`, ml_patterns.py:208-214).
- `check_recent_signal` returns BUY if any buy appears in the window (main.py:55-63).
- `RiskManager` is created (main.py:52) and never called.

**2. Almost every reported number is biased upwards or simply wrong.** I confirmed:
- Trades enter at the close of the same bar that produced the signal.
- The equity curve is a step function that only moves when a trade exits.
- Sharpe subtracts the full risk-free rate from returns that use only 5% of capital (backtester.py:310-311).
- `bars_held` is computed as `i - trades[-1].bars_held` (backtester.py:247).
- `walk_forward_validate` fits nothing and throws away its training slice (backtester.py:348-384).
- ML results are in-sample, pairs P&L is divided by the spread level, and regime stress tests stitch non-contiguous bars together.

There is also a tests-first rule. There are no tests in either repo today. So Phase 0 locks the environment and writes one bug-pinning test for every known issue, marked `xfail(strict=True)`. Each later workstream is "done" only when its pinned tests flip to passing.

The phases:
- **Phase 0, Freeze and pin.** Lock the environment, build the test harness, write the bug-pinning tests, and add emergency order interlocks. Trading mode defaults to off.
- **Phase 1, Safe orders.** One execution chokepoint, a persisted risk state and signal ledger in SQLite, a symbol registry, risk-based sizing, idempotent order IDs, protective brackets at the broker, safe alerts, and repair of the broken Claude call.
- **Phase 2, Correct results.** Data correctness, a next-bar mark-to-market engine with correct metrics, a real walk-forward with a locked hold-out, ML without leakage, a two-leg pairs engine, causal stress tests, and a fix for the API's handling of NaN and Infinity, its unit mismatches and its error statuses.
- **Phase 3, Foundation.** One service layer used by the CLI, the scheduler and the API; typed Pydantic contracts with generated frontend types; a parquet bar store with retries and rate limits; SQLite-backed background jobs; versioned model files; and validated config.
- **Phase 4, Honest selection at scale.** A registry of every trial, Deflated Sharpe and Probability of Backtest Overfitting (DSR/PBO), the SPA (Superior Predictive Ability) data-snooping test, ML calibration and drift checks, and pairs walk-forward. These become the hard gate for any order.
- **Phase 5, Parity and features.** A typed LLM briefing with a budget and evals, a frontend analysis page, smaller chart bundles, retirement of the Streamlit dashboard, a Kalman hedge ratio option, triple-barrier labels and cost realism.

I chose one tool for each need, kept proportionate for one owner:
- Environment: uv lock, Python 3.12, pandas 2.3.x. vectorbt, pandas-ta and xgboost are dropped from the runtime.
- Tests: pytest, hypothesis and `responses`, plus an in-house FakeBroker.
- Persistent state: one SQLite file. Bars: parquet. Models: joblib with a JSON metadata file.
- Statistics: in-house DSR/PSR/PBO and a purged splitter, plus the `arch` package (already a dependency).
- Market data: Alpaca for US stocks and crypto, yfinance as a labelled best-effort source for FX and futures. Alpha Vantage is removed. Calendars: exchange_calendars. Retries: tenacity.
- Config: pydantic-settings.
- Frontend: openapi-typescript, openapi-fetch, TanStack Query, Vitest with React Testing Library and MSW, and a minimal Playwright smoke test. No zod.
- LLM: the official `anthropic` SDK with Pydantic structured output on claude-opus-5-5. It is advisory only and can never touch orders.

Heavy frameworks were evaluated and not chosen: nautilus_trader, zipline, MLflow, Celery/Redis, DuckDB, pandera, skfolio, LangChain and similar agent frameworks.

The research tracks disagreed in a few places. I checked the code where they did; the results are in the key decisions. The "backtest-engineering" track returned only a placeholder, so I covered engine engineering from the backtest-validity track and my own read of the code.

### Target architecture

END STATE (backend: ML-Trading-Platform-Backend; frontend: ML-Trading-Platform-Frontend)

1. INSTRUMENTS (`instruments.py`)
- One registry maps each canonical symbol ('AAPL', 'BRK-B', 'BTC-USD', 'EURUSD=X', 'GC=F') to:
  - asset_class (equity, crypto, fx, futures)
  - exchange calendar (XNYS, 24/7, Sunday-Friday FX, CMES)
  - bars_per_year (252 or 365)
  - per-provider native symbols (Alpaca data 'BTC/USD', Alpaca trading 'BRK.B', yfinance as given, or None if unsupported)
  - whether it is executable on Alpaca, shortable, and fractionable
- Data code, order code, pairs and features all consult it. An unknown symbol raises an error.

2. DATA LAYER (`data/`: adapters, validator, store)
- Adapters:
  - Alpaca for US equities and crypto: explicit `feed`, end time clamped to now minus 16 minutes, adjustment=all.
  - yfinance as a fallback and for FX/futures: auto_adjust=True explicitly. Futures are tagged 'continuous_unadjusted'.
  - Alpha Vantage is removed.
- Every adapter returns `Bars(df, meta)`. meta holds source, adjusted flag, calendar, fetched_at, requested and actual date range, and a quality report.
- Daily index: normalised to the exchange session date, unique and monotonic. The in-progress bar is dropped.
- A plain-Python OHLCV validator runs at every boundary and quarantines failures.
- Storage: one parquet file per (symbol, timeframe, source, adjustment) under `data/bars/`.
  - Written atomically (temp file then rename).
  - Updated incrementally by re-fetching an overlap of about 5 sessions; a full re-fetch runs if a corporate action is detected.
  - The source is pinned per symbol in SQLite, so every view of a symbol reads the same frame.
- One shared HTTP layer per provider: requests.Session, tenacity retries (429/5xx/timeouts, honouring Retry-After), a token bucket, a persisted daily quota and a circuit breaker. Failures are logged at WARNING.

3. DOMAIN (pure functions, no I/O)
- `patterns.py`: signals use only trailing data, with boolean idioms that are safe under pandas 3.
- `engine.py`: the single backtest engine.
  - Timing: a signal at bar t is executed at the open of bar t+1.
  - Fills: stop and target fills are gap-aware; costs apply to every fill, stops included.
  - Equity: marked to market every bar at the actual position notional. Trades carry entry_index and exit_index.
- `metrics.py`: shared by every engine.
  - Sharpe is computed on daily mark-to-market returns with rf=0, documented.
  - Also: Sortino with true downside deviation, CAGR-based Calmar, max drawdown and its duration, PSR, and a capped profit factor (None when there are no losses).
  - Output is finite floats or None.
- `validation.py`:
  - Walk-forward with warm-up and selection only on the training window, plus a locked hold-out of the last 20% of history.
  - Random-entry null test, cost stress (2x/3x) and delay stress.
  - DSR, PBO via CSCV, SPA (via `arch`) and Benjamini-Hochberg (BH) correction.
- `pairs.py`:
  - Spread on log prices with an intercept, from one causal `compute_spread_stats`.
  - Two-leg dollar-neutral daily engine with next-bar execution.
  - Engle-Granger tested in both orderings with BH correction; half-life via ln(phi), None when not mean-reverting.
  - Stops: frozen-z at entry, a time stop and a cointegration-breakdown exit.
- `ml/`:
  - make_labels (unresolved rows are NaN and dropped); stationary features with a fixed schema.
  - sklearn Pipeline with a purged and embargoed time-series splitter; out-of-sample walk-forward predictions only.
  - Calibrated probabilities (Phase 4); a baseline-first model choice between LightGBM, RF and logistic regression.
  - Saved as joblib plus metadata.json, with version and schema checks on load.
- `stress.py`: regimes defined causally, P&L attributed to regimes (never filtered and re-run), seeded block-bootstrap Monte Carlo at real position size.

4. SERVICES (`services/`): scan, backtest, pairs, ml, stress, briefing, data_status
- The only place that orchestrates the domain code.
- Inputs and outputs are Pydantic models (a FiniteFloat type rejects NaN/inf; units are fractions).
- Every evaluation is recorded in the trial registry: daily return series to parquet, metadata row to SQLite.
- Each scan returns candidate signals with validation_status in {unvalidated, oos_validated, deflated_validated}.

5. ADAPTERS (thin)
- FastAPI routes:
  - response_model on every route; one error envelope `{error:{code,message,details}}` with real HTTP status codes.
  - Heavy work goes through jobs: `POST` returns job_id; `GET /api/jobs/{id}`.
  - CORS origins and an optional bearer token come from env; the server binds to 127.0.0.1 by default.
- CLI (`main.py`): typer or argparse subcommands: scan, risk status/halt/resume/reconcile, data-status, check-llm.
- Scheduler: one process holding a file lock, calling the same services. Each run is logged in the runs table.
- Streamlit `dashboard.py`: frozen in Phase 0 and retired in Phase 5 (or reduced to a read-only client of the services).

6. EXECUTION (`execution.py`): the only module that can reach the broker
- A Broker protocol with AlpacaBroker and FakeBroker.
- `submit_intent(intent) -> Decision(status, reasons, order_id)`. It runs these checks in order and fails closed on any error:
  1. kill switch or halt
  2. mode and key sanity (TRADING_MODE off/paper/live, separate keys, live confirmation)
  3. registry tradability and asset flags
  4. reconciliation against the broker
  5. dedup through the SQLite signal ledger (UNIQUE key) and a deterministic client_order_id
  6. sizing: min(risk budget / stop distance, volatility-target notional, per-symbol cap, per-order cap, remaining gross exposure); confidence may only scale down
  7. shorts allowed only if enabled and eligible
  8. TIF chosen per asset class
- Then submit: a bracket order for equities; crypto gets no bracket and uses a stop_limit or a locally managed exit.
- `RiskManager` keeps its state in SQLite: peak equity and day-start equity taken from broker equity, drawdown and daily-loss halts, and a resume that requires confirmation.
- Order eligibility: an intent is accepted only from signals at oos_validated or better (deflated_validated from Phase 4 on). Pairs stay alert-only until two-leg execution is built.

7. ALERTS (`alerts.py`)
- Sent after each Decision and including the actual outcome.
- Telegram uses HTML parse mode with escaping, truncation to 4096 characters, retries on 429/5xx, and a plain-text fallback.
- A redaction filter covers every handler. Alerts are deduplicated per run.

8. LLM (`claude_integration.py`): advisory only
- Official anthropic SDK, model id from env (default claude-opus-5-5), messages.parse returning a Pydantic Briefing, typed error handling, a cost budget and an llm_calls table.
- It must never import execution. At most it supplies a reduce-only exposure multiplier in [0,1], behind a flag that is off by default.

9. STATE
- SQLite in WAL mode at `state/trading.db`, with versioned SQL migrations. Tables: risk_state, signal_ledger, orders, alerts_sent, runs, jobs, bar_sources, trials, llm_calls.
- Parquet under `data/bars` and `data/trials`. Model files under `models/<symbol>/<ts>/`.

10. FRONTEND
- `src/api/schema.d.ts` is generated by openapi-typescript from the committed openapi.json. A drift check runs in CI.
- An openapi-fetch client reads `VITE_API_BASE_URL` (default /api). Middleware turns non-2xx responses and old `{error}` bodies into a thrown ApiError.
- TanStack Query: queries for reads, mutations for actions, and a useJob polling hook.
- An ErrorBoundary at the app shell and on each route. Every page has explicit loading, error-with-retry and empty states.
- `src/lib/format.ts` is the only percent and number formatter; it renders null/NaN/Infinity as 'n/a'.
- `no-explicit-any` is enforced as an error. Plotly is lazy-loaded as a partial bundle.
- Tests: Vitest with React Testing Library and MSW, plus a Playwright smoke test of six routes.

11. CI (GitHub Actions, or `make check` if the repos are not on GitHub)
- Backend: `uv sync --frozen`, ruff, a fast pytest suite under 60s (network blocked), and a nightly `-m slow` statistical suite.
- Frontend: `npm ci`, api:check, lint, `tsc -b`, vitest, build, Playwright smoke.

SIGNAL-TO-ORDER FLOW
1. The scheduler takes its lock and the bar store refreshes.
2. The services run patterns through the engine and validation and record the trials.
3. Candidates are reduced to one per (symbol, signal_bar), keeping only validated signals whose signal bar is the latest completed session.
4. The intent goes to the chokepoint.
5. The chokepoint submits a bracket order with a deterministic client_order_id, or records a structured rejection.
6. The ledger is updated, an alert is sent with the outcome, and the next run reconciles against the broker.

### Key decisions

#### Python and pandas version, and which heavy libraries stay in the runtime
- **Options:** (a) Pin pandas 3.x so vectorbt 1.1.1 can be used; (b) pin pandas 2.3.x and drop vectorbt; (c) leave versions open.
- **Recommendation:** (b). Use Python 3.12 with a uv lock (pyproject plus uv.lock), pandas~=2.3, numpy 2.x, scikit-learn~=1.9, lightgbm 4.x, statsmodels~=0.15 and alpaca-py~=0.44. Remove vectorbt, pandas-ta (it is imported nowhere) and xgboost from the runtime. backtesting.py goes into the dev group as a cross-check engine only.
- **Rationale:** I checked the code. vectorbt is used only as an overlay that overwrites Sortino and Calmar with numbers from a different accounting convention (backtester.py:330-345). pandas-ta has no imports. xgboost 3.4 requires Python 3.12 or later and adds a third ensemble member with no evidence that it helps. The `ta` package has had no release since 2023 and is untested on pandas 3, and patterns.py:40-41,142-143 rely on fillna downcasting behaviour that pandas 3 changes. The sandbox runs Python 3.11, but 3.12 satisfies every chosen dependency. The tracks disagreed: backtest-validity was open to pinning pandas 3, while ML, data, risk and pairs all said <3. Pinning <3 wins until a golden test shows identical signal counts under 3.x. The ADR records when to revisit.

#### Backtest engine
- **Options:** Harden the in-house classic_backtest; adopt vectorbt 1.x; adopt backtesting.py; adopt nautilus_trader or zipline-reloaded.
- **Recommendation:** Harden the in-house engine as the single source of truth. Use backtesting.py only in a dev cross-check test. Do not adopt nautilus_trader or zipline.
- **Rationale:** The engine keeps the existing contract (a pattern returns a DataFrame with a signal column) and gives full control over the execution-timing contract. It needs only numpy and pandas. A cross-check against a second engine catches accounting bugs, and backtesting.py's AGPL licence does not matter for a dev-only test. The other frameworks are too heavy for daily bars on 39 symbols.

#### Sharpe and risk-free rate convention
- **Options:** Subtract the full rf from portfolio returns (current, wrong because only 5% of capital is deployed); credit idle cash with rf and subtract rf; use rf=0 on mark-to-market equity returns and document it.
- **Recommendation:** rf=0 on daily mark-to-market equity returns, stated in every report. Also report PSR. With rf=0, Sharpe does not change with position size.
- **Rationale:** This is the simplest correct convention that needs no cash-interest model. It also fixes the current behaviour where is_valid almost never passes.

#### Where validation gates sit: order eligibility versus display
- **Options:** DSR and PBO as a hard gate everywhere; advisory everywhere; hard gate for orders and advisory for display.
- **Recommendation:** Make it a hard gate for orders and advisory for display. In Phase 1 to 3, orders require validation_status='oos_validated': the signal passes walk-forward out-of-sample and the locked hold-out, has at least 30 trades, has PSR>0.95 and survives 2x costs. From Phase 4, orders require 'deflated_validated': DSR p<0.05 or an SPA p-value below 0.05 across all trials in the run. Display surfaces show every metric along with its validation status.
- **Rationale:** Selection bias currently feeds order sizing directly. With roughly 780 hypotheses per scan, the honest expectation is that few or no signals will be eligible for orders, and the owner needs to accept that (see the questions). A hard gate only on display would hide useful research.

#### Persistence for operational state
- **Options:** JSON files; SQLite; Postgres or Timescale; DuckDB; MLflow.
- **Recommendation:** One SQLite file in WAL mode (state/trading.db) for risk state, the signal ledger, orders, alerts, runs, jobs, bar sources, the trial index and LLM calls. Use parquet for bar data and trial return series, and joblib plus metadata.json for models. Use no server database, no DuckDB and no MLflow.
- **Rationale:** The tracks proposed SQLite (risk), parquet (data), JSONL (LLM) and CSV (ML). I merged them into one transactional store plus columnar files. A UNIQUE constraint on signal_key makes dedup safe under concurrent runs, and SQLite comes with Python. The JSONL and CSV run logs become SQLite tables.

#### Background jobs and scheduling
- **Options:** Celery or RQ with Redis; APScheduler; an in-process ThreadPoolExecutor with a SQLite jobs table plus a single scheduler process holding a file lock; external cron.
- **Recommendation:** An in-process executor inside the API (max_workers=2) with a SQLite jobs table. On startup, any job still marked running from a previous process is marked failed. Keep the `schedule` library in one `main.py schedule` process that takes a file lock (fcntl), so two runs can never overlap. External cron is an equal alternative if the owner prefers it.
- **Rationale:** This removes the 300-second synchronous requests (all routes are sync `def`, so the real risk is threadpool exhaustion and hanging requests, not a blocked event loop). It needs no new infrastructure, and a cancel flag lets jobs stop cooperatively.

#### Market data provider for each asset class
- **Options:** Alpaca, Tiingo, Alpha Vantage, Massive (formerly Polygon), Databento, yfinance.
- **Recommendation:** US equities and crypto: Alpaca as primary, yfinance as a flagged fallback that is pinned per symbol and never switched silently. FX and futures: yfinance only, labelled research-only, with futures tagged continuous and unadjusted, and never executable. Remove the Alpha Vantage adapter entirely. Tiingo and Databento are optional extras in Phase 5.
- **Rationale:** The data and risk tracks disagreed: data proposed Tiingo or Alpaca as primary, risk proposed Alpaca. Alpaca is already keyed, is the same vendor as the broker so symbols line up, and handles adjustment. Alpha Vantage's free quota (25 calls a day) cannot cover a 42-symbol watchlist. Its code mixes adjusted Close with raw open/high/low (data_fetcher.py:199-200) and maps '=F' futures to equity tickers, so removing it is less work than fixing it.

#### Data validation tool
- **Options:** pandera; plain pandas assertions.
- **Recommendation:** A plain `data/validate.py` of about 60 lines that returns a QualityReport.
- **Rationale:** The checks are fixed OHLC invariants. Plain code avoids pandera's API churn and an extra dependency, which is proportionate for one owner.

#### Typed API contract and frontend runtime validation
- **Options:** openapi-typescript with openapi-fetch; @hey-api/openapi-ts; orval; add zod on top.
- **Recommendation:** Pydantic response_model on the backend, using a FiniteFloat type and fractions for every return and drawdown. On the frontend, openapi-typescript plus openapi-fetch plus TanStack Query. No zod: runtime guarantees come from Pydantic at the source, and format.ts handles null.
- **Rationale:** Generating types from one contract removes the class of bug that caused the Dashboard's 100x error. I checked it: pattern_routes.py:124 sends `round(total_return_pct, 2)` as a fraction, so the value also loses precision before Dashboard.tsx:103 prints it as a percent. The same class caused PairsTrading reading total_return_pct when the backend sends total_return. Adding zod would duplicate the contract.

#### Single service layer and the future of the Streamlit dashboard
- **Options:** Keep the logic duplicated across dashboard.py (993 lines), main.py and the routes; build services/ that the CLI, scheduler and API all call; also retire Streamlit.
- **Recommendation:** Build services/ in Phase 3. Freeze dashboard.py in Phase 0 (no new features, and a banner saying it shows legacy metrics). Retire it in Phase 5 once the React app covers the same features, unless the owner wants to keep it as a thin client.
- **Rationale:** Three copies of the scan, backtest and pairs logic is why fixes don't reach every view. Parity tests between the CLI and the API become possible once there is one copy.

#### Execution safety model
- **Options:** Patch execute_signal; a single chokepoint with a Broker protocol and FakeBroker; an OMS framework.
- **Recommendation:** A single chokepoint in execution.py. TRADING_MODE defaults to off. Deterministic client_order_id plus the SQLite ledger. Bracket orders at the broker for equities. Direction -1 means 'exit long' unless ALLOW_SHORTS is true. FX, futures and index symbols are rejected through the registry.
- **Rationale:** This makes RiskManager actually enforce limits, removes the unbounded size, the duplicate orders and the unintended shorts, and lets the safety rules be tested offline.

#### Statistics and ML helper libraries
- **Options:** skfolio CPCV, mlfinlab, small purged-CV packages, the arch package, in-house code.
- **Recommendation:** Write DSR, PSR, PBO, the purged and embargoed TimeSeriesSplit, and triple-barrier labels in-house, each with known-answer tests. Use `arch` (already a dependency) for StationaryBootstrap, SPA and StepM. Use sklearn's TimeSeriesSplit(gap, test_size) as the minimum.
- **Rationale:** Each is roughly 30 to 40 lines taken from the papers. That is easier to own and test than a heavy or closed library.

#### Pairs hedge ratio
- **Options:** Full-sample OLS (current); rolling or expanding OLS; a Kalman filter (pykalman, filterpy or hand-rolled).
- **Recommendation:** Rolling OLS on log prices with an intercept as the default, re-fitted on a schedule. A hand-rolled Kalman filter is an optional mode in Phase 5, promoted only if walk-forward shows it is better out of sample.
- **Rationale:** Rolling OLS is causal and simple. filterpy is abandoned, and pykalman's EM fitting leaks future data.

#### LLM integration
- **Options:** Keep raw requests; the official SDK with one structured call; an Opus orchestrator with Sonnet workers; agent frameworks.
- **Recommendation:** The official `anthropic` SDK (>=1.11,<2) with `messages.parse` returning a Pydantic Briefing on claude-opus-5-5, model id overridable by env. Set effort to medium explicitly, max_tokens about 16000, check stop_reason, and catch typed errors from most to least specific. The server-side refusal fallback (`fallbacks: 'default'` with its beta header) is on by default for opus-5-5, and the owner can turn it off. Fan-out to Sonnet workers is Phase 5 and only if the eval shows a gain. The LLM must never import or influence execution.
- **Rationale:** The current call hard-codes claude-sonnet-4-20250514 with raw requests, a 30-second timeout, max_tokens=1024 and `content[0]['text']`, and swallows every failure. The tracks disagreed on that model's status: the LLM track says it was retired on 2026-06-15, while the bundled claude-api skill reference lists it as deprecated with retirement TBD. Either way it must be replaced. A `check-llm` command calling models.retrieve will settle it at runtime.

#### Config and secrets
- **Options:** Plain os.getenv; dataclass validation; pydantic-settings.
- **Recommendation:** pydantic-settings with SecretStr. It is validated at import time and startup fails on bad values.
- **Rationale:** Pydantic is already present through FastAPI. SecretStr masks secrets in repr, and validation stops unit errors such as MAX_POSITION_SIZE_PCT=5.

#### Units across the API
- **Options:** Percent numbers; fractions; pre-formatted strings (some ML and stress endpoints do this today).
- **Recommendation:** Every ratio leaves the API as a fraction stored in a float, never as a pre-formatted string. Only the frontend's format.ts converts to percent. Field names keep the backend's (total_return, max_drawdown).
- **Rationale:** Mixed units and pre-formatted strings such as main.py's f"{x:.2%}" and MLSignals' parseFloat are the cause of the current display errors.

#### Auth and deployment posture
- **Options:** No auth; a static bearer token; a full user system.
- **Recommendation:** Bind to 127.0.0.1 by default and read CORS origins from env. When API_TOKEN is set, a dependency requires `Authorization: Bearer` on every route except /health. Nothing more until the owner deploys.
- **Rationale:** This is a single-owner tool. The token is cheap insurance in case it is ever exposed.

### Phases

#### Phase 0: Freeze and pin (tests that pin the bugs, plus emergency interlocks)
**Goal:** Make the environment reproducible, build an offline test harness, encode every known bug as a strict-xfail test, and stop any harmful order immediately with minimal code. Nothing in this phase tries to fix results; that comes later.

**Exit criteria:** The environment is reproducible from the lock. The offline suite runs green, with every known bug pinned as strict xfail. TRADING_MODE defaults to off, and order notional is mathematically bounded even in paper mode. CI enforces all of this. Phase 0 can ship alone: the system then places no harmful orders and every bug is tracked by a test.

- **WS0.1 Lock the backend environment (no dependencies)** (ML-Trading-Platform-Backend, effort S)
  - Files: pyproject.toml (new), uv.lock (new), requirements.txt (generated from the lock for compatibility), .python-version (new: 3.12), docs/decisions.md (new ADR)
  - Changes: Create pyproject with exact pins: pandas~=2.3, numpy, scipy, scikit-learn~=1.9, lightgbm, statsmodels~=0.15, arch, ta==0.11.0, yfinance, alpaca-py~=0.44, fastapi, uvicorn, requests, python-dotenv, schedule, streamlit, plotly. Remove vectorbt, pandas-ta, xgboost, `backtesting` from the runtime, and also matplotlib and seaborn if unused (check with grep). Dev group: pytest, pytest-xdist, hypothesis, responses, freezegun, ruff, backtesting. Delete the unused imports (backtester.py:21-30 vectorbt and backtesting; ml_patterns XGB branch behind HAS_XGB stays importable but unused). Write an ADR with the decisions above and when to revisit each.
  - Acceptance: `uv sync --frozen && uv run python -c 'import main, server, dashboard'` succeeds on a clean machine with Python 3.12. `uv run pip check` is clean. Removing uv.lock and re-locking with the same pyproject gives identical top-level versions. The ADR file exists and lists every decision with its revisit trigger.
- **WS0.2 Offline test harness and fixtures (depends on WS0.1)** (ML-Trading-Platform-Backend, effort M)
  - Files: tests/conftest.py, tests/fixtures/synthetic.py, tests/fixtures/fake_broker.py, tests/fixtures/recorded/ (Alpaca, yfinance and Telegram payloads), pytest.ini or pyproject [tool.pytest]
  - Changes: Seeded generators for: GBM OHLC, random walk, an OU-cointegrated pair, a gap-through-stop bar, a 2:1 split day, a zero-volume FX frame, and a crypto weekend frame. FakeBroker (in memory: account, positions, orders, assets; records every call; can be told to raise or time out). A frozen-clock fixture. An autouse fixture that blocks sockets so tests fail on any network access. Markers: `slow` (statistical simulations, nightly) and `live` (opt-in, needs keys). A golden JSON of signal counts per PATTERN_REGISTRY entry on a fixed 500-bar fixture.
  - Acceptance: `uv run pytest -q` passes offline in under 60s. A test that calls requests.get without a mock fails with a 'network disabled' error. Re-running the golden pattern test reproduces the committed JSON exactly.
- **WS0.3 Bug-pinning tests for every known issue (depends on WS0.2)** (ML-Trading-Platform-Backend, effort M)
  - Files: tests/bugs/test_orders_bugs.py, tests/bugs/test_backtest_bugs.py, tests/bugs/test_ml_bugs.py, tests/bugs/test_pairs_bugs.py, tests/bugs/test_stress_bugs.py, tests/bugs/test_api_bugs.py, tests/bugs/test_data_bugs.py, tests/bugs/test_alerts_llm_bugs.py, tests/bugs/README.md (bug ID index)
  - Changes: One test per known issue. Each asserts the CORRECT behaviour and is marked `@pytest.mark.xfail(strict=True, reason='BUG-<id>')`, so a fix makes it pass, strict mode turns that into a failure, and the fixer has to remove the marker. IDs:
- ORD-1: qty bounded for PF=1e10.
- ORD-2: one order per (symbol, bar) across patterns and repeated runs.
- ORD-3: RiskManager consulted.
- ORD-4: no '=F', '=X' or '^' symbol reaches the broker.
- ORD-5: SELL with no position and shorts off is rejected.
- ORD-6: ML SELL confidence is >=0.5.
- SIG-1: check_recent_signal returns the latest non-zero signal.
- BT-1: entry at the next bar's open.
- BT-2: gap fill at the open.
- BT-3: mark-to-market equity.
- BT-4: bars_held = exit_index - entry_index.
- BT-5: random-walk Sharpe is about 0 and a flat zero-trade curve gives Sharpe 0.
- BT-6: profit factor is finite and capped.
- BT-7: len(equity_curve) == len(df).
- WF-1: walk-forward uses the training window.
- ML-1: targets on unresolved rows are NaN.
- ML-2: scaler not fit on test folds.
- ML-3: backtest uses only out-of-sample rows.
- PR-1: P&L does not change when both prices shift by +1000.
- PR-2: z and beta are causal under truncation.
- PR-3: /analyze JSON is valid with allow_nan=False.
- PR-4: /scan does not return 500 on np.bool_.
- ST-1: regimes are causal.
- ST-2: no non-contiguous slices.
- ST-3: Monte Carlo dispersion scales with position size.
- API-1: helpers.json_response does not 500 on NaN.
- API-2: errors return a 4xx or 5xx status.
- API-3: pattern scan total_return is not rounded to 2 decimals.
- DATA-1: no '=F' symbol is sent to Alpha Vantage.
- DATA-2: High >= max(Open, Close) after adjustment.
- DATA-3: the in-progress bar is dropped.
- ALR-1: an underscore in a Telegram message sends.
- ALR-2: the bot token is absent from logs.
- LLM-1: model id is configurable and a failure alerts.
  - Acceptance: `uv run pytest tests/bugs -q` reports N xfailed, 0 xpassed and 0 errors, where N equals the bug index count (about 34). CI fails if any bug test xpasses without its marker removed. The bug index maps each ID to the workstream that fixes it.
- **WS0.4 Emergency order interlocks (depends on WS0.2; temporary, replaced by WS1.3)** (ML-Trading-Platform-Backend, effort S)
  - Files: config.py, paper_trader.py, main.py
  - Changes: Add TRADING_MODE in {off, paper}, default off. execute_signal returns None and logs 'dry-run' unless the mode is paper. Clamp confidence to [0,1] after a NaN/inf check. Compute qty as floor(min(PAPER_TRADE_MAX_ORDER_VALUE, equity*MAX_POSITION_SIZE_PCT)*conf/price) with no max(1, ...) and reject qty 0. Reject symbols containing '=' or '^'. Reject direction -1 when no long position is held. main.py: remove win_rate*profit_factor (pass 1.0) and pass max(p, 1-p) for ML signals. Freeze dashboard.py with a 'legacy metrics' banner.
  - Acceptance: Hypothesis property test: for confidence in {0, 0.5, 1, 1e10, inf, nan}, price in [1, 1e5] and equity in [1e3, 1e6], the submitted notional is <= min(1000, 0.05*equity), and NaN/inf are rejected. With TRADING_MODE unset, a full scan against FakeBroker makes zero submit calls. Bug tests ORD-1, ORD-4, ORD-5 and ORD-6 flip to passing.
- **WS0.5 Frontend test baseline and bug-pinning tests (no dependencies)** (ML-Trading-Platform-Frontend, effort S)
  - Files: package.json, vitest.config.ts, src/test/setup.ts, src/test/handlers.ts, src/pages/__tests__/Dashboard.test.tsx, src/pages/__tests__/PairsTrading.test.tsx, .nvmrc, eslint.config.js
  - Changes: Add vitest (jsdom), @testing-library/react and user-event, and msw with shared handlers that return backend-shaped fixtures. Add `test` and `typecheck` scripts, engines node>=22.12, and .nvmrc. Write `it.fails` tests that pin bugs: Dashboard shows '15.3%' for total_return 0.153; PairsTrading shows '8.0%' and '-12.0%' for {total_return: 0.08, max_drawdown: -0.12}; a 500 or a 200 `{error}` response shows an error panel. Set `no-explicit-any` to warn for now.
  - Acceptance: `npm ci && npm test` runs offline with every request handled by MSW. The pinned tests are reported as expected failures. `npm run typecheck` passes.
- **WS0.6 CI for both repos (depends on WS0.1 and WS0.5)** (both, effort S)
  - Files: .github/workflows/ci.yml (backend), .github/workflows/nightly.yml (backend, -m slow), .github/workflows/ci.yml (frontend), Makefile (both: make check)
  - Changes: Backend: `uv sync --frozen`, ruff, `pytest -m 'not slow and not live'` with network blocked. Nightly runs the slow statistical suite. Frontend: npm ci, lint, `tsc -b`, vitest, `vite build` on Node 22.12. If the repos are not on GitHub, `make check` runs the same steps locally.
  - Acceptance: A PR that removes an xfail marker without fixing the bug turns CI red. A PR that adds `import requests; requests.get(...)` in a test turns CI red. Both pipelines finish in under 5 minutes.

#### Phase 1: Safe orders and alerts (depends on Phase 0)
**Goal:** Replace the interlocks with a proper execution chokepoint that has persisted risk state, idempotency, a symbol registry, risk-based sizing, protective exits at the broker, reconciliation, and reliable redacted alerts. Also repair the broken Claude call. Orders stay gated on validation_status, which nothing reaches until Phase 2, so this phase is safe to ship.

**Exit criteria:** Every ORD, SIG, ALR and LLM bug test passes. A restarted process stays halted. Two scheduled runs on the same bar produce exactly one order per (symbol, bar). No order can exceed the configured caps under property tests. Every order passes through execution.py, enforced by an AST test. Alerts and logs contain no secrets. With TRADING_MODE=paper, orders are accepted only for validated signals, so the system is effectively alert-only until Phase 2. This phase can be shipped and run in paper mode.

- **WS1.1 Operational state store in SQLite (no dependencies)** (ML-Trading-Platform-Backend, effort S)
  - Files: state/db.py (new), state/migrations/001_init.sql (new), .gitignore
  - Changes: An sqlite3 connection factory in WAL mode with busy_timeout, plus a tiny migration runner (a user_version pragma and ordered .sql files). Tables: risk_state(singleton), signal_ledger(signal_key UNIQUE, created_at, status), orders(client_order_id UNIQUE, broker_id, symbol, side, qty, status, decision_json), alerts_sent(dedup_key UNIQUE), runs(id, kind, started, finished, status). Later phases add migrations for jobs, bar_sources, trials and llm_calls.
  - Acceptance: Test: running the migrations twice is a no-op. Two threads inserting the same signal_key yield exactly one row and one IntegrityError. A writer killed mid-transaction (a simulated os._exit in a subprocess) leaves the DB readable and the row absent.
- **WS1.2 Instrument registry and capability matrix (no dependencies)** (ML-Trading-Platform-Backend, effort S)
  - Files: instruments.py (new), config.py (WATCHLIST and PAIRS reference registry IDs)
  - Changes: An Instrument dataclass with: id, asset_class, calendar, bars_per_year, alpaca_data_symbol, alpaca_trade_symbol, yfinance_symbol, executable, shortable_default, fractionable_default, and notes. Build it from config.WATCHLIST and PAIRS. Raise UnknownSymbol for anything else. Tag the PAIRS asset class, and flag GC=F/SI=F and CL=F/NG=F as research_only and excluded from the default scan.
  - Acceptance: Parametrised test over every WATCHLIST and PAIRS symbol: asset_class is resolved; 'GC=F', 'EURUSD=X' and '^GSPC' are not executable and have no Alpaca trade symbol; 'BTC-USD' maps to 'BTC/USD'; 'BRK-B' maps to 'BRK.B'; bars_per_year is 365 for crypto and 252 for equities; 'XYZ123' raises UnknownSymbol.
- **WS1.3 Execution chokepoint with Broker protocol, sizing and idempotency (depends on WS1.1 and WS1.2)** (ML-Trading-Platform-Backend, effort M)
  - Files: execution.py (new), brokers/alpaca.py (new; absorbs paper_trader.py), brokers/fake.py (moved from tests/fixtures), paper_trader.py (thin shim, then deleted), main.py
  - Changes: Define OrderIntent(symbol, direction, signal_bar_date, strategy_key, stop_pct, tp_pct, confidence in [0,1], validation_status) and Decision(status in {submitted, rejected, duplicate, halted, error, dry_run}, reasons, client_order_id, broker_id).

submit_intent runs checks in this order and fails closed on any exception:
1. kill switch or halt
2. mode
3. registry executable check, plus an Alpaca /v2/assets check (tradable, shortable, easy_to_borrow, fractionable), cached per run
4. validation_status eligibility
5. reconciliation clean
6. ledger insert of key = sha1(strategy|symbol|side|bar_date)[:N], readable prefix
7. no existing position or open order in the same direction
8. sizing: qty = floor(min(equity*RISK_PER_TRADE/(price*stop_pct), MAX_SYMBOL_PCT*equity/price, MAX_ORDER_NOTIONAL/price, remaining gross room/price) * confidence), with confidence only scaling down; equity comes from get_account, not buying_power
9. TIF per asset class (crypto gtc; equities day)
10. explicit order types (unknown or missing limit price raises)
11. submit with client_order_id

On a duplicate-ID error or a timeout, call get_order_by_client_id and return 'duplicate' or 'submitted'. Never retry with a new ID.

get_positions and get_account raise instead of returning [] or None.

main.py aggregates the triggered patterns per (symbol, bar) into one intent per bar, keeping the highest validation tier, with ties broken deterministically. Remove direct broker use everywhere else.
  - Acceptance: FakeBroker tests:
- Two runs of run_full_scan on the same data produce exactly one order per (symbol, bar), and the second run's Decisions are 'duplicate'.
- Two patterns firing for AAPL produce one order.
- A timeout after the broker accepts, followed by a retry, produces one order.
- halted=True gives zero broker calls and status 'halted'.
- If get_account raises, status is 'error' with zero submits.
- A $1000 cap on a $5000 stock gives qty 0, rejected.
- An unknown order_type raises and becomes a rejection, never a market order.
- A SELL with no position and ALLOW_SHORTS=false is rejected with 'shorts_disabled'; with a long position it sells at most the position qty.
- A crypto SELL with no position is rejected.
- An AST test asserts that only execution.py imports brokers.*.

Bug tests ORD-2 and ORD-3 flip to passing.
- **WS1.4 Persisted RiskManager, kill switch and risk CLI (depends on WS1.1)** (ML-Trading-Platform-Backend, effort M)
  - Files: risk_manager.py, config.py, main.py (risk subcommands)
  - Changes: State lives in risk_state: halted, halt_reason, halted_at, peak_equity, day_start_equity, day. update_from_broker(equity) runs at the start of each run and before each batch. Halt triggers: drawdown >= MAX_DRAWDOWN_HALT_PCT, daily loss >= MAX_DAILY_LOSS_PCT, orders today >= MAX_ORDERS_PER_DAY. A KILL_SWITCH file path or env var is checked on every intent. CLI: `risk status`, `risk halt --reason`, `risk resume --confirm`. A halt never clears itself. On halt: send an alert, and cancel open orders if CANCEL_ON_HALT is set. Delete the dead calculate_position_size, whose sizing now lives in execution, or delegate it there.
  - Acceptance: Drive equity down past the limit and assert halted. Construct a new RiskManager on the same DB file and assert it is still halted and that an intent is rejected. `risk resume` without --confirm exits non-zero. Touching the KILL_SWITCH file blocks the next intent with no restart. A daily loss beyond the limit halts, and a new UTC day does not clear the halt automatically.
- **WS1.5 Signal selection correctness (depends on Phase 0; merges into WS1.3's main.py edits, so assign to the same worker or sequence after)** (ML-Trading-Platform-Backend, effort S)
  - Files: main.py, backtester.py (profit factor only), signals.py (new: latest_signal and intent building)
  - Changes: latest_signal(df) returns the most recent non-zero signal and its bar, and returns 0 unless that bar is the latest completed session (staleness <= 1 bar). Profit factor is None when there are no losses, capped at 10 for display, and profit_factor_capped is exposed. Remove every sizing input derived from backtest statistics. ML passes direction-aware confidence max(p, 1-p). Compute validation_status per candidate: 'unvalidated' until Phase 2 supplies OOS results.
  - Acceptance: latest_signal on [+1, 0, -1] returns -1, and on [-1, 0, +1] returns +1. A signal 3 bars old returns 0. A backtest with 10 wins and 0 losses gives profit_factor None and a JSON-serialisable summary. With a stub ML p_up=0.15, the intent is SELL with confidence 0.85. With all candidates 'unvalidated', a paper-mode scan produces only 'rejected: not_validated' Decisions. Bug tests SIG-1 and BT-6 flip to passing.
- **WS1.6 Paper/live separation, bracket exits and reconciliation (depends on WS1.3)** (ML-Trading-Platform-Backend, effort M)
  - Files: config.py, brokers/alpaca.py, execution.py, main.py (risk reconcile), .env.example, README.md (paper-to-live checklist)
  - Changes: TRADING_MODE in {off, paper, live}. Separate ALPACA_PAPER_KEY/SECRET, ALPACA_LIVE_KEY/SECRET and ALPACA_DATA_KEY/SECRET. Live requires LIVE_TRADING_CONFIRM to match the account number suffix, and the startup check asserts the account matches the mode. Retire ALPACA_BASE_URL and the duplicated --paper and PAPER_TRADE_ENABLED switches.

Equities get order_class='bracket', with stop and take-profit prices rounded to the tick size and checked against the latest quote so each is on the correct side; prices are inverted for shorts; whole shares only. Crypto gets no bracket: a gtc stop_limit exit, or skip with reason 'no_protective_exit' if ALLOW_CRYPTO_WITHOUT_BRACKET is false.

Reconciliation at the start of each run compares broker positions and open orders with the ledger. A mismatch alerts and blocks new entries until `risk reconcile --accept` is run.
  - Acceptance: - mode=live without LIVE_TRADING_CONFIRM raises at startup.
- A test asserts TradingClient is built with paper=True unless the mode is 'live'.
- FakeBroker records a BUY at 100 with SL 2% and TP 4% as one bracket: stop 98.00, limit 104.00.
- A short inverts the sides.
- A fractional bracket qty is rejected.
- An unknown broker position leads to 'reconcile_mismatch' and a queued alert.
- A broker error during reconciliation blocks trading and is never read as a flat book.
- One manual verification against a real Alpaca paper account is recorded in the README checklist.
- **WS1.7 Reliable, redacted alerts (no dependencies)** (ML-Trading-Platform-Backend, effort S)
  - Files: alerts.py, logging_setup.py (new), main.py, server.py
  - Changes: Telegram uses parse_mode='HTML' with html.escape on every dynamic field, truncation to 4096 characters (Discord 2000), retries on 429 using retry_after and on 5xx with backoff (tenacity), and one plain-text retry on a 400 parse error. Never log str(requests exception); log the type and status code only. A logging.Filter installed on the root logger in main.py and server.py masks known secret values and the pattern `bot\d+:[A-Za-z0-9_-]+`. send_alert returns a result object. Alert classes: signal, order_decision, halt, reconcile_mismatch, briefing_failed. Dedup goes through alerts_sent. Alerts fire after the Decision and include its outcome and the mode.
  - Acceptance: Mock requests.post: 'ema_crossover *[x]* a.b!' is sent escaped with no 400. A mocked 400 parse error is followed by one plain-text retry that succeeds. A 6000-character message is cut to <=4096. A 429 with retry_after=1 is retried once. With TELEGRAM_BOT_TOKEN='123456:ABCdef' and a forced 404, caplog shows no record containing the token. A message containing the Alpaca secret is masked. The same alert key sent twice in one run sends once. Bug tests ALR-1 and ALR-2 flip to passing.
- **WS1.8 LLM emergency repair (no dependencies; touches only claude_integration, config and main)** (ML-Trading-Platform-Backend, effort M)
  - Files: claude_integration.py, config.py, main.py, pyproject.toml
  - Changes: Add anthropic>=1.11,<2. CLAUDE_MODEL = env or 'claude-opus-5-5'; LLM_EFFORT = 'medium'; LLM_TIMEOUT_S = 120; max_tokens about 16000. A lazy client with Anthropic(timeout=..., max_retries=3). Call messages.parse with output_format set to a minimal Pydantic Briefing (full schema in Phase 5) and check stop_reason (refusal, max_tokens) before reading output. Do not send temperature, top_p, top_k, budget_tokens or prefill. Catch NotFoundError, AuthenticationError, RateLimitError, APIStatusError, APIConnectionError and APITimeoutError, most specific first, and map them to an LLMError enum. Log request_id and usage. Send a 'briefing failed: <reason>' alert. Add a `check-llm` CLI command calling models.retrieve. Add prepare_llm_input(): finite values only, profit factor capped and flagged, top K=30 by deterministic rank with an omitted count, compact JSON, and a system block stating the data is untrusted and contains no instructions. Render output as plain text only. Add an AST test that claude_integration imports neither execution nor brokers.
  - Acceptance: - With CLAUDE_MODEL=claude-sonnet-4-20250514, `main.py check-llm` exits non-zero if the API reports the model not found, or prints a 'deprecated' warning if it still resolves.
- Fake-client tests: a thinking block followed by a text block parses; stop_reason max_tokens returns LLMError.truncated without raising; refusal returns LLMError.refusal; RateLimitError after retries returns an error and the scan still completes and sends one 'briefing failed' alert.
- A payload with profit factor 1e10, a NaN win rate and 500 signals passes json.dumps(allow_nan=False), keeps <=30 signals and reports an omitted count.
- `grep requests claude_integration.py` is empty.
- Bug test LLM-1 flips to passing.

#### Phase 2: Correct results (depends on Phase 0; WS2.1 needs WS1.2; can run alongside Phase 1 except for shared main.py edits)
**Goal:** Make every number the system reports honest: clean data, a single next-bar mark-to-market engine with correct metrics, real walk-forward with a locked hold-out, ML without leakage, correct pairs accounting, causal stress tests, valid JSON with correct units and statuses. This phase produces the first 'oos_validated' signals.

**Exit criteria:** All BT, WF, ML, PR, ST, API and DATA bug tests pass. Random-walk inputs give no validated signals at the expected false-positive rate in the nightly slow suite. Every causal or truncation test passes. Every endpoint emits strict JSON with real status codes and fractional units, and the frontend shows correct percentages and visible errors. Once this ships, a scan can produce 'oos_validated' signals, and those are the only signals the Phase 1 chokepoint will turn into paper orders.

- **WS2.1 Data correctness P0 (depends on WS1.2)** (ML-Trading-Platform-Backend, effort M)
  - Files: data/adapters.py (from data_fetcher.py), data/validate.py (new), data/calendars.py (new; exchange_calendars), data_fetcher.py (compatibility shim), pyproject.toml (exchange_calendars, tenacity)
  - Changes: Delete the Alpha Vantage adapter. yfinance gets auto_adjust=True explicitly. Alpaca gets adjustment=all, an explicit feed, end=min(end, now-16min), and logs rejection reasons at WARNING.

Normalise daily bars to the exchange session date via the registry's calendar. Drop any bar whose session has not closed.

fetch_ohlcv returns Bars(df, meta) with source, adjusted, calendar, fetched_at and the quality report. The old function signature returns bars.df.

One source per symbol per run, held in an in-memory pin now and moved to SQLite in Phase 3. fetch_pair uses the same source for both legs and aligns per calendar: crypto keeps weekends; a mixed-calendar pair is aligned on the stricter calendar with an explicit warning.

Validator checks: unique monotonic index, OHLC>0, High>=max(O,C,L), Low<=min(O,C,H), Volume>=0, |1-day return| flag threshold, staleness against the calendar, and coverage >=95% of expected sessions (replacing len>=30). Hard failures are never returned silently.
  - Acceptance: - Recorded split-day fixture: High>=max(Open,Close) and Low<=min(Open,Close) on every row.
- With a frozen clock mid-session, the last SPY bar is the previous completed session.
- Alpaca index values carry no 04:00/05:00 time component.
- Mocked Alpaca failing on the second call: three loads of AAPL report the same source.
- fetch_pair(AAPL, MSFT) with mixed timestamp conventions returns >=250 aligned rows.
- BTC-USD keeps Saturday bars.
- Fixtures with High<Close, a duplicate timestamp, a NaN close or a 31-row 2-year request each yield their specific QualityReport failure.
- Bug tests DATA-1, DATA-2 and DATA-3 flip to passing.
- **WS2.2 Engine and metrics correctness (depends on Phase 0)** (ML-Trading-Platform-Backend, effort L)
  - Files: engine.py (new; from backtester.classic_backtest), metrics.py (new), backtester.py (compatibility wrapper, then removed), patterns.py, config.py (MIN_TRADES=30), tests/test_engine_golden.py, tests/test_crosscheck_backtesting_py.py
  - Changes: execution='next_open' is the default; 'close' is an explicit opt-in. Evaluate stop and target from the entry bar using its high and low after entry at the open. Gap-aware fills: long stop fills at min(open, stop); the target fills at max(open, target) only when the open gaps through it. Pessimistic tie-break (stop first) is documented. Slippage and commission apply to every fill, including stops and the end-of-data close. A flip becomes two fills at the t+1 open.

Trade gets entry_index and exit_index. Equity is cash plus position marked to market at each bar's close on notional = capital*MAX_POSITION_SIZE_PCT. len(equity) equals len(df).

metrics.py:
- Sharpe with rf=0 on daily mark-to-market returns.
- Sortino via downside deviation sqrt(mean(min(r,0)^2)).
- CAGR-based Calmar.
- Max drawdown and its duration.
- PSR including skew and kurtosis.
- Win rate with a Wilson confidence interval.
- Profit factor None or capped.
- Expectancy at both notional and equity scale.
- summary() returns finite floats or None.

Delete the vectorbt overlay. patterns.py: replace `.shift(1).fillna(False)` with `.shift(1, fill_value=False).astype(bool)` and cast squeeze and expansion explicitly.
  - Acceptance: - Golden 8-bar fixture: entry price = next open*(1+slip); a bar opening 5% below the stop fills at the open; no trade has entry_date equal to its signal bar.
- Buy-and-hold over a known path: equity_t = C*(1+size*(P_t/P_0-1)) at every bar, minus costs.
- bars_held == exit_index - entry_index.
- A flat zero-trade run gives Sharpe 0.0, not NaN.
- Slow test: across 1,000 random walks, mean Sharpe is within ±0.2 of 0.
- json.dumps(summary, allow_nan=False) works for zero-trade, all-win and all-loss runs.
- Cross-check against backtesting.py (trade_on_close=False, same costs and sizing) on 500 bars of ema_crossover: same trade count, total return within 1%.
- Golden pattern signal counts are unchanged except where documented.
- Bug tests BT-1 to BT-7 flip to passing.
- **WS2.3 Walk-forward, locked hold-out and validation_status (depends on WS2.2)** (ML-Trading-Platform-Backend, effort M)
  - Files: validation.py (new; walk-forward, null, stress parts), main.py / signals.py (validation_status), config.py
  - Changes: walk_forward(df, candidates, train, test, step): the full contiguous series includes warm-up; candidates (pattern and stop/target grid) are selected on the training window only; the test window is traded with the position carried across; the OOS daily returns are stitched together. The locked hold-out is the last 20% of history and is never used for selection.

Random-entry null: 1,000 seeded draws with matching trade count, holding period and stop/target rules, giving a p-value. Cost stress at 2x and 3x, and a +1 bar delay stress.

validation_status='oos_validated' iff OOS trades >=30, OOS PSR>0.95, the hold-out is not negative, the null p<0.05, and the sign holds at 2x cost. All parameters are seeded.
  - Acceptance: - Changing data in a test window after the selection date leaves that fold's choice unchanged.
- OOS windows do not overlap.
- Slow test on random walks over 100 seeds: in-sample best Sharpe is clearly >0 while OOS mean Sharpe is within noise of 0, and fewer than 5% are 'oos_validated'.
- A planted 1-bar-ahead predictive signal gives null p<0.01 and is validated, and loses significance at +5 bars of delay.
- Running the same seed twice gives byte-identical JSON.
- Bug test WF-1 flips to passing.
- **WS2.4 Stress test correctness (depends on WS2.2)** (ML-Trading-Platform-Backend, effort M)
  - Files: stress_test.py, validation.py (block bootstrap via arch)
  - Changes: detect_regimes uses expanding-window percentile thresholds, shifted by one bar. stress_test_regimes runs the strategy once on the full frame and attributes trades (by entry) and daily returns to regimes; regimes with fewer than 10 trades are reported as insufficient. monte_carlo_analysis uses arch StationaryBootstrap on daily mark-to-market returns at real size with a fixed seed, reports CAGR, max drawdown and Sharpe bands, and drops the trade-count annualisation. parameter_sensitivity reports the share of neighbouring stop/target sets with positive OOS Sharpe instead of std<1.0, and its trial count is recorded.
  - Acceptance: - Regime labels for bars 0..k are identical when computed on df[:k+1] and on the full df.
- A spy confirms the pattern function is never called on a frame with index gaps above the median spacing.
- Monte Carlo with the same seed gives identical output twice, and return-band width at 5% size is about 1/20 of that at 100%.
- Bug tests ST-1, ST-2 and ST-3 flip to passing.
- **WS2.5 ML pipeline without leakage (depends on WS2.2; WS2.1 is a soft dependency)** (ML-Trading-Platform-Backend, effort L)
  - Files: features.py, ml/labels.py (new), ml/splits.py (new: PurgedTimeSeriesSplit), ml_patterns.py, routes/ml_routes.py, main.py (scan_ml)
  - Changes: make_labels separates targets from features. Unresolved rows and exact zeros (or a dead zone) are NaN and dropped. Assert no 'target_' column reaches X.

Features are stationary: price-normalised MACD and ATR, a volume z-score, rolling VWAP distance, no duplicated weekly/monthly/quarterly returns, consec_* capped, a fixed schema (volume features are NaN when volume is zero), inf mapped to NaN, and the unused lookback parameter removed.

The Pipeline has no scaler for trees. Use PurgedTimeSeriesSplit(gap = horizon + embargo, fixed test_size).

Walk-forward predictions retrain every ML_RETRAIN_DAYS using only past data; the backtest uses only that OOS series, with oos_start reported. Metrics: log-loss, Brier, AUC, and accuracy minus the majority-class rate, with mean and std. The scan gate becomes OOS AUC above baseline AND oos_validated.

probas with a single class returns p_up in {0,1} explicitly. model_type reflects the models actually used. Feature importance comes from permutation importance on the OOS block.
  - Acceptance: - For N bars and horizon h, the labels have exactly h trailing NaNs, excluded from X.
- compute_features(df) and compute_features(df.iloc[100:]) agree for rows past 100+warm-up.
- Columns are identical for zero-volume FX and for stocks; np.isfinite(X).all().
- In every fold, max train label time < min test time minus the embargo.
- Perturbing test rows leaves the fitted pipeline unchanged.
- Leakage canary: a feature equal to the next-bar return gives AUC near 1; the production features on shuffled labels give AUC 0.5±0.05.
- Slow test on random walks over 50 seeds: OOS Sharpe mean is about 0, fewer than 5% above the 95% threshold, and every non-zero signal index is >= the first OOS index.
- Bug tests ML-1, ML-2 and ML-3 flip to passing.
- **WS2.6 Pairs correctness (depends on WS2.2 for metrics.py; WS2.1 is a soft dependency for alignment)** (ML-Trading-Platform-Backend, effort L)
  - Files: pairs_trading.py, routes/pairs_routes.py, config.py
  - Changes: compute_spread_stats(logA, logB, window) returns causal beta_t, alpha_t, spread_t and z_t using data through t-1 (rolling OLS with an intercept). The signal, the backtest, /analyze and /scan all consume it; delete the full-sample current_zscore signal use (pairs_trading.py:126-128, 376-381) and the copy in the route (pairs_routes.py:50-52).

Two-leg engine: pos shifted by one bar; daily return = pos[t-1]*(w_a*r_a - w_b*r_b) with w_a=1/(1+|b|) and w_b=|b|/(1+|b|); costs on both legs' turnover; open trades marked at the end; annualised with the registry's bars_per_year.

Statistics: Engle-Granger on log prices in both orderings, taking the max p-value; drop the ADF gate on fitted residuals; return correlation replaces price correlation; half-life = -ln2/ln(phi), None unless 0<phi<1; BH across the scanned pairs; at least 252 aligned bars.

Exits: a frozen-at-entry z stop, a time stop at 3 half-lives, and exit_reason. Pairs with non-equity legs are labelled non_executable.
  - Acceptance: - B flat at 100 and A from 100 to 110, long the spread with beta 1: return 5% on gross, unchanged when both prices are shifted by +1000.
- Truncation test: z and beta at or before T are bit-identical when random data is appended after T.
- The z returned by /scan equals the backtest's last z.
- Slow tests: OU with phi=0.95 recovers half-life within 25% on 1000 bars; independent random walks are accepted <=5% after BH; swapping A and B gives the same is_cointegrated.
- A spread that drifts persistently exits via the frozen-z or time stop where the rolling-60 z stays below 3.5.
- BTC/ETH keeps weekend bars and uses 365.
- Bug tests PR-1 and PR-2 flip to passing.
- **WS2.7 API serialisation, status codes and units, backend and frontend (no dependencies; start immediately)** (both, effort M)
  - Files: Backend: api/serialize.py (new), server.py, routes/helpers.py (deleted), routes/*.py, api/errors.py (new), Frontend: src/lib/format.ts (new), src/components/ErrorPanel.tsx (new), src/components/ErrorBoundary.tsx (new), src/api/client.ts, src/pages/*.tsx
  - Changes: Backend:
- Add to_native(obj), which recursively maps np.* types to Python types and nan/inf to None, and a SafeJSONResponse that renders with allow_nan=False.
- Return SafeJSONResponse(to_native(result)) from every route. FastAPI's jsonable_encoder runs before the response class, which is why np.bool_ currently 500s, and the existing NumpyEncoder.default never sees np.float64 because it subclasses float.
- Exception handlers map domain errors to HTTPException 404, 422, 502 or 503, with the envelope {error:{code,message,details}}. Remove every `return {'error': ...}`.
- Pydantic request validation: period_days bounds, symbols through the registry, symbol_a != symbol_b.
- Units: fractions everywhere. Remove round(x, 2) on fractions (pattern_routes.py:124) and remove pre-formatted strings (main.py and ML/stress summaries).

Frontend:
- format.ts pct/num/usd render null, NaN and Infinity as 'n/a'.
- Dashboard and Pairs read the backend field names and format with pct().
- Remove the `|| 0` fallbacks.
- A client interceptor throws ApiError on non-2xx responses and on legacy {error} bodies.
- Every page shows error-with-retry and empty states.
- An ErrorBoundary wraps the routes.
  - Acceptance: - A backend TestClient sweep over every route with fixture data returns bodies that pass json.loads under a strict parser (no NaN/Infinity tokens); /api/pairs/scan with has_signal=True returns 200; an unknown symbol returns 404; period_days=10 returns 422.
- Frontend: Dashboard shows '15.3%' for 0.153; Pairs shows '8.0%' and '-12.0%'; an MSW 500 or 200 {error} response shows ErrorPanel with Retry; a component that throws renders the fallback while the sidebar still works; grep finds no catch block in src/pages whose only action is console.error.
- Bug tests API-1, API-2, API-3, PR-3 and PR-4 and the frontend it.fails tests flip to passing.

#### Phase 3: Structural foundation (depends on Phases 1 and 2)
**Goal:** Turn the corrected parts into one maintainable system: a single service layer, typed contracts end to end, a persistent bar store with resilient fetching, background jobs, versioned models and validated config. This removes the three-way duplication and the 5-minute synchronous requests.

**Exit criteria:** Every consumer (CLI, scheduler, API, and Streamlit if kept) calls services/, enforced by an import test. Frontend types are generated, and drift fails CI. No request blocks for more than about 30s: heavy work runs as jobs. Bars come from the local store with pinned sources and retry/rate limits. Models are versioned and loaded, not retrained per request. Config is validated and secrets are masked. Every Phase 0-2 test is still green.

- **WS3.1 Single service layer (no dependencies within the phase; do first)** (ML-Trading-Platform-Backend, effort L)
  - Files: services/__init__.py, services/scan.py, services/backtest.py, services/pairs.py, services/ml.py, services/stress.py, services/data_status.py, services/models.py (Pydantic I/O), main.py, routes/*.py, dashboard.py (calls services or stays frozen)
  - Changes: Move the orchestration loops out of main.py (scan_technical, scan_pairs, scan_ml) and out of the routes (the pattern_routes scan loop, the backtest, pairs, ML and stress handlers) into services that return Pydantic models. main.py becomes argparse subcommands (scan, schedule, risk, data-status, check-llm) calling services. Routes become 5-15 line adapters. Each service takes a data provider dependency, so tests inject fixtures.
  - Acceptance: - Import-graph test: routes/* and main.py import only services, execution, alerts and config, never engine, patterns, pairs_trading or ml_patterns directly.
- Parity test: the CLI scan and POST /api/patterns/scan on the same fixture data return identical candidate sets and metrics.
- dashboard.py contains no copy of the scan or backtest loops (grep for classic_backtest returns nothing).
- **WS3.2 Typed contracts end to end (depends on WS3.1)** (both, effort M)
  - Files: Backend: api/schemas.py, routes/*.py (response_model=...), scripts/export_openapi.py, openapi.json (committed), tests/test_openapi_snapshot.py, Frontend: package.json (openapi-typescript, openapi-fetch; axios removed), src/api/schema.d.ts (generated), src/api/client.ts, src/plotly.d.ts (replaced with typings), eslint.config.js (no-explicit-any: error), .env.example
  - Changes: Backend: every route declares response_model built from service models. A FiniteFloat annotated type rejects NaN and inf at validation time. The error envelope is documented in the OpenAPI responses. The snapshot test fails when the schema changes without regenerating openapi.json.

Frontend: an `api:gen` script reads ../backend/openapi.json (or a copied file); `api:check` regenerates and runs git diff --exit-code. The openapi-fetch client uses baseUrl import.meta.env.VITE_API_BASE_URL ?? '/api', and middleware throws ApiError. Remove every `any` from src/pages.
  - Acceptance: - Renaming a backend response field and then running `npm run api:gen && npm run build` makes tsc fail at the page that reads the old field.
- CI api:check fails when schema.d.ts is stale.
- `npm run lint` fails on a new `: any`.
- A build with VITE_API_BASE_URL=https://example.test/api sends requests to that host (Vitest using the built config).
- A backend test returning NaN in a FiniteFloat field produces a 500 envelope logged as a contract violation, not an invalid JSON body.
- **WS3.3 Persistent bar store with resilient HTTP (depends on WS2.1 and WS1.1)** (ML-Trading-Platform-Backend, effort M)
  - Files: data/store.py (new), data/http.py (new: Session, tenacity, token bucket, quota, circuit breaker), state/migrations/002_bars.sql (bar_sources, provider_quota), services/data_status.py, config.py
  - Changes: Parquet at data/bars/{asset_class}/{symbol}__{tf}__{source}__{adj}.parquet, written to a temp file then renamed with os.replace. Incremental fetch from last_session minus 5. If the overlap differs beyond tolerance, refetch the full history. Today's data uses a TTL; refresh=True forces a fetch. The source pin for each symbol lives in bar_sources. A failing primary source serves cached data flagged stale and never switches provider silently.

HTTP layer: tenacity retries 429, 5xx and timeouts with exponential backoff and jitter, honours Retry-After, and never retries 4xx auth errors. A per-provider token bucket (Alpaca 200/min) and a circuit breaker with cooldown.

The data-status CLI and endpoint list each symbol's source, adjusted flag, last session, coverage, quality flags and cache age.
  - Acceptance: - Mock HTTP: the first call downloads; a second call in the same session makes 0 requests; the next session makes exactly 1 incremental request starting at last minus 5.
- The TechnicalScanner's three views of one symbol cause 1 provider call.
- Killing the process mid-write leaves the old parquet intact.
- A stub returning 429 with Retry-After: 2 twice then 200 succeeds after about 4s with 3 requests; a 401 is not retried.
- 42 symbols under a fake clock never exceed the configured rate.
- data-status lists all watchlist symbols with no 'unknown' source.
- **WS3.4 Background jobs and the single scheduler (depends on WS3.1 and WS1.1)** (both, effort L)
  - Files: Backend: jobs.py (new), state/migrations/003_jobs.sql, routes/jobs_routes.py (new), routes for scan, stress, ML training and walk-forward (POST returns job_id), main.py schedule (fcntl lock, runs table), Frontend: src/hooks/useJob.ts, src/pages/Dashboard.tsx, StressTestLab.tsx, MLSignals.tsx, vite.config.ts (300s timeout removed), src/api/client.ts
  - Changes: jobs table: id, kind, params_json, status in {queued, running, done, failed, cancelled}, progress, result_ref, error, timestamps. ThreadPoolExecutor(max_workers=2) in the API lifespan. On startup, jobs still marked running become failed with reason 'orphaned'. Cooperative cancellation through a flag checked between symbols. GET /api/jobs/{id} and POST /api/jobs/{id}/cancel. The scheduler takes a file lock and a second instance exits immediately; every run writes a runs row.

Frontend: useJob is built on TanStack Query with refetchInterval returning false once the job reaches a terminal status. It shows a progress bar and a cancel button, and stores the job_id in the URL so a reload resumes. Normal requests time out at 30s.
  - Acceptance: - A TestClient POST to the scan endpoint returns 202 with a job_id in under 200ms; polling reaches done with the same result as the synchronous service.
- Restarting the app marks a running job failed with reason 'orphaned'.
- Cancel stops within one symbol.
- Starting two schedulers: the second exits with 'lock held'.
- Frontend MSW: running x3 then done shows progress, then results, then no further requests.
- A failed job shows the error panel.
- A reload mid-job resumes polling.
- **WS3.5 Versioned model artefacts and retrain policy (depends on WS2.5 and WS3.4)** (ML-Trading-Platform-Backend, effort M)
  - Files: ml/persistence.py (new), ml_patterns.py, services/ml.py, routes/ml_routes.py (train job separate from predict), config.py
  - Changes: joblib bundle plus metadata.json under models/<symbol>/<timestamp>/. Metadata: library versions, git SHA, data range and hash, feature list and schema hash, label spec, seeds, CV and OOS metrics. load() refuses on a major-version mismatch or a schema mismatch. Predict loads the latest valid model, and training runs only as a job when the model is older than ML_RETRAIN_DAYS. Remove pickle.
  - Acceptance: - Save then load gives np.allclose on predict_proba.
- A mutated feature list or a faked sklearn major version raises a clear error.
- Two predict calls within ML_RETRAIN_DAYS cause zero fits (mocked fit call count is 0 on the second).
- metadata.json contains every listed key.
- grep finds no `pickle`.
- **WS3.6 Config, security posture and runbook (no dependencies)** (ML-Trading-Platform-Backend, effort S)
  - Files: config.py (pydantic-settings), server.py (CORS from env, optional bearer dependency, host default 127.0.0.1), .env.example, README.md
  - Changes: A Settings class with SecretStr for every key and validators: 0<MAX_POSITION_SIZE_PCT<=MAX_SYMBOL_PCT<=MAX_GROSS_PCT<=1, STOP<TP, caps>0, LLM_EFFORT in {low, medium, high, xhigh, max}, TRADING_MODE valid. CORS_ORIGINS and API_TOKEN come from env. README covers run, schedule, risk commands, the paper-to-live checklist and data/ToS caveats (yfinance is research-only).
  - Acceptance: - MAX_POSITION_SIZE_PCT=5 fails startup with a clear message; LLM_EFFORT=banana fails naming the valid values.
- repr(settings) contains no secret.
- With API_TOKEN set, /api/* without the header returns 401 and /health returns 200.
- A CORS preflight from an origin not in CORS_ORIGINS is rejected.
- **WS3.7 Frontend server state with TanStack Query (depends on WS3.2)** (ML-Trading-Platform-Frontend, effort M)
  - Files: src/main.tsx, src/App.tsx, src/hooks/*.ts, src/pages/*.tsx, package.json
  - Changes: Add QueryClientProvider with retry 2 for GETs only, 0 for mutations, and staleTime of minutes for patterns, pairs and config. Reads use useQuery and actions use useMutation or useJob. Pass the AbortSignal to the client. TechnicalScanner's three requests become one query keyed by [symbol, pattern] (backed by the single data endpoint from WS3.3). A global onError raises a toast.
  - Acceptance: - Navigating Dashboard → Settings → Dashboard keeps the MSW patterns request count at 1.
- Double-clicking Analyze produces one in-flight request.
- Unmounting mid-request aborts it.
- A GET failing once with 503 then succeeding shows data with no user action.

#### Phase 4: Honest selection at scale (depends on Phases 2 and 3)
**Goal:** Account for the whole search process (about 20 patterns × 39 symbols × parameters). The order gate becomes a deflated, multiple-testing-aware standard, and ML probabilities become calibrated.

**Exit criteria:** Every scan reports N trials, DSR, PBO and an SPA/BH result. Only deflated_validated signals can become orders. ML outputs are calibrated probabilities with drift-based abstention. Noise-only simulations in the nightly suite produce no orderable signals at the designed error rates.

- **WS4.1 Trial registry (depends on WS3.1 and WS3.3)** (ML-Trading-Platform-Backend, effort M)
  - Files: services/trials.py (new), state/migrations/004_trials.sql, data/trials/ (parquet)
  - Changes: Every evaluation in a scan run, valid or not, records trial_id, run_id, symbol, pattern, params hash, engine version, data hash, and a daily return series in a (date × trial) parquet per run. N is counted per run, including stop/target and signal-parameter grids.
  - Acceptance: - A scan of 3 symbols × 4 patterns × 2 parameter sets records exactly 24 trials and a 24-column returns matrix aligned on dates.
- Re-running with the same seed and data gives an identical matrix hash.
- **WS4.2 DSR, PBO and SPA layer and the deflated order gate (depends on WS4.1)** (ML-Trading-Platform-Backend, effort M)
  - Files: validation.py, services/scan.py, execution.py (eligibility), config.py
  - Changes: In-house code: PSR, and DSR using N and the variance of Sharpe across trials, plus skew and kurtosis. PBO via CSCV with S=16 blocks. Then arch.bootstrap SPA (or StepM) on the returns matrix. BH-FDR across pairs. validation_status='deflated_validated' requires oos_validated plus DSR p<0.05 (or SPA p<0.05). From here on, execution requires deflated_validated. The UI and API show N, DSR, PBO and the status.
  - Acceptance: - Slow simulation: 780 zero-mean series of 750 days. The best raw Sharpe exceeds 0.3 in most runs, but the best DSR is not significant in >=90% of runs, and PBO is about 0.5±0.1.
- A planted series with annual Sharpe 1.5 stays significant in >=80% of runs.
- DSR and PBO known-answer unit tests match the paper examples within 1e-3.
- A FakeBroker test shows an oos_validated but not deflated_validated intent rejected with 'not_deflated'.
- **WS4.3 Signal-parameter robustness (depends on WS4.1)** (ML-Trading-Platform-Backend, effort M)
  - Files: patterns.py (parameterised registry entries), stress_test.py
  - Changes: Expose the key parameters (EMA windows, RSI thresholds, channel lengths) with neighbourhood grids. Robustness = share of neighbours with positive OOS Sharpe, with a smooth-degradation requirement. Grid trials feed N.
  - Acceptance: - A planted strategy with an edge only at one exact window is flagged non-robust; a smooth-edge strategy is flagged robust.
- The trial count reported by the sensitivity run equals the N recorded in WS4.1.
- **WS4.4 ML calibration, thresholds, model choice and drift guard (depends on WS2.5 and WS3.5)** (ML-Trading-Platform-Backend, effort M)
  - Files: ml_patterns.py, ml/calibration.py (new), ml/drift.py (new), config.py
  - Changes: Sigmoid calibration on a time-ordered validation block, checked against the installed sklearn 1.9 API (FrozenEstimator or a time-aware cv). The threshold is chosen on validation for expected value after costs, with an abstain band, replacing 0.60/0.40. Baseline-first selection: fit logistic regression, shallow LightGBM and an RF+LGBM ensemble under the same purged CV, and use the ensemble only if it beats the best single model by more than one fold std on log-loss. Apply class_weight consistently. Drift guard: store per-feature quantiles at training time; at prediction, compute PSI on the last 60 bars and abstain with reason 'drift', 'stale' or 'nan' when needed.
  - Acceptance: - Synthetic data with known probabilities: calibrated Brier/ECE is lower than uncalibrated, and buckets with n>=50 are within 0.05.
- On noise data, selection picks the simple model or abstains in >=90% of seeds.
- A ×10 volatility regime shift makes predict abstain with 'drift'.
- Held-out data from the same process has PSI<0.2 on >=90% of features.
- **WS4.5 Pairs walk-forward and breakdown guard (depends on WS2.6 and WS4.1)** (ML-Trading-Platform-Backend, effort M)
  - Files: pairs_trading.py, services/pairs.py
  - Changes: walk_forward_pairs(formation=252, trade=63, step=63) with parameters frozen within each block and OOS results stitched. The z lookback is tied to the half-life, clamped to 20-120. A rolling EG re-test forces exit and blocks entries when p>0.15 (hysteresis against the 0.05 entry threshold) or the half-life leaves its band. is_valid is based on OOS. Pairs trials feed the registry.
  - Acceptance: - A synthetic pair cointegrated in its first half and independent in its second has non-significant OOS P&L in the second half, while the full-sample backtest looks profitable.
- Changing data inside a later block leaves earlier blocks unchanged.
- After an OU→random-walk switch, exit happens via coint_break or time_stop within a bounded number of bars, with no re-entry.

#### Phase 5: Parity and features (depends on Phase 3; LLM items also need Phase 4 data_quality flags)
**Goal:** Add useful features on top of the trusted base: a typed AI briefing, UI parity, performance, and optional modelling improvements. Retire the duplicated Streamlit app.

**Exit criteria:** The AI briefing is typed, grounded, budgeted, evaluated, visible in the UI and unable to raise any order size. The frontend is lean, smoke-tested and the only UI, with Streamlit retired unless the owner chose to keep it. Optional upgrades are adopted only with recorded out-of-sample evidence.

- **WS5.1 Typed LLM briefing, budget and eval harness (depends on WS1.8, WS3.1 and WS3.4)** (ML-Trading-Platform-Backend, effort L)
  - Files: claude_integration.py, services/briefing.py, state/migrations/005_llm_calls.sql, routes/briefing_routes.py, tests/test_briefing_quality.py, tests/llm_fixtures/*.json, config.py
  - Changes: Full Briefing schema: top_ideas[{symbol, pattern, direction enum, rationale<=300, evidence_refs}], red_flags, pairs_comment, ml_agreement enum, risk_summary, bias enum, and exposure_multiplier in [0,1] validated in Pydantic (not in the JSON schema). Drop ungrounded ideas. render_briefing_text produces plain text.

The llm_calls table records ts, model, effort, request_id, tokens, cache read/write, latency, usd_estimate and stop_reason. LLM_MAX_USD_PER_RUN and LLM_MAX_USD_PER_DAY are enforced before each call.

POST /api/briefing runs as a job and is cached by input hash for N minutes. LLM_CAN_REDUCE_EXPOSURE defaults to false; when enabled, the multiplier only reduces size. Add a stable cached system block once the prompt exceeds 512 tokens.

Eval: about 20 fixtures, including PF=1e10, NaN, empty, 200 signals, in-sample-flagged sources, conflicting technical and ML signals, and injection text in a symbol. Offline deterministic checks: schema, grounding, numbers match the input, no recommendation on flagged sources. An opt-in `-m live` judge on claude-sonnet-5-5 writes scores and cost to a CSV.
  - Acceptance: - A fake client returning an idea citing an absent symbol: the idea is dropped and the ungrounded counter increments.
- exposure_multiplier 1.7 is rejected.
- Property test: for any multiplier in [-5, 5] or NaN, final qty <= qty without the LLM.
- LLM_MAX_USD_PER_DAY=0.01 with $0.02 already logged: no client call, and a budget alert is sent.
- Two endpoint calls within the TTL make 1 LLM call.
- A simulated RateLimitError returns 503 with the envelope.
- The offline eval runs in under 10s with no key; a fabricated symbol in a recorded response fails grounding.
- `-m live` refuses to run without an explicit env flag.
- **WS5.2 Frontend analysis page, chart performance and smoke tests (depends on WS3.2, WS3.7 and WS5.1)** (ML-Trading-Platform-Frontend, effort M)
  - Files: src/pages/Analysis.tsx, src/components/Chart.tsx, src/pages/TechnicalScanner.tsx, src/pages/MLSignals.tsx, src/pages/PairsTrading.tsx, package.json, playwright.config.ts, e2e/smoke.spec.ts, .github/workflows/ci.yml
  - Changes: Analysis page: calls /api/briefing through useJob and renders the typed fields as plain text (no raw HTML). It is labelled as AI commentary, not a signal, and shows the cited metrics next to the text.

Chart.tsx: react-plotly.js/factory with a partial Plotly bundle that includes candlestick (check whether the finance or cartesian bundle exists for the pinned Plotly major before upgrading from 3.4 to 4.x), loaded with React.lazy and Suspense, plus useMemo traces, useResizeHandler and decimation above about 3k points.

Playwright smoke: visit 6 routes with mocked responses and fail on any console error or unhandled rejection.
  - Acceptance: - An MSW briefing whose rationale contains `<script>` puts no script element in the DOM.
- A malformed briefing shows the error panel.
- The build output has Plotly in a separate lazy chunk, the main chunk is smaller than the Phase 0 baseline (recorded number), and Settings does not request the Plotly chunk.
- The Playwright smoke job passes in CI.
- **WS5.3 Retire the Streamlit dashboard (depends on WS3.1 and WS5.2; owner decision)** (ML-Trading-Platform-Backend, effort S)
  - Files: dashboard.py, pyproject.toml (streamlit removed), README.md
  - Changes: Write a feature parity checklist between dashboard.py and the React pages. Once parity is reached, delete dashboard.py and the streamlit dependency. If the owner wants to keep it, reduce it to read-only calls to services/ and jobs.
  - Acceptance: The parity checklist is complete in the README. grep finds no `streamlit` in the repo (or, if kept, the import-graph test shows dashboard.py imports only services). The lock no longer contains streamlit.
- **WS5.4 Modelling and realism upgrades, each opt-in and kept only if OOS improves (depends on Phase 4)** (ML-Trading-Platform-Backend, effort L)
  - Files: pairs_trading.py (Kalman mode), ml/labels.py (triple barrier, uniqueness weights, meta-labeling), engine.py and metrics.py (ATR-scaled slippage, half-spread by asset class, borrow cost, vol-targeted sizing, trailing and time stops), risk_manager.py (check_correlation wired into the chokepoint)
  - Changes: Hand-rolled 2-state Kalman hedge ratio (hedge_mode), with delta chosen on the formation window. Triple-barrier labels with vol-scaled widths and uniqueness sample weights; the purged splitter uses touch times; meta-labeling on the rule patterns. Cost realism and vol-targeted sizing. A correlation gate in the chokepoint. Each is compared against the baseline through the Phase 4 walk-forward and DSR, and promoted only if better.
  - Acceptance: - Kalman: after a step change in beta at bar 500 it converges within 50 bars and passes the truncation test.
- Triple barrier: hand-built paths give +1, -1 and 0 with correct touch times; overlapping events get lower weights.
- Cost: a high-vol fixture has strictly lower net return than a low-vol one at equal gross edge.
- Two correlated open positions block a third correlated entry.
- A comparison report records the OOS DSR for baseline versus upgrade.
- **WS5.5 Optional data and LLM extensions (owner opt-in)** (ML-Trading-Platform-Backend, effort M)
  - Files: data/adapters.py (Tiingo), scripts/compare_futures_databento.py, claude_integration.py (orchestrator and workers)
  - Changes: Tiingo adapter for daily equity bars, which provides adjusted and raw OHLC together, as a cross-check source. A one-off pull of the 7 futures with the Databento free credit to compare against yfinance =F roll behaviour, with a decision recorded. An Opus orchestrator with Sonnet workers (claude-sonnet-5-5, effort low, concurrency 4 or the Batch API), kept only if the eval shows a clear gain over the single call and the cost multiple is logged.
  - Acceptance: - Tiingo golden test on a recorded split day: adjusted O/H/L/C are consistent.
- The comparison script prints per-symbol return correlation and the maximum roll-day discrepancy, and the ADR is updated.
- Orchestrator: 2 of 10 workers timing out still produces a briefing with those 2 marked 'unreviewed'; the cost cap stops further worker calls; the eval table shows single-call versus orchestrated scores and USD.

### Test strategy

PRINCIPLES
- Pin every bug before fixing it. Phase 0 writes one strict-xfail test per known issue (about 34 IDs: ORD, SIG, BT, WF, ML, PR, ST, API, DATA, ALR, LLM).
- A fix is accepted only when its pinned test flips to passing and the xfail marker is removed in the same PR. Strict mode makes an unremoved marker fail CI.
- For every P0 fix, temporarily reverting the fix must make at least one named test fail. The reviewer checks this with `git revert` on the fix commit.

BACKEND LAYERS (pytest, all offline, sockets blocked by an autouse fixture)
1. Unit and known-answer tests.
   - Hand-built golden OHLC fixtures with expected trades (8-bar engine fixture, gap-through-stop, split day).
   - Metric formulas checked against closed forms: buy-and-hold equity path, analytic drawdown, DSR and PBO paper examples.
   - Golden signal counts per pattern on a fixed 500-bar fixture.
2. Property tests (hypothesis).
   - Order notional <= caps for any confidence, price or equity, including NaN and inf.
   - qty >= 0.
   - Equity at the end = initial + sum of trade pnl.
   - Drawdown is in [-1, 0].
   - No entry happens on its signal bar.
   - A one-bar-later signal series never moves an entry earlier.
   - Adjusted OHLC satisfies the Low/High invariants.
   - An LLM exposure multiplier never increases qty.
3. Causality and truncation tests. A shared helper `assert_causal(fn, df, T)` appends random data after T and asserts outputs at or before T are bit-identical. It is applied to patterns, regimes, pairs spread stats, ML predictions and features (window independence).
4. Statistical simulation tests (marked slow, seeded, run nightly).
   - Random walks give Sharpe about 0 and validated rates at or below the designed alpha.
   - 780 noise series: DSR non-significant in >=90% of runs, PBO about 0.5.
   - Planted edges are detected (>=80%).
   - OU half-life recovery.
   - Engle-Granger false-acceptance rate <=5% after BH.
   - ML leakage canary (AUC near 1 with the leaked feature, 0.5±0.05 on shuffled labels).
5. Integration tests with fakes.
   - FakeBroker drives the full run_full_scan, chokepoint, ledger, reconciliation and halts, including a restart test that rebuilds RiskManager from the same SQLite file and a timeout-after-accept test.
   - `responses` replays recorded Alpaca, yfinance and Telegram payloads.
   - freezegun covers sessions and partial bars.
   - A fake Anthropic client covers thinking blocks, refusal, max_tokens and typed errors.
6. API and contract tests.
   - A TestClient sweep over every route asserts strict JSON (no NaN or Infinity tokens), the error envelope, and the 4xx/5xx codes.
   - An openapi.json snapshot test.
   - An import-graph and AST test: only execution.py imports brokers; claude_integration never imports execution; routes and main import only services.
   - Parity tests: CLI and API return identical results on the same fixtures.
7. Cross-check: classic engine versus backtesting.py on the same signals, with trade count equal and total return within 1%.
8. Opt-in live tests (`-m live`, explicit env flag): one Alpaca paper bracket order, `check-llm`, and the LLM judge eval with a cost printout. Never in CI by default.
9. Reproducibility: same seed and data give byte-identical report JSON; the lock reproduces versions.

FRONTEND
- Vitest with React Testing Library and MSW shared handlers:
  - units and formatting (fractions shown as percent; null, NaN and Inf shown as 'n/a')
  - field names matching the generated types
  - error, empty and loading states for each page
  - ErrorBoundary fallback
  - TanStack request-count, dedupe and abort behaviour
  - useJob polling stopping at a terminal status
  - no script injection from the briefing
- Compile-time contract: generated schema.d.ts, `tsc -b`, `no-explicit-any` as an error, and an api:check drift gate.
- Playwright smoke (2-3 specs): visit the 6 routes against mocked APIs and fail on console errors or unhandled rejections.

CI GATES
- Backend PR: `uv sync --frozen`, ruff, fast suite (<60s), bug-index consistency.
- Backend nightly: the slow statistical suite.
- Frontend PR: npm ci, api:check, lint, typecheck, vitest, build, Playwright smoke.
- Each phase's exit criteria are expressed as the named tests above passing in CI.

### Questions for the owner

1. Is this an unattended pipeline from signal to paper or live order, or a research screen with alerts? The plan makes deflated validation a hard gate for orders. Expect few or zero orderable signals after the fixes, which is probably the honest result. Is that acceptable?
2. Which asset classes should ever be executable on Alpaca: US equities only, or crypto too? Crypto has no shorting and no bracket orders. FX and futures are planned as research-only. Should futures pairs (GC/SI, CL/NG) be dropped from the default scan?
3. Should SELL signals ever open shorts (ALLOW_SHORTS)? Is margin enabled on the paper account? The default in the plan is that -1 means exit the long only.
4. What risk numbers do you want? Per-trade risk % of equity, max order notional, max % per symbol, max gross exposure, max open positions, max orders per day, daily-loss halt %, drawdown halt %. The current 5% / 15% / 10% and $1000 values are placeholders.
5. For pairs, is MAX_POSITION_SIZE_PCT meant as gross exposure across both legs or per leg?
6. Should the strategy run on daily bars only, or are intraday timeframes planned? Execution timing, calendars, data quotas and ML embargo sizes all depend on this.
7. How do scheduled scans run: a long-lived `main.py schedule` process, external cron, or CI? Can two runs ever overlap? Where will the API and frontend be deployed: local only, or on a server? That decides whether the bearer token and CORS work matters.
8. Are the repos on GitHub (for GitHub Actions), or should CI be a local `make check`? Is Python 3.12 acceptable as the target runtime (the sandbox has 3.11)?
9. Should the Streamlit dashboard be retired once the React app reaches parity, or kept as a thin read-only client?
10. LLM: is claude-opus-5-5 at medium effort acceptable as the default briefing model? Are hard caps of about $0.50 per run and $2 per day acceptable? Is the server-side refusal fallback acceptable? Should the LLM ever be allowed even a reduce-only exposure multiplier (default: no)? Do you want the Opus-orchestrator / Sonnet-worker variant evaluated later?
11. Do you hold any paid data plans (Alpaca SIP, Alpha Vantage premium, Massive/Polygon, Tiingo)? The plan assumes free Alpaca plus yfinance and drops Alpha Vantage. Will the data or frontend ever be shared beyond personal use? yfinance's terms make it personal and research use only.
12. Should patterns be tuned per symbol (larger N for DSR) or kept as global fixed rules?
13. For an ambiguous order submit (a timeout), should the system look up by client_order_id and retry once with the same ID automatically (the default in the plan), or alert and wait for you?

## Appendix A: research by area

### backtest-validity
**Current state.** Core engine is backtester.py::classic_backtest (a bar loop) plus an optional VectorBT overlay. Weaknesses found, with file:line. NOTE: line numbers for backtester.py are from my read of the file; the earlier audit's numbers (247, 251, 296-297, 310-311, 368-380) were not all re-verified, so confirm them before editing.

LOOK-AHEAD AND EXECUTION
- patterns.py signals are computed on the bar-t Close, including Donchian/volume/ATR breakouts (patterns.py:208-209, 220-221, 231-232) which test Close against a prior channel. classic_backtest reads `signal` at row i and enters at that same bar's close (`entry_price=close*(1+slippage*signal)`, in the `if position is None and signal != 0` block). This is same-bar entry: the strategy trades at a price it could only know at the close, with zero latency.
- The stop-loss/take-profit check never runs on the entry bar, because the exit block executes before the entry block. Intrabar excursions on that bar are ignored, which flatters results.
- SL/TP fill exactly at the barrier price (`exit_price = entry*(1-stop_loss)`). If the bar gaps through the level, the real fill is the open, which is worse for stops. The engine also checks SL before TP when both are hit on one bar, which is fine as a pessimistic tie-break but is never made explicit.
- A signal of opposite sign closes the position and immediately opens a new one on the same bar. Because exits are processed before entries, the exit and re-entry both fill at that bar's close.
- A repeated same-direction signal while in a position is silently dropped.

COSTS AND SIZING
- Commission is charged as `2*commission` on the pnl_pct. Slippage is applied to entries and signal exits but not to SL/TP fills. There is no gap-aware, spread or volatility-scaled cost, no borrow cost or shorting constraint, and no overnight financing.
- pnl_abs = capital*MAX_POSITION_SIZE_PCT(5%)*pnl_pct, but `pnl_pct` (and therefore win_rate, profit_factor, expectancy and avg_win) is reported at 100% notional. The two are inconsistent. The monte_carlo_analysis in stress_test.py:156 compounds the full `pnl_pct` as if 100% of capital were bet each trade, which overstates the return and drawdown distribution by about 20x.

EQUITY CURVE AND RISK METRICS
- Equity is appended once per bar but `capital` only changes on exits. The curve is a step function with no mark-to-market. Max drawdown, Sharpe and Sortino are therefore computed on mostly-zero returns, and intra-trade drawdown is invisible.
- Sharpe subtracts the full risk-free rate per bar (`RISK_FREE_RATE/252`, so 5% annual) from returns that come from only 5% of capital being deployed. Sharpe is negative or tiny for nearly all strategies, so `is_valid` (config.MIN_SHARPE=0.3, MIN_WIN_RATE 0.52, MIN_PROFIT_FACTOR 1.15, MIN_TRADES 8) almost never passes. There is a second effect: when the pass rate is near zero, someone will relax the thresholds, which is a data-snooping path.
- Sortino divides by std of negative returns only, using a `+1e-10` hack. The standard form is downside deviation computed as sqrt(mean(min(r-MAR,0)^2)) over all observations.
- Calmar annualisation is linear: `total_return*(252/len(df))`. It should use CAGR.
- `bars_held = i - trades[-1].bars_held` is wrong, because it subtracts the previous trade's bars_held rather than an entry index. Trade has no entry_index.
- profit_factor with no losses is gross_profit/1e-10, about 1e9. This is the known source of unbounded paper-order size, and it also corrupts any ranking.
- A trade with pnl exactly 0 or a negative value is counted as a loss (`p <= 0`). Equity curve slicing `equity[:len(df)]` can drop the final point, and the open-trade `end_of_data` close is booked at an unslipped close.
- The `equity` list starts with `[capital]` and then appends once per loop starting at i=1, so its length is len(df)+1. The `index=df.index[:len(equity)]` alignment then silently truncates the final point.

VECTORBT OVERLAY
- backtest_pattern (backtester.py:~339) calls vbt_backtest_signals(df, df), which runs `from_signals(close, entries, exits)` on the same bar's close with no `delay`. It then overwrites Sortino and Calmar on a result whose trades come from a different engine and different cost, sizing and timing assumptions. This mixes two incompatible accounting conventions into one result.
- requirements.txt pins `vectorbt>=0.26.0`. PyPI now serves vectorbt 1.1.1 (2026-09-26), which requires numpy>=2.4.6, pandas>=3.0.3 and Python>=3.11, and is a new major version. The 0.2x API used here (`vbt.MA.run_combs`, `ma_crossed_above`, the `pf.stats()` key strings) was not checked against 1.x and may have changed. The blanket `except Exception` returns None, so a silent API break would just disable the overlay.
- `from backtesting import ...` is imported but unused.
- The HAS_VBT guard is the only thing preventing a hard dependency.

WALK-FORWARD (backtester.py:~348-384)
- walk_forward_validate never fits anything. It takes the test slice df.iloc[train_end:test_end] and calls the parameter-free `pattern_func` on it. The 'train' portion is discarded, so this is merely a backtest on 4-6 sub-windows with indicator warm-up restarted from scratch inside each short slice (e.g. SMA-200 is all-NaN in a ~100-bar slice). It gives no out-of-sample guarantee for anything because nothing is selected in-sample.
- With 20 patterns, 39 symbols and SL/TP sweeps, the actual selection step (`is_valid` filter in main.py:96) happens on the full sample, so every reported 'valid' result is selected in-sample.

SELECTION BIAS / MULTIPLE TESTING (main.py:83-130)
- scan_technical loops symbols x PATTERN_REGISTRY (20 patterns; 39 symbols means about 780 hypotheses) and filters on win_rate>=0.52, PF>=1.15, Sharpe>=0.3, trades>=8. There is no multiple-testing control. The top-ranked 'winner' of about 780 noisy trials will usually look good by chance: the expected maximum Sharpe of N independent null trials grows roughly like sqrt(2 ln N). MIN_TRADES=8 is far too small for any significance claim (the standard error of a win-rate over 8 trades is about 17 percentage points).
- check_recent_signal (main.py:55-63) scans the last VALIDATION_WINDOW_DAYS and returns BUY if any buy appears, even if a SELL came later. That is a stale or contradictory-signal bug rather than a statistics bug, but it feeds paper trading.
- In-sample backtest results feed straight into paper-order confidence (main.py:123, confidence = win_rate*profit_factor), so selection bias directly drives order sizing. Combined with the PF~1e9 case, size is effectively unbounded. This is the P0 safety link between backtest validity and live orders.

STRESS TEST (stress_test.py)
- detect_regimes (26-61) uses `rank(pct=True)` over the whole sample, which uses future data to define 'high vol', 'bull' and so on. It is look-ahead in the regime labels. Percentile cuts 0.25/0.75 also mean exactly 25% of bars are high-vol in every symbol by construction. The first `window` bars are NaN and pd.cut leaves them unlabelled, which is fine, but the pd.cut `bins=[0,...]` excludes a pct rank of exactly 0.
- stress_test_regimes (105-110) does `df[mask]` and runs `pattern_func` on the boolean-filtered frame. That stitches non-contiguous bars together: indicators, crossovers and shift(1) comparisons span gaps of weeks or years, and classic_backtest holds positions 'across' the splice. The regime results are artefacts, not tests.
- monte_carlo_analysis (128-199): i.i.d. bootstrap of trade pnl (a) at 100% notional (see above), (b) ignores serial dependence, (c) ignores that trades overlap or are concurrent across symbols, (d) takes a Sharpe annualised with sqrt(252/n_trades), which is dimensionally wrong (trades per year is unknown), and (e) only measures sampling noise of the same selected trades, so it says nothing about selection bias. `prob_positive` reflects the bootstrap, not the probability the edge is real. Seeds are not set (np.random.choice), so results are non-reproducible.
- parameter_sensitivity (206-245): sweeps SL and TP on the full sample and reports `is_robust = std(sharpe) < 1.0`. That threshold is arbitrary and, because all Sharpes are compressed by the rf bug, nearly always true. It does not perturb the signal parameters (EMA windows, RSI thresholds), which is where overfitting lives. The grid also creates 8x8=64 more trials per pattern.
- _check_regime_consistency counts regimes with total_return>0 and PF>1, with no minimum trade count per regime.

DATA/ENV
- pandas is unpinned (`pandas>=2.0.0`). Under pandas 3.x, `~bullish.shift(1).fillna(False)` (patterns.py:40-41, 142-143) changes semantics (object-dtype downcasting of fillna was deprecated and removed), which can silently change which bars signal.
- patterns.py:164 `squeeze.shift(1)` on a boolean with NaN rows yields object dtype and `&` can then misbehave.
- There are no tests in the repo, so none of the above is guarded.

Pattern-level leakage check: every pattern function in patterns.py uses only trailing windows, so there is no centre-window or future-shift leakage inside patterns.py itself. The leakage is in the engine timing contract (a signal known at close fills at that close) and in the selection and evaluation protocol, not in the indicators.

**Best practices**

- Next-bar execution contract: a signal computed from data through bar t is executed at bar t+1 open (or later), and the engine, not the pattern, enforces this. Stops are evaluated from the bar after entry; entry-bar intrabar range is evaluated against the stop. Gap-aware fills: stop fill = min(open, stop) for longs (max for shorts), target fill = max(open, target) only if the open gaps through it, else the target.: Same-bar fills are the single most common source of inflated backtests. Gap-aware fills remove optimistic stop prices. Both vectorbt (via `from_signals`'s signal delay / shifting entries by one bar) and backtesting.py (`trade_on_close=False` default) treat next-bar open as the default. (https://kernc.github.io/backtesting.py/doc/backtesting/backtesting.html; https://vectorbt.dev/)
- Build the equity curve mark-to-market every bar: equity_t = cash + position*close_t, with returns computed from that series, sized by actual deployed notional. Compute Sharpe on daily excess returns with a consistent rf (subtract rf only on the capital actually earning it, or use rf=0 and say so), Sortino with proper downside deviation against a MAR, CAGR-based Calmar, and max drawdown on the MTM curve (also report max drawdown duration). Also report the Probabilistic Sharpe Ratio with skew and kurtosis, because Sharpe estimates from short samples have a large standard error.: A step-function equity curve makes Sharpe, Sortino and drawdown meaningless. Sharpe's standard error with ~100 daily observations is huge, so a point estimate must come with a confidence statement. (https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1821643; https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- Control multiple testing across the whole 20 patterns x 39 symbols (x parameter) search: count every trial (N), and report the Deflated Sharpe Ratio (Bailey and Lopez de Prado 2014), which deflates the observed best Sharpe by the expected maximum Sharpe across N trials, using the variance of trial Sharpes and the skew and kurtosis of returns. As cheaper alternatives, use a family-wise or FDR correction (Bonferroni/Holm, Benjamini-Hochberg) on per-strategy p-values, or the White Reality Check / Hansen SPA / Romano-Wolf stepdown via a stationary bootstrap on the matrix of all strategy returns.: With ~780 trials, a handful will clear Sharpe 0.3 and PF 1.15 on noise alone. DSR is the lightest-weight defensible correction and fits a one-person project. White and SPA use the full joint distribution so they account for correlation among related patterns. (https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551; https://www.ssrn.com/abstract=2326253; https://www.econometricsociety.org/publications/econometrica/2000/09/01/reality-check-data-snooping; https://arch.readthedocs.io/en/latest/multiple-comparison/introduction.html)
- Estimate Probability of Backtest Overfitting (PBO) using Combinatorially Symmetric Cross-Validation on a (time x trials) matrix of returns: partition into S blocks, for every in-sample/out-of-sample split pick the in-sample best trial, record its out-of-sample rank, and report the fraction of splits in which it falls below the median. Requires the full returns matrix for every candidate, so it is natural once all pattern x symbol returns are stored as columns.: PBO directly answers 'does picking the best in-sample pattern predict out-of-sample performance in my own search process', which is exactly the scan_technical workflow. (https://www.ssrn.com/abstract=2326253; https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)
- Use purged and embargoed validation when labels or trades span multiple bars: purge training observations whose label or holding window overlaps the test window, and embargo a gap after the test window. For stateless rule-based patterns with no fitted parameters, simple walk-forward (select on trailing window, evaluate on next) with an honest out-of-sample hold-out is enough; reserve purged k-fold or CPCV (combinatorial purged CV) for the ML models, where features use overlapping lookbacks and the label horizon is multi-bar.: Random or plain k-fold on overlapping-label financial data leaks information. CPCV additionally yields a distribution of out-of-sample paths rather than one lucky path. Proportionality: CPCV is overkill for fixed-rule patterns. (https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html; https://en.wikipedia.org/wiki/Purged_cross-validation; https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)
- Monte Carlo done right: bootstrap with a block or stationary bootstrap on the daily strategy return series (not i.i.d. trade pnl), use the actual sizing, set a seed, and report percentile bands for CAGR, max drawdown and Sharpe. Separately, run a permutation or random-entry null test (shuffle signal timing or use random signals with the same trade count, holding period and exit rules) to get a p-value for 'is the pattern better than random entries', since bootstrap of realised trades only quantifies sampling noise around a possibly spurious result.: The existing MC conflates sampling uncertainty with edge significance, ignores autocorrelation and mis-sizes trades. A random-entry null is cheap and interpretable. (https://arch.readthedocs.io/en/latest/bootstrap/timeseries-bootstraps.html; https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- Regime analysis without stitching: compute regime labels causally (rolling or expanding-window thresholds known at time t, shifted one bar), run the strategy once over the full contiguous history, then attribute each bar's or trade's P&L to the regime in force at entry. Never filter the DataFrame and re-run the strategy on the result.: Attribution preserves indicator warm-up and position continuity and removes both look-ahead in the regime definition and the artefacts from joined gaps. (https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- Minimum evidence and cost realism: require a sensible minimum trade count (dozens, not 8) and report confidence intervals for win rate, expectancy and profit factor; model costs as a spread/half-spread plus slippage that scales with volatility or ATR, apply them to all fills including stops, and run a cost-stress (2x and 3x costs) as a standard pass/fail check. Use unadjusted OHLC or consistently adjusted OHLC with corporate actions.: Edges of a few basis points per trade vanish under realistic costs; low trade counts give uninformative statistics. (https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551; https://kernc.github.io/backtesting.py/doc/backtesting/backtesting.html)
- Reproducibility and regression tests: pin dependencies, keep a golden-file test with hand-computed expected trades on a tiny synthetic OHLC series, plus property tests (no trade enters on the signal bar, equity at the end equals initial capital plus sum of trade pnl, drawdown is within [-1, 0], a random-walk price series yields Sharpe about 0 and DSR below a threshold).: There are no tests today; for a backtester a handful of known-answer tests catches the whole class of timing and accounting bugs. (https://hypothesis.readthedocs.io/en/latest/)

**Tool options**

| Tool | 2026 status | Recommended | Pros | Cons |
|---|---|---|---|---|
| Hardened in-house engine (extend classic_backtest + new validation module) | Own code, about 300-500 lines for engine fixes plus about 200 for DSR/PBO/bootstrap; only numpy, pandas and scipy (already in requirements) and optionally `arch` (already listed) for stationary and block bootstraps and the SPA/Romano-Wolf multiple-comparison tests. | yes | Fits the existing signal-DataFrame contract (pattern -> 'signal'); full control of the timing contract, gap fills and sizing; no new dependency or API churn; the validation layer (DSR/PBO/null tests) is engine-independent anyway; easy to unit test; proportionate for one owner. | You own the bugs; per-bar Python loop is slow for big sweeps (acceptable: ~780 daily series, or use numba on the hot loop); shorting, margin and portfolio-level interactions are only as good as you write them. |
| vectorbt (open source) | PyPI shows 1.1.1 released 2026-09-26, requires Python >=3.11, numpy>=2.4.6, pandas>=3.0.3 (<4), numba>=0.66; license Apache 2.0 with Commons Clause. A new major version versus the 0.2x API this repo was written against, so the existing vbt calls need re-verification. The search-engine snippet that called it 'sustainable' came from third-party aggregator pages, so rely on the PyPI metadata above. | no | Fast vectorised sweeps and built-in stats and a deflated_sharpe_ratio helper (as reported by search results; verify against 1.x docs); good for parameter-grid exploration. | Forces pandas 3 and numpy 2.4 (a hard environment constraint, also a risk because the repo's patterns.py fillna semantics change under pandas 3); API break from 0.2x; the same-bar vs next-bar semantics must be set explicitly; Commons Clause restricts commercial redistribution; the overlay currently blends two accounting conventions. Does not fix the selection-bias problem. |
| backtesting.py (kernc) | PyPI 0.6.6 released 2026-07-22, Python >=3.9, numpy>=1.17, pandas>=0.25 (loose); actively released (0.6.3 March 2025, 0.6.4 March 2025, through 0.6.6 in July 2026). | yes | Simple, well documented, next-bar-open default, supports SL/TP and a built-in optimiser with heatmaps; small and readable; light dependency footprint; useful as an independent cross-check engine for validating your own engine on the same signals. | Single instrument per run; strategies are class-based so the signal-DataFrame contract needs an adapter; position sizing and portfolio features limited; AGPL-3.0 license; the built-in optimiser invites overfitting unless wrapped by DSR/PBO. |
| nautilus_trader | PyPI 1.231.0 (uploaded 2026-08-02), Python >=3.12,<3.15, pandas>=2.3.3,<4; v2.0.0 release candidates (rc3, rc4) are reported by release-tracker pages (not verified against GitHub), implying breaking API changes ahead. | no | Production-grade event-driven engine with realistic order types, OCO/OTO contingency orders, fee and fill models, identical research and live code; the right choice if this ever becomes a live multi-venue system. | Heavy: Rust-native, steep learning curve, data catalogue and instrument model, frequent breaking releases (1.x to 2.0). Large overkill for daily-bar pattern scans on 39 symbols. |
| zipline-reloaded | PyPI 3.1.1 uploaded 2025-07-19 (no newer release visible as of Oct 2026, so about 14 months stale); requires pandas<3.0 and Python 3.10-3.13 per PyPI metadata. | no | Mature pipeline API, bundle ingestion, calendar handling, realistic slippage and commission models. | Appears low-activity; pins pandas<3.0 which conflicts with vectorbt 1.x; bundle/ingest workflow and trading-calendar machinery are heavy for yfinance data; weak fit for crypto and futures symbols. |
| Validation helpers: arch, skfolio, jsharpe / sharpebench-style DSR packages | arch is already in requirements.txt (bootstrap, SPA, StepM, Romano-Wolf in arch.bootstrap); skfolio provides CombinatorialPurgedCV (n_folds, n_test_folds, purged_size, embargo_size); small DSR packages exist on PyPI (jsharpe, sharpebench) but are low-profile; mlfinlab is commercial/closed. | yes | Avoids reimplementing statistics: `arch.bootstrap.StationaryBootstrap` / `SPA` / `StepM` for data-snooping tests; skfolio CPCV splitter for the ML track. | DSR and PBO are about 30 lines each from the papers' formulas and are easier to own and unit test than to trust an obscure package; skfolio is a heavy install for one splitter (the splitter can be copied or reimplemented in about 40 lines). |

**Recommendations**

| Priority | Effort | Change | Files | Acceptance test |
|---|---|---|---|---|
| P0 | S | Cap profit_factor (e.g. min(PF, 10) or None when no losses) and emit explicit `profit_factor_capped`; never use win_rate*profit_factor as an order-sizing input. Gate every paper order on a validated, deflated result (see DSR rec) plus a hard position-size clamp from RiskManager. | backtester.py, main.py, paper_trader.py | Unit test: a backtest with 10 winning trades and 0 losers returns profit_factor <= the cap and finite; scan_technical with a mocked result of PF=1e9 produces an order qty <= MAX_POSITION_SIZE_PCT*equity/price. |
| P0 | M | Fix the timing contract in classic_backtest: signal at bar t is executed at bar t+1 open (add `execution='next_open'` default; keep `'close'` only as an explicit opt-in). Evaluate SL/TP starting on the entry bar using that bar's high/low after entry at the open; fill stops at min(open, stop) for longs (max for shorts) and take-profits at max(open, target) only if the bar gaps through; apply slippage and half-spread to stop fills too. Make the flip (reverse) case two separate fills at t+1 open. | backtester.py | Golden-file test on a hand-built 8-bar OHLC with a known signal: entry price equals the next bar's open*(1+slippage), a bar opening 5% below the stop fills at the open, not at the stop price, and no trade has entry_date equal to a signal bar's date. Property test: shifting the signal series by one bar later never changes entry bar index by less than one. |
| P0 | M | Replace the step-function equity with a mark-to-market per-bar equity curve computed from the position's notional (capital*MAX_POSITION_SIZE_PCT) and bar closes; compute Sharpe and Sortino on daily excess returns of that curve using rf applied only to the deployed fraction (or rf=0 documented), Sortino with true downside deviation, CAGR-based Calmar, max drawdown on MTM equity, and fix the equity length/index alignment and the bars_held bug (store entry_index on Trade). Report trade pnl at consistent sizing (return on equity) as well as per-notional. | backtester.py, config.py | Test: for a buy-and-hold single trade over a known price path the equity curve equals initial*(1+size*(P_t/P_0-1)) at every bar; drawdown equals the analytic value; a flat zero-trade curve gives Sharpe 0 (not NaN); random-walk prices over 1,000 simulated series give mean Sharpe within +/-0.2 of 0; len(equity_curve)==len(df) and bars_held equals exit_index-entry_index. |
| P0 | M | Remove the non-contiguous stitching in stress_test_regimes: compute regimes causally (expanding or rolling thresholds, shifted one bar), run the pattern and engine once on the full contiguous frame, then attribute trades and daily returns to the regime at entry (or per bar). Drop regimes with fewer than N trades. Likewise make Monte Carlo use block/stationary bootstrap on daily MTM returns at real sizing, seeded. | stress_test.py, backtester.py | Test: detect_regimes labels on bars 0..k are identical whether computed on df[:k+1] or on the full df (no look-ahead); stress_test_regimes never calls the pattern function on a frame whose index has gaps larger than the median spacing (assert via a spy); MC with a fixed seed returns identical output twice and its return_ci scales with position size (5% size gives about 1/20 the dispersion of 100%). |
| P0 | S | Fix check_recent_signal to return the most recent non-zero signal inside the window (not 'BUY if any buy') and require that the signal bar is the latest completed bar (or at most 1 bar old), consistent with next-bar execution. Add a de-duplication key (symbol, pattern, signal_bar_date) before alerts and orders. | main.py | Test: a window with [+1, 0, -1] returns -1; a window with [-1, 0, +1] returns +1; calling the scan twice on the same data produces one order/alert per (symbol, pattern, date). |
| P1 | M | Add a `validation.py` (new, engine-independent) that takes a (time x trials) matrix of daily strategy returns for every symbol x pattern (and SL/TP) candidate and computes: PSR, DSR with N = number of trials actually evaluated and the cross-trial Sharpe variance, PBO via CSCV, and a stationary-bootstrap SPA or Romano-Wolf test using `arch`. Have scan_technical record every trial (including failed `is_valid` ones) and make `is_valid` require DSR (or SPA p-value / BH-FDR q-value) in addition to the point thresholds, with MIN_TRADES raised to a statistically meaningful level (30+ ) and a CI on win rate. | validation.py (new), main.py, backtester.py, config.py | Simulation test: 780 i.i.d. zero-mean noise return series of 750 days each; the best raw Sharpe exceeds 0.3 in the large majority of runs, but DSR p-value of the best is not significant in >=90% of runs (and PBO is about 0.5); injecting one series with true annual Sharpe 1.5 is retained (DSR significant) in >=80% of runs. |
| P1 | M | Make walk_forward_validate real: expanding or rolling windows on the full contiguous series with indicator warm-up included in the data passed in, selection performed only on the train window (e.g. choose pattern/params or the top-k by in-sample score), then evaluated on the next untouched test window with the position carried across; stitch out-of-sample daily returns into one OOS equity curve and report OOS Sharpe/DSR. Add a final locked hold-out (last 15-20% of history) never used for any selection. | backtester.py, main.py | Test: tampering with data in a test window after the selection date does not change the chosen pattern/params for that fold; OOS returns do not overlap between folds; on a pure random-walk series the OOS Sharpe averaged over 100 seeds is within noise of 0 while the in-sample best Sharpe is clearly positive (demonstrates the selection bias being measured). |
| P1 | S | Add a null/permutation test: random-entry baseline with the same number of trades, same holding-period and SL/TP rules, 1,000 seeded draws; report the p-value for the strategy's total return or Sharpe vs the null. Add a cost-stress (2x, 3x commission and slippage) and a signal-delay stress (entry one extra bar late) to the report; treat a sign flip or collapse under 2x costs as fail. | stress_test.py, backtester.py | Test: a strategy that buys at random yields null p-value roughly uniform over 200 seeded runs (mean about 0.5); a synthetic series with a planted 1-bar-ahead predictive signal shows p<0.01 at 1-bar delay and no significance at +5 bar delay; 2x cost reduces total return by the expected analytic amount. |
| P1 | S | Dependency and environment pinning: add a lock (pip-tools/uv) with pandas pinned to a tested major (2.x or 3.x, not open), numpy, scipy, and decide on vectorbt: either drop it from the default path or pin it and adapt calls to 1.x. Fix `.shift(1).fillna(False)` boolean idioms in patterns.py (e.g. `.shift(1, fill_value=False).astype(bool)`) and explicitly cast `squeeze`/`expansion`. Add a CI step running the golden tests under the pinned pandas. | requirements.txt, patterns.py, backtester.py | CI job: `pip install -r requirements.lock` then pytest passes; a test runs every PATTERN_REGISTRY function on a fixed 500-bar fixture and compares signal counts to a committed golden JSON, identical under pandas 2.x and 3.x if both are supported. |
| P1 | S | Stop mixing engines in backtest_pattern: make the classic engine the single source of truth for all metrics; keep vectorbt (if retained) only as an optional cross-check or sweep tool with `delay`/next-bar semantics aligned, reported separately. Alternatively add a backtesting.py cross-check test that runs the same signals with trade_on_close=False and asserts trade count and total return agree within a tolerance. | backtester.py, tests/test_crosscheck.py (new) | Cross-check test: on a fixed 500-bar fixture and ema_crossover signals, classic_backtest and backtesting.py (same costs, same next-open fills, same sizing) produce the same number of trades and total return within 1%; backtest_pattern returns no field sourced from a different engine. |
| P1 | S | Reproducibility and logging of the research process: seed all RNG (np.random.default_rng(seed)), record number of trials N, data range, config hash and package versions in every report; return structured results (finite floats, None instead of inf/NaN) from summary() rather than formatted strings so downstream code, JSON endpoints and DSR consume numbers. | backtester.py, stress_test.py, main.py, config.py | Test: running a scan twice with the same seed gives byte-identical report JSON; json.dumps(result.summary(), allow_nan=False) succeeds for zero-trade, all-win and all-loss cases. |
| P1 | M | Fix the ML evaluation path in the same framework (ml_patterns.py is outside this file list but is gated by the same engine): fit scaler inside each fold, use purged+embargoed splits (embargo >= label horizon) such as a small in-house purged TimeSeriesSplit or skfolio's CombinatorialPurgedCV, and backtest only on out-of-fold predictions. | ml_patterns.py, main.py, backtester.py | Test: with labels as a future-return sign, shuffling future prices after a fold's test end does not change that fold's predictions; the maximum training index is at least (horizon + embargo) bars before the minimum test index in every fold; on random-walk prices OOS accuracy averages about 0.5 over seeds. |
| P2 | M | Realism upgrades: ATR/volatility-scaled slippage, optional half-spread by asset class, ADV participation cap for sizing, overnight/borrow cost for shorts, trailing stops and time stops, and vol-targeted position sizing as an alternative to flat 5%. Use consistently adjusted OHLC (adjust Open/High/Low by the same factor as Close) or raw prices with explicit dividends. | backtester.py, config.py, data_fetcher.py | Test: with ATR-scaled slippage, a high-vol fixture yields strictly lower net return than a low-vol one at equal gross edge; adjusted O/H/L/C satisfy Low <= Open,Close <= High after adjustment on a split fixture. |
| P2 | M | Add CPCV or at least multi-path walk-forward for the ML models and a parameter-neighbourhood robustness test (perturb signal parameters such as EMA windows and RSI thresholds, not only SL/TP) that requires performance to degrade smoothly; replace the arbitrary `std(sharpe) < 1.0` rule with the share of neighbouring parameter sets whose OOS Sharpe is positive. | stress_test.py, patterns.py, ml_patterns.py | Test: an overfit planted-parameter strategy (edge only at one exact window value) is flagged non-robust while a smooth-edge strategy is flagged robust; the sensitivity report includes the signal-parameter grid and its trial count feeds N in DSR. |
| P2 | S | Do not adopt nautilus_trader or zipline-reloaded for this project; revisit nautilus_trader only if the project moves to live multi-venue intraday execution. Optionally adopt backtesting.py purely as a cross-check engine (see P1) and treat vectorbt 1.x as optional exploration tooling pinned in a separate requirements file. | requirements.txt, docs/decisions.md (new) | requirements.txt contains no unused backtest engine in the default install; a one-paragraph ADR documents the decision and the revisit trigger. |

**Open questions**

- Which bar frequency is the production target (daily only, or intraday)? Next-open fills, gap handling and costs change materially for intraday, and nautilus_trader becomes more defensible there.
- Is the goal a research screen (rank candidates) or an unattended signal-to-order pipeline? If orders are unattended, the P0 gating and DSR/hold-out requirements should be hard blockers; if advisory, they can be reported as warnings first.
- Are patterns meant to be tuned per symbol (more trials, higher N for DSR) or fixed globally? This sets N and whether walk-forward must select parameters.
- Do you intend to keep shorting and futures/crypto symbols in the backtest? Those need borrow/financing, contract multipliers, session calendars and non-equity data sourcing (the Alpha Vantage '=F' stripping issue) before results are trustworthy.
- I could not directly verify vectorbt 1.x API compatibility with the current `vbt.MA.run_combs`/`pf.stats()` calls, the claim that vectorbt exposes a deflated_sharpe_ratio helper, or the reported nautilus_trader 2.0.0rc releases (sourced from release-tracker aggregator pages). The PyPI versions, dates and dependency constraints quoted come directly from PyPI JSON; the earlier audit's line numbers for backtester.py (247, 251, 296-297, 310-311, 368-380) were not all matched against my read of the file and should be re-checked.
- Do you want the DSR/PBO layer to be a hard gate on `is_valid` or an advisory annotation initially? A hard gate will likely produce zero 'valid' patterns after the Sharpe and cost fixes, which is probably the honest answer but changes alert volume.

### ml-pipeline
**Current state.** Read in full: ml_patterns.py, features.py, routes/ml_routes.py, main.py:100-260, config ML_* and requirements.txt. The pipeline as built cannot produce a trustworthy out-of-sample number anywhere.

LEAKAGE AND VALIDATION
- ml_patterns.py:123-127 fits StandardScaler on the full dataset before TimeSeriesSplit at :130, so every fold's test statistics leak into training. The scaler is also pointless for RF/XGB/LGBM, which are scale-invariant.
- :130 TimeSeriesSplit(n_splits=5) has no gap, embargo or test_size. Labels look 1-5 bars ahead, so the last training labels overlap the first test bars.
- :142 scores plain accuracy only. There is no majority-class or buy-and-hold baseline, no log-loss/Brier/AUC, and no per-fold probability or trading metric.
- main.py:~190 gates trades on mean_cv_accuracy >= 0.52. That is a noisy, selected statistic with no confidence interval. If train() returns {"error": "insufficient_data"}, .get(...,0) silently skips the symbol.

LABELS
- features.py:143-149: target_1d is NaN on the last row (shift(-1)) and target_3d on the last 3 rows. `(x>0).astype(int)` turns NaN into 0. target_up_1d is therefore never NaN, so the dropna at ml_patterns.py:113 does not remove those rows. The final, unresolved bars enter training labelled "down".
- Exact-zero returns are also labelled 0. The label is a raw fixed-horizon sign with no volatility scaling or neutral zone.
- Overlapping labels at 3d/5d horizons are not purged and get no sample weights.
- The scan only exposes 1d/3d targets, though 5d exists.

IN-SAMPLE RESULTS PRESENTED AS OUT-OF-SAMPLE
- ml_patterns.py:241-242 trains on the first 75% but returns predict(df) for the FULL range. The training segment's signals are in-sample.
- routes/ml_routes.py:26 and :49-50 backtest those full-range predictions and report is_valid/metrics. The feature importance at ml_patterns.py:147-164 comes from the final model fit on all data.
- main.py:183-204 trains on all of df (train(df) at :184), predicts on the same df (:190) and backtests it (:204).
- There is a single split, no repeated walk-forward, and ML_RETRAIN_DAYS (config.py:107) is not wired into the logic in the files read.

PROBABILITIES AND THRESHOLDS
- :58-100 builds a soft-voting ensemble of uncalibrated models. class_weight='balanced' is set on RF and LGBM but not XGB, so the averaged probabilities are inconsistently skewed. The 0.60/0.40 thresholds (:217-218, config.py:108) are therefore arbitrary.
- No calibration is done. There is no reliability check.
- :208 picks probas[:,0] if only one class is present, which silently inverts meaning.
- main.py:201 passes P(up) as `confidence` to execute_signal even for SELL signals (low P(up) = strong SELL), so SELL sizing is inverted. This stacks with the unbounded-qty issue already known.

FEATURES
- Several features are non-stationary or window-dependent:
  - raw price-scale macd, macd_signal, macd_hist and atr_7/14/21 (:59-61, :95)
  - raw volume level vol_sma_* (:107)
  - cumulative-from-start VWAP (:115), which depends on where the DataFrame starts, so train and serve features differ
  - consec_bullish/bearish run-lengths (:124-128), which depend on the slice start
- `if v.sum() > 0` (:105) changes the feature set per symbol. FX and futures with zero volume get fewer columns, which breaks any shared or persisted model.
- weekly/monthly/quarterly_ret (:131-133) exactly duplicate ret_5d/21d/63d.
- sma_200 dist forces a 200-bar warm-up. With 730 calendar days (~500 bars) that is 40% of the data.
- division can emit inf (vol_ratio at :37-40, log(h/l) at :44 with bad bars). dropna does not remove inf and sklearn will raise.
- `lookback` in compute_features (:18) is unused.
- There is no stability, drift or target-leak check (for example a feature importance sanity check or shuffled-label test).

PERSISTENCE AND REPRODUCIBILITY
- ml_patterns.py:244-265 uses pickle with no versioning. It stores no sklearn/xgboost/lightgbm/pandas versions, training date range, data hash, metrics or feature schema. pickle.load executes arbitrary code and breaks across library versions.
- load() does not check feature_cols against incoming data.
- Nothing calls save/load in the paths read. Every API request (ml_routes.py:25) and every scheduled scan (main.py:183) retrains from scratch, synchronously, in the request thread. A 3-model, 200-tree, 5-fold CV plus final fit runs per symbol.
- random_state=42 is fixed (good). Only n_jobs=-1 thread-order effects could introduce non-determinism, and that is unchecked.
- requirements.txt is all `>=`. Current resolution gives pandas 3.0.x, sklearn 1.9.x, xgboost 3.4 (Python >=3.12). `ta` has had no release since Nov 2023.

API HYGIENE
- ml_routes.py:23 returns errors as HTTP 200 {"error"}.
- :61 hard-codes model_type="ensemble_rf_xgb_lgbm" even when xgboost or lightgbm failed to import (HAS_XGB/HAS_LGBM false).

**Best practices**

- Keep every fitted transform (imputer, scaler, feature selection) inside an sklearn Pipeline, and cross-validate the whole Pipeline. For tree models drop the scaler entirely.: Fitting any statistic on the full sample before splitting leaks test information. A Pipeline refits per fold by construction. (https://scikit-learn.org/stable/common_pitfalls.html (could not fetch: egress blocked; cited from known sklearn docs))
- Use purged and embargoed time-series CV. Purge training rows whose label window overlaps the test fold and add an embargo after it. In plain sklearn, TimeSeriesSplit(gap=horizon+embargo, test_size=fixed) is the proportionate version. Use Combinatorial Purged CV only when you need a distribution of out-of-sample paths.: Overlapping forward-looking labels make adjacent rows share future information. Without purging, CV scores are inflated. (https://en.wikipedia.org/wiki/Purged_cross-validation; https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html; https://github.com/eslazarev/purged-cross-validation; https://arxiv.org/pdf/2507.04176)
- Label with volatility-scaled forward returns, ideally a triple barrier (profit target, stop, vertical time barrier, widths as multiples of rolling vol) with a neutral class or dropped near-zero moves. Give overlapping labels uniqueness weights. Use meta-labeling (a secondary model decides whether to take a primary rule signal and how to size) when a rule-based signal already exists. Never label unresolved tail rows.: A fixed-horizon sign label is noisy and path-blind, and it mismatches how trades actually exit with SL/TP. Meta-labeling lets the existing 25+ rule patterns be filtered rather than predicting direction from scratch. This is the AFML recipe, but the full machinery is overkill for daily bars on a few symbols. Start with vol-scaled fixed horizon plus a dead zone, then triple barrier. (https://www.luxalgo.com/library/concept/label-definition-and-prediction-horizon.md; https://fynance.readthedocs.io/en/latest/features.labels.html)
- Score with proper scoring rules and baselines: log-loss, Brier, AUC, and accuracy minus the majority-class baseline, per fold and in aggregate with a spread. Then score the strategy itself (net of costs, Sharpe, drawdown, turnover) on strictly out-of-sample predictions only. Deflate the Sharpe for the number of configurations tried.: Accuracy near 50% on balanced daily direction is almost indistinguishable from chance. Selecting on a threshold like 0.52 across many symbols and patterns is multiple testing. The Deflated Sharpe Ratio corrects for selection bias and non-normality. (https://papers.ssrn.com/abstract=2460551; https://www.davidhbailey.com/dhbpapers/overfit-tools-at.pdf)
- Calibrate probabilities on held-out, time-ordered data. Use CalibratedClassifierCV with a time-aware cv (TimeSeriesSplit), or calibrate a frozen fitted model on the latest validation block. Prefer sigmoid (Platt) with small samples, because isotonic overfits under about 1000 samples. Calibrate the ensemble output, not each member separately. Then pick thresholds from the calibrated reliability curve and expected value after costs, not 0.60 by fiat.: Boosters and balanced-weight forests output badly scaled probabilities, so 0.60 means different things per model and symbol. Position size and confidence consumers (paper_trader) need real probabilities. Note: cv='prefit' was deprecated in sklearn 1.6 in favour of FrozenEstimator. This is from my recollection of the 1.6 release; I could not fetch the docs, so verify against the installed 1.9.1. (https://scikit-learn.org/stable/modules/calibration.html (could not fetch: egress blocked); https://pypi.org/project/scikit-learn/ (current 1.9.1))
- Make features stationary and window-independent: returns and ratios instead of raw price-scale values (MACD/ATR/volume level), rolling rather than cumulative statistics, a fixed schema (always the same columns, NaN-fill missing volume features). Add cheap stability checks: PSI or KS per feature between train and the most recent window, a minimum-history guard, and an inf/NaN gate before predict.: A persisted model is only valid if the serving features have the same definition and distribution as at training. Drift checks tell you when to retrain or stop trading. (https://en.wikipedia.org/wiki/Purged_cross-validation)
- Persist with metadata. Save a model bundle (joblib, or skops.io for a no-arbitrary-code format) plus a JSON sidecar: library versions, git SHA, symbol, data date range and hash, feature list and schema hash, label spec, seeds, CV metrics and the config. Verify the schema and versions on load and refuse mismatches. Retrain on a schedule (ML_RETRAIN_DAYS) rather than per request. Pin dependencies with a lock file.: Pickle executes code on load and is not stable across versions. Sklearn's own persistence docs recommend skops or ONNX for security and say pickles/joblib must be loaded with the same library versions. I could not fetch that page; it is cited from known docs. skops 0.16.0 was released 2026-09-24 and is beta-status but maintained by sklearn core devs. (https://pypi.org/project/skops/; https://scikit-learn.org/stable/model_persistence.html (could not fetch: egress blocked))
- Ensemble RF+XGB+LGBM only when it measurably beats the best single model out-of-sample on purged CV by more than fold-to-fold noise. On a few hundred to a few thousand daily rows with low signal-to-noise, a single strongly regularised model (shallow LightGBM or ExtraTrees/RF with min_samples_leaf large, or even logistic regression on a few features) is usually as good and far easier to audit. Averaging helps variance, not bias, and the three share the same features and labels.: With ~500 usable rows (730d minus 200-bar warm-up), 200 trees at depth 6-8 on 60+ correlated features will overfit. A simple-model baseline is the honest test of whether any signal exists. (https://arxiv.org/pdf/2507.04176)

**Tool options**

| Tool | 2026 status | Recommended | Pros | Cons |
|---|---|---|---|---|
| scikit-learn (Pipeline, TimeSeriesSplit(gap, test_size), CalibratedClassifierCV, FrozenEstimator, permutation_importance) | 1.9.1 current on PyPI (supports Python 3.11-3.14) | yes | Already a dependency. Covers leakage-free Pipelines, gap-based purging, calibration and scoring with no new tooling. TimeSeriesSplit gap/test_size are in current sklearn. | No true label-overlap-aware purge, only a fixed gap. No triple-barrier or sample-uniqueness tooling. |
| skfolio model_selection (CombinatorialPurgedCV, WalkForward) | 1.4.11 current on PyPI; actively developed, backed by Skfolio Labs, BSD-3; paper arXiv 2507.04176 | no | Maintained CPCV with purging and embargo, sklearn-compatible splitters, gives a distribution of OOS paths. | A portfolio-optimisation library, so heavy dependency for just a splitter. Its CPCV is built around portfolio estimators and may need adapting for classifiers. Worth it only if you want CPCV. |
| Small hand-written PurgedTimeSeriesSplit (about 40 lines) | Own code, no dependency | yes | Uses real label end-times (t1) to purge overlapping rows plus embargo. Fully testable, no maintenance risk. Fits the single-owner scale. | You maintain and unit-test it. |
| purged-cross-validation / purgedcv / temporalcv (small PyPI packages) | Small community packages. I found them via search but did not verify versions or maintenance status. | no | Ready-made splitters. | Unknown bus factor and release cadence. Easy to replace with own splitter. Unverified. |
| mlfinlab / Hudson & Thames | Not verified. Search returned no authoritative current status. I believe the core is now commercial/closed. | no | Full AFML toolkit (triple barrier, meta-labeling, uniqueness). | Licensing and maintenance uncertain. Overkill. Writing triple-barrier labels is about 30 lines of pandas. |
| joblib + JSON sidecar metadata | joblib ships with sklearn's dependencies | yes | Zero new dependency, simple, easy to inspect. | Same pickle security and version fragility as the current code. Only safe for files you wrote yourself, and load must check version metadata. |
| skops.io | 0.16.0 (2026-09-24), beta, maintained by sklearn developers | no | Safe-by-design persistence with an allow-list of trusted types. Good if model files are ever shared or downloaded. | Beta. Support for xgboost/lightgbm estimators needs explicit trusted types and should be tested. Not needed while the owner is the only writer. |
| MLflow | 3.16.1 current on PyPI, very actively maintained | no | Experiment tracking, model registry, UI. | Heavy for one person, one machine and a few symbols. Adds a server and database and more dependencies. A JSON metadata file plus a models/ directory plus a small CSV of runs covers the need. |
| XGBoost / LightGBM / RandomForest | xgboost 3.4.1 (requires Python >=3.12); lightgbm 4.7.0 (Python 3.10-3.14); both current and maintained | yes | All available and maintained. Pinning is simple. | xgboost needing Python >=3.12 can break an older venv when unpinned. Prefer one booster (LightGBM) plus RF as a baseline. |
| ta (technical-analysis library) | 0.11.0, last release 2023-11-02 | no | Works today. | Unmaintained for about 3 years and untested with pandas 3.x. Pin pandas<3 until tested, or replace the few indicators with plain pandas/numpy. Same concern for pandas-ta (not verified). |

**Recommendations**

| Priority | Effort | Change | Files | Acceptance test |
|---|---|---|---|---|
| P0 | M | Stop reporting in-sample results as out-of-sample. In walk_forward_predict return signals only for the held-out segment (zero or NaN the training rows), or better, produce a true expanding/rolling walk-forward prediction series (retrain every ML_RETRAIN_DAYS using only data before each block, predict the next block). Backtest only that OOS series. In routes/ml_routes.py:26,49-50 and main.py:184-204 replace train(df)+predict(df)+classic_backtest(df) with this OOS path. Return oos_start in the response and the metrics. Feature importance should be reported from the fold models or from permutation importance on the OOS block, not the all-data fit. | /home/user/ML-Trading-Platform-Backend/ml_patterns.py, /home/user/ML-Trading-Platform-Backend/routes/ml_routes.py, /home/user/ML-Trading-Platform-Backend/main.py | Unit test on a synthetic random-walk series (no signal): the backtest of the OOS series must not show significant positive Sharpe over 50 seeds (mean Sharpe about 0, and fewer than ~5% of seeds above a 95% threshold). Also assert that every row with signal != 0 has index >= first test index and that predictions for any timestamp t are unchanged when all data after t is truncated (no-lookahead / truncation-invariance test). |
| P0 | S | Fix the label bug: compute targets so that rows whose forward window is unresolved are NaN and are dropped, e.g. `target_up_1d = (fwd>0).astype(float).where(fwd.notna())`. Treat exactly-zero (or |ret| < k*sigma dead-zone) as neutral and drop it. Compute targets in a separate function (make_labels) so compute_features returns features only and cannot be mixed into X by accident. Assert no column starting with 'target_' reaches X. | /home/user/ML-Trading-Platform-Backend/features.py, /home/user/ML-Trading-Platform-Backend/ml_patterns.py | For a df of N bars, compute_labels(horizon=h) has exactly h trailing NaNs and the training matrix excludes them (len(X)==N-h-warmup). Test that the last row's label is never 0 by default. |
| P0 | S | Fix the SELL confidence inversion: have the detector return a direction-aware confidence = max(p, 1-p) (and also p_up) and pass that to execute_signal. In main.py:201 use the direction-aware value. Combine with the existing qty clamp and RiskManager work from the earlier audit so ML sizing is bounded. | /home/user/ML-Trading-Platform-Backend/ml_patterns.py, /home/user/ML-Trading-Platform-Backend/main.py | Test with a stub model returning p_up=0.15: signal==-1 and the confidence passed to execute_signal is 0.85, never 0.15. Order qty derived from it is within the global cap. |
| P1 | M | Put the model in a sklearn Pipeline (drop StandardScaler for trees; keep it only for any linear baseline) and cross-validate with a purged splitter. Minimum: TimeSeriesSplit(n_splits=5, gap=horizon+embargo, test_size=fixed). Better: a small PurgedTimeSeriesSplit using label end-times with embargo. Score with log-loss, Brier, AUC and accuracy minus the majority-class baseline per fold, and report mean and std. Replace the main.py 0.52 accuracy gate with a gate on OOS log-loss/AUC beating the baseline and the OOS backtest being valid. | /home/user/ML-Trading-Platform-Backend/ml_patterns.py, /home/user/ML-Trading-Platform-Backend/main.py | A leakage canary: build features that include the true next-bar return (deliberate leak); the purged CV must flag it (AUC near 1 only when leak included) and a test asserts the production feature set scores AUC within ~0.5±0.05 on shuffled-label data. Test that for every fold max(train_end_label_time) < min(test_start_time) - embargo, and that scaler statistics are unchanged when test data is perturbed. |
| P1 | S | Add a baseline-first model-selection rule: always fit a simple regularised single model (e.g. shallow LightGBM, or logistic regression on a few stationary features) and the RF+XGB+LGBM ensemble under the same purged CV. Use the ensemble only if it beats the best single model on out-of-sample log-loss by more than one fold-std. Fix inconsistent class_weight (apply to all or none). Report which model was used (fix the hard-coded model_type string at ml_routes.py:61 to reflect HAS_XGB/HAS_LGBM). | /home/user/ML-Trading-Platform-Backend/ml_patterns.py, /home/user/ML-Trading-Platform-Backend/routes/ml_routes.py | With xgboost import patched to fail, response model_type contains no 'xgb'. A test on random-noise data asserts the selection function picks the simple model (or abstains) in at least 90% of seeds. |
| P1 | M | Add probability calibration and principled thresholds. Calibrate the ensemble's output on a time-ordered validation block (CalibratedClassifierCV with a time-aware cv, sigmoid by default, or FrozenEstimator on a held-out tail; verify API against the installed sklearn 1.9.x). Replace fixed 0.60/0.40 with a threshold chosen on validation for net expected value after costs, with an abstain band. Also fix :208 so a single-class model returns p_up explicitly (0 or 1) instead of probas[:,0]. | /home/user/ML-Trading-Platform-Backend/ml_patterns.py, /home/user/ML-Trading-Platform-Backend/config.py | On a synthetic dataset with known conditional probabilities, calibrated OOS Brier/ECE is lower than uncalibrated, and bucketed predicted-vs-observed frequencies are within 0.05 for buckets with n>=50. A test with a single-class training target yields p_up in {0,1} consistent with that class. |
| P1 | M | Make features stationary, fixed-schema and finite. Convert macd/macd_signal/macd_hist and ATR to price-normalised forms; replace raw vol_sma_* with volume z-score or ratio; replace cumulative VWAP with a rolling-window VWAP distance; drop the duplicate weekly/monthly/quarterly returns; remove or cap consec_* run lengths; always emit the same columns (NaN, then fill, when volume is zero). Replace inf with NaN and drop. Remove the unused `lookback` param. Add a sanity check that the feature order and names equal those saved with the model. | /home/user/ML-Trading-Platform-Backend/features.py | (a) compute_features on df and on df.iloc[100:] gives identical values for rows >= 100+warmup (window-independence; this fails today for vwap_dist and consec_*). (b) Output columns identical for a zero-volume FX frame and a stock frame. (c) np.isfinite(X).all() after the gate. (d) ADF or simple stationarity check: no feature's rolling mean drifts more than X sigma on a long fixture. |
| P1 | M | Replace pickle with versioned persistence. Save one bundle per (symbol, label spec) via joblib (skops.io if models are ever shared) into models/<symbol>/<timestamp>/ plus metadata.json: sklearn/xgboost/lightgbm/pandas/numpy versions, git SHA, data start/end and a hash of the training frame, feature list + schema hash, label spec, seeds, CV and OOS metrics, calibration and threshold. load() must raise on version-major mismatch or feature-schema mismatch. Wire ML_RETRAIN_DAYS: scans and API load the latest valid model, and retrain only if older than N days or drift check fails. Add a /api/ml/train job (background) separate from /api/ml/predict. | /home/user/ML-Trading-Platform-Backend/ml_patterns.py, /home/user/ML-Trading-Platform-Backend/routes/ml_routes.py, /home/user/ML-Trading-Platform-Backend/main.py, /home/user/ML-Trading-Platform-Backend/config.py | Round-trip test: save then load produces identical predict_proba (np.allclose) on a fixture. Loading with a mutated feature list or a faked sklearn major version raises a clear error. Calling /predict twice within ML_RETRAIN_DAYS performs zero trainings (mock fit asserts call count 0 on the second call). metadata.json contains all listed keys. |
| P1 | S | Pin and lock the environment. Create requirements.lock or use uv/pip-compile with exact versions for numpy, pandas, scikit-learn, xgboost, lightgbm, ta (and pandas-ta if still used). Cap pandas<3 until patterns.py/features.py are tested on 3.x. Note xgboost 3.4.x needs Python >=3.12, so set python_requires and CI Python explicitly. Set deterministic seeds centrally (config.SEED) for all models and for any numpy use, and test that two training runs give identical probabilities (n_jobs=1 or deterministic settings). | /home/user/ML-Trading-Platform-Backend/requirements.txt, /home/user/ML-Trading-Platform-Backend/config.py, /home/user/ML-Trading-Platform-Backend/ml_patterns.py | A fresh venv installed from the lock file runs the ML test suite green. Two successive train() calls on the same fixture give identical predict_proba (np.array_equal, or allclose at 1e-12) with n_jobs=1. CI fails if the lock is out of sync with requirements.in. |
| P2 | M | Add a feature drift and data guard before inference. At train time store per-feature quantiles or mean/std from the training window. At predict time compute PSI or KS of the last ~60 bars against them and refuse to emit signals (return abstain with reason) if more than a configured fraction of features drift, if the latest bar is stale or an incomplete intraday bar, if NaN/inf remain, or if history is shorter than warm-up + min_train_rows. Log drift per run. | /home/user/ML-Trading-Platform-Backend/ml_patterns.py, /home/user/ML-Trading-Platform-Backend/main.py | Shifting all inputs by a large synthetic regime change (x10 volatility) causes predict to abstain with reason 'drift'. Normal held-out data from the same distribution yields PSI < 0.2 for at least 90% of features. |
| P2 | L | Upgrade labels to a volatility-scaled triple barrier (profit and stop at k*rolling-vol, vertical barrier at horizon) with uniqueness sample weights passed as sample_weight to fit, and make the purged splitter use the barrier touch times. Optionally add meta-labeling: use the existing rule patterns (patterns.py) as the primary signal and train the model to predict whether each triggered signal would have won, using that probability to filter and size. Make the label spec part of the saved metadata and make the backtester exit logic consistent with it. | /home/user/ML-Trading-Platform-Backend/features.py, /home/user/ML-Trading-Platform-Backend/ml_patterns.py | Hand-built price paths with known first-touch outcomes (hit TP first, hit SL first, neither) produce labels +1, -1, 0 and correct touch times. Sample weights are lower for heavily overlapping events than isolated ones. The purge test from the CV recommendation passes using touch times. |
| P2 | M | Add multiple-testing accounting. Keep a small CSV or JSON log of every model/threshold/symbol configuration evaluated, and report a Deflated Sharpe Ratio (or at minimum a bootstrap confidence interval of OOS Sharpe) instead of a single is_valid boolean. Surface the OOS metrics and n_trials in the API response. Do NOT adopt MLflow here: a JSON/CSV run log is proportionate. | /home/user/ML-Trading-Platform-Backend/routes/ml_routes.py, /home/user/ML-Trading-Platform-Backend/main.py, /home/user/ML-Trading-Platform-Backend/ml_patterns.py | For pure-noise strategies with 100 trials, the best trial's raw Sharpe looks good but its Deflated Sharpe probability is below 0.95 in the large majority of seeds. The response includes n_trials and a CI. |
| P2 | S | API behaviour for ML endpoints: return real HTTP status codes (HTTPException 422/404/503) instead of 200 {'error'}, run training off the request thread (background task or a pre-trained cached model), and use json_response only after replacing NaN/inf. Validate period_days bounds and a symbol allow-list in MLRequest to cap compute. | /home/user/ML-Trading-Platform-Backend/routes/ml_routes.py | POST /api/ml/predict with an unknown symbol returns 404, with period_days=10 returns 422, and a result containing NaN serialises without a 500. |

**Open questions**

- Which horizon and timeframe is the ML meant to trade (daily only, or intraday)? This sets the embargo, label width and minimum viable history. At 730 calendar days (~500 bars) minus a 200-bar warm-up there may be too little data for a 60-feature model at all.
- Is the ML signal meant to be a standalone strategy or a filter on the rule-based patterns? That decides between direct-direction labels and meta-labeling, and which backtest harness applies.
- Does the user want any sizing driven by model confidence? If yes, calibration and fractional-Kelly caps are required first; if not, a simple binary filter is enough.
- I could not fetch scikit-learn.org (egress blocked), so the calibration API details (FrozenEstimator, cv='prefit' deprecation in 1.6) and persistence security guidance are from memory, not verified against 1.9.1 docs. I also did not verify the maintenance status of pandas-ta, mlfinlab, purgedcv, temporalcv or purged-cross-validation. Verify before depending on them.
- I did not check whether ML_RETRAIN_DAYS or ML_LOOKBACK_BARS are used anywhere else (I only grepped for ML_ definitions in config.py and the call sites of MLPatternDetector). Confirm with a repo-wide grep.
- Are saved models ever shared or downloaded from outside the machine? If not, joblib plus metadata is enough; if yes, use skops.io or ONNX.

### pairs-stat-arb
**Current state.** pairs_trading.py (388 lines) and routes/pairs_routes.py (128 lines) were read in full. Weaknesses, most severe first:

1. Return accounting is wrong (P0). backtest_pair books P&L as (exit_spread - entry_spread)*direction / (|entry_spread| + 1e-10) (pairs_trading.py:270-272). The spread is A - beta*B in raw price units. Its level is arbitrary because it can be near zero or change sign, so |entry_spread| is not a capital base. A tiny entry spread gives an enormous return. The result is then multiplied by position_size = 5% of capital (:245, :272). It is neither dollar-neutral nor per-share. Costs are position_size*commission*4 (:272), so it is unclear whether position_size is gross or per leg. config.COMMISSION_PCT is 0.1% (config.py:75), so costs are probably fine, but there is no slippage, borrow or financing cost.

2. Look-ahead and in-sample fitting (P0). analyze_pair fits the hedge ratio and runs coint/ADF on the whole window (:107, :112). generate_pair_signals then uses that full-sample beta (:173) and backtest_pair trades the same data (:365). Signals use rolling-60 z (:176-178) but the hedge ratio, cointegration p-value and pair-validity filter all saw the future. The z-score is also inconsistent across the code. analysis.current_zscore is full-sample mean/std (:126-128), while the signal uses rolling z. scan_all_pairs sets has_signal and signal_direction from the full-sample z (:376-381), which can disagree with the signal the backtest would trade. routes/pairs_routes.py:50-52 hard-codes a third copy of rolling-60 z.

3. Same-bar execution and equity accounting. A signal on bar i at the close is filled at the spread of bar i's close (:259, :265). There is no one-bar lag. Equity is appended every loop iteration but only changes on exits (:274, :285), so the curve is a step function. Max drawdown and Sharpe (:303-304) are computed from this step curve, so they are meaningless. Sharpe is not risk-free adjusted. A trade still open at the end of data is silently dropped. sqrt(252) is applied to all asset classes, including crypto (365) and 24/5 FX. profit_factor can be inf (:315).

4. Spread model.
- No intercept is carried: spread = A - beta*B (:60), although beta comes from an OLS with a constant (:51-53). The constant is discarded.
- Regression is on raw prices rather than log prices. Beta is in shares, so dollar-neutral sizing is not possible without B/A price scaling. Beta also drifts with price levels.
- Half-life uses theta from d(spread) = c + theta*lag, with half-life = -ln2/theta (:83-85). That is the continuous-time approximation, and it is biased for theta near -1. It returns float('inf') (:75, :84, :87), which breaks JSON.
- The Engle-Granger test uses coint(a, b) in one ordering only (:107). The test is asymmetric, so results can differ when A and B are swapped.
- adfuller on the fitted residual (:118) with standard ADF p-values is wrong. Residuals from an estimated regression need Engle-Granger/MacKinnon critical values, which is what coint already provides. The ADF p-value is therefore anti-conservative, and is_valid_pair (:153) uses it as an extra gate.
- Price-level correlation (:104, :152) is a spurious-regression statistic. Use return correlation, or drop it.
- Multiple-testing: 10 pairs are scanned at p<0.05 with no correction.

5. Strategy logic. The z-stop at 3.5 on rolling-60 z (:209, :216) rarely fires because the rolling window adapts to the divergence. There is no time stop based on half-life and no cointegration-breakdown check. Rolling lookback of 60 is fixed (:161) and not tied to the half-life. PAIRS_LOOKBACK is 252 (config.py:114) and the scan only needs >=60 overlapping bars (:346), so beta and the tests are estimated on very few effective independent observations. Position flipping (long straight to short) is impossible (:199-220), which is acceptable but undocumented.

6. Cross-asset pairs are configured (config.py:41-52): BTC-USD/ETH-USD, EURUSD=X/GBPUSD=X, GC=F/SI=F, CL=F/NG=F. Intersection of date indexes (:345, routes:38) silently drops crypto weekends and misaligns timezones. Yahoo '=F' series are unadjusted continuous futures, so roll gaps contaminate the spread. Contract multipliers, margin and FX swap/carry are ignored. Alpha Vantage maps futures to equities (known issue). CL=F/NG=F and GC=F/SI=F are fundamentally weak cointegration candidates.

7. API layer (routes/pairs_routes.py).
- Errors are returned as HTTP 200 {error} (:35, :40).
- /analyze returns round(float(half_life)) which is Infinity when not mean-reverting (:81), and invalid JSON. _sanitize is only applied to the backtest block (:87).
- /scan returns results unsanitised (:127), and has_signal is np.bool_ from abs(np.float64) > float (pairs_trading.py:376), which FastAPI cannot encode, so it 500s.
- backtest keys total_return and max_drawdown do not match the frontend's total_return_pct and max_drawdown_pct. Returns are fractions, not percent.
- PairRequest has no validation (period_days unbounded, symbols unchecked, symbol_a==symbol_b allowed).
- Synchronous heavy work (two fetches plus coint, row-by-row iterrows for charts :55-68). The scan fetches every symbol with no cache.
- No tests anywhere.

**Best practices**

- Separate formation (estimation) window from trading window and re-estimate walk-forward. Beta, intercept, spread mean/std, half-life and the cointegration test must use only data up to t-1 at every decision. Evaluate with an out-of-sample trading period after a formation period.: Fitting beta and testing cointegration on the same data you backtest is the dominant source of overstated pairs-trading performance. Pair selection on in-sample p-values also creates selection bias. (https://arxiv.org/html/2412.12458v1; https://www.interactivebrokers.com/campus/ibkr-quant-news/kalman-filter-python-tutorial-and-strategies-part-i/)
- Regress log prices with an intercept: logA = alpha + beta*logB + e. Spread = logA - beta*logB - alpha. Trade it in dollar terms: for $N gross, hold +w_a in A and -w_b in B with w_a = 1/(1+|beta|) and w_b = |beta|/(1+|beta|) of gross. Daily P&L is position(t-1) * (w_a*r_a - w_b*r_b), the sum of the two legs' returns on gross capital, minus costs on both legs' turnover. Never divide by the spread level.: Spread level is not a capital base. Per-leg return accounting is the only way to get correct returns, drawdowns, Sharpe and leverage. Log prices make beta scale-invariant and make dollar weights time-stable. (https://arxiv.org/html/2412.12458v1)
- Engle-Granger (statsmodels coint, trend 'c') is adequate for a two-asset pair. Test both orderings and require the better p-value, or require both. Use the MacKinnon p-value returned by coint rather than a separate ADF on the fitted residuals. Johansen (coint_johansen) is symmetric and gives the cointegrating vector via the eigenvector, but it is only worth using for 3+ asset baskets or as a cross-check. It is sensitive to det_order and k_ar_diff choices. Apply a multiple-testing correction (Bonferroni/Benjamini-Hochberg) when scanning many pairs.: EG is asymmetric and ADF on estimated residuals uses the wrong null distribution. Johansen adds complexity that does not pay off for a fixed list of 10 pairs. (https://www.statsmodels.org/stable/generated/statsmodels.tsa.vector_ar.vecm.coint_johansen.html; https://www.statsmodels.org/stable/generated/statsmodels.tsa.stattools.coint.html)
- Hedge ratio: use an expanding or rolling-window OLS (re-fit weekly or monthly, not daily) as the baseline. Add a Kalman-filter dynamic regression (state = [beta, alpha], random-walk transition, observation y=logA, x=[logB,1]) only if a rolling OLS demonstrably breaks down out of sample. Tune Q/R (delta) on a formation window, not on the test period.: Daily re-fit hedge ratios inject noise and turnover. A Kalman filter adapts faster but introduces tuning parameters that are easy to overfit. It is standard practice (Chan; QuantStart; IBKR Campus), but should be benchmarked against rolling OLS. (https://www.interactivebrokers.com/campus/ibkr-quant-news/kalman-filter-python-tutorial-and-strategies-part-i/; https://www.quantstart.com/articles/Dynamic-Hedge-Ratio-Between-ETF-Pairs-Using-the-Kalman-Filter; https://blog.quantinsti.com/kalman-filter/)
- Half-life: regress the spread level on its lag with an intercept, s_t = c + phi*s_{t-1} + e. Use half-life = -ln2/ln(phi) for 0<phi<1. Otherwise mark it as non-mean-reverting and return null, never inf. Tie z-score lookback to the half-life (about 1-3x HL, clamped), and set a time stop at about 2-3x HL. Reject pairs whose HL is below the bar spacing or above about 1/3 of the window.: A half-life of at most a few bars cannot survive costs. A half-life near the window length cannot be distinguished from a random walk. The log-phi form removes the discretisation bias of the -ln2/theta approximation. (https://arxiv.org/html/2412.12458v1; https://www.luxalgo.com/library/concept/cointegration.md)
- One shared function computes the z-score. Signal generation, backtest, /analyze charting and /scan current-signal must all call the same function with the same causal parameters (the same lookback, shift(1) so the z-score at t uses the mean/std through t-1 or at least through t, and execution at t+1). Do not compute z in the route or from full-sample analysis fields.: Signal/backtest mismatch is the most common reason a live signal differs from what the backtest traded. A single source of truth also makes a regression test possible. (https://www.interactivebrokers.com/campus/ibkr-quant-news/kalman-filter-python-tutorial-and-strategies-part-i/)
- Cointegration-breakdown guard: re-run the EG test and recompute beta on a rolling window each period. Exit and block new entries when the rolling p-value exceeds a looser threshold (e.g. 0.10-0.15, hysteresis relative to the 0.05 entry gate), the half-life leaves its band, or the spread exceeds a hard z-stop on a frozen (entry-time) mean/std. Optionally add a CUSUM on residuals or a rolling-beta stability check.: Rolling-z stops adapt to a drifting spread and never trigger. A frozen-parameter stop and a re-test are the practical defences against structural breaks. Heavier changepoint tooling is overkill for ten pairs. (https://scholar.nycu.edu.tw/en/publications/online-structural-break-detection-for-pairs-trading-using-wavelet/; https://www.luxalgo.com/library/concept/pairs-trading-stack/)
- Cross-asset caveats: trade ETF/equity pairs on a common exchange calendar. Do not intersect crypto 24/7 with equity calendars silently. For futures use back-adjusted or ratio-adjusted continuous series (or contract-specific data), include multipliers and margin, and model roll. For FX, include swap/carry. Annualise with the right factor (252 vs 365). Treat commodity ratios (GC/SI, CL/NG) as unlikely candidates and require stricter tests. Tag every pair with its asset class and gate the order path for non-equity pairs.: Yahoo '=F' series have unadjusted roll jumps, which are fake spread moves. Mixed calendars and contract multipliers change the effective hedge ratio. FX carry and futures margin are real costs and constraints. (https://arxiv.org/html/2412.12458v1)
- Reporting hygiene: all numeric API output through a single sanitiser that maps nan/inf to null and np.* to Python natives (also top-level fields). Use HTTP status codes for errors. Return fractional returns consistently and fix the frontend key mapping once.: Prevents the Infinity JSON, np.bool_ 500s and silent 200 errors already seen. (https://fastapi.tiangolo.com/tutorial/handling-errors/)

**Tool options**

| Tool | 2026 status | Recommended | Pros | Cons |
|---|---|---|---|---|
| statsmodels (coint, adfuller, OLS, RollingOLS, coint_johansen) | 0.15.0 (PyPI upload 2026-08-27, verified via pypi.org JSON). Already a dependency. I could not fetch the statsmodels docs (egress blocked), so API details are from memory and should be checked against 0.15. | yes | Already used. coint implements Engle-Granger with MacKinnon p-values. RollingOLS and RecursiveLS give rolling/expanding beta. coint_johansen is available for baskets. | coint is asymmetric. Johansen is sensitive to det_order/k_ar_diff. No built-in Kalman pairs workflow, though it has a statespace MLEModel. |
| Hand-rolled numpy 2-state Kalman filter (about 30 lines) | No dependency. numpy 2.5.3 (verified 2026-09-06). | yes | Transparent, easy to unit-test, no maintenance risk. The standard beta/alpha random-walk recursion is short. Easy to make strictly causal. | You own the code and the Q/R tuning. Only worth adding if rolling OLS fails. |
| pykalman | 0.11.2 (PyPI upload 2026-01-31, verified). Repo github.com/pykalman/pykalman. Has had releases again recently, but I did not independently verify issue/PR activity. | no | Off-the-shelf KalmanFilter with EM fitting. Widely used in tutorials. | Past maintenance gaps. EM parameter fitting on the full sample leaks the future. Another dependency for 30 lines of maths. |
| filterpy | 1.4.5, last release 2018-10-10 (verified). Effectively unmaintained. | no | Familiar API. | Abandoned. Compatibility with numpy 2.x / pandas 3.x is not guaranteed. |
| Johansen via statsmodels coint_johansen | Part of statsmodels 0.15.0. | no | Symmetric, handles 3+ series, gives a cointegrating vector and rank. | Overkill for a fixed list of 2-asset pairs. Needs det_order and lag choices. For two series it mostly duplicates EG-both-orderings. |
| Dedicated stat-arb frameworks / changepoint libraries (e.g. ruptures, backtesting engines) | Not verified. | no | Offer more structural-break detection and portfolio plumbing. | Heavyweight for a single-owner project. A rolling re-test with hysteresis and a frozen-parameter stop covers most of the benefit. |

**Recommendations**

| Priority | Effort | Change | Files | Acceptance test |
|---|---|---|---|---|
| P0 | M | Rewrite backtest_pair as a vectorised two-leg, dollar-neutral engine. Inputs: aligned close prices, per-bar target position in {-1,0,+1}, beta (static or a time-varying series), commission and slippage per leg. Position is shifted by one bar (trade at t+1 from a signal at t). Daily return = pos[t-1] * (w_a*r_a - w_b*r_b) with w_a=1/(1+|beta|), w_b=|beta|/(1+|beta|), minus cost * (|d pos_a| + |d pos_b|). Equity is compounded daily, so Sharpe and drawdown are computed from a real daily curve. Per-trade pnl_pct is the compounded return of that trade. Mark open trades to market at the final bar. Remove the |entry_spread| denominator (pairs_trading.py:271) and the step-function equity (:274, :285). | /home/user/ML-Trading-Platform-Backend/pairs_trading.py | Unit test with synthetic prices where B=100 flat and A rises from 100 to 110 while long-A/short-B is held with beta=1: return equals (0.5*10% - 0.5*0%) = 5% on gross, independent of the spread level. A second test shifts both series by +1000 (spread level near zero/sign change) and the return is unchanged. Test that a trade open at end of data is reported and that equity changes on days with no exits. |
| P0 | M | Make the hedge ratio, intercept and z-score causal and consistent. Add one function compute_spread_stats(logA, logB, window, mode) returning beta_t, alpha_t, spread_t, z_t using only data through t-1 (rolling or expanding OLS with an intercept on log prices). generate_pair_signals, backtest_pair, routes /analyze and /scan current-signal all consume its output. Delete the full-sample current_zscore usage for signals (pairs_trading.py:126-128, 376-381) and the duplicated rolling-60 code in routes/pairs_routes.py:50-52. Keep full-sample numbers only as explicitly labelled descriptive fields. | /home/user/ML-Trading-Platform-Backend/pairs_trading.py, /home/user/ML-Trading-Platform-Backend/routes/pairs_routes.py | Look-ahead test: truncate the input at bar T, compute z and beta, then extend with random data after T. Values at or before T must be bit-identical. Consistency test: the z returned by /scan current-signal equals the last z in the series the backtest used. |
| P0 | S | Fix API serialisation and errors. Apply one sanitiser (nan/inf to None, np.bool_/np.integer/np.floating to Python natives) to every response field including top-level hedge_ratio/half_life/correlation (routes:77-83) and the /scan result list (routes:127). compute_half_life returns None instead of float('inf'). profit_factor returns None when there are no losses. Use HTTPException (422/404/502) instead of HTTP 200 {error}. Validate PairRequest (symbols distinct and non-empty, period_days bounded, e.g. 120 to 2520). Rename response fields to the frontend's expected names (total_return_pct, max_drawdown_pct) or fix the frontend, in percent consistently. | /home/user/ML-Trading-Platform-Backend/routes/pairs_routes.py, /home/user/ML-Trading-Platform-Backend/pairs_trading.py, /home/user/ML-Trading-Platform-Frontend (pairs page/types) | TestClient test: POST /api/pairs/analyze with a random-walk pair (half-life undefined) returns 200 with half_life null and passes json.loads with allow_nan=False. POST /api/pairs/scan with data making has_signal True returns 200. Bad symbols return 4xx, not 200. A contract test asserts the frontend-consumed keys exist in the response. |
| P1 | M | Fix the pair-selection statistics. (a) Run coint on log prices in both orderings and use the max p-value (conservative). (b) Remove the adfuller gate on fitted residuals (pairs_trading.py:118, 153) or keep it as informational only. (c) Replace price-level correlation with return correlation. (d) Half-life from AR(1) with intercept via -ln2/ln(phi), None unless 0<phi<1 (pairs_trading.py:63-87). (e) Apply Benjamini-Hochberg across the pairs scanned in scan_all_pairs. (f) Reject pairs with fewer than e.g. 252 aligned bars or HL outside [max(2, 2 bars), window/3]. Remove unused imports (scipy.stats). | /home/user/ML-Trading-Platform-Backend/pairs_trading.py, /home/user/ML-Trading-Platform-Backend/config.py | Simulate an OU spread with known phi=0.95 (true HL about 13.5): estimated HL within 25% on 1000 bars. Simulated independent random walks pair: the share of accepted pairs across 500 trials is at or below about 5% (about 5% or below after BH). Swapping A and B gives identical is_cointegrated. |
| P1 | M | Walk-forward evaluation. Add a walk_forward_pairs(df_a, df_b, formation=252, trade=63, step=63) that estimates beta, alpha, test and half-life on each formation window, trades the next block with parameters frozen, and stitches out-of-sample returns. Report in-sample vs out-of-sample metrics separately and base is_valid on out-of-sample results. Tie the z lookback to half-life (clamped, e.g. 20-120) instead of the fixed 60 at pairs_trading.py:161. | /home/user/ML-Trading-Platform-Backend/pairs_trading.py, /home/user/ML-Trading-Platform-Backend/routes/pairs_routes.py | On a synthetic pair that is cointegrated for the first half and a pair of independent random walks for the second half, out-of-sample P&L from the second half is not significantly positive, while the in-sample (full-sample) backtest still looks profitable. Confirms the evaluation exposes overfit. Frozen-parameter test: changing data inside a later trade block does not change earlier blocks' results. |
| P1 | M | Cointegration-breakdown and exit rules in signal generation. Add (a) a time stop at k*half-life (default 3x), (b) a hard stop on a z frozen at entry (entry mean/std), not the rolling z (pairs_trading.py:209, 216), (c) a rolling EG re-test every N bars that forces exit and blocks entries when p > 0.15 or HL leaves the band, with hysteresis, (d) optional beta-stability check (rolling beta change beyond X sigma). Return an exit_reason per trade (reversion, z_stop, time_stop, coint_break, end_of_data) and expose it in the API. | /home/user/ML-Trading-Platform-Backend/pairs_trading.py, /home/user/ML-Trading-Platform-Backend/config.py | Synthetic spread that is OU for 300 bars then becomes a random walk with drift: strategy exits via coint_break or time_stop within a bounded number of bars after the regime change and does not re-enter. The frozen-z stop fires on a persistent divergence where the rolling-60 z stays below 3.5 (assert that the old logic would not have exited). |
| P1 | M | Cross-asset handling. Add an asset_class tag per configured pair (equity, crypto, fx, futures) in config.PAIRS. Align on a per-class calendar (do not inner-join crypto to equities; forward-fill only within a class and cap the fill gap). Use the correct annualisation (252 vs 365). For futures, require back-adjusted continuous data or flag the pair as unreliable and exclude by default (data_fetcher '=F' issue). Add contract multipliers/notional and FX swap/borrow cost parameters, and refuse to emit executable signals for non-equity pairs until the order path supports them. Review config.PAIRS: drop or flag GC=F/SI=F and CL=F/NG=F by default. | /home/user/ML-Trading-Platform-Backend/config.py, /home/user/ML-Trading-Platform-Backend/pairs_trading.py, /home/user/ML-Trading-Platform-Backend/data_fetcher.py | A BTC-USD/ETH-USD analysis keeps weekend bars and uses 365 for annualisation. A futures pair with a synthetic roll jump (a +5% step in one series on the roll date) is flagged or has the jump removed so the spread shows no step. A futures pair returns a non-executable flag in the API response. |
| P1 | S | Add a pytest suite for pairs_trading and pairs_routes (the repo has no tests). Cover: OU half-life recovery, causality/truncation test, dollar-neutral P&L identity, sanitiser/JSON validity, both-orderings coint symmetry, np.bool_ handling in /scan. Pin numpy, pandas (<3 or tested on 3.0.6), statsmodels (0.15.x) and scipy in requirements with upper bounds, and add a lock/constraints file. | /home/user/ML-Trading-Platform-Backend/tests/test_pairs_trading.py (new), /home/user/ML-Trading-Platform-Backend/tests/test_pairs_routes.py (new), /home/user/ML-Trading-Platform-Backend/requirements.txt | pytest passes locally and in CI on a clean venv from the pinned requirements. Reverting any one of the P0 fixes makes at least one named test fail. |
| P2 | M | Optional Kalman hedge ratio as a selectable mode (hedge_mode='ols_rolling'|'ols_expanding'|'kalman') using a hand-rolled 2-state numpy filter (state [beta, alpha], random walk), with delta chosen on the formation window only. Keep rolling OLS as the default, and promote Kalman only if walk-forward out-of-sample Sharpe/turnover is better on the configured pairs. Avoid pykalman EM fitting on the full sample. | /home/user/ML-Trading-Platform-Backend/pairs_trading.py | On a synthetic pair with a step change in true beta at bar 500, the Kalman beta converges to the new value within 50 bars and the filter output is causal (truncation test passes). A comparison script reports OOS Sharpe and turnover for the three modes on the configured pairs. |
| P2 | S | Performance and API hygiene for /analyze and /scan: replace the iterrows chart building (routes:55-68) with vectorised to_dict output, cache fetch_ohlcv per symbol for the request (and ideally a short TTL cache), and make the heavy endpoints def (threadpool) with a timeout, or move /scan to a background job. Optionally add a Johansen cross-check field for informational comparison. | /home/user/ML-Trading-Platform-Backend/routes/pairs_routes.py, /home/user/ML-Trading-Platform-Backend/data_fetcher.py | A /scan call over 10 pairs makes at most one fetch per unique symbol (mocked fetcher call-count assertion) and /analyze with 2000 bars completes the chart-building step with no per-row Python loop (profile or code check). |

**Open questions**

- Is 5% MAX_POSITION_SIZE_PCT meant to be gross exposure across both legs or per leg? The cost model (commission*4) and the dollar-neutral weights depend on this.
- Daily bars only, or intraday too? Half-life bands (5-120) and the 252 annualisation assume daily.
- Are pair signals ever meant to be executed (paper or live), or is this research-only? Alpaca cannot trade the crypto/FX/futures pairs as configured, and the shorting/borrow assumptions only apply to equities.
- Does the frontend expect percent or fractional returns? The Dashboard has a known 100x error, so units should be standardised once.
- Caveat on verification: PyPI versions and dates (statsmodels 0.15.0, pykalman 0.11.2, filterpy 1.4.5 last released 2018, pandas 3.0.6, numpy 2.5.3, scipy 1.18.1) were verified from the PyPI JSON API. The statsmodels documentation pages were not retrievable (egress blocked), so details such as coint's trend/autolag defaults and Johansen parameter semantics are from memory and should be confirmed against the 0.15 docs. Web searches returned mostly tutorials and one arXiv paper (2412.12458), not primary sources on breakdown detection, so those thresholds (p>0.15 hysteresis, 3x HL time stop) are engineering judgement to be tuned, not published constants.
- Should futures pairs be kept at all given the unadjusted Yahoo continuous series? The cheapest robust option is to drop them from the default scan until adjusted data is available.

### data-layer
**Current state.** Read data_fetcher.py (326 lines), config.py (168), .env.example, requirements.txt, and features.py:120-175. Findings:

PROVENANCE / CONSISTENCY
- fetch_ohlcv (data_fetcher.py:260-287) returns a bare DataFrame. The winning source is not recorded and not returned, so three TechnicalScanner fetches for one symbol can come from different providers (e.g. Alpaca one call, yfinance the next after a transient failure). The 'sources' param (275) exists but nothing pins it per symbol.
- fetch_pair (312-325) fetches each leg independently, so leg A can be Alpaca and leg B yfinance. Alpaca daily bar timestamps are session-open UTC (04:00/05:00Z); after tz_localize(None) (98) they are not midnight, while yfinance/AV indexes are midnight. index.intersection (321) can then be empty -> returns None (322), silently killing a pair. Mixed adjustment is likewise possible.

ADJUSTMENT / OHLC
- Alpha Vantage (199-200) overwrites Close with adjusted close but leaves O/H/L raw. High<Close and Low>Close become possible on split/dividend days, corrupting ATR, candlestick patterns, SL/TP and every feature using h/l.
- Alpaca uses adjustment=all (65). yfinance history() relies on the library default for auto_adjust (231, implicit). Adjusted-vs-raw is never recorded or asserted.

SYMBOL MAPPING / CAPABILITY
- No mapping layer. Ad hoc string tests: Alpaca skips '=' (34); crypto via '-USD' replace (37-41); AV strips '=F' (153), so GC=F/ES=F/CL=F are requested as equity tickers 'GC', 'ES', 'CL' (the issue already noted) and could return a real but unrelated equity. Forex 'EURUSD=X' slices [:3]/[3:] (140-141) OK but fragile. Class tickers like BRK-B / BF.B are not mapped for Alpaca (needs BRK.B).
- Futures are served only by yfinance continuous front-month (GC=F), whose roll behaviour is opaque and unadjusted. POLYGON_API_KEY (config.py:67) is read but there is no Polygon fetcher. No capability matrix, so an unsupported (provider, asset class) pair is only discovered by an exception swallowed at debug level.

TIME / CALENDAR
- end_date = datetime.now() (272) is naive local time; Alpaca params are built with a hard-coded 'T23:59:59Z' end (62), i.e. in the future for today. On the free Alpaca plan (no SIP data newer than 15 minutes) a request whose end is inside that window and has no feed param can be rejected; the except (107-109) turns it into an empty frame logged at DEBUG, so the system silently drops to AV/yfinance and changes data source (likely, not reproduced; I have no keys).
- All tz info is stripped (98, 238) with no normalisation to a canonical session date. Crypto/forex (24/7 or Sun-Fri) and equities (NYSE calendar) are mixed on one naive index; fetch_pair intersects them as-is (EURUSD vs GC=F etc.). Today's partial bar is included (end=now) and can be treated as a completed bar by pattern/signal code (relates to check_recent_signal bias). No exchange calendar, so missing sessions vs holidays are indistinguishable.
- The AV interval param is ignored (always daily); unknown Alpaca intervals silently map to '1Day' (53).

RESILIENCE
- Bare requests.get with no Session, no retry/backoff, no Retry-After/429 handling, no rate limiter (78, 159). AV free tier = 5/min and 25/day, and AV returns HTTP 200 with a 'Note'/'Information' JSON body on throttle; the code treats that as 'no time series key' and returns empty (172-173) with no log. A scan of 42 watchlist symbols with AV configured can burn the 25/day quota in one run (fetch_watchlist 303-306 is sequential and unthrottled).
- All provider failures logged at logger.debug (108, 217, 245); the only visible output is 'All data sources failed' at warning (286) with no reason. yfinance is called with no retry; yfinance 429 YFRateLimitError is a well-known recurring failure.
- Acceptance bar is len(df) >= 30 (283): a 2-year request returning 31 rows (truncated history, AV compact/limit, partial yfinance response) is accepted silently.

QUALITY VALIDATION / CACHE
- No validation: no checks for High>=max(O,C), Low<=min(O,C), non-positive prices, zero volume runs, duplicate/unsorted timestamps, stale last bar, gaps vs calendar, or outlier returns/split artefacts. df.dropna drops rows silently (102, 211, 239). Volume=0 injected for AV (207).
- No cache or store: every API call, scanner view, and scheduled run re-downloads 2 years. This amplifies rate-limit exposure and lets numbers differ between views of the same symbol minutes apart.

CONFIG / DEPENDENCIES
- config.DATA_SOURCE_PRIORITY (58) is global; not per asset class. .env.example has no way to set priority, cache dir, or provider tier (free/premium) and no Tiingo/Databento/Massive entries. Keys read via os.getenv with no strip/validate. requirements.txt is all '>=' (pandas>=2.0 will resolve to 3.x; yfinance>=0.2.31 spans a very large API drift, now 1.x).
- features.py:148-149 target_up NaN -> 0 (known). Also compute_features assumes a clean, gap-free, sorted, single-source daily index: pct_change(5/21/63) means 5 calendar-agnostic bars (a 'week' is 5 bars for stocks, 5 days for 24/7 crypto), so features are not comparable across asset classes, and nothing validates the index before use.

**Best practices**

- Introduce a canonical bar schema plus a provider-adapter interface (fetch_bars(symbol, start, end, timeframe) -> Bars with attrs: source, adjusted flag, tz, fetched_at), a per-asset-class provider priority, and a symbol registry mapping one canonical id (e.g. 'GC=F', 'BTC-USD') to each provider's native symbol or None if unsupported.: Provider symbology differs (Alpaca BTC/USD and BRK.B, Alpha Vantage FX/crypto functions, Databento continuous symbols like ES.c.0 or ES.v.0 with stype_in='continuous', Massive/Polygon prefixes C:/X:). A registry makes unsupported combos explicit instead of silently wrong. (https://docs.alpaca.markets/docs/about-market-data-api; https://databento.com/docs/quickstart; https://github.com/ranaroussi/yfinance)
- Adjust OHLC consistently: derive a single adjustment factor (adj_close/close) and multiply O/H/L by it, or request provider-adjusted OHLC (Alpaca adjustment=all, yfinance auto_adjust=True explicit). Store raw and adjusted separately plus the adjustment mode; never mix.: Mixed adjusted Close with raw OHL breaks High/Low invariants and every range-based indicator, pattern and SL/TP. Alpaca historical data older than 15 minutes is identical on free and paid plans, so adjustment parameters apply equally. (https://docs.alpaca.markets/docs/about-market-data-api)
- Pin a single source per symbol per run (and persist the choice): the fetch layer returns (df, meta) and all views of a symbol read from the cache, never from a different provider; if the primary source fails, serve the cached data flagged stale rather than switching silently to a different provider with different adjustment/timestamps.: Cross-provider switching causes index misalignment, adjustment mismatches and non-reproducible results. (https://docs.alpaca.markets/docs/about-market-data-api)
- Normalise time: store UTC timestamps; for daily bars store the session date (tz-naive date in exchange tz) keyed by exchange calendar (XNYS for US stocks/ETFs, 24/7 calendar for crypto, CMES/XCME for futures, forex 24/5 via custom or Sun-Fri). Use exchange_calendars to detect missing sessions, drop partial in-progress bars, and compute 'last complete session'.: Prevents holiday/gap confusion, partial-bar signals, and empty index intersections when pairing instruments. exchange_calendars is the maintained successor to trading_calendars (latest 4.13.2, March 2026, Python 3.10-3.13). (https://pypi.org/pypi/exchange-calendars/json; https://github.com/gerrymanoim/exchange_calendars)
- Local columnar store: one parquet file per (symbol, timeframe, source, adjustment) under a data/ directory, written atomically (temp file + rename), with an incremental update: read last stored session, fetch from last_date minus a small overlap (e.g. 5 sessions) to catch revisions, merge/dedupe on timestamp, and re-fetch fully if a corporate action (adj factor change) is detected. DuckDB (1.5.x) can query the parquet directory but is optional for a single owner.: Cuts API calls by 95%+, protects free-tier quotas (AV 25/day, Tiingo 1000/day), and makes every view consistent. Plain parquet via pandas/pyarrow is proportionate; DuckDB or a database is overkill unless querying across many symbols. (https://pypi.org/pypi/duckdb/json; https://alpaca.markets/docs/market-data)
- Rate limiting and retries: one shared requests.Session per provider with a token-bucket/min-interval limiter sized to the plan (AV free 5/min + 25/day, Tiingo free 50/hr + 1000/day + 500 unique symbols/month, Massive/Polygon free 5/min, Alpaca free 200/min), and tenacity retries (exponential backoff with jitter, honour Retry-After, retry only 429/5xx/timeouts, never 4xx auth errors). Detect AV 'Note'/'Information' body as a throttle/premium-only error, not an empty result. Add a circuit breaker/cooldown per provider.: Prevents quota burn and silent empty results. tenacity 9.1.4 (Feb 2026) is current and maintained. (https://pypi.org/pypi/tenacity/json; https://qveris.ai/guides/alpha-vantage-pricing-alternative/; https://apicostcalc.com/blog/polygon-massive-rebrand-api-pricing.html; https://www.quantstart.com/articles/evaluating-data-coverage-with-tiingo/)
- Validate every frame at the boundary with a pandera DataFrameSchema (or ~40 lines of plain assertions): DatetimeIndex unique+monotonic, no NaN in OHLC, all prices > 0, High >= max(Open,Close,Low), Low <= min(Open,Close,High), Volume >= 0, abs daily return below a configurable threshold (flag, quarantine, don't drop silently), last bar not older than N sessions per calendar, minimum row count tied to requested range. Return a quality report with the frame and refuse to cache failed data.: Catches provider errors, split artefacts and truncated history before they become trades. Pandera is the maintained standard (0.33.x), but plain checks are enough at this scale. (https://pypi.org/pypi/pandera/json; https://pandera.readthedocs.io)
- Treat yfinance as a best-effort fallback/research source only, not a primary: it is an unofficial scraper of Yahoo, community maintained (1.7.0 at time of check), states it is for research/education and personal use, and Yahoo's data is subject to Yahoo's terms. 429 YFRateLimitError and breaking changes are recurrent. Use for futures/forex continuous series only when no licensed alternative is configured, label such data source=yfinance in provenance, and never use it to drive live paper orders without a cross-check.: ToS and reliability risk; the project is personal so risk is tolerable for research but should be explicit and visible. (https://github.com/ranaroussi/yfinance; https://pypi.org/pypi/yfinance/json; https://github.com/ranaroussi/yfinance/discussions/2431)
- Pin dependencies (requirements.txt with == or a lock via pip-tools/uv) and add a small test suite for the data layer using recorded fixtures (VCR/responses), including property tests for OHLC invariants and a golden test per adapter.: pandas 3.x semantic changes and yfinance API drift silently change results; fixture tests make provider schema drift visible. (https://pypi.org/pypi/yfinance/json; https://pypi.org/pypi/tenacity/json)

**Tool options**

| Tool | 2026 status | Recommended | Pros | Cons |
|---|---|---|---|---|
| Alpaca Market Data (alpaca-py) | alpaca-py 0.44.0 per PyPI (upload date reported as Jan 2025 by the fetch summariser; date is suspect, verify). Free Basic plan: IEX-only real-time equities, SIP data only if older than 15 min, 200 historical calls/min, history since 2016. | yes | Free with paper account; split/dividend adjustment parameter; crypto bars; official SDK; same vendor as the paper broker so symbols/orders line up; good primary for US stocks/ETFs and crypto. | No forex, no futures. Free-plan IEX volume is a small share of consolidated volume (use feed explicitly; SIP for historical only >15 min old). Timestamps are session-open UTC; needs normalisation. Class-share symbols use '.' not '-'. |
| yfinance | 1.7.0 (PyPI), Apache-2.0, community maintained, not affiliated with Yahoo, personal/research use only; recurring 429 rate-limit issues; relies on curl_cffi. | yes | Free, covers stocks, crypto, forex (=X) and continuous futures (=F) with one interface; long history; auto_adjust option. | Unofficial scraping of Yahoo with ToS exposure and unannounced breakage; futures are an opaque continuous front-month series with unknown roll rule; throttling from home IPs; data errors without notice. |
| Alpha Vantage | Free: 5 req/min and 25 req/day; premium $49.99-$249.99/month (75-1200 req/min, no daily cap) per third-party summaries. I could not reach alphavantage.co from this environment (egress blocked), so premium-only status of TIME_SERIES_DAILY_ADJUSTED and outputsize=full is from memory/unverified; check docs. | no | Official API with FX_DAILY, crypto, commodities endpoints and technical indicators; stable for small volumes. | Free quota is far too small for a 42-symbol watchlist; 200-OK throttle bodies; no equity-style futures contracts (strip '=F' bug); adjusted endpoint likely premium; premium price high for a single-owner project. Use only as a low-volume cross-check (a handful of symbols) or drop. |
| Massive (formerly Polygon.io) | Polygon.io rebranded to Massive in early 2026; same API and keys; polygon.io/pricing redirects to massive.com/pricing. Free: 5 calls/min, end-of-day data, about 2 years history; Stocks Starter $29/mo, Developer $79, Advanced $199 (third-party reports, unverified against the primary pricing page). | yes | Clean REST API covering stocks, options, indices, forex, crypto, futures; adjusted flag on aggregates; POLYGON_API_KEY is already in config; a good paid upgrade path. | Free tier is rate-limited (5/min) and limited history; futures need a higher plan; the SDK/package/domain naming changed (verify the client library name and base URL before coding). |
| Tiingo | Free: 50 requests/hour, 1000/day, 500 unique symbols/month (per QuantStart and other summaries; primary pricing page was blocked here). Paid plans exist; commercial-use terms not verified. | yes | Reputable EOD data with adjusted and raw OHLC provided together (adjOpen/adjHigh/adjLow/adjClose plus splitFactor/divCash), IEX intraday, crypto and FX endpoints; excellent as a daily-bar primary for stocks within quota. | 500 unique symbols/month is fine for this watchlist but limits exploration; no futures; free tier terms for non-personal use unclear. |
| Databento | Usage-based pricing (historical CME Globex from about $0.50/GB), $125 free credit for new accounts expiring after 6 months, also subscription plans; covers CME/CBOT/NYMEX/COMEX futures with continuous symbology. | no | The proper source for futures: real contracts, explicit roll rules (calendar/volume/open-interest continuous symbols), institutional quality, Python client. | Overkill for a daily-bar personal project unless futures matter to results; need to pull and store data yourself; costs scale with data volume. Use the free credit for a one-off daily OHLCV download of the 7 futures to validate yfinance =F series. |
| exchange_calendars | 4.13.2 (March 2026), Python 3.10-3.13, actively maintained. | yes | Authoritative holiday/early-close/session schedules for 50+ exchanges (XNYS, CMES, etc.); is_session, sessions_in_range, previous_close. | No built-in crypto or FX calendars (use a custom 24/7 or Sun-Fri rule); adds a dependency; pin version as calendars update with holiday changes. |
| pandera | 0.33.1 per PyPI JSON read (upload date could not be reliably established through the summariser; verify), Python >=3.10. | yes | Declarative DataFrame schemas with checks, lazy validation producing complete error reports, pandas/polars support. | Another dependency and API churn (pandera.pandas import path); simple invariants can be done in 30 lines of pandas for a project this size. |
| tenacity | 9.1.4 (Feb 2026), Python >=3.10. | yes | Decorator-based retry with exponential backoff, jitter, stop conditions, retry predicates, before_sleep logging. Standard and maintained. | It does not do rate limiting by itself; pair with a token bucket (e.g. pyrate-limiter, or a 15-line limiter) and honour Retry-After manually. |
| Parquet files (pandas/pyarrow) vs DuckDB | DuckDB 1.5.6 on PyPI, MIT, Python >=3.10; parquet via pyarrow. | yes | Parquet per symbol is simple, diffable, portable, and enough for hundreds of symbols of daily bars; DuckDB adds SQL across files and metadata tables with almost no ops. | A server database (Postgres/Timescale/ArcticDB) is overkill for a single-owner daily-bar project. DuckDB is a nice-to-have, not needed on day one; concurrent writers (FastAPI + scheduler + Streamlit) need single-writer discipline or atomic renames. |

**Recommendations**

| Priority | Effort | Change | Files | Acceptance test |
|---|---|---|---|---|
| P0 | S | P0: Fix Alpha Vantage futures mapping and OHLC mixing now. Remove the '=F' to equity fallthrough (return empty for unsupported asset classes), and when using adjusted data scale Open/High/Low by adj_close/close (or use the unadjusted close and record adjusted=False). Raise explicit errors for AV 'Note'/'Information' bodies instead of returning empty. | /home/user/ML-Trading-Platform-Backend/data_fetcher.py | Unit test with a recorded AV payload containing a 2:1 split: assert High >= max(Open, Close) and Low <= min(Open, Close) on every row; test that _fetch_alphavantage('GC=F', ...) never issues an HTTP request and returns empty; test that a {'Note': '...'} payload raises ProviderThrottled (not empty frame). |
| P0 | M | P0: Make the fetch return a Bars result with provenance (source, adjusted flag, tz, fetched_at, requested/actual range) and enforce one source per symbol per request group. fetch_pair must fetch both legs from the same source (or the same cached source) and align on normalised session dates. Never fall back silently to a different source mid-run: either use the cached source's data flagged stale or fail visibly. | /home/user/ML-Trading-Platform-Backend/data_fetcher.py, /home/user/ML-Trading-Platform-Backend/config.py | Test with mocked providers where Alpaca fails on the 2nd call: three consecutive loads of 'AAPL' for the TechnicalScanner all report the same source and identical frames (or a stale-flagged cached frame). fetch_pair('AAPL','MSFT') with Alpaca timestamps at 05:00Z for one leg and yfinance midnight for the other returns >= 250 aligned rows, not None. |
| P0 | M | P0: Normalise daily timestamps to the exchange session date (convert tz-aware UTC to exchange tz, then to date) for every adapter, strip the in-progress bar (drop any bar whose session has not closed using exchange_calendars), and replace the hard-coded 'T23:59:59Z' Alpaca end with min(end, now - 16 min) plus an explicit feed param (feed=iex or sip as configured). Log the Alpaca rejection reason at WARNING. | /home/user/ML-Trading-Platform-Backend/data_fetcher.py | With a frozen clock during the NYSE session, fetch_ohlcv('SPY') last index equals the previous completed session; Alpaca index values are all midnight (no 04:00/05:00 components); with a stubbed 403 'subscription does not permit querying recent SIP data' response the log contains a WARNING naming the symbol, and the fetch does not raise or switch source silently. |
| P0 | M | P0 (data safety for orders): add a symbol registry with a provider capability matrix (asset_class, per-provider native symbol or None, adjustment support, supported timeframes, limits). Alpaca stock and crypto only; forex and futures never sent to Alpaca (also needed so paper_trader never submits '=X'/'=F' symbols; expose registry.alpaca_symbol(sym) for order code). | /home/user/ML-Trading-Platform-Backend/data_fetcher.py, /home/user/ML-Trading-Platform-Backend/config.py | Parametrised test over every symbol in config.WATCHLIST and config.PAIRS: asset_class is resolved, every provider returns either a native symbol or None, 'GC=F', 'EURUSD=X' resolve to None for alpaca and alphavantage-equity, 'BTC-USD' -> 'BTC/USD' for Alpaca data, 'BRK-B' -> 'BRK.B'; an unknown symbol raises UnknownSymbol. |
| P1 | M | P1: Add a local parquet cache with incremental updates: data/cache/{asset_class}/{symbol}__{timeframe}__{source}__{adj}.parquet, atomic writes, overlap re-fetch of the last ~5 sessions, full refetch when overlap values differ beyond tolerance (corporate action), TTL for 'today', and a refresh=True flag. All views (scanner, pairs, scheduler, Streamlit) call the cache API only. | /home/user/ML-Trading-Platform-Backend/data_fetcher.py, /home/user/ML-Trading-Platform-Backend/config.py, .env.example | Mock HTTP: first call downloads 2y (1 request set), second call within the same session makes 0 requests, a call next session makes exactly 1 incremental request starting at last_date minus overlap; the TechnicalScanner's three views for one symbol result in 1 provider call. Kill the process mid-write: the parquet is not corrupted (old file intact). |
| P1 | M | P1: Shared HTTP layer with requests.Session per provider, tenacity retry (exponential backoff + jitter, retry 429/5xx/timeouts only, honour Retry-After), a per-provider token-bucket limiter plus a persisted daily-quota counter (AV 25/day, Tiingo 1000/day & 500 symbols/month), and a circuit breaker with cooldown. Surface failures at WARNING with provider, symbol and reason, and return a structured FetchError list with the final result. | /home/user/ML-Trading-Platform-Backend/data_fetcher.py, /home/user/ML-Trading-Platform-Backend/requirements.txt | With a stub server returning 429 with Retry-After: 2 twice then 200, the adapter succeeds after about 4 s with exactly 3 requests; a 401 is not retried; scanning 42 symbols with AV configured never exceeds 5 req/min or 25/day (assert via fake clock), and the 26th call raises QuotaExhausted without issuing an HTTP request. |
| P1 | M | P1: Add a pandera (or plain) OHLCV validator run at every adapter boundary and on cache load: unique monotonic index, OHLC > 0, High/Low invariants, Volume >= 0, max abs 1-day return threshold (configurable, flag only), staleness vs the exchange calendar, expected row count vs requested range. Return a DataQuality report in metadata; replace the len >= 30 gate with a calendar-based coverage ratio (e.g. >= 95% of expected sessions); do not cache or serve frames that fail hard checks. | /home/user/ML-Trading-Platform-Backend/data_fetcher.py, /home/user/ML-Trading-Platform-Backend/features.py | Fixtures: frame with High < Close, duplicate timestamp, NaN Close, a 90% one-day jump, stale last bar, and a 31-row frame for a 2y request each produce the specific expected failure/flag; compute_features refuses (raises) on a frame that failed hard validation. |
| P1 | M | P1: Per-asset-class provider priority and source tier in config (e.g. stocks: tiingo|alpaca -> yfinance; crypto: alpaca -> yfinance; forex: yfinance (documented best-effort) or Massive paid; futures: yfinance flagged 'continuous, unadjusted, roll unknown'). Implement a Tiingo adapter (adjusted + raw OHLC supplied together) and drop or demote Alpha Vantage. Add keys/priority/cache dir/tier to .env.example with comments about free limits and ToS. Wire POLYGON_API_KEY to a Massive adapter only if a paid plan is purchased. | /home/user/ML-Trading-Platform-Backend/config.py, .env.example, /home/user/ML-Trading-Platform-Backend/data_fetcher.py | Config test: every asset class has a non-empty provider list containing only providers whose capability matrix supports the class; with only yfinance configured, futures return data tagged source='yfinance' and meta.warnings contains 'continuous_unadjusted'; Tiingo adapter golden test on a recorded split day returns consistent adjusted O/H/L/C. |
| P1 | M | P1: Pin dependencies (pip-tools or uv lock; at minimum pandas<3, numpy, yfinance, alpaca-py exact), add pytest with recorded fixtures for each adapter, calendar and validator, and a CI/pre-commit command. Add exchange_calendars, tenacity, pyarrow, pandera to requirements. | /home/user/ML-Trading-Platform-Backend/requirements.txt, /home/user/ML-Trading-Platform-Backend/tests/test_data_layer.py | Fresh venv from the lock file installs the same versions; 'pytest tests/test_data_layer.py' passes offline (no network) and fails if an adapter's output schema drifts. |
| P1 | S | P1: Make features.py robust to data issues: assert a validated, sorted, unique index at entry; keep target NaN as NaN (drop those rows) rather than 0; define 'week/month/quarter' returns by bars with a documented per-asset-class bars-per-year constant from the registry so crypto (365) and stocks (252) features are comparable or at least labelled. | /home/user/ML-Trading-Platform-Backend/features.py | Test: the final 1/3/5 rows have NaN targets and are excluded by prepare_ml_data; passing an index with duplicates or unsorted timestamps raises; feature column set is identical for a stock and a crypto frame. |
| P2 | S | P2: Add a data-status endpoint/CLI command (python main.py data-status) listing per symbol: source, adjusted flag, last session, coverage %, quality flags, cache age; show source badge in the frontend scanner/pairs views. | /home/user/ML-Trading-Platform-Backend/data_fetcher.py, /home/user/ML-Trading-Platform-Backend/main.py | CLI output lists all 42 watchlist symbols with a source column and no 'unknown'; API response JSON contains source and quality for a given symbol. |
| P2 | M | P2: Use the Databento free credit for a one-off pull of daily OHLCV for the 7 futures (GC, SI, CL, NG, ES, NQ, YM) with explicit roll rule and compare to yfinance =F series; keep Databento only if discrepancies matter to results. Optionally add DuckDB for ad hoc queries across the parquet store. | /home/user/ML-Trading-Platform-Backend/data_fetcher.py | Comparison script outputs per-symbol return-correlation and max roll-day discrepancy between Databento continuous and yfinance =F; a decision note records whether to adopt. |

**Open questions**

- Which markets actually drive decisions or orders (stocks/crypto only, or futures/forex too)? That decides whether paid futures/FX data (Databento/Massive) is worth anything.
- Do you have a paid Alpha Vantage or Alpaca market-data plan? I could not reach alphavantage.co, docs.alpaca.markets, tiingo.com or databento.com directly (network egress blocked), so AV premium-only endpoints, Tiingo terms and Alpaca/Databento limits come from search snippets and third-party summaries and should be verified against the official pages before purchase.
- Is data ever redistributed or used beyond personal use (frontend shared with others)? That changes the yfinance and free-tier ToS risk.
- Daily bars only, or are intraday timeframes planned? Intraday multiplies quota, storage and calendar complexity (and Alpaca free IEX volume caveat).
- Version facts to re-verify before pinning: pandera 0.33.1 (PyPI date unclear), alpaca-py 0.44.0 (reported upload date Jan 2025 looks inconsistent), Massive's new Python client/base URL name after the Polygon rebrand.
- Is the Alpaca SIP 15-minute rejection actually triggered by today's end param on a free key? Likely from the docs but not reproduced (no keys available here).

### risk-execution
**Current state.** Risk/execution is effectively unguarded. Line numbers below are from the files as read.

RiskManager (risk_manager.py)
- Instantiated at main.py:52 but never called by the order path. register_position, close_position and update_capital are also never called, so can_trade (:48-57) is dead code.
- State is in memory only (:22-28). `halted`, `peak_capital` and `open_positions` are lost on restart, and the scheduled job (main.py:94-112) is a long-lived loop that can restart at any time. The halt cannot survive a crash.
- calculate_position_size (:59-100) computes risk_per_share (:82-84) but never uses it. Shares = capital*5%*scalar/price (:90), so there is no risk-based or volatility-based sizing. It also ignores shorts and has no per-symbol notional cap, no gross/net exposure cap and no liquidity check. can_trade is a hard stop only at >=15% exposure, so a single order can overshoot it.
- check_correlation (:102-135) is never used. It uses only the last N bars of the passed data and a hard-coded threshold.
- The drawdown halt is based on internal capital, not broker equity. There is no daily-loss limit, no max-orders-per-day, no kill-switch file or env var, and no manual unhalt.

paper_trader.py
- The sizing path ignores RiskManager entirely (:177-185).
  - confidence = win_rate*profit_factor (main.py:123) can be ~1e9, so the max(0.5, confidence) floor (:185) is the only clamp and there is no ceiling.
  - Qty is bounded only indirectly by min(PAPER_TRADE_MAX_ORDER_VALUE=1000, buying_power*5%) multiplied by the unbounded confidence. A $1000 cap multiplied by 1e9 is a huge order, and buying_power with margin is the wrong base (equity should be used).
  - qty is floored with max(1, ...) (:185), so it can force a 1-share order that exceeds the cap on expensive symbols.
- Paper mode is hard-coded with paper=True (:31). That is the safe default, but there is no explicit mode setting. ALPACA_BASE_URL (config.py:62) is defined and unused, and there is one key pair for everything (config.py:60-61), including data_fetcher. Going live would need a code edit with no guard against mixing keys.
- submit_order (:62-117):
  - There is no client_order_id, so retries and scheduled re-runs create duplicates. This is the biggest idempotency gap.
  - TIF is "day" or GTC only (:89). Crypto accepts only gtc/ioc, so "day" is rejected for crypto.
  - Symbols are passed unmapped. yfinance-style 'BTC-USD' must be 'BTC/USD' on Alpaca, and '=F' futures and '^' indices are not tradeable there.
  - Unknown order_type, or a limit with no price, silently falls through to a market order (:91-102). stop_price is accepted and ignored.
  - There are no bracket/OCO/OTO legs, so positions get no protective stop or take-profit even though STOP_LOSS_PCT and TAKE_PROFIT_PCT are in config.
  - `except Exception` returns None (:117), which hides every failure class from callers. A rejection, a timeout (order may still have been placed) and a bug all look the same.
- execute_signal (:161-186):
  - direction -1 submits a plain sell with no position check, so it opens an unintended short. Short rules are not handled (shortable/easy_to_borrow, no fractional shorts, margin).
  - It does not check for an existing position or open orders, does not check market hours, and does not check order status afterwards.
  - It calls get_account on every signal (an extra API call each time) with no staleness check.
- get_positions (:145-156) returns [] on any error, so a broker outage looks like a flat book. There is no reconciliation of RiskManager state with broker state. get_account reads daytrade_count but nothing acts on it.

main.py
- scan_technical (:122-124) executes once per (symbol, pattern) hit, so the same symbol can be ordered once per pattern in one run.
- Two scheduled runs (the same bar on consecutive days, or a restart) re-order identically, because there is no dedup or persisted signal ledger. check_recent_signal (:59-62) looks back over a window and prefers BUY.
- Pairs "paper trade" only logs (main.py:~140-146). ML confidence is a 0-1 value, but the technical path uses win_rate*PF.
- Execution is wrapped in a broad try/except that logs a warning (:~129), so order failures are lost.
- --paper plus PAPER_TRADE_ENABLED are two independent switches with no live/paper concept.

config.py
- Risk constants exist (:93-98) but are not enforced anywhere. There is no validation (for example 0<pct<=1), no trading-mode setting, and no per-asset-class limits.
- Secrets are read straight from the environment (:60-61, :144-153) and there is no redaction layer.
- CLAUDE_MODEL is hard-coded and deprecated (:160), out of scope here.

alerts.py
- _telegram (:87-95) uses parse_mode "Markdown" (legacy) with no escaping. Underscores, asterisks and brackets in pattern names such as ema_crossover break the send with HTTP 400, and the alert is lost.
- resp.raise_for_status() (:94) raises an HTTPError whose message contains the full URL including /bot<TOKEN>/. It is logged at :74 as f"Alert failed ({method}): {e}", so the bot token leaks into logs. The SMTP and Discord paths have the same pattern: the Discord webhook URL is itself a secret.
- There is no retry or backoff, and no handling of Telegram 429 `retry_after`. There is no 4096-char (Telegram) or 2000-char (Discord) truncation, and no dedup or throttling of alerts.
- send_alert (:411-424) falls back to console, and that is the only fallback. _discord and _email return False silently when unconfigured.
- Alerts fire before and independent of order submission, so an alert can say 'BUY' while the order was rejected. There is no 'order rejected / halted / reconcile mismatch' alert class.
- Emoji and '=' banners are baked into the message with no structured fields.

**Best practices**

- Single order chokepoint: every order (paper or live, CLI, API or scheduler) goes through one function that runs ordered pre-trade checks and returns a structured Decision(approved, qty, reasons). The broker client is private to that module so nothing can bypass it.: A chokepoint is the standard pre-trade-risk architecture, and it is proportionate for a one-person project: one function, no framework. It makes the RiskManager that is currently dead code actually load-bearing. (Industry practice (SEC Rule 15c3-5 'market access' pre-trade controls are the institutional analogue); no single primary URL fetched)
- Check order, cheapest and most severe first: kill switch and halt, then mode and credential sanity, then symbol validity and tradability (Alpaca /v2/assets: tradable, shortable, easy_to_borrow, fractionable), then dedup (existing position or open order, idempotency key), then size caps (per-order notional, per-symbol %, gross exposure, max open positions, daily order count, daily loss), then market-hours/TIF compatibility, then submit. Fail closed: any exception or missing data means reject.: Prevents the failure mode seen at main.py:123/paper_trader.py:185 where an unbounded input becomes an unbounded order. (https://docs.alpaca.markets/docs/working-with-orders (reached via search only; docs.alpaca.markets was blocked from direct fetch))
- Deterministic client_order_id built from (strategy, symbol, side, signal bar date), for example sha1 truncated, or a readable 'tp-{pattern}-{symbol}-{YYYYMMDD}-{side}' within Alpaca's length limit. Alpaca rejects a duplicate client_order_id, so a rerun on the same bar is a free no-op. Treat a 'client_order_id must be unique' error as 'already placed', and look the order up with get_order_by_client_id.: This is the broker-side half of dedup. It also resolves the ambiguous-timeout case (the order may exist even though the call raised). Pair it with a local ledger because an ID is only unique among that account's live and recent orders. (https://docs.alpaca.markets/docs/working-with-orders (via search; the 128-char limit was not confirmed in the snippets, so verify))
- Bracket orders (order_class='bracket', take_profit.limit_price, stop_loss.stop_price) for equities so the exit lives at the broker. Use OTO for stop-only, OCO to exit existing positions. Bracket legs need a TIF of day or gtc. Do not use brackets for crypto (Alpaca crypto supports only market, limit and stop_limit with gtc or ioc), so crypto needs a locally managed exit or a stop_limit order.: Server-side exits survive a crashed scheduler. This closes the gap that STOP_LOSS_PCT and TAKE_PROFIT_PCT exist in config but are never sent. (https://docs.alpaca.markets/docs/working-with-orders (via search); https://docs.alpaca.markets/docs/crypto-orders (via search))
- Bounded sizing: qty = min(risk_budget/stop_distance, vol_target_notional/price, per_symbol_cap/price, buying power buffer). Risk budget is about 0.25-1% of equity per trade. Volatility target means notional = (target_daily_vol * equity)/(ATR% or EWMA sigma). Confidence may only scale down (0..1 clamp) and must never feed a raw ratio such as profit factor. If you use Kelly, use at most quarter to half Kelly capped at the per-symbol limit, with edge estimated out-of-sample and shrunk.: Full Kelly is extremely sensitive to estimation error, and the in-sample backtest stats that feed it here are already known to be inflated. A hard cap is the real protection and sizing is the refinement. (Thorp, 'The Kelly Criterion in Blackjack, Sports Betting and the Stock Market' (fractional-Kelly rationale); Standard ATR/vol-targeting practice (Carver, 'Systematic Trading'); not fetched this session)
- Explicit TRADING_MODE in {off, paper, live} with separate env vars (ALPACA_PAPER_KEY/SECRET, ALPACA_LIVE_KEY/SECRET). Construct TradingClient(paper=(mode!='live')). Live requires a second confirmation (for example LIVE_TRADING_CONFIRM=<account id suffix>), refuses to start if the live account id is found with paper keys or vice versa, and logs the account number and mode at startup. Keep market-data keys separate from trading keys.: Alpaca paper and live use different keys and base URLs. A wrong key or mode combination is the classic way to send real orders by accident. (https://docs.alpaca.markets/docs/paper-trading (not fetched; blocked); alpaca-py 0.44.0 TradingClient(paper=...) on PyPI)
- Persist risk state in a small SQLite file (WAL mode): kill-switch flag, peak equity, day-start equity, halt reason and timestamp, a signal ledger (key, ts, status), and an order log (client_order_id, broker id, status). Load it at startup. Do the halt check against broker equity (get_account) and not against internal pnl. Resetting requires an explicit CLI command, never an automatic reset.: A halt that clears on restart is not a halt. SQLite is in the stdlib and enough for a single owner. A real database or queue is overkill. (SQLite documentation (WAL); standard practice)
- Reconcile on every run start and before every order batch: pull broker positions and open orders (raising on error, not returning []), and compare them to the ledger and RiskManager. On a mismatch, alert and block new entries until resolved. Use the broker as the source of truth. Subscribe to trade updates (alpaca-py TradingStream) only if long-running, otherwise poll get_orders.: The current get_positions returns [] on error, which is the dangerous direction (it looks flat, so the system re-enters). (https://alpaca.markets/sdks/python/trading.html (alpaca-py docs; not fetched))
- Alpaca specifics. Equities: TIF day/gtc, and fractional orders must be day and are not allowed in brackets (verify against current docs). Crypto: symbols use slash form 'BTC/USD', TIF only gtc or ioc, order types market/limit/stop_limit, fractional via qty or notional, no shorting. Shorting equities: easy-to-borrow only by default, check asset.shortable and asset.easy_to_borrow, whole shares only, margin account required, overnight maintenance of about 30% (price >= $5) or the greater of $2.50/share (price < $5). Futures and indices ('=F', '^') are not tradeable on Alpaca, so map or reject them.: Rejections for these reasons currently surface only as a swallowed exception returning None. (https://docs.alpaca.markets/docs/crypto-orders (via search snippet); https://docs.alpaca.markets/docs/margin-and-short-selling (via search snippet); https://docs.alpaca.markets/docs/working-with-orders (via search snippet))
- Telegram: prefer parse_mode='HTML' with html.escape() on all dynamic text, or send plain text with no parse_mode. If using MarkdownV2, escape _ * [ ] ( ) ~ ` > # + - = | { } . ! with a backslash (inside code blocks only ` and \, and inside link URLs only ) and \). Truncate to 4096 chars, handle 429 retry_after, and retry with a plain-text fallback on a 400 'can't parse entities' error.: Pattern names contain underscores, so legacy Markdown fails today, and the failed alert is the one you most needed. (https://core.telegram.org/bots/api#formatting-options (fetch blocked; escape set confirmed via search snippets only); https://github.com/utterstep/telegram-escape)
- Secret redaction: never log exception strings from requests directly. Catch requests.RequestException and log type(e).__name__ plus status code. Install a logging.Filter on the root handlers that regex-replaces known secrets (loaded from config at startup) and the pattern bot\d+:[\w-]+. Store secrets in a pydantic SecretStr or similar so repr() prints '**********'. Put the Discord webhook URL in the same bucket.: The bot token is in the URL path, so any HTTPError string carries it (alerts.py:92-94, logged at :74). (https://docs.pydantic.dev/latest/api/pydantic_extra_types/ (SecretStr; not fetched); https://docs.python.org/3/library/logging.html#filter-objects)

**Tool options**

| Tool | 2026 status | Recommended | Pros | Cons |
|---|---|---|---|---|
| alpaca-py (official SDK) | 0.44.0, released 2026-08-11 on PyPI (actively maintained). The repo pins only >=0.21.0. | yes | Official, supports bracket/OCO/OTO request classes, crypto, client_order_id, get_order_by_client_id, TradingStream, and paper=True/False. | The requirements pin is far too loose. Pin a tested minor (alpaca-py~=0.44) and re-test on upgrade. |
| Hand-rolled single-chokepoint risk gate (a new execution.py plus a trimmed RiskManager) | No dependency; roughly 200-300 lines. | yes | Proportionate, fully testable, and it fits the paper-to-live path with no framework lock-in. | You own the correctness, so write the acceptance tests below. |
| SQLite (stdlib sqlite3) for state, ledger and kill switch | Stdlib; WAL mode. | yes | Persists across restarts, atomic, one file, and the INSERT ... UNIQUE(signal_key) constraint gives race-safe dedup. | Slightly more code than a JSON file. Avoid a JSON state file, which can be corrupted by a crash mid-write (if you use one, write to a temp file and os.replace). |
| IBKR via ib_async (the maintained fork of ib_insync) or IBKR Client Portal Web API | Not verified in this session (no fetch of PyPI or GitHub for it); verify before adopting. | no | Only needed for futures, FX and wider asset coverage that Alpaca lacks. | Needs a running TWS or IB Gateway, with a different order and contract model. Do not build the abstraction now. |
| Broker-abstraction layer (a Broker protocol with AlpacaBroker and a FakeBroker) | Small interface of about 6 methods. | yes | A FakeBroker makes the acceptance tests offline and deterministic, and gives a cheap seam for a future IBKR adapter. | Keep it tiny. Do not build a general multi-broker framework. |
| Heavy OMS/EMS or trading frameworks (NautilusTrader, Lean, Freqtrade, etc.) | Not evaluated for 2026 versions. | no | Mature order and risk machinery. | Overkill for a single-owner scanner that places a handful of orders a day, and it would mean a rewrite. |
| Telegram: raw requests with HTML parse_mode (current approach, fixed) vs python-telegram-bot | requests 2.34.2 (2026-05-14). python-telegram-bot was not version-checked. | yes | Raw requests is enough for sendMessage. Add html.escape, a length cap, and a retry on 429. | python-telegram-bot is async and heavy, which is unneeded for one-way alerts. |
| tenacity for retry/backoff | 9.1.4 (2026-02-07), maintained. | yes | Clean decorator for alert and read-only broker call retries. | Never auto-retry order submission with a fresh client_order_id. Retry only with the same ID. A 20-line manual loop also works. |
| pydantic-settings for config validation and SecretStr | 2.15.0 (2026-08-07), maintained. | yes | Validates the risk constants and the mode, and hides secrets in repr. | A new dependency. Plain dataclass validation in config.py is an acceptable lighter alternative. |

**Recommendations**

| Priority | Effort | Change | Files | Acceptance test |
|---|---|---|---|---|
| P0 | M | Create ONE order chokepoint, `submit_intent(intent) -> Decision`, in paper_trader.py (or a new execution.py). Make the Alpaca client private to it and remove direct submit_order use from main.py. Call RiskManager.check(intent, account, positions, open_orders) inside it. Fail closed on any exception or missing data. Return a structured Decision(status in {submitted, rejected, duplicate, halted, error}, reasons, order_id) and never None. Have main.py log, alert and count the decisions. | /home/user/ML-Trading-Platform-Backend/paper_trader.py, /home/user/ML-Trading-Platform-Backend/risk_manager.py, /home/user/ML-Trading-Platform-Backend/main.py | With a FakeBroker, call the old direct-submit path and assert it is gone or private. Submit an intent while RiskManager.halted=True and assert zero broker calls and Decision.status=='halted'. Make get_account raise and assert status=='error' with zero broker calls (fail closed). |
| P0 | M | Bound the qty. Remove win_rate*profit_factor as a size input (main.py:123). Clamp confidence to [0,1] and use it only as a down-scalar. Size as min(risk_budget/stop_distance, vol_target_notional/price, per_symbol_cap, per_order_cap, remaining gross-exposure room), based on equity (not buying_power). Allow qty 0 and reject (remove max(1,...) at paper_trader.py:185). Enforce hard limits in config: MAX_ORDER_NOTIONAL, MAX_SYMBOL_PCT, MAX_GROSS_PCT, MAX_OPEN_POSITIONS, MAX_ORDERS_PER_DAY. Use RiskManager.calculate_position_size and make it actually use risk_per_share. | /home/user/ML-Trading-Platform-Backend/risk_manager.py:59-100, /home/user/ML-Trading-Platform-Backend/paper_trader.py:177-186, /home/user/ML-Trading-Platform-Backend/main.py:123, /home/user/ML-Trading-Platform-Backend/config.py:93-98 | Property test: for confidence in {0, 0.5, 1, 1e9, inf, nan}, price in {1, 500, 1e5} and equity in {1e3, 1e6}, assert notional <= min(MAX_ORDER_NOTIONAL, MAX_SYMBOL_PCT*equity) and qty>=0. NaN or inf inputs are rejected. A $1000 cap on a $5000 stock yields qty 0 and a rejection, not 1. |
| P0 | M | Idempotency and dedup. Build a deterministic client_order_id from (strategy/pattern, symbol, side, signal bar date) and pass it to every order. Add a SQLite signal ledger with UNIQUE(signal_key) and insert before submission. Dedup to one order per (symbol, bar) per run regardless of how many patterns fire, and skip entries when the symbol already has an open position or open order. On a duplicate-ID error or a timeout, look the order up by client id and report 'duplicate' instead of resubmitting. | /home/user/ML-Trading-Platform-Backend/paper_trader.py:62-117, /home/user/ML-Trading-Platform-Backend/main.py:93-125, /home/user/ML-Trading-Platform-Backend/main.py:59-62 | Run run_full_scan twice with the same data against the FakeBroker and assert exactly one order per symbol and bar. Two patterns firing for AAPL in one run gives one order. Simulate a timeout after the broker accepted the order, retry, and assert one order and not two. |
| P0 | S | Map and validate symbols before submission. Add a symbol_to_alpaca() that turns 'BTC-USD' into 'BTC/USD', rejects '=F', '^' and other non-Alpaca symbols with reason 'untradable_on_alpaca', and checks /v2/assets for tradable, shortable and fractionable (cached per run). Choose TIF per asset class: crypto gtc (or ioc), equities day (gtc for brackets), and never send TIF 'day' for crypto. Remove the silent fallthrough to market for unknown order types or a limit with no price (paper_trader.py:91-102): raise a ValueError, which the chokepoint turns into a rejection. | /home/user/ML-Trading-Platform-Backend/paper_trader.py, /home/user/ML-Trading-Platform-Backend/config.py | Parametrised unit tests: 'BTC-USD'->'BTC/USD' with gtc, 'ES=F' rejected, '^GSPC' rejected, 'AAPL' day. A limit order with limit_price=None raises and does not become a market order. |
| P0 | S | Short-selling and direction safety. Treat direction -1 as 'close long' when a long position exists, and as 'open short' only if ALLOW_SHORTS is true, the asset is shortable and easy_to_borrow, the account is margin-enabled, and qty is a whole number, with margin headroom checked. Crypto shorts are always rejected. Never submit a sell with no position when shorts are off. | /home/user/ML-Trading-Platform-Backend/paper_trader.py:161-186, /home/user/ML-Trading-Platform-Backend/config.py | FakeBroker with no AAPL position: a SELL intent with ALLOW_SHORTS=false is rejected with 'shorts_disabled'. With a long position it submits a sell for min(position qty, ...). A crypto SELL with no position is rejected. A fractional short qty is rejected. |
| P0 | M | Persisted kill switch and drawdown halt. Add risk_state.sqlite (WAL) holding halted, halt_reason, peak_equity, day_start_equity and the order and signal ledgers. Load it at startup, and update peak and drawdown from broker equity (get_account) on every run and before every order batch. Halt on a total drawdown beyond MAX_DRAWDOWN_HALT_PCT and on a daily loss beyond MAX_DAILY_LOSS_PCT. Also honour a KILL_SWITCH file or env var checked on every order. Add CLI commands `risk status`, `risk halt` and `risk resume --confirm`. A halt never auto-clears. On halt, send an alert and optionally cancel open orders. | /home/user/ML-Trading-Platform-Backend/risk_manager.py:22-57, /home/user/ML-Trading-Platform-Backend/main.py:52, /home/user/ML-Trading-Platform-Backend/config.py | Set equity so drawdown exceeds the limit, assert halted, construct a new RiskManager from the same DB file ('restart'), and assert it is still halted and the order is rejected. `risk resume` without --confirm fails. Touching the KILL_SWITCH file blocks orders without a restart. |
| P0 | S | Fix alert reliability. Switch Telegram to parse_mode='HTML' with html.escape on every dynamic field (or drop parse_mode for the plain-text banner format), truncate to 4096 (Discord 2000), retry on 429 using retry_after and on 5xx with backoff, and retry once as plain text on a 400 parse error. Make send_alert return a result that main.py checks. Add alert classes for order rejected, halt, reconcile mismatch and mode. Send the alert after the order Decision and include the actual outcome. Add a per-run dedup key to avoid duplicate alerts on scheduled re-runs. | /home/user/ML-Trading-Platform-Backend/alerts.py:66-103, /home/user/ML-Trading-Platform-Backend/main.py:117-124 | Mock requests.post: a message containing 'ema_crossover *[x]* a.b!' is sent with HTML escaping and no 400. A mocked 400 parse error triggers one plain-text retry that succeeds. A 6000-char message is split or truncated to <=4096. A 429 with retry_after=1 is retried once. |
| P0 | S | Secret redaction. Never log str(requests exception). Catch RequestException and log the type and status code only. Add a logging.Filter (installed in main.py's basicConfig and in server.py) that masks values of ALPACA_*, TELEGRAM_BOT_TOKEN, DISCORD_WEBHOOK_URL, SMTP_PASS and ANTHROPIC_API_KEY, plus the regex bot\d+:[A-Za-z0-9_-]+. Wrap secrets so repr() is masked (pydantic SecretStr, or a tiny class). | /home/user/ML-Trading-Platform-Backend/alerts.py:74, /home/user/ML-Trading-Platform-Backend/alerts.py:92-101, /home/user/ML-Trading-Platform-Backend/config.py:60-61, /home/user/ML-Trading-Platform-Backend/config.py:144-153, /home/user/ML-Trading-Platform-Backend/main.py:45-49 | Force the Telegram request to fail with HTTP 404 with TELEGRAM_BOT_TOKEN='123456:ABCdef'. Capture the logs with caplog and assert the token string does not appear in any record. Log a message containing the Alpaca secret and assert it is masked. repr(config) never contains a secret. |
| P1 | S | Explicit paper/live separation. Add TRADING_MODE in {off, paper, live} (default off), with separate ALPACA_PAPER_KEY/SECRET and ALPACA_LIVE_KEY/SECRET. Build TradingClient(paper=(mode!='live')). Live requires LIVE_TRADING_CONFIRM matching the account number suffix and a startup assertion that account_number and status fit the mode. Retire the unused ALPACA_BASE_URL and the double switch (--paper plus PAPER_TRADE_ENABLED). Log mode and masked account id at the start of every run, and put the mode in every alert. | /home/user/ML-Trading-Platform-Backend/paper_trader.py:15-35, /home/user/ML-Trading-Platform-Backend/config.py:60-63, /home/user/ML-Trading-Platform-Backend/config.py:163-167, /home/user/ML-Trading-Platform-Backend/main.py | mode=live with no LIVE_TRADING_CONFIRM raises at startup. mode=paper with live keys, or live with paper keys, is rejected by the account check. Default mode=off submits no orders and logs 'dry-run'. A unit test asserts TradingClient is constructed with paper=True unless mode=='live'. |
| P1 | M | Send protective exits at the broker. For equities use order_class='bracket' with take_profit.limit_price and stop_loss.stop_price derived from STOP_LOSS_PCT, TAKE_PROFIT_PCT (or ATR multiples), using whole shares and TIF day or gtc. Use OTO for stop-only. For crypto use gtc stop_limit or a locally managed exit, because brackets are not available. Verify the stop and take-profit prices against tick size (round to 2 decimals) and against the current quote so they are on the correct side of the market. | /home/user/ML-Trading-Platform-Backend/paper_trader.py:62-117, /home/user/ML-Trading-Platform-Backend/config.py:95-96 | FakeBroker records a BUY at 100 with SL 2% and TP 4% as a single bracket request: stop_loss.stop_price==98.00, take_profit.limit_price==104.00, and class 'bracket'. For a short the sides are inverted (SL above, TP below). A fractional qty with a bracket is rejected or converted to whole shares. Verify against a real paper account once. |
| P1 | M | Position reconciliation. On every run start and before each order batch, fetch broker positions and open orders, and make get_positions and get_account raise instead of returning []/None. Diff them against the ledger and RiskManager.open_positions. On a mismatch (unknown position, a missing position, a qty difference) alert and block new entries until `risk reconcile --accept` is run. Rebuild RiskManager exposure from broker positions, not memory. | /home/user/ML-Trading-Platform-Backend/paper_trader.py:131-156, /home/user/ML-Trading-Platform-Backend/risk_manager.py:137-158, /home/user/ML-Trading-Platform-Backend/main.py | FakeBroker holds a position the ledger does not know: the next intent is rejected with 'reconcile_mismatch' and an alert is queued. A broker API error during reconcile blocks trading and does not read as 'flat'. |
| P1 | S | Config validation and one source of truth. Validate the risk constants at import (0<MAX_POSITION_SIZE_PCT<=MAX_PORTFOLIO_RISK_PCT<=1, STOP<TP, caps > 0) and fail startup on bad values. Pin dependencies (alpaca-py~=0.44, requests, pandas<3, tenacity) in a lock file or constraints, and add a minimal pytest suite with FakeBroker covering the P0 items. Document the paper-to-live checklist in the README. | /home/user/ML-Trading-Platform-Backend/config.py, /home/user/ML-Trading-Platform-Backend/requirements.txt | Setting MAX_POSITION_SIZE_PCT=5 in the environment fails startup with a clear message. pytest runs offline (no network, no keys) in under 10s with all of the above acceptance tests green. |
| P2 | M | Pairs and portfolio-level checks (later). Wire check_correlation into the chokepoint with a configurable lookback and a rolling window. Implement real two-leg pairs execution only when both legs pass the checks, using a dollar-neutral hedge-ratio notional, and cancel or flatten leg 1 if leg 2 is rejected. Until then keep pairs as alert-only, with the log line made explicit that no order is placed. | /home/user/ML-Trading-Platform-Backend/risk_manager.py:102-135, /home/user/ML-Trading-Platform-Backend/main.py:~140-146 | Two highly correlated open positions block a third correlated entry. A pairs intent where leg B is rejected leaves no net position at the broker (FakeBroker). |
| P2 | M | Optional and later: add a thin Broker protocol (submit, cancel, get_order_by_client_id, positions, account, assets) with AlpacaBroker and FakeBroker, so an IBKR adapter can slot in later. Do not build the IBKR adapter now. | /home/user/ML-Trading-Platform-Backend/paper_trader.py | The same chokepoint test suite passes unchanged against FakeBroker and against a recorded-fixture AlpacaBroker. |

**Open questions**

- Primary docs (docs.alpaca.markets, core.telegram.org) were blocked by the egress proxy in this environment. The Alpaca and Telegram rules above come from web-search snippets of those pages, not from reading them. These need verifying against the live docs before coding: the client_order_id length and uniqueness window, whether fractional orders can be brackets, whether TIF 'day' is the only allowed value for fractional equities, and current crypto order types and TIF.
- Does the account have margin enabled on the paper account? Short-sale behaviour depends on it. Should shorts be supported at all, or should direction -1 only ever mean 'exit long'?
- PDT rules: Alpaca's day_trade_count is read but unused. Check the current 2026 PDT regime and thresholds before relying on day-trade limits. This was not verified here.
- Is crypto trading intended (Alpaca crypto has no shorts or brackets and uses gtc/ioc), or are futures/indices only data sources? Futures would need IBKR.
- What are the target risk numbers: per-trade risk %, vol target, daily loss limit, max order notional? The $1000 cap and 5/15/10% constants are placeholders.
- Does the scheduler run as one long-lived process (main.py --schedule) or via external cron or CI? This decides whether SQLite state or in-process locking is enough, and whether two runs can overlap (needing a lock file).
- Is the Streamlit dashboard (dashboard.py imports RiskManager) or FastAPI intended to place orders? If so they must also go through the chokepoint. IBKR library status (ib_async) was not verified.
- Should an ambiguous submit (timeout) auto-retry with the same client_order_id, or alert and wait for the operator?

### frontend-engineering
**Current state.** The frontend is a thin ~1,200-line React 19 / Vite 8 / TS 5.9 SPA with six pages (Dashboard, TechnicalScanner, PairsTrading, MLSignals, StressTestLab, Settings). It has no tests, no CI (no .github directory), and no data layer or typed contract with the backend.

Weaknesses, with file:line:
- **Untyped API client.** src/api/client.ts:3-37 is hand-written axios wrappers. Every function returns an untyped AxiosResponse<any>, so the frontend cannot detect backend contract drift.
- **No env-based base URL.** client.ts:3 hard-codes `baseURL: '/api'`. vite.config.ts:8-12 proxies /api to http://localhost:8000 in dev only. A production build has no way to point at a deployed backend. The 300s axios timeout (client.ts:3) and 300s proxy timeout (vite.config.ts:11) paper over synchronous heavy endpoints, so a request can hang for 5 minutes with no progress and no cancel.
- **`any` everywhere.** Examples: `useState<any>` at PairsTrading.tsx:11, MLSignals.tsx:10, StressTestLab.tsx:11, Settings.tsx:6 and TechnicalScanner.tsx:12-14. Callbacks use `(d: any)` at PairsTrading.tsx:36/84-91/116-117, MLSignals.tsx:65-90/122 and TechnicalScanner.tsx:97-148/185. src/plotly.d.ts:1 is `declare module 'react-plotly.js'`, which makes all chart props `any`.
- **Failures are invisible.** The catch blocks at TechnicalScanner.tsx:35, Dashboard.tsx:32, StressTestLab.tsx:25, MLSignals.tsx:17 and PairsTrading.tsx:28 only call console.error, so the user sees a spinner and then nothing. PairsTrading.tsx:14-18 has no catch at all, so a rejected request is an unhandled rejection and the pair list stays empty. Settings.tsx:13 swallows the error with `.catch(() => setLoading(false))`.
- **HTTP 200 errors are treated as success.** The backend returns `{error}` with status 200, and the pages do `res.data.signals || []` (Dashboard.tsx:30) and `dataRes.data.data || []` (TechnicalScanner.tsx:32). A backend error therefore renders as an empty result that looks like "no signals".
- **No error boundary** in App.tsx or main.tsx. A render crash from a missing field, such as `a.backtest.total_return_pct.toFixed`, blanks the whole app.
- **Hand-rolled fetch state.** Each page repeats useState/useEffect loading code. There is no caching, so TechnicalScanner (:29-31) fires 3 requests on every click, with no dedupe and no cancellation on unmount or double-click. Dashboard "Run Full Scan" holds one request open for minutes and is lost on navigation. Results are lost on route change.
- **Contract drift already causing wrong numbers.**
  - Dashboard.tsx:103 prints `total_return_pct.toFixed(1)%`, but the backend value is a fraction, so it displays 100x too small.
  - PairsTrading.tsx:155-159 reads `total_return_pct` and `max_drawdown_pct`, but the backend returns `total_return` and `max_drawdown`. These render as 0.0% through the `|| 0` fallbacks, which hides the bug.
  - MLSignals.tsx:51-52 does `parseFloat(r.metrics.total_return)` on what appears to be a pre-formatted string, so formatting is split between the backend and frontend.
  - StressTestLab.tsx:97 renders `data.total_return` raw.
  - PairsTrading.tsx:36 filters `zscore !== undefined`, but NaN and Infinity from the backend would arrive as invalid JSON or null.
- **Plotly bundle weight.** package.json:14,17 use full plotly.js ^3.4.0 with react-plotly.js ^2.6.0, which ships the ~6 MB full bundle. There is no lazy loading. Every chart component is imported statically on all four chart pages, and none is code-split.
- **Charts are unguarded.** The charts take raw arrays with no useMemo, decimation or `useResizeHandler`. The candlestick at TechnicalScanner.tsx:84 plots a 730-day series.
- **Tooling gaps.**
  - package.json has only dev, build, lint and preview scripts, with no test, typecheck or format step.
  - eslint.config.js uses the default recommended rules, with no `no-explicit-any` enforcement.
  - Dependencies use `^` ranges, and axios is used only for trivial calls.
  - There is no .env.example.
  - There is no Claude analysis UI. The Claude layer is CLI-only on the backend, so nothing is exposed here.

**Best practices**

- Generate the API types from the FastAPI OpenAPI schema and fail CI when the committed types differ from a fresh generation. Use response_model on every backend route so the schema is accurate.: Drift between hand-written frontend types and backend responses is the root cause of the 100x return bug and the total_return_pct / total_return mismatch. Generating types from the contract makes them a compile error. This only works if FastAPI routes declare response models. Today they return raw dicts, which yields an empty schema. (https://openapi-ts.dev/openapi-fetch/; https://openapi-ts.dev/about; https://www.npmjs.com/package/openapi-typescript)
- Use TanStack Query for all server state, with useMutation for POST-style heavy operations and useQuery for reads. For long jobs, switch to submit-then-poll with refetchInterval as a function that returns false when the job is done.: This gives caching, de-duplication, cancellation via AbortSignal, retry with backoff, and results that survive route changes. Polling with refetchInterval stops cleanly on terminal states and pauses in background tabs. Mutations should not auto-retry, because a retried POST must never duplicate side effects. (https://tanstack.com/query/v5/docs/framework/react/guides/polling; https://tanstack.com/query/latest/docs/framework/react/guides/query-retries)
- Validate at the boundary with zod only for the small, high-risk, non-generated payloads. Examples are the metrics block that gets formatted as percentages, and the Claude analysis output once exposed. Do not zod-wrap every endpoint when you already have generated types.: Generated types are compile-time only, so a backend sending null, a string or NaN where a number is expected still crashes at runtime or renders silently wrong. A thin parse step on money-adjacent fields turns that into a visible, logged error. Full runtime validation of everything duplicates the generator and is overkill for a single owner. (https://zod.dev; https://www.npmjs.com/package/zod)
- Add an error boundary at the app shell and per route, plus an explicit isError / empty / loading state on every page. Normalise errors in a single fetch middleware that maps HTTP 4xx/5xx and legacy `{error}` bodies to a thrown typed error.: This removes silent console.error failures and turns render crashes into a recoverable panel instead of a white screen. With TanStack Query, a global QueryCache onError can drive a toast, while the per-page error UI shows retry. (https://react.dev/reference/react/Component#catching-rendering-errors-with-an-error-boundary; https://www.npmjs.com/package/react-error-boundary)
- Configure the API base URL through import.meta.env.VITE_API_BASE_URL, with the Vite proxy used only in dev. Add a .env.example and read the variable in exactly one module.: Vite inlines VITE_-prefixed vars at build time. The current hard-coded /api only works behind the dev proxy or a same-origin reverse proxy. (https://vite.dev/guide/env-and-mode)
- Load Plotly from a partial bundle via react-plotly.js/factory and lazy-load chart components with React.lazy. Use the finance bundle, or a custom bundle, because candlestick is not in the basic bundle.: Full plotly.js is about 6 MB unpacked. Basic dist-min is about 1.2 MB unpacked and about 320 kB gzipped, but it covers only scatter, bar and pie. The candlestick trace is needed for TechnicalScanner. Use plotly.js-finance-dist-min or a custom bundle, and check its availability and size before adopting. Also memoise trace arrays and use `useResizeHandler` and a fixed layout height. (https://community.plotly.com/t/how-can-i-reduce-bundle-size-of-plotly-js-in-react-app/89910; https://github.com/plotly/react-plotly.js)
- Test pyramid for a solo project: Vitest + React Testing Library for components and formatters, MSW for API mocking in unit and integration tests, and a very small Playwright smoke suite run against a mocked or real backend in CI.: The highest-value tests are contract and formatting tests: percentage units, missing fields, error states. These are cheap in Vitest + MSW. Playwright is worth adding only for 2 to 3 happy-path smoke flows and a console-error check. (https://vitest.dev; https://mswjs.io; https://playwright.dev/docs/ci-intro)
- Run a single GitHub Actions workflow on every PR: npm ci, the API type drift check, eslint, tsc -b, vitest, vite build, and Playwright smoke. Enforce `@typescript-eslint/no-explicit-any` as an error.: With no tests and no CI today, nothing stops a regression from reaching the app. Banning `any` is what turns the generated types into actual protection. (https://docs.github.com/en/actions; https://typescript-eslint.io/rules/no-explicit-any/)

**Tool options**

| Tool | 2026 status | Recommended | Pros | Cons |
|---|---|---|---|---|
| openapi-typescript + openapi-fetch (+ openapi-react-query) | npm registry checked 2026-10-04. openapi-typescript 7.13.0 and openapi-fetch 0.17.0 were last modified 2026-06-15. openapi-react-query 0.5.4 was last modified 2026-02-11. openapi-fetch depends only on openapi-typescript-helpers. | yes | Types-only generation with no generated runtime code, so the output is one .d.ts file. openapi-fetch is a ~6 kB fetch wrapper with middleware, and openapi-react-query adds typed useQuery/useMutation hooks. This is the lightest fit for a small FastAPI project. | Pre-1.0 versions for openapi-fetch and openapi-react-query. openapi-react-query moves slowly, so you can fall back to plain useQuery wrapping client.GET. It has no runtime validation, and no polling helpers beyond TanStack's own. |
| @hey-api/openapi-ts | 0.99.0, last modified 2026-09-30 and actively maintained. Pre-1.0, with frequent breaking changes between minor versions. | no | Generates SDK functions, optional zod schemas and TanStack Query hooks via plugins. Used by large projects. | Heavier, with generated runtime code and a fast-moving plugin API that needs more upgrade care. More machinery than a six-page app needs. |
| orval | 8.40.0, last modified 2026-10-04 and very active. | no | Batteries-included generation of react-query hooks, zod schemas and MSW mocks from one config. Good if you want generated MSW handlers for tests. | Generates a lot of code. FastAPI's auto-generated operationIds and schemas often need an override. More config than needed here. |
| @tanstack/react-query | 5.104.1, last modified 2026-10-02. Mature v5 line. | yes | Standard for server state. Provides polling, retries, cancellation, a global error hook and devtools. | Adds a concept layer and a provider. Overkill only if the app stayed at one or two endpoints, which it will not. |
| zod | 4.6.5, last modified 2026-10-02. | yes | Small and widely used. Good for the money-adjacent subset of responses and for Claude analysis output. | Duplicates generated types if applied everywhere. A schema per endpoint is maintenance overhead. |
| Vitest + React Testing Library + MSW | vitest 5.0.3 (peer vite ^6.4 || ^7 || ^8, engines node ^22.12 || ^24 || >=26), @testing-library/react 16.3.3, msw 3.0.2 (node >=22.12). All are active. Verify Node 22.12+ in CI. | yes | Vitest reuses the Vite config and works with Vite 8. MSW lets the same handlers serve tests and a dev mock mode. | Node engine requirement. msw 3 and vitest 5 are major versions, so check release notes for breaking changes when pinning. |
| @playwright/test | 1.63.0, last modified 2026-10-04. | yes | Real-browser smoke tests. Can catch a blank-screen regression or a console error. | Slower CI and browser downloads. Worth keeping to 2 or 3 tests. |
| plotly.js partial bundles via react-plotly.js/factory | plotly.js 4.1.1 and react-plotly.js 4.1.0 (react-plotly peer plotly.js >=3.0.0). plotly.js-basic-dist-min 4.1.1 is about 1.19 MB unpacked, cartesian about 1.50 MB, full dist-min about 6.15 MB. Note that package.json pins ^3.4.0 and ^2.6.0, so an upgrade to 4.x is a separate step. | yes | Cuts initial JS substantially and removes the dependency on the loose `declare module` typing if you add types. | Basic bundle lacks candlestick. Need the finance or cartesian bundle or a custom build. Plotly 4.x is a major bump to test before adopting. |
| Lightweight charts library (e.g. TradingView lightweight-charts) for the OHLC view | Not verified in this research. Check the current version and licence before adoption. | no | Much faster and smaller for large candlestick series. | Second charting library to maintain. Only worth it if Plotly candlestick performance proves poor after bundling and decimation. |

**Recommendations**

| Priority | Effort | Change | Files | Acceptance test |
|---|---|---|---|---|
| P0 | S | Fix the known unit and field mismatches immediately and pin them with tests. Dashboard.tsx:103 must format the fraction consistently with the backend (multiply by 100, or fix pattern_routes.py:91 so the unit is explicit). PairsTrading.tsx:155-159 must read total_return and max_drawdown. Remove the `|| 0` fallbacks that hide missing fields, and render an explicit 'n/a' instead. Create a single src/lib/format.ts (pct, num, usd) that handles null, NaN and Infinity, and use it in every page. | src/pages/Dashboard.tsx, src/pages/PairsTrading.tsx, src/pages/MLSignals.tsx, src/pages/StressTestLab.tsx, src/lib/format.ts | A Vitest + RTL test renders Dashboard with an MSW fixture where total_return is 0.153 and expects '15.3%'. A PairsTrading test with backend-shaped backtest {total_return: 0.08, max_drawdown: -0.12} shows '8.0%' and '-12.0%' rather than 0.0%. A format test confirms pct(null), pct(NaN) and pct(Infinity) all return 'n/a'. |
| P0 | M | Replace silent failures with visible, typed error handling. Add a fetch middleware that throws a typed ApiError for non-2xx responses and for HTTP 200 bodies containing `error`. Add QueryCache and MutationCache onError handlers that show a toast. Add an `<ErrorBoundary>` in App.tsx around Routes and per page. Every page renders distinct loading, error-with-retry and empty states, and no page uses console.error as its only handler. | src/api/client.ts, src/App.tsx, src/main.tsx, src/components/ErrorPanel.tsx, src/pages/*.tsx | With MSW returning 500 for /api/patterns/scan, clicking Run Full Scan shows an error panel with the message and a Retry button. With MSW returning 200 {error:'No data'} the same panel shows. A component that throws during render shows the fallback and the sidebar still works. grep finds no catch block in src/pages that only calls console.error. |
| P1 | M | Generate typed API from FastAPI. Backend first: add response_model Pydantic schemas to every route (coordinate with the backend track, since schemas must also reject NaN and Infinity). Frontend: add `openapi-typescript` as a devDependency, an `api:gen` script that reads backend /openapi.json (or a committed openapi.json file), and write src/api/schema.d.ts. Replace axios with an openapi-fetch client created in src/api/client.ts using a base URL from `import.meta.env.VITE_API_BASE_URL ?? '/api'`. Commit generated types and add `api:check` that regenerates and runs `git diff --exit-code`. | package.json, src/api/client.ts, src/api/schema.d.ts, src/api/openapi.json, .env.example, vite.config.ts | Renaming a field in a backend Pydantic response model and re-running `npm run api:gen && npm run build` makes tsc fail at the page that reads the old field. CI step api:check fails when schema.d.ts is stale. A production build with VITE_API_BASE_URL=https://example.test/api issues requests to that host, verified in a Playwright or Vitest test using the built config. |
| P1 | M | Introduce TanStack Query. Wrap App in QueryClientProvider with sane defaults (retry 2 for GET only, retry 0 for mutations, staleTime a few minutes for static lists such as patterns, pairs and config). Convert the list fetches (patterns, pairs, config) to useQuery and the heavy actions (scan, backtest, analyze, ml predict, stress) to useMutation. Pass the AbortSignal into the client so navigating away or re-clicking cancels. Replace the three Promise.all calls in TechnicalScanner with one query keyed by [symbol, pattern]. Use openapi-react-query only if it keeps hooks short. | src/main.tsx, src/App.tsx, src/hooks/*.ts, src/pages/*.tsx, package.json | Navigating Dashboard -> Settings -> Dashboard does not refetch the patterns list within staleTime (MSW request counter equals 1). Double-clicking Analyze produces one in-flight request. Unmounting a page mid-request aborts it (MSW sees a cancelled request). A GET that fails once with 503 then succeeds shows data without user action. |
| P1 | L | Coordinate with the backend to make long operations (full scan, stress test, ML train/backtest) asynchronous: POST returns a job_id and GET /jobs/{id} returns status, progress and a result or error. On the frontend add a useJob hook built on useQuery with `refetchInterval: (q) => terminal(q.state.data?.status) ? false : 2000`, a progress bar, and a cancel button. Then drop the 300s axios and proxy timeouts to a short normal timeout. If the backend stays synchronous in the short term, keep the long timeout only on those specific mutations and show elapsed time. | src/hooks/useJob.ts, src/pages/Dashboard.tsx, src/pages/StressTestLab.tsx, src/pages/MLSignals.tsx, vite.config.ts, src/api/client.ts | With MSW returning status running x3 then done, the UI shows progress and then results, and the timer stops after done (no further requests). With a failed job the error panel shows the job error. Reloading the page mid-job resumes polling from the stored job_id (kept in URL or localStorage). |
| P1 | M | Remove `any` and add runtime guards where they matter. After type generation, delete `useState<any>`, `(d: any)` callbacks and plotly.d.ts `declare module`. Enable `@typescript-eslint/no-explicit-any` as error. Add zod schemas for only the metrics blocks that are formatted as numbers (backtest metrics, stress assessment, pair backtest) and for the future Claude analysis response; parse them in the client layer so shape errors become a visible ApiError rather than a crash or silent NaN. | eslint.config.js, src/pages/*.tsx, src/plotly.d.ts, src/api/schemas.ts | `npm run lint` fails if `: any` is introduced. An MSW fixture with total_return as the string 'N/A' makes the metrics parse fail and show the error panel instead of rendering NaN%. `tsc -b` passes with zero `any` in src/pages. |
| P1 | M | Stand up the test and CI baseline. Add vitest (jsdom), @testing-library/react, @testing-library/user-event and msw (shared handlers in src/test/handlers.ts), plus `test`, `typecheck` and `api:check` scripts. Add .github/workflows/ci.yml running npm ci, api:check, lint, tsc -b, vitest run, vite build on Node 22.12+ or 24. Add Playwright with a tiny smoke suite (app loads, navigation works, scan flow against mocked responses, no uncaught console errors) as a separate CI job. | package.json, vitest.config.ts, src/test/handlers.ts, src/test/setup.ts, src/**/*.test.tsx, playwright.config.ts, e2e/smoke.spec.ts, .github/workflows/ci.yml | A PR that reintroduces the Dashboard 100x bug or breaks a field name turns CI red. `npm test` runs offline with all network handled by MSW. The Playwright smoke job visits all six routes and fails on any console error or unhandled rejection. |
| P2 | M | Reduce chart cost. Create src/components/Chart.tsx using react-plotly.js/factory with a partial Plotly bundle that includes candlestick (verify finance or cartesian bundle availability for the installed plotly.js major), lazy-loaded with React.lazy and Suspense. Use `useResizeHandler`, `style={{width:'100%'}}`, and memoise trace data with useMemo keyed on the response. Decimate or cap series (for example, the equity curve to a few thousand points) in a selector. Pin plotly.js and react-plotly.js to compatible versions (package.json currently has plotly 3.4 with react-plotly 2.6). | src/components/Chart.tsx, src/pages/TechnicalScanner.tsx, src/pages/MLSignals.tsx, src/pages/PairsTrading.tsx, package.json, vite.config.ts | `vite build` output shows the main chunk well under the previous size and Plotly in a separate lazy chunk (check with rollup-plugin-visualizer or build log). Initial route load does not request the Plotly chunk on Settings. Rendering a 5,000-point candlestick stays interactive in a Playwright trace (no long task over 200 ms is a reasonable target, adjustable). |
| P2 | S | Production config and hygiene. Add .env.example with VITE_API_BASE_URL, document dev vs prod in README, pin exact dependency versions via the lockfile and `npm ci`, add engines (node >=22.12) and .nvmrc. Keep the Vite dev proxy for local use. Note that auth and CORS are backend concerns, but the client middleware should be ready to attach an Authorization header from an env or stored token once the backend has auth. | .env.example, README.md, package.json, .nvmrc, src/api/client.ts | A fresh clone follows the README and runs `npm ci && npm run build && npm test` successfully on Node 22.12. A build with no VITE_API_BASE_URL falls back to /api and logs nothing misleading. Setting the variable changes the request origin. |
| P2 | M | Prepare the Claude analysis UI once the backend exposes it. Add an /analysis page or panel that calls a backend endpoint (never the Anthropic API from the browser, so the key stays server-side) via useMutation or the job pattern, renders markdown safely (react-markdown, no raw HTML), shows model and timestamp, labels it clearly as AI commentary and not a signal, and validates the response with a zod schema (summary, risks, confidence, cited metrics). Show the backtest metrics next to it so users can check claims. | src/pages/Analysis.tsx, src/api/schemas.ts, src/App.tsx | With MSW returning an analysis payload containing a script tag in markdown, the DOM contains no script element. A malformed response (missing summary) shows the error panel. A backend 429 or 5xx shows retry guidance without crashing the page. |

**Open questions**

- Several primary documentation sites (openapi-ts.dev, tanstack.com, playwright.dev) were blocked by the network egress proxy, so doc claims came from search snippets and from the npm registry. Versions and maintenance dates are verified against the registry as of 2026-10-04. The behaviours of openapi-fetch middleware and TanStack refetchInterval should be re-checked in the docs when implementing.
- Does the installed plotly.js major (registry shows 4.1.1, package.json pins ^3.4.0) still publish a finance or cartesian partial bundle with candlestick, and does react-plotly.js 4.1.0 change the factory import? Verify before the chart bundle change.
- Should the backend adopt async jobs (job_id plus polling) for scan, stress and ML? That decides the effort of the P1 polling work, and whether to keep a long-timeout fallback.
- FastAPI routes currently lack response_model (they return raw dicts and sometimes {error} with HTTP 200), so generated types will be near-empty until the backend track adds schemas. Which track owns that, and what is the agreed error envelope?
- Is the app single-user and local, or will it be deployed? If it stays local behind the Vite proxy, the production base-URL and auth work shrinks to documentation.
- Does the owner want Playwright at all? For a solo project, Vitest + RTL + MSW plus the build check may be sufficient, with Playwright limited to a smoke job.

### llm-layer
**Current state.** The whole LLM layer is one 67-line function, claude_integration.py:16-67 (generate_summary). It is called once from main.py:297-302 and nothing else (no routes, no Streamlit, no frontend). Weaknesses:

1. P0, the integration is almost certainly dead today. config.py:160 hard-codes CLAUDE_MODEL = "claude-sonnet-4-20250514". The Anthropic deprecations page (checked 2026-10-04) lists that ID as Retired on 2026-06-15 (replacement claude-sonnet-4-6). Every call now fails. claude_integration.py:65-67 catches Exception, logs one line and returns None. main.py:299-302 then silently skips the briefing. The user sees nothing wrong. Nothing logs the response body or request-id.

2. No typed output. The model returns free prose (prompt at claude_integration.py:25-46) and main.py:301-304 prints it and forwards it to Telegram via send_alert(f"AI ANALYSIS:\n\n{ai}"). Any text could carry anything. The Telegram Markdown problem from the audit (alerts.py:75,92) applies to LLM text with underscores. There is no schema, no field for risk or exposure, and no way to test or score it. Item 5 of the prompt asks for 'recommended total exposure' as prose. Nothing stops a future change from regex-parsing that into an order size.

3. Raw requests (claude_integration.py:49-62). No SDK, so no SDK retry or backoff (default 2 retries, honours retry-after), no typed exceptions (429, 529 and 400 look identical), and no request-id logging. There is a flat 30 s timeout (line 61) and no streaming. That timeout is wrong for current models: Opus 5.5 and Sonnet 5.5 think adaptively, so a briefing can exceed 30 s and then fails silently. max_tokens=1024 (line 58) is shared between thinking and the answer. If the stop reason is max_tokens, or the first content block is a thinking block, content[0]['text'] (line 64) raises KeyError. That error is swallowed, so the briefing is lost. stop_reason is never checked, including 'refusal'. usage is never logged, so there is no cost visibility.

4. Model and keys are not configurable. CLAUDE_MODEL is a literal (config.py:160). It has no os.getenv, unlike the API key on line 159. The API key is checked in two places (claude_integration.py:22 and main.py:297). There are no timeout, max-token, effort or budget settings.

5. Input handling is unbounded. json.dumps(..., indent=2, default=str) of the full signal lists (lines 29-37) means token cost grows with the number of signals, with indent whitespace and no cap. stress_results is accepted but main.py:300 never passes it, so the 'STRESS TEST HIGHLIGHTS' branch is dead. The inputs are the audit's known-bad numbers: in-sample ML backtests, profit_factor of about 1e9, Infinity from /api/pairs/analyze, NaN. The prompt tells Claude to 'cross-reference backtest quality' and to flag 'overfitting', but the data given to it is contaminated. The briefing can confidently praise bogus signals. Strings such as pattern names, symbols and direction emoji go in unescaped, and there is no 'data not instructions' framing. The current risk is low because the strings are self-generated, but it is a bad habit once you add news or other external text.

6. No guardrail structure. LLM output is only printed and alerted today, so there is no live path from text to orders. But the module has no contract that makes this permanent. main.py also imports paper_trader (line 43) next to claude_integration (line 42), and RiskManager is created but unused (main.py:52). When someone adds 'use AI bias to size trades', the cheapest implementation is a regex on prose.

7. Cache and evaluation. The prompt is a single user message with the instructions mixed in with volatile data, so nothing is cacheable (the minimum is only 512 tokens on Opus 5.5 and Sonnet 5.5, so a stable system prompt would cache). There is no eval, no golden fixtures and no record of what the briefing said versus what happened. The project has no tests (audit).

8. Dependency state. requests is the only HTTP dependency (requirements.txt). The anthropic package is not listed. Current anthropic is 1.11.0 on PyPI (Python>=3.10, built on httpx2, and temperature/top_p/top_k removed from the signatures).

**Best practices**

- Use the official SDK (pip anthropic>=1.11,<2) with an explicit client: Anthropic(timeout=..., max_retries=...). Catch typed exceptions in a most-specific-first chain (APITimeoutError, RateLimitError, APIStatusError, APIConnectionError). Log the request-id (message._request_id) and usage on every call.: The SDK retries 408/409/429/5xx and connection errors with exponential backoff and honours retry-after. The API distinguishes 429 (rate or spend cap, which does not recover by retrying), 529 (overloaded) and 400 (a retired or invalid model ID, which should never be retried). Request-id is what support needs. (https://platform.claude.com/docs/en/api/errors.md; https://pypi.org/pypi/anthropic/json)
- Make model IDs env-configurable with a validated default. Default for the single-call briefing: claude-opus-5-5. Cheaper option: claude-sonnet-5-5. Use exact IDs only, with no date suffixes. At startup (or in a `--check-llm` CLI flag), call client.models.retrieve(id) and fail loudly if it 404s.: claude-sonnet-4-20250514 was retired 2026-06-15, which silently broke this project. IDs for current models are dateless pinned snapshots. Opus 5.5 retirement is 'not sooner than 2027-09-22' and Sonnet 5.5 'not sooner than 2027-09-28', and Anthropic gives at least 60 days notice. A startup check turns a silent failure into a loud one. (https://platform.claude.com/docs/en/about-claude/model-deprecations.md; https://platform.claude.com/docs/en/about-claude/models/overview.md)
- Get typed JSON with structured outputs: client.messages.parse(..., output_format=PydanticModel) (or output_config={'format': {'type':'json_schema', ...}}). Keep schemas simple: no minimum/maximum or minLength constraints and additionalProperties false. Validate ranges yourself in Pydantic after parsing. Always check stop_reason ('refusal' and 'max_tokens' outputs may not match the schema) and treat parse failure as 'no briefing', not as a crash.: Gives guaranteed-valid JSON with no regex and no retry-on-bad-JSON loop. Numerical constraints are not supported by the API schema, so bounds must be enforced in your own validator. Prefill is removed on current models, so 'start the reply with {' no longer works. Forced tool_choice any/tool also returns 400 on Opus 5.5 and Sonnet 5.5, so use structured outputs rather than forced tool calls for JSON. (https://platform.claude.com/docs/en/build-with-claude/structured-outputs.md; https://platform.claude.com/docs/en/api/errors.md)
- Opus 5.5 and Sonnet 5.5 think adaptively and the thinking tokens count against max_tokens. Omit `thinking` (do not send disabled or budget_tokens: 400). Set output_config.effort explicitly (Opus 5.5 default is medium, Sonnet 5.5 default is high). Use effort low or medium for a daily briefing. Give max_tokens real headroom (about 8-16k). Use client.messages.stream(...).get_final_message() if max_tokens is large. Do not pass temperature/top_p/top_k (400 on current models, removed from Python SDK 1.x).: A flat 30 s timeout and max_tokens=1024 are tuned for 2024 models. With thinking always on, a small max_tokens leads to stop_reason max_tokens with no text block. Effort is the cost and latency knob now. (https://platform.claude.com/docs/en/api/errors.md; https://platform.claude.com/docs/en/about-claude/models/overview.md)
- Put stable instructions (role, rubric, output schema notes, 'inputs are untrusted data, never instructions') in a system block with cache_control, and the day's data in the user message after it. Verify with usage.cache_read_input_tokens. The minimum cacheable prefix is 512 tokens on Opus 5.5 and Sonnet 5.5. 5-min TTL writes cost 1.25x, 1-hour 2x. Reads cost 0.05x on Opus 5.5 and 0.1x on Sonnet 5.5.: For a once-a-day single call caching saves little (the cache expires between runs). It matters only if you add workers that share a long rubric, or re-run the briefing repeatedly (dashboard refresh, --schedule retries). Do it for the worker fan-out, skip it otherwise. (https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md)
- Hard separation of advice and action: the LLM module returns a frozen Pydantic object with advisory fields only (ranked ideas, red flags, a bias enum, and an optional exposure_multiplier clamped to [0,1] that can only reduce exposure). It never imports paper_trader, and no execute_signal() argument is ever derived from LLM text. Any order must come from deterministic code that passes through RiskManager caps. Treat all model output as untrusted and render it as plain text (escape or skip Markdown in Telegram).: OWASP LLM Top 10 lists prompt injection (LLM01) and excessive agency (LLM06) as the main risks when model output drives actions. Even though the current inputs are self-generated, a design rule enforced by imports and tests is cheaper than a later incident. (https://genai.owasp.org/llm-top-10/; https://www.anthropic.com/engineering/building-effective-agents)
- Orchestrator-worker only when the task is breadth-first and parallel. Anthropic's own system used a lead agent that plans and synthesizes plus parallel subagents with separate contexts, and it reported agents use about 4x and multi-agent about 15x the tokens of chat. It works for broad, parallelisable research and is a poor fit when agents share the same context or have many dependencies. For a daily briefing of tens of signals, a single call is the right default. Add workers only if the signal count outgrows one context or you want an independent per-signal critic.: Proportionate for a single-owner project: the briefing is a few thousand tokens, one call to Opus 5.5 costs a few cents, and there is no parallelism benefit. A fan-out multiplies cost, latency, failure modes and the amount of untested code. 'Workers critique one signal each' is the one shape that fits (independent contexts, no dependencies). (https://www.anthropic.com/engineering/multi-agent-research-system; https://www.anthropic.com/engineering/building-effective-agents)
- Evaluate with a small fixed set, not a framework. Start with about 20 saved real input snapshots (golden fixtures, including adversarial ones: PF=1e9, NaN, empty lists, 1 trade) and (a) deterministic checks (schema valid, every cited symbol or pattern exists in the input, every number quoted matches the input, bias in enum, exposure_multiplier within [0,1], word cap) plus (b) an LLM judge with a single-prompt 0-1 rubric on a cheaper or different model. Keep a human spot-check. Log each briefing with its inputs for later comparison with outcomes.: Anthropic reports that about 20 queries were enough to see large effects from prompt changes, that a single judge prompt with a rubric tracked human judgement best, and that manual review caught what automation missed. Deterministic grounding checks catch the dominant failure for a trading briefing: invented or mis-quoted numbers. (https://www.anthropic.com/engineering/multi-agent-research-system; https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence.md)
- Budget and observe cost: log usage (input, output, cache read/write) and a computed USD cost per call to a JSONL file; enforce a per-run and per-day cap in code (refuse to call when exceeded); use Batch API (50% off, async) for non-urgent worker fan-out; set a spend limit in the Console as a backstop. Use messages.count_tokens before large fan-outs.: Pricing as of Oct 2026: Opus 5.5 $4/$20 per MTok, Sonnet 5.5 $2/$10, Haiku 4.5 $1/$5, batch 50% off. A bug that loops or fans out per-signal can multiply cost. A hard cap in code is the only guard that works before the bill. (https://platform.claude.com/docs/en/about-claude/models/overview.md; https://platform.claude.com/docs/en/api/errors.md)

**Tool options**

| Tool | 2026 status | Recommended | Pros | Cons |
|---|---|---|---|---|
| Official anthropic Python SDK (messages.parse + Pydantic) | anthropic 1.11.0 on PyPI, Python>=3.10, depends on httpx2 (<3,>=2.0.0), pydantic<3. Actively maintained by Anthropic. | yes | First-party; retries, typed errors, request-id, streaming, structured outputs helper, prompt-cache and usage fields. Removes about 40 lines of hand-rolled HTTP. Matches the skill's rule not to use raw requests in a Python project. | New dependency on a 1.x major (httpx2 fork) and Python 3.10+; check the project's Python version. Pin anthropic>=1.11,<2. |
| Keep raw requests | requests 2.x is maintained, but nothing here makes it a good choice. | no | No new dependency. | You re-implement retries, backoff, typed errors, streaming, schema parsing and every API change (thinking, effort, structured outputs). The current code already silently broke on a model retirement. |
| Single call to claude-opus-5-5 with effort medium, structured output | claude-opus-5-5: $4/$20 per MTok, 1M context, 128K output, thinking always on, retirement not sooner than 2027-09-22. | yes | Simplest design that meets the need. Best reasoning for cross-checking signals for suspicious results. A briefing is a few cents. Easy to test. | Slower than Sonnet; thinking tokens add cost; no per-signal independence. |
| Single call to claude-sonnet-5-5 | claude-sonnet-5-5: $2/$10 per MTok, 1M context, thinking adaptive, retirement not sooner than 2027-09-28. | yes | Half the price and faster; likely good enough for summarizing tens of signals. Good default worker model. | Weaker at skeptical cross-checking than Opus; verify with the eval set before trusting it as the only model. |
| Opus orchestrator + Sonnet workers (hand-written, plain SDK calls) | Pattern from Anthropic's multi-agent research system post; no framework needed. Implement as a Python function: Sonnet workers per signal (parallel via ThreadPoolExecutor or Batch API) returning a Pydantic Critique; Opus synthesises the critiques into the final Briefing. | yes | Independent per-signal critique contexts; cheap workers; matches the owner's stated interest. Only about 60-100 lines. Can use Batch for 50% off. | Multi-agent systems use about 15x chat tokens (Anthropic). More failure modes (partial worker failure) and more to evaluate. For a handful of signals it gains little. Build only if the owner wants it and the eval shows the single call is not enough. |
| Claude Agent SDK / Managed Agents / LangChain / LangGraph / CrewAI | Claude Agent SDK is a separate coding-agent product; Managed Agents is Anthropic-hosted (beta). Third-party frameworks not verified here. | no | Useful for open-ended agents with tools and state. | Overkill: this is a fixed pipeline with a known data shape and no tool loop. Adds dependencies, abstraction and upgrade churn for no benefit at this scale. Anthropic's own guidance is to start with simple direct API calls. |
| Eval tooling: plain pytest + saved JSON fixtures + one LLM-judge prompt | pytest is mature. Optional: the claude-api skill's build-eval flow. | yes | Zero new infrastructure; runs offline for deterministic checks (use a recorded or fake client); LLM judge only run on demand (costs money). | Needs about 20 fixtures curated by hand. |
| Heavy eval and observability platforms (Langfuse, promptfoo, Braintrust, etc.) | Not verified in this research. | no | Dashboards, tracing, dataset management. | Overkill for one briefing per day by one owner; a JSONL log of request, usage and output is enough. |

**Recommendations**

| Priority | Effort | Change | Files | Acceptance test |
|---|---|---|---|---|
| P0 | S | P0: Fix the dead model immediately. Replace the literal at config.py:160 with CLAUDE_MODEL = os.getenv('CLAUDE_MODEL', 'claude-opus-5-5') (and CLAUDE_WORKER_MODEL default 'claude-sonnet-5-5'). Add a `--check-llm` CLI flag in main.py (and a startup call) that does client.models.retrieve(model) and prints a clear error. Stop swallowing failures: log at ERROR with exception type, HTTP status and request-id, and send a one-line 'AI briefing failed: <reason>' via send_alert so the owner sees it. | config.py, claude_integration.py, main.py | With CLAUDE_MODEL=claude-sonnet-4-20250514 and a valid key, `python main.py --check-llm` exits non-zero with a message containing 'not found' or 'retired'. A scan run logs an ERROR line with request-id and sends an alert saying the briefing failed. With the default model, the check passes. A unit test with a stubbed client raising NotFoundError asserts the alert text. |
| P0 | M | P0: Replace requests with the official SDK. Add anthropic>=1.11,<2 (and confirm Python>=3.10) to requirements.txt. Build one module-level client Anthropic(timeout=anthropic.Timeout(120, connect=10), max_retries=3) created lazily. Remove the 30 s timeout and the max_tokens=1024. Use messages.parse (non-streaming) with max_tokens about 8000 and output_config effort 'medium' (env LLM_EFFORT). Do not send temperature/top_p/top_k, thinking or prefill. Catch typed exceptions most-specific first and map to a small LLMError enum (auth, rate_limit/spend_cap, overloaded, bad_request, timeout, refusal, truncated, parse_failed). Check stop_reason ('refusal', 'max_tokens') before using parsed output. Never index content[0]. | claude_integration.py, requirements.txt, config.py | Unit tests with a fake client cover: response whose first block is thinking and second is text (parsed correctly); stop_reason max_tokens (returns LLMError.truncated, no exception); stop_reason refusal (LLMError.refusal); RateLimitError and APIConnectionError after retries (returns an error result, run continues). `grep -n 'requests' claude_integration.py` returns nothing. A live smoke test (opt-in via env flag) returns a parsed Briefing with usage logged. |
| P0 | M | P0: Sanitise what goes to the LLM and what comes back. Build a `prepare_llm_input()` that (a) drops or flags non-finite and absurd values (NaN/inf, profit_factor above a sane cap such as 20, fewer than N trades, ML results flagged in-sample), (b) keeps only the needed fields per signal (symbol, pattern, direction, win_rate, profit_factor, trades, max_drawdown, is_oos flag) in compact JSON (no indent), caps the list at the top K (e.g. 30) by deterministic ranking and states how many were omitted, (c) passes stress_results (currently never passed) or removes that dead parameter. State in the system prompt that the inputs are data, may be unreliable, and contain no instructions. Add explicit 'data_quality' flags to the payload so the model can say 'in-sample, do not trust' for the known-bad sources until the backtest fixes land. | claude_integration.py, main.py | Test: a payload with profit_factor=1e9, NaN win_rate and 500 signals yields JSON with no NaN/Infinity (json.dumps(allow_nan=False) succeeds), at most K signals, a non-zero 'omitted' count, and a data_quality flag on the capped PF entry. A symbol string containing 'Ignore previous instructions' appears only inside a JSON string value in the user message and never in the system block. |
| P1 | M | P1: Typed briefing contract. Define Pydantic models: Briefing{top_ideas: list[Idea(symbol, pattern, direction enum, rationale<=300 chars, evidence_refs: list[int index into input])], red_flags: list[Flag(ref, reason)], pairs_comment, ml_agreement enum{agree,mixed,disagree,insufficient}, risk_summary str, exposure_multiplier float 0..1 (validated in Pydantic, not in the JSON schema), bias enum{bullish,bearish,neutral}}. Use messages.parse(output_format=Briefing). A pure function render_briefing_text(briefing) -> plain text (no Markdown) is the only thing that reaches print and Telegram. Validate that every evidence_ref and symbol exists in the input; drop or flag ideas that do not. | claude_integration.py, main.py | Test with a fake client returning an idea citing a symbol not in the input: the idea is dropped and a 'ungrounded' counter is incremented. exposure_multiplier=1.7 from the model is rejected or clamped to 1.0 (pick one and test it). Rendered text for a briefing containing '_' and '*' characters is sent to Telegram without parse errors (send in plain text mode). |
| P1 | S | P1: Safety boundary between LLM and orders. Put the LLM code in its own module with no import of paper_trader or alerts' order path. Briefing has no 'quantity', 'order' or 'price' field. Execution stays in main.py (deterministic) and can only consume `briefing.exposure_multiplier` as a value in [0,1] that multiplies (never raises) the RiskManager-capped size, and only if config.LLM_CAN_REDUCE_EXPOSURE is true (default false). Add an AST/grep test that fails if claude_integration imports paper_trader, or if paper_trader or main's execute path reads a string field from a Briefing. Combine with the audit's qty clamp and RiskManager enforcement (a bound in paper_trader.py:185 and RiskManager called before every order), so even a malicious Briefing cannot enlarge an order. | claude_integration.py, main.py, paper_trader.py, risk_manager.py | pytest: parse claude_integration.py with ast and assert no import of paper_trader/execute_signal. Property test: for any exposure_multiplier in [-5, 5] (including NaN), the final qty is <= the RiskManager cap and <= the qty computed without the LLM. With the flag off, the briefing changes no order qty at all. |
| P1 | S | P1: Cost and latency budget with logging. After each call append a JSON line {ts, model, effort, request_id, input_tokens, output_tokens, cache_read, cache_write, latency_s, usd_estimate, stop_reason, ok} to a local llm_calls.jsonl. Enforce LLM_MAX_USD_PER_RUN and LLM_MAX_USD_PER_DAY (defaults, e.g. $0.50 and $2) by summing the log before calling and refusing (with an alert) when exceeded. Put prices in config as a small dict keyed by model ID, and label them as manually maintained. Optionally call messages.count_tokens first to refuse an oversized payload. | claude_integration.py, config.py | Test: with LLM_MAX_USD_PER_DAY=0.01 and a log showing $0.02 spent today, generate_briefing returns a budget-exceeded result without calling the client (fake client call count 0). Each successful call appends exactly one valid JSON line containing request_id and token counts. |
| P1 | M | P1: Eval harness. Add tests/llm_fixtures/*.json (start with ~20 saved scan snapshots incl. adversarial: PF=1e9, NaN, empty, 1 signal, 200 signals, in-sample ML flagged, conflicting technical vs ML, symbol with injection text) and tests/test_briefing_quality.py. Deterministic checks run offline against a recorded response: schema valid, all refs grounded, every number quoted in text appears in the input within tolerance, exposure in [0,1], word cap, no recommendation on flagged in-sample sources. An opt-in `pytest -m live` mode calls the real model and a judge (Sonnet 5.5, one prompt, 0-1 scores for grounding, calibration/skepticism, usefulness) and writes scores to a CSV so prompt or model changes can be compared. Re-run before changing CLAUDE_MODEL. | tests/test_briefing_quality.py, tests/llm_fixtures/, claude_integration.py | `pytest tests/test_briefing_quality.py` (offline) passes in under 10 s with no network and no API key. Injecting a fabricated symbol into a recorded response makes the grounding test fail. `pytest -m live` prints a score table and the total cost, and refuses to run without an explicit env flag. |
| P1 | S | P1: Dependency and config hygiene for the LLM layer: pin anthropic>=1.11,<2 and pydantic>=2 in requirements.txt, read all LLM settings once from config with env overrides (CLAUDE_MODEL, CLAUDE_WORKER_MODEL, LLM_EFFORT, LLM_TIMEOUT_S, LLM_MAX_OUTPUT_TOKENS, LLM_MAX_USD_PER_RUN/DAY), validate effort and models at import (clear error), and delete the duplicated key checks (claude_integration.py:22 vs main.py:297). Never log the API key or full prompts; log hashes and sizes. Document the settings in a README section. | config.py, requirements.txt, claude_integration.py, main.py | Setting LLM_EFFORT=banana makes config import (or --check-llm) fail with a message naming the valid values. grep of the repo and a captured log from a scan run contains no occurrence of the API key string. Unsetting ANTHROPIC_API_KEY makes the scan complete normally with one INFO line 'LLM briefing disabled'. |
| P2 | M | P1: Make the briefing available outside the CLI (the 'CLI-only' gap) without duplicating logic: add `POST /api/briefing` (or GET with cached latest) in a new routes file that calls the same claude_integration function, returns the typed Briefing JSON plus status, and uses a proper HTTP status for failures (502/503/429) instead of 200 {error}. Run it in a threadpool (def endpoint, not async calling a blocking SDK) and cache the latest result for N minutes keyed by an input hash so a dashboard refresh cannot trigger repeated paid calls. The Streamlit dashboard and React frontend then render the same object. Do this only after the CLI path is solid. | routes/, server.py, claude_integration.py, dashboard.py | Calling the endpoint twice with the same stored scan results within the TTL makes exactly one LLM call (fake client count 1) and returns identical JSON. A simulated RateLimitError returns HTTP 429/503 with a JSON error body, not 200. |
| P2 | L | P2: Optional Opus-orchestrator / Sonnet-workers analysis layer, built only if the owner wants it and only after the single-call path, schema, budget log and evals exist. Design: (1) deterministic code ranks and caps signals; (2) a worker (CLAUDE_WORKER_MODEL=claude-sonnet-5-5, effort low/medium, cached system block holding the shared rubric, via ThreadPoolExecutor with a concurrency limit of about 4 or the Batch API for 50% off when not time-critical) returns a Pydantic Critique{ref, verdict enum{credible,weak,suspect}, reasons<=3, data_quality_issues}; one worker failure yields verdict 'unreviewed' and does not abort; (3) the orchestrator (claude-opus-5-5, effort medium/high) receives only the compact critiques plus portfolio-level data and returns the final Briefing. Workers and orchestrator can never call tools or touch orders. Compare against the single-call baseline on the eval set and keep the fan-out only if it scores clearly better; state the cost multiple in the log. | claude_integration.py, config.py, tests/test_briefing_quality.py | With a fake client where 2 of 10 worker calls raise APITimeoutError, the briefing is still produced, those 2 signals carry verdict 'unreviewed', and the log shows 12 calls. A cost test asserts per-run cost cap aborts further worker calls when exceeded. The eval report shows judged scores for single-call versus orchestrated on the same fixtures, with USD per briefing for each. |
| P2 | S | P2: Prompt caching for repeated or fan-out calls. Move the stable role, rubric and 'untrusted data' text into a system block with cache_control (5 min TTL; 1h only if workers are spread over longer than 5 min), keep it above 512 tokens, keep it byte-stable (no timestamps, sorted keys), and put volatile data after it. Log cache_read_input_tokens and assert it is non-zero on the second worker call. | claude_integration.py | Live opt-in test: two consecutive worker calls within 5 minutes show cache_read_input_tokens > 0 on the second. Unit test: the rendered system block is identical across two runs on different dates. |

**Open questions**

- Does the owner actually want the Opus-orchestrator / Sonnet-workers layer, or is one briefing call enough? This research recommends one call first and fan-out only if the eval shows a gain. Pricing as of Oct 2026 is Opus 5.5 $4/$20 and Sonnet 5.5 $2/$10 per MTok, so cost is small either way.
- Should any LLM output ever influence position sizing? This research recommends no by default, and at most a reduce-only multiplier in [0,1] behind a config flag. The owner needs to confirm.
- Should the default single-call model be claude-opus-5-5 (best skepticism) or claude-sonnet-5-5 (half the price)? This should be decided from the eval set rather than assumed.
- What Python version does the project run? anthropic 1.11.0 needs Python>=3.10 (and pulls in httpx2). pandas 3.x and the other unpinned requirements are already a concern in the audit.
- The briefing is only as good as the signals. Until the audit's backtest fixes (in-sample ML, Sharpe/rf bug, PF=1e9) land, should the briefing be flagged or suppressed for those sources? This research recommends passing data_quality flags and telling the model to discount them.
- Is a daily or per-run USD cap acceptable as a hard stop, and what values (suggested $0.50 per run and $2 per day)? Also should a Console spend limit be set as a backstop?
- Prices in config are hand-maintained (no pricing API found in this research); the owner should accept a manual update when models change, or skip USD estimates and track tokens only.
- I did not verify OWASP LLM Top 10 or the 'Building effective agents' post by fetching them (the URLs are cited from memory); everything else cited above was fetched on 2026-10-04.

## Sources (from the plan)

- https://kernc.github.io/backtesting.py/doc/backtesting/backtesting.html
- https://vectorbt.dev/
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1821643
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- https://papers.ssrn.com/abstract=2460551
- https://www.ssrn.com/abstract=2326253
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
- https://www.econometricsociety.org/publications/econometrica/2000/09/01/reality-check-data-snooping
- https://arch.readthedocs.io/en/latest/multiple-comparison/introduction.html
- https://arch.readthedocs.io/en/latest/bootstrap/timeseries-bootstraps.html
- https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html
- https://en.wikipedia.org/wiki/Purged_cross-validation
- https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
- https://hypothesis.readthedocs.io/en/latest/
- https://scikit-learn.org/stable/common_pitfalls.html (not fetched by the track: egress blocked)
- https://github.com/eslazarev/purged-cross-validation
- https://arxiv.org/pdf/2507.04176
- https://www.luxalgo.com/library/concept/label-definition-and-prediction-horizon.md
- https://fynance.readthedocs.io/en/latest/features.labels.html
- https://www.davidhbailey.com/dhbpapers/overfit-tools-at.pdf
- https://scikit-learn.org/stable/modules/calibration.html (not fetched by the track: egress blocked)
- https://pypi.org/project/scikit-learn/
- https://pypi.org/project/skops/
- https://scikit-learn.org/stable/model_persistence.html (not fetched by the track: egress blocked)
- https://arxiv.org/html/2412.12458v1
- https://www.interactivebrokers.com/campus/ibkr-quant-news/kalman-filter-python-tutorial-and-strategies-part-i/
- https://www.statsmodels.org/stable/generated/statsmodels.tsa.vector_ar.vecm.coint_johansen.html
- https://www.statsmodels.org/stable/generated/statsmodels.tsa.stattools.coint.html
- https://www.quantstart.com/articles/Dynamic-Hedge-Ratio-Between-ETF-Pairs-Using-the-Kalman-Filter
- https://blog.quantinsti.com/kalman-filter/
- https://www.luxalgo.com/library/concept/cointegration.md
- https://scholar.nycu.edu.tw/en/publications/online-structural-break-detection-for-pairs-trading-using-wavelet/
- https://www.luxalgo.com/library/concept/pairs-trading-stack/
- https://fastapi.tiangolo.com/tutorial/handling-errors/
- https://docs.alpaca.markets/docs/about-market-data-api
- https://alpaca.markets/docs/market-data
- https://databento.com/docs/quickstart
- https://github.com/ranaroussi/yfinance
- https://github.com/ranaroussi/yfinance/discussions/2431
- https://pypi.org/pypi/yfinance/json
- https://pypi.org/pypi/exchange-calendars/json
- https://github.com/gerrymanoim/exchange_calendars
- https://pypi.org/pypi/duckdb/json
- https://pypi.org/pypi/tenacity/json
- https://qveris.ai/guides/alpha-vantage-pricing-alternative/
- https://apicostcalc.com/blog/polygon-massive-rebrand-api-pricing.html
- https://www.quantstart.com/articles/evaluating-data-coverage-with-tiingo/
- https://pypi.org/pypi/pandera/json
- https://pandera.readthedocs.io
- https://docs.alpaca.markets/docs/working-with-orders (seen via search snippets only)
- https://docs.alpaca.markets/docs/crypto-orders (seen via search snippets only)
- https://docs.alpaca.markets/docs/margin-and-short-selling (seen via search snippets only)
- https://docs.alpaca.markets/docs/paper-trading (not fetched)
- https://alpaca.markets/sdks/python/trading.html (not fetched)
- https://core.telegram.org/bots/api#formatting-options (escape set confirmed via search snippets only)
- https://github.com/utterstep/telegram-escape
- https://docs.pydantic.dev/latest/api/pydantic_extra_types/ (not fetched)
- https://docs.python.org/3/library/logging.html#filter-objects
- https://openapi-ts.dev/openapi-fetch/
- https://openapi-ts.dev/about
- https://www.npmjs.com/package/openapi-typescript
- https://tanstack.com/query/v5/docs/framework/react/guides/polling
- https://tanstack.com/query/latest/docs/framework/react/guides/query-retries
- https://zod.dev
- https://www.npmjs.com/package/zod
- https://react.dev/reference/react/Component#catching-rendering-errors-with-an-error-boundary
- https://www.npmjs.com/package/react-error-boundary
- https://vite.dev/guide/env-and-mode
- https://community.plotly.com/t/how-can-i-reduce-bundle-size-of-plotly-js-in-react-app/89910
- https://github.com/plotly/react-plotly.js
- https://vitest.dev
- https://mswjs.io
- https://playwright.dev/docs/ci-intro
- https://docs.github.com/en/actions
- https://typescript-eslint.io/rules/no-explicit-any/
- https://platform.claude.com/docs/en/api/errors.md
- https://pypi.org/pypi/anthropic/json
- https://platform.claude.com/docs/en/about-claude/model-deprecations.md
- https://platform.claude.com/docs/en/about-claude/models/overview.md
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs.md
- https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md
- https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence.md
- https://genai.owasp.org/llm-top-10/ (cited from memory by the track)
- https://www.anthropic.com/engineering/building-effective-agents (cited from memory by the track)
- https://www.anthropic.com/engineering/multi-agent-research-system
- Non-URL references from the tracks: SEC Rule 15c3-5 (pre-trade controls analogue); Thorp, 'The Kelly Criterion in Blackjack, Sports Betting and the Stock Market'; Carver, 'Systematic Trading'; SQLite WAL documentation
- Version facts the tracks took from PyPI JSON (as of 2026-10-04): vectorbt 1.1.1, backtesting 0.6.6, nautilus_trader 1.231.0, zipline-reloaded 3.1.1, statsmodels 0.15.0, pykalman 0.11.2, filterpy 1.4.5 (last release 2018), alpaca-py 0.44.0, pydantic-settings 2.15.0, tenacity 9.1.4, exchange_calendars 4.13.2, duckdb 1.5.6, yfinance 1.7.0, anthropic 1.11.0, skops 0.16.0, MLflow 3.16.1, xgboost 3.4.1, lightgbm 4.7.0, ta 0.11.0. npm, as of 2026-10-04: openapi-typescript 7.13.0, openapi-fetch 0.17.0, @tanstack/react-query 5.104.1, vitest 5.0.3, msw 3.0.2, @playwright/test 1.63.0, plotly.js 4.1.1, react-plotly.js 4.1.0
- My code spot-checks, which settle the points where tracks disagreed, under /home/user/ML-Trading-Platform-Backend:
- backtester.py:247: bars_held = i - trades[-1].bars_held (confirmed).
- backtester.py:296-297: gross_loss = 1e-10 when there are no losses (confirmed).
- backtester.py:310-311: excess = returns - RISK_FREE_RATE/252 (confirmed).
- backtester.py:330-345: vectorbt overlay overwrites Sortino and Calmar (confirmed).
- backtester.py:348-384: walk_forward discards the training slice (confirmed).
- main.py:55-63: check_recent_signal favours BUY (confirmed).
- main.py:122-124: confidence = win_rate*profit_factor (confirmed).
- main.py:228 with ml_patterns.py:208-214: ML confidence is P(up) even for SELL (confirmed).
- paper_trader.py:177-186: qty = max(1, int(max_value*max(0.5, confidence)/price)) (confirmed).
- routes/pattern_routes.py:124: total_return_pct is round(fraction, 2). The audit cited line 91. This also loses precision, so the Dashboard shows 0.0% for small returns.
- server.py:25-50: NumpySafeResponse never sees np.float64 (it subclasses float) and does not set allow_nan=False, so it emits NaN/Infinity tokens. FastAPI's jsonable_encoder runs first and raises on np.bool_. routes/helpers.py:24-26 round-trips NaN and then 500s in Starlette's allow_nan=False render.
- All route handlers are sync `def` (0 async), so the risk is long requests and threadpool exhaustion, not a blocked event loop.
- requirements.txt: every pin is >=; pandas-ta is not imported anywhere.
- Frontend: Dashboard.tsx:103 and PairsTrading.tsx:155-156 read total_return_pct.
- Bundled claude-api skill reference (cached 2026-09-25): current model IDs claude-opus-5-5 ($4/$20 per MTok, effort default medium, thinking cannot be disabled) and claude-sonnet-5-5 ($2/$10). claude-sonnet-4-20250514 is listed as Deprecated with retirement TBD, while the LLM track says it was retired on 2026-06-15; either way, replace it. Structured outputs: Python messages.parse(output_format=PydanticModel) or output_config.format. Temperature, top_p, top_k, budget_tokens and prefill are rejected on current models. The server-side refusal fallback is recommended by default for opus-5-5.
## Appendix B: backend engineering research (re-run)

_A Sonnet researcher read the code but did not run it. The FastAPI and pydantic doc sites were blocked by the proxy, so those claims rest on PyPI metadata and search snippets._

### Where it disagrees with the plan
- **Long jobs:** serve a nightly result file written atomically by the CLI, read-only through the API, for the scan and pairs scan. Interactive endpoints stay synchronous `def`, behind a concurrency cap that returns 429 when busy, and `/ml/predict` gets a TTL cache. No SQLite job table.
- **Keep routes as `def`, not `async def`.** The work is CPU-bound. The fix is limiting concurrency.
- **Don't use `FiniteFloat` on outputs.** It turns NaN into a 500. Use `float | None` and one sanitiser that maps NaN/inf to null.
- **Response models only on the 4–5 endpoints the frontend uses,** not on every endpoint.
- **pydantic-settings for API settings only** (key, CORS, bind address). Don't port all of config.py.
- **Drop the `responses` library.** Monkeypatch `fetch_ohlcv` and block sockets with pytest-socket instead. Use hypothesis only for the sanitiser.
- **Missing from the plan:** API-key auth, input bounds, the silent `except` at pattern_routes.py:128, and `.gitignore` gaps.
- **Drop pandas-ta.** It is never imported, and its latest release needs Python >=3.12. Plan a separate pandas-3 migration later.

### Recommendations
| Priority | Effort | Change | Acceptance |
|---|---|---|---|
| P0 | S | One `to_jsonable()` sanitiser (numpy, NaN/inf to null, np.bool_), replacing NumpyEncoder x2 and `_sanitize`. Real HTTP errors (404/422/502) with an `{"error":{code,message}}` envelope and exception handlers | A stub returning nan, inf and np.True_ gives 200 with strict-valid JSON. An unknown pattern gives 404. A bad period_days gives 422 |
| P0 | S | `settings.py` with API_KEY, CORS_ORIGINS and ALLOW_CREDENTIALS. APIKeyHeader dependency using `compare_digest`, `/api/health` left open, bind to 127.0.0.1 | No key gives 401. A disallowed Origin gets no CORS headers |
| P0 | S | Input bounds: `Field(ge,le)` on period_days, n_splits and recency_days. Symbol checked against a regex. pattern_name restricted to an enum from PATTERN_REGISTRY | period_days=10**9 gives 422 |
| P0 | S | `pyproject.toml` with Python >=3.11,<3.13, pandas~=2.3 and fastapi>=0.115, plus a committed `uv.lock`. Drop pandas-ta. vectorbt, backtesting and streamlit become optional extras | A clean `uv sync --locked` and pytest pass, and pandas is 2.3 |
| P1 | M | Heavy work: nightly result file, concurrency cap returning 429, ML TTL cache | Two concurrent `/ml/predict` calls give one 200 and one 429. `/scan/latest` responds in under 50 ms |
| P1 | M | `services/` with `scan_patterns`, `run_backtest` and `serialize_equity`, used by main.py, the routes and dashboard.py. One set of validity and recency rules | A golden test shows the CLI scan and the API scan give the same rows |
| P1 | M | response_model on config, patterns/detect, backtest/run and pairs/analyze | OpenAPI shows response schemas, and a contract test passes |
| P1 | S | Log the scan failures that are currently swallowed, and return them in a `failed[]` list | A pattern that raises appears in `failed` |
| P1 | M | Offline pytest using a synthetic OHLCV fixture, TestClient and pytest-socket | Runs in under 30 s and fails on any socket |
| P2 | S | GitHub Actions running setup-uv, ruff and pytest on a 3.11/3.12 matrix | A failing test turns the PR red |
| P2 | S | `/api/health` reports the version and the result-file timestamp. `.gitignore` covers .venv, sqlite files and reports | — |

### Sources
- https://pypi.org/pypi/fastapi/json (0.142.2)
- https://pypi.org/pypi/pandas-ta/json (0.4.71b0, Python >=3.12)
- https://pandas.pydata.org/docs/whatsnew/v3.0.0.html
- https://starlette.dev/threadpool/
- https://pydantic.dev/docs/validation/2.12/api/pydantic-core/pydantic_core_schema/
