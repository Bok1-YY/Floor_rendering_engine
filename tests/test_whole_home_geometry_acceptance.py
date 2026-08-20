from __future__ import annotations

import copy
import json
import math

import pytest

from Floor_engine_Linux import whole_home_geometry as geometry


SOURCE_HASH = "a" * 64
MODEL_HASH = "b" * 64
CAD_HASH = "c" * 64


def _registration(*, grade: str = "vector_authoritative", **overrides):
    payload = {
        "source_type": "cad" if grade == "vector_authoritative" else "raster",
        "source_hash": SOURCE_HASH,
        "input_grade": grade,
        "source_space": "m" if grade == "vector_authoritative" else "source_pixels",
        "cad_units": "m",
        "source_to_canonical": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "canonical_to_model": [[0.01, 0, 2], [0, 0.01, 3], [0, 0, 1]],
        "scale_anchors": [],
    }
    if grade == "raster_human_locked":
        payload["scale_anchors"] = [
            {"start": [10, 10], "end": [210, 10], "actual_length_m": 2.0},
            {"pixel_length": 100, "actual_length_m": 1.0},
        ]
    payload.update(overrides)
    return geometry.SourceRegistration(payload)


def _cad_registration_v2():
    return geometry.SourceRegistration({
        "version": 2,
        "source_type": "cad",
        "source_hash": SOURCE_HASH,
        "input_grade": "vector_authoritative",
        "cad_units": "m",
        "source_to_canonical": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "canonical_xyz_to_model": [
            [1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 8], [0, 0, 0, 1],
        ],
        "axis_mapping": {
            "cad_x": "+model_x", "cad_y": "-model_z", "elevation": "+model_y",
        },
    })


def _wall(**overrides):
    payload = {
        "id": "wall-1",
        "source_representation": "paired_faces",
        "source_entity_handles": ["A1", "A2"],
        "source_segments": [],
        "insert_chain": [],
        "footprint_polygon": [[0, 0], [4, 0], [4, 0.2], [0, 0.2]],
        "centerline": [[0, 0.1], [4, 0.1]],
        "thickness_m": 0.2,
        "thickness_source": "cad_geometry",
        "height_m": 2.8,
        "height_source": "project_default_assumption",
        "review_status": "confirmed",
    }
    payload.update(overrides)
    return geometry.WallAssembly(payload)


def test_redundant_wall_evidence_is_valid_provenance_but_not_geometry():
    evidence = geometry.WallAssembly({
        "id": "duplicate-1",
        "source_representation": "redundant_evidence",
        "review_status": "rejected",
        "source_entity_handles": ["D1"],
        "footprint_polygon": None,
        "centerline": None,
        "thickness_m": None,
        "reason_codes": ["cad_wall_source_redundant_with_accepted_footprint"],
        "redundancy_evidence": {
            "accepted_wall_assembly_id": "wall-1",
            "coverage_ratio": 1.0,
            "uncovered_length_m": 0.0,
            "axis_angle_difference_deg": 0.0,
        },
    })
    assert evidence["review_status"] == "rejected"

    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly({**evidence, "review_status": "confirmed"})
    assert ex.value.code == "redundant_wall_evidence_not_rejected"


def test_detached_site_boundary_evidence_is_strictly_audit_only():
    evidence = {
        "id": "site-boundary-1",
        "source_representation": "detached_site_boundary_evidence",
        "review_status": "rejected",
        "source_entity_handles": ["site-a", "site-b"],
        "reason_codes": [
            "cad_detached_site_boundary_not_physical_space_boundary"],
        "detached_site_boundary_evidence": {
            "method": "cad_detached_site_boundary_component_v1",
            "physical_space_count": 4,
            "original_wall_component_count": 2,
            "component_to_physical_space_distance_m": .8,
            "occupied_x_span_overlap_ratio": 1.0,
            "occupied_z_span_overlap_ratio": 1.0,
            "maximum_component_to_occupied_span_ratio": 2.2,
            "component_boundary_length_m": 48.0,
            "occupied_space_boundary_length_m": 42.0,
            "source_wall_geometry": {
                "source_representation": "paired_faces",
                "centerline": [[-3, -4], [-3, 12]],
                "footprint_polygon": [[-3.1, -4], [-2.9, -4],
                                      [-2.9, 12], [-3.1, 12]],
                "thickness_m": .2,
            },
        },
    }

    validated = geometry.validate_wall_assembly(evidence)
    assert validated["review_status"] == "rejected"

    leaked = copy.deepcopy(evidence)
    leaked["centerline"] = [[-3, -4], [-3, 12]]
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.validate_wall_assembly(leaked)
    assert ex.value.code == "detached_site_boundary_evidence_has_geometry"

    coalesced = copy.deepcopy(evidence)
    proof = coalesced["detached_site_boundary_evidence"]
    proof.update({
        "method": "cad_oversized_coalesced_site_boundary_clip_v1",
        "component_to_physical_space_distance_m": 0.0,
        "maximum_component_to_occupied_span_ratio": 1.8,
        "component_boundary_length_m": 130.0,
        "outside_source_assembly_count": 4,
        "outside_source_assembly_total_length_m": 32.0,
        "outside_source_x_span_overlap_ratio": 1.0,
        "outside_source_z_span_overlap_ratio": 1.0,
    })
    validated_coalesced = geometry.validate_wall_assembly(coalesced)
    assert validated_coalesced["detached_site_boundary_evidence"][
        "method"] == "cad_oversized_coalesced_site_boundary_clip_v1"


def test_nonspace_projected_geometry_evidence_is_strictly_audit_only():
    site_proof = {
        "method": "cad_oversized_coalesced_site_boundary_clip_v1",
        "physical_space_count": 4,
        "original_wall_component_count": 2,
        "occupied_x_span_overlap_ratio": 1.0,
        "occupied_z_span_overlap_ratio": 1.0,
        "maximum_component_to_occupied_span_ratio": 1.8,
        "component_boundary_length_m": 130.0,
        "occupied_space_boundary_length_m": 60.0,
        "outside_source_assembly_count": 3,
        "outside_source_assembly_total_length_m": 32.0,
        "outside_source_x_span_overlap_ratio": 1.0,
        "outside_source_z_span_overlap_ratio": 1.0,
    }
    evidence = {
        "id": "site-detail-1",
        "source_representation": "nonspace_projected_geometry_evidence",
        "review_status": "rejected",
        "source_entity_handles": ["detail-a", "detail-b"],
        "reason_codes": [
            "cad_projected_geometry_not_adjacent_to_physical_space"],
        "nonspace_projected_geometry_evidence": {
            "method":
                "cad_nonspace_geometry_within_oversized_site_plan_v1",
            "source_to_physical_space_distance_m": .8,
            "physical_space_neighbourhood_m": .35,
            "source_length_m": .3,
            "oversized_site_component_proof": site_proof,
            "source_wall_geometry": {
                "source_representation": "paired_faces",
                "centerline": [[-2.8, 12], [-2.5, 12]],
                "footprint_polygon": [
                    [-2.8, 11.96], [-2.5, 11.96],
                    [-2.5, 12.04], [-2.8, 12.04]],
                "thickness_m": .08,
                "length_m": .3,
            },
        },
    }

    validated = geometry.validate_wall_assembly(evidence)
    assert validated["source_representation"] \
        == "nonspace_projected_geometry_evidence"

    leaked = copy.deepcopy(evidence)
    leaked["centerline"] = [[-2.8, 12], [-2.5, 12]]
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.validate_wall_assembly(leaked)
    assert ex.value.code == \
        "nonspace_projected_geometry_evidence_has_geometry"

    too_close = copy.deepcopy(evidence)
    too_close["nonspace_projected_geometry_evidence"][
        "source_to_physical_space_distance_m"] = .349
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.validate_wall_assembly(too_close)
    assert ex.value.code == \
        "nonspace_projected_geometry_evidence_proof_invalid"


def test_projected_detail_requires_strict_topology_invariance_proof():
    payload = {
        "id": "projected-detail-1",
        "source_representation": "projected_detail_evidence",
        "review_status": "rejected",
        "source_entity_handles": ["P1"],
        "footprint_polygon": None,
        "centerline": None,
        "thickness_m": None,
        "reason_codes": ["cad_projected_detail_topology_invariant"],
        "projected_detail_evidence": {
            "method": "cad_projected_detail_topology_invariance_v1",
            "original_space_count": 4,
            "trial_space_count": 4,
            "space_union_iou": .9979,
            "minimum_matched_space_iou": .9968,
            "wall_area_reduction_ratio": .0217,
            "excluded_entity_count": 31,
            "trial_unresolved_wall_assembly_count": 0,
            "source_entity_indexes": [84],
        },
    }

    evidence = geometry.WallAssembly(payload)
    assert evidence["review_status"] == "rejected"
    assert evidence["source_representation"] == "projected_detail_evidence"

    partial = copy.deepcopy(payload)
    partial["projected_detail_evidence"].update({
        "trial_unresolved_wall_assembly_count": 5,
        "trial_unresolved_removed_source_count": 0,
        "trial_new_unresolved_source_count": 0,
    })
    assert geometry.WallAssembly(partial)["review_status"] == "rejected"

    invalid_partial = copy.deepcopy(partial)
    invalid_partial["projected_detail_evidence"][
        "trial_unresolved_removed_source_count"] = 1
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid_partial)
    assert ex.value.code == "projected_detail_evidence_proof_invalid"

    invalid_space = copy.deepcopy(payload)
    invalid_space["projected_detail_evidence"][
        "minimum_matched_space_iou"] = .989
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid_space)
    assert ex.value.code == "projected_detail_evidence_proof_invalid"

    invalid_geometry = copy.deepcopy(payload)
    invalid_geometry["centerline"] = [[0, 0], [1, 0]]
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid_geometry)
    assert ex.value.code == "projected_detail_evidence_has_geometry"


