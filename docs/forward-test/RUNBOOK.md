# Forward paper test: daily runbook (October 2026, one week)

What this is: the platform's full nightly pipeline (scan, validation, risk gates, `execution.submit_intent`,
reconciliation) running end to end WITHOUT Alpaca. Prices come from IBKR (read-only MCP `get_price_history`),
which the orchestrator fetches each evening and writes into `state/external_bars/`. Orders go to a simulated
paper broker (`brokers/sim.py`, state in `state/sim_broker.json`, fictional 100,000 USD).

Safety (unchanged and tested): paper only, no live mode, long-only, US equities/ETFs only executable, every order
through `execution.submit_intent`. IBKR is READ-ONLY: only `search_contracts` and `get_price_history`
(get_*/search_* tools). NEVER call any IBKR tool named create_*, delete_*, edit_*, update_* or set_*.
Never read, print or commit `state/holdings*.csv` or `state/ibkr_snapshot*.json`. The shell has no internet
(Alpaca, Yahoo, SEC blocked); the sim cannot use a network even if it tried.

Trading is OFF by default in this app (`TRADING_MODE=off`). `main.py forward ...` switches paper trading ON for that
process only, and only for the simulated broker. More precisely, `config.enable_forward_test()` forces the external
bar source, the sim broker, a separate state DB (`state/forward/trading.db`), console alerts and no IBKR account
sync, but leaves `TRADING_MODE=off`; only `forward step` turns paper trading on, for the duration of the step. The
journal records this on every day. `FORWARD_TEST=1` is only for read-only/setup commands that must see the same
files (`risk init`, `risk status`). Do NOT run `run-nightly` or `scan --paper` under it: only `forward step` may
place sim orders (the sim also refuses orders while its clock is behind the bar files).

## 0. Fresh container only

```bash
cd /home/user/ML-Trading-Platform-Backend
git pull --rebase                               # picks up docs/forward-test/2026-10/snapshot/
FORWARD_TEST=1 .venv/bin/python main.py forward restore   # restores sim_broker.json + forward trading.db if missing
FORWARD_TEST=1 .venv/bin/python main.py risk init         # only if state/forward/trading.db still does not exist
```

`state/` is gitignored, so the committed snapshot (`docs/forward-test/2026-10/snapshot/`, written by every
`forward step`) is how the sim account and risk state survive a container restart. Bars are not committed:
re-fetch them (step 2, FIVE_YEARS path).

## 1. When to run

Each US trading day after the close and after IBKR has the final daily bar: fetch only after about 17:30 ET.
Run on trading days only. The runner ignores bars of sessions that have not closed yet and dates that are not
XNYS sessions, but a fetch before the final bar would simply leave you one session behind. A session already
journaled is refused (`already journaled`, exit 1, a `crash` line in the journal) unless you pass `--rerun`, which
replaces that day's journal row instead of adding a second one.

## 2. Fetch bars (orchestrator, via MCP)

For every symbol below call `get_price_history` with `security_type=STK, step=ONE_DAY, outside_rth=false` and
- `period=FIVE_YEARS` if `state/external_bars/<SYMBOL>.json` does not exist (fresh container / new symbol),
- else `step_count=10` (a top-up; the ingest merges by timestamp).

Large results are saved to a file by the tool; pass that file path to the ingest script (or pipe stdin).

```bash
# full history (replaces <SYMBOL>.json)
.venv/bin/python scripts/ingest_ibkr_bars.py AAPL /path/to/saved_tool_output.json
# daily top-up (adds AAPL.<last-bar-date>.json, merged by timestamp, later files win)
.venv/bin/python scripts/ingest_ibkr_bars.py AAPL /path/to/saved_tool_output.json --topup
# or:  cat output.json | .venv/bin/python scripts/ingest_ibkr_bars.py AAPL - --topup
```

The script validates the payload, drops vendor metadata and writes atomically. IBKR stamps daily bars at the
session open (13:30Z summer / 14:30Z winter); the platform converts that to the New York session date. A bar for a
session that has not closed yet is dropped (`external_bars.closed_bars`, used by the pipeline, the forward runner
and the sim), so a top-up fetched at 15:00 ET does not change what runs, but the day then is not yet available:
fetch again after about 17:30 ET. A full `FIVE_YEARS` ingest deletes that symbol's old top-up files; a `--topup`
whose overlapping closes differ from the stored history by more than 1% is refused (split/adjustment: re-fetch the
full history). A symbol with only top-up files and no base file is not accepted into research. Known data notes: prices are split-adjusted (not dividend-adjusted); IBKR sometimes prints a Close a few
bp outside High/Low, which the loader widens (logged); history is 5 years, shorter than the 8-year research window
(coverage is judged from the first bar of the file, only when a base `<SYMBOL>.json` exists or there are >= 500 bars); META starts 2022-06-09 (ticker history) and XOM resolves to the
new ExxonMobil holding company contract.

