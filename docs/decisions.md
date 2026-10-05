# Decisions (ADR log)

Each decision lists when to revisit it. The sources are `docs/research/robustness-roadmap.md` and `docs/research/portfolio-recommendations.md`.

## D1 — The platform is a research and allocation cockpit, not an autonomous trader (2026-10-04)
- **Decision:** The platform is a research screen with alerts. Allocation changes are produced as rebalance *proposals*, and the owner places them manually in IBKR. Unattended orders are allowed only in Alpaca **paper**, and only after the Phase 1 chokepoint and the Phase 2 multiple-testing gate exist.
- **Revisit:** after 18–24 months of paper results that pass the graduation gates.

## D2 — Paper only, no live mode
- **Decision:** `paper=True` stays hard-coded in `paper_trader.py` and is enforced by a test. Roadmap WS1.6 "live mode" is dropped.
- **Revisit:** only as a separate, explicit owner decision after D1's gates pass.

## D3 — What can be traded
- **Decision:** In paper, only US-listed ETFs and US large-cap stocks can be traded, long-only. There are no shorts, no margin and no leveraged or inverse ETFs. A SELL signal means "exit the long", and its quantity is capped at the quantity held. Crypto, forex and futures are research-only. Pairs trading is a diagnostic only. Drop GC/SI and CL/NG from the default pairs scan.
- **Revisit:** if a signal family passes the D6 gates and needs a different instrument.

## D4 — Portfolio design
- **Decision:** The core is about 90% of capital: 32% US, 18% developed ex-US, 5% EM, 13% intermediate Treasuries, 7% long Treasuries, 7% TIPS, 8% T-bills, 5% gold and 0–5% commodities. It is rebalanced by bands, with no stops. A trend sleeve of about 10% uses a Faber 8/10/12-month moving average and T-bills when off. The platform's own signals get 0% of real capital and at most 5% later, and only if they pass the gates.
- **Caveat:** none of this has been validated on a long history yet. A long-history proxy backtest (1990+, ideally 1972+) is required before the core's drawdown expectations are relied on.
- **Revisit:** after the long-history backtest, and once a year after that.

## D5 — Risk defaults for the signal sleeve (to be implemented in Phase 1; Phase 0 only adds interlocks)
- 0.5% risk per trade, with the stop at 2×ATR(20) from the fill.
- At most 10% of the sleeve in any one symbol, and at most 4% total risk to stops ("heat").
- Gross exposure at most 100%, with no leverage.
- At most 8 positions, and at most 2 per cluster.
- At most 5 new orders a day.
- Daily and weekly entry stops at -1.5% and -3%.
- A drawdown ladder at -5%, -8% and -10%, with a persisted, fail-closed halt.
- Each order is capped at min(10% of the sleeve, $1000) during plumbing tests.
- Sleeve equity is a configured dollar amount, never taken from the broker's account equity.

## D6 — Signal graduation gates
- **Decision:** Statistical evidence must come from a **pre-registered backtest** on a fixed calendar hold-out. It must pass a BH/Holm correction across every hypothesis tested and have a Deflated Sharpe p below 0.05. Paper trading is an implementation check only: divergence from the backtest within tolerance, and zero breaches. Scaling up requires PSR(SR>0) ≥ 0.95 on paper returns.

## D7 — Toolchain (Phase 0, WS0.1)
- **Decision:** Python 3.11 (the range 3.11–3.12 is allowed), with uv, `pyproject.toml` and a committed `uv.lock`. `requirements.txt` is generated with `uv export`. pandas is pinned to `~=2.3`, because 3.x changes how the patterns compute.
- **Removed from the dependencies:** pandas-ta, matplotlib, seaborn and rich, which nothing imports; and vectorbt and backtesting, which were only used by optional, guarded imports in `backtester.py` whose results are never used. xgboost is **kept**, because removing it would change ML results before a baseline exists.
- **Dev tools:** pytest, pytest-socket, hypothesis, httpx and ruff.
- **Revisit:** move to pandas 3 as a separate migration, run with `-W error::FutureWarning`.

## D8 — Tests before fixes
- **Decision:** Every known bug is pinned by a test marked `xfail(strict=True, raises=AssertionError)` and written against today's public interface. A fix must flip its test to passing and remove the marker in the same commit.
- **Baseline:** a frozen data snapshot and a metrics report act as the baseline. Every Phase 2 change attaches a before/after diff of that report.


## D9 — SIG-1 fixed early (WS0.4)
The check_recent_signal fix (latest non-zero signal wins) landed in Phase 0 rather than WS1.5 because order direction depends on it; it also changes alert direction. ML order confidence is taken from the bar of that signal. Risk-free-rate and uninvested-cash yield convention for Sharpe is still open (decide in WS2.2 before BT-5 is flipped).