def test_projected_topology_boundary_requires_physical_room_counterfactual():
    payload = {
        "id": "projected-boundary-1",
        "source_representation": "projected_topology_boundary_evidence",
        "review_status": "rejected",
        "source_entity_handles": ["P2"],
        "footprint_polygon": None,
        "centerline": None,
        "thickness_m": None,
        "reason_codes": [
            "cad_projected_topology_boundary_counterfactual"],
        "projected_topology_boundary_evidence": {
            "method":
                "cad_projected_topology_boundary_counterfactual_v1",
            "source_entity_index": 92,
            "source_entity_indexes": [92],
            "safe_excluded_scope_hash": "d" * 64,
            "reference_physical_space_count": 6,
            "reference_space_union_iou": .999,
            "reference_minimum_matched_space_iou": .998,
            "counterfactual_status": "unresolved",
            "counterfactual_reason": "trial_physical_space_count_changed",
            "counterfactual_physical_space_count": 5,
        },
    }

    evidence = geometry.WallAssembly(payload)
    assert evidence["review_status"] == "rejected"
    assert evidence["source_representation"] == \
        "projected_topology_boundary_evidence"

    unchanged = copy.deepcopy(payload)
    unchanged["projected_topology_boundary_evidence"][
        "counterfactual_physical_space_count"] = 6
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(unchanged)
    assert ex.value.code == \
        "projected_topology_boundary_evidence_proof_invalid"

    geometry_payload = copy.deepcopy(payload)
    geometry_payload["centerline"] = [[0, 0], [1, 0]]
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(geometry_payload)
    assert ex.value.code == \
        "projected_topology_boundary_evidence_has_geometry"


def test_projected_geometry_dependency_requires_unresolved_counterfactual():
    payload = {
        "id": "projected-dependency-1",
        "source_representation": "projected_geometry_dependency_evidence",
        "review_status": "rejected",
        "source_entity_handles": ["P3", "P4"],
        "footprint_polygon": None,
        "centerline": None,
        "thickness_m": None,
        "reason_codes": [
            "cad_projected_geometry_dependency_counterfactual"],
        "projected_geometry_dependency_evidence": {
            "method":
                "cad_projected_geometry_dependency_counterfactual_v1",
            "source_entity_index": 93,
            "source_entity_indexes": [93, 94],
            "safe_excluded_scope_hash": "e" * 64,
            "reference_physical_space_count": 6,
            "reference_space_union_iou": .999,
            "reference_minimum_matched_space_iou": .998,
            "counterfactual_status": "unresolved",
            "counterfactual_reason":
                "trial_wall_assembly_decisions_remain_unresolved",
            "counterfactual_trial_unresolved_wall_assembly_count": 3,
            "counterfactual_removed_source_indexes_still_unresolved": [],
            "counterfactual_new_unresolved_source_indexes": [101],
        },
    }

    evidence = geometry.WallAssembly(payload)
    assert evidence["review_status"] == "rejected"

    no_dependency = copy.deepcopy(payload)
    no_dependency["projected_geometry_dependency_evidence"][
        "counterfactual_new_unresolved_source_indexes"] = []
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(no_dependency)
    assert ex.value.code == \
        "projected_geometry_dependency_evidence_proof_invalid"


def test_opening_host_stitch_requires_a_bounded_two_wall_gap_proof():
    payload = {
        "id": "opening-host",
        "source_representation": "opening_host_stitch",
        "review_status": "confirmed",
        "source_entity_handles": ["L1", "L2", "R1", "R2"],
        "footprint_polygon": [[0, 0], [4, 0], [4, .2], [0, .2]],
        "centerline": [[0, .1], [4, .1]],
        "thickness_m": .2,
        "thickness_source": "matched_adjacent_cad_wall_assemblies",
        "height_m": 2.8,
        "height_source": "matched_adjacent_cad_wall_assemblies",
        "opening_host_evidence": {
            "candidate_id": "door-gap",
            "source_wall_assembly_ids": ["left", "right"],
            "opening_axis_cad_m": [[1.03, 0], [1.87, 0]],
            "gap_interval_m": [1.0, 1.9],
            "jamb_offsets_m": [.03, .03],
            "max_jamb_offset_m": .08,
            "max_gap_width_delta_m": .16,
        },
    }

    host = geometry.WallAssembly(payload)
    assert host["source_representation"] == "opening_host_stitch"

    invalid = copy.deepcopy(payload)
    invalid["opening_host_evidence"]["jamb_offsets_m"] = [.03, .081]
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid)
    assert ex.value.code == "opening_host_stitch_proof_invalid"


def test_terminal_open_connection_host_requires_measured_gap_semantics_and_topology_proof():
    payload = {
        "id": "terminal-open-host",
        "source_representation": "terminal_open_connection_host",
        "review_status": "confirmed",
        "source_entity_handles": ["V1", "V2", "H1", "H2", "STORE", "KITCHEN"],
        "footprint_polygon": [[0, 0], [.12, 0], [.12, .66], [0, .66]],
        "centerline": [[.06, 0], [.06, .66]],
        "thickness_m": .12,
        "thickness_source": "matched_terminal_and_transverse_cad_wall_assemblies",
        "height_m": 2.8,
        "height_source": "matched_terminal_and_transverse_cad_wall_assemblies",
        "terminal_open_connection_evidence": {
            "method": "cad_labeled_terminal_open_connection_v1",
            "candidate_id": "store-open",
            "source_wall_assembly_ids": ["terminal", "transverse"],
            "source_handles": ["V1", "V2", "H1", "H2"],
            "opening_axis_cad_m": [[9.8674, 3.2248], [9.8674, 3.8916]],
            "clear_gap_width_m": .6096,
            "terminal_axis_extension_m": .66675,
            "terminal_transverse_angle_deg": 90.0,
            "wall_thickness_samples_m": [.1143, .1143],
            "wall_thickness_spread_m": 0.0,
            "intermediate_wall_coverage_m": 0.0,
            "unique_transverse_support_count": 1,
            "storage_anchor_id": "store-label",
            "storage_anchor_profile": "storage",
            "kitchen_anchor_id": "kitchen-label",
            "kitchen_anchor_profile": "kitchen",
            "topology_space_count_delta": 1,
            "closed_storage_space_area_m2": 1.4,
            "closed_space_semantic_anchor_ids": ["store-label"],
            "thresholds": {
                "min_clear_gap_width_m": .35,
                "max_clear_gap_width_m": 1.5,
                "min_terminal_transverse_angle_deg": 89,
                "max_wall_thickness_spread_m": .04,
                "max_intermediate_wall_coverage_m": .01,
                "required_topology_space_count_delta": 1,
            },
        },
    }

    host = geometry.WallAssembly(payload)
    assert host["source_representation"] == "terminal_open_connection_host"

    invalid = copy.deepcopy(payload)
    invalid["terminal_open_connection_evidence"][
        "topology_space_count_delta"] = 0
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid)
    assert ex.value.code == "terminal_open_connection_host_proof_invalid"

    wrong_semantics = copy.deepcopy(payload)
    wrong_semantics["terminal_open_connection_evidence"][
        "storage_anchor_profile"] = "living_room"
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(wrong_semantics)
    assert ex.value.code == "terminal_open_connection_host_proof_invalid"


