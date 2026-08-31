from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


class ProviderError(str):
    """String-compatible error carrying billing and retry metadata."""

    def __new__(cls, message: str, *, failure_code: str = "provider_failure",
                retry_safety: str = "fatal", may_have_been_billed: bool = False,
                attempts: Optional[list[dict]] = None):
        obj = str.__new__(cls, str(message))
        obj.failure_code = failure_code
        obj.retry_safety = retry_safety
        obj.may_have_been_billed = bool(may_have_been_billed)
        obj.attempts = list(attempts or [])
        return obj


@dataclass
class ProviderCallOutcome:
    image: Any = None
    error: Optional[str] = None
    provider: str = ""
    model_id: str = ""
    failure_code: str = ""
    retry_safety: str = "safe"
    may_have_been_billed: bool = False
    attempts: list[dict] = field(default_factory=list)

    def __iter__(self):
        # Preserve the historical ``img, err, provider = call_image_generate(...)`` contract.
        yield self.image
        yield self.error
        yield self.provider


def coerce_provider_outcome(value, *, provider: str = "", model_id: str = "") -> ProviderCallOutcome:
    if isinstance(value, ProviderCallOutcome):
        return value
    image = error = None
    actual_provider = provider
    if isinstance(value, tuple):
        if len(value) >= 1:
            image = value[0]
        if len(value) >= 2:
            error = value[1]
        if len(value) >= 3:
            actual_provider = value[2] or provider
    else:
        image = value
    return ProviderCallOutcome(
        image=image,
        error=error,
        provider=actual_provider,
        model_id=model_id,
        failure_code=str(getattr(error, "failure_code", "provider_failure" if error else "")),
        retry_safety=str(getattr(error, "retry_safety", "fatal" if error else "safe")),
        may_have_been_billed=bool(getattr(error, "may_have_been_billed", False)),
        attempts=list(getattr(error, "attempts", []) or []),
    )


__all__ = ["ProviderCallOutcome", "ProviderError", "coerce_provider_outcome"]
