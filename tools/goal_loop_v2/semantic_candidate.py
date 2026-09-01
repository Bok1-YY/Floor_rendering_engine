"""Fail-closed semantic candidate package for all source openings.

Geometry and Gemini observations are deliberately kept separate from source
truth.  This artifact is a review queue, never a promotion or build input.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib, json
from pathlib import Path
from typing import Any, Mapping

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document

SCHEMA = "opening-semantic-candidate-v1"
ADVISORY_FIELDS = {"observed_type", "confidence", "notes", "question_ids", "source"}
DECISION = "unresolved_candidate"

def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()

def _bind(role: str, path: str) -> dict[str, str]:
    p = Path(path).expanduser().resolve(); raw = p.read_bytes()
    if p.suffix.lower() == ".json":
        canonical = _hash(json.loads(raw.decode("utf-8"))); media = "application/json"
    else:
        canonical = hashlib.sha256(raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest(); media = "application/octet-stream"
    return {"role": role, "path": str(p), "file_sha256": hashlib.sha256(raw).hexdigest(), "canonical_sha256": canonical, "media_type": media}

def build_semantic_candidate(document: Mapping[str, Any], opening_evidence: Mapping[str, Any], geometry_evidence: Mapping[str, Mapping[str, Any]], evidence_files: Mapping[str, str] | None = None, gemini_advisories: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    doc = validate_v21_document(document)
    if opening_evidence.get("schema") != "opening-wall-space-evidence-candidate-v1":
        raise ValueError("opening evidence schema mismatch")
    source = {o["id"]: o for o in doc["opening_contract"]["openings"]}
    if set(geometry_evidence) != set(source):
        raise ValueError("geometry evidence must cover exactly all source openings")
    advisories = gemini_advisories or {}
    if not set(advisories).issubset(source):
        raise ValueError("Gemini advisory contains unknown opening")
    rows = []
    for oid in source:
        advisory = deepcopy(dict(advisories.get(oid, {})))
        if set(advisory) - ADVISORY_FIELDS:
            raise ValueError("unsupported Gemini advisory field")
        rows.append({"opening_id": oid, "geometry": deepcopy(dict(geometry_evidence[oid])), "gemini_advisory": advisory, "decision": DECISION, "review_questions": ["Q_TYPE", "Q_HOST_WALL", "Q_SIDE_A", "Q_SIDE_B", "Q_TRAVERSABLE", "Q_HEIGHT"], "semantic_promotion": False, "build_authorized": False})
    result = {"schema": SCHEMA, "source_structure_hash": doc["structure_hash"], "opening_evidence_hash": opening_evidence["candidate_hash"], "evidence_bindings": [] if evidence_files is None else [_bind(k, v) for k, v in sorted(evidence_files.items())], "openings": rows, "status": "pending_independent_review", "semantic_promotion": False, "build_authorized": False, "ready": False, "candidate_hash": "0" * 64}
    result["candidate_hash"] = _hash({k: v for k, v in result.items() if k != "candidate_hash"})
    return validate_semantic_candidate(doc, opening_evidence, result)

def validate_semantic_candidate(document: Mapping[str, Any], opening_evidence: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    doc = validate_v21_document(document)
    required = {"schema", "source_structure_hash", "opening_evidence_hash", "evidence_bindings", "openings", "status", "semantic_promotion", "build_authorized", "ready", "candidate_hash"}
    if set(candidate) != required or candidate["schema"] != SCHEMA: raise ValueError("semantic candidate keys/schema invalid")
    if candidate["source_structure_hash"] != doc["structure_hash"] or candidate["opening_evidence_hash"] != opening_evidence.get("candidate_hash"): raise ValueError("semantic candidate provenance mismatch")
    if candidate["status"] != "pending_independent_review" or any(candidate[k] is not False for k in ("semantic_promotion", "build_authorized", "ready")): raise ValueError("semantic candidate is not fail-closed")
    source_ids = [o["id"] for o in doc["opening_contract"]["openings"]]
    if [r.get("opening_id") for r in candidate["openings"]] != source_ids: raise ValueError("opening coverage/order mismatch")
    for row in candidate["openings"]:
        if row["decision"] != DECISION or row["semantic_promotion"] is not False or row["build_authorized"] is not False: raise ValueError("opening was promoted")
        if set(row["gemini_advisory"]) - ADVISORY_FIELDS: raise ValueError("unbounded Gemini advisory")
        if row["geometry"].get("geometry_finding") in {"confirmed_door", "confirmed_window", "confirmed_portal"}: raise ValueError("geometry finding claims semantics")
    expected = _hash({k: v for k, v in candidate.items() if k != "candidate_hash"})
    if candidate["candidate_hash"] != expected: raise ValueError("semantic candidate hash drift")
    return deepcopy(dict(candidate))

__all__ = ["build_semantic_candidate", "validate_semantic_candidate"]