def test_opening_axis_wall_evidence_is_rejected_provenance_only():
    evidence = {
        "version": 1,
        "id": "threshold-evidence",
        "source_representation": "opening_evidence",
        "resolved_as": "opening_evidence",
        "review_status": "rejected",
        "source_entity_handles": ["source-threshold"],
        "source_centerline": [[0.0, 0.0], [1.2, 0.0]],
        "footprint_polygon": None,
        "centerline": None,
        "thickness_m": None,
        "reason_codes": ["cad_wall_source_resolved_as_opening_evidence"],
        "production_blockers": [],
        "opening_evidence": {
            "candidate_id": "door-1",
            "accepted_wall_assembly_id": "host-1",
            "source_coverage_ratio": 1.0,
            "opening_axis_coverage_ratio": 1.0,
            "maximum_distance_m": 0.0,
            "axis_angle_difference_deg": 0.0,
            "length_difference_m": 0.0,
            "thresholds": {
                "minimum_bidirectional_coverage_ratio": .995,
                "maximum_axis_distance_m": .015,
                "maximum_angle_difference_deg": 1.0,
                "maximum_length_difference_m": .024,
            },
        },
    }

    validated = geometry.validate_wall_assembly(evidence)
    assert validated["source_representation"] == "opening_evidence"
    assert validated["review_status"] == "rejected"

    invalid = copy.deepcopy(evidence)
    invalid["opening_evidence"]["source_coverage_ratio"] = .994
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.validate_wall_assembly(invalid)
    assert ex.value.code == "opening_wall_evidence_proof_invalid"

    owned = copy.deepcopy(evidence)
    owned["source_entity_handles"] = ["door-glyph"]
    owned["opening_evidence"] = {
        "method": "accepted_opening_source_handle_ownership_v1",
        "candidate_id": "door-1", "accepted_wall_assembly_id": "host-1",
        "owned_source_handles": ["door-glyph"], "source_length_m": .115,
        "opening_axis_cad_m": [[100, -20], [100.813, -20]],
        "maximum_source_length_m": .30,
    }
    assert geometry.validate_wall_assembly(owned)["review_status"] == "rejected"

    contained = copy.deepcopy(evidence)
    contained["opening_evidence"] = {
        "method": "accepted_opening_contained_threshold_axis_v1",
        "candidate_id": "door-1", "accepted_wall_assembly_id": "host-1",
        "source_coverage_ratio": .96111111,
        "opening_axis_coverage_ratio": 1.0,
        "maximum_distance_m": .02,
        "maximum_lateral_offset_m": 0.0,
        "source_axis_overhang_m": [.02, .015],
        "source_length_m": .9, "opening_axis_length_m": .865,
        "axis_angle_difference_deg": 0.0, "length_difference_m": .035,
        "thresholds": {"minimum_opening_axis_coverage_ratio": .995},
    }
    assert geometry.validate_wall_assembly(contained)[
        "review_status"] == "rejected"

    excessive_overhang = copy.deepcopy(contained)
    excessive_overhang["opening_evidence"][
        "source_axis_overhang_m"] = [.061, 0.0]
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.validate_wall_assembly(excessive_overhang)
    assert ex.value.code == "opening_wall_evidence_proof_invalid"

    parallel_face = copy.deepcopy(evidence)
    parallel_face["source_centerline"] = [[0.0, .1143], [1.2192, .1143]]
    parallel_face["opening_evidence"] = {
        "method": "accepted_opening_parallel_wall_face_v1",
        "candidate_id": "window-1",
        "accepted_wall_assembly_id": "window-host-1",
        "opening_axis_cad_m": [[100.0, -20.0], [101.2192, -20.0]],
        "source_axis_model_m": [[0.0, .1143], [1.2192, .1143]],
        "host_wall_thickness_m": .2286,
        "expected_half_thickness_m": .1143,
        "measured_lateral_offset_m": .1143,
        "lateral_offset_spread_m": 0.0,
        "half_thickness_offset_delta_m": 0.0,
        "source_axial_coverage_ratio": 1.0,
        "opening_axis_axial_coverage_ratio": 1.0,
        "source_length_m": 1.2192,
        "opening_axis_length_m": 1.2192,
        "axis_angle_difference_deg": 0.0,
        "length_difference_m": 0.0,
        "degenerate_return_path_evidence": {
            "method": "cad_closed_two_point_return_path_v1",
            "unique_point_count": 2,
            "unique_axis_model_m": [[0.0, .1143], [1.2192, .1143]],
            "unique_axis_length_m": 1.2192,
            "source_path_length_m": 2.4384,
            "return_length_ratio": 2.0,
        },
        "thresholds": {
            "minimum_bidirectional_axial_coverage_ratio": .995,
            "maximum_half_thickness_offset_delta_m": .02,
        },
    }
    assert geometry.validate_wall_assembly(parallel_face)[
        "review_status"] == "rejected"

    invalid_parallel_face = copy.deepcopy(parallel_face)
    invalid_parallel_face["opening_evidence"][
        "measured_lateral_offset_m"] = .15
    invalid_parallel_face["opening_evidence"][
        "half_thickness_offset_delta_m"] = .0357
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.validate_wall_assembly(invalid_parallel_face)
    assert ex.value.code == "opening_wall_evidence_proof_invalid"

    companion_rail = copy.deepcopy(evidence)
    companion_rail["source_centerline"] = [[0.0, -.045], [.8, -.045]]
    companion_rail["opening_evidence"] = {
        "method": "accepted_opening_frame_companion_rail_v1",
        "candidate_id": "window-frame",
        "accepted_wall_assembly_id": "window-host",
        "opening_axis_cad_m": [[100.0, -20.0], [100.8, -20.0]],
        "source_axis_model_m": [[0.0, -.045], [.8, -.045]],
        "source_frame_bbox_model_m": [0.0, -.115, .8, .115],
        "frame_short_span_m": .23,
        "source_axial_coverage_ratio": 1.0,
        "opening_axis_axial_coverage_ratio": 1.0,
        "measured_lateral_offset_m": .045,
        "lateral_offset_spread_m": 0.0,
        "axis_angle_difference_deg": 0.0,
        "source_length_m": .8,
        "opening_axis_length_m": .8,
        "frame_geometry": {
            "long_rail_count": 2, "cross_member_count": 2,
            "opposite_wall_face_support": True,
        },
        "thresholds": {
            "minimum_bidirectional_axial_coverage_ratio": .995,
        },
    }
    assert geometry.validate_wall_assembly(companion_rail)[
        "review_status"] == "rejected"

    outside_frame = copy.deepcopy(companion_rail)
    outside_frame["opening_evidence"]["source_axis_model_m"] = [
        [0.0, -.25], [.8, -.25]]
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.validate_wall_assembly(outside_frame)
    assert ex.value.code == "opening_wall_evidence_proof_invalid"


@pytest.mark.parametrize("support_method,support,extra", [
    (
        "single_accepted_wall_face_cap_v1",
        {
            "wall_assembly_id": "wall-cap-host",
            "length_difference_m": 0.0,
            "axis_angle_difference_deg": 90.0,
            "axis_endpoint_to_source_midpoint_m": .1,
            "expected_half_thickness_offset_m": .1,
        },
        {},
    ),
    (
        "accepted_opening_axis_endpoint_jamb_v1",
        {
            "wall_assembly_id": "door-host",
            "candidate_id": "door-candidate",
            "axis_angle_difference_deg": 90.0,
            "endpoint_distance_m": 0.0,
        },
        {"source_length_m": .2286},
    ),
])
def test_terminal_junction_evidence_contract_accepts_strict_geometric_proofs(
        support_method, support, extra):
    payload = {
        "id": "terminal-cap",
        "source_representation": "junction_evidence",
        "resolved_as": "junction_evidence",
        "review_status": "rejected",
        "source_entity_handles": ["cap-source"],
        "source_centerline": [[0, 0], [.2286, 0]],
        "footprint_polygon": None,
        "centerline": None,
        "thickness_m": None,
        "reason_codes": ["cad_wall_source_is_transverse_cap_or_junction"],
        "production_blockers": [],
        "junction_evidence": {
            "support_method": support_method,
            "supports": [support],
            "coverage_ratio": 0.0,
            "uncovered_length_m": .2286,
            **extra,
        },
    }

    validated = geometry.validate_wall_assembly(payload)

    assert validated["source_representation"] == "junction_evidence"


def test_global_corner_chain_junction_contract_requires_complete_topology_proof():
    payload = {
        "id": "global-corner-chain",
        "source_representation": "junction_evidence",
        "resolved_as": "junction_evidence",
        "review_status": "rejected",
        "source_entity_handles": ["corner-h", "corner-v"],
        "source_centerline": [[0, 0], [.5, 0]],
        "footprint_polygon": None,
        "centerline": None,
        "thickness_m": None,
        "reason_codes": ["cad_wall_source_is_transverse_cap_or_junction"],
        "production_blockers": [],
        "junction_evidence": {
            "support_method": "proved_global_topology_corner_chain_v1",
            "supports": [
                {"endpoint_index": 0, "wall_assembly_id": "wall-left",
                 "endpoint_distance_m": .01,
                 "source_to_support_axis_angle_difference_deg": 1.2},
                {"endpoint_index": 1, "wall_assembly_id": "wall-upper",
                 "endpoint_distance_m": .02,
                 "source_to_support_axis_angle_difference_deg": 1.3},
            ],
            "chain_source_handles": ["corner-h", "corner-v"],
            "shared_endpoint_distance_m": 0.0,
            "axis_angle_difference_deg": 87.75,
            "source_wall_mask_coverage_ratios": [1.0, .995],
            "global_topology_hash": "d" * 64,
            "coverage_ratio": 0.0,
            "uncovered_length_m": .9,
        },
    }

    assert geometry.validate_wall_assembly(payload)["review_status"] == "rejected"

    invalid = copy.deepcopy(payload)
    invalid["junction_evidence"]["source_wall_mask_coverage_ratios"][1] = .994
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.validate_wall_assembly(invalid)
    assert ex.value.code == "junction_wall_evidence_proof_invalid"


def test_global_corner_face_extension_contract_requires_measured_support_face():
    payload = {
        "id": "terminal-face-extension",
        "source_representation": "junction_evidence",
        "resolved_as": "junction_evidence",
        "review_status": "rejected",
        "source_entity_handles": ["extension-face"],
        "source_centerline": [[1, .1], [1.4, .1]],
        "footprint_polygon": None, "centerline": None, "thickness_m": None,
        "reason_codes": ["cad_wall_source_is_transverse_cap_or_junction"],
        "production_blockers": [],
        "junction_evidence": {
            "support_method": "accepted_wall_face_global_corner_extension_v1",
            "supports": [{
                "wall_assembly_id": "accepted-wall",
                "source_to_support_axis_angle_difference_deg": 0.0,
                "support_wall_thickness_m": .2,
                "measured_face_offset_m": .1,
                "expected_half_thickness_offset_m": .1,
                "face_offset_delta_m": 0.0,
                "axial_gap_m": 0.0,
                "support_source_endpoint_index": 0,
                "support_endpoint_distance_m": 0.0,
                "terminal_source_endpoint_index": 1,
                "terminal_global_boundary_distance_m": 0.0,
                "terminal_forward_outside_samples": [True, True],
                "terminal_normal_inside_sample_count": 1,
            }],
            "source_length_m": .4,
            "source_wall_mask_coverage_ratio": 1.0,
            "source_wall_mask_boundary_coverage_ratio": 1.0,
            "global_topology_hash": "f" * 64,
            "coverage_ratio": 0.0,
            "uncovered_length_m": .4,
        },
    }

    assert geometry.validate_wall_assembly(payload)["review_status"] == "rejected"

    invalid = copy.deepcopy(payload)
    invalid["junction_evidence"]["supports"][0]["face_offset_delta_m"] = .021
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.validate_wall_assembly(invalid)
    assert ex.value.code == "junction_wall_evidence_proof_invalid"


