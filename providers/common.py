from __future__ import annotations

import time

import requests

from .types import ProviderError


EXPLICIT_RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}


def exception_chain(exc):
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)


def is_safe_pre_submit_exception(exc) -> bool:
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    safe_names = {"NameResolutionError", "NewConnectionError"}
    return any(item.__class__.__name__ in safe_names for item in exception_chain(exc))


def attempt_event(attempt: int, phase: str, started_at: float, *, outcome: str,
                  http_status: int | None = None) -> dict:
    return {
        "attempt": int(attempt),
        "phase": str(phase),
        "http_status": http_status,
        "started_at": float(started_at),
        "duration_ms": max(0, round((time.time() - started_at) * 1000)),
        "outcome": str(outcome),
    }


def ambiguous_provider_error(message: str, *, failure_code: str,
                             attempts: list[dict]) -> ProviderError:
    return ProviderError(
        message,
        failure_code=failure_code,
        retry_safety="ambiguous",
        may_have_been_billed=True,
        attempts=attempts,
    )


def safe_provider_error(message: str, *, failure_code: str,
                        attempts: list[dict]) -> ProviderError:
    return ProviderError(
        message,
        failure_code=failure_code,
        retry_safety="safe",
        may_have_been_billed=False,
        attempts=attempts,
    )


__all__ = [
    "EXPLICIT_RETRYABLE_HTTP", "ambiguous_provider_error", "attempt_event",
    "is_safe_pre_submit_exception", "safe_provider_error",
]
