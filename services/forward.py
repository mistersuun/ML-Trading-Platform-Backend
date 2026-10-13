"""Forward paper test runner: one nightly pipeline run per session on external (IBKR-fed) bars and the simulated
paper broker, plus the journal (docs/forward-test/<month>/).

``step()`` advances the sim broker to the latest session in the bar files, runs ``scheduler.run_nightly`` with
paper trading switched on for the SIM broker only (a process-local override, recorded in the journal), then writes
one journal line and one day file. ``report()`` summarises every day. The journal holds simulated data only.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

import config
import external_bars
from data.calendars import get_calendar

logger = logging.getLogger(__name__)

REF_SYMBOL = "SPY"
SHADOW_HORIZONS = (1, 5)
_REAL_DECISIONS = ("submitted",)


# ------------------------------------------------------------------ paths

def journal_dir() -> Path:
    return Path(config.FORWARD_JOURNAL_DIR)


def journal_path() -> Path:
    return journal_dir() / "journal.jsonl"


def read_journal() -> list[dict]:
    p = journal_path()
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _append(entry: dict) -> None:
    journal_dir().mkdir(parents=True, exist_ok=True)
    with open(journal_path(), "a") as fh:
        fh.write(json.dumps(entry, default=str, sort_keys=True) + "\n")


# ------------------------------------------------------------------ small helpers

class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.items: list[dict] = []
        self.missing: list[str] = []
        self.required = set(config.WATCHLIST["stocks"])

    def result(self) -> list[dict]:
        out = list(self.items)
        if self.missing:
            m = sorted(set(self.missing))
            out.append({"level": "INFO", "msg": f"{len(m)} watchlist symbols have no external bar files "
                                                f"(optional, research-only): {', '.join(m)}"})
        return out

    def emit(self, record):
        raw = record.getMessage()
        if raw.startswith("No usable data for "):          # optional symbols (crypto/fx/futures) with no bar files
            sym = raw.split(":")[0].replace("No usable data for ", "").strip()
            if sym not in self.required:                   # a REQUIRED symbol keeps its warning (and degrades the step)
                self.missing.append(sym)
                return
        msg = f"{record.name}: {raw}"[:300]
        if not any(i["msg"] == msg for i in self.items) and len(self.items) < 60:
            self.items.append({"level": record.levelname, "msg": msg})


def _watchlist_symbols() -> list[str]:
    syms = list(config.WATCHLIST["stocks"])
    d = external_bars.bars_dir()
    if d.exists():                       # optional extra asset classes the orchestrator chose to fetch
        extra = {p.name.split(".")[0] for p in d.glob("*.json")}
        syms += [s for s in sorted(extra) if s not in syms and any(s in v for v in config.WATCHLIST.values())]
    return syms


def _closed(sym: str, now: datetime) -> pd.DataFrame:
    """Finished XNYS sessions only (see external_bars.closed_bars); everything in this module reads bars through it."""
    return external_bars.closed_bars(sym, now, get_calendar("XNYS"))


def latest_session(now: Optional[datetime] = None) -> Optional[pd.Timestamp]:
    df = _closed(REF_SYMBOL, now or datetime.now(timezone.utc))
    return None if df.empty else df.index[-1]


def freshness(now: datetime) -> list[dict]:
    cal = get_calendar("XNYS")
    last_closed = cal.last_closed_session(now)
    rows = []
    for sym in _watchlist_symbols():
        try:
            df = _closed(sym, now)
        except external_bars.ExternalBarsError:
            rows.append({"symbol": sym, "bars": 0, "last_bar": None, "sessions_behind": None, "status": "missing"})
            continue
        if df.empty:
            rows.append({"symbol": sym, "bars": 0, "last_bar": None, "sessions_behind": None, "status": "missing"})
            continue
        last = df.index[-1]
        behind = len(cal.sessions(last, last_closed)) - 1 if last_closed is not None and last < last_closed else 0
        rows.append({"symbol": sym, "bars": len(df), "last_bar": last.date().isoformat(), "sessions_behind": behind,
                     "status": "ok" if behind == 0 else "stale"})
    return rows


def _close_on_or_before(sym: str, day: str, now: Optional[datetime] = None) -> Optional[float]:
    df = _closed(sym, now or datetime.now(timezone.utc))
    df = df[df.index <= pd.Timestamp(day)]
    return None if df.empty else float(df["Close"].iloc[-1])


def _close_on(sym: str, day: str, now: Optional[datetime] = None) -> Optional[float]:
    """Close of exactly `day` (None when the symbol's last closed bar is not that day: no stale entry prices)."""
    df = _closed(sym, now or datetime.now(timezone.utc))
    ts = pd.Timestamp(day)
    return float(df.loc[ts, "Close"]) if ts in df.index else None


# ------------------------------------------------------------------ shadow tracking

def _direction(txt) -> int:
    return -1 if "SELL" in str(txt).upper() else 1


def raw_pattern_signals() -> list[dict]:
    """Raw technical pattern signals on the LAST bar of each watchlist stock, with no validation applied. The
    validated scan reports nothing when no candidate clears the gates; these keep the shadow ledger meaningful."""
    import data_fetcher
    import signals
    from patterns import PATTERN_REGISTRY
    out = []
    for sym in config.WATCHLIST["stocks"]:
        df = data_fetcher.fetch_ohlcv(sym)
        if df.empty:
            continue
        for name, fn in PATTERN_REGISTRY.items():
            try:
                direction, bar_date = signals.latest_signal(fn(df), max_staleness_bars=0)
            except Exception as e:
                logger.warning("raw pattern %s on %s failed: %s", name, sym, type(e).__name__)
                continue
            if direction != 0:
                out.append({"symbol": sym, "pattern": name, "direction": direction, "bar_date": bar_date})
    return out


def shadow_items(day: str, results: dict, decisions: list[dict], now: Optional[datetime] = None) -> list[dict]:
    """Every alert-only or non-eligible signal of the night, with the entry price (close of `day`)."""
    placed = {d["symbol"] for d in decisions if d["status"] in _REAL_DECISIONS}
    eligible = set(config.ORDER_ELIGIBLE_STATUSES)
    out: list[dict] = []
    seen: set = set()

    def add(kind, symbol, pattern, direction, status, reason, bar_date):
        key = f"{day}:{kind}:{symbol}:{pattern}:{direction}"
        if key in seen:
            return
        seen.add(key)
        out.append({"id": key, "date": day, "kind": kind, "symbol": symbol, "pattern": pattern, "direction": direction,
                    "validation_status": status, "reason": reason, "signal_bar_date": bar_date,
                    "entry_price": None if "/" in symbol else _close_on(symbol, day, now)})

    for r in results.get("technical", []) or []:
        sym, st = r.get("symbol"), r.get("validation_status")
        if sym in placed and st in eligible:
            continue
        reason = "alert_only:not_eligible" if st not in eligible else "alert_only:not_orderable_or_blocked"
        add("technical", sym, r.get("pattern"), _direction(r.get("direction")), st, reason, r.get("signal_bar_date"))
    for r in results.get("ml", []) or []:
        sym, st = r.get("symbol"), r.get("validation_status")
        if sym in placed and st in eligible:
            continue
        add("ml", sym, "ml_ensemble", _direction(r.get("direction")), st, "alert_only:not_eligible", r.get("signal_bar_date"))
    for r in results.get("pairs", []) or []:
        add("pairs", f"{r.get('symbol_a')}/{r.get('symbol_b')}", "pairs", _direction(r.get("signal_direction")),
            "unvalidated", "alert_only:pairs_never_executed", None)
    for r in raw_pattern_signals():
        add("raw_pattern", r["symbol"], r["pattern"], r["direction"], "unvalidated", "alert_only:unvalidated_raw_signal",
            r["bar_date"])
    for d in decisions:                      # intents the gates stopped (risk, reconcile, duplicate, ...)
        if d["status"] not in _REAL_DECISIONS:
            add("intent", d["symbol"], "intent", d["direction"], "n/a", f"decision:{d['status']}:{','.join(d['reasons'])[:80]}", day)
    return out


def score_shadow(journal: list[dict], today: str, now: Optional[datetime] = None) -> list[dict]:
    """Score each unscored shadow item for every horizon that now has enough later bars. Pure; returns score rows.

    Two bases are recorded: ``fwd_return`` from the close of the signal day (the entry price of the item) and
    ``fwd_return_open`` from the OPEN of the next session, which is what an order placed after the close can actually
    get. ``signed_return`` (what the report aggregates) uses the executable open-based entry. Items are
    deduplicated by id (a re-run journals the same ids again) and each (id, horizon) is scored once."""
    now = now or datetime.now(timezone.utc)
    items: dict = {}
    for e in journal:
        if e.get("type") == "day":
            for i in e.get("shadow_new", []):
                items[i["id"]] = i                      # last journaled row of an id wins
    done = {(e["id"], e["horizon"]) for e in journal if e.get("type") == "shadow_score"}
    rows = []
    for it in items.values():
        if not it.get("entry_price") or "/" in it["symbol"]:
            continue
        df = _closed(it["symbol"], now)
        later = df[df.index > pd.Timestamp(it["date"])]
        for h in SHADOW_HORIZONS:
            if (it["id"], h) in done or len(later) < h:
                continue
            done.add((it["id"], h))
            px = float(later["Close"].iloc[h - 1])
            raw = px / it["entry_price"] - 1
            open_px = float(later["Open"].iloc[0])
            raw_open = px / open_px - 1
            rows.append({"type": "shadow_score", "id": it["id"], "horizon": h, "scored_on": today,
                         "symbol": it["symbol"], "kind": it["kind"], "direction": it["direction"],
                         "reason": it["reason"], "entry_price": it["entry_price"], "entry_open": open_px,
                         "exit_price": px, "exit_date": later.index[h - 1].date().isoformat(),
                         "fwd_return": round(raw, 6), "fwd_return_open": round(raw_open, 6),
                         "signed_return_close": round(raw * it["direction"], 6),
                         "signed_return": round(raw_open * it["direction"], 6)})
    return rows


# ------------------------------------------------------------------ snapshot (state survives fresh containers)

def _state_files() -> list:
    """(snapshot name, live path, is_sqlite) of everything that must survive a fresh container: the sim account,
    the risk/order state DB, the trials registry (n_trials for DSR/PBO) and the holdout-read ledger."""
    from services import trial_runs
    from state import holdout
    return [("sim_broker.json", Path(config.SIM_BROKER_PATH), False),
            ("trading.db", Path(config.STATE_DB_PATH), True),
            ("trials.sqlite", Path(trial_runs.trials_db_path()), True),
            ("holdout_reads.sqlite", Path(holdout.default_path()), True)]


def snapshot() -> None:
    """Copy the sim broker file and consistent sqlite backups of the state DBs into the journal dir."""
    d = journal_dir() / "snapshot"
    d.mkdir(parents=True, exist_ok=True)
    for name, target, is_sql in _state_files():
        if not target.exists():
            continue
        if not is_sql:
            shutil.copy2(target, d / name)
            continue
        src = sqlite3.connect(target)
        dst = sqlite3.connect(d / name)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()


def restore(force: bool = False) -> list[str]:
    """Restore every snapshotted state file. All-or-nothing: refuses (RuntimeError) unless every target is missing or
    `force` is set, so a half-old / half-new state (e.g. new sim file with an old DB) cannot arise."""
    d = journal_dir() / "snapshot"
    todo = [(d / name, target, is_sql) for name, target, is_sql in _state_files() if (d / name).exists()]
    present = [str(t) for _, t, _ in todo if t.exists()]
    if present and not force:
        raise RuntimeError("refusing a partial restore, these targets already exist: " + ", ".join(present)
                           + " (use --force to overwrite all of them)")
    done = []
    for src, target, is_sql in todo:
        target.parent.mkdir(parents=True, exist_ok=True)
        if is_sql:
            for suffix in ("-wal", "-shm"):
                Path(str(target) + suffix).unlink(missing_ok=True)
        shutil.copy2(src, target)
        done.append(str(target))
    return done


# ------------------------------------------------------------------ plumbing test (NOT a strategy)

PLUMBING_LABEL = "PLUMBING TEST — not a strategy"
_PLACED = ("submitted", "pending", "unknown", "duplicate")   # ledger states in which an order may exist at the broker


def _plumbing_ids(conn, journal: list[dict]) -> dict:
    """client_order_ids of the (single) plumbing-test entry and close, from the signal ledger and the journal."""
    import execution
    ids = {"entry_cid": None, "entry_date": None, "close_cid": None, "close_date": None}
    for r in conn.execute("SELECT signal_key, side, bar_date, status FROM signal_ledger WHERE strategy_key=? "
                          "ORDER BY rowid", (execution.PLUMBING_STRATEGY_KEY,)):
        k = "entry" if r["side"] == "buy" else "close"
        if r["status"] in _PLACED and ids[f"{k}_cid"] is None:
            ids[f"{k}_cid"], ids[f"{k}_date"] = r["signal_key"], r["bar_date"]
    for e in journal:                                       # belt and braces: the journal outlives a restored DB
        d = e.get("decision") or {}
        if e.get("type") == "plumbing_test" and e.get("action") in ("entry", "close") and d.get("status") == "submitted":
            k = e["action"]
            if ids[f"{k}_cid"] is None:
                ids[f"{k}_cid"], ids[f"{k}_date"] = d.get("client_order_id"), e.get("date")
    return ids


def plumbing_status(sim, ids: dict) -> dict:
    """Where the plumbing trade stands in the sim: state none|entry_pending|entry_failed|open|closed, its fills, P&L."""
    import execution
    out = {**ids, "state": "none", "fills": [], "realized_pl": 0.0, "unrealized_pl": 0.0, "pl": 0.0, "position": None,
           "exit_reason": None, "stop": None, "take_profit": None}
    E, C = ids.get("entry_cid"), ids.get("close_cid")
    if not E:
        return out
    sim._sync()
    orders = {o["client_order_id"]: o for o in sim.s["orders"]}
    entry = orders.get(E)
    mine = [f for f in sim.s["fills"] if f["client_order_id"] == E or f["client_order_id"].startswith(E + "-")
            or (C and f["client_order_id"] == C)]
    out["fills"] = mine
    if entry is None:
        out["state"] = "entry_missing"
        return out
    for lid in entry.get("leg_ids", []):
        leg = sim._find(lid)
        if leg and leg["type"] == "stop":
            out["stop"] = leg["stop_price"]
        elif leg and leg["type"] == "limit":
            out["take_profit"] = leg["limit_price"]
    if entry["status"] in ("accepted", "new"):
        out["state"] = "entry_pending"
    elif entry["status"] != "filled":
        out["state"] = "entry_failed"
    else:
        # the plumbing position is worked out from its OWN fills, never from "any SPY position": a strategy SPY
        # position opened after the plumbing exit must not be mistaken for it
        bought = [f for f in mine if f["side"] == "buy"]
        sold = [f for f in mine if f["side"] == "sell"]
        net = round(sum(f["qty"] for f in bought) - sum(f["qty"] for f in sold), 6)
        pos = sim.s["positions"].get(execution.PLUMBING_SYMBOL)
        out["state"] = "open" if net > 0 else "closed"
        if net > 0:
            bq = sum(f["qty"] for f in bought)
            avg = sum(f["qty"] * f["price"] for f in bought) / bq if bq else 0.0
            last = pos["last_price"] if pos else avg
            out["position"] = {"qty": net, "avg_entry": avg, "last": last}
            out["unrealized_pl"] = round(net * (last - avg), 2)
        exits = sold
        if exits:
            out["exit_reason"] = {"stop": "stop_loss", "limit": "take_profit", "market": "closed_at_open"}.get(exits[-1]["type"], exits[-1]["type"])
    out["realized_pl"] = round(sum(f["realized_pl"] for f in mine), 2)
    out["pl"] = round(out["realized_pl"] + out["unrealized_pl"], 2)
    return out


def _plumbing_night(day: str, now: datetime, broker, sim, journal: list[dict], request: Optional[str],
                    errors: list[str], nightly_block: Optional[str] = None) -> tuple[dict, list[dict]]:
    """Run tonight's plumbing-test action (request: "entry" | "close" | None) through execution.submit_intent and
    return (summary for the day entry, journal lines). Never raises: a problem is a journaled outcome, and a failure
    of the status/ids work is reported as state "unknown" (a reporting feature must not stop the forward test).
    `nightly_block` (e.g. "nightly_not_ok:error") skips the ENTRY only, so the wiring check never runs on a broken night."""
    import execution
    from risk_manager import RiskManager
    from state import db as state_db
    lines: list[dict] = []

    def line(action: str, **kw) -> None:
        lines.append({"type": "plumbing_test", "label": PLUMBING_LABEL, "date": day, "run_at": now.isoformat(),
                      "action": action, **kw})

    conn = None
    ids: dict = {}
    try:
        conn = state_db.require_initialized()
        ids = _plumbing_ids(conn, journal)
        before = plumbing_status(sim, ids)
        if request:
            line_kw: dict = {}
            try:
                price = _close_on(REF_SYMBOL, day, now)
                if request == "entry" and ids["entry_cid"]:
                    line_kw = {"outcome": "skipped", "reason": f"already_placed:{ids['entry_cid']}"}
                elif request == "entry" and nightly_block:
                    line_kw = {"outcome": "skipped", "reason": nightly_block}
                elif request == "close" and not ids["entry_cid"]:
                    line_kw = {"outcome": "skipped", "reason": "no_plumbing_entry_was_ever_placed"}
                elif request == "close" and ids["close_cid"]:
                    line_kw = {"outcome": "skipped", "reason": f"close_already_placed:{ids['close_cid']}"}
                elif request == "close" and before["state"] != "open":
                    line_kw = {"outcome": "skipped", "reason": f"no_open_plumbing_position (state={before['state']})"}
                elif request == "close" and ids["entry_date"] and day <= ids["entry_date"]:
                    line_kw = {"outcome": "skipped", "reason": "close_only_on_a_later_night"}
                elif price is None:
                    line_kw = {"outcome": "skipped", "reason": f"no_{REF_SYMBOL}_close_for_{day}"}
                else:
                    intent = execution.build_plumbing_intent(broker, 1 if request == "entry" else -1, day, price)
                    rm = RiskManager(conn)
                    execution.refresh_sleeve_equity(rm, broker, now=now)
                    d = execution.submit_intent(intent, broker=broker, conn=conn, now=now, risk_manager=rm)
                    line_kw = {"outcome": d.status, "intent_price": price, "decision": asdict(d)}
                    if d.status not in ("submitted", "rejected", "duplicate", "halted"):
                        errors.append(f"plumbing test {request}: {d.status} {d.reasons}")
            except Exception as e:
                logger.exception("plumbing test failed")
                errors.append(f"plumbing test {request}: {type(e).__name__}: {e}")
                line_kw = {"outcome": "error", "reason": f"{type(e).__name__}: {e}"}
            line(request, **line_kw)
            ids = _plumbing_ids(conn, journal + lines)
        status = plumbing_status(sim, ids)
        done_before = any(e.get("type") == "plumbing_test" and e.get("state") == "closed" for e in journal)
        if status["state"] != "none" and not request and not done_before:
            line("status", state=status["state"], pl=status["pl"], fills=status["fills"], position=status["position"])
        if lines and lines[-1]["action"] in ("entry", "close"):
            lines[-1]["state"] = status["state"]
    except Exception as e:
        logger.exception("plumbing test status failed")
        errors.append(f"plumbing test status: {type(e).__name__}: {e}")
        status = {**ids, "state": "unknown", "fills": [], "realized_pl": 0.0, "unrealized_pl": 0.0, "pl": 0.0,
                  "position": None, "exit_reason": None, "stop": None, "take_profit": None}
    finally:
        if conn is not None:
            conn.close()
    summary = {**status, "requested": request, "label": PLUMBING_LABEL,
               "lines": [{k: v for k, v in ln.items() if k not in ("type", "label", "run_at")} for ln in lines]}
    return summary, lines


# ------------------------------------------------------------------ the step

def _fmt_money(x) -> str:
    return "n/a" if x is None else f"{x:,.2f}"


def _replace_days(date: str) -> None:
    """Drop the journaled 'day' rows of `date` (a --rerun replaces that day instead of adding a second row)."""
    p = journal_path()
    if not p.exists():
        return
    keep = [ln for ln in p.read_text().splitlines()
            if ln.strip() and not (lambda r: r.get("type") == "day" and r.get("date") == date)(json.loads(ln))]
    p.write_text("".join(ln + "\n" for ln in keep))


def step(date: Optional[str] = None, modes=("technical", "pairs", "ml"), run_stress: bool = True,
         rerun: bool = False, now: Optional[datetime] = None, plumbing_test: bool = False,
         plumbing_close: bool = False) -> dict:
    """One forward-test night. Returns the journal entry (also written to disk).

    Paper trading is switched on here, for this call only (config.enable_forward_test leaves it off so no other
    command can place sim orders). A session already journaled is refused unless `rerun` (which replaces that day's
    row). Only finished sessions are ever used (see external_bars.closed_bars).

    `plumbing_test` places the ONE labelled plumbing-test trade tonight (idempotent, see `_plumbing_night`);
    `plumbing_close` closes it at the next open on a later night. Neither is a strategy."""
    if plumbing_test and plumbing_close:
        raise ValueError("--plumbing-test and --plumbing-close are mutually exclusive (place it, then close it on a later night)")
    config.enable_forward_test()
    saved_mode = (config.TRADING_MODE, config.PAPER_TRADE_ENABLED)
    config.TRADING_MODE, config.PAPER_TRADE_ENABLED = "paper", True
    try:
        return _step(date, modes, run_stress, rerun, now, "entry" if plumbing_test else "close" if plumbing_close else None)
    finally:
        config.TRADING_MODE, config.PAPER_TRADE_ENABLED = saved_mode


def _step(date, modes, run_stress, rerun_ok, now, plumbing_request=None) -> dict:
    import execution
    import scheduler
    from risk_manager import RiskManager
    from services import scan
    from state import db as state_db

    t_start = time.perf_counter()
    now = now or datetime.now(timezone.utc)
    cap = _Capture()
    logging.getLogger().addHandler(cap)
    errors: list[str] = []
    plumbing_lines: list[dict] = []
    try:
        latest = latest_session(now)
        if latest is None:
            raise RuntimeError(f"no finished {REF_SYMBOL} session in {config.EXTERNAL_BARS_DIR}; ingest bars first (RUNBOOK step 2)")
        day = latest.date().isoformat()
        last_closed = get_calendar("XNYS").last_closed_session(now)
        if last_closed is None or latest > last_closed:
            raise RuntimeError(f"session {day} has not closed yet; run after the close (about 17:30 ET)")
        if date and date != day:
            raise RuntimeError(f"--date {date} but the latest session in the bar files is {day}"
                               + (" (fetch newer bars)" if day < date else " (cannot rewind: bars already extend past it)"))
        try:
            conn = state_db.require_initialized()
            conn.close()
        except state_db.StateNotInitialized as e:
            raise RuntimeError(f"{e}. Run `FORWARD_TEST=1 python main.py risk init` first") from e

        journal = read_journal()
        prior_days = [e for e in journal if e.get("type") == "day"]
        rerun = any(e["date"] == day for e in prior_days)
        if rerun and not rerun_ok:
            raise RuntimeError(f"session {day} is already journaled; no new session yet. Pass --rerun to replace that day")
        if rerun:
            prior_days = [e for e in prior_days if e["date"] != day]

        broker = execution.default_broker()      # AlpacaBroker(SimBroker) under FORWARD_TEST (PAPER_BROKER=sim)
        sim = broker.client
        if not hasattr(sim, "advance_to"):
            raise RuntimeError("forward test needs the simulated broker (PAPER_BROKER=sim)")
        first_ever = sim.last_date is None
        t0 = time.perf_counter()
        adv = sim.advance_to(day)
        t_adv = time.perf_counter() - t0

        captured: dict = {}

        def scan_fn(**kw):
            kw["paper_trade"] = True      # run_nightly is alert-only by contract; the forward test routes intents to the sim
            captured["results"] = scan.run_full_scan(**kw)
            return captured["results"]

        results_root = Path(config.STATE_DB_PATH).parent / "results"
        t0 = time.perf_counter()
        res = scheduler.run_nightly(modes=tuple(modes), run_stress=run_stress, scan_fn=scan_fn, results_root=results_root)
        t_night = time.perf_counter() - t0
        results = captured.get("results") or {}
        summary = res.get("summary", {}) or {}
        if res.get("status") != "ok":
            errors.append(f"nightly status={res.get('status')}: {summary.get('error') or res.get('message')}")

        decisions = results.get("decisions", []) or []
        # the plumbing test is submitted AFTER the nightly run, through the same execution.submit_intent, and its
        # decision is kept out of `decisions` (shadow items, intent counts and every strategy statistic)
        nightly_block = None
        if res.get("status") != "ok":
            nightly_block = f"nightly_not_ok:{res.get('status')}"
        elif any(r["symbol"] in config.WATCHLIST["stocks"] and r["status"] in ("missing", "stale") for r in freshness(now)):
            nightly_block = "nightly_not_ok:degraded"
        plumbing, plumbing_lines = _plumbing_night(day, now, broker, sim, journal, plumbing_request, errors, nightly_block)
        conn = state_db.require_initialized()
        try:
            order_rows = [dict(r) for r in conn.execute(
                "SELECT client_order_id, symbol, side, qty, stop_price, limit_price, status FROM orders WHERE created_at >= ?",
                (now.isoformat(),))]
            by_sym = {r["symbol"]: r for r in order_rows}
            for d in decisions:
                o = by_sym.get(d["symbol"])
                if o and d["status"] == "submitted":
                    d.update({"qty": o["qty"], "stop": o["stop_price"], "take_profit": o["limit_price"],
                              "client_order_id": o["client_order_id"]})
            rm = RiskManager(conn)
            acct = broker.get_account()
            rec = execution.reconcile(broker, conn)
            reconciliation = {"ok": rec.ok, "mismatches": list(rec.mismatches)}
            if rec.positions is not None:       # same equity refresh the trading session does, so check() has a reading
                execution.refresh_sleeve_equity(rm, broker, rec.positions)
            chk = rm.check()
            risk = {**{k: v for k, v in rm.status().items() if k != "initialized"},
                    "risk_multiplier": chk.risk_multiplier, "allowed": chk.allowed, "reasons": list(chk.reasons)}
        finally:
            conn.close()

        # account / performance
        sim._sync()
        hist = sim.s["equity_history"]
        eq = float(acct["equity"])
        peak = max([h["equity"] for h in hist] + [sim.s["start_cash"]])
        spy_now = _close_on_or_before(REF_SYMBOL, day, now)
        base_entry = prior_days[0] if prior_days else None
        spy_base = base_entry["spy"]["close"] if base_entry else spy_now
        base_eq = base_entry["account"]["start_equity"] if base_entry else (hist[0]["equity"] if hist else eq)
        positions = []
        for p in sim.get_all_positions():
            row = {"symbol": p.symbol, "qty": float(p.qty), "avg_entry": float(p.avg_entry_price),
                   "last": float(p.current_price), "unrealized_pl": float(p.unrealized_pl), "plumbing_test": False}
            pp = plumbing.get("position") if p.symbol == execution.PLUMBING_SYMBOL else None
            if pp and plumbing["state"] == "open":
                pq = min(float(pp["qty"]), row["qty"])
                if pq < row["qty"]:        # a strategy SPY position next to the plumbing one: report them separately
                    positions.append({**row, "qty": row["qty"] - pq, "unrealized_pl": round(row["unrealized_pl"] - plumbing["unrealized_pl"], 2)})
                    row = {**row, "qty": pq, "unrealized_pl": plumbing["unrealized_pl"]}
                row["plumbing_test"] = True
            positions.append(row)
        open_orders = [{"id": o.id, "symbol": o.symbol, "side": o.side, "type": o.order_type, "status": o.status,
                        "qty": float(o.qty), "stop": o.stop_price, "limit": o.limit_price,
                        "legs": [{"type": l.order_type, "status": l.status, "stop": l.stop_price, "limit": l.limit_price}
                                 for l in (o.legs or [])]} for o in sim.get_orders()]

        shadow = shadow_items(day, results, decisions, now)
        scores = score_shadow([e for e in journal if not (e.get("type") == "day" and e.get("date") == day)], day, now)

        funnel = summary.get("funnel") or {}
        fresh = freshness(now)
        degraded = [r for r in fresh if r["symbol"] in config.WATCHLIST["stocks"] and r["status"] in ("missing", "stale")]
        if degraded:
            errors.append("required symbols missing or stale: " + ", ".join(f"{r['symbol']}={r['status']}" for r in degraded))
        entry = {
            "type": "day", "date": day, "run_at": now.isoformat(), "rerun": rerun, "first_day": not prior_days,
            "mode": {"trading_mode": config.TRADING_MODE, "paper_trade_enabled": bool(config.PAPER_TRADE_ENABLED),
                     "paper_broker": config.PAPER_BROKER, "data_source": list(config.DATA_SOURCE_PRIORITY),
                     "note": "paper trading is OFF by default in this app; the forward runner switches it ON for this "
                             "process only, and only for the simulated broker (execution.default_broker refuses "
                             "anything else under FORWARD_TEST). No Alpaca or IBKR order path is reachable."},
            "status": ("degraded" if degraded and res.get("status") == "ok" else res.get("status")),
            "run_id": res.get("run_id"),
            "timings_s": {"advance_sim": round(t_adv, 2), "nightly": round(t_night, 2),
                          "total": round(time.perf_counter() - t_start, 2)},
            "freshness": fresh,
            "funnel": funnel,
            "counts": {"technical_rows": len(results.get("technical", []) or []),
                       "technical_candidates": len(results.get("technical_candidates", []) or []),
                       "ml_rows": len(results.get("ml", []) or []), "pairs_rows": len(results.get("pairs", []) or []),
                       "intents_decided": len(decisions),
                       "decisions_by_status": {s: sum(1 for d in decisions if d["status"] == s)
                                               for s in sorted({d["status"] for d in decisions})}},
            "decisions": decisions,
            "sim_advance": {"sessions": adv["sessions"], "fills": adv["fills"]},
            "positions": positions, "open_orders": open_orders,
            "account": {"equity": round(eq, 2), "cash": float(acct["cash"]), "start_cash": sim.s["start_cash"],
                        "start_equity": round(base_eq, 2), "peak_equity": round(peak, 2),
                        "drawdown_pct": round((eq / peak - 1) * 100, 3) if peak else 0.0,
                        "return_pct": round((eq / base_eq - 1) * 100, 3) if base_eq else 0.0,
                        "plumbing_pl": plumbing["pl"], "equity_ex_plumbing": round(eq - plumbing["pl"], 2),
                        "return_pct_ex_plumbing": round(((eq - plumbing["pl"]) / base_eq - 1) * 100, 3) if base_eq else 0.0},
            "spy": {"close": spy_now, "base_close": spy_base,
                    "return_pct": round((spy_now / spy_base - 1) * 100, 3) if spy_now and spy_base else None},
            "risk": risk, "reconciliation": reconciliation,
            "warnings": cap.result(), "errors": errors,
            "shadow_new": shadow, "shadow_scored": scores, "plumbing_test": plumbing,
            "summary_keys": {k: v for k, v in summary.items() if k in ("technical", "pairs", "ml", "briefing", "backup")},
        }
        if first_ever:
            entry["note"] = "first step: the sim broker was created and positioned at this session (no history to fill)"
    except Exception as e:                                   # the journal records a crash too (type "crash")
        logger.exception("forward step failed")
        errors.append(f"{type(e).__name__}: {e}")
        entry = {"type": "crash", "date": date or now.date().isoformat(), "run_at": now.isoformat(),
                 "status": "crash", "errors": errors, "warnings": cap.result(),
                 "timings_s": {"total": round(time.perf_counter() - t_start, 2)}, "crashed": True}
    finally:
        logging.getLogger().removeHandler(cap)

    if entry.get("crashed"):
        _append(entry)
        for ln in plumbing_lines:           # an order may already be placed: never lose its journal line
            _append(ln)
        return entry
    try:                                        # before the journal line, so a failure is recorded in it
        snapshot()
    except Exception as e:  # pragma: no cover
        entry["errors"].append(f"snapshot failed: {type(e).__name__}: {e}")
    if entry["rerun"]:
        _replace_days(entry["date"])
    _append(entry)
    for ln in plumbing_lines:
        _append(ln)
    for sc in entry["shadow_scored"]:
        _append(sc)
    write_day_file(entry)
    return entry


# ------------------------------------------------------------------ markdown

def _plumbing_md(pt: dict, e: dict) -> list[str]:
    """Day-file / report lines for the plumbing test. It is a wiring check, never evidence for any strategy."""
    out = ["One SPY long placed only to exercise the order path (next-open fill, bracket legs, risk checks, sizing, "
           "reconciliation, marking, exit). It is excluded from the strategy and shadow statistics.", ""]
    out.append(f"- State: **{pt['state']}**" + (f", exit: {pt['exit_reason']}" if pt.get("exit_reason") else "")
               + f", entry order {pt.get('entry_cid')}, close order {pt.get('close_cid')}")
    if pt.get("stop") or pt.get("take_profit"):
        out.append(f"- Bracket: stop {pt.get('stop')}, take-profit {pt.get('take_profit')} (GTC)")
    if pt.get("position"):
        p = pt["position"]
        out.append(f"- Position: {p['qty']:g} SPY @ {p['avg_entry']:.2f}, last {p['last']:.2f}, unrealized {pt['unrealized_pl']:.2f}")
    out.append(f"- P&L: realized {pt['realized_pl']:.2f}, unrealized {pt['unrealized_pl']:.2f}, total {pt['pl']:.2f}")
    for f in pt.get("fills", []):
        out.append(f"- Fill {f['date']}: {f['side']} {f['qty']:g} @ {f['price']} ({f['type']}{', exit leg' if f['leg'] else ''})")
    for ln in pt.get("lines", []):
        out.append(f"- Tonight [{ln['action']}]: {ln.get('outcome', ln.get('state', ''))} "
                   f"{ln.get('reason') or (ln.get('decision') or {}).get('reasons') or ''}"
                   + (f" qty={(ln.get('decision') or {}).get('qty')} stop={(ln.get('decision') or {}).get('stop_price')} "
                      f"tp={(ln.get('decision') or {}).get('limit_price')}" if ln.get("decision") else ""))
    out.append(f"- Reconciliation tonight: {'OK' if e['reconciliation']['ok'] else 'MISMATCH ' + str(e['reconciliation']['mismatches'])}")
    return out


def write_day_file(e: dict) -> Path:
    d = journal_dir() / "days"
    d.mkdir(parents=True, exist_ok=True)
    a, spy, f, risk = e["account"], e["spy"], e["funnel"], e["risk"]
    L = [f"# Forward test day {e['date']}", "",
         f"- Run status: **{e['status']}** (run {e.get('run_id')}), rerun={e['rerun']}, at {e['run_at']}",
         f"- Timings: advance {e['timings_s']['advance_sim']}s, nightly {e['timings_s']['nightly']}s, total {e['timings_s']['total']}s",
         f"- Mode: TRADING_MODE={e['mode']['trading_mode']}, PAPER_TRADE_ENABLED={e['mode']['paper_trade_enabled']}, "
         f"broker={e['mode']['paper_broker']}, data={e['mode']['data_source']}",
         f"- Note: {e['mode']['note']}", ""]
    L += ["## Account (simulated, fictional dollars)", "",
          f"- Equity {_fmt_money(a['equity'])}, cash {_fmt_money(a['cash'])}, peak {_fmt_money(a['peak_equity'])}, "
          f"drawdown vs peak {a['drawdown_pct']}%, return since start {a['return_pct']}%",
          f"- SPY close {spy['close']}, SPY return over the same period {spy['return_pct']}%"]
    pt = e.get("plumbing_test") or {"state": "none"}
    if pt["state"] != "none":
        L += [f"- Includes the {PLUMBING_LABEL} position/orders (state {pt['state']}): its P&L is {_fmt_money(a.get('plumbing_pl'))}; "
              f"equity excluding it {_fmt_money(a.get('equity_ex_plumbing'))}, return excluding it {a.get('return_pct_ex_plumbing')}%"]
    L.append("")
    L += ["## Risk state", "",
          f"- halted={risk.get('halted')} ({risk.get('halt_reason')}), risk multiplier {risk.get('risk_multiplier')} "
          f"(ladder), allowed={risk.get('allowed')}, reasons={risk.get('reasons')}, drawdown={risk.get('drawdown')}, "
          f"orders_today={risk.get('orders_today')}, kill_switch={risk.get('kill_switch')}",
          f"- Reconciliation: {'OK' if e['reconciliation']['ok'] else 'MISMATCH ' + str(e['reconciliation']['mismatches'])}", ""]
    L += ["## Data freshness", "", "| symbol | bars | last bar | sessions behind | status |", "|---|---|---|---|---|"]
    L += [f"| {r['symbol']} | {r['bars']} | {r['last_bar']} | {r['sessions_behind']} | {r['status']} |" for r in e["freshness"]]
    L += ["", "## Funnel", "", f"- Technical rows reported {e['counts']['technical_rows']}, typed candidates "
          f"{e['counts']['technical_candidates']}, ML rows {e['counts']['ml_rows']}, pairs rows {e['counts']['pairs_rows']}",
          "- Validation funnel (candidates -> gates -> eligible): " + (", ".join(f"{k}={v}" for k, v in f.items()
                                                                       if k not in ("run_id", "sharpe_var")) or "n/a"),
          f"- Intents decided {e['counts']['intents_decided']}: {e['counts']['decisions_by_status']}", ""]
    L += ["## Orders / intents", ""]
    L += [f"- {d['symbol']} {'BUY' if d['direction'] == 1 else 'SELL'}: **{d['status']}** {d.get('reasons') or ''} "
          f"qty={d.get('qty')} stop={d.get('stop')} tp={d.get('take_profit')}" for d in e["decisions"]] or ["- none"]
    L += ["", "## Fills (processed when advancing the sim to this session)", ""]
    L += [f"- {x['date']} {x['side']} {x['qty']:g} {x['symbol']} @ {x['price']} ({x['type']}{', exit leg' if x['leg'] else ''})"
          for x in e["sim_advance"]["fills"]] or ["- none"]
    L += ["", "## Positions", ""]
    L += [f"- {p['symbol']}: {p['qty']:g} @ {p['avg_entry']:.2f}, last {p['last']:.2f}, unrealized {p['unrealized_pl']:.2f}"
          + (f"  [{PLUMBING_LABEL}]" if p.get("plumbing_test") else "") for p in e["positions"]] or ["- flat"]
    L += ["", "## Open orders", ""]
    L += [f"- {o['symbol']} {o['side']} {o['qty']:g} {o['type']} [{o['status']}] legs={o['legs']}" for o in e["open_orders"]] or ["- none"]
    L += ["", f"## {PLUMBING_LABEL}", ""] + _plumbing_md(pt, e) if (pt["state"] != "none" or pt.get("requested")) else []
    L += ["", "## Shadow signals recorded tonight (alert-only / non-eligible)", ""]
    L += [f"- {s['kind']} {s['symbol']} {s['pattern']} {'BUY' if s['direction'] == 1 else 'SELL'} [{s['validation_status']}] "
          f"entry {s['entry_price']} - {s['reason']}" for s in e["shadow_new"]] or ["- none"]
    L += ["", "## Shadow signals scored tonight", ""]
    L += [f"- {s['symbol']} {s['kind']} h={s['horizon']}d: {s['fwd_return'] * 100:+.2f}% (signed {s['signed_return'] * 100:+.2f}%) "
          f"entry {s['entry_price']} -> {s['exit_price']} on {s['exit_date']}" for s in e["shadow_scored"]] or ["- none"]
    L += ["", "## Errors and warnings", ""]
    L += [f"- ERROR {x}" for x in e["errors"]] + [f"- {w['level']} {w['msg']}" for w in e["warnings"]] or ["- none"]
    p = d / f"{e['date']}.md"
    p.write_text("\n".join(L) + "\n")
    return p


# ------------------------------------------------------------------ report

def report() -> str:
    j = read_journal()
    by_date: dict = {}
    for e in j:
        if e.get("type") == "day":
            by_date[e["date"]] = e                     # one row per session: the last journaled run of a date wins
    days = [by_date[k] for k in sorted(by_date)]
    scores = {}
    for e in j:
        if e.get("type") == "shadow_score":
            scores[(e["id"], e["horizon"])] = e        # each (id, horizon) counted once
    scores = list(scores.values())
    crashes = [e for e in j if e.get("type") == "crash"]
    L = ["# Forward test report", "", f"Days journaled: {len(days)}", ""]
    if not days:
        L += ["No days yet. Run `python main.py forward step`."]
        L += ["", "## Crashes", ""] + ([f"- {c['date']} at {c['run_at']}: {'; '.join(c['errors'])}" for c in crashes] or ["- none"])
        return "\n".join(L) + "\n"
    L += ["| date | status | equity | return % | SPY % | DD % | cand | intents | orders | fills | pos | recon | warn/err |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in days:
        a = e["account"]
        pt = e.get("plumbing_test") or {}
        pfills = {(f["date"], f["client_order_id"]) for f in pt.get("fills", [])}
        n_fills = sum(1 for f in e["sim_advance"]["fills"] if (f["date"], f.get("client_order_id")) not in pfills)
        n_pos = sum(1 for p in e["positions"] if not p.get("plumbing_test"))
        L.append(f"| {e['date']} | {e['status']} | {a.get('equity_ex_plumbing', a['equity']):,.0f} | "
                 f"{a.get('return_pct_ex_plumbing', a['return_pct'])} | {e['spy']['return_pct']} | "
                 f"{a['drawdown_pct']} | {e['counts']['technical_candidates']} | {e['counts']['intents_decided']} | "
                 f"{e['counts']['decisions_by_status'].get('submitted', 0)} | {n_fills} | "
                 f"{n_pos} | {'ok' if e['reconciliation']['ok'] else 'MISMATCH'} | {len(e['warnings'])}/{len(e['errors'])} |")
    L += ["", f"Equity, return, fills and positions above EXCLUDE the {PLUMBING_LABEL} (see its own section below)."]
    last = days[-1]
    lp = last["account"]
    L += ["", f"Latest: equity {lp.get('equity_ex_plumbing', lp['equity']):,.2f} ({lp.get('return_pct_ex_plumbing', lp['return_pct'])}% vs SPY {last['spy']['return_pct']}%), "
          f"drawdown {last['account']['drawdown_pct']}%, risk multiplier {last['risk'].get('risk_multiplier')}, "
          f"halted={last['risk'].get('halted')}", "", "## Shadow signals (forward returns of alert-only / non-eligible signals)", ""]
    if not scores:
        L.append("No shadow signal has reached a scoring horizon yet.")
    else:
        L += ["| kind | reason | horizon | n | mean signed % | hit rate |", "|---|---|---|---|---|---|"]
        groups: dict = {}
        for s in scores:
            groups.setdefault((s["kind"], s["reason"].split(":")[0] + ":" + s["reason"].split(":")[1] if ":" in s["reason"] else s["reason"], s["horizon"]), []).append(s["signed_return"])
        for (k, r, h), v in sorted(groups.items()):
            L.append(f"| {k} | {r} | {h}d | {len(v)} | {sum(v) / len(v) * 100:+.2f} | {sum(1 for x in v if x > 0) / len(v):.0%} |")
    ids = {i["id"] for e in days for i in e.get("shadow_new", []) if i.get("entry_price") and "/" not in i["symbol"]}
    pending = len(ids) * len(SHADOW_HORIZONS) - len({k for k in ((s_["id"], s_["horizon"]) for s_ in scores) if k[0] in ids})
    L += ["", f"Shadow score slots still pending: {max(pending, 0)}", ""]
    pt_lines = [e for e in j if e.get("type") == "plumbing_test"]
    if pt_lines:
        lastpt = (days[-1].get("plumbing_test") or {})
        L += [f"## {PLUMBING_LABEL}", "",
              "A single SPY long to exercise the order path. Excluded from every table and statistic above and from the "
              "shadow study; its result says nothing about any strategy.", "",
              f"Latest state: {lastpt.get('state', 'n/a')}" + (f", exit {lastpt['exit_reason']}" if lastpt.get("exit_reason") else "")
              + f", P&L {lastpt.get('pl', 0):.2f}", ""]
        for ln in pt_lines:
            d = ln.get("decision") or {}
            L.append(f"- {ln['date']} [{ln['action']}] {ln.get('outcome', ln.get('state', ''))}"
                     + (f" qty={d.get('qty')} stop={d.get('stop_price')} tp={d.get('limit_price')}" if d else "")
                     + (f" {ln['reason']}" if ln.get("reason") else "") + (f" {d['reasons']}" if d.get("reasons") else ""))
        L.append("")
    errs = [(e["date"], x) for e in days for x in e.get("errors", [])]
    L += ["## Errors", ""] + ([f"- {d}: {x}" for d, x in errs] or ["- none"])
    L += ["", "## Crashes", ""] + ([f"- {c['date']} at {c['run_at']}: {'; '.join(c['errors'])}" for c in crashes] or ["- none"])
    text = "\n".join(L) + "\n"
    journal_dir().mkdir(parents=True, exist_ok=True)
    (journal_dir() / "REPORT.md").write_text(text)
    return text
