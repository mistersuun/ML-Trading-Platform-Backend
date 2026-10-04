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
