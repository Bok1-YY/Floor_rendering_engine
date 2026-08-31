from __future__ import annotations

from copy import deepcopy

from tools.goal_loop_v2.geometry_adjudication import (
    adjudicate_geometry,
    apply_resolved_geometry_decisions,
    assess_crossing_jambs,
    assess_return_wall_faces,
    assess_wall_face_terminations,
    proposal_content_hash,
)
from tests.test_research_structure_v2 import v2_fixture


def _wall(wall_id, a, b, thickness=0.12, height=2.8):
    return {"id": wall_id, "proposed_centerline_m": [a, b], "nominal_thickness_m": thickness, "base_m": 0.0, "height_m": height}


def _request(document, proposal, bindings):
    return {"schema": "geometry-adjudication-request-v1", "source_structure_hash": document["structure_hash"], "proposal_content_hash": proposal_content_hash(proposal), "adjudication_authority": "independent_reference_reviewer", "verdict": "authorize_geometry_check_only", "build_authorized": False, "bindings": bindings}


def test_wall_face_endpoint_adjudication_fails_closed_on_gap_or_dangling():
    proposal = {"wall_graph": {"walls": [_wall("A", [0, 0], [1, 0]), _wall("B", [0, 0], [0, 1])], "endpoint_diagnostics": [
        {"wall_id": "A", "endpoint_index": 0, "status": "wall_face_termination_candidate", "junction_ids": [], "wall_face_support_candidates": [{"wall_id": "B", "continuous_at_1mm": True, "wall_face_distance_m": 0.0}]},
        {"wall_id": "A", "endpoint_index": 1, "status": "dangling_unresolved", "junction_ids": [], "wall_face_support_candidates": []},
    ]}}
    result = assess_wall_face_terminations(proposal)
    assert result["outcome"] == "unresolved"
    assert result["mechanically_supported_count"] == 1


def test_crossing_jamb_requires_real_junction_face_and_host_scoped_void():
    proposal = {
        "wall_graph": {"walls": [_wall("HOST", [0, -2], [0, 2]), _wall("CROSS", [-2, 0], [2, 0])], "junctions": [{"id": "J", "kind": "X", "point_m": [0, 0], "incident_wall_ids": ["HOST", "CROSS"]}]},
        "openings": [{"id": "O", "owning_wall_id_candidate": "HOST", "sill_m": 0.0, "height_m": 2.1, "effective_void": {"segment_m": [[0, 1], [0, 0.06]], "crossing_wall_jamb": {"host_scoped_subtraction": True, "supporting_wall_id": "CROSS", "junction_kind": "X"}}, "jamb_before_support": {}, "jamb_after_support": {"mode": "crossing_wall_jamb", "supporting_wall_id": "CROSS"}}],
    }
    assert assess_crossing_jambs(proposal)[0]["outcome"] == "resolved"
    attacked = deepcopy(proposal)
    attacked["openings"][0]["effective_void"]["crossing_wall_jamb"]["host_scoped_subtraction"] = False
    assert assess_crossing_jambs(attacked)[0]["outcome"] == "unresolved"
    attacked = deepcopy(proposal)
    attacked["wall_graph"]["walls"][1]["nominal_thickness_m"] = 0.04
    assert assess_crossing_jambs(attacked)[0]["outcome"] == "unresolved"
    attacked = deepcopy(proposal)
    attacked["openings"][0]["effective_void"]["segment_m"] = [[0, 0.06], [0, 1.0]]
    assert assess_crossing_jambs(attacked)[0]["outcome"] == "unresolved"
    attacked = deepcopy(proposal)
    attacked["wall_graph"]["walls"][1]["height_m"] = 0.5
    assert assess_crossing_jambs(attacked)[0]["outcome"] == "unresolved"
    attacked = deepcopy(proposal)
    attacked["openings"].append({"id": "CUTTER", "owning_wall_id_candidate": "CROSS", "sill_m": 0.0, "height_m": 2.1, "effective_void": {"segment_m": [[-0.1, 0], [0.1, 0]]}, "jamb_before_support": {}, "jamb_after_support": {}})
    result = next(row for row in assess_crossing_jambs(attacked) if row["opening_id"] == "O")
    assert result["outcome"] == "unresolved" and result["support_cut_by_opening_ids"] == ["CUTTER"]
    attacked = deepcopy(proposal)
    attacked["openings"][0]["effective_void"]["crossing_wall_jamb"]["supporting_wall_id"] = "WB006"
    assert assess_crossing_jambs(attacked)[0]["outcome"] == "unresolved"
    attacked = deepcopy(proposal)
    attacked["openings"][0]["effective_void"]["crossing_wall_jamb"]["junction_kind"] = "T"
    assert assess_crossing_jambs(attacked)[0]["outcome"] == "unresolved"


def test_return_wall_face_preserves_unresolved_gap_and_accepts_continuous_solid():
    proposal = {"wall_graph": {"walls": [_wall("RETURN", [0, -1], [0, 2])]}, "openings": [{"id": "O", "owning_wall_id_candidate": "HOST", "source_segment_m": [[-1, 0], [-0.084, 0]], "sill_m": 0.0, "height_m": 2.1, "jamb_before_support": {}, "jamb_after_support": {"mode": "return_or_cross_wall_face_candidate", "preferred_candidate_wall_id": "RETURN", "candidates": [{"wall_id": "RETURN", "continuous_at_1mm": False, "wall_face_distance_m": 0.024}]}}]}
    assert assess_return_wall_faces(proposal)[0]["outcome"] == "unresolved"
    proposal["openings"][0]["source_segment_m"][1] = [-0.06, 0]
    assert assess_return_wall_faces(proposal)[0]["outcome"] == "resolved"


