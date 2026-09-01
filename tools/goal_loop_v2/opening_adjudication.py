"""Fail-closed adjudication packet for high-impact opening evidence.

This module packages geometry findings and compact, independently obtained AI
observations for human/verifier review.  It intentionally cannot promote an
opening, adjacency, or build readiness.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib, json
from pathlib import Path
from typing import Any, Mapping
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document

SCHEMA = "opening-adjudication-candidate-v1"
TARGETS = ("OP001", "OP002", "OP007", "OP008")

def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()

def _binding(role: str, path: str) -> dict[str, str]:
    p = Path(path).resolve(); raw = p.read_bytes()
    canonical = _hash(json.loads(raw.decode("utf-8"))) if p.suffix.lower()==".json" else hashlib.sha256(raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()
    return {"role": role, "path": str(p), "file_sha256": hashlib.sha256(raw).hexdigest(), "canonical_sha256": canonical}

def build_adjudication_candidate(document: Mapping[str, Any], opening_evidence: Mapping[str, Any], geometry_rows: Mapping[str, Mapping[str, Any]], evidence_files: Mapping[str, str] | None = None, gemini_observations: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    doc = validate_v21_document(document)
    if opening_evidence.get("schema") != "opening-wall-space-evidence-candidate-v1": raise ValueError("opening evidence schema mismatch")
    source_ids = {o["id"] for o in doc["opening_contract"]["openings"]}
    rows=[]
    observations = gemini_observations or {}
    for oid in TARGETS:
        if oid not in source_ids or oid not in geometry_rows: raise ValueError(f"missing adjudication target: {oid}")
        geometry = deepcopy(dict(geometry_rows[oid]))
        ai = deepcopy(dict(observations.get(oid, {})))
        # AI is advisory, bounded, and never copied into authoritative fields.
        if set(ai) - {"observed_type", "confidence", "notes", "question_ids"}: raise ValueError("unsupported Gemini observation field")
        rows.append({"opening_id": oid, "geometry": geometry, "gemini_advisory": ai, "review_questions":["Q_OPENING_TYPE","Q_HOST_WALL","Q_SIDE_A","Q_SIDE_B","Q_TRAVERSABLE","Q_HEIGHT"], "decision":"unresolved_candidate","semantic_promotion":False,"build_authorized":False})
    result={"schema":SCHEMA,"source_structure_hash":doc["structure_hash"],"opening_evidence_hash":opening_evidence["candidate_hash"],"evidence_bindings":[] if evidence_files is None else [_binding(k,v) for k,v in sorted(evidence_files.items())],"targets":rows,"status":"pending_independent_review","semantic_promotion":False,"build_authorized":False,"ready":False,"candidate_hash":"0"*64}
    result["candidate_hash"]=_hash({k:v for k,v in result.items() if k!="candidate_hash"})
    validate_adjudication_candidate(doc, opening_evidence, result)
    return result

def validate_adjudication_candidate(document: Mapping[str, Any], opening_evidence: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    doc=validate_v21_document(document)
    required={"schema","source_structure_hash","opening_evidence_hash","evidence_bindings","targets","status","semantic_promotion","build_authorized","ready","candidate_hash"}
    if set(candidate)!=required or candidate["schema"]!=SCHEMA: raise ValueError("adjudication keys/schema invalid")
    if candidate["source_structure_hash"]!=doc["structure_hash"] or candidate["opening_evidence_hash"]!=opening_evidence.get("candidate_hash"): raise ValueError("adjudication provenance mismatch")
    if candidate["status"]!="pending_independent_review" or any(candidate[k] is not False for k in ("semantic_promotion","build_authorized","ready")): raise ValueError("adjudication is not fail-closed")
    if [r.get("opening_id") for r in candidate["targets"]]!=list(TARGETS): raise ValueError("target coverage/order mismatch")
    for row in candidate["targets"]:
        if row["decision"]!="unresolved_candidate" or row["semantic_promotion"] is not False or row["build_authorized"] is not False: raise ValueError("target promoted")
        if set(row["gemini_advisory"]) - {"observed_type","confidence","notes","question_ids"}: raise ValueError("unbounded advisory")
        if row["geometry"].get("geometry_finding") in {"confirmed_door","confirmed_window","confirmed_portal"}: raise ValueError("geometry finding claims semantics")
    if candidate["candidate_hash"]!=_hash({k:v for k,v in candidate.items() if k!="candidate_hash"}): raise ValueError("adjudication hash drift")
    return deepcopy(dict(candidate))

__all__=["build_adjudication_candidate","validate_adjudication_candidate"]