def test_collinear_face_continuation_requires_measured_overlap_and_two_terminals():
    payload = {
        "id": "wall-continuation",
        "source_representation": "collinear_face_continuation",
        "resolved_as": "collinear_face_continuation",
        "review_status": "accepted",
        "source_entity_handles": ["pending-face", "mate-face"],
        "footprint_polygon": [[0, 0], [.2, 0], [.2, 1], [0, 1]],
        "centerline": [[.1, 0], [.1, 1]],
        "thickness_m": .2,
        "thickness_source": "matched_staggered_cad_wall_faces",
        "height_m": 2.8,
        "height_source": "project_default_assumption",
        "collinear_face_continuation_evidence": {
            "method": "bounded_staggered_paired_faces_v1",
            "source_wall_assembly_id": "source-wall",
            "continuation_face_handle": "continuation-face",
            "mate_face_handle": "mate-face",
            "face_separation_m": .2,
            "wall_thickness_m": .2,
            "continuation_face_gap_m": .2,
            "continuation_face_collinear_distance_m": 0.0,
            "projected_overlap_length_m": .8,
            "projected_overlap_ratio": .8,
            "occupied_overlap_length_m": 0.0,
            "terminal_supports": [{
                "wall_assembly_id": "lower", "axis_angle_difference_deg": 90,
                "axis_extension_m": .1, "axis_extension_limit_m": .12,
                "support_axis_extension_m": .1,
                "support_axis_extension_limit_m": .12,
            }, {
                "wall_assembly_id": "upper", "axis_angle_difference_deg": 90,
                "axis_extension_m": .1, "axis_extension_limit_m": .12,
                "support_axis_extension_m": .1,
                "support_axis_extension_limit_m": .12,
            }],
        },
    }

    validated = geometry.validate_wall_assembly(payload)

    assert validated["review_status"] == "confirmed"
    invalid = copy.deepcopy(payload)
    invalid["collinear_face_continuation_evidence"][
        "terminal_supports"][1]["wall_assembly_id"] = "lower"
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.validate_wall_assembly(invalid)
    assert ex.value.code == "collinear_face_continuation_proof_invalid"


def test_window_frame_host_requires_matching_rails_and_covering_source_face():
    payload = {
        "id": "window-host",
        "source_representation": "window_frame_host_extension",
        "review_status": "confirmed",
        "source_entity_handles": ["outer-face", "inner-face"],
        "footprint_polygon": [[0, 0], [3.2, 0], [3.2, .2], [0, .2]],
        "centerline": [[0, .1], [3.2, .1]],
        "thickness_m": .2,
        "thickness_source": "window_frame_rail_spacing_and_source_wall_assembly",
        "height_m": 2.8,
        "height_source": "matched_source_wall_assembly",
        "window_frame_host_evidence": {
            "candidate_id": "window-overlay",
            "source_wall_assembly_id": "wall-source",
            "source_face_handle": "outer-face",
            "opening_source_handles": ["rail-a", "rail-b", "jamb-a", "jamb-b"],
            "opening_axis_cad_m": [[100.8, -20], [102.4, -20]],
            "frame_rail_separation_m": .2,
            "wall_thickness_m": .2,
            "opening_overlap_m": 1.6,
            "source_face_interval_m": [0, 3.2],
            "host_interval_m": [0, 3.2],
            "max_axis_offset_m": .005,
            "max_thickness_delta_m": .005,
            "max_source_face_jamb_m": .08,
        },
    }

    host = geometry.WallAssembly(payload)
    assert host["source_representation"] == "window_frame_host_extension"

    invalid = copy.deepcopy(payload)
    invalid["window_frame_host_evidence"]["frame_rail_separation_m"] = .21
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid)
    assert ex.value.code == "window_frame_host_proof_invalid"


def test_repeated_window_frame_host_requires_unique_matching_reference_window():
    payload = {
        "id": "repeated-window-host",
        "source_representation": "repeated_window_frame_opening_host",
        "review_status": "confirmed",
        "source_entity_handles": ["c1", "c2", "c3", "c4", "c5", "c6"],
        "footprint_polygon": [[-.12, 1.3], [.12, 1.3], [.12, 1.85], [-.12, 1.85]],
        "centerline": [[0, 1.311], [0, 1.844]],
        "thickness_m": .24,
        "thickness_source": "matched_repeated_window_wall_assembly",
        "height_m": 2.8,
        "height_source": "matched_reference_window_wall_assembly",
        "repeated_window_frame_opening_evidence": {
            "method": "cad_repeated_collinear_window_frame_host_v1",
            "kind": "window", "candidate_id": "window-current",
            "opening_source_handles": ["c1", "c2", "c3", "c4", "c5", "c6"],
            "opening_axis_cad_m": [[100, -18.689], [100, -18.156]],
            "long_rail_count": 3, "cross_member_count": 2,
            "wall_mask_endpoint_distance_m": [0.0, 0.0],
            "wall_endpoint_support_distance_m": [.16, .16],
            "reference_candidate_id": "window-reference",
            "reference_wall_assembly_id": "reference-wall",
            "reference_opening_source_handles": ["r1", "r2", "r3", "r4", "r5", "r6"],
            "reference_axis_cad_m": [[100, -20], [100, -19.467]],
            "reference_wall_thickness_m": .24,
            "axis_angle_difference_deg": 0.0,
            "axis_transverse_offset_m": 0.0,
            "opening_width_difference_m": 0.0,
            "frame_rail_separation_difference_m": 0.0,
            "axis_interval_gap_m": .778,
        },
    }

    assert geometry.WallAssembly(payload)["review_status"] == "confirmed"

    invalid = copy.deepcopy(payload)
    invalid["repeated_window_frame_opening_evidence"][
        "axis_transverse_offset_m"] = .006
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid)
    assert ex.value.code == "repeated_window_frame_opening_host_proof_invalid"


def test_window_frame_geometry_can_measure_a_host_inside_an_intentional_wall_gap():
    payload = {
        "id": "window-frame-gap-host",
        "source_representation": "frame_geometry_opening_host",
        "review_status": "confirmed",
        "source_entity_handles": ["rail-a", "rail-b", "jamb-a", "jamb-b"],
        "footprint_polygon": [[1, -.12], [2, -.12], [2, .12], [1, .12]],
        "centerline": [[1, 0], [2, 0]],
        "thickness_m": .24,
        "thickness_source": "cad_window_frame_rail_spacing",
        "height_m": 2.8,
        "height_source": "project_default_assumption",
        "frame_geometry_opening_evidence": {
            "method": "cad_window_frame_measured_host_v1",
            "kind": "window", "candidate_id": "window-gap",
            "opening_source_handles": ["rail-a", "rail-b", "jamb-a", "jamb-b"],
            "opening_axis_cad_m": [[101, -20], [102, -20]],
            "opening_width_m": 1.0,
            "frame_rail_separation_m": .24,
            "signed_wall_face_offsets_m": [-.11, -.11, .13],
            "long_rail_count": 4, "cross_member_count": 2,
            "interior_wall_overlap_ratio": 1.0,
            "wall_endpoint_support_distance_m": [.10, .10],
            "wall_mask_endpoint_distance_m": [0, 0],
            "thresholds": {},
        },
    }

    host = geometry.WallAssembly(payload)
    assert host["source_representation"] == "frame_geometry_opening_host"

    invalid = copy.deepcopy(payload)
    invalid["frame_geometry_opening_evidence"][
        "wall_mask_endpoint_distance_m"] = [.151, 0]
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid)
    assert ex.value.code == "frame_geometry_opening_host_proof_invalid"


def test_sparse_window_frame_host_requires_two_wall_faces_per_side():
    payload = {
        "id": "sparse-window-frame-host",
        "source_representation": "frame_geometry_opening_host",
        "review_status": "confirmed",
        "source_entity_handles": ["rail-a", "rail-b"],
        "footprint_polygon": [[1, -.12], [2, -.12], [2, .12], [1, .12]],
        "centerline": [[1, 0], [2, 0]],
        "thickness_m": .24,
        "thickness_source": "cad_sparse_frame_supported_wall_face_span",
        "height_m": 2.8,
        "height_source": "project_default_assumption",
        "frame_geometry_opening_evidence": {
            "method": "cad_sparse_window_frame_wall_face_host_v1",
            "kind": "window", "candidate_id": "sparse-window-gap",
            "opening_source_handles": ["rail-a", "rail-b"],
            "opening_axis_cad_m": [[101, -20], [102, -20]],
            "original_frame_axis_cad_m": [[101, -20.01], [102, -20.01]],
            "opening_width_m": 1.0,
            "supported_wall_face_span_m": .24,
            "wall_band_midpoint_offset_m": .01,
            "signed_wall_face_offsets_m": [-.11, -.11, .13, .13],
            "negative_wall_face_support_count": 2,
            "positive_wall_face_support_count": 2,
            "source_row_count": 2,
            "long_rail_count": 2, "cross_member_count": 0,
            "interior_wall_overlap_ratio": 0.0,
            "wall_endpoint_support_distance_m": [.04, .04],
            "wall_mask_endpoint_distance_m": [.01, .01],
            "canonical_wall_mask_endpoint_distance_m": [0, 0],
            "thresholds": {},
        },
    }

    host = geometry.WallAssembly(payload)
    assert host["thickness_m"] == pytest.approx(.24)

    invalid = copy.deepcopy(payload)
    invalid["frame_geometry_opening_evidence"][
        "positive_wall_face_support_count"] = 1
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid)
    assert ex.value.code == "frame_geometry_opening_host_proof_invalid"


