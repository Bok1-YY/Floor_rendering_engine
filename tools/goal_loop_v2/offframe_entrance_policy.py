"""Fail-closed policy for entrances outside the supplied plan frame.

An off-frame entrance is a modelling hypothesis, not a source fact.  This
sidecar deliberately permits a research-only grey model while making it
impossible to treat the hypothesis as BIM/IFC authorization.
"""
from __future__ import annotations
import hashlib, json
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "off-frame-entrance-policy-candidate-v1"
POLICY = {"research_graymodel_allowed": True, "bim_ifc_authorized": False,
          "semantic_promotion": False, "build_authorized": False, "ready": False}

def _canon(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def _hash(v: Any) -> str:
    return hashlib.sha256(_canon(v).encode()).hexdigest()

def _bind(path: str | Path) -> dict[str, str]:
    p = Path(path).resolve(); raw = p.read_bytes()
    out = {"path": str(p), "sha256": hashlib.sha256(raw).hexdigest(), "file_type": p.suffix.lstrip(".") or "unknown"}
    if p.suffix.lower() == ".json":
        out["canonical_sha256"] = _hash(json.loads(raw.decode("utf-8")))
    return out

def build_policy(document: Mapping[str, Any], source_document: str | Path,
                 *, rationale: str = "No source-supported exterior opening is present in the supplied frame.") -> dict[str, Any]:
    """Create an unapproved policy candidate; never mutates *document*."""
    result = {"schema": SCHEMA, "sample_id": document.get("sample_id", "1308"),
              "source": {"structure_hash": document.get("structure_hash"), "document": _bind(source_document)},
              "frame_scope": {"source_frame": "supplied_plan_only", "off_frame_region": "outside_confirmed_outer_boundary",
                              "entrance_opening_id": None, "entrance_geometry": None},
              "rationale": rationale,
              "research_use": {"allowed": True, "purpose": "non-authoritative grey-model exploration",
                               "required_disclosure": "Off-frame entrance is hypothetical and must be visibly labelled."},
              "bim_ifc_use": {"allowed": False, "reason": "No source geometry, host wall, jamb, or path evidence for an exterior entrance."},
              "policy": dict(POLICY), "status": "pending_independent_review", "candidate_hash": "0" * 64}
    result["candidate_hash"] = _hash({k: v for k, v in result.items() if k != "candidate_hash"})
    return result

def validate_policy(candidate: Mapping[str, Any], document: Mapping[str, Any]) -> dict[str, Any]:
    if candidate.get("schema") != SCHEMA or candidate.get("source", {}).get("structure_hash") != document.get("structure_hash"):
        raise ValueError("off-frame policy source/schema mismatch")
    if candidate.get("policy") != POLICY:
        raise ValueError("off-frame policy contains unsafe authorization")
    frame = candidate.get("frame_scope", {})
    if frame.get("entrance_opening_id") is not None or frame.get("entrance_geometry") is not None:
        raise ValueError("off-frame candidate must not invent entrance geometry")
    if candidate.get("status") != "pending_independent_review":
        raise ValueError("off-frame policy prematurely promoted")
    expected = _hash({k: v for k, v in candidate.items() if k != "candidate_hash"})
    if candidate.get("candidate_hash") != expected:
        raise ValueError("off-frame policy hash mismatch")
    return dict(candidate)
