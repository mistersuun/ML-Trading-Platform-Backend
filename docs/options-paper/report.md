# Options paper test (as of 2026-10-15)

Paper only, rules in `PREREG.md` (frozen 2026-10-15). Inception 2026-10-14. Fills at ask/bid plus US$0.65 per contract; positions marked at mid. Not advice.

| Book | Start | Equity | Return | Benchmark | Bench value | Open | Closed |
|---|---:|---:|---:|---|---:|---:|---:|
| Book L: $500 lottery (buys calls/puts on QQQ/SPY) | $500 | $500.00 | +0.00% | QQQ buy-and-hold | $499.35 | 0 | 0 |
| Book P: SPY cash-secured put writer | $100,000 | $100,078.85 | +0.08% | SPY buy-and-hold | $100,284.56 | 1 | 0 |

## Book L: $500 lottery (buys calls/puts on QQQ/SPY)

No open position.

## Book P: SPY cash-secured put writer

Open:

| Contract | Qty | Side | Entry | Entry date | Mark |
|---|---:|---|---:|---|---:|
| SPY 2026-11-20 740P | 1 | short | 5.18 | 2026-10-14 | 4.38 |

## Session log

- **2026-10-15** L $500.00, P $100,078.85. no action
- **2026-10-14** L $500.00, P $99,996.85. P: sell SPY 2026-11-20 740P x1 at 5.18 (delta -0.2124)

Caveats: closing quotes are used for both decision and fill; Book P's collateral earns nothing here (a real account would earn about the T-bill rate); no early assignment or dividends are modelled. Fewer than 30 closed trades per book is an illustration, not evidence.