def test_root_window_frame_host_requires_expanded_geometry_and_measured_face_span():
    payload = {
        "id": "root-window-frame-host",
        "source_representation": "frame_geometry_opening_host",
        "review_status": "confirmed",
        "source_entity_handles": ["window-insert"],
        "footprint_polygon": [[1, -.075], [2, -.075], [2, .075], [1, .075]],
        "centerline": [[1, 0], [2, 0]],
        "thickness_m": .15,
        "thickness_source": "cad_root_frame_supported_wall_face_span",
        "height_m": 2.8,
        "height_source": "project_default_assumption",
        "frame_geometry_opening_evidence": {
            "method": "cad_root_window_frame_wall_face_host_v1",
            "kind": "window", "candidate_id": "root-window-gap",
            "opening_source_handles": ["window-insert"],
            "opening_axis_cad_m": [[101, 0], [102, 0]],
            "original_frame_axis_cad_m": [[101, 0], [102, 0]],
            "opening_width_m": 1.0,
            "supported_wall_face_span_m": .15,
            "wall_band_midpoint_offset_m": 0.0,
            "signed_wall_face_offsets_m": [-.075, -.075, .075, .075],
            "negative_wall_face_support_count": 2,
            "positive_wall_face_support_count": 2,
            "source_row_count": 5,
            "long_rail_count": 6, "cross_member_count": 6,
            "frame_short_span_m": .15,
            "interior_wall_overlap_ratio": 0.0,
            "wall_endpoint_support_distance_m": [.075, .075],
            "wall_mask_endpoint_distance_m": [0, 0],
            "canonical_wall_mask_endpoint_distance_m": [0, 0],
            "thresholds": {},
        },
    }

    assert geometry.WallAssembly(payload)["review_status"] == "confirmed"

    invalid = copy.deepcopy(payload)
    invalid["frame_geometry_opening_evidence"]["source_row_count"] = 3
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid)
    assert ex.value.code == "frame_geometry_opening_host_proof_invalid"

    invalid_thickness = copy.deepcopy(payload)
    invalid_thickness["thickness_m"] = .10
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid_thickness)
    assert ex.value.code == "frame_geometry_opening_host_proof_invalid"


def test_door_swing_host_requires_two_matching_jamb_cross_sections():
    payload = {
        "id": "door-swing-host",
        "source_representation": "door_swing_geometry_opening_host",
        "review_status": "confirmed", "source_entity_handles": ["door-arc"],
        "footprint_polygon": [[1, -.1], [1.8, -.1], [1.8, .1], [1, .1]],
        "centerline": [[1, 0], [1.8, 0]], "thickness_m": .2,
        "thickness_source": "cad_door_jamb_global_wall_cross_sections",
        "height_m": 2.8, "height_source": "project_default_assumption",
        "door_swing_geometry_opening_evidence": {
            "method": "cad_door_swing_unique_jamb_host_v1",
            "kind": "door", "candidate_id": "door-gap",
            "opening_source_handles": ["door-arc"],
            "opening_axis_cad_m": [[101, -20], [101.8, -20]],
            "opening_width_m": .8, "axis_candidate_count": 2,
            "viable_axis_count": 1, "selected_axis_source": "candidate_1",
            "wall_mask_endpoint_distance_m": [0, 0],
            "jamb_cross_section_width_m": [.2, .2],
            "jamb_sample_outward_offset_m": [.03, .03],
            "source_reason_codes": ["circular_swing_arc", "radial_door_leaf",
                                    "wall_network_supported"],
            "thresholds": {},
        },
    }

    host = geometry.WallAssembly(payload)
    assert host["source_representation"] == "door_swing_geometry_opening_host"

    invalid = copy.deepcopy(payload)
    invalid["door_swing_geometry_opening_evidence"][
        "jamb_cross_section_width_m"] = [.2, .241]
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid)
    assert ex.value.code == "door_swing_geometry_opening_host_proof_invalid"


def test_parallel_leaf_without_arc_host_requires_three_rail_source_proof():
    leaf_source = {
        "method": "cad_parallel_door_leaf_without_arc_v1",
        "source_row_count": 3, "parallel_rail_count": 3,
        "leaf_length_m": .9, "leaf_length_spread_m": .005,
        "leaf_angle_spread_deg": .2,
        "hinge_endpoint_cluster_radius_m": .02,
        "free_endpoint_cluster_radius_m": .02,
        "hinge_wall_distance_m": .1,
        "free_endpoint_wall_distance_m": .65,
        "selected_wall_face_separation_m": .2,
        "axis_candidates": [
            {"axis_segment_cad_m": [[101, 0], [101.9, 0]]},
            {"axis_segment_cad_m": [[100.1, 0], [101, 0]]},
        ],
    }
    payload = {
        "id": "door-leaf-host",
        "source_representation": "door_swing_geometry_opening_host",
        "review_status": "confirmed",
        "source_entity_handles": ["leaf-a", "leaf-b", "leaf-c"],
        "footprint_polygon": [[1, -.1], [1.9, -.1], [1.9, .1], [1, .1]],
        "centerline": [[1, 0], [1.9, 0]], "thickness_m": .2,
        "thickness_source": "cad_door_jamb_global_wall_cross_sections",
        "height_m": 2.8, "height_source": "project_default_assumption",
        "door_swing_geometry_opening_evidence": {
            "method": "cad_door_leaf_unique_jamb_host_v1",
            "kind": "door", "candidate_id": "door-leaf-gap",
            "opening_source_handles": ["leaf-a", "leaf-b", "leaf-c"],
            "opening_axis_cad_m": [[101, 0], [101.9, 0]],
            "opening_width_m": .9, "axis_candidate_count": 2,
            "viable_axis_count": 1, "selected_axis_source": "primary",
            "wall_mask_endpoint_distance_m": [0, 0],
            "jamb_cross_section_width_m": [.2, .2],
            "jamb_sample_outward_offset_m": [.03, .03],
            "source_reason_codes": [
                "parallel_door_leaf_rails", "hinge_endpoint_wall_supported",
                "swing_leaf_without_arc", "wall_network_supported",
            ],
            "parallel_leaf_without_arc_evidence": leaf_source,
            "thresholds": {},
        },
    }

    assert geometry.WallAssembly(payload)["review_status"] == "confirmed"

    invalid = copy.deepcopy(payload)
    invalid["door_swing_geometry_opening_evidence"][
        "parallel_leaf_without_arc_evidence"]["parallel_rail_count"] = 2
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid)
    assert ex.value.code == "door_swing_geometry_opening_host_proof_invalid"


def test_projected_arc_host_requires_local_wall_pair_and_transverse_jamb_proof():
    axis = [[102.05, .075], [101.018, .075]]
    payload = {
        "id": "projected-arc-host",
        "source_representation": "door_swing_geometry_opening_host",
        "review_status": "confirmed", "source_entity_handles": ["arc", "leaf"],
        "footprint_polygon": [[2.05, 0], [1.018, 0],
                              [1.018, .15], [2.05, .15]],
        "centerline": [[2.05, .075], [1.018, .075]],
        "thickness_m": .15,
        "thickness_source": "cad_arc_projected_wall_face_pair_thickness",
        "height_m": 2.8, "height_source": "project_default_assumption",
        "door_swing_geometry_opening_evidence": {
            "method": "cad_door_swing_wall_pair_transverse_jamb_host_v1",
            "kind": "door", "candidate_id": "inset-arc-door",
            "opening_source_handles": ["arc", "leaf"],
            "opening_axis_cad_m": axis, "opening_width_m": 1.032,
            "axis_candidate_count": 1, "viable_axis_count": 1,
            "selected_axis_source": "primary",
            "wall_mask_endpoint_distance_m": [0, .032],
            "jamb_cross_section_width_m": [.15, .15],
            "jamb_sample_outward_offset_m": [0, 0],
            "source_reason_codes": ["circular_swing_arc", "radial_door_leaf",
                                    "wall_network_supported"],
            "projected_arc_transverse_jamb_evidence": {
                "method":
                    "cad_arc_leaf_wall_pair_transverse_jamb_projection_v1",
                "axis_segment_cad_m": axis,
                "wall_face_entity_indexes": [1, 2],
                "transverse_jamb_entity_index": 5,
                "wall_face_source_handles": ["face-a", "face-b", "jamb"],
                "wall_face_separation_m": .15,
                "hinge_to_wall_centerline_offset_m": .035,
                "transverse_jamb_snap_distance_m": .05,
                "transverse_jamb_angle_difference_deg": 90.0,
            },
            "thresholds": {},
        },
    }

    assert geometry.WallAssembly(payload)["review_status"] == "confirmed"

    remote = copy.deepcopy(payload)
    remote["door_swing_geometry_opening_evidence"][
        "projected_arc_transverse_jamb_evidence"][
            "hinge_to_wall_centerline_offset_m"] = .201
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(remote)
    assert ex.value.code == "door_swing_geometry_opening_host_proof_invalid"


