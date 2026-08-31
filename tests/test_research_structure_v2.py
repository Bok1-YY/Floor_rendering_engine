from __future__ import annotations

from copy import deepcopy

import pytest

from tools.fastloop_research.v2_contract import (
    V2ContractError,
    assess_v2_build_readiness,
    compute_v2_structure_hash,
    validate_v2_document,
)


def v2_fixture() -> dict:
    h_source, h_pixels, h_view = "a" * 64, "b" * 64, "c" * 64
    confirmed = "confirmed"
    document = {
        "schema": "research-structure-bundle-v2",
        "project": {"project_id": "project-v2", "revision": 1, "sample_id": "fixture-v2"},
        "source_hash": h_source,
        "structure_hash": "0" * 64,
        "source": {
            "schema": "source-provenance-v3",
            "original": {"file_sha256": h_source, "pixel_sha256": h_pixels, "size_px": [100, 100], "exif_orientation": 1},
            "canonical": {"file_sha256": h_view, "pixel_sha256": h_pixels, "size_px": [100, 100], "orientation_policy": "raw_identity", "raw_to_canonical_3x3": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]},
            "views": [{"id": "VIEW-EVIDENCE", "role": "normalized_evidence", "file_sha256": h_view, "pixel_sha256": h_pixels, "size_px": [100, 100], "canonical_to_view_3x3": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}],
            "metric_registration": {"model": "affine-2d", "solver": "exact", "canonical_px_to_metric_3x3": [[0.1, 0, 0], [0, 0.1, 0], [0, 0, 1]], "control_points": [{"id": "CTRL-0", "canonical_px": [0, 0], "metric_m": [0, 0], "evidence_refs": ["VIEW-EVIDENCE"]}, {"id": "CTRL-1", "canonical_px": [10, 0], "metric_m": [1, 0], "evidence_refs": ["VIEW-EVIDENCE"]}, {"id": "CTRL-2", "canonical_px": [0, 10], "metric_m": [0, 1], "evidence_refs": ["VIEW-EVIDENCE"]}], "max_residual_m": 0.0, "tolerance_m": 0.001, "scale_anchor_id": "ANCHOR-SCALE"},
            "anchors": [
                {"id": "ANCHOR-SCALE", "kind": "scale", "geometry": {"space": "canonical_px", "primitive": "segment", "points_px": [[0, 0], [10, 0]]}, "measured_distance_mm": 1000, "status": confirmed, "evidence_asset_id": "VIEW-EVIDENCE", "note": "one metre"},
                {"id": "ANCHOR-ENTRY", "kind": "entrance", "geometry": {"space": "canonical_px", "primitive": "segment", "points_px": [[20, 0], [30, 0]]}, "measured_distance_mm": None, "status": confirmed, "evidence_asset_id": "VIEW-EVIDENCE", "note": "entry"},
            ],
        },
        "outer_boundary": {"polygon_m": [[0, 0], [4, 0], [4, 4], [0, 4]], "status": confirmed, "evidence_refs": ["VIEW-EVIDENCE"]},
        "spaces": [{"id": "SPACE-LIVING", "label": "Living", "point_m": [2, 2], "status": confirmed, "evidence_refs": ["VIEW-EVIDENCE"]}],
        "wall_graph": {
            "version": "atomic-wall-junction-graph-v2",
            "branches": [{"id": "BRANCH-SOUTH", "centerline_m": [[0, 0], [4, 0]], "status": confirmed, "evidence_refs": ["VIEW-EVIDENCE"]}],
            "atoms": [{"id": "WALL-SOUTH", "branch_id": "BRANCH-SOUTH", "branch_interval": [0, 1], "centerline_m": [[0, 0], [4, 0]], "thickness_m": 0.2, "base_m": 0, "height_m": 2.8, "left_space_id": "exterior", "right_space_id": "SPACE-LIVING", "start_node_id": "NODE-0", "end_node_id": "NODE-1", "status": confirmed, "evidence_refs": ["VIEW-EVIDENCE"], "assumption_ids": ["ASSUME-Z"]}],
            "junctions": [
                {"id": "NODE-0", "kind": "endpoint", "axis_point_m": [0, 0], "termination_kind": "exterior_boundary", "incidents": [{"atom_id": "WALL-SOUTH", "end": "start", "role": "terminating", "attachment": "axis", "contact_point_m": [0, 0]}], "solid_union_policy": "cap", "status": confirmed, "evidence_refs": ["VIEW-EVIDENCE"]},
                {"id": "NODE-1", "kind": "endpoint", "axis_point_m": [4, 0], "termination_kind": "exterior_boundary", "incidents": [{"atom_id": "WALL-SOUTH", "end": "end", "role": "terminating", "attachment": "axis", "contact_point_m": [4, 0]}], "solid_union_policy": "cap", "status": confirmed, "evidence_refs": ["VIEW-EVIDENCE"]},
            ],
        },
        "opening_contract": {
            "version": "opening-contract-v2", "minimum_jamb_support_m": 0.05,
            "openings": [{
                "id": "OPEN-ENTRY",
                "source_observation": {"kind": "entrance_symbol", "nominal_segment_m": [[2, 0], [3, 0]], "nominal_width_m": 1.0, "anchor_id": "ANCHOR-ENTRY", "evidence_refs": ["VIEW-EVIDENCE"], "status": confirmed},
                "build_disposition": "cut", "build_kind": "entrance", "owning_wall_atom_id": "WALL-SOUTH",
                "effective_void": {"segment_m": [[2, 0], [3, 0]], "width_m": 1.0, "sill_m": 0, "head_m": 2.1, "host_cut_scope": "owning_wall_atom_only", "status": confirmed},
                "swing_direction": "not_shown", "traversable": True, "side_a_space_id": "exterior", "side_b_space_id": "SPACE-LIVING",
                "jamb_before": {"mode": "same_wall_solid", "supporting_atom_ids": ["WALL-SOUTH"], "junction_id": "NODE-0", "face_distance_m": 2.0, "effective_support_m": 2.0, "evidence_refs": ["VIEW-EVIDENCE"], "status": confirmed},
                "jamb_after": {"mode": "same_wall_solid", "supporting_atom_ids": ["WALL-SOUTH"], "junction_id": "NODE-1", "face_distance_m": 1.0, "effective_support_m": 1.0, "evidence_refs": ["VIEW-EVIDENCE"], "status": confirmed},
                "status": confirmed, "assumption_ids": ["ASSUME-Z"],
            }],
        },
        "adjacency_truth": {"version": "adjacency-truth-v2", "status": confirmed, "entrance_opening_id": "OPEN-ENTRY", "edges": [{"id": "EDGE-ENTRY", "space_a_id": "exterior", "space_b_id": "SPACE-LIVING", "kind": "door", "opening_id": "OPEN-ENTRY", "status": confirmed, "evidence_refs": ["VIEW-EVIDENCE"]}]},
        "assumptions": {"schema": "assumption-registry-v2", "research_only": True, "items": [{"id": "ASSUME-Z", "category": "z_geometry", "targets": [{"entity_kind": "wall_atom", "entity_id": "WALL-SOUTH", "field": "height_m"}], "value": 2.8, "unit": "m", "basis": "human_accepted_research_assumption", "status": "human_accepted", "build_policy": "allow_research_only", "evidence_refs": [], "disclosure": "Research-only wall height"}]},
        "unresolved_issues": [],
    }
    document["structure_hash"] = compute_v2_structure_hash(document)
    return document


