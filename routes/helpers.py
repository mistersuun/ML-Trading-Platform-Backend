"""Shared route helpers: request-model validators (from the services) and the strict-JSON response."""
from __future__ import annotations

from api.serialize import SafeJSONResponse, to_native
from services.common import (PERIOD_MAX, PERIOD_MIN, SYMBOL_RE, check_pattern, check_symbol, cut_holdout, finite,  # noqa: F401
                             holdout_info, require_data)


def json_response(data) -> SafeJSONResponse:
    """Back-compat name: strict JSON (NaN/inf -> null) for any payload."""
    return SafeJSONResponse(content=to_native(data))
