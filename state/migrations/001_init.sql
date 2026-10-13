CREATE TABLE risk_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    halted INTEGER NOT NULL DEFAULT 0,
    halt_reason TEXT,
    halted_at TEXT,
    peak_equity REAL,
    day TEXT,
    day_start_equity REAL,
    week TEXT,
    week_start_equity REAL,
    orders_today INTEGER NOT NULL DEFAULT 0
);
INSERT INTO risk_state (id, halted) VALUES (1, 0);

CREATE TABLE signal_ledger (
    id INTEGER PRIMARY KEY,
    signal_key TEXT NOT NULL UNIQUE,
    strategy_key TEXT,
    symbol TEXT,
    side TEXT,
    bar_date TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    decision_json TEXT
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    client_order_id TEXT NOT NULL UNIQUE,
    broker_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL,
    order_class TEXT,
    stop_price REAL,
    limit_price REAL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decision_json TEXT
);

CREATE TABLE alerts_sent (
    id INTEGER PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    sent_at TEXT NOT NULL,
    channel TEXT,
    kind TEXT
);

CREATE TABLE runs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT,
    summary_json TEXT
);
