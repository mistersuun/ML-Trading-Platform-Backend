"""Nightly pooled validation, SHADOW ONLY (decision D18).

``run_shadow(research)`` is called by the nightly scan with the same research-length frames the per-symbol validation
used. It computes the pooled verdict (``pooled_validation.evaluate_pooled``) and returns the payload that the
scheduler stores as ``results/pooled/latest.json`` and ``GET /api/results/pooled/latest`` serves.

Isolation from the nightly run (the per-symbol path and the forward-test journal must be unaffected):

* it NEVER raises: any failure becomes a stored ``pooled_error`` payload (and an INFO log line), never an exception,
  an alert, a warning in the run's log capture or a change to the run's status;
* it reads nothing the per-symbol path writes and writes only its own namespaces: ``pooled-*`` trial runs, the
  ``pooled_*`` ledger tables in ``trials.sqlite``, hold-out keys ``pooled|...`` and ``results/pooled/``;
* it creates no order, intent or alert, and no pooled status is in ``config.ORDER_ELIGIBLE_STATUSES``;
* it is skipped when ``POOLED_VALIDATION`` is not ``shadow`` and, by default, inside the forward paper test
  (``POOLED_SKIP_IN_FORWARD_TEST``), whose journal and timings must not change during the forward week;
* it runs only from the NIGHTLY scheduler (``nightly_scope``): a manual ``main.py scan`` never registers pooled
  trials or uses the single per-pattern pooled hold-out read (that read and its verdict would never reach the stored
  result, which only ``scheduler.run_nightly`` writes);
* a time budget is not needed: the pooled run is ~20 s on 17 symbols (the target is under 3 minutes).
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

KIND = "pooled"
ERROR_STATUS = "pooled_error"


_NIGHTLY = contextvars.ContextVar("pooled_nightly_scope", default=None)     # None | (results_root,)


@contextlib.contextmanager
def nightly_scope(results_root=None):
    """``scheduler.run_nightly`` enters this around the scan: only then does ``run_full_scan`` run the pooled shadow.
    ``results_root`` is the nightly's results root (the forward test uses its own), so the prior verdict that floors
    the variance and the stored result come from the same place."""
    tok = _NIGHTLY.set((results_root,))
    try:
        yield
    finally:
        _NIGHTLY.reset(tok)


def in_nightly_scope() -> bool:
    return _NIGHTLY.get() is not None


def nightly_root():
    """The results root of the enclosing ``nightly_scope`` (None: the default ``results/``)."""
    got = _NIGHTLY.get()
    return got[0] if got else None


def enabled() -> bool:
    """Is the nightly pooled shadow run switched on for this process?"""
    if config.POOLED_VALIDATION != "shadow":
        return False
    if getattr(config, "FORWARD_TEST", False) and config.POOLED_SKIP_IN_FORWARD_TEST:
        return False
    return True


def _holdout_store():
    try:
        from state.holdout import HoldoutStore
        return HoldoutStore()
    except Exception as e:      # None makes every survivor fail closed (holdout_store_unavailable)
        logger.info("pooled: hold-out store unavailable (%s: %s)", type(e).__name__, e)
        return None


def _prior(root=None) -> Optional[dict]:
    """The last stored pooled payload (with its generated_at), or None."""
    try:
        from results import store
        doc = store.read_document(KIND, root)
        if not doc:
            return None
        return dict(doc["payload"], generated_at=doc["generated_at"])
    except Exception:
        return None


def error_payload(exc: BaseException) -> dict:
    return {"unit": "pooled", "mode": config.POOLED_VALIDATION, "shadow_only": True, "order_eligible": False,
            "status": ERROR_STATUS, "message": f"{type(exc).__name__}: {exc}"[:300], "patterns": [],
            "retired": [], "shadow_signals": [], "universe": {"hash": "", "symbols": [], "k": 0, "missing": [],
                                                               "missing_reasons": {}, "complete": False,
                                                               "partial": False, "flags": []}}


def run_shadow(research: dict, *, root=None, patterns: Optional[dict] = None, end=None) -> Optional[dict]:
    """Compute the pooled shadow payload from ``research`` (symbol -> frame). None when switched off. Never raises."""
    if not enabled():
        return None
    if root is None:
        root = nightly_root()
    t0 = time.perf_counter()
    store = None
    try:
        import pooled_validation as PV
        store = _holdout_store()
        kw = {} if end is None else {"end": end}
        payload = PV.evaluate_pooled(research, patterns, holdout_store=store, registry=None, prior=_prior(root), **kw)
        payload["elapsed_s"] = round(time.perf_counter() - t0, 2)
        logger.info("pooled shadow: %s in %.1fs (%s)", payload.get("status"), time.perf_counter() - t0,
                    (payload.get("funnel") or {}).get("pooled_validated"))
        return payload
    except Exception as e:               # isolation: nothing pooled may fail the nightly run
        logger.info("pooled shadow failed (%s: %s); the nightly run is unaffected", type(e).__name__, e)
        return error_payload(e)
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def store_result(payload: Optional[dict], root=None) -> Optional[str]:
    """Write the pooled result file. Never raises; returns an error string or None."""
    if not payload:
        return None
    try:
        from results import store
        store.write_result(KIND, payload, root=root)
        return None
    except Exception as e:
        logger.info("pooled result not stored (%s: %s)", type(e).__name__, e)
        return f"{type(e).__name__}: {e}"[:200]


# ------------------------------------------------------------------ shadow-period health
def _is_ok_night(payload: dict) -> bool:
    return payload.get("status") == "ok" and bool((payload.get("universe") or {}).get("complete"))


REQUIRED_OK_SESSIONS = 20
REQUIRED_CALENDAR_DAYS = 28


def track_health(payload: dict, prior: Optional[dict], now: Optional[datetime] = None) -> dict:
    """Attach ``shadow_health`` to a pooled payload and return it.

    D18(a) needs at least 4 weeks of nightly pooled shadow results before any pooled pattern may become
    order-eligible, and a shadow run that fails every night must not go unnoticed. The nightly runs every calendar
    day, weekends included, and a weekend run sees the same bars, so wall-clock nights are NOT the clock:

    * ``ok_sessions`` counts DISTINCT ``payload['asof']`` bar dates (new trading sessions) of ok nights (status
      ``ok`` with a complete universe; a pooled_error, an incomplete run and a same-day re-run add nothing);
    * ``first_ok_date`` is the UTC date of the first counted ok night; ``calendar_days`` is the span from it to today;
    * ``complete`` is True only when ``ok_sessions >= 20`` AND ``calendar_days >= 28``;
    * ``ok_nights`` (distinct UTC dates with an ok run) and ``consecutive_failed_nights`` are kept for the alert.

    The counters are carried through failed nights (a failed payload replaces the stored file) from the prior
    payload's ``shadow_health``."""
    now = now or datetime.now(timezone.utc)
    day = now.date().isoformat()
    h = dict((prior or {}).get("shadow_health") or {}) if isinstance(prior, dict) else {}
    ok_nights = int(h.get("ok_nights", 0) or 0)
    streak = int(h.get("consecutive_failed_nights", 0) or 0)
    last = h.get("last_counted_date")
    ok_dates = list(h.get("ok_dates") or [])
    sessions = list(h.get("ok_session_dates") or [])
    n_sessions = int(h.get("ok_sessions", len(sessions)) or 0)
    first_ok = h.get("first_ok_date")
    ok = _is_ok_night(payload)
    asof = payload.get("asof")

    def count_ok() -> None:
        nonlocal ok_nights, ok_dates, streak, sessions, n_sessions, first_ok
        ok_nights += 1
        ok_dates = (ok_dates + [day])[-120:]
        streak = 0
        if asof and asof not in sessions:
            sessions = (sessions + [str(asof)])[-120:]
            n_sessions += 1
            if first_ok is None:
                first_ok = day

    if last != day:
        if ok:
            count_ok()
        else:
            streak += 1
        last = day
    elif ok and day not in ok_dates:      # a same-day re-run that recovered: counts once
        count_ok()
    days = None
    if first_ok:
        try:
            days = (now.date() - datetime.fromisoformat(first_ok).date()).days
        except ValueError:
            days = None
    payload["shadow_health"] = {
        "ok_nights": ok_nights, "consecutive_failed_nights": streak, "last_counted_date": last, "ok_dates": ok_dates,
        "ok_sessions": n_sessions, "ok_session_dates": sessions, "first_ok_date": first_ok, "calendar_days": days,
        "required_ok_sessions": REQUIRED_OK_SESSIONS, "required_calendar_days": REQUIRED_CALENDAR_DAYS,
        "complete": bool(n_sessions >= REQUIRED_OK_SESSIONS and days is not None and days >= REQUIRED_CALENDAR_DAYS)}
    return payload


def failure_alert_message(payload: dict) -> Optional[str]:
    """The text of the (non-halt) alert when the pooled shadow has failed ``POOLED_FAILURE_ALERT_NIGHTS`` or more
    nights in a row, else None. The scheduler alerts with a per-day dedup key."""
    h = payload.get("shadow_health") or {}
    n = int(h.get("consecutive_failed_nights", 0) or 0)
    if n < config.POOLED_FAILURE_ALERT_NIGHTS:
        return None
    return (f"Pooled shadow validation has not produced a complete verdict for {n} nights in a row "
            f"(last status: {payload.get('status')}; ok sessions so far: {h.get('ok_sessions', 0)}). Only ok sessions count "
            f"toward the D18 shadow period.")
