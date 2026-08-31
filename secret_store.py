"""OS-backed API-key storage with explicit environment and legacy fallbacks."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass


SERVICE_NAME = "FloorEngine"
SECRET_ENV = {
    "gemini_api_key": "GEMINI_API_KEY",
    "fal_api_key": "FAL_API_KEY",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
}
SECRET_FIELDS = tuple(SECRET_ENV)


class SecretStoreError(RuntimeError):
    pass


class SecretBackendUnavailable(SecretStoreError):
    pass


@dataclass(frozen=True)
class SecretResolution:
    value: str
    source: str


_lock = threading.RLock()
_cache: dict[str, str] = {}
_backend_error = ""


def _keyring_module():
    try:
        import keyring
        from keyring.backends.null import Keyring as NullKeyring
    except Exception as exc:
        raise SecretBackendUnavailable(f"keyring unavailable: {exc}") from exc
    try:
        backend = keyring.get_keyring()
        if isinstance(backend, NullKeyring) or float(getattr(backend, "priority", 0) or 0) <= 0:
            raise SecretBackendUnavailable("no recommended system keyring backend")
    except SecretBackendUnavailable:
        raise
    except Exception as exc:
        raise SecretBackendUnavailable(f"system keyring unavailable: {exc}") from exc
    return keyring, backend


def backend_status() -> dict:
    global _backend_error
    try:
        _keyring, backend = _keyring_module()
        _backend_error = ""
        return {
            "name": f"{backend.__class__.__module__}.{backend.__class__.__name__}",
            "available": True,
            "persistent": True,
            "error": "",
        }
    except SecretBackendUnavailable as exc:
        _backend_error = str(exc)
        return {
            "name": "environment-only",
            "available": False,
            "persistent": False,
            "error": str(exc),
        }


def environment_secret(field: str) -> str:
    env_name = SECRET_ENV.get(field, "")
    return str(os.environ.get(env_name) or "").strip() if env_name else ""


def resolve_secret(field: str, legacy_value: str = "") -> SecretResolution:
    if field not in SECRET_ENV:
        raise KeyError(field)
    env_value = environment_secret(field)
    if env_value:
        return SecretResolution(env_value, "environment")
    with _lock:
        if field in _cache:
            value = _cache[field]
            return SecretResolution(value, "keyring" if value else "missing")
        try:
            keyring, _backend = _keyring_module()
            value = str(keyring.get_password(SERVICE_NAME, field) or "").strip()
            _cache[field] = value
            if value:
                return SecretResolution(value, "keyring")
        except SecretBackendUnavailable:
            pass
        except Exception:
            pass
    legacy = str(legacy_value or "").strip()
    return SecretResolution(legacy, "legacy" if legacy else "missing")


def set_secret(field: str, value: str) -> None:
    if field not in SECRET_ENV:
        raise KeyError(field)
    clean = str(value or "").strip()
    if not clean:
        delete_secret(field)
        return
    keyring, _backend = _keyring_module()
    try:
        keyring.set_password(SERVICE_NAME, field, clean)
        verified = str(keyring.get_password(SERVICE_NAME, field) or "")
    except Exception as exc:
        raise SecretStoreError(f"failed to persist {field}: {exc}") from exc
    if verified != clean:
        raise SecretStoreError(f"secret read-back verification failed for {field}")
    with _lock:
        _cache[field] = clean


def delete_secret(field: str) -> None:
    if field not in SECRET_ENV:
        raise KeyError(field)
    if environment_secret(field):
        raise SecretStoreError(f"{SECRET_ENV[field]} is supplied by the process environment")
    keyring, _backend = _keyring_module()
    try:
        existing = keyring.get_password(SERVICE_NAME, field)
        if existing is not None:
            keyring.delete_password(SERVICE_NAME, field)
    except Exception as exc:
        raise SecretStoreError(f"failed to delete {field}: {exc}") from exc
    with _lock:
        _cache[field] = ""


def clear_cache() -> None:
    with _lock:
        _cache.clear()


__all__ = [
    "SECRET_ENV", "SECRET_FIELDS", "SERVICE_NAME", "SecretBackendUnavailable",
    "SecretResolution", "SecretStoreError", "backend_status", "clear_cache",
    "delete_secret", "environment_secret", "resolve_secret", "set_secret",
]
