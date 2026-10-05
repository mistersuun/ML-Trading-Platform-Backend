"""Versioned model artefacts: models/<symbol>/<UTC timestamp>/{model.joblib, metadata.json}.

* joblib only (no pickle module); the bundle is a plain dict, metadata is JSON.
* a version is written to a temp directory and renamed into place, so readers never see a
  half-written artefact.
* `load` refuses (ModelStoreError) on a major-version mismatch of sklearn / lightgbm / xgboost
  or on a feature-schema mismatch; `load_latest_valid` skips such versions and returns the newest
  one that loads.
* this module is a store only: it never trains and does not import the detector.
"""
from __future__ import annotations

from importlib.metadata import version as _metadata_version
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import joblib
import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)

BUNDLE_FILE = "model.joblib"
META_FILE = "metadata.json"
SCHEMA_VERSION = 1
_LIBS = ("sklearn", "lightgbm", "xgboost", "numpy", "pandas")
_DISTRIBUTIONS = {"sklearn": "scikit-learn", "lightgbm": "lightgbm", "xgboost": "xgboost", "numpy": "numpy",
                  "pandas": "pandas"}       # constant names only: no computed imports (tests/test_ast_boundaries.py)
_MAJOR_CHECKED = ("sklearn", "lightgbm", "xgboost")
_TS_FMT = "%Y%m%dT%H%M%S%fZ"
_TS_RE = re.compile(r"^\d{8}T\d{12}Z$")


class ModelStoreError(RuntimeError):
    """A stored model cannot be used (missing, corrupt, version or schema mismatch)."""


def default_root() -> Path:
    return Path(os.environ.get("ML_MODELS_DIR") or Path(__file__).resolve().parent.parent / "models")


def _symbol_dir(root, symbol: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._=-]", "_", symbol)
    return Path(root) / safe


def library_versions() -> dict[str, Optional[str]]:
    out: dict[str, Optional[str]] = {}
    for name in _LIBS:
        try:
            out[name] = _metadata_version(_DISTRIBUTIONS[name])
        except Exception:  # noqa: BLE001  (lightgbm / xgboost are optional)
            out[name] = None
    return out


def _major(v: Optional[str]) -> Optional[str]:
    return v.split(".")[0] if v else None


def git_sha() -> Optional[str]:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5,
                           cwd=Path(__file__).resolve().parent)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def schema_hash(feature_cols) -> str:
    return hashlib.sha256(json.dumps(list(feature_cols)).encode()).hexdigest()


def data_hash(df: pd.DataFrame) -> str:
    """Hash of the OHLCV values and index the model was trained on."""
    h = hashlib.sha256()
    h.update(pd.util.hash_pandas_object(df, index=True).to_numpy().tobytes())
    return h.hexdigest()


def _jsonable(o: Any):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if np.isfinite(f) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


def save(symbol: str, bundle: dict, *, feature_cols, df: Optional[pd.DataFrame] = None,
         label_spec: Optional[dict] = None, metrics: Optional[dict] = None,
         drift_reference: Optional[dict] = None, calibration: Optional[dict] = None,
         root=None, now: Optional[datetime] = None) -> Path:
    """Write a new version atomically and return its directory.

    `drift_reference`: per-feature quantile edges / bin shares of the training rows (ml.drift.reference), kept
    in the metadata so the drift guard can be inspected without unpickling; `calibration`: model choice,
    thresholds and calibration facts (JSON)."""
    sdir = _symbol_dir(root or default_root(), symbol)
    sdir.mkdir(parents=True, exist_ok=True)
    ts = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    name = ts.strftime(_TS_FMT)
    final = sdir / name
    while final.exists():  # same-microsecond collision: step forward
        ts += timedelta(microseconds=1)
        name = ts.strftime(_TS_FMT)
        final = sdir / name
    meta = {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol,
        "created_utc": ts.isoformat(),
        "library_versions": library_versions(),
        "git_sha": git_sha(),
        "data": ({"start": df.index[0].isoformat(), "end": df.index[-1].isoformat(),
                  "n_rows": int(len(df)), "hash": data_hash(df)} if df is not None and len(df) else None),
        "features": list(feature_cols),
        "feature_schema_hash": schema_hash(feature_cols),
        "label_spec": label_spec or {},
        "seeds": {"seed": config.SEED},
        "metrics": metrics or {},
        "drift_reference": drift_reference,
        "calibration": calibration or {},
    }
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=sdir))
    try:
        joblib.dump(bundle, tmp / BUNDLE_FILE)
        (tmp / META_FILE).write_text(json.dumps(_jsonable(meta), indent=2, sort_keys=True))
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final


