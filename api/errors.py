"""Domain errors and the error envelope ``{"error": {"code", "message", "details"}}`` (WS2.7).

Every failure is a non-2xx response; no route returns ``{"error": ...}`` with status 200.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.serialize import SafeJSONResponse
from data.validate import DataQualityError, DataUnavailableError
from instruments import UnknownSymbol
from pairs_trading import InsufficientData

logger = logging.getLogger(__name__)


class ApiError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, details: Optional[Any] = None, *, status_code: Optional[int] = None,
                 code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.details = details
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code


class NoData(ApiError):
    status_code, code = 404, "no_data"

    def __init__(self, symbol: str):
        super().__init__(f"No data for {symbol}", {"symbol": symbol})


class UnknownPattern(ApiError):
    status_code, code = 422, "unknown_pattern"

    def __init__(self, name: str):
        super().__init__(f"Unknown pattern: {name}", {"pattern": name})


class InsufficientHistory(ApiError):
    status_code, code = 422, "insufficient_data"


def envelope(code: str, message: str, details: Any = None, request_id: Optional[str] = None) -> dict:
    err = {"code": code, "message": message, "details": details}
    if request_id:
        err["request_id"] = request_id
    return {"error": err}


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", None) or uuid.uuid4().hex[:12]


def _respond(request: Request, status: int, code: str, message: str, details: Any = None) -> SafeJSONResponse:
    rid = _rid(request)
    return SafeJSONResponse(envelope(code, message, details, rid), status_code=status,
                            headers={"X-Request-ID": rid})


def install(app: FastAPI) -> None:
    """Register the request-id middleware and every exception handler on `app`."""

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        response = await call_next(request)
        response.headers.setdefault("X-Request-ID", request.state.request_id)
        return response

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return _respond(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(UnknownSymbol)
    async def _unknown_symbol(request: Request, exc: UnknownSymbol):
        sym = exc.args[0] if exc.args else ""
        return _respond(request, 404, "unknown_symbol", f"Unknown symbol: {sym}", {"symbol": sym})

    @app.exception_handler(DataQualityError)
    async def _data_quality(request: Request, exc: DataQualityError):
        logger.warning("data quality failure: %s", exc)
        return _respond(request, 502, "data_quality", str(exc))

    @app.exception_handler(DataUnavailableError)
    async def _data_unavailable(request: Request, exc: DataUnavailableError):
        return _respond(request, 404, "no_data", str(exc))

    @app.exception_handler(InsufficientData)
    async def _insufficient(request: Request, exc: InsufficientData):
        return _respond(request, 422, "insufficient_data", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        details = [{"loc": [str(p) for p in e.get("loc", ())], "message": e.get("msg", ""), "type": e.get("type", "")}
                   for e in exc.errors()]
        return _respond(request, 422, "validation_error", "Invalid request", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _respond(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        rid = _rid(request)
        logger.exception("unhandled error (request %s) on %s", rid, request.url.path)
        return SafeJSONResponse(envelope("internal_error", "Internal server error", None, rid), status_code=500,
                                headers={"X-Request-ID": rid})
