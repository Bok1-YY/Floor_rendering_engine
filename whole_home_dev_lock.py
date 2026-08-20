# -*- coding: utf-8 -*-
"""Small cross-process locks for durable whole-home development state.

The application normally runs with one worker, but development agents and
recovery tools are separate processes.  ``threading.RLock`` cannot protect a
read/modify/write sequence across those processes, so the state stores use a
one-byte advisory lock for their short critical sections.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from typing import Any, Callable
from typing import Iterator


class WholeHomeStateLockTimeout(TimeoutError):
    pass


class WholeHomeStateLockOrderError(RuntimeError):
    pass


_THREAD_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_HELD_LOCKS = threading.local()
_SAFE_NAME = re.compile(r'[^A-Za-z0-9_.-]+')
_ATOMIC_REPLACE_DELAYS = (0.02, 0.05, 0.1)

# State-store lock order is deliberately one-way.  A store may call project
# CAS while holding no other state lock; no code may acquire a lower-ranked
# store while a higher-ranked store is held.  In practice the stores avoid
# nesting entirely, but this assertion turns a future inversion into a
# deterministic error instead of a data-root/session/workflow deadlock.
LOCK_ORDER = (
    'manual-service-owner',
    'autopilot-session',
    'workflow-projection',
    'external-review',
    'json',
)


def _namespace_rank(namespace: str) -> int:
    value = str(namespace or 'state')
    for index, prefix in enumerate(LOCK_ORDER):
        if value.startswith(prefix):
            return index
    return len(LOCK_ORDER)


def _held_locks() -> list[tuple[str, int]]:
    stack = getattr(_HELD_LOCKS, 'stack', None)
    if stack is None:
        stack = []
        _HELD_LOCKS.stack = stack
    return stack


def fsync_directory(path: str) -> None:
    """Durably commit directory entries on POSIX; Windows has no equivalent.

    Windows ``os.replace`` is still atomic and is retried for transient sharing
    violations.  Python cannot open a directory handle suitable for fsync on
    Windows, so the documented fallback is to return after replace succeeds.
    """
    if os.name == 'nt':
        return
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
    descriptor = os.open(os.path.realpath(path), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_directory(path: str) -> None:
    target = os.path.realpath(path)
    missing = []
    cursor = target
    while not os.path.isdir(cursor):
        missing.append(cursor)
        parent = os.path.dirname(cursor)
        if parent == cursor:
            break
        cursor = parent
    for folder in reversed(missing):
        os.mkdir(folder)
        fsync_directory(os.path.dirname(folder))


def _replace_with_retry(temporary: str, path: str) -> None:
    for attempt in range(len(_ATOMIC_REPLACE_DELAYS) + 1):
        try:
            os.replace(temporary, path)
            return
        except OSError as ex:
            retryable = (
                isinstance(ex, PermissionError)
                or getattr(ex, 'winerror', None) in (5, 32)
            )
            if not retryable or attempt >= len(_ATOMIC_REPLACE_DELAYS):
                raise
            time.sleep(_ATOMIC_REPLACE_DELAYS[attempt])


def durable_atomic_write(path: str, writer: Callable[[Any], None], *,
                         binary: bool = False) -> None:
    """Same-directory atomic commit with file and directory durability."""
    destination = os.path.realpath(path)
    parent = os.path.dirname(destination)
    _ensure_directory(parent)
    temporary = f'{destination}.{uuid.uuid4().hex}.tmp'
    mode = 'xb' if binary else 'x'
    kwargs = {} if binary else {'encoding': 'utf-8', 'newline': '\n'}
    try:
        with open(temporary, mode, **kwargs) as handle:
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temporary, destination)
        fsync_directory(parent)
    finally:
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass


def durable_atomic_json(path: str, payload: dict) -> None:
    durable_atomic_write(
        path,
        lambda handle: json.dump(
            payload, handle, ensure_ascii=False, indent=2),
    )


def durable_atomic_bytes(path: str, payload: bytes) -> None:
    durable_atomic_write(path, lambda handle: handle.write(payload), binary=True)


def _thread_lock(path: str) -> threading.RLock:
    normalized = os.path.normcase(os.path.realpath(path))
    with _THREAD_GUARD:
        return _THREAD_LOCKS.setdefault(normalized, threading.RLock())


def lock_path(data_root: str, namespace: str = 'state') -> str:
    root = os.path.realpath(str(data_root or ''))
    if not root:
        raise ValueError('data_root is required')
    readable = _SAFE_NAME.sub('_', str(namespace or 'state')).strip('._')[:80] or 'state'
    root_digest = hashlib.sha256(
        os.path.normcase(root).encode('utf-8')).hexdigest()[:20]
    digest = hashlib.sha256(str(namespace or 'state').encode('utf-8')).hexdigest()[:12]
    # Locks are operational coordination, not product/audit state.  Keeping
    # them in the OS temp directory lets a dry-run lock real data without
    # mutating the formal data root it is auditing.
    return os.path.join(
        tempfile.gettempdir(), 'floor-engine-state-locks', root_digest,
        f'{readable}-{digest}.lock')


def _try_os_lock(handle) -> bool:
    if os.name == 'nt':
        import msvcrt
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle) -> None:
    if os.name == 'nt':
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def data_root_lock(data_root: str, namespace: str = 'state', *,
                   timeout: float = 10.0, poll_interval: float = 0.025) -> Iterator[str]:
    """Hold a process and OS lock, returning the audit-visible lock path."""
    path = lock_path(data_root, namespace)
    rank = _namespace_rank(namespace)
    held = _held_locks()
    if held and path != held[-1][0] and rank < max(value[1] for value in held):
        raise WholeHomeStateLockOrderError(
            f'lock order violation: {namespace} after rank {max(value[1] for value in held)}')
    thread_lock = _thread_lock(path)
    deadline = time.monotonic() + max(0.0, float(timeout))
    if not thread_lock.acquire(timeout=max(0.0, float(timeout))):
        raise WholeHomeStateLockTimeout(f'timed out waiting for thread lock: {path}')
    handle = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handle = open(path, 'a+b')
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        while not _try_os_lock(handle):
            if time.monotonic() >= deadline:
                raise WholeHomeStateLockTimeout(f'timed out waiting for OS lock: {path}')
            time.sleep(max(0.001, float(poll_interval)))
        held.append((path, rank))
        try:
            yield path
        finally:
            held.pop()
            _unlock(handle)
    finally:
        if handle is not None:
            handle.close()
        thread_lock.release()


__all__ = [
    'LOCK_ORDER', 'WholeHomeStateLockOrderError', 'WholeHomeStateLockTimeout',
    'data_root_lock', 'durable_atomic_bytes', 'durable_atomic_json',
    'durable_atomic_write', 'fsync_directory', 'lock_path',
]
