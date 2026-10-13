# Long-history pooled run: owner's machine (D22)

Status: written 2026-10-10 under D20 and D22. **Not runnable yet**: the converter and runner in section 2 do not exist,
and they are written in the clean-up commit after the forward week (2026-10-14). Nothing in this file touches the
container, `state/forward/`, `state/sim_broker.json` or `docs/forward-test/2026-10/`.

What this is: the UNCHANGED pooled shadow (21 evaluated patterns, same gates, same `HOLDOUT_START = 2025-07-01`,
same pooling method version "2") on 15 to 20 years of yfinance daily bars for universe C of D22. Why: calendar length
T is the binding constraint (review section 2.4: the required pooled Sharpe at N = 44 is 2.57 at T = 672 and 1.33 and
0.94 at 10 and 20 years). Even a null result is informative.

## 0. Frozen inputs (D22; do not change)

| | |
|---|---|
| Universe B (27) | `AAPL AMD AMZN BAC CVX DIA GOOG GS IWM JPM META MSFT NVDA QQQ SPY TSLA XOM` + `XLE XLU XLV XLP XLF TLT IEF GLD EEM EFA`; hash `b80e79d265e12cf5` |
| Universe C | B restricted to tickers whose FIRST yfinance daily bar is on or before 2010-06-30 (dates only, never prices). The list and its hash come from the runner. |
| Universe A | the current 17, hash `6eabbbfea159b2ee`; already paid for, not the object of this run |
| Bars | yfinance daily, `auto_adjust=True` (split and dividend adjusted O/H/L/C), history from 2005-01-01 or the first available bar, through the day you run. Dividend- and split-adjusted bars were chosen before any fetch; C is not compared to A's ledger numbers (A's IBKR bars are price bars, so the series are not numerically comparable) |
| Cut | evaluation uses bars strictly before 2025-07-01; later bars may be in the files (the hold-out read is made by the code, once) |
| N | 44 (22 + 22 for the combined B and C registration); the run must report `n_trials = 44` |
| Rules / gates | exactly as built on the nightly shadow: no flag changes, no pattern subset |

## 1. Before you start

