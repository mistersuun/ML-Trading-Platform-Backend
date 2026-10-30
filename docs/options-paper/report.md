# Options paper test (as of 2026-10-30)

Paper only, rules in `PREREG.md` (frozen 2026-10-15). Inception 2026-10-14. Fills at ask/bid plus US$0.65 per contract; positions marked at mid. Not advice.

| Book | Start | Equity | Return | Benchmark | Bench value | Open | Closed |
|---|---:|---:|---:|---|---:|---:|---:|
| Book L: $500 lottery (buys calls/puts on QQQ/SPY) | $500 | $365.55 | -26.89% | QQQ buy-and-hold | $473.62 | 1 | 1 |
| Book P: SPY cash-secured put writer | $100,000 | $99,903.85 | -0.10% | SPY buy-and-hold | $98,196.47 | 1 | 0 |
| Book M: $1,000 momentum calls (aggressive group, from 2026-10-16) | $1,000 | $977.20 | -2.28% | QQQ buy-and-hold | $972.80 | 2 | 0 |

## Book L: $500 lottery (buys calls/puts on QQQ/SPY)

Open:

| Contract | Qty | Side | Entry | Entry date | Mark |
|---|---:|---|---:|---|---:|
| SPY 2026-11-20 665P | 1 | long | 1.36 | 2026-10-23 | 0.95 |

Closed trades: 1, win rate 0%, total P&L -93.30, average loss -93.30

| Contract | Qty | Entry | Exit | Dates | Reason | P&L |
|---|---:|---:|---:|---|---|---:|
| QQQ 2026-11-20 570P | 1 | 1.35 | 0.43 | 2026-10-22 to 2026-10-28 | stop (-60%) | -93.30 |

## Book P: SPY cash-secured put writer

Open:

| Contract | Qty | Side | Entry | Entry date | Mark |
|---|---:|---|---:|---|---:|
| SPY 2026-11-20 740P | 1 | short | 5.18 | 2026-10-14 | 6.13 |

## Book M: $1,000 momentum calls (aggressive group, from 2026-10-16)

Open:

| Contract | Qty | Side | Entry | Entry date | Mark |
|---|---:|---|---:|---|---:|
| MRK 2027-01-15 160C | 1 | long | 4.50 | 2026-10-16 | 4.78 |
| CSCO 2027-01-15 120C | 1 | long | 3.35 | 2026-10-19 | 2.86 |

## Session log

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
- **2026-10-15** L $500.00, P $100,078.85. no action
- **2026-10-14** L $500.00, P $99,996.85. P: sell SPY 2026-11-20 740P x1 at 5.18 (delta -0.2124)

Caveats: closing quotes are used for both decision and fill; Book P's collateral earns nothing here (a real account would earn about the T-bill rate); no early assignment or dividends are modelled. Fewer than 30 closed trades per book is an illustration, not evidence.
