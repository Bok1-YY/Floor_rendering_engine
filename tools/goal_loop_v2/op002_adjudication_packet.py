"""Fail-closed OP002 geometry/room-pair adjudication packet."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.opening_side_candidates import build_opening_side_space_candidate
from tools.goal_loop_v2.op002_cut_closure import build_op002_cut_closure_candidate

SCHEMA = "op002-adjudication-packet-v1"
OPENING_ID = "OP002"


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _file_binding(path: str | Path) -> dict[str, str]:
    target = Path(path).resolve()
    raw = target.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    return {
        "path": str(target),
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "canonical_sha256": _hash(value),
    }


def build_op002_adjudication_packet(document: Mapping[str, Any], vertical_evidence_file: str | Path, *, _skip_validate: bool = False) -> dict[str, Any]:
    doc = validate_v21_document(document)
    opening = next(row for row in doc["opening_contract"]["openings"] if row["id"] == OPENING_ID)
    cut_closure = build_op002_cut_closure_candidate(doc)
    side = build_opening_side_space_candidate(doc)
    side_row = next(row for row in side["openings"] if row["opening_id"] == OPENING_ID)
    physical_group = next(group for group in cut_closure["physical_membership"]["anchor_groups"] if "bedroom_01" in group)
    closure_bedroom_group = next(group for group in cut_closure["closure_anchor_groups"] if "bedroom_01" in group)
    closure_main_group = next(group for group in cut_closure["closure_anchor_groups"] if "bedroom_corridor" in group)
    result = {
        "schema": SCHEMA,
        "source_structure_hash": doc["structure_hash"],
        "opening_id": OPENING_ID,
        "vertical_evidence": _file_binding(vertical_evidence_file),
        "cut_closure_candidate_hash": cut_closure["candidate_hash"],
        "side_candidate_hash": side["candidate_hash"],
        "source_candidate_pair": [opening["side_a_space_id"], opening["side_b_space_id"]],
        "review_pair_candidate": ["bedroom_01", "bedroom_corridor"],
        "geometry_findings": {
            "host_atom_id": opening["host"]["owning_wall_atom_id"],
            "registered_segment_m": deepcopy(opening["effective_void"]["segment_m"]),
            "physical_group": physical_group,
            "closure_bedroom_group": closure_bedroom_group,
            "closure_main_group": closure_main_group,
            "bedroom_01_isolated_by_topology_closure": closure_bedroom_group == ["bedroom_01"],
            "bedroom_corridor_present_on_other_side": "bedroom_corridor" in closure_main_group,
            "other_side_is_multi_anchor": len(closure_main_group) > 1,
        },
        "side_ranking": deepcopy(side_row),
        "blockers": [
            "SOURCE_OPENING_STATUS_CANDIDATE",
            "OTHER_SIDE_FACE_MULTI_ANCHOR",
            "ROOM_POLYGONS_NOT_SOURCE_CONFIRMED",
            "GEMINI_COMPLETE_REVIEW_MISSING",
            "HUMAN_REVIEW_PENDING",
        ],
        "gemini_review_status": "advisory_failed_or_missing",
        "human_review_status": "pending",
        "decision": "unresolved_candidate",
        "status": "pending_independent_review",
        "pair_confirmation": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _hash({key: value for key, value in result.items() if key != "candidate_hash"})
    return result if _skip_validate else validate_op002_adjudication_packet(doc, vertical_evidence_file, result)


def validate_op002_adjudication_packet(document: Mapping[str, Any], vertical_evidence_file: str | Path, candidate: Mapping[str, Any]) -> dict[str, Any]:
    doc = validate_v21_document(document)
    if candidate.get("schema") != SCHEMA or candidate.get("opening_id") != OPENING_ID:
        raise ValueError("OP002 adjudication schema/allowlist violation")
    for key in ("pair_confirmation", "semantic_promotion", "build_authorized", "ready"):
        if candidate.get(key) is not False:
            raise ValueError("OP002 adjudication packet was promoted")
    expected = build_op002_adjudication_packet(doc, vertical_evidence_file, _skip_validate=True)
    if dict(candidate) != expected:
        raise ValueError("OP002 adjudication evidence drift")
    return deepcopy(dict(candidate))


__all__ = ["build_op002_adjudication_packet", "validate_op002_adjudication_packet"]
