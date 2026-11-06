# Options paper test (as of 2026-11-06)

Paper only, rules in `PREREG.md` (frozen 2026-10-15). Inception 2026-10-14. Fills at ask/bid plus US$0.65 per contract; positions marked at mid. Not advice.

| Book | Start | Equity | Return | Benchmark | Bench value | Open | Closed |
|---|---:|---:|---:|---|---:|---:|---:|
| Book L: $500 lottery (buys calls/puts on QQQ/SPY) | $500 | $301.40 | -39.72% | QQQ buy-and-hold | $450.03 | 0 | 2 |
| Book P: SPY cash-secured put writer | $100,000 | $100,286.55 | +0.29% | SPY buy-and-hold | $95,541.84 | 1 | 1 |
| Book M: $1,000 momentum calls (aggressive group, from 2026-10-16) | $1,000 | $605.55 | -39.45% | QQQ buy-and-hold | $924.34 | 1 | 1 |

## Book L: $500 lottery (buys calls/puts on QQQ/SPY)

No open position.

Closed trades: 2, win rate 0%, total P&L -198.60, average loss -99.30

| Contract | Qty | Entry | Exit | Dates | Reason | P&L |
|---|---:|---:|---:|---|---|---:|
| QQQ 2026-11-20 570P | 1 | 1.35 | 0.43 | 2026-10-22 to 2026-10-28 | stop (-60%) | -93.30 |
| SPY 2026-11-20 665P | 1 | 1.36 | 0.32 | 2026-10-23 to 2026-11-04 | stop (-60%) | -105.30 |

## Book P: SPY cash-secured put writer

Open:

| Contract | Qty | Side | Entry | Entry date | Mark |
|---|---:|---|---:|---|---:|
| SPY 2026-12-11 690P | 1 | short | 6.91 | 2026-11-06 | 6.96 |

Closed trades: 1, win rate 100%, total P&L +292.70, average win +292.70

| Contract | Qty | Entry | Exit | Dates | Reason | P&L |
|---|---:|---:|---:|---|---|---:|
| SPY 2026-11-20 740P | 1 | 5.18 | 2.24 | 2026-10-14 to 2026-11-04 | take profit (50% of credit) | +292.70 |

## Book M: $1,000 momentum calls (aggressive group, from 2026-10-16)

Open:

| Contract | Qty | Side | Entry | Entry date | Mark |
|---|---:|---|---:|---|---:|
| MRK 2027-01-15 160C | 1 | long | 4.50 | 2026-10-16 | 3.27 |

Closed trades: 1, win rate 0%, total P&L -271.30, average loss -271.30

| Contract | Qty | Entry | Exit | Dates | Reason | P&L |
|---|---:|---:|---:|---|---|---:|
| CSCO 2027-01-15 120C | 1 | 3.35 | 0.65 | 2026-10-19 to 2026-11-06 | stop (-60%) | -271.30 |

## Session log

- **2026-11-06** L $301.40, P $100,286.55, M $605.55. P: sell SPY 2026-12-11 690P x1 at 6.91 (delta -0.1957); M: close CSCO 2027-01-15 120C x1 at 0.65 (stop (-60%)), P&L -271.30; M: no affordable call on AMD, MRK, CSCO, XOM, CVX within US$278.05
- **2026-11-04** L $301.40, P $100,292.70, M $867.20. L: close SPY 2026-11-20 665P x1 at 0.32 (stop (-60%)), P&L -105.30; P: close SPY 2026-11-20 740P x1 at 2.24 (take profit (50% of credit)), P&L +292.70; P: no eligible put in the quotes
- **2026-11-03** L $361.55, P $99,856.35, M $725.70. no action
- **2026-11-02** L $376.05, P $99,787.85, M $856.70. L: QQQ put signal; no contract fits the US$112.81 budget
- **2026-10-30** L $365.55, P $99,903.85, M $977.20. L: QQQ put signal; no contract fits the US$109.67 budget
- **2026-10-29** L $334.05, P $100,139.35, M $943.70. no action
- **2026-10-28** L $329.55, P $100,205.85, M $914.70. L: close QQQ 2026-11-20 570P x1 at 0.43 (stop (-60%)), P&L -93.30
- **2026-10-27** L $360.20, P $100,153.35, M $962.70. no action
- **2026-10-26** L $404.70, P $100,041.35, M $903.70. no action
- **2026-10-23** L $486.70, P $99,871.85, M $936.20. L: buy SPY 2026-11-20 665P x1 at 1.36 (delta -0.0481)
- **2026-10-22** L $496.35, P $99,940.35, M $985.70. L: buy QQQ 2026-11-20 570P x1 at 1.35 (delta None)
- **2026-10-21** L $500.00, P $100,162.35, M $1,118.70. no action
- **2026-10-20** L $500.00, P $100,091.85, M $1,043.70. no action
- **2026-10-19** L $500.00, P $99,989.35, M $881.20. M: buy CSCO 2027-01-15 120C x1 at 3.35 (delta 0.2976)
- **2026-10-16** L $500.00, P $99,817.85, M $979.35. L: QQQ put signal; no contract fits the US$150.00 budget; M: buy MRK 2027-01-15 160C x1 at 4.50 (delta 0.3188)

Caveats: closing quotes are used for both decision and fill; Book P's collateral earns nothing here (a real account would earn about the T-bill rate); no early assignment or dividends are modelled. Fewer than 30 closed trades per book is an illustration, not evidence.
