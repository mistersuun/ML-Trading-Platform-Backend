"""Concurrency cap for interactive heavy endpoints (WS3.4): at most `limit` run at once, the rest get 429.

Use as a decorator on a plain ``def`` route (signature preserved for FastAPI) or as a dependency.
"""
from __future__ import annotations

import functools
import threading
from typing import Callable

from api.errors import ERROR_RESPONSES, ApiError, ErrorEnvelope


# One cap shared by every interactive heavy route: only one CPU-heavy job runs at a time.
HEAVY = threading.BoundedSemaphore(1)
HEAVY_RESPONSES: dict = {**ERROR_RESPONSES,
                         429: {"model": ErrorEnvelope, "description": "busy: another heavy request is running"}}


class Busy(ApiError):
    status_code, code = 429, "busy"

    def __init__(self):
        super().__init__("Server is busy with another heavy request; retry shortly", {"retry_after_s": 5})


def heavy_endpoint(limit: int = 1, semaphore: threading.BoundedSemaphore | None = None) -> Callable:
    """Decorator: run the route only if a slot is free, else raise Busy (429 ``{"error": {"code": "busy"}}``)."""
    sem = semaphore or threading.BoundedSemaphore(limit)

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not sem.acquire(blocking=False):
                raise Busy()
            try:
                return fn(*args, **kwargs)
            finally:
                sem.release()
        wrapper.semaphore = sem
        return wrapper
    return deco


def heavy_dependency(limit: int = 1):
    """FastAPI dependency factory with the same semantics: ``Depends(heavy_dependency(1))``."""
    sem = threading.BoundedSemaphore(limit)

    def dep():
        if not sem.acquire(blocking=False):
            raise Busy()
        try:
            yield
        finally:
            sem.release()
    dep.semaphore = sem
    return dep
