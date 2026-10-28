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
| CBRS | 882732191 | CRWV | 771759702 | CROX | 37792836 |
| PYPL | 199169591 | SMH (etf) | 229725622 | QUAL (etf) | 131255429 |
| MAR | 9358 | SYK | 4430948 | WAT | 1448494 |
| SEQUX (FUND NAV) | 141447600 | | | | |

Added 2026-10-10 (Altimeter, Punch Card, Sequoia, Fundsmith portfolios): NVDA, META, TSM, PDD, V, UBER, BRK.B and SPY/QQQ are already covered above or by the watchlist top-up. Evening top-up checklist for these (10 symbols): CBRS, CRWV, CROX, PYPL, MAR, SYK, WAT and the ETFs SMH, QUAL, plus SEQUX below. The first fetch (ONE_MONTH) is saved as `<SYM>.investor.json`; every evening thereafter `ingest_ibkr_bars.py <SYM> <file> --topup` (ETFs with `--asset-class etf`); both files are merged as top-ups. SGOV is no longer tracked (Punch Card has no stated SGOV weight). SEQUX is a mutual-fund NAV: call `get_price_history` with `security_type=FUND` and `exchange=FUNDSERV` (without the exchange the call errored), `ingest_ibkr_bars.py SEQUX <file> --topup`; the NAV for a day can repeat the prior day's value. These four portfolios (and the two definition-only ones) carry their own `inception_from: 2026-10-10`: inception is the first SPY session on or after that date (Monday 2026-10-12), shown as "pending" until that bar is on file. The 13D activist and insider cluster-buy baskets are `definition_only` (rules in the JSON, no holdings): EDGAR is blocked in the container, so the owner's machine must populate them; nothing is tracked or invented meanwhile.

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

## 5. Continuation after the forward week (from the 2026-10-15 session, owner approved 2026-10-15)

The week ended with `docs/forward-test/2026-10/FINAL-REPORT.md`. Nightly runs continue so the D18 pooled shadow can accumulate its
confirmation window (at least 20 forward shadow sessions over at least 28 days). Differences from the week:

- Run `POOLED_SKIP_IN_FORWARD_TEST=false .venv/bin/python main.py forward step` so the pooled shadow evaluation runs inside the
  forward step (it is shadow-only and can never place an order; its trials and hold-out reads go to the forward state).
- No plumbing flags: the plumbing test is finished (closed 2026-10-14).
- Investor tracker: also top up VTV (27638093) and RSP (319359375), the style benchmarks added 2026-10-15. SEQUX converts into the
  Sequoia ETF (SEQ, NYSE Arca) after about 2026-10-16; once SEQUX stops printing, resolve SEQ with `search_contracts`, record the
  conversion ratio, and switch the `sequoia_nav` portfolio to SEQ in `investor-portfolios.json`.
- Planned end: the 2026-11-13 session, then a short pooled-shadow summary; the routine is then disabled.

## 6. Options paper test (owner request 2026-10-15; rules frozen in `docs/options-paper/PREREG.md`)

Paper only. IBKR read-only: `get_option_parameters`, `get_option_data`, `get_price_snapshot`. Never `create_order_instruction`
or any other write tool. Every nightly session, after the SPY/QQQ top-up is ingested:

1. `.venv/bin/python scripts/options_paper.py plan` lists the open contracts and the wanted entries (book, underlying,
   right, DTE window, strike range).
2. Fetch quotes with `market_data_names=["bid_ask","option_greeks"]`:
   - **Open contracts:** one snapshot each.
   - **Each wanted entry:**
     - Expirations come from `get_option_parameters`, using SPY 756733 and QQQ 320227571. Pick the expiry in the DTE
       window closest to `target_dte`, preferring the trading class equal to the symbol.
     - Get strikes with `get_option_data` (bounded by the strike range).
     - Snapshot about 5 to 8 strikes spread over the range. For Book P, centre them on |delta| ≈ 0.20, roughly 4% below
       spot. For Book L calls and puts, step outward from spot until the ask × 100 exceeds 30% of Book L equity.
3. Save the quotes as `docs/options-paper/quotes/<session>.json` (format in the script docstring), then run
   `.venv/bin/python scripts/options_paper.py step --quotes docs/options-paper/quotes/<session>.json`.
4. Commit `docs/options-paper/` with the forward-test files.

## 7. Day-trading paper test (owner request 2026-10-15; rules frozen in `docs/daytrade-paper/PREREG.md`)

Paper only; IBKR read-only. Every nightly session (forward record starts with the 2026-10-15 session):

1. Fetch the session's 5-minute regular-hours bars for QQQ (320227571) and SPY (756733):
   `get_price_history(contract_id, security_type="STK", step="FIVE_MINS", outside_rth=false, period="ONE_DAY")`.
   - About 6 KB each.
   - Save the raw JSON as `docs/daytrade-paper/bars/<SYM>.<YYYYMMDD>.json`.
   - If a night was missed, use `step_count` (at most 1000 bars, about 12 sessions) to back-fill.
2. Run `.venv/bin/python scripts/daytrade_paper.py step`. This defaults to the latest complete session present for both
   symbols. Missed sessions are stepped in date order with `--asof`.
3. Commit `docs/daytrade-paper/`.

## 8. Five US$1,000 quant accounts (owner request 2026-10-15; rules frozen in `docs/quant-accounts/PREREG.md`)

Paper only; IBKR is read-only. The live window runs from the 2026-10-14 close decision (first fills at the 2026-10-15 open) to the 2026-11-16 close.

