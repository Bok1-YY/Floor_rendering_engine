from __future__ import annotations

from copy import deepcopy

import pytest

from tests.test_research_structure_v2 import v2_fixture
from tools.goal_loop_v2.endpoint_repair import (
    _target_wall_face,
    apply_endpoint_repair_plan,
    build_endpoint_repair_plan,
    proposal_declared_hash,
)
from tools.goal_loop_v2.geometry_adjudication import proposal_content_hash
from tools.fastloop_research.contract import canonical_json
import hashlib


def _inputs(*, gap=0.02, dangling=False, topology_attack=False):
    document = v2_fixture()
    document["wall_graph"]["branches"][0]["id"] = "A"
    document["wall_graph"]["atoms"][0]["branch_id"] = "A"
    document["wall_graph"]["branches"].append({"id": "B", "centerline_m": [[4.08, -1], [4.08, 1]], "status": "candidate", "evidence_refs": ["VIEW-EVIDENCE"]})
    from tools.fastloop_research.v2_contract import compute_v2_structure_hash
    document["structure_hash"] = compute_v2_structure_hash(document)
    support_x = 4.0 + gap + 0.06
    proposal = {
        "schema": "generic-proposal", "proposal_sha256": "0" * 64,
        "wall_graph": {
            "walls": [
                {"id": "A", "source_centerline_m": [[0, 0], [4, 0]], "proposed_centerline_m": [[0, 0], [4, 0]], "source_centerline_px": [[0, 0], [40, 0]], "proposed_centerline_px": [[0, 0], [40, 0]], "source_centerline_norm": [[0, 0], [400, 0]], "proposed_centerline_norm": [[0, 0], [400, 0]], "nominal_thickness_m": 0.12},
                {"id": "B", "source_centerline_m": [[support_x, -1], [support_x, 1]], "proposed_centerline_m": [[support_x, -1], [support_x, 1]], "source_centerline_px": [[support_x * 10, -10], [support_x * 10, 10]], "proposed_centerline_px": [[support_x * 10, -10], [support_x * 10, 10]], "source_centerline_norm": [[support_x * 100, -100], [support_x * 100, 100]], "proposed_centerline_norm": [[support_x * 100, -100], [support_x * 100, 100]], "nominal_thickness_m": 0.12},
            ],
            "endpoint_diagnostics": [{"wall_id": "A", "endpoint_index": 1, "point_m": [4, 0], "status": "dangling_unresolved" if dangling else "wall_face_termination_candidate", "junction_ids": [], "wall_face_support_candidates": [] if dangling else [{"wall_id": "B", "wall_face_distance_m": gap, "centerline_distance_m": gap + 0.06, "continuous_at_1mm": False}]}],
        },
        "openings": [{"id": "UNCHANGED", "source_segment_m": [[1, 0], [2, 0]], "owning_wall_id_candidate": "A"}],
    }
    if topology_attack:
        proposal["wall_graph"]["walls"].append({"id": "C", "source_centerline_m": [[4.01, -1], [4.01, 1]], "proposed_centerline_m": [[4.01, -1], [4.01, 1]], "source_centerline_px": [[40.1, -10], [40.1, 10]], "proposed_centerline_px": [[40.1, -10], [40.1, 10]], "source_centerline_norm": [[401, -100], [401, 100]], "proposed_centerline_norm": [[401, -100], [401, 100]], "nominal_thickness_m": 0.12})
    proposal["proposal_sha256"] = proposal_declared_hash(proposal)
    adjudication = {"schema": "research-geometry-adjudication-v1", "decisions": [{"issue_id": "ISSUE-ENDPOINTS", "outcome": "unresolved", "evidence": {"check": "wall_face_termination_completeness", "outcome": "unresolved", "unresolved": [{"wall_id": "A", "endpoint_index": 1}]}}]}
    adjudication_hash = hashlib.sha256(canonical_json(adjudication)).hexdigest()
    request = {"schema": "endpoint-repair-request-v1", "source_document_hash": document["structure_hash"], "source_proposal_hash": proposal_content_hash(proposal), "source_adjudication_hash": adjudication_hash, "operation": "move_wall_endpoint", "attempt": 1, "max_attempts": 1, "max_displacement_m": 0.03, "repair_authority": "independent_reference_reviewer", "build_authorized": False}
    return document, proposal, adjudication, request


def test_repair_plan_moves_only_source_registered_endpoint_and_preserves_openings():
    document, proposal, adjudication, request = _inputs()
    plan = build_endpoint_repair_plan(document, proposal, adjudication, request)
    assert len(plan["operations"]) == 1
    operation = plan["operations"][0]
    assert operation["operation"] == "move_wall_endpoint"
    assert operation["supporting_wall_id"] == "B"
    assert operation["displacement_m"] == pytest.approx(0.02)
    assert operation["axis_angle_change_degrees"] <= 0.1
    repaired_proposal, repaired_document = apply_endpoint_repair_plan(document, proposal, adjudication, request, plan)
    assert repaired_proposal["openings"] == proposal["openings"]
    assert repaired_document["adjacency_truth"] == document["adjacency_truth"]
    assert repaired_proposal["proposal_sha256"] == proposal_declared_hash(repaired_proposal)


def test_large_snap_and_dangling_guess_produce_no_operation():
    inputs = _inputs(gap=0.05)
    assert build_endpoint_repair_plan(*inputs)["operations"] == []
    inputs = _inputs(dangling=True)
    plan = build_endpoint_repair_plan(*inputs)
    assert plan["operations"] == [] and "no explicit source target" in plan["skipped"][0]["reason"]


def test_wb006_shape_cannot_snap_diagonally_outside_finite_wb016_rectangle():
    owner = {"proposed_centerline_m": [[4.423994, 15.293456], [4.423994, 5.708684]]}
    support = {"proposed_centerline_m": [[0.014637, 5.62869], [4.39838, 5.62869]], "nominal_thickness_m": 0.12}
    with pytest.raises(ValueError, match="does not intersect finite"):
        _target_wall_face(owner, 1, support)


def test_stale_hash_wrong_support_and_handcrafted_plan_are_rejected():
    document, proposal, adjudication, request = _inputs()
    stale = deepcopy(request)
    stale["source_proposal_hash"] = "f" * 64
    with pytest.raises(ValueError, match="stale"):
        build_endpoint_repair_plan(document, proposal, adjudication, stale)
    plan = build_endpoint_repair_plan(document, proposal, adjudication, request)
    plan["operations"][0]["supporting_wall_id"] = "WRONG"
    with pytest.raises(ValueError, match="independent recomputation"):
        apply_endpoint_repair_plan(document, proposal, adjudication, request, plan)


def test_topology_change_and_off_axis_handcrafted_move_are_rejected():
    inputs = _inputs(topology_attack=True)
    plan = build_endpoint_repair_plan(*inputs)
    assert plan["operations"] == []
    assert "intersection" in plan["skipped"][0]["reason"]
    document, proposal, adjudication, request = _inputs()
    plan = build_endpoint_repair_plan(document, proposal, adjudication, request)
    assert plan["operations"][0]["axis_angle_change_degrees"] == pytest.approx(0.0)
    attacked = deepcopy(plan)
    attacked["operations"][0]["new_metric_point_m"][1] += 0.01
    with pytest.raises(ValueError, match="independent recomputation"):
        apply_endpoint_repair_plan(document, proposal, adjudication, request, attacked)
