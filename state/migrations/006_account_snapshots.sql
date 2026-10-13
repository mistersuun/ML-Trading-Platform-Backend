-- Read-only IBKR account snapshots (account/store.py). History is kept: one row per sync, never updated.
-- cash is the base-currency total cash (negative = margin loan); positions_json / fx_json hold the detail.
CREATE TABLE IF NOT EXISTS account_snapshots (
    id               INTEGER PRIMARY KEY,
    as_of            TEXT NOT NULL,
    source           TEXT NOT NULL,
    base_currency    TEXT NOT NULL,
    net_liquidation  REAL NOT NULL,
    cash             REAL NOT NULL,
    gross_positions  REAL NOT NULL,
    leverage         REAL,
    excess_liquidity REAL,
    maint_margin     REAL,
    buying_power     REAL,
    fx_json          TEXT NOT NULL DEFAULT '{}',
    positions_json   TEXT NOT NULL DEFAULT '[]',
    cash_json        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_account_snapshots_as_of ON account_snapshots (as_of);
