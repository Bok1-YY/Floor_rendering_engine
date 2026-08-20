import copy

from Floor_engine_server.whole_home_geometry_acceptance import (
    build_cad_source_registration,
    build_project_geometry_acceptance,
    measure_cad_correspondence,
    measure_manifest_correspondence,
    measure_raster_correspondence,
)
from Floor_engine_server.whole_home_geometry_kernel import compile_geometry_manifest


def _model():
    provenance = {
        "handle": "10", "source_handle": "10", "root_handle": "10",
        "source_segment_m": [[0, 0], [4, 0]],
    }
    return {
        "coordinate_system": "metres-y-up", "wall_height_m": 2.8,
        "walls": [{
            "id": "wall-1", "wall_assembly_id": "assembly-1",
            "start": {"x": 0, "z": 0}, "end": {"x": 4, "z": 0},
            "thickness_m": .2, "height_m": 2.8, "cad_provenance": provenance,
        }],
        "wall_assemblies": [{
            "id": "assembly-1", "source_representation": "paired_faces",
            "centerline": [[0, 0], [4, 0]],
            "footprint_polygon": [[0, -.1], [4, -.1], [4, .1], [0, .1]],
            "thickness_m": .2, "height_m": 2.8, "review_status": "accepted",
            "source_entity_handles": ["10"],
        }],
        "rooms": [{
            "id": "room-1", "polygon": [
                {"x": 0, "z": 0}, {"x": 4, "z": 0},
                {"x": 4, "z": 3}, {"x": 0, "z": 3},
            ],
            "cad_provenance": {
                "source_handle": "20", "root_handle": "20",
                "source_polygon_m": [[0, 0], [4, 0], [4, 3], [0, 3]],
            },
        }],
        "openings": [], "fixed_objects": [], "model_to_cad": {"x": 0, "z": 0},
    }


def _project():
    return {
        "project_id": "home-1", "source_type": "cad", "revision": 1,
        "input_grade": "vector_authoritative", "model": _model(),
        "source_registration": {
            "source_hash": "a" * 64, "registration_hash": "registration-1",
            "input_grade": "vector_authoritative",
        },
        "parse_report": {"alignment_metrics": {
            "room_coverage": 1.0, "room_overlap_area_m2": 0.0,
            "outer_wall_closed": True, "opening_endpoint_errors": [],
            "opening_width_errors": [],
        }},
        "cad_import": {"cad_facts_hash": "cad-facts-1"},
    }


def test_compiled_manifest_projection_is_an_exact_correspondence():
    model = _model()
    manifest = compile_geometry_manifest(
        model, project_id="home-1", model_revision=1, registration_hash="registration-1")
    metrics = measure_manifest_correspondence(model, manifest)
    assert metrics == {
        "floor_footprint_iou": 1.0,
        "wall_footprint_symmetric_difference_m2": 0.0,
        "wall_footprint_symmetric_difference_ratio": 0.0,
        "opening_interval_error_m": 0.0,
        "projection_iou": 1.0,
        "orphan_manifest_opening_count": 0,
    }


def test_cad_measurement_blocks_unresolved_wall_assemblies():
    project = _project()
    project["model"]["wall_assemblies"][0]["review_status"] = "needs_review"
    metrics = measure_cad_correspondence(project)
    assert metrics["unresolved_wall_count"] == 1
    assert metrics["wall_assembly_coverage"] == 0


def test_cad_measurement_counts_rejected_redundant_evidence_as_terminal_disposition():
    project = _project()
    project["model"]["wall_assemblies"][0] = {
        "id": "assembly-1", "source_representation": "redundant_evidence",
        "review_status": "rejected", "source_entity_handles": ["10"],
    }
    metrics = measure_cad_correspondence(project)
    assert metrics["unresolved_wall_count"] == 0
    assert metrics["wall_assembly_coverage"] == 1


def test_raster_measurement_does_not_invent_missing_review_metrics():
    registration = {
        "scale_anchor_count": 2, "scale_disagreement": .01, "roundtrip_error": .1,
    }
    metrics = measure_raster_correspondence(registration, {"room_iou": .97})
    assert metrics["scale_anchor_count"] == 2
    assert metrics["room_iou"] == .97
    assert "opening_recall" not in metrics


def test_server_wall_measurement_cannot_be_overridden_by_raster_review():
    metrics = measure_raster_correspondence(
        {"scale_anchor_count": 1, "scale_disagreement": 0, "roundtrip_error": 0},
        {"wall_centerline_p95_m": 0.0, "room_iou": 1.0},
        {"wall_centerline_p95_m": 0.125},
    )
    assert metrics["wall_centerline_p95_m"] == .125


def test_cad_registration_binds_units_and_selected_plan_translation():
    registration = build_cad_source_registration(
        source_hash="b" * 64,
        parse_report={"insunits": 4, "unit_scale_to_m": .001},
        model={"cad_to_model": {"schema_version": 2, "x": -12.5, "z": 3.0,
                                "x_scale": 1, "z_scale": -1},
               "scale": {"unit_code": 4, "metres_per_unit": .001}},
    )
    assert registration["cad_units"] == "mm"
    assert registration["source_to_canonical"][0][0] == .001
    assert registration["version"] == 2
    assert registration["canonical_xyz_to_model"][0][3] == -12.5
    assert registration["canonical_xyz_to_model"][2] == [0.0, -1.0, 0.0, 3.0]
    assert registration["axis_mapping"]["cad_y"] == "-model_z"
    assert registration["registration_hash"]


def test_cad_registration_uses_audited_resolved_units_over_wrong_header():
    registration = build_cad_source_registration(
        source_hash="c" * 64,
        parse_report={
            "insunits": 1,
            "resolved_insunits": 6,
            "unit_scale_to_m": 1.0,
            "unit_resolution": {
                "method": "cad_explicit_annotation_unit_resolution_v1",
                "declared_insunits": 1,
                "resolved_insunits": 6,
            },
        },
        model={
            "cad_to_model": {"schema_version": 2, "x": -5.0, "z": -7.0,
                             "x_scale": 1, "z_scale": -1},
            "scale": {"unit_code": 6, "metres_per_unit": 1.0},
        },
    )
    assert registration["cad_units"] == "m"
    assert registration["source_to_canonical"][0][0] == 1.0
    assert (registration["registration_method"]
            == "cad_explicit_annotation_unit_resolution_v1_then_cad_xy_to_right_handed_model_xyz_v2")


def test_project_acceptance_is_passed_only_with_audited_assumptions():
    manifest, report, metrics = build_project_geometry_acceptance(
        _project(), reviewer="reviewer-1", review_note="checked against CAD",
        assumptions_confirmed=True,
    )
    assert manifest["manifest_hash"] == report["manifest_hash"]
    assert report["model_facts_hash"] == manifest["model_facts_hash"]
    assert report["status"] == "passed"
    assert metrics["cad"]["provenance_coverage"] == 1

    _, incomplete, _ = build_project_geometry_acceptance(
        copy.deepcopy(_project()), reviewer="", review_note="", assumptions_confirmed=False)
    assert incomplete["status"] == "needs_human_review"
    assert {row["code"] for row in incomplete["issues"]} >= {
        "human_review_incomplete", "engineering_assumptions_unconfirmed",
    }