def rehash(document: dict) -> dict:
    document["structure_hash"] = compute_v2_structure_hash(document)
    return document


def test_confirmed_v2_document_is_build_ready() -> None:
    document = v2_fixture()
    assert validate_v2_document(document)["structure_hash"] == document["structure_hash"]
    readiness = assess_v2_build_readiness(document)
    assert readiness["ready"] is True
    assert readiness["build_opening_ids"] == ["OPEN-ENTRY"]


def test_candidate_document_is_valid_but_not_build_ready() -> None:
    document = v2_fixture()
    document["outer_boundary"]["status"] = "candidate"
    document["unresolved_issues"] = [{"id": "ISSUE-OUTER", "severity": "hard", "category": "outer_boundary", "entity_refs": [], "status": "open", "message": "Outer boundary still needs review", "blocks_reference_freeze": True, "blocks_build": True, "evidence_refs": ["VIEW-EVIDENCE"]}]
    rehash(document)
    validate_v2_document(document)
    readiness = assess_v2_build_readiness(document)
    assert readiness["ready"] is False
    assert {"outer_boundary_not_confirmed", "ISSUE-OUTER"} <= set(readiness["blocker_ids"])


def test_evidence_only_glazed_interface_never_creates_build_semantics() -> None:
    document = v2_fixture()
    opening = document["opening_contract"]["openings"][0]
    opening.update(build_disposition="evidence_only", build_kind=None, owning_wall_atom_id=None, effective_void=None, swing_direction=None, traversable=False, side_a_space_id=None, side_b_space_id=None, jamb_before=None, jamb_after=None)
    opening["source_observation"]["kind"] = "glazed_interface"
    document["adjacency_truth"] = {"version": "adjacency-truth-v2", "status": "confirmed", "entrance_opening_id": None, "edges": []}
    rehash(document)
    validate_v2_document(document)
    readiness = assess_v2_build_readiness(document)
    assert readiness["ready"] is True
    assert readiness["build_opening_ids"] == []
    assert readiness["evidence_only_opening_ids"] == ["OPEN-ENTRY"]


