"""Fail-closed S07 adjacency/reachability evidence candidate.

This module records what must be proved for an opening to connect two spaces.
It intentionally does not infer adjacency from labels, proximity, or Gemini
text, and cannot promote the source document or authorize a build.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib, json
from pathlib import Path
from typing import Any, Mapping

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document

def _hash(v: Any) -> str:
    return hashlib.sha256(canonical_json(v)).hexdigest()

def _binding(role: str, path: str) -> dict:
    p = Path(path).expanduser().resolve(); raw = p.read_bytes()
    if p.suffix.lower() == ".json":
        canonical = _hash(json.loads(raw.decode("utf-8"))); media = "application/json"
    else:
        canonical = hashlib.sha256(raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest(); media = "application/octet-stream"
    return {"role": role, "path": str(p), "file_sha256": hashlib.sha256(raw).hexdigest(), "canonical_sha256": canonical, "media_type": media}

def build_adjacency_evidence_candidate(document: Mapping[str, Any], opening_candidate: Mapping[str, Any], source_document_file=None, evidence_files=None) -> dict:
    doc = validate_v21_document(document)
    if opening_candidate.get("schema") != "opening-wall-space-evidence-candidate-v1":
        raise ValueError("opening candidate schema required")
    if opening_candidate.get("source_structure_hash") != doc["structure_hash"]:
        raise ValueError("opening candidate source drift")
    spaces = [{"id": s["id"], "root_status": "unresolved", "path_status": "unresolved"} for s in doc["spaces"]]
    opening_ids = {r["opening_id"] for r in opening_candidate["openings"]}
    edges = []
    for i, row in enumerate(opening_candidate["openings"], 1):
        a, b = row.get("side_a_space_id"), row.get("side_b_space_id")
        edges.append({
            "edge_id": f"EDGE-{row['opening_id']}", "opening_id": row["opening_id"],
            "space_a_id": a, "space_b_id": b,
            "status": "candidate", "traversability_status": "unresolved",
            "host_wall_atom_id": None, "effective_void": None, "path_trace": None,
            "required_evidence": ["opening_endpoints_crop", "host_wall_overlap", "effective_void_or_negative_decision", "bounded_space_a", "bounded_space_b", "barrier_removed_path_trace"],
        })
    roots = [{"root_id": "ROOT-EXTERIOR", "kind": "exterior", "opening_id": None, "status": "unresolved", "path_to_component": None}]
    result = {
        "schema": "adjacency-reachability-evidence-candidate-v1", "source_structure_hash": doc["structure_hash"],
        "source_document": None if source_document_file is None else _binding("source_document", source_document_file),
        "evidence_bindings": [] if evidence_files is None else [_binding(k, v) for k, v in sorted(evidence_files.items())],
        "spaces": spaces, "edges": edges, "roots": roots,
        "coverage": {"source_opening_ids": sorted(opening_ids), "edge_count": len(edges), "space_count": len(spaces)},
        "limitations": {"semantic_promotion": False, "adjacency_confirmation": False, "reachability_confirmation": False, "entrance_confirmation": False, "build": False},
        "status": "pending_independent_review", "semantic_promotion": False, "build_authorized": False, "ready": False, "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _hash({k: v for k, v in result.items() if k != "candidate_hash"})
    validate_adjacency_evidence_candidate(doc, opening_candidate, result)
    return result

def validate_adjacency_evidence_candidate(document: Mapping[str, Any], opening_candidate: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict:
    doc = validate_v21_document(document)
    required = {"schema", "source_structure_hash", "source_document", "evidence_bindings", "spaces", "edges", "roots", "coverage", "limitations", "status", "semantic_promotion", "build_authorized", "ready", "candidate_hash"}
    if not isinstance(candidate, Mapping) or set(candidate) != required: raise ValueError("adjacency candidate keys invalid")
    if candidate["schema"] != "adjacency-reachability-evidence-candidate-v1" or candidate["source_structure_hash"] != doc["structure_hash"]: raise ValueError("adjacency source drift")
    if candidate["status"] != "pending_independent_review" or any(candidate[k] is not False for k in ("semantic_promotion", "build_authorized", "ready")): raise ValueError("adjacency candidate promoted")
    if candidate["limitations"] != {"semantic_promotion": False, "adjacency_confirmation": False, "reachability_confirmation": False, "entrance_confirmation": False, "build": False}: raise ValueError("adjacency limitations leak claims")
    source_ids = {r["opening_id"] for r in opening_candidate["openings"]}
    if set(candidate["coverage"]) != {"source_opening_ids", "edge_count", "space_count"} or set(candidate["coverage"]["source_opening_ids"]) != source_ids: raise ValueError("adjacency coverage drift")
    space_ids = {s["id"] for s in doc["spaces"]}
    if {s.get("id") for s in candidate["spaces"]} != space_ids: raise ValueError("space coverage mismatch")
    if {e.get("opening_id") for e in candidate["edges"]} != source_ids: raise ValueError("edge coverage mismatch")
    for e in candidate["edges"]:
        if e.get("status") != "candidate" or e.get("traversability_status") != "unresolved": raise ValueError("edge promoted")
        if e.get("space_a_id") not in space_ids and e.get("space_a_id") is not None: raise ValueError("dangling space A")
        if e.get("space_b_id") not in space_ids and e.get("space_b_id") is not None: raise ValueError("dangling space B")
        if e.get("host_wall_atom_id") is not None or e.get("effective_void") is not None or e.get("path_trace") is not None: raise ValueError("unverified geometry leaked")
    if len(candidate["edges"]) != candidate["coverage"]["edge_count"] or len(candidate["spaces"]) != candidate["coverage"]["space_count"]: raise ValueError("count drift")
    if len(candidate["roots"]) != 1 or candidate["roots"][0].get("status") != "unresolved" or candidate["roots"][0].get("path_to_component") is not None: raise ValueError("root was confirmed")
    if candidate["candidate_hash"] != _hash({k: v for k, v in candidate.items() if k != "candidate_hash"}): raise ValueError("candidate hash drift")
    return deepcopy(dict(candidate))

__all__ = ["build_adjacency_evidence_candidate", "validate_adjacency_evidence_candidate"]
