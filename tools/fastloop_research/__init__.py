"""Deterministic local research-model backend.

The public entry point intentionally has no Blender or IfcOpenShell import at
module import time.  Product code can therefore validate and queue work even
when an optional modeling dependency is not installed on the web process.
"""

from .contract import ResearchModelError, compute_structure_hash, validate_bundle
from .engine import run_research_model

__all__ = [
    "ResearchModelError",
    "compute_structure_hash",
    "run_research_model",
    "validate_bundle",
]