def test_corner_adjacent_door_host_requires_two_distinct_terminal_walls():
    payload = {
        "id": "door-terminal-host",
        "source_representation": "door_swing_geometry_opening_host",
        "review_status": "confirmed",
        "source_entity_handles": ["door-arc"],
        "footprint_polygon": [[0, -.1], [.8, -.1], [.8, .1], [0, .1]],
        "centerline": [[0, 0], [.8, 0]],
        "thickness_m": .2,
        "thickness_source": "cad_door_terminal_wall_support_thickness",
        "height_m": 2.8,
        "height_source": "project_default_assumption",
        "door_swing_geometry_opening_evidence": {
            "method": "cad_door_swing_unique_terminal_wall_support_v1",
            "kind": "door", "candidate_id": "door-corner",
            "opening_source_handles": ["door-arc"],
            "opening_axis_cad_m": [[100, -20], [100.8, -20]],
            "wall_mask_endpoint_distance_m": [.02, .02],
            "jamb_cross_section_width_m": [.2, .2],
            "viable_axis_count": 1,
            "source_reason_codes": ["circular_swing_arc", "radial_door_leaf",
                                    "wall_network_supported"],
            "terminal_wall_supports": [{
                "endpoint_index": 0, "wall_assembly_id": "left-corner",
                "orientation": "transverse_terminal",
                "endpoint_footprint_distance_m": .02,
                "endpoint_axis_terminal_distance_m": .07,
                "endpoint_axis_terminal_distance_limit_m": .14,
                "wall_thickness_m": .2,
            }, {
                "endpoint_index": 1, "wall_assembly_id": "right-wall",
                "orientation": "collinear",
                "endpoint_footprint_distance_m": .02,
                "endpoint_axis_terminal_distance_m": .02,
                "endpoint_axis_terminal_distance_limit_m": .14,
                "wall_thickness_m": .2,
            }],
        },
    }

    assert geometry.WallAssembly(payload)["review_status"] == "confirmed"
    invalid = copy.deepcopy(payload)
    invalid["door_swing_geometry_opening_evidence"]["terminal_wall_supports"][1][
        "wall_assembly_id"] = "left-corner"
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid)
    assert ex.value.code == "door_swing_geometry_opening_host_proof_invalid"


def test_global_topology_wall_source_is_valid_audit_evidence_not_duplicate_geometry():
    evidence = {
        "id": "global-source-face",
        "source_representation": "global_topology_evidence",
        "resolved_as": "global_topology_evidence",
        "review_status": "rejected",
        "source_entity_handles": ["source-face"],
        "footprint_polygon": None, "centerline": None, "thickness_m": None,
        "global_topology_evidence": {
            "method": "accepted_space_boundary_stable_wall_cross_section_v1",
            "global_topology_method": "cad-global-wall-topology-v1",
            "global_topology_status": "proved",
            "global_topology_hash": "a" * 64,
            "global_wall_footprint_ids": ["global-wall"],
            "source_length_m": 3.6,
            "source_wall_mask_coverage_ratio": 1.0,
            "space_boundary_coverage_ratio": 1.0,
            "nearest_space_boundary_distance_m": 0.0,
            "cross_sections": [
                {"fraction": .2, "width_m": .2},
                {"fraction": .5, "width_m": .2},
                {"fraction": .8, "width_m": .2},
            ],
            "maximum_cross_section_width_delta_m": 0.0,
        },
    }

    validated = geometry.WallAssembly(evidence)
    assert validated["review_status"] == "rejected"

    invalid = copy.deepcopy(evidence)
    invalid["global_topology_evidence"]["cross_sections"][2]["width_m"] = .261
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid)
    assert ex.value.code == "global_topology_wall_evidence_proof_invalid"


def test_global_topology_strip_role_contract_rejects_floating_source_lines():
    evidence = {
        "id": "global-single-run-source",
        "source_representation": "global_topology_evidence",
        "resolved_as": "global_topology_evidence",
        "review_status": "rejected",
        "source_entity_handles": ["single-run"],
        "footprint_polygon": None, "centerline": None, "thickness_m": None,
        "global_topology_evidence": {
            "method": "proved_global_wall_strip_role_v1",
            "global_topology_method": "cad-global-wall-topology-v1",
            "global_topology_status": "proved",
            "global_topology_hash": "e" * 64,
            "global_wall_footprint_ids": ["global-wall"],
            "source_length_m": 1.0,
            "source_wall_mask_coverage_ratio": 1.0,
            "strip_role": "inferred_single_run_centerline",
            "reference_width_m": .12,
            "wall_mask_boundary_coverage_ratio": 0.0,
            "consistent_wall_side": "centered",
            "cross_sections": [
                {"fraction": fraction, "width_m": .12,
                 "signed_min_offset_m": -.06,
                 "signed_max_offset_m": .06,
                 "section_midpoint_offset_m": 0.0}
                for fraction in (.12, .50, .88)
            ],
            "maximum_cross_section_width_delta_m": 0.0,
        },
    }

    assert geometry.validate_wall_assembly(evidence)["review_status"] == "rejected"

    floating = copy.deepcopy(evidence)
    for section in floating["global_topology_evidence"]["cross_sections"]:
        section["signed_min_offset_m"] = -.01
        section["signed_max_offset_m"] = .11
        section["section_midpoint_offset_m"] = .05
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.validate_wall_assembly(floating)
    assert ex.value.code == "global_topology_wall_evidence_proof_invalid"


def test_short_global_topology_connector_and_boundary_evidence_contracts():
    common = {
        "global_topology_method": "cad-global-wall-topology-v1",
        "global_topology_status": "proved",
        "global_topology_hash": "b" * 64,
        "global_wall_footprint_ids": ["global-wall"],
        "source_length_m": .15,
        "source_wall_mask_coverage_ratio": 1.0,
        "endpoint_wall_boundary_distances_m": [0.0, .01],
        "reference_wall_width_m": .15,
        "valid_perpendicular_cross_section_count": 0,
    }
    connector = {
        "id": "transverse-cap",
        "source_representation": "global_topology_connector_evidence",
        "review_status": "rejected", "source_entity_handles": ["cap"],
        "footprint_polygon": None, "centerline": None, "thickness_m": None,
        "global_topology_connector_evidence": {
            **common,
            "method": "proved_global_wall_transverse_connector_v1",
            "midpoint_wall_boundary_distance_m": .075,
        },
    }
    assert geometry.WallAssembly(connector)["review_status"] == "rejected"

    boundary = {
        **connector,
        "id": "short-end-face",
        "source_representation": "global_topology_boundary_evidence",
        "source_entity_handles": ["end-face"],
        "global_topology_boundary_evidence": {
            **common,
            "method": "proved_global_wall_short_boundary_face_v1",
            "midpoint_wall_boundary_distance_m": .01,
        },
    }
    boundary.pop("global_topology_connector_evidence")
    assert geometry.WallAssembly(boundary)["review_status"] == "rejected"

    invalid = copy.deepcopy(boundary)
    invalid["global_topology_boundary_evidence"][
        "midpoint_wall_boundary_distance_m"] = .03
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid)
    assert ex.value.code == "global_topology_short_evidence_proof_invalid"

    wide_connector = copy.deepcopy(connector)
    wide_connector["global_topology_connector_evidence"].update(
        source_length_m=.24, reference_wall_width_m=.24,
        midpoint_wall_boundary_distance_m=.12)
    assert geometry.WallAssembly(wide_connector)["review_status"] == "rejected"


