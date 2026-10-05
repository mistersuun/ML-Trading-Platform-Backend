# ML Trading Platform - Backend

Research and **paper-trading** backend: pattern, pairs and ML scans, backtests, stress tests, alerts, an
advisory long-term allocation tool, and a guarded paper-order path. There is **no live trading mode**
(decision D2). Everything is long-only, with no margin, and crypto, forex and futures are never executed.
Nothing is order-eligible until a signal has a `validation_status` in `config.ORDER_ELIGIBLE_STATUSES`;
nothing reaches that in Phase 1, so the system is effectively **alert-only**.

## Setup (uv)

    uv sync --locked
    cp .env.example .env        # fill in keys; leave TRADING_MODE=off to start
    uv run pytest -q            # offline, no network
    uv run ruff check .

## Commands

    uv run python main.py scan [--mode technical pairs ml] [--symbol AAPL] [--market stocks] [--report] [--paper]
    uv run python main.py scan --stress AAPL ema_crossover
    uv run python main.py scan --schedule --time 08:00
    uv run python main.py risk init | status | halt --reason TEXT | resume --confirm | reconcile [--accept]
    uv run python main.py check-llm
    uv run python main.py rebalance --holdings holdings.csv [--contributions 500]
    uv run python main.py backup --dest backups/trading.db
    uv run uvicorn server:app --port 8000        # API

The old flat flags (`python main.py --mode pairs`) still work and mean `scan`.
`rebalance` prints an advisory proposal for the 90% core / 10% trend allocation (see `docs/allocation.md`);
you place any trades yourself, the platform never trades it.

## Paper-trading checklist

Do these in order before ever setting `TRADING_MODE=paper`:

1. Use a dedicated Alpaca **paper** account and put its keys in `.env`.
2. `python main.py risk init` creates and migrates the state DB (`STATE_DB_PATH`). Trading refuses without it.
3. Set `SIGNAL_SLEEVE_EQUITY` (the sleeve's dollars; broker equity is never used) and review the risk limits in
   `config.py` (0.5% risk per trade, 8 positions, 5 orders a day, daily/weekly stops, -10% halt).
4. `python main.py risk reconcile` should say OK on a clean account. If it reports a mismatch, inspect the
   paper account, then `risk reconcile --accept` to record that you know about that state. A mismatch blocks
   entries until accepted.
5. Know the kill switch: `touch state/KILL` (or `KILL_SWITCH=1`) blocks every order; `risk halt --reason ...`
   persists a halt; `risk resume --confirm` clears it and resets the drawdown peak. A -10% sleeve drawdown halts
   automatically and needs the same manual resume.
6. `python main.py check-llm` if you want the AI briefing (optional; it is advisory and never places orders).
7. Run scans with `TRADING_MODE=off` first. Then, only when you want to exercise the plumbing, set
   `TRADING_MODE=paper` and `PAPER_TRADE_ENABLED=true`. Until Phase 2 validates a signal, every order decision
   will be `rejected: not_validated`, and each decision is sent as an `order_decision` alert.
8. **Owner action, recorded by you:** one manual verification against a real Alpaca *paper* account (reconcile,
   one eligible test order via a temporary override of the eligibility list, bracket legs visible, kill switch
   blocks the next order). Also confirm on the paper account that the stop-loss and take-profit legs of a bracket
   entered with `time_in_force=gtc` are still open the next day (the code relies on legs inheriting the parent's
   GTC; `reconcile` reports `missing_protective_stop` if a long loses its stop), and that closing a position via
   a SELL intent cancels the legs first. Record the date and outcome in `docs/decisions.md`. Live trading is out of scope (D2).

## Where things live

- `docs/decisions.md` - binding decisions D1-D10.
- `docs/research/robustness-roadmap.md` - the phased plan; `docs/research/portfolio-recommendations.md` - risk defaults.
- `docs/allocation.md` - the core/trend allocation and rebalance tool.
- `tests/bugs/README.md` - the index of known bugs and their pinning tests.
- `execution.py` is the only path to a broker; `risk_manager.py` + `state/` hold the persisted risk state;
  `instruments.py` is the symbol registry (which instruments are executable).