0. Setup (owner's machine): `python -m venv .venv && .venv/bin/pip install -r requirements.txt` (it pins `yfinance==1.7.0`).

1. Use a clean checkout of the branch that contains the clean-up commit (it contains the two scripts below). Do NOT
   run this on the container's `state/`; use a separate directory.
2. Keep the fetched files unmodified. Do not plot, chart, describe or compute returns on them. Do not open the
   result JSON beyond what section 4 sends back.
3. Write down the date and time you start. The run is a single look.

## 2. Scripts that must exist (written in the clean-up commit; none exists today)

There is no converter today: `scripts/ingest_ibkr_bars.py` only takes IBKR `get_price_history` JSON. Two scripts
are specified here and tested in the clean-up commit:

**`scripts/fetch_yfinance_bars.py`** (owner's machine only, network)
- Input: the symbol list of universe B (hard-coded from D22) and an output directory (default `state/external_bars_long/`).
- For each symbol: `yfinance.Ticker(s).history(period="max", interval="1d", auto_adjust=True)`; keep Open, High, Low,
  Close, Volume; drop rows with NaN or non-positive prices; keep only sessions up to the run date.
- Write the IBKR columnar format that `external_bars.parse_payload` reads: `{"time": [...], "open": [...], "high": [...],
  "low": [...], "close": [...], "volume": [...]}`. **The time stamp must be `YYYY-MM-DDT16:00:00Z` for the session
  date** (16:00Z is 11:00 or 12:00 in New York, so `parse_payload`'s conversion to America/New_York and normalise
  returns the same calendar date; a midnight-UTC stamp would land on the previous day).
- Validate each file with `external_bars.parse_payload` + `check_frame` before writing; refuse any file that fails.
  Write `<SYMBOL>.json` (no top-ups).
- Print per symbol: first bar date, last bar date, number of bars. Print nothing about prices.
- Write `universe_C.json` = the symbols whose first bar date is on or before 2010-06-30, with their hash from
  `pooled_validation.universe_hash`. This is the mechanical D22 rule; it is not editable by hand.

**`scripts/run_long_history_pooled.py`**
- Loads the frames from the output directory with `external_bars.load_bars`, restricts to `universe_C.json`, and
  refuses to run unless the symbol list equals the D22 rule (first bar <= 2010-06-30) and at least 8 symbols remain.
- Sets `POOLED_APPROVED_UNIVERSES[<C hash>] = "D22"` in-process only and passes the N floor of 44 through a new keyword `n_trials_floor` on `evaluate_pooled` (it only raises: `n_trials = max(ledger count, pooled_universe_trials(), n_trials_floor)`; applied in `evaluate_pooled_research`, default off, so nothing in the nightly path changes until the clean-up commit; the code takes `max(ledger count, pooled_universe_trials())` today, so 22 would be wrongly low); it must abort if the result reports
  `n_trials < 44`.
- Calls `pooled_validation.evaluate_pooled_research` (in-memory ledger and hold-out store: it never writes a live
  registry). Because that store is in memory, the single-look rule is enforced by the script: it writes
  `results/long_history/D22-<UTC>.json` and refuses to run again if any `D22-*.json` exists, unless
  `--disclosed-reread` is given and a decision entry is cited.
- Prints only the funnel and the per-pattern table of section 4 (it does not print the series, equity curves, or
  per-symbol returns).

## 3. The steps

```bash
cd ML-Trading-Platform-Backend            # clean checkout, clean-up commit present
.venv/bin/python scripts/fetch_yfinance_bars.py --out state/external_bars_long
.venv/bin/python scripts/run_long_history_pooled.py --bars state/external_bars_long --decision D22
```

Notes: step one needs the internet and takes a few minutes; step two takes minutes to tens of minutes on 20 years
(the container needed about 17 s for 5 years and 17 symbols; the null escalates only when p is at or below alpha).
If a download fails, rerun step one for that symbol; if a symbol is delisted or has no data the runner reports it as
missing, and more than 10% of the universe missing makes the run `pooled_universe_incomplete` (nothing evaluated;
report that, do not repair it).

## 4. What to send back (and nothing else)

1. The funnel: number of patterns evaluated, and at each gate how many pass (trades/clusters/breadth, OOS > 0, PSR,
   BH on the cluster null, DSR, concentration, cost x2, delay, capped replay, hold-out), with `n_trials`, T (OOS
   days), the required annualised Sharpe at that T and N, the universe hash and the list of symbols in C.
2. The per-pattern table: pattern, status, the gate where it stops, trades, entry-week clusters, symbols with >= 3
   trades, mean R, pooled annualised Sharpe, PSR, cluster-null p, DSR p.

Paste the two tables as text. Do not send charts, equity curves, per-symbol results, hold-out breakdowns, or your
interpretation. The statement "no pattern passed" is a complete and acceptable result.

## 5. No-peeking rules

- One run, one look (D22). If the run crashes before writing the result file, fix the plumbing and rerun without touching any input file; if it wrote
  a result, it counts as the look.
- Do not rerun with another universe, period, pattern subset, gate, parameter, cost or exit. Do not drop a symbol
  because of how it traded. Any such change is a new trial (N += 22 for a universe or exit change) under D18 §6.1.
- Do not compute or view any pattern's or the pooled series' performance outside the runner's printed tables.
- Do not read the hold-out return of any pattern; the code reads it once, only for patterns that pass every other
  gate, and the result reports pass or fail.
- Do not tell Claude (or anyone) the numbers before the two tables are final; do not tune the next step on a single
  pattern's number. Decisions after the look (D21 first look, the clean-up version, allocation work) are made in
  decision entries, in writing, before the next computation.
- Treat survivorship honestly: the 13 single names in C are today's winners. A pass is shadow-only evidence, and the
  confirmation remains the forward shadow period.
