"""Compatibility alias for the provider implementation.

New orchestration code belongs in ``providers/``. Replacing this module in
``sys.modules`` preserves historical imports and monkeypatch behavior while
legacy provider functions migrate in small verified slices.
"""

import sys

from . import api_legacy as _implementation

sys.modules[__name__] = _implementation
