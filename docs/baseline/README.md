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

## Phase 2 before / after (synthetic baseline)

Regenerated after WS2.2 (engine, next-open fills, mark-to-market equity, rf = 0 Sharpe on actual-size returns).
3 symbols x 20 patterns = 60 rows, same columns, `is_valid` stays 0 -> 0. Means over the 60 rows:

| Metric | Before (Phase 1 backtester) | After (engine.py) |
|---|---|---|
| total_trades | 24.53 | 24.37 (23 rows changed) |
| win_rate | 0.264 | 0.274 |
| profit_factor | 0.809 | 0.808 |
| sharpe | -13.04 | -0.70 |
| max_drawdown | -0.0106 | -0.0108 |
| total_return | -0.0073 | -0.0069 |

The old Sharpe was biased: it subtracted rf/252 from sparse per-bar (per-trade) returns and annualised by trade
count; the new one uses daily mark-to-market returns of the actual position size with rf = 0. Everything else moves
little because the synthetic frames have no edge. On random walks the engine's Sharpe is about 0 (see the slow test
in `tests/test_engine_golden.py`). The validation layer (`validation.py`) adds out-of-sample, hold-out and
Benjamini-Hochberg-corrected results on top of these; on 100 random-walk seeds x 8 candidates it validated 1 of 800
candidates and 1 of 100 runs (`tests/test_validation.py`, slow).

The real-data baseline still needs the owner's local snapshot (step 1).

## Real-data attribution (OPEN, owner action, merge-blocking)

Phase 2 changes the data source / adjustment AND the engine at the same time, so a real-data shift cannot be
attributed from one diff. D8 therefore needs THREE runs on frozen snapshots (the sandbox cannot reach the market-data
hosts, so only the owner can produce them). The last pre-Phase-2 commit is `bd3e990`.

```
git worktree add ../mltp-old bd3e990                      # old engine + old data layer
(cd ../mltp-old && uv run python scripts/snapshot_bars.py) # -> data/snapshots/<date>/ (OLD data); archive it
uv run python scripts/snapshot_bars.py                     # NEW data layer -> data/snapshots/<date2>/; archive + hash it

# run 1: old engine + old data
(cd ../mltp-old && uv run python scripts/metrics_report.py <old-snapshot-dir> --out /tmp/r1.csv)
# run 2: old engine + new data      (isolates the data change)
(cd ../mltp-old && uv run python scripts/metrics_report.py <new-snapshot-dir> --out /tmp/r2.csv)
# run 3: new engine + new data      (isolates the engine change)
uv run python scripts/metrics_report.py <new-snapshot-dir> --out docs/baseline/<date2>_metrics.csv

uv run python scripts/metrics_report.py --diff /tmp/r1.csv /tmp/r2.csv   # data effect
uv run python scripts/metrics_report.py --diff /tmp/r2.csv docs/baseline/<date2>_metrics.csv   # engine effect
```

Paste both diffs and the snapshot manifest hashes below before merging. Status: **not done** (no real-data
snapshot exists in the repository). Until then the only attribution evidence is the synthetic diff above.