def read_metadata(path) -> dict:
    p = Path(path) / META_FILE
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError) as e:
        raise ModelStoreError(f"cannot read model metadata at {p}: {e}") from e


def check_compatible(meta: dict, expected_schema_hash: Optional[str] = None) -> None:
    """Raise ModelStoreError on a major library mismatch or a feature-schema mismatch."""
    cur = library_versions()
    for lib in _MAJOR_CHECKED:
        saved = (meta.get("library_versions") or {}).get(lib)
        if saved is None and cur.get(lib) is None:
            continue
        if _major(saved) != _major(cur.get(lib)):
            raise ModelStoreError(
                f"model was saved with {lib} {saved} but {cur.get(lib)} is installed "
                f"(major version mismatch); retrain the model")
    if expected_schema_hash is not None and meta.get("feature_schema_hash") != expected_schema_hash:
        raise ModelStoreError("feature schema mismatch: the stored model was trained on a different "
                              "feature set than the current code produces; retrain the model")


def load(path, expected_schema_hash: Optional[str] = None) -> tuple[dict, dict]:
    """Load (bundle, metadata) from a version directory, refusing incompatible artefacts."""
    meta = read_metadata(path)
    check_compatible(meta, expected_schema_hash)
    try:
        bundle = joblib.load(Path(path) / BUNDLE_FILE)
    except Exception as e:  # noqa: BLE001
        raise ModelStoreError(f"cannot load model bundle at {path}: {e}") from e
    if meta.get("feature_schema_hash") != schema_hash(bundle.get("feature_cols", [])):
        raise ModelStoreError("model bundle does not match its metadata (feature schema hash)")
    return bundle, meta


def versions(symbol: str, root=None) -> list[Path]:
    """Version directories, newest first (the timestamp name sorts chronologically)."""
    sdir = _symbol_dir(root or default_root(), symbol)
    if not sdir.is_dir():
        return []
    return sorted((p for p in sdir.iterdir() if p.is_dir() and _TS_RE.match(p.name)), reverse=True)


def load_latest_valid(symbol: str, expected_schema_hash: Optional[str] = None,
                      root=None) -> Optional[tuple[dict, dict, Path]]:
    """Newest version that loads; incompatible or corrupt ones are skipped (logged). None if none."""
    for p in versions(symbol, root):
        try:
            bundle, meta = load(p, expected_schema_hash)
            return bundle, meta, p
        except ModelStoreError as e:
            logger.warning("skipping model %s: %s", p, e)
    return None


def drift_reference(meta: dict) -> Optional[dict]:
    """Training feature quantiles from a version's metadata (None for versions saved before WS4.4)."""
    return meta.get("drift_reference")


def age_days(meta: dict, now: Optional[datetime] = None) -> float:
    created = datetime.fromisoformat(meta["created_utc"])
    return ((now or datetime.now(timezone.utc)) - created).total_seconds() / 86400.0


def is_stale(meta: dict, retrain_days: Optional[int] = None, now: Optional[datetime] = None) -> bool:
    return age_days(meta, now) > (config.ML_RETRAIN_DAYS if retrain_days is None else retrain_days)
