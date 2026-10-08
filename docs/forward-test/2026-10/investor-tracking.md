# Investor shadow portfolios vs SPY (as of 2026-10-07)

Inception 2026-10-06: close of 2026-10-06. Start value $10,000 each, buy-and-hold, price return only. Paper tracking, not advice. Weights are secondary-source 13F data, unverified against SEC.

| Portfolio | Value | Since inception | vs SPY | 1d | 1M | 1Y | Missing |
|---|---:|---:|---:|---:|---:|---:|---|
| Berkshire 13F clone (top 5) | $10,017.29 | +0.17% | +0.41% | +0.17% | -1.92% | +23.94% (46% cov.) | - |
| TCI (Hohn) 13F clone | $9,932.84 | -0.67% | -0.43% | -0.67% | -5.03% | n/a | - |
| Himalaya (Li Lu) 13F clone | $10,031.25 | +0.31% | +0.55% | +0.31% | +1.14% | +25.02% (40% cov.) | - |
| Pershing Square 13F clone | $9,922.86 | -0.77% | -0.53% | -0.77% | +1.25% | +6.45% (41% cov.) | - |
| Appaloosa (Tepper) 13F clone | $10,106.51 | +1.07% | +1.31% | +1.07% | +3.57% | +17.20% (27% cov.) | - |
| Consensus best ideas | $10,050.67 | +0.51% | +0.75% | +0.51% | -0.37% | +17.20% (25% cov.) | - |
| Listed vehicles (USD) | $9,951.34 | -0.49% | -0.25% | -0.49% | -3.38% | -2.90% (75% cov.) | - |
| Fairfax Financial (FFH.TO, CAD) | $9,944.80 | -0.55% | -0.31% | -0.55% | -0.82% | n/a | - |
| ETF basket (NANC, GURU, GVIP) | $9,959.50 | -0.40% | -0.16% | -0.40% | -0.13% | +13.97% (33% cov.) | - |
| SPY (control) | $9,976.00 | -0.24% | +0.00% | -0.24% | +0.91% | +16.16% | - |
| QQQ (control) | $9,974.59 | -0.25% | -0.01% | -0.25% | +5.39% | +25.35% | - |

1M and 1Y are trailing static-weight returns to the as-of date for context; where a ticker has no history that far back it is left out, the rest renormalised, and the weight coverage is shown.

## Daily value of $10,000

| Date | Berkshire 13F clone (top 5) | TCI (Hohn) 13F clone | Himalaya (Li Lu) 13F clone | Pershing Square 13F clone | Appaloosa (Tepper) 13F clone | Consensus best ideas | Listed vehicles (USD) | Fairfax Financial (FFH.TO, CAD) | ETF basket (NANC, GURU, GVIP) | SPY (control) | QQQ (control) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-10-06 | 10,000.00 | 10,000.00 | 10,000.00 | 10,000.00 | 10,000.00 | 10,000.00 | 10,000.00 | 10,000.00 | 10,000.00 | 10,000.00 | 10,000.00 |
| 2026-10-07 | 10,017.29 | 9,932.84 | 10,031.25 | 9,922.86 | 10,106.51 | 10,050.67 | 9,951.34 | 9,944.80 | 9,959.50 | 9,976.00 | 9,974.59 |

## Notes

- **Berkshire 13F clone (top 5)**: Q2 2026 13F top 5 holdings, weights renormalised to 100%, unverified against SEC (secondary sources). Skipped: CVX, OXY, MCO named as holdings but no weight in the note.
- **TCI (Hohn) 13F clone**: Q2 2026 13F top 5 holdings, weights renormalised to 100%, unverified against SEC (secondary sources). Skipped: MLM and FER (no weight stated); non-US holdings are not on the 13F.
- **Himalaya (Li Lu) 13F clone**: Q2 2026 13F, 5 of 8 positions with stated weights (GOOGL 24.6 + GOOG 23.4 + PDD 22.2 + BRK.B 15.0 + EWBC 9.7), renormalised to 100%, unverified against SEC (secondary sources). Skipped: 3 smaller positions (about 5% of the book, not named).
- **Pershing Square 13F clone**: Q2 2026 13F top 7 holdings, weights renormalised to 100%, unverified against SEC (secondary sources). Skipped: V, MA, SPGI, NFLX are new positions with no weight in the note; UMG is not US-listed. BN is the NYSE line (Brookfield Corp).
- **Appaloosa (Tepper) 13F clone**: Q2 2026 13F top 5 holdings (AMZN ~15.5% taken as the midpoint of 15 to 16%), weights renormalised to 100%, unverified against SEC (secondary sources). High turnover: a 45-day-late copy is stale. Skipped: AAPL, BA, AAL, CRWV, META, EWY (no weight).
- **Consensus best ideas**: Names held by 3 or more of the 8 managers in the research note (Berkshire, Pershing, Duquesne, Appaloosa, Himalaya, TCI, Akre, Baupost), Alphabet counted once and held as GOOGL, equal weight: GOOGL (7 managers), AMZN (4), V (3: Pershing, TCI, Akre), MCO (3: Berkshire, TCI, Akre). Skipped: none.
- **Listed vehicles (USD)**: Equal weight BRK.B, PSHD (Pershing Square Holdings, LSE USD line), MKL, L. FFH is tracked separately because it trades in CAD. Skipped: FFH (CAD, own portfolio). PSHD trades on the LSE, so its bar can be one session ahead of the US names.
- **Fairfax Financial (FFH.TO, CAD)**: Single listed vehicle on TSX, price in CAD, no FX adjustment; the SPY comparison is not like for like. Skipped: none.
- **ETF basket (NANC, GURU, GVIP)**: Equal weight congressional-trades and 13F-copying ETFs. Skipped: KRUZ not found by search_contracts.
- **SPY (control)**: Benchmark. Skipped: none.
- **QQQ (control)**: Tech-tilt control. Skipped: none.