def test_micro_and_piecewise_global_topology_evidence_contracts():
    micro = {
        "id": "embedded-mark",
        "source_representation": "global_topology_micro_evidence",
        "review_status": "rejected", "source_entity_handles": ["micro"],
        "footprint_polygon": None, "centerline": None, "thickness_m": None,
        "global_topology_micro_evidence": {
            "method": "proved_global_wall_embedded_micro_detail_v1",
            "global_topology_method": "cad-global-wall-topology-v1",
            "global_topology_status": "proved",
            "global_topology_hash": "d" * 64,
            "global_wall_footprint_ids": ["wall"],
            "source_length_m": .04,
            "source_wall_mask_coverage_ratio": 1.0,
            "endpoint_wall_boundary_distances_m": [.05, .05],
            "midpoint_wall_boundary_distance_m": .05,
        },
    }
    assert geometry.WallAssembly(micro)["review_status"] == "rejected"

    boundary_micro = copy.deepcopy(micro)
    boundary_micro["id"] = "boundary-mark"
    boundary_micro["global_topology_micro_evidence"].update(
        method="proved_global_wall_boundary_micro_detail_v1",
        endpoint_wall_boundary_distances_m=[0.0, .01],
        midpoint_wall_boundary_distance_m=.01)
    assert geometry.WallAssembly(boundary_micro)[
        "review_status"] == "rejected"

    floating_micro = copy.deepcopy(boundary_micro)
    floating_micro["global_topology_micro_evidence"].update(
        endpoint_wall_boundary_distances_m=[.03, .03],
        midpoint_wall_boundary_distance_m=.03)
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(floating_micro)
    assert ex.value.code == "global_topology_micro_evidence_proof_invalid"

    sections = [
        {
            "fraction": fraction, "width_m": .1,
            "signed_min_offset_m": -.05, "signed_max_offset_m": .05,
            "section_midpoint_offset_m": 0.0, "source_role": "centerline",
        }
        for fraction in (.12, .25, .38, .50, .62, .75, .88)
    ]
    piecewise = {
        "id": "piecewise-source",
        "source_representation": "global_topology_piecewise_evidence",
        "review_status": "rejected", "source_entity_handles": ["piecewise"],
        "footprint_polygon": None, "centerline": None, "thickness_m": None,
        "global_topology_piecewise_evidence": {
            "method": "proved_global_wall_piecewise_role_v1",
            "global_topology_method": "cad-global-wall-topology-v1",
            "global_topology_status": "proved",
            "global_topology_hash": "e" * 64,
            "global_wall_footprint_ids": ["wall"],
            "source_length_m": .8,
            "source_wall_mask_coverage_ratio": 1.0,
            "classified_cross_sections": sections,
            "valid_cross_section_count": 7,
            "classified_cross_section_count": 7,
            "classified_fraction_span": .76,
            "inferred_single_run_width_m": .1,
            "collinear_duplicate_source_ids": ["duplicate-source"],
        },
    }
    assert geometry.WallAssembly(piecewise)["review_status"] == "rejected"

    invalid = copy.deepcopy(piecewise)
    invalid["global_topology_piecewise_evidence"][
        "classified_cross_sections"][0]["source_role"] = "floating"
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid)
    assert ex.value.code == "global_topology_piecewise_evidence_proof_invalid"


def test_independently_supported_local_boundary_face_contract():
    independent = [
        {"fraction": fraction, "width_m": .195,
         "signed_min_offset_m": -.16, "signed_max_offset_m": .035,
         "section_midpoint_offset_m": -.0625}
        for fraction in (.25, .38, .50, .62, .75)
    ]
    evidence = {
        "id": "local-boundary-face",
        "source_representation": "global_topology_evidence",
        "review_status": "rejected",
        "source_entity_handles": ["local-face"],
        "footprint_polygon": None, "centerline": None, "thickness_m": None,
        "global_topology_evidence": {
            "method": "proved_global_wall_strip_role_v1",
            "global_topology_method": "cad-global-wall-topology-v1",
            "global_topology_status": "proved",
            "global_topology_hash": "c" * 64,
            "global_wall_footprint_ids": ["global-wall"],
            "source_length_m": .9,
            "source_wall_mask_coverage_ratio": 1.0,
            "strip_role": "independently_supported_local_boundary_face",
            "reference_width_m": .195,
            "wall_mask_boundary_coverage_ratio": .005,
            "local_wall_boundary_coverage_ratio": 1.0,
            "consistent_wall_side": "negative",
            "cross_sections": independent[:3],
            "independent_cross_sections": independent,
            "maximum_cross_section_width_delta_m": 0.0,
        },
    }
    assert geometry.WallAssembly(evidence)["review_status"] == "rejected"

    invalid = copy.deepcopy(evidence)
    for row in invalid["global_topology_evidence"]["independent_cross_sections"]:
        row["signed_max_offset_m"] = .055
    with pytest.raises(geometry.GeometryContractError) as ex:
        geometry.WallAssembly(invalid)
    assert ex.value.code == "global_topology_wall_evidence_proof_invalid"


def _manifest(registration_hash: str, **overrides):
    payload = {
        "project_id": "home-1",
        "model_revision": 4,
        "model_facts_hash": MODEL_HASH,
        "registration_hash": registration_hash,
        "geometry_kernel_version": "whole-home-geometry/1",
        "units": "meter",
        "coordinate_system": {"up": "+Y", "forward": "+Z"},
        "vertices": [
            [0, 0, 0], [4, 0, 0], [4, 2.8, 0], [0, 2.8, 0],
            [0, 0, 0.2], [4, 0, 0.2], [4, 2.8, 0.2], [0, 2.8, 0.2],
        ],
        "wall_parts": [{"id": "wall-1", "entity_id": "wall-1", "indices": [0, 1, 2, 0, 2, 3]}],
        "floor_parts": [{"id": "room-1", "entity_id": "room-1", "indices": [0, 1, 4, 1, 5, 4]}],
        "opening_voids": [{"id": "door-1", "wall_id": "wall-1", "offset_m": 1, "width_m": 0.9}],
    }
    payload.update(overrides)
    return geometry.GeometryManifest(payload)


def _passing_cad_metrics():
    return {
        "cad": {
            "provenance_coverage": 1.0,
            "wall_assembly_coverage": 1.0,
            "boundary_p95_m": 0.02,
            "boundary_max_m": 0.04,
            "max_room_area_relative_error": 0.005,
            "room_coverage": 0.995,
            "room_overlap_area_m2": 0,
            "outer_max_gap_m": 0.005,
            "opening_eligible_count": 0,
            "orphan_opening_count": 0,
            "outside_opening_count": 0,
            "overlapping_opening_count": 0,
            "unresolved_wall_count": 0,
        },
        "manifest": {
            "floor_footprint_iou": 0.9999,
            "wall_footprint_symmetric_difference_m2": 0.00009,
            "wall_footprint_symmetric_difference_ratio": 0.01,
            "opening_interval_error_m": 0,
            "projection_iou": 0.999,
            "orphan_manifest_opening_count": 0,
        },
    }


def _passing_raster_metrics():
    return {
        "raster": {
            "scale_anchor_count": 2,
            "scale_disagreement": 0.01,
            "registration_roundtrip_px": 0.1,
            "wall_centerline_p95_m": 0.05,
            "room_iou": 0.97,
            "opening_precision": 1,
            "opening_recall": 1,
            "human_review_completion": 1,
            "unresolved_review_count": 0,
        },
        "manifest": _passing_cad_metrics()["manifest"],
    }


def _report(registration, manifest, *, metrics=None, input_grade="vector_authoritative"):
    return geometry.build_geometry_acceptance_report(
        project_id="home-1",
        source_type="cad" if input_grade == "vector_authoritative" else "raster",
        input_grade=input_grade,
        source_hash=SOURCE_HASH,
        model_revision=4,
        model_facts_hash=MODEL_HASH,
        registration_hash=registration["registration_hash"],
        cad_facts_hash=CAD_HASH if input_grade == "vector_authoritative" else "",
        geometry_kernel_version="whole-home-geometry/1",
        manifest_hash=manifest["manifest_hash"],
        metrics=metrics or _passing_cad_metrics(),
        human_review={"required": input_grade.startswith("raster"), "completed": True},
    )


def test_canonical_json_and_hash_are_order_and_float_stable():
    left = {"z": -0.0, "a": {"value": 1.12345678941}, "items": (2, 1)}
    right = {"items": [2, 1], "a": {"value": 1.12345678944}, "z": 0.0}
    assert geometry.canonical_json(left) == geometry.canonical_json(right)
    assert geometry.canonical_hash(left) == geometry.canonical_hash(right)
    assert json.loads(geometry.canonical_json(left))["z"] == 0
    with pytest.raises(geometry.GeometryContractError) as error:
        geometry.canonical_json({"bad": math.nan})
    assert error.value.code == "canonical_number_invalid"
    with pytest.raises(geometry.GeometryContractError) as error:
        geometry.canonical_json({1: "number", "1": "string"})
    assert error.value.code == "canonical_key_collision"


def test_source_registration_computes_inverses_roundtrip_and_stable_hash():
    registration = _registration()
    assert registration["canonical_to_source"] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    assert registration["roundtrip_error"] <= 1e-6
    assert registration["uniform_scale"] == pytest.approx(0.01)
    reordered = geometry.SourceRegistration(dict(reversed(list(registration.items()))))
    assert reordered["registration_hash"] == registration["registration_hash"]


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"canonical_to_model": [[0.01, 0, 0], [0, 0.02, 0], [0, 0, 1]]}, "model_scale_non_uniform"),
        ({"canonical_to_model": [[-0.01, 0, 0], [0, 0.01, 0], [0, 0, 1]]}, "model_transform_mirrored"),
        ({"source_to_canonical": [[1, 0, 0], [0, 0, 0], [0, 0, 1]]}, "matrix_singular"),
        ({"source_to_canonical": [[1, 0.1, 0], [0, 1, 0], [0, 0, 1]]}, "model_scale_non_uniform"),
        ({"measured_roundtrip_error": 0.01}, "registration_roundtrip_exceeded"),
        ({"cad_units": "", "source_space": "cad_units"}, "cad_units_unconfirmed"),
    ],
)
def test_vector_registration_rejects_unsafe_transforms(override, code):
    with pytest.raises(geometry.GeometryContractError) as error:
        _registration(**override)
    assert error.value.code == code


def test_raster_registration_requires_scale_and_limits_anchor_disagreement():
    locked = _registration(grade="raster_human_locked")
    assert locked["scale_anchor_count"] == 2
    assert locked["scale_disagreement"] == pytest.approx(0)
    with pytest.raises(geometry.GeometryContractError) as error:
        _registration(grade="raster_human_locked", scale_anchors=[])
    assert error.value.code == "raster_scale_anchor_required"
    with pytest.raises(geometry.GeometryContractError) as error:
        _registration(
            grade="raster_human_locked",
            scale_anchors=[
                {"pixel_length": 100, "actual_length_m": 1},
                {"pixel_length": 100, "actual_length_m": 1.1},
            ],
        )
    assert error.value.code == "scale_anchor_disagreement"


