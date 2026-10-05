-- Trial registry (trials/registry.py, WS4.1): one row per evaluated (symbol x pattern x params) trial of a run.
-- The OOS daily-return series live in data/trials/<run_id>.parquet (date x trial_id); N per run = row count.
CREATE TABLE IF NOT EXISTS trials (
    id               INTEGER PRIMARY KEY,
    run_id           TEXT NOT NULL,
    trial_id         TEXT NOT NULL,
    ordinal          INTEGER NOT NULL,
    symbol           TEXT NOT NULL,
    pattern          TEXT NOT NULL,
    params_hash      TEXT NOT NULL,
    params_json      TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    data_hash        TEXT NOT NULL,
    n_obs            INTEGER NOT NULL,
    recorded_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, trial_id)
);
CREATE INDEX IF NOT EXISTS idx_trials_run ON trials (run_id, ordinal);
