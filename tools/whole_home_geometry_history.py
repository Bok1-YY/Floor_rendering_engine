"""Repository-root launcher for publishing geometry audit history."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT.parent))
if "Floor_engine_server" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "Floor_engine_server", REPO_ROOT / "__init__.py",
        submodule_search_locations=[str(REPO_ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Floor Engine package")
    package = importlib.util.module_from_spec(spec)
    sys.modules["Floor_engine_server"] = package
    spec.loader.exec_module(package)

from Floor_engine_server.whole_home_geometry_history import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