def test_applying_geometry_decision_never_changes_opening_or_adjacency_semantics():
    document = v2_fixture()
    document["adjacency_truth"]["status"] = "candidate"
    for edge in document["adjacency_truth"]["edges"]:
        edge["status"] = "candidate"
    document["unresolved_issues"] = [{"id": "ISSUE-GEO", "severity": "hard", "category": "opening_contract_capability", "entity_refs": [], "status": "open", "message": "crossing evidence", "blocks_reference_freeze": True, "blocks_build": True, "evidence_refs": ["ANCHOR-SCALE"]}]
    from tools.fastloop_research.v2_contract import compute_v2_structure_hash
    document["structure_hash"] = compute_v2_structure_hash(document)
    proposal = {
        "wall_graph": {"endpoint_diagnostics": [], "walls": [_wall("HOST", [0, -2], [0, 2]), _wall("CROSS", [-2, 0], [2, 0])], "junctions": [{"id": "J", "kind": "X", "point_m": [0, 0], "incident_wall_ids": ["HOST", "CROSS"]}]},
        "openings": [{"id": "O", "owning_wall_id_candidate": "HOST", "sill_m": 0.0, "height_m": 2.1, "effective_void": {"segment_m": [[0, 1], [0, 0.06]], "crossing_wall_jamb": {"host_scoped_subtraction": True, "supporting_wall_id": "CROSS", "junction_kind": "X"}}, "jamb_before_support": {}, "jamb_after_support": {"mode": "crossing_wall_jamb", "supporting_wall_id": "CROSS"}}],
    }
    request = _request(document, proposal, [{"issue_id": "ISSUE-GEO", "check": "crossing_wall_jamb", "opening_id": "O", "jamb_side": "jamb_after_support", "owner_wall_id": "HOST", "supporting_wall_id": "CROSS"}])
    report = adjudicate_geometry(document, proposal, request)
    before_openings, before_adjacency = deepcopy(document["opening_contract"]), deepcopy(document["adjacency_truth"])
    adjudicated, readiness = apply_resolved_geometry_decisions(document, proposal, request, report)
    assert adjudicated["opening_contract"] == before_openings
    assert adjudicated["adjacency_truth"] == before_adjacency
    assert readiness["ready"] is False
    forged = deepcopy(report)
    forged["decisions"][0]["evidence"]["face_error_m"] = 0.5
    import pytest
    with pytest.raises(ValueError, match="independent recomputation"):
        apply_resolved_geometry_decisions(document, proposal, request, forged)


def test_decisions_require_hash_bound_explicit_identity_not_issue_category():
    document = v2_fixture()
    document["unresolved_issues"] = [
        {"id": "ISSUE-BOUND", "severity": "hard", "category": "opening_contract_capability", "entity_refs": [], "status": "open", "message": "bound", "blocks_reference_freeze": True, "blocks_build": True, "evidence_refs": ["ANCHOR-SCALE"]},
        {"id": "ISSUE-SAME-CATEGORY", "severity": "hard", "category": "opening_contract_capability", "entity_refs": [], "status": "open", "message": "must remain untouched", "blocks_reference_freeze": True, "blocks_build": True, "evidence_refs": ["ANCHOR-SCALE"]},
    ]
    from tools.fastloop_research.v2_contract import compute_v2_structure_hash
    document["structure_hash"] = compute_v2_structure_hash(document)
    proposal = {"wall_graph": {"endpoint_diagnostics": [], "walls": [_wall("HOST", [0, -2], [0, 2]), _wall("CROSS", [-2, 0], [2, 0])], "junctions": [{"id": "J", "kind": "X", "point_m": [0, 0], "incident_wall_ids": ["HOST", "CROSS"]}]}, "openings": [{"id": "O", "owning_wall_id_candidate": "HOST", "sill_m": 0.0, "height_m": 2.1, "effective_void": {"segment_m": [[0, 1], [0, 0.06]], "crossing_wall_jamb": {"host_scoped_subtraction": True, "supporting_wall_id": "CROSS", "junction_kind": "X"}}, "jamb_before_support": {}, "jamb_after_support": {"mode": "crossing_wall_jamb", "supporting_wall_id": "CROSS"}}]}
    request = _request(document, proposal, [{"issue_id": "ISSUE-BOUND", "check": "crossing_wall_jamb", "opening_id": "O", "jamb_side": "jamb_after_support", "owner_wall_id": "HOST", "supporting_wall_id": "CROSS"}])
    report = adjudicate_geometry(document, proposal, request)
    assert [row["issue_id"] for row in report["decisions"]] == ["ISSUE-BOUND"]
    attacked = deepcopy(request)
    attacked["proposal_content_hash"] = "f" * 64
    import pytest
    with pytest.raises(ValueError, match="not bound"):
        adjudicate_geometry(document, proposal, attacked)
    semantic = deepcopy(document)
    semantic["unresolved_issues"][0]["category"] = "space_topology"
    semantic["structure_hash"] = compute_v2_structure_hash(semantic)
    semantic_request = _request(semantic, proposal, [{"issue_id": "ISSUE-BOUND", "check": "crossing_wall_jamb", "opening_id": "O", "jamb_side": "jamb_after_support", "owner_wall_id": "HOST", "supporting_wall_id": "CROSS"}])
    with pytest.raises(ValueError, match="incompatible"):
        adjudicate_geometry(semantic, proposal, semantic_request)
    pending = deepcopy(request)
    pending.update(schema="geometry-adjudication-request-candidate-v1", adjudication_authority="pending_independent_reference_reviewer", verdict="pending")
    with pytest.raises(ValueError, match="invalid geometry adjudication request"):
        adjudicate_geometry(document, proposal, pending)
