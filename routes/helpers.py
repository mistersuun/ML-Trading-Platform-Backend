"""Shared route helpers: request-model validators and payload builders (WS2.7)."""
from __future__ import annotations

import math
import re
from typing import Optional

import config
from api.errors import NoData
from api.serialize import SafeJSONResponse, to_native
from patterns import PATTERN_REGISTRY

SYMBOL_RE = re.compile(r"^[A-Za-z0-9^][A-Za-z0-9^.\-=/]{0,19}$")
PERIOD_MIN, PERIOD_MAX = 60, 3650


def json_response(data) -> SafeJSONResponse:
    """Back-compat name: strict JSON (NaN/inf -> null) for any payload."""
    return SafeJSONResponse(content=to_native(data))


def check_symbol(v: str) -> str:
    """Format check only (pydantic validator); the data layer answers 404 for symbols with no data."""
    v = v.strip()
    if not SYMBOL_RE.match(v):
        raise ValueError(f"invalid symbol {v!r}")
    return v.upper() if v.isalpha() else v


def check_pattern(v: str) -> str:
    if v not in PATTERN_REGISTRY:
        raise ValueError(f"unknown pattern {v!r}; choose from {sorted(PATTERN_REGISTRY)}")
    return v


def require_data(df, symbol: str):
    if df is None or df.empty:
        raise NoData(symbol)
    return df


def finite(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def metrics_payload(result) -> dict:
    """Numeric (fraction) metrics for a BacktestResult; non-finite -> None. Display strings live in
    `metrics_display` (the pre-Phase-2 string summary), kept for one release."""
    return result.metrics_payload()


def holdout_info(df) -> dict:
    """Label for research views: how much of the shown window is the fixed hold-out (never tune against it)."""
    import validation
    n = len(df)
    pre = validation.holdout_position(df) if n else 0
    return {
        "start": config.HOLDOUT_START, "bars": int(n - pre), "share": (n - pre) / n if n else 0.0,
        "note": (f"Bars on/after {config.HOLDOUT_START} are the hold-out: read once per strategy version, "
                 "never use them to choose or tune a strategy. Send include_holdout=false to exclude them."),
    }


def cut_holdout(df):
    """`df` restricted to bars before HOLDOUT_START."""
    import validation
    return df.iloc[:validation.holdout_position(df)]