1. Nightly, besides the section 2 top-ups, top up daily bars for the extra symbols and save them as
   `state/external_bars/<SYM>.<YYYYMMDD>.json`.
   - ETFs: EFA 13002510, IEF 15547844, GLD 51529211, DBC 319355208, SGOV 424099317.
   - Stocks: UNH 13272, JNJ 8719, PG 11054, HD 7930, LLY 9160, AVGO 313130367, COST 272997, WMT 13824, PEP 11017,
     MRK 70101545, ABBV 118089500, CSCO 268084, ORCL 272800, ADBE 265768, CRM 29624264, NFLX 15124833, KO 8894,
     V 49462172.
   - In a fresh container, first fetch each one with `step_count=1000` (daily) into `state/external_bars/<SYM>.json`.
     These base files are not committed.
2. Run `.venv/bin/python scripts/quant_accounts.py forward`. It deterministically re-simulates all five accounts from
   the frozen start, through the latest bar, and rewrites `docs/quant-accounts/report.md` and `journal.jsonl`.
3. After the 2026-11-16 session, write `docs/quant-accounts/FINAL.md`.
   - Results against SPY and against the `backtest.md` one-month ranges.
   - Costs paid.
   - What each strategy did and why.
   - Honest caveats.
4. Commit `docs/quant-accounts/`.

## 9. Aggressive US$1,000 accounts (owner request 2026-10-16; rules frozen in `docs/aggressive-accounts/PREREG.md`)

Paper only. Review on 2027-04-15.

1. Nightly, also top up TQQQ (72539702, etf) as `state/external_bars/TQQQ.<YYYYMMDD>.json`. In a fresh container,
   first fetch `step_count=1000` into `TQQQ.json`. QQQ, SGOV and the stock universe are already in sections 2 and 8.
2. Run `.venv/bin/python scripts/aggressive_accounts.py forward` for Accounts A and B. It re-simulates
   deterministically from the frozen start and rewrites `docs/aggressive-accounts/report.md` and `journal.jsonl`.
3. Account C is Book M of the options test (section 6). `options_paper.py plan` now also lists a Book M entry with the
   momentum ranking (`underlyings_in_order`). For those stocks, fetch quotes in rank order and stop at the first one
   with an affordable call:
   - Resolve the stock's conid with `search_contracts`; `underlying_contract_id` is the one to use.
   - Call `get_option_parameters`, and pick the expiry with 60 to 120 days left, closest to 90, preferring the
     trading class equal to the symbol.
   - Call `get_option_data` with strikes from spot to spot × 1.25.
   - Take 4 to 6 `get_price_snapshot` quotes (`bid_ask`, `option_greeks`) around delta 0.35.
   - Add the quotes to the same `quotes/<session>.json`, with `underlying` set to the stock symbol.
   - Open Book M positions get a snapshot each night, like any open contract.
4. Commit `docs/aggressive-accounts/` (and `docs/options-paper/`).

## 10. Gap-and-go day-trading account, US$5,000 (owner request 2026-10-16; rules in `docs/gapgo-paper/PREREG.md`)

Paper only. The forward record starts with the 2026-10-16 session.

1. Nightly, top up daily bars (`step_count=10`) for the 72 added names in `docs/gapgo-paper/universe_added.json`
   (conids there) as `state/external_bars/<SYM>.<YYYYMMDD>.json` with `ingest_ibkr_bars.py --topup`.
   - The other universe names are already topped up in sections 2 and 8.
   - In a fresh container, first fetch `step_count=60` for each added name into `<SYM>.gapgo.json`.
   - Save each payload verbatim: never rebuild or infer the `time` array.
2. Run `.venv/bin/python scripts/gapgo_paper.py scan`. It lists the session's gap-ups of +4% or more.
3. For each candidate, fetch 5-minute bars:
   `get_price_history(conid, STK, FIVE_MINS, outside_rth=false, period=ONE_DAY)`, saved verbatim as
   `docs/gapgo-paper/bars/<SYM>.<YYYYMMDD>.json`.
4. Run `.venv/bin/python scripts/gapgo_paper.py step`, then commit `docs/gapgo-paper/`.

## 11. Portfolio Pilot: weekly report on the owner's real account (Fridays; D25, `docs/pilot/`)

Advisory only. IBKR is read-only: `get_account_summary`, `get_account_positions` and `get_price_history`. Never any
order tool. Nothing from this section is committed: the account file and the report live in `state/pilot/`, which is
git-ignored.

1. Save the verbatim tool outputs as
   `state/pilot/account-YYYYMMDD.json = {"asof": "YYYY-MM-DD", "summary": <summary>, "positions": <positions>}`.
2. Top up the daily bars of the 14 TSX tickers into `state/pilot_bars/<SYM>.D.<YYYYMMDD>.json`, using
   `step_count=10` and verbatim output.
   - Tickers (TSE): VFV 116763367, XUU 183908251, HXQ 700079680, XEF 125765017, ZEB 288989354, ZLB 96847744,
     ZUT 73281854, HBB 700079731, XGD 74580664, CGL 105224182, CCO 13464841, HURA 700079737, COPP 700079802,
     CHPS 765560340.
   - In a fresh container, first fetch `step_count=1000` into `<SYM>.D.json`.
3. Run `.venv/bin/python scripts/pilot_report.py state/pilot/account-YYYYMMDD.json`. The default policy is static, 1.45x.
4. Put a short summary in the Friday message to the owner: leverage, cushion, stress loss, alerts, and whether a
   rebalance is needed with its main trades.