def test_registration_hash_detects_transform_tampering():
    registration = _registration()
    registration["canonical_to_model"][0][2] += 1
    registration.pop("model_to_canonical")
    with pytest.raises(geometry.GeometryContractError) as error:
        geometry.validate_source_registration(registration)
    assert error.value.code == "registration_hash_mismatch"


def test_raster_draft_can_exist_without_scale_but_is_not_formally_locked():
    draft = _registration(grade="raster_draft")
    assert draft["scale_anchor_count"] == 0
    issues = geometry.evaluate_geometry_metrics(
        source_type="raster", input_grade="raster_draft", metrics=_passing_raster_metrics(),
    )
    assert any(issue["code"] == "raster_not_human_locked" for issue in issues)


def test_wall_assembly_normalizes_closed_footprint_and_retains_provenance():
    wall = _wall()
    assert wall["footprint_polygon"][0] == wall["footprint_polygon"][-1]
    assert wall["footprint_area_m2"] == pytest.approx(0.8)
    assert wall["source_entity_handles"] == ["A1", "A2"]


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"source_entity_handles": []}, "wall_provenance_missing"),
        ({"source_entity_handles": ["A1"]}, "paired_wall_faces_missing"),
        ({"thickness_m": 0.7}, "paired_wall_thickness_invalid"),
        ({"footprint_polygon": [[0, 0], [1, 0], [2, 0]]}, "wall_footprint_degenerate"),
        ({"footprint_polygon": [[0, 0], [3, 0], [1, 2], [1, -1], [0, 1]]}, "wall_footprint_self_intersection"),
        ({"source_representation": "human_confirmed_ambiguous", "review_status": "pending"}, "ambiguous_wall_unconfirmed"),
    ],
)
def test_wall_assembly_rejects_unproven_or_degenerate_geometry(override, code):
    with pytest.raises(geometry.GeometryContractError) as error:
        _wall(**override)
    assert error.value.code == code


def test_centerline_default_thickness_requires_confirmation():
    with pytest.raises(geometry.GeometryContractError) as error:
        _wall(
            source_representation="centerline", source_entity_handles=["A1"],
            thickness_source="project_default_assumption", review_status="pending",
        )
    assert error.value.code == "centerline_thickness_unconfirmed"


def test_manifest_hash_is_stable_across_key_and_part_order():
    registration = _registration()
    manifest = _manifest(registration["registration_hash"])
    payload = dict(manifest)
    payload.pop("manifest_hash")
    payload["wall_parts"] = list(reversed(payload["wall_parts"]))
    payload = dict(reversed(list(payload.items())))
    rebuilt = geometry.GeometryManifest(payload)
    assert rebuilt["manifest_hash"] == manifest["manifest_hash"]
    assert rebuilt["wall_parts"][0]["id"] == "wall-1"


def test_manifest_rejects_bad_indices_and_tampering():
    registration = _registration()
    manifest = _manifest(registration["registration_hash"])
    with pytest.raises(geometry.GeometryContractError) as error:
        _manifest(registration["registration_hash"], wall_parts=[{"id": "wall", "indices": [0, 1, 999]}])
    assert error.value.code == "manifest_vertex_reference_invalid"
    tampered = copy.deepcopy(manifest)
    tampered["vertices"][0][0] = 9
    with pytest.raises(geometry.GeometryContractError) as error:
        geometry.validate_manifest(tampered)
    assert error.value.code == "manifest_hash_mismatch"


def test_passing_cad_report_has_integrity_hash_and_no_blockers():
    registration = _registration()
    manifest = _manifest(registration["registration_hash"])
    report = _report(registration, manifest)
    assert report["status"] == "passed"
    assert report["report_id"].startswith("gar_")
    assert geometry.report_hash_matches(report)
    assert geometry.validate_acceptance_report(report) == report


def test_metric_gate_blocks_cad_geometry_mismatch_and_missing_metric():
    registration = _registration()
    manifest = _manifest(registration["registration_hash"])
    metrics = _passing_cad_metrics()
    metrics["cad"]["boundary_p95_m"] = 0.051
    metrics["cad"].pop("room_coverage")
    report = _report(registration, manifest, metrics=metrics)
    assert report["status"] == "blocked"
    codes = {issue["code"] for issue in report["issues"]}
    assert {"cad_boundary_p95_exceeded", "metric_missing"} <= codes


def test_manifest_symmetric_difference_uses_either_absolute_or_relative_tolerance():
    metrics = _passing_cad_metrics()
    # Absolute error is below 1e-4, so a high ratio on a tiny wall remains valid.
    issues = geometry.evaluate_geometry_metrics(
        source_type="cad", input_grade="vector_authoritative", metrics=metrics,
    )
    assert "manifest_wall_mismatch" not in {issue["code"] for issue in issues}
    metrics["manifest"]["wall_footprint_symmetric_difference_m2"] = 0.001
    metrics["manifest"]["wall_footprint_symmetric_difference_ratio"] = 0.0011
    issues = geometry.evaluate_geometry_metrics(
        source_type="cad", input_grade="vector_authoritative", metrics=metrics,
    )
    assert "manifest_wall_mismatch" in {issue["code"] for issue in issues}


def test_passing_locked_raster_report_and_manual_review_requirement():
    registration = _registration(grade="raster_human_locked")
    manifest = _manifest(registration["registration_hash"])
    report = _report(
        registration, manifest, metrics=_passing_raster_metrics(), input_grade="raster_human_locked",
    )
    assert report["status"] == "passed"
    incomplete = geometry.build_geometry_acceptance_report(
        project_id="home-1", source_type="raster", input_grade="raster_human_locked",
        source_hash=SOURCE_HASH, model_revision=4, model_facts_hash=MODEL_HASH,
        registration_hash=registration["registration_hash"],
        geometry_kernel_version="whole-home-geometry/1", manifest_hash=manifest["manifest_hash"],
        metrics=_passing_raster_metrics(), human_review={"required": True, "completed": False},
    )
    assert incomplete["status"] == "needs_human_review"


def test_report_becomes_stale_when_any_authority_hash_changes():
    registration = _registration()
    manifest = _manifest(registration["registration_hash"])
    report = _report(registration, manifest)
    current = {
        "source_hash": SOURCE_HASH,
        "model_revision": 4,
        "model_facts_hash": "changed",
        "registration_hash": registration["registration_hash"],
        "cad_facts_hash": CAD_HASH,
        "geometry_kernel_version": "whole-home-geometry/1",
        "manifest_hash": manifest["manifest_hash"],
    }
    stale = geometry.refresh_acceptance_staleness(report, current)
    assert stale["status"] == "stale"
    assert stale["stale_reasons"] == [{"field": "model_facts_hash", "expected": MODEL_HASH, "actual": "changed"}]
    assert geometry.report_hash_matches(stale)


def test_legacy_migration_preserves_history_but_never_inherits_proof():
    legacy = {"project_id": "old", "verified": True, "revision": 7, "model": {"schema_version": 2}}
    migrated = geometry.migrate_legacy_project_geometry(legacy)
    assert legacy == {"project_id": "old", "verified": True, "revision": 7, "model": {"schema_version": 2}}
    assert migrated["verified"] is True
    assert migrated["legacy_verified"] is True
    assert migrated["input_grade"] == "legacy_unproven"
    assert migrated["geometry_schema_version"] == 3
    assert migrated["model"]["schema_version"] == 2
    assert migrated["model"]["geometry_schema_version"] == 3


def test_production_readiness_requires_current_passed_report_and_manifest():
    registration = _cad_registration_v2()
    manifest = _manifest(registration["registration_hash"])
    report = _report(registration, manifest)
    project = {
        "project_id": "home-1",
        "revision": 4,
        "input_grade": "vector_authoritative",
        "source_registration": registration,
        "model": {
            "model_facts_hash": MODEL_HASH, "cad_facts_hash": CAD_HASH,
            "coordinate_contract_version": 2,
            "coordinate_system": "right-handed-y-up-x-east-z-south-v2",
        },
    }
    result = geometry.production_readiness(project, report, manifest)
    assert result["ready"] is True
    assert result["code"] == "ready"
    assert geometry.assert_production_ready(project, report, manifest)["ready"] is True

    changed = copy.deepcopy(project)
    changed["model"]["model_facts_hash"] = "changed"
    result = geometry.production_readiness(changed, report, manifest)
    assert result["ready"] is False
    assert any(reason["code"] == "geometry_acceptance_stale" for reason in result["reasons"])
    with pytest.raises(geometry.GeometryContractError) as error:
        geometry.assert_production_ready(changed, report, manifest)
    assert error.value.code == "production_not_ready"


def test_verified_legacy_project_is_fail_closed_without_new_evidence():
    legacy = geometry.migrate_legacy_project_geometry({
        "project_id": "old", "verified": True, "revision": 3, "model": {"schema_version": 2},
    })
    readiness = geometry.production_readiness(legacy)
    assert readiness["ready"] is False
    codes = {reason["code"] for reason in readiness["reasons"]}
    assert {"input_not_locked", "source_registration_missing", "geometry_manifest_missing", "geometry_acceptance_missing"} <= codes
