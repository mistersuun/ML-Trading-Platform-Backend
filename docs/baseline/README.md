# Baseline workflow

Purpose: every Phase 2 change must show exactly which backtest numbers it moved.

1. **Snapshot (owner, locally).** Market-data hosts are blocked in CI/sandbox, so the
   owner must take the real-data snapshot on their own machine:
   `uv run python scripts/snapshot_bars.py` writes `data/snapshots/<date>/<symbol>.csv`
   for all of `config.WATCHLIST` plus `manifest.json` (sha256, row count, source per file).
   Commit or archive the snapshot so later runs use identical bars.
2. **Metrics report.** `uv run python scripts/metrics_report.py data/snapshots/<date> --out docs/baseline/<date>_metrics.csv`
   runs every `PATTERN_REGISTRY` pattern through `backtester.classic_backtest` and writes one
   row per (symbol, pattern): total_trades, win_rate, profit_factor, sharpe, max_drawdown,
   total_return, is_valid.
3. **Diff on every Phase 2 change.** Re-run the report after the change and attach the output of
   `uv run python scripts/metrics_report.py --diff old.csv new.csv` to the change.

## Synthetic baseline (available now)

`synthetic_metrics.csv` is the pre-fix baseline on seeded synthetic GBM frames
(`--synthetic`, 3 fake symbols, 500 bars). It is reproduced byte-for-byte by
`tests/test_metrics_report.py`; when a Phase 2 fix intentionally changes results, regenerate with
`uv run python scripts/metrics_report.py --synthetic --out docs/baseline/synthetic_metrics.csv`
and include the `--diff` against the previous version in the same change.

A real-data baseline does not exist yet: it needs the owner's local snapshot (step 1).
