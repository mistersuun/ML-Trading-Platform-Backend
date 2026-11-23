# Options paper test (as of 2026-11-23)

Paper only, rules in `PREREG.md` (frozen 2026-10-15). Inception 2026-10-14. Fills at ask/bid plus US$0.65 per contract; positions marked at mid. Not advice.

| Book | Start | Equity | Return | Benchmark | Bench value | Open | Closed |
|---|---:|---:|---:|---|---:|---:|---:|
| Book L: $500 lottery (buys calls/puts on QQQ/SPY) | $500 | $301.40 | -39.72% | QQQ buy-and-hold | $446.55 | 0 | 2 |
| Book P: SPY cash-secured put writer | $100,000 | $99,894.25 | -0.11% | SPY buy-and-hold | $93,852.65 | 1 | 2 |
| Book M: $1,000 momentum calls (aggressive group, from 2026-10-16) | $1,000 | $221.15 | -77.89% | QQQ buy-and-hold | $917.20 | 1 | 4 |

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
| SPY 2026-12-18 715P | 1 | short | 5.95 | 2026-11-09 | 14.12 |

Closed trades: 2, win rate 100%, total P&L +711.40, average win +355.70

| Contract | Qty | Entry | Exit | Dates | Reason | P&L |
|---|---:|---:|---:|---|---|---:|
| SPY 2026-11-20 740P | 1 | 5.18 | 2.24 | 2026-10-14 to 2026-11-04 | take profit (50% of credit) | +292.70 |
| SPY 2026-12-11 690P | 1 | 6.91 | 2.71 | 2026-11-06 to 2026-11-09 | take profit (50% of credit) | +418.70 |

## Book M: $1,000 momentum calls (aggressive group, from 2026-10-16)

Open:

| Contract | Qty | Side | Entry | Entry date | Mark |
|---|---:|---|---:|---|---:|
| XOM 2027-02-19 195C | 1 | long | 0.86 | 2026-11-19 | 0.86 |

Closed trades: 4, win rate 0%, total P&L -778.20, average loss -194.55

| Contract | Qty | Entry | Exit | Dates | Reason | P&L |
|---|---:|---:|---:|---|---|---:|
| CSCO 2027-01-15 120C | 1 | 3.35 | 0.65 | 2026-10-19 to 2026-11-06 | stop (-60%) | -271.30 |
| CSCO 2027-01-15 115C | 1 | 1.97 | 0.50 | 2026-11-10 to 2026-11-13 | stop (-60%) | -148.30 |
| MRK 2027-01-15 160C | 1 | 4.50 | 1.79 | 2026-10-16 to 2026-11-16 | stop (-60%) | -272.30 |
| CSCO 2027-02-19 120C | 1 | 1.15 | 0.30 | 2026-11-18 to 2026-11-20 | stop (-60%) | -86.30 |

## Session log

- **2026-11-23** L $301.40, P $99,894.25, M $221.15. M: no usable quote for XOM 2027-02-19 195C; held at last mark 0.86; M: no affordable call on AMD, XOM, CVX, MRK, CSCO within US$110.57
- **2026-11-20** L $301.40, P $97,960.75, M $221.15. L: QQQ put signal; no contract fits the US$90.42 budget; L: SPY put signal; no contract fits the US$90.42 budget; M: close CSCO 2027-02-19 120C x1 at 0.30 (stop (-60%)), P&L -86.30; M: no affordable call on AMD, MRK, XOM, CVX, UNH within US$110.57
- **2026-11-19** L $301.40, P $99,517.75, M $293.30. L: QQQ put signal; no contract fits the US$90.42 budget; L: SPY put signal; no contract fits the US$90.42 budget; M: buy XOM 2027-02-19 195C x1 at 0.86 (delta 0.08712651900004852)
- **2026-11-18** L $301.40, P $100,164.25, M $299.95. L: SPY put signal; no contract fits the US$90.42 budget; M: buy CSCO 2027-02-19 120C x1 at 1.15 (delta 0.14071134743236946)
- **2026-11-17** L $301.40, P $100,154.75, M $308.10. L: SPY put signal; no contract fits the US$90.42 budget; M: no affordable call on AMD, MRK, XOM, CVX, CSCO within US$154.05
- **2026-11-16** L $301.40, P $100,458.25, M $308.10. M: close MRK 2027-01-15 160C x1 at 1.79 (stop (-60%)), P&L -272.30; M: no affordable call on AMD, MRK, XOM, CVX, CSCO within US$154.05
- **2026-11-13** L $301.40, P $100,784.25, M $401.75. M: close CSCO 2027-01-15 115C x1 at 0.50 (stop (-60%)), P&L -148.30; M: no affordable call on AMD, MRK, CSCO, XOM, CVX within US$129.75
- **2026-11-12** L $301.40, P $100,865.25, M $608.90. no action
- **2026-11-11** L $301.40, P $100,930.75, M $596.90. no action
- **2026-11-10** L $301.40, P $100,721.25, M $584.90. M: buy CSCO 2027-01-15 115C x1 at 1.97 (delta 0.2106)
- **2026-11-09** L $301.40, P $100,708.25, M $620.55. P: close SPY 2026-12-11 690P x1 at 2.71 (take profit (50% of credit)), P&L +418.70; P: sell SPY 2026-12-18 715P x1 at 5.95 (delta -0.2039); M: no affordable call on AMD, MRK, CSCO, XOM, JNJ within US$278.05
- **2026-11-06** L $301.40, P $100,286.55, M $605.55. P: sell SPY 2026-12-11 690P x1 at 6.91 (delta -0.1957); M: close CSCO 2027-01-15 120C x1 at 0.65 (stop (-60%)), P&L -271.30; M: no affordable call on AMD, MRK, CSCO, XOM, CVX within US$278.05
- **2026-11-04** L $301.40, P $100,292.70, M $867.20. L: close SPY 2026-11-20 665P x1 at 0.32 (stop (-60%)), P&L -105.30; P: close SPY 2026-11-20 740P x1 at 2.24 (take profit (50% of credit)), P&L +292.70; P: no eligible put in the quotes
- **2026-11-03** L $361.55, P $99,856.35, M $725.70. no action
- **2026-11-02** L $376.05, P $99,787.85, M $856.70. L: QQQ put signal; no contract fits the US$112.81 budget

Caveats: closing quotes are used for both decision and fill; Book P's collateral earns nothing here (a real account would earn about the T-bill rate); no early assignment or dividends are modelled. Fewer than 30 closed trades per book is an illustration, not evidence.
