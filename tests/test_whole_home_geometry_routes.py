import copy
import asyncio
import hashlib

import pytest
from fastapi import HTTPException
from PIL import Image, ImageDraw

from Floor_engine_server import routes_whole_home
from Floor_engine_server.server_schemas import (
    WholeHomeCadAiAssistRequest,
    WholeHomeCadOpeningAnnotationsRequest,
    WholeHomeCadWallAssemblyConfirmRequest,
    WholeHomeGeometryAcceptanceRequest,
    WholeHomeRasterRegistrationPrepareRequest,
    WholeHomeSourceRegistrationRequest,
)
from Floor_engine_server.whole_home_cad import cad_facts_hash
from Floor_engine_server.whole_home_geometry import SourceRegistration


def _registration(source_hash="a" * 64, source_type="cad", grade="vector_authoritative"):
    payload = {
        "source_type": source_type, "input_grade": grade,
        "source_hash": source_hash,
        "source_to_canonical": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "measured_roundtrip_error": 0,
    }
    if source_type == "cad":
        payload.update({
            "version": 2,
            "cad_units": "m",
            "canonical_xyz_to_model": [
                [1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1],
            ],
            "axis_mapping": {
                "cad_x": "+model_x", "cad_y": "-model_z", "elevation": "+model_y",
            },
        })
    else:
        payload["canonical_to_model"] = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        payload["scale_anchors"] = [{"actual_length_m": 4, "pixel_length": 400}]
    return SourceRegistration(payload).to_dict()


def _cad_project():
    registration = _registration()
    provenance = {
        "handle": "10", "source_handle": "10", "root_handle": "10",
        "effective_layer": "A-WALL", "source_segment_m": [[0, 0], [4, 0]],
    }
    model = {
        "schema_version": 2, "geometry_schema_version": 3,
        "coordinate_system": "right-handed-y-up-x-east-z-south-v2",
        "coordinate_contract_version": 2, "wall_height_m": 2.8,
        "input_grade": "vector_authoritative", "source_registration": registration,
        "walls": [{
            "id": "wall-1", "wall_assembly_id": "assembly-1",
            "start": {"x": 0, "z": 0}, "end": {"x": 4, "z": 0},
            "thickness_m": .2, "height_m": 2.8, "kind": "interior",
            "source": "cad", "cad_provenance": provenance,
        }],
        "wall_assemblies": [{
            "id": "assembly-1", "source_representation": "paired_faces",
            "centerline": [[0, 0], [4, 0]], "opening_axis": [[0, 0], [4, 0]],
            "footprint_polygon": [[0, -.1], [4, -.1], [4, .1], [0, .1]],
            "thickness_m": .2, "height_m": 2.8, "review_status": "accepted",
            "source_entity_handles": ["10"],
        }],
        "rooms": [{
            "id": "room-1", "label": "Room", "room_type": "other",
            "polygon": [{"x": 0, "z": 0}, {"x": 4, "z": 0},
                        {"x": 4, "z": 3}, {"x": 0, "z": 3}],
            "selected": True, "semantic_status": "complete",
            "cad_provenance": {
                "source_handle": "20", "root_handle": "20",
                "source_polygon_m": [[0, 0], [4, 0], [4, -3], [0, -3]],
            },
        }],
        "openings": [], "fixed_objects": [], "cameras": [],
        "model_to_cad": {"schema_version": 2, "x": 0, "z": 0,
                         "x_scale": 1, "z_scale": -1},
        "cad_to_model": {"schema_version": 2, "x": 0, "z": 0,
                         "x_scale": 1, "z_scale": -1},
    }
    facts = cad_facts_hash(model)
    model["cad_facts_hash"] = facts
    return {
        "project_id": "route-home", "source_type": "cad", "revision": 1,
        "input_grade": "vector_authoritative", "source_registration": registration,
        "geometry_acceptance_required": True, "geometry_schema_version": 3,
        "model": model, "cad_import": {"cad_facts_hash": facts},
        "parse_report": {"alignment_metrics": {
            "room_coverage": 1, "room_overlap_area_m2": 0,
            "outer_wall_closed": True, "opening_endpoint_errors": [],
            "opening_width_errors": [],
        }},
        "verified": False, "verified_revision": 0, "captures": [], "operations": [],
    }


