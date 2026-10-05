"""Strict JSON serialisation for every route (WS2.7).

``to_native`` converts numpy / pandas / dataclass values to Python natives and maps NaN / +-inf to None, so
``json.dumps(..., allow_nan=False)`` can never raise and clients never see ``NaN`` or ``Infinity`` tokens.
``SafeJSONResponse`` is the app's default response class and applies it to whatever a route returns.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import enum
import json
import math
from typing import Any

import numpy as np
import pandas as pd
from fastapi.responses import JSONResponse


def to_native(obj: Any) -> Any:
    """Recursively convert `obj` to JSON-safe Python types (non-finite floats -> None)."""
    if obj is None or isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, dict):
        return {str(k): to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_native(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [to_native(v) for v in obj.tolist()]
    if obj is pd.NaT:
        return None
    if isinstance(obj, (pd.Timestamp, _dt.datetime, _dt.date)):
        return obj.isoformat()
    if isinstance(obj, pd.Timedelta):
        return obj.total_seconds()
    if isinstance(obj, pd.Series):
        return {str(k): to_native(v) for k, v in obj.items()}
    if isinstance(obj, pd.DataFrame):
        return [to_native(r) for r in obj.reset_index().to_dict("records")]
    if isinstance(obj, enum.Enum):
        return to_native(obj.value)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return to_native(dataclasses.asdict(obj))
    if isinstance(obj, BaseException):
        return str(obj)
    if hasattr(obj, "item") and callable(obj.item):  # other numpy-like scalars
        try:
            return to_native(obj.item())
        except (ValueError, TypeError):
            pass
    return str(obj)


class SafeJSONResponse(JSONResponse):
    """JSONResponse that sanitises its content and refuses to emit NaN / Infinity."""

    def render(self, content: Any) -> bytes:
        return json.dumps(to_native(content), allow_nan=False, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")


def ok(data: Any, status_code: int = 200) -> SafeJSONResponse:
    """Return `data` as strict JSON. Routes use this so numpy values never reach FastAPI's encoder."""
    return SafeJSONResponse(content=data, status_code=status_code)
