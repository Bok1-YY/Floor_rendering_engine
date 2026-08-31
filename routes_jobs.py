"""Compatibility alias for legacy job routes during phased service extraction."""

import sys

from . import routes_jobs_legacy as _implementation

sys.modules[__name__] = _implementation