def _memory_store(monkeypatch, project):
    store = {project["project_id"]: copy.deepcopy(project)}
    monkeypatch.setattr(routes_whole_home, "load_project",
                        lambda project_id: copy.deepcopy(store.get(project_id)))
    monkeypatch.setattr(routes_whole_home, "_project_entry",
                        lambda project_id: copy.deepcopy(store.get(project_id)))

    def persist(value):
        store[value["project_id"]] = copy.deepcopy(value)

    monkeypatch.setattr(routes_whole_home, "_persist_project", persist)
    return store


def test_acceptance_preview_commit_and_production_gate(monkeypatch):
    project = _cad_project()
    store = _memory_store(monkeypatch, project)
    preview = routes_whole_home.evaluate_whole_home_geometry_acceptance(
        project["project_id"], WholeHomeGeometryAcceptanceRequest(
            base_revision=1, operation_id="accept_001", reviewer="reviewer",
            review_note="CAD overlay checked", assumptions_confirmed=True, commit=False))
    assert preview["committed"] is False
    assert preview["report"]["status"] == "passed"
    assert store[project["project_id"]]["revision"] == 1

    committed = routes_whole_home.evaluate_whole_home_geometry_acceptance(
        project["project_id"], WholeHomeGeometryAcceptanceRequest(
            base_revision=1, operation_id="accept_002", reviewer="reviewer",
            review_note="CAD overlay checked", assumptions_confirmed=True, commit=True))
    locked = store[project["project_id"]]
    assert committed["committed"] is True
    assert locked["revision"] == 2
    assert locked["geometry_acceptance"]["model_revision"] == 2
    assert locked["model"]["geometry_manifest"]["manifest_hash"]
    assert committed["project"]["geometry_contract"]["geometry_facts_hash"]
    assert (committed["project"]["geometry_contract"]["cad_geometry_fingerprint"]
            == committed["project"]["geometry_contract"]["geometry_facts_hash"])
    routes_whole_home._assert_geometry_production_gate(locked)
    stale = copy.deepcopy(locked)
    stale["revision"] = 3
    with pytest.raises(HTTPException) as error:
        routes_whole_home._assert_geometry_production_gate(stale)
    assert error.value.detail["code"] == "geometry_correspondence_not_ready"


