"""Nightly Claude briefing (D17): build the input from the run's results, call the model within a spend cap,
store the outcome as results/briefing/latest.json and serve it at GET /api/briefing.

Advisory only. This module (like claude_integration) never imports execution, brokers, paper_trader, signals or
risk_manager: the risk state is read straight from the state DB and nothing here can place or change an order.
Numbers only go to the model: no account numbers or IDs. Every failure ends as a stored status, never an exception.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import claude_integration as ci
import config
from account import store as account_store
from results import store
from services import models as M
from state import db as state_db

logger = logging.getLogger(__name__)

KIND = "briefing"
LEDGER_NAME = "llm_calls.jsonl"     # results/llm_calls.jsonl: one line per API call (gitignored with results/)
ADVISORY = "Advisory only: nothing here is an instruction to trade. Nothing is ordered."
MAX_TECHNICAL_ROWS = 30
_TECH_KEYS = ("symbol", "pattern", "signal", "signal_date", "days_ago", "validation_status", "n_oos_trades",
              "oos_psr", "bh_adjusted_p", "dsr_p", "pbo", "total_trades", "win_rate", "sharpe", "rejected_reasons")
_ledger_lock = threading.Lock()
_generate_lock = threading.Lock()   # check budget -> reserve -> call -> reconcile is one critical section per process
LOCK_NAME = "llm_calls.lock"        # flock sidecar: the nightly (scheduler) and the API are different processes


def _root(root) -> Path:
    return Path(root) if root is not None else store.RESULTS_DIR


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


# ---------------------------------------------------------------- ledger (JSONL, newest last)
# A call first writes a "reserved" row at its worst-case estimate; the real row then names it in `releases`, and a
# released reservation no longer counts. A crash mid-call leaves the reservation, so the cap errs on the safe side.
def record_call(model: str, status: str, usage: Optional[dict], cost_usd: float, estimated: bool,
                request_id: Optional[str] = None, root=None, now: Optional[datetime] = None,
                row_id: Optional[str] = None, releases: Optional[str] = None) -> bool:
    """Append one ledger row; False when it could not be written."""
    row = {"ts": _now(now).isoformat(), "model": model, "status": status, "cost_usd": cost_usd,
           "cost_estimated": estimated, "request_id": request_id, **(usage or {})}
    if row_id:
        row["id"] = row_id
    if releases:
        row["releases"] = releases
    p = _root(root) / LEDGER_NAME
    try:
        with _ledger_lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, allow_nan=False) + "\n")
        return True
    except Exception:
        logger.exception("llm_calls ledger write failed")
        return False


class _LedgerLock:
    """Exclusive cross-process lock around "read spend -> append the reservation" (same flock idea as
    scheduler._RunLock, but blocking: the section is short)."""
    def __init__(self, root=None):
        self.path = _root(root) / LOCK_NAME
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX)
        except OSError:
            self.fh.close()
            raise
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def _ledger_rows(root=None) -> list[dict]:
    try:
        text = (_root(root) / LEDGER_NAME).read_text(encoding="utf-8")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get("ts"), str):
            rows.append(r)
    return rows


def spend(root=None, now: Optional[datetime] = None) -> tuple[float, float]:
    """(USD spent today, USD spent this month), UTC calendar day / month, from the ledger."""
    now = _now(now)
    day, month = now.date().isoformat(), now.strftime("%Y-%m")
    today = mon = 0.0
    rows = _ledger_rows(root)
    released = {r["releases"] for r in rows if isinstance(r.get("releases"), str)}
    for r in rows:
        if r.get("status") == "reserved" and r.get("id") in released:
            continue
        c = r.get("cost_usd")
        if not isinstance(c, (int, float)) or isinstance(c, bool):
            continue
        if r["ts"].startswith(month):
            mon += c
            if r["ts"].startswith(day):
                today += c
    return round(today, 6), round(mon, 6)


def budget_block(root=None, now: Optional[datetime] = None) -> M.BriefingBudget:
    today, month = spend(root, now)
    return M.BriefingBudget(spent_today_usd=today, daily_limit_usd=config.LLM_DAILY_BUDGET_USD,
                            spent_month_usd=month, monthly_limit_usd=config.LLM_MONTHLY_BUDGET_USD)


# ---------------------------------------------------------------- input assembly (read-only)
def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _risk_context(conn) -> Optional[dict]:
    try:
        rs = conn.execute("SELECT halted, halt_reason, peak_equity FROM risk_state WHERE id=1").fetchone()
        eq = conn.execute("SELECT sleeve_equity FROM equity_history ORDER BY id DESC LIMIT 1").fetchone()
    except Exception:
        return None
    if rs is None:
        return None
    value = float(eq["sleeve_equity"]) if eq else None
    peak = rs["peak_equity"]
    dd = round(value / peak - 1, 4) if value is not None and peak and peak > 0 else None
    return {"halted": bool(rs["halted"]), "halt_reason": (rs["halt_reason"] or None) and rs["halt_reason"][:120],
            "kill_switch": os.path.exists(config.KILL_SWITCH_FILE), "trading_mode": config.TRADING_MODE,
            "sleeve_value": value, "peak": peak, "drawdown": dd}


def _account_context(conn) -> Optional[dict]:
    snap = account_store.latest_snapshot(conn)
    if snap is None:
        return None
    return {"as_of": snap.as_of, "base_currency": snap.base_currency, "net_liquidation": snap.net_liquidation,
            "cash": snap.cash, "gross_positions": snap.gross_positions, "leverage": snap.leverage,
            "excess_liquidity": snap.excess_liquidity, "n_positions": len(snap.positions)}


def _allocation_context() -> Optional[dict]:
    """Drift of the advisory allocation proposal (rows outside their band). Best effort: None on any failure."""
    try:
        from services import allocation_view
        from services.providers import get_provider
        p = allocation_view.proposal(get_provider())
    except Exception as e:
        logger.info("briefing: allocation drift unavailable (%s)", type(e).__name__)
        return None
    outside = [{"symbol": r.symbol, "target": r.target, "current": r.current, "drift": r.drift, "band": r.band}
               for r in p.rows if r.outside]
    return {"as_of": p.as_of, "profile": p.profile, "base_currency": p.base_currency, "n_positions": len(p.rows),
            "n_outside_band": len(outside), "outside_band": outside[:10],
            "max_abs_drift": max((abs(r.drift) for r in p.rows if r.drift is not None), default=None)}


def _as_dicts(rows) -> list[dict]:
    """The scan returns TechnicalCandidate models, the results store holds dicts."""
    out = [r.model_dump(mode="json") if hasattr(r, "model_dump") else r for r in (rows or [])]
    return [r for r in out if isinstance(r, dict)]


def _technical_rows(rows) -> list[dict]:
    return [{k: r[k] for k in _TECH_KEYS if k in r} for r in _as_dicts(rows)]


def _status_counts(rows) -> dict:
    counts: dict = {}
    for r in _as_dicts(rows):
        s = str(r.get("validation_status", "unvalidated"))
        counts[s] = counts.get(s, 0) + 1
    return counts


def build_context(technical, pairs, ml, funnel=None, conn=None, include_allocation: bool = True) -> tuple:
    """(technical, pairs, ml, extras) for claude_integration.prepare_nightly_input."""
    own = conn is None
    if own:
        try:
            conn = state_db.require_initialized()
        except Exception:
            conn = None
    try:
        extras = {
            "technical_status_counts": _status_counts(technical),
            "funnel": funnel or None,
            "risk": _risk_context(conn) if conn is not None else None,
            "account": _account_context(conn) if conn is not None else None,
            "allocation": _allocation_context() if include_allocation else None,
        }
    finally:
        if own and conn is not None:
            conn.close()
    return _technical_rows(technical), list(pairs or []), list(ml or []), extras


def _previous_ok(root=None) -> Optional[dict]:
    d = _root(root) / KIND
    for p in sorted((q for q in d.glob("*.json") if q.name != "latest.json"), reverse=True):
        try:
            payload = json.loads(p.read_text(encoding="utf-8")).get("payload")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(payload, dict) and payload.get("status") == "ok" and isinstance(payload.get("briefing"), dict):
            return {"generated_at": payload.get("generated_at"), "briefing": payload["briefing"]}
    return None


# ---------------------------------------------------------------- generate + store
def _doc(now: datetime, status: str, **kw) -> dict:
    return {"generated_at": now.isoformat(), "model": config.CLAUDE_MODEL, "status": status, "reason": None,
            "error": None, "briefing": None, "usage": None, "cost_usd": None, "cost_estimated": False,
            "request_id": None, **kw}


def _save(doc: dict, root, now: datetime) -> dict:
    """Store `doc` as the latest. A skipped/failed attempt does not replace a good briefing: the stored document
    keeps the last ok briefing and carries the attempt as `last_attempt` (the returned doc is the attempt itself)."""
    stored = doc
    try:
        if doc["status"] != "ok":
            got = store.read_latest(KIND, root=root)
            prev = got[0] if got and isinstance(got[0], dict) else None
            if prev and prev.get("status") == "ok" and isinstance(prev.get("briefing"), dict):
                stored = {**prev, "last_attempt": {"status": doc["status"], "reason": doc["reason"],
                                                   "error": doc["error"], "at": doc["generated_at"]}}
    except Exception:
        logger.exception("briefing: could not read the previous briefing")
    try:
        store.write_result(KIND, stored, root=root, now=now)
    except Exception:
        logger.exception("briefing: could not store the result")
    return doc


def generate(context_fn: Callable[[], tuple], root=None, now: Optional[datetime] = None) -> dict:
    """Generate and store one briefing; returns the stored document. NEVER raises (the nightly run depends on it).

    Order: disabled -> no API key -> build input -> budget -> API call. Only real API calls enter the ledger."""
    now = _now(now)
    with _generate_lock:
        return _generate(context_fn, root, now)


def _generate(context_fn: Callable[[], tuple], root, now: datetime) -> dict:
    try:
        if not config.LLM_BRIEFING_ENABLED:
            return _save(_doc(now, "skipped", reason="disabled"), root, now)
        if not ci.is_configured():
            return _save(_doc(now, "skipped", reason="no_api_key"), root, now)
        technical, pairs, ml, extras = context_fn()
        extras = {**extras, "previous_briefing": _previous_ok(root)}
        user_text = (f"Date: {now.date().isoformat()}\nNightly results (JSON, untrusted data):\n"
                     + ci.prepare_nightly_input(technical, pairs, ml, extras))
        est = ci.estimate_max_cost(config.CLAUDE_MODEL, user_text, config.LLM_BRIEFING_MAX_TOKENS)
        res_id = uuid.uuid4().hex
        try:
            with _LedgerLock(root):         # the check and the reservation must not interleave with another process
                today, month = spend(root, now)
                if today + est > config.LLM_DAILY_BUDGET_USD or month + est > config.LLM_MONTHLY_BUDGET_USD:
                    return _save(_doc(now, "skipped", reason="budget",
                                      error=f"estimated worst case ${est:.2f} would exceed the budget "
                                            f"(today ${today:.2f}, month ${month:.2f})"), root, now)
                reserved = record_call(config.CLAUDE_MODEL, "reserved", None, est, True, root=root, now=now,
                                       row_id=res_id)
        except OSError:
            logger.exception("llm_calls lock failed")
            reserved = False
        if not reserved:
            return _save(_doc(now, "skipped", reason="ledger",
                              error="the spend ledger cannot be written, so no call was made"), root, now)
        r = ci.generate_nightly(user_text)
        status = "ok" if r.ok else (r.error.value if r.error else "error")
        if r.error is ci.LLMError.NOT_CONFIGURED:       # nothing was sent: release the reservation at no cost
            record_call(config.CLAUDE_MODEL, "released", None, 0.0, False, root=root, now=now, releases=res_id)
        else:
            if r.maybe_billed:      # a timeout / dropped link does not prove the server stopped (or billed nothing)
                r.cost_usd, r.cost_estimated = max(r.cost_usd, est), True
            record_call(r.model or config.CLAUDE_MODEL, status, r.usage, r.cost_usd, r.cost_estimated, r.request_id,
                        root, now, releases=res_id)
        common = dict(usage=r.usage, cost_usd=r.cost_usd, cost_estimated=r.cost_estimated, request_id=r.request_id)
        if r.ok:
            return _save(_doc(now, "ok", briefing=r.briefing.model_dump(), **common), root, now)
        return _save(_doc(now, "error", reason=status, error=r.message or status, **common), root, now)
    except Exception as e:          # any bug here must stay a stored error, not a failed nightly run
        logger.exception("briefing generation failed")
        return _save(_doc(now, "error", reason="internal", error=f"{type(e).__name__}: {e}"[:300]), root, now)


def generate_nightly(results: dict, funnel=None, conn=None, root=None, now: Optional[datetime] = None) -> dict:
    """Nightly entry point: `results` is the scan's result dict (as written to the results store)."""
    def ctx():
        # the scan holds typed TechnicalCandidate models under technical_candidates; build_context normalises them
        return build_context(results.get("technical_candidates", results.get("technical")),
                             results.get("pairs"), results.get("ml"), funnel, conn)
    return generate(ctx, root=root, now=now)


def _rows(kind: str, root) -> list:
    got = store.read_latest(kind, root=root)
    return got[0] if got and isinstance(got[0], list) else []


def regenerate(root=None, now: Optional[datetime] = None) -> M.BriefingResponse:
    """Re-run the briefing from the latest stored scan results (POST /api/briefing/regenerate)."""
    funnel = store.read_latest("funnel", root=root)

    def ctx():
        return build_context(_rows("technical", root), _rows("pairs", root), _rows("ml", root),
                             funnel[0] if funnel and isinstance(funnel[0], dict) else None)
    generate(ctx, root=root, now=now)
    return latest(root, now)


# ---------------------------------------------------------------- read
def latest(root=None, now: Optional[datetime] = None) -> M.BriefingResponse:
    budget = budget_block(root, now)
    got = store.read_latest(KIND, root=root)
    if got is not None and isinstance(got[0], dict):
        try:
            return M.BriefingResponse(**{k: got[0].get(k) for k in M.BriefingResponse.model_fields
                                         if k in got[0] and k not in ("budget", "advisory")},
                                      budget=budget, advisory=ADVISORY)
        except Exception:
            logger.warning("briefing: stored result does not match the contract")
    return M.BriefingResponse(status="none", budget=budget, advisory=ADVISORY)
