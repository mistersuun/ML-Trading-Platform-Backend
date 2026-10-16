# Options paper test (as of 2026-10-16)

Paper only, rules in `PREREG.md` (frozen 2026-10-15). Inception 2026-10-14. Fills at ask/bid plus US$0.65 per contract; positions marked at mid. Not advice.

| Book | Start | Equity | Return | Benchmark | Bench value | Open | Closed |
|---|---:|---:|---:|---|---:|---:|---:|
| Book L: $500 lottery (buys calls/puts on QQQ/SPY) | $500 | $500.00 | +0.00% | QQQ buy-and-hold | $486.86 | 0 | 0 |
| Book P: SPY cash-secured put writer | $100,000 | $99,817.85 | -0.18% | SPY buy-and-hold | $99,174.90 | 1 | 0 |
| Book M: $1,000 momentum calls (aggressive group, from 2026-10-16) | $1,000 | $979.35 | -2.06% | QQQ buy-and-hold | $1,000.00 | 1 | 0 |

## Book L: $500 lottery (buys calls/puts on QQQ/SPY)

No open position.

## Book P: SPY cash-secured put writer

Open:

| Contract | Qty | Side | Entry | Entry date | Mark |
|---|---:|---|---:|---|---:|
| SPY 2026-11-20 740P | 1 | short | 5.18 | 2026-10-14 | 7.00 |

## Book M: $1,000 momentum calls (aggressive group, from 2026-10-16)

Open:

| Contract | Qty | Side | Entry | Entry date | Mark |
|---|---:|---|---:|---|---:|
| MRK 2027-01-15 160C | 1 | long | 4.50 | 2026-10-16 | 4.30 |

## Session log

- **2026-10-16** L $500.00, P $99,817.85, M $979.35. L: QQQ put signal; no contract fits the US$150.00 budget; M: buy MRK 2027-01-15 160C x1 at 4.50 (delta 0.3188)
- **2026-10-15** L $500.00, P $100,078.85. no action
- **2026-10-14** L $500.00, P $99,996.85. P: sell SPY 2026-11-20 740P x1 at 5.18 (delta -0.2124)

Caveats: closing quotes are used for both decision and fill; Book P's collateral earns nothing here (a real account would earn about the T-bill rate); no early assignment or dividends are modelled. Fewer than 30 closed trades per book is an illustration, not evidence.