def test_cad_gemini_assist_is_advisory_only_and_revision_stable(monkeypatch, tmp_path):
    project = _cad_project()
    store = _memory_store(monkeypatch, project)
    preview = tmp_path / "selected.png"
    preview.write_bytes(b"\x89PNG\r\n\x1a\norientation evidence")
    report = {
        "source_sha256": "a" * 64,
        "semantic_preview_path": str(preview),
        "selected_candidate_id": "candidate-1",
        "selected_entity_role_summary": {"retained_wall_entity_count": 1},
        "selected_entity_role_evidence": [{
            "evidence_id": "role-1", "role": "review", "confidence": "review",
            "reason_codes": ["ambiguous"], "source_handles": ["10"],
        }],
        "raw_opening_summary": {"candidate_count": 1},
        "raw_opening_candidates": [{
            "candidate_id": "opening-1", "kind": "door", "status": "review",
            "confidence": .6, "source_handles": ["20"],
        }],
        "text_anchors": [], "hard_errors": [], "warnings": [],
    }
    monkeypatch.setattr(routes_whole_home, "_cad_report", lambda value: copy.deepcopy(report))
    monkeypatch.setattr(routes_whole_home, "load_config", lambda: {"gemini_api_key": "test-key"})

    def fake_gemini(*args, **kwargs):
        return ({
            "summary": "Only a proposal",
            "orientation_assessment": {
                "sky_to_ground": True, "cad_y_screen_direction": "up",
                "confidence": .99, "reason": "contract v2",
            },
            "room_label_proposals": [{
                "physical_space_id": "room-1", "label": "Room", "zone_type": "other",
                "confidence": .6, "evidence_ids": ["role-1"], "reason": "text absent",
            }],
            "wall_role_reviews": [{
                "evidence_id": "role-1", "disposition": "needs_review",
                "confidence": .6, "reason": "ambiguous",
            }],
            "opening_reviews": [{
                "candidate_id": "opening-1", "disposition": "needs_review",
                "kind": "door", "confidence": .6, "reason": "weak jamb evidence",
            }],
            "risks": [{"code": "room_label_unproven", "severity": "review", "reason": "no text"}],
            "_floor_engine_model": "gemini-3.6-flash",
            "_floor_engine_usage_metadata": {"promptTokenCount": 100},
        }, None)

    monkeypatch.setattr(routes_whole_home, "call_gemini_json", fake_gemini)
    before_model = copy.deepcopy(project["model"])
    advisory = asyncio.run(routes_whole_home.review_whole_home_cad_with_ai(
        project["project_id"], WholeHomeCadAiAssistRequest(
            base_revision=1, operation_id="cadai_test_001", review_passes=1)))
    saved = store[project["project_id"]]
    assert advisory["authority"] == "advisory_only"
    assert advisory["geometry_mutated"] is False
    assert advisory["revision_unchanged"] is True
    assert advisory["passes"][0]["model"] == "gemini-3.6-flash"
    assert saved["revision"] == 1
    assert saved["model"] == before_model
    assert saved["operations"][-1]["type"] == "cad_ai_assist_advisory"

def test_raster_registration_requires_current_image_hash(monkeypatch, tmp_path):
    image = tmp_path / "plan.png"
    image.write_bytes(b"current-plan")
    source_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    project = {
        "project_id": "raster-home", "source_type": "floorplan", "revision": 1,
        "floorplan_path": str(image), "input_grade": "raster_draft",
        "geometry_acceptance_required": True, "model": {}, "operations": [],
    }
    store = _memory_store(monkeypatch, project)
    registration = _registration(source_hash, "raster", "raster_human_locked")
    result = routes_whole_home.save_whole_home_source_registration(
        project["project_id"], WholeHomeSourceRegistrationRequest(
            base_revision=1, operation_id="register_001", reviewer="reviewer",
            registration=registration))
    assert result["geometry_contract"]["registration"]["source_hash"] == source_hash
    assert store[project["project_id"]]["revision"] == 2
    assert store[project["project_id"]]["input_grade"] == "raster_human_locked"

    wrong = copy.deepcopy(registration)
    wrong.pop("registration_hash")
    wrong["source_hash"] = "f" * 64
    with pytest.raises(HTTPException) as error:
        routes_whole_home.save_whole_home_source_registration(
            project["project_id"], WholeHomeSourceRegistrationRequest(
                base_revision=2, operation_id="register_002", reviewer="reviewer",
                registration=wrong))
    assert error.value.detail["code"] == "raster_registration_source_hash_mismatch"


