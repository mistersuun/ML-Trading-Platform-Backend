-- Realized P&L without a fills table: sleeve equity = SIGNAL_SLEEVE_EQUITY + (paper account equity - baseline).
-- The baseline is account equity minus open P&L, captured at the first trading session (D10).
ALTER TABLE risk_state ADD COLUMN account_baseline REAL;
-- Intent price (last close) of the entry, used to size against pending entries (gross / heat caps).
ALTER TABLE orders ADD COLUMN intent_price REAL;