## D10 — Phase 1 as built (execution, risk state, alerts, LLM, allocation)
- **No live mode.** `TRADING_MODE` is `off` or `paper`; the Alpaca client is always `paper=True` (tested). Long-only, whole shares, equities/ETFs only (`instruments.py` is the registry). Orders need paper mode, `PAPER_TRADE_ENABLED`, an initialised state DB, and a `validation_status` in `config.ORDER_ELIGIBLE_STATUSES`. Nothing reaches that in Phase 1, so scans are alert-only and paper decisions are `rejected: not_validated`.
- **One chokepoint.** `execution.submit_intent` runs risk gate, mode, intent/registry/validation checks, broker reconciliation, a unique-key signal ledger (one intent per symbol/bar), ATR sizing with the D5 caps, bracket BUYs, and deterministic `client_order_id`s. Timeouts are resolved by lookup, never by resubmitting. `paper_trader.py` is a shim with no raw order path.
- **Persisted risk state** in SQLite (`state/`, WAL, migrations 001-002): halt, peak, day/week baselines, orders per day, account baseline. Fail-closed: `update_equity()` rejects NaN/inf/<=0, and `check()` blocks (`equity_not_updated`) until a reading exists. Sleeve equity = `SIGNAL_SLEEVE_EQUITY + (paper account equity - baseline)`, where the baseline (account equity minus open P&L) is captured at the first trading session, so realized losses persist after a stop fills. This is consistent with D5 only because the paper account is dedicated to the sleeve (deposits, withdrawals or manual trades there distort it; reset by `risk init` on a fresh DB). The latest equity reading is held in memory, so every run refreshes it (`execution.refresh_sleeve_equity`) before `check()`. A depleted sleeve (<= 0) halts.
- **CLI.** `main.py` has subcommands (`scan`, `risk init|status|halt|resume|reconcile`, `check-llm`, `rebalance`, `backup`); the old flat flags alias `scan`. Each run reconciles first (paper mode), records a `runs` row, and sends alerts (`signal`, `order_decision`, `halt`, `reconcile_mismatch`, `briefing_failed`) after the Decision, with the mode, deduplicated via `alerts_sent` (order-decision keys are built from the intent, `signal_key|status`, because decisions made before the ledger key exists carry no `client_order_id`). Pairs are never executed (they need a short leg). GC/SI and CL/NG are dropped from the default pairs scan.
- **Alerts and LLM hardened** (WS1.7, WS1.8): Telegram HTML escaping and limits, retries, log redaction; the briefing uses the Anthropic SDK with structured output, never imports the order path, and a failure sends one `briefing_failed` alert and the run continues.
- **Allocation** (D1/D4) is advisory: `main.py rebalance` prints a proposal from latest prices and 13 monthly closes; the owner places trades.
- **Deviations from the roadmap, recorded.** (1) WS1.4 says a daily loss halts and a new UTC day does not clear it; D5 is binding, so the daily (-1.5%) and weekly (-3%) limits are entry stops that roll over with the UTC day / ISO week, not persisted halts. Only the -10% drawdown halt persists. That acceptance line is superseded. (2) `CANCEL_ON_HALT` is implemented: on a halt or kill switch at session open or during dispatch, unfilled parent BUY entries placed by this platform are cancelled and an alert is sent; protective stop/take-profit legs are never cancelled. (3) Bracket entries are `gtc` (legs inherit the parent's TIF, so `day` would drop the stop on day 2) and `reconcile` raises `missing_protective_stop` for any platform-bracketed long with no live stop or in-flight exit; a SELL exit first cancels that position's legs, then sells. This rests on Alpaca's bracket rules, which could not be re-verified against the official docs from the build environment: the README checklist makes it an owner check. (4) Definitive broker refusals (4xx other than 408/429, order not found) mark the signal `rejected` and do not block the sleeve; timeouts, 5xx and transport errors stay `unknown` and block until `risk reconcile --accept`. (5) Pending entries count toward the gross and heat caps. (6) Relative `STATE_DB_PATH` / `KILL_SWITCH_FILE` resolve against the repo, not the cwd.
- **Before anything becomes order-eligible (Phase 2):** realized P&L comes from the account-equity baseline above, not from a fills ledger; reconcile it against Alpaca account activities before trusting the daily/weekly stops on real signals.
- **Still open:** the owner's one manual verification on a real Alpaca paper account (see README checklist); take-profit is a fixed 3 x ATR constant in `execution.py`; entry/stop prices use the last close, to be reconciled with the Phase 2 backtest engine.