### Contract ids (resolved with `search_contracts`, US primary listings, security_type STK)

| Symbol | contract_id | Exchange | Description |
|---|---|---|---|
| AAPL | 265598 | NASDAQ | APPLE INC |
| MSFT | 272093 | NASDAQ | MICROSOFT CORP |
| NVDA | 4815747 | NASDAQ | NVIDIA CORP |
| TSLA | 76792991 | NASDAQ | TESLA INC |
| AMZN | 3691937 | NASDAQ | AMAZON.COM INC |
| META | 107113386 | NASDAQ | META PLATFORMS INC-CLASS A |
| GOOG | 208813720 | NASDAQ | ALPHABET INC-CL C |
| AMD | 4391 | NASDAQ | ADVANCED MICRO DEVICES |
| SPY | 756733 | ARCA | SPDR S&P 500 ETF TRUST (also the benchmark and the "latest session" reference: required) |
| QQQ | 320227571 | NASDAQ | INVESCO QQQ TRUST SERIES 1 |
| IWM | 9579970 | ARCA | ISHARES RUSSELL 2000 ETF |
| DIA | 73128548 | ARCA | SPDR DOW JONES INDUS AVG |
| JPM | 1520593 | NYSE | JPMORGAN CHASE & CO |
| GS | 4627828 | NYSE | GOLDMAN SACHS GROUP INC |
| BAC | 10098 | NYSE | BANK OF AMERICA CORP |
| XOM | 895178251 | NYSE | EXXONMOBIL HOLDINGS CORP |
| CVX | 5684 | NYSE | CHEVRON CORP |

These 17 are `config.WATCHLIST["stocks"]` and are the only REQUIRED symbols (only US stocks/ETFs are executable).
Crypto, forex and futures (`WATCHLIST["crypto"|"forex"|"futures"]`) are optional and research/alert-only; none were
resolved. To add one: `search_contracts` with the ticker or name, pick the row whose `symbol` matches exactly (futures:
`search_futures`; forex pairs `CASH`; crypto `CRYPTO`), fetch with the matching `security_type`, and ingest under the
platform symbol (`BTC-USD`, `EURUSD=X`, `GC=F`) with `--asset-class crypto|fx|futures` (UTC session dates; crypto
daily bars need the 24/7 calendar, so only add them if you accept the extra validation noise).

## 3. Run the night

```bash
FORWARD_TEST=1 .venv/bin/python main.py risk init      # only if the state DB does not exist (step 0)
.venv/bin/python main.py forward step                  # add --date YYYY-MM-DD to assert the expected session
                                                       # 2026-10-06 session only: add --plumbing-test;
                                                       # 2026-10-13 session: add --plumbing-test-close (section 3b)
.venv/bin/python main.py forward report                # cumulative table, writes docs/forward-test/2026-10/REPORT.md
```

`forward step` (about 15 minutes with stress and ML on 17 symbols; `--no-stress` is faster):
1. finds the latest CLOSED session in the bar files (SPY), refuses a mismatching `--date` or an already-journaled
   session (without `--rerun`);
