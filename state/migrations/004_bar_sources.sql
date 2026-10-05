-- Pinned market-data source per (symbol, window class) (WS3.3): a series never silently mixes vendors.
CREATE TABLE IF NOT EXISTS bar_sources (
    symbol       TEXT NOT NULL,
    window_class TEXT NOT NULL,            -- 'default' | 'research'
    source       TEXT NOT NULL,
    pinned_at    TEXT NOT NULL,
    PRIMARY KEY (symbol, window_class)
);
