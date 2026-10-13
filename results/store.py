"""Atomic JSON result files under ``results/<kind>/`` (gitignored).

Layout: ``results/<kind>/latest.json`` plus timestamped copies ``<UTC stamp>.json`` (newest 30 kept).
Every file is ``{"kind", "generated_at", "code_version", "config_hash", "payload"}``. Writes go to a temp file
in the same directory and are published with ``os.replace``, so a crash leaves the previous file intact.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import config
from api.serialize import to_native

RESULTS_DIR = Path(config.BASE_DIR) / "results"
KEEP = 30
KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")

# Non-secret values that define how a result was produced (a change invalidates comparisons across runs).
_HASHED_CONFIG = (
    "LOOKBACK_DAYS", "BACKTEST_INITIAL_CAPITAL", "COMMISSION_PCT", "SLIPPAGE_PCT", "MIN_WIN_RATE", "MIN_TRADES",
    "MIN_PROFIT_FACTOR", "MIN_SHARPE", "MAX_POSITION_SIZE_PCT", "MAX_PORTFOLIO_RISK_PCT", "STOP_LOSS_PCT",
    "TAKE_PROFIT_PCT", "MAX_CORRELATED_POSITIONS", "MAX_DRAWDOWN_HALT_PCT", "ML_TRAIN_TEST_SPLIT",
    "ML_N_ESTIMATORS", "ML_MIN_CONFIDENCE", "PAIRS_LOOKBACK", "PAIRS_ZSCORE_ENTRY", "PAIRS_COINT_PVALUE",
    "WATCHLIST", "PAIRS", "TRADING_MODE",
)


def _root(root) -> Path:
    return Path(root) if root is not None else RESULTS_DIR


def check_kind(kind: str) -> str:
    if not isinstance(kind, str) or not KIND_RE.match(kind):
        raise ValueError(f"invalid result kind: {kind!r}")
    return kind


def config_hash() -> str:
    snap = {k: getattr(config, k, None) for k in _HASHED_CONFIG}
    return hashlib.sha256(json.dumps(to_native(snap), sort_keys=True, default=str).encode()).hexdigest()[:16]


def code_version() -> Optional[str]:
    """Git SHA of the checkout, or None when git / a repo is unavailable."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=str(config.BASE_DIR),
                             capture_output=True, text=True, timeout=5)
        sha = out.stdout.strip()
        return sha if out.returncode == 0 and sha else None
    except Exception:
        return None


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_result(kind: str, payload: Any, root=None, now: Optional[datetime] = None, keep: int = KEEP) -> Path:
    """Write `payload` (pydantic model, dict or list) as the new latest result of `kind`; returns latest.json."""
    check_kind(kind)
    now = now or datetime.now(timezone.utc)
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    doc = {"kind": kind, "generated_at": now.isoformat(), "code_version": code_version(),
           "config_hash": config_hash(), "payload": to_native(payload)}
    text = json.dumps(doc, allow_nan=False, ensure_ascii=False)
    d = _root(root) / kind
    _atomic_write(d / f"{now.strftime('%Y%m%dT%H%M%S%f')}.json", text)   # history first, latest last
    _atomic_write(d / "latest.json", text)
    prune(kind, keep=keep, root=root)
    return d / "latest.json"


def prune(kind: str, keep: int = KEEP, root=None) -> list[Path]:
    """Delete all but the newest `keep` timestamped copies (latest.json is never touched)."""
    d = _root(root) / check_kind(kind)
    files = sorted(p for p in d.glob("*.json") if p.name != "latest.json")
    gone = files[:-keep] if keep > 0 else files
    for p in gone:
        p.unlink(missing_ok=True)
    return gone


def read_document(kind: str, root=None) -> Optional[dict]:
    """The full latest document for `kind`, or None when missing / unreadable."""
    p = _root(root) / check_kind(kind) / "latest.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and "payload" in doc and "generated_at" in doc else None


def read_latest(kind: str, root=None) -> Optional[tuple[Any, datetime]]:
    """(payload, generated_at) of the latest result, or None."""
    doc = read_document(kind, root)
    if doc is None:
        return None
    try:
        ts = datetime.fromisoformat(doc["generated_at"])
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return doc["payload"], ts
