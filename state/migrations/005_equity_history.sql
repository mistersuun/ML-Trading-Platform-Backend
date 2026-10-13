-- Every accepted sleeve-equity reading (RiskManager.update_equity), for the Risk page's equity chart.
CREATE TABLE IF NOT EXISTS equity_history (
    id            INTEGER PRIMARY KEY,
    ts            TEXT NOT NULL,
    sleeve_equity REAL NOT NULL,
    peak          REAL
);
CREATE INDEX IF NOT EXISTS idx_equity_history_ts ON equity_history (ts);