2. advances the sim broker to that session (refuses if a held/ordered symbol lacks a bar for a session; fetch it and
   retry) (yesterday's orders fill at this session's open, brackets are evaluated on
   the day's high/low, stop before target, gap-through-stop fills at the open, 5 bp slippage, 0 commission);
3. runs `scheduler.run_nightly` (technical + pairs + ML, validation, risk gates, reconciliation) with intents routed to
   the sim through `execution.submit_intent`; orders placed tonight fill at tomorrow's open;
4. refreshes `snapshot/` (sim file, trading DB, trials registry, holdout-read ledger), appends to
   `docs/forward-test/2026-10/journal.jsonl`, writes `days/<date>.md`, scores earlier shadow signals whose 1-day /
   5-day horizon is now reached.

Status `degraded` (exit 1) means a REQUIRED watchlist symbol is missing or stale. A crash is printed, exits 1 and is
journaled as a `crash` line (listed by `forward report`): fix the cause, re-run. `forward restore` is all-or-nothing:
it refuses unless every snapshotted file is missing, or `--force` overwrites them all.

## 3b. The plumbing test (ONE labelled trade, NOT a strategy)

Because the strict eligibility bar may leave the whole week with zero orders, one SPY long is placed on purpose to
exercise the order path that zero candidates never reach: next-open fill, bracket stop + take-profit legs, risk
checks, sizing, reconciliation with an open position, position marking, exit handling. It is a wiring check and
NEVER evidence for any strategy.

- The 2026-10-06 session (night 2 of the week; 2026-10-05 is already journaled without it) uses the flag: `.venv/bin/python main.py forward step --plumbing-test`.
  Nights in between use the plain `forward step` (the flag on a later night is harmless: it is skipped, see below),
  and the 2026-10-13 session uses `--plumbing-test-close`. Do not skip either dated flag.
- What it does: builds a SPY long intent (`execution.build_plumbing_intent`, source `plumbing_test`) sized by the normal
  sizing code (about 1-3 shares, capped by the symbol/order notional on a 10,000 USD sleeve), bracket stop 3% below the
  entry reference close and the platform take-profit (3 x ATR, i.e. 4.5% above), GTC legs, and sends it through
  `execution.submit_intent` with ALL normal gates (kill switch, halts, ladder, exposure, duplicates, client_order_id
  ledger, reconciliation). The only thing bypassed is the candidate validation-status requirement, and only for this
  source, only for SPY, and only when the broker is the sim in forward mode. With the Alpaca broker or outside the
  forward runner, creating the intent raises `PlumbingTestRefused` and `submit_intent` rejects it
  (`plumbing_test_not_allowed`). Normal intents are unchanged (`ORDER_ELIGIBLE_STATUSES` is untouched).
- Idempotent: it is placed once per forward run. A rerun, or the flag on a later night, journals `skipped
  already_placed:<client_order_id>` (checked against the signal ledger and the journal). If risk gates reject it
  (e.g. halted), nothing is placed and the flag can be used again on a later night. The entry is also skipped
  unless the nightly run is healthy (nightly `status` `ok` and no required symbol stale or missing): it then
  journals `skipped`, reason `nightly_not_ok:<status>` (e.g. `nightly_not_ok:error`, `nightly_not_ok:degraded`).
  That reason is not a placed state, so after fixing the cause the flag can be used again with `--rerun`.
- It fills at the next session's open (5 bp slippage) like any order; the stop/target legs are then evaluated on the
  daily bars like any bracket. Expect on the 2026-10-07 run: a SPY fill, a position flagged `[PLUMBING TEST — not a
  strategy]`, reconciliation OK (position + live stop leg).
- Closing: if neither leg has triggered, close it on the LAST regular night (the 2026-10-13 session) with
  `.venv/bin/python main.py forward step --plumbing-test-close` (alias `--plumbing-close`; mutually exclusive with
  `--plumbing-test`). That cancels the bracket legs and sells at the next open, so the exit fill shows in the following
  night's run (2026-10-14, if that run is made; otherwise the position is simply closed in the next session).
  It is refused (journaled `skipped`) on the entry night, with no plumbing entry, or if the position is already gone.
- Interactions with strategy risk handling (NOT fully isolated; the D5 caps are unchanged): while the SPY position
  is open it takes one of the 2 `us_index` cluster slots (`MAX_POSITIONS_PER_CLUSTER=2`, shared with QQQ, IWM, DIA),
  so a QQQ/IWM/DIA strategy intent can be rejected as `max_positions_per_cluster`; it takes 1 of the 8 open-position
  slots, about 7.8% of gross exposure, about 23 USD of heat, and one `MAX_ORDERS_PER_DAY` slot on its entry night.
  Its P&L feeds sleeve equity, so the drawdown ladder and daily-loss halts see it, and the report's drawdown column
  includes it (equity and return columns exclude it). The plumbing position is identified from its own fills, so a
  later strategy SPY position is reported as a strategy position, not as the plumbing trade.
- Journal: separate `"type": "plumbing_test"` lines (actions `entry`, `close`, `status`), a section "PLUMBING TEST — not
  a strategy" in each day file (state, bracket, position, fills, P&L, tonight's decision), and a note on the sim equity
  line. The day entry has `plumbing_test` and `account.plumbing_pl / equity_ex_plumbing / return_pct_ex_plumbing`. The
  plumbing decision is NOT in `decisions` (no intent counts, no shadow item, no shadow score). `forward report` shows it
  in its own section and its equity, return, fills and positions columns exclude it.
- Daily-trigger expectations: the 2026-10-06 night (`--plumbing-test`): one `plumbing_test` entry line with outcome `submitted`
  (qty, stop, take-profit), the SPY order open with two held legs, no position yet, reconciliation OK, funnel and
  `decisions` unaffected. The next night: one SPY fill at the open, state `open`, reconciliation OK. Nights 3-6: `status`
  lines; a stop or take-profit leg fill ends it (`closed`, exit reason), otherwise close it on 2026-10-13 as above.
  A reconciliation MISMATCH or a `plumbing test ...` entry in `errors` is a real finding: report it, do not hide it.
  Nothing else changes: zero strategy orders is still a valid result, and the plumbing trade must not be quoted as one.

## 4. Commit only the journal, then push

```bash
git add docs/forward-test/
git commit -m "forward test: <date>"
git push
```

Nothing under `state/` or `results/` is ever committed. Do not commit code changes from this loop.

## Reading the output

- `days/<date>.md`: status and timings, freshness per symbol, funnel (candidates, each gate, eligible), intents with
  risk decisions, fills, positions, sim equity/cash/drawdown vs peak, SPY return over the same period, risk ladder and
  halts, reconciliation, errors/warnings, and the SHADOW list.
- Shadow signals: every alert-only or non-eligible signal (not deflated_validated, unvalidated ML, pairs, blocked
  intents) is recorded with tonight's close as entry price; later `forward step` runs append `shadow_score` lines
  (1-day and 5-day forward return, direction-signed; the headline `signed_return` is measured from the NEXT session's
  open, the executable entry, and `fwd_return` from tonight's close; a symbol not current on the signal day is not scored) and `forward report` aggregates them by kind and reason.
- Expectation: the order-eligibility bar (`deflated_validated`) is strict, so a week may produce zero orders. That is a
  valid finding; the shadow table is how to judge whether the gate is too tight.

## Investor shadow portfolios (side study, does not touch the sim)

Tracks "famous investor" 13F clones, listed vehicles and ETFs next to SPY (definitions, weights, conids:
`docs/forward-test/2026-10/investor-portfolios.json`). Inception is the close of 2026-10-06 (until that bar is on
file the latest close is used and the output says so). Each evening, after the normal fetch, top up these tickers
the same way as the watchlist (`get_price_history` ONE_WEEK or ONE_MONTH, ONE_DAY, `outside_rth=false`, then
`.venv/bin/python scripts/ingest_ibkr_bars.py <SYMBOL> <file> --topup`, `--asset-class etf` for ETFs). SPY, QQQ,
AAPL, AMZN, GOOG, META, MSFT and BAC are already in the watchlist top-up. BRK.B (conid 72063691, stored as `BRK_B`), MKL (271460, NYSE) and L (9252, NYSE, Loews) are NOT among the 17 and need their own daily top-up. GURU: skip until its base history is re-fetched (bad 2024-12-18 bar). PSHD (LSE) can carry a partial same-day bar if fetched before the London close; later top-ups supersede it.

| Ticker | conid | Ticker | conid | Ticker | conid |
|---|---|---|---|---|---|
| GOOGL | 208813719 | AXP | 4721 | KO | 8894 |
| V | 49462172 | MCO | 6497 | GE | 498843743 |
| SPGI | 229629397 | UBER | 365207014 | PDD | 326398585 |
| MU | 9939 | TSM | 6223250 | BN (NYSE) | 599900800 |
| HHH | 647324601 | QSR | 176372132 | EWBC | 4730364 |
| NANC (etf) | 751081186 | GVIP (etf) | 254147634 | GURU (etf) | 108473907 |
| PSHD (LSE, USD) | 170204225 | FFH (TSX, CAD) | 14890639 | BRK.B | 72063691 |
| SPY | 756733 | QQQ | 320227571 | | |

Notes: fetch FFH and PSHD without an `exchange` argument (SMART; data is delayed 15 min, fine for daily bars; a
PSHD bar for the current London day can be partial, so fetch after the London close). The tracker only uses SPY
dates, so a not-yet-final bar for a later day is ignored. GURU's base file has an old bad bar (2024-12-18), so
the tracker reads its closes without the OHLC check and logs a warning. No IBKR write tools, as always.

Run (offline, safe to repeat; one row per date is replaced, not duplicated):

```bash
.venv/bin/python scripts/track_investor_portfolios.py            # as of today
.venv/bin/python scripts/track_investor_portfolios.py --asof 2026-10-09
```

Writes `docs/forward-test/2026-10/investor-tracking.jsonl` (one row per date) and rewrites
`investor-tracking.md` (value of $10,000, since-inception return, vs SPY, 1d/1M/1Y). Commit those two files with the
journal (`git add docs/forward-test/`).