def test_server_prepares_raster_registration_and_measures_model_wall_ink(monkeypatch, tmp_path):
    image_path = tmp_path / "measured-plan.png"
    image = Image.new("RGB", (420, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 35, 380, 265), outline="black", width=10)
    image.save(image_path)
    project = {
        "project_id": "raster-prepare-home", "source_type": "floorplan", "revision": 1,
        "floorplan_path": str(image_path), "input_grade": "raster_draft",
        "geometry_acceptance_required": True, "operations": [],
        "model": {
            "schema_version": 2, "walls": [
                {"id": "top", "start": {"x": .4, "z": .35}, "end": {"x": 3.8, "z": .35}},
                {"id": "right", "start": {"x": 3.8, "z": .35}, "end": {"x": 3.8, "z": 2.65}},
                {"id": "bottom", "start": {"x": 3.8, "z": 2.65}, "end": {"x": .4, "z": 2.65}},
                {"id": "left", "start": {"x": .4, "z": 2.65}, "end": {"x": .4, "z": .35}},
            ],
        },
    }
    store = _memory_store(monkeypatch, project)
    monkeypatch.setattr(routes_whole_home, "MAIN_OUTPUT_DIR", str(tmp_path / "output"))
    result = routes_whole_home.prepare_whole_home_raster_registration(
        project["project_id"], WholeHomeRasterRegistrationPrepareRequest(
            base_revision=1, operation_id="raster_measure_001", reviewer="reviewer",
            origin_px=[0, 0], scale_anchors=[
                {"id": "width", "start_px": [40, 280], "end_px": [380, 280], "length_m": 3.4},
                {"id": "height", "start_px": [400, 35], "end_px": [400, 265], "length_m": 2.3},
            ],
        ))
    saved = store[project["project_id"]]
    assert saved["revision"] == 2
    assert saved["source_registration"]["scale_anchor_count"] == 2
    assert saved["raster_alignment_metrics"]["wall_centerline_p95_m"] <= .05
    assert result["geometry_contract"]["raster_alignment_metrics"]["wall_axis_count"] == 4


def test_manual_wall_confirmation_and_opening_annotation_are_audited(monkeypatch):
    project = _cad_project()
    project["model"]["walls"][0].update({
        "source": "cad_review_evidence",
        "review_status": "needs_review",
        "boundary_kind": "unresolved_review_evidence",
        "display_mode": "review_floor_trace",
        "height_m": .12,
        "thickness_m": .03,
    })
    project["model"]["wall_assemblies"] = [{
        "id": "assembly-1", "source_representation": "human_confirmed_ambiguous",
        "resolved_as": None, "source_centerline": [[0, 0], [4, 0]],
        "review_status": "needs_review", "source_entity_handles": ["10"],
        "source_entities": [], "cad_provenance": {},
    }]
    project["model"]["cad_facts_hash"] = cad_facts_hash(project["model"])
    project["cad_import"]["cad_facts_hash"] = project["model"]["cad_facts_hash"]
    store = _memory_store(monkeypatch, project)
    routes_whole_home.confirm_whole_home_cad_wall_assembly(
        project["project_id"], "assembly-1", WholeHomeCadWallAssemblyConfirmRequest(
            base_revision=1, operation_id="wallfix_01", reviewer="reviewer",
            reason="measured on CAD dimension", thickness_m=.2, height_m=2.8))
    assert store[project["project_id"]]["revision"] == 2
    assert store[project["project_id"]]["model"]["wall_assemblies"][0]["review_status"] == "accepted"
    confirmed_wall = store[project["project_id"]]["model"]["walls"][0]
    assert confirmed_wall["review_status"] == "accepted"
    assert confirmed_wall["source"] == "cad"
    assert confirmed_wall["boundary_kind"] == "centerline"
    assert confirmed_wall["height_m"] == 2.8
    assert confirmed_wall["thickness_m"] == .2
    assert "display_mode" not in confirmed_wall

    routes_whole_home.save_whole_home_cad_opening_annotations(
        project["project_id"], WholeHomeCadOpeningAnnotationsRequest(
            base_revision=2, operation_id="opening_01", reviewer="reviewer",
            annotations=[{
                "id": "door-manual", "wall_assembly_id": "assembly-1",
                "kind": "door", "start_offset_m": 1.0, "width_m": .9,
                "reason": "door swing symbol checked",
            }]))
    saved = store[project["project_id"]]
    assert saved["revision"] == 3
    assert saved["model"]["openings"][0]["wall_assembly_id"] == "assembly-1"
    assert saved["model"]["openings"][0]["reviewer"] == "reviewer"
