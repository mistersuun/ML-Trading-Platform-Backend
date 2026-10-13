-- Equity-jump guard (D10): last sleeve equity reading and the newest orders row at that time.
ALTER TABLE risk_state ADD COLUMN last_sleeve_equity REAL;
ALTER TABLE risk_state ADD COLUMN last_sleeve_order_id INTEGER;
