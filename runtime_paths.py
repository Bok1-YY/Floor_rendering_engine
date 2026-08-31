"""Single source of truth for Floor Engine runtime data paths.

Source checkouts keep mutable data in ``<project>/data``. Frozen one-file
builds keep it next to the executable so a copied runnable remains portable.
``FLOOR_DATA_DIR`` is the explicit override for tests and custom deployments.
"""

from __future__ import annotations

import os
import sys


def is_frozen_runtime() -> bool:
    return bool(getattr(sys, "frozen", False) or ("__compiled__" in globals()))


def frozen_executable_path() -> str:
    return str(
        os.environ.get("NUITKA_ONEFILE_BINARY")
        or os.environ.get("NUITKA_ORIGINAL_ARGV0")
        or sys.argv[0]
        or sys.executable
    )


def resolve_data_dir(package_dir: str | None = None) -> str:
    """Return the absolute directory that owns config, outputs and caches."""
    override = str(os.environ.get("FLOOR_DATA_DIR") or "").strip()
    if override:
        return os.path.abspath(override)
    if is_frozen_runtime():
        return os.path.dirname(os.path.abspath(frozen_executable_path()))
    root = os.path.abspath(package_dir or os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data")


__all__ = ["is_frozen_runtime", "frozen_executable_path", "resolve_data_dir"]