def test_v2_rejects_hash_drift_duplicate_ids_and_unknown_references() -> None:
    document = v2_fixture()
    document["outer_boundary"]["polygon_m"][0][0] = 0.2
    with pytest.raises(V2ContractError, match="content hash mismatch"):
        validate_v2_document(document)
    document = v2_fixture()
    document["spaces"].append(deepcopy(document["spaces"][0]))
    rehash(document)
    with pytest.raises(V2ContractError, match="duplicate"):
        validate_v2_document(document)
    document = v2_fixture()
    document["wall_graph"]["atoms"][0]["branch_id"] = "MISSING"
    rehash(document)
    with pytest.raises(V2ContractError, match="unknown"):
        validate_v2_document(document)


def test_v2_hash_is_stable_for_entity_and_id_reference_reordering() -> None:
    document = v2_fixture()
    expected = document["structure_hash"]
    document["source"]["anchors"].reverse()
    document["wall_graph"]["junctions"].reverse()
    document["opening_contract"]["openings"][0]["jamb_before"]["evidence_refs"] = ["VIEW-EVIDENCE"]
    assert compute_v2_structure_hash(document) == expected
    validate_v2_document(document)


def test_v2_rejects_evidence_only_as_entrance_and_unknown_assumption_refs() -> None:
    document = v2_fixture()
    opening = document["opening_contract"]["openings"][0]
    opening.update(build_disposition="evidence_only", build_kind=None, owning_wall_atom_id=None, effective_void=None, swing_direction=None, traversable=False, side_a_space_id=None, side_b_space_id=None, jamb_before=None, jamb_after=None)
    document["adjacency_truth"]["edges"] = []
    rehash(document)
    with pytest.raises(V2ContractError, match="cut entrance"):
        validate_v2_document(document)

    document = v2_fixture()
    document["wall_graph"]["atoms"][0]["assumption_ids"] = ["MISSING"]
    rehash(document)
    with pytest.raises(V2ContractError, match="unknown assumption"):
        validate_v2_document(document)


def test_v2_requires_three_non_collinear_registration_controls() -> None:
    document = v2_fixture()
    document["source"]["metric_registration"]["control_points"].pop()
    rehash(document)
    with pytest.raises(V2ContractError, match="at least three"):
        validate_v2_document(document)

    document = v2_fixture()
    document["source"]["metric_registration"]["control_points"][2].update(canonical_px=[20, 0], metric_m=[2, 0])
    rehash(document)
    with pytest.raises(V2ContractError, match="non-collinear"):
        validate_v2_document(document)
