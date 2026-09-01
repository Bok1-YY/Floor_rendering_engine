"""Auditable S08 registry for assumptions and unresolved source facts.

This sidecar is deliberately non-authorizing: it documents what is *not* known
from the drawing and may only be consumed by research tooling.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "source-assumption-registry-v1"

def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()

def _file_binding(path: str | Path) -> dict[str, str]:
    p = Path(path)
    data = p.read_bytes()
    return {"path": str(p.resolve()), "sha256": hashlib.sha256(data).hexdigest(),
            "canonical_sha256": hashlib.sha256(_canonical(json.loads(data.decode("utf-8"))).encode()).hexdigest(),
            "file_type": "json"}

def build_assumption_registry(document: Mapping[str, Any], source_document: str | Path | None = None) -> dict[str, Any]:
    """Build a complete, conservative registry from a frozen source document."""
    assumptions = ((document.get("assumptions") or {}).get("items") or [])
    items = [dict(item) for item in assumptions]
    ids = {item.get("id") for item in items}
    if "ASSUME-Z-RESEARCH" not in ids:
        items.append({"id":"ASSUME-Z-RESEARCH", "category":"z_geometry", "status":"unverified",
                      "basis":"not_present_in_source", "targets":[{"entity_kind":"bundle","entity_id":None,"field":"wall/opening heights"}],
                      "value":{"wall_height_m":None,"door_head_m":None}, "unit":"m",
                      "evidence_refs":[], "disclosure":"Source is a 2D plan; Z dimensions are not certified.",
                      "build_policy":"research_only"})
    if "ASSUME-OPENING-HEIGHTS-RESEARCH" not in ids:
        items.append({"id":"ASSUME-OPENING-HEIGHTS-RESEARCH", "category":"opening_z_geometry", "status":"unverified",
                      "basis":"not_present_in_source", "targets":[{"entity_kind":"opening","entity_id":"*","field":"sill/head"}],
                      "value":{"sill_m":None,"head_m":None}, "unit":"m", "evidence_refs":[],
                      "disclosure":"Door/window sill and head heights are absent or not source-confirmed.", "build_policy":"research_only"})
    unresolved = [dict(x) for x in (document.get("unresolved_issues") or []) if x.get("status") in {"open", "unverified"}]
    unresolved.append({"id":"UNRESOLVED-OP011", "category":"opening_semantics", "status":"open", "entity_refs":["OP011"],
                       "severity":"hard", "evidence_refs":[], "blocks_build":True,
                       "message":"OP011 remains semantically and geometrically indeterminate; do not invent a door/window or adjacency.",
                       "required_evidence":["source_segment", "host_wall", "side_a_space", "side_b_space", "traversability"]})
    # deterministic de-duplication while retaining the strongest unresolved record
    unresolved = list({x["id"]: x for x in unresolved}.values())
    result = {"schema":SCHEMA, "source": {"structure_hash": document.get("structure_hash")},
              "assumptions": items, "unresolved": sorted(unresolved, key=lambda x:x["id"]),
              "policy":{"research_only":True,"semantic_promotion":False,"build_authorized":False,"ready":False},
              "source_document": _file_binding(source_document) if source_document else None,
              "status":"pending_independent_review", "registry_hash":"0"*64}
    result["registry_hash"] = _sha({k:v for k,v in result.items() if k != "registry_hash"})
    return result

def validate_assumption_registry(registry: Mapping[str, Any], document: Mapping[str, Any]) -> dict[str, Any]:
    if registry.get("schema") != SCHEMA or registry.get("source",{}).get("structure_hash") != document.get("structure_hash"):
        raise ValueError("assumption registry source/schema mismatch")
    if registry.get("policy") != {"research_only":True,"semantic_promotion":False,"build_authorized":False,"ready":False}:
        raise ValueError("assumption registry has unsafe authorization policy")
    if not any(x.get("id") == "ASSUME-Z-RESEARCH" for x in registry.get("assumptions", [])):
        raise ValueError("Z assumption must be explicit")
    if not any(x.get("id") == "UNRESOLVED-OP011" and x.get("status") == "open" for x in registry.get("unresolved", [])):
        raise ValueError("OP011 must remain unresolved")
    expected = _sha({k:v for k,v in registry.items() if k != "registry_hash"})
    if registry.get("registry_hash") != expected:
        raise ValueError("assumption registry hash mismatch")
    return dict(registry)
