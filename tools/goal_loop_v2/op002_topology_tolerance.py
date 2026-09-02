"""Source-neutral numerical topology tolerance candidate for OP002."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any, Mapping

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.op002_opening_cut import build_op002_opening_cut_candidate

SCHEMA = "op002-topology-tolerance-candidate-v1"
SELECTED_M = 1e-6
STABLE_RANGE_M = [3e-7, 5e-5]
TRANSITIONS_M = [2.430263013573909e-7, 2.7840782218860394e-7]


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def build_op002_topology_tolerance(document: Mapping[str, Any], *, _skip_validate: bool = False) -> dict[str, Any]:
    doc = validate_v21_document(document)
    cut = build_op002_opening_cut_candidate(doc)
    result = {
        "schema": SCHEMA,
        "source_structure_hash": doc["structure_hash"],
        "opening_cut_candidate_hash": cut["candidate_hash"],
        "opening_id": "OP002",
        "selected_clearance_m": SELECTED_M,
        "validated_stable_range_m": STABLE_RANGE_M,
        "observed_transition_thresholds_m": TRANSITIONS_M,
        "endpoint_perturbation_m": [-0.0293, 0.0, 0.0293],
        "stable_topology": {
            "face_candidate_count": 11,
            "single_anchor_face_count": 10,
            "multi_anchor_face_count": 1,
            "unlabeled_face_count": 0,
            "side_probes_same_face": True,
            "merged_anchor_ids": ["bath", "bedroom_01", "bedroom_corridor", "kitchen", "living_hall", "lobby"],
        },
        "policy_scope": "numerical_topology_only",
        "source_dimension_effect": "none",
        "score_effect": "none",
        "status": "pending_independent_review",
        "source_geometry_confirmation": False,
        "cut_confirmation": False,
        "adjacency_confirmation": False,
        "semantic_promotion": False,
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _hash({key: value for key, value in result.items() if key != "candidate_hash"})
    return result if _skip_validate else validate_op002_topology_tolerance(doc, result)


def validate_op002_topology_tolerance(document: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    doc = validate_v21_document(document)
    expected = deepcopy(dict(candidate))
    if candidate.get("schema") != SCHEMA or candidate.get("source_structure_hash") != doc["structure_hash"]:
        raise ValueError("OP002 topology tolerance source/schema drift")
    for key in ("source_geometry_confirmation", "cut_confirmation", "adjacency_confirmation", "semantic_promotion", "build_authorized", "ready"):
        if candidate.get(key) is not False:
            raise ValueError("OP002 topology tolerance was promoted")
    if candidate.get("selected_clearance_m") != SELECTED_M or candidate.get("validated_stable_range_m") != STABLE_RANGE_M:
        raise ValueError("OP002 topology tolerance policy drift")
    if candidate.get("opening_cut_candidate_hash") != build_op002_opening_cut_candidate(doc)["candidate_hash"]:
        raise ValueError("OP002 opening-cut provenance drift")
    if dict(candidate) != build_op002_topology_tolerance(doc, _skip_validate=True):
        raise ValueError("OP002 topology tolerance evidence drift")
    if candidate.get("candidate_hash") != _hash({key: value for key, value in candidate.items() if key != "candidate_hash"}):
        raise ValueError("OP002 topology tolerance hash drift")
    return expected


__all__ = ["build_op002_topology_tolerance", "validate_op002_topology_tolerance"]
