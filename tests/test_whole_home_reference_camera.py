from __future__ import annotations

import copy
import base64
import io
import math

import pytest
from fastapi import HTTPException
from PIL import Image

from Floor_engine_server import routes_whole_home, server_schemas, whole_home_engine
from Floor_engine_server.whole_home_cad import JUSTEASY_REFERENCE_CONTRACT
from Floor_engine_server.whole_home_reference_camera import (
    _anchor_visible,
    _living_open_semantic_boundaries,
    _yaw_evaluation,
    bind_reference_slots_to_rooms,
    evaluate_subject_id_pixels,
    generate_reference_camera_candidates,
    split_reference_contract,
)


def _reference_model() -> dict:
    profiles = [
        "living_room", "kitchen", "bedroom_master", "bedroom_secondary",
        "bathroom_master", "bathroom_secondary", "dry_vanity",
    ]
    objects_for_profile = {
        "living_room": ["dining_table", "tv", "sofa"],
        "kitchen": ["hob", "hood", "kitchen_run", "fridge", "sink"],
        "bedroom_master": ["bed"],
        "bedroom_secondary": ["bed"],
        "bathroom_master": ["toilet", "shower_zone", "basin"],
        "bathroom_secondary": ["toilet", "shower_zone"],
        "dry_vanity": ["basin", "faucet", "mirror"],
    }
    rooms, walls, openings, objects = [], [], [], []
    for index, profile in enumerate(profiles):
        x0 = index * 12.0
        room_id = f"room_{profile}"
        polygon = [{"x": x0, "z": 0}, {"x": x0 + 8, "z": 0},
                   {"x": x0 + 8, "z": 6}, {"x": x0, "z": 6}]
        rooms.append({"id": room_id, "label": profile, "room_type": profile,
                      "semantic_profile": "bathroom" if "bathroom" in profile or profile == "dry_vanity" else profile,
                      "reference_room_profile": profile, "polygon": polygon, "selected": True,
                      "floor_elevation_m": 0})
        room_walls = []
        for edge_index, (first, second) in enumerate(zip(polygon, polygon[1:] + polygon[:1])):
            wall = {"id": f"wall_{index}_{edge_index}", "start": copy.deepcopy(first),
                    "end": copy.deepcopy(second), "height_m": 2.8, "thickness_m": .12}
            walls.append(wall)
            room_walls.append(wall)
        door = {"id": f"door_{index}", "wall_id": room_walls[0]["id"], "kind": "door",
                "offset_m": 3.5, "width_m": 1, "reference_anchor_ready": True}
        openings.append(door)
        if profile in {"living_room", "bedroom_secondary", "bathroom_secondary"}:
            openings.append({"id": f"window_{index}", "wall_id": room_walls[2]["id"], "kind": "window",
                             "offset_m": 3.4, "width_m": 1.2, "reference_anchor_ready": True})
        roles = objects_for_profile[profile]
        for role_index, role in enumerate(roles):
            objects.append({
                "id": f"object_{index}_{role}", "name": role, "kind": role, "semantic_role": role,
                "room_id": room_id, "position": {"x": x0 + 2.1 + role_index * .85, "y": 0, "z": 4.6},
                "size": {"x": .45, "y": .8, "z": .45}, "rotation_y_deg": 0,
                "clearance_m": .12, "observed": True, "source": "cad",
                "reference_anchor_ready": True,
            })
    return {
        "schema_version": 2, "model_id": "fixture", "cad_facts_hash": "cad-fixture",
        "width_m": 80, "depth_m": 6, "rooms": rooms, "walls": walls,
        "openings": openings, "fixed_objects": objects, "cameras": [],
        "reference_anchor_report": {"status": "ready", "hard_errors": []},
    }


def test_reference_generates_nine_independent_slot_pools_without_20mm():
    result = generate_reference_camera_candidates(
        _reference_model(), JUSTEASY_REFERENCE_CONTRACT, max_per_slot=3, project_revision=7)
    assert result["status"] == "ready", result.get("hard_errors")
    assert len(result["slot_pools"]) == 9
    assert {pool["slot_id"] for pool in result["slot_pools"]} == {
        slot["slot_id"] for slot in JUSTEASY_REFERENCE_CONTRACT["slots"]}
    assert all(pool["pool_scope"] == "reference_slot" and pool["candidates"]
               for pool in result["slot_pools"])
    # Spacious fixture rooms all have legal interior landings.  A doorway or
    # inferred circulation origin must never outrank those in-room poses.
    assert all(pool["candidates"][0]["camera"]["origin_scope"] == "inside_room"
               for pool in result["slot_pools"])
    assert all(pool["primary_origin_scope"] == "inside_room"
               for pool in result["slot_pools"])
    assert all(20 not in pool["focal_samples_mm"] for pool in result["slot_pools"])
    for candidate in result["candidates"]:
        camera = candidate["camera"]
        if camera.get("origin_scope") == "adjacent_portal":
            assert camera.get("origin_room_ids")
        if camera.get("origin_scope") == "cad_semantic_adjacent_free_space":
            assert not camera.get("portal_opening_id")
        assert camera["position"]["y"] == 1.45
        horizontal = math.hypot(
            camera["target"]["x"] - camera["position"]["x"],
            camera["target"]["z"] - camera["position"]["z"],
        )
        pitch = math.degrees(math.atan2(
            camera["target"]["y"] - camera["position"]["y"], horizontal))
        assert abs(pitch) <= 10
        assert camera["reference_contract_validation"]["safe_frame_status"] == "pending_browser"
        assert camera["reference_contract_validation"]["safe_frame_pass"] is None
    living = [pool for pool in result["slot_pools"] if pool["slot_id"].startswith("living_")]
    first, second = (pool["candidates"][0]["camera"]["position"] for pool in living)
    assert math.dist((first["x"], first["z"]), (second["x"], second["z"])) >= .9
    secondary = [pool for pool in result["slot_pools"] if pool["slot_id"].startswith("secondary_bed_")]
    first, second = (pool["candidates"][0]["camera"]["position"] for pool in secondary)
    assert (first["x"], first["z"]) != (second["x"], second["z"])


def test_secondary_binding_blocks_more_than_two_rooms():
    model = _reference_model()
    secondary = next(row for row in model["rooms"] if row["reference_room_profile"] == "bedroom_secondary")
    for suffix in ("b", "c"):
        room = copy.deepcopy(secondary)
        room["id"] = f"secondary_{suffix}"
        model["rooms"].append(room)
    result = bind_reference_slots_to_rooms(model, split_reference_contract(JUSTEASY_REFERENCE_CONTRACT))
    assert result["status"] == "blocked"
    assert any(row["code"] == "reference_secondary_room_binding_ambiguous" for row in result["hard_errors"])


def test_subject_id_pixels_use_top_left_exact_colors_and_fail_safe_frame():
    image = Image.new("RGB", (10, 10), (0, 0, 0))
    for y in range(2, 5):
        for x in range(2, 5):
            image.putpixel((x, y), (1, 2, 3))
    legend = {"subjects": [{"subject": "bed", "anchor_id": "bed_1", "color": [1, 2, 3]}]}
    passing = evaluate_subject_id_pixels(
        image, legend, ["bed"], {"x_min": .1, "x_max": .9, "y_min": .1, "y_max": .9})
    assert passing["pass"] is True
    assert passing["pixel_origin"] == "top-left"
    assert passing["must_show_bounds"][0]["y_min"] == .2
    failing = evaluate_subject_id_pixels(
        image, legend, ["bed"], {"x_min": .3, "x_max": .9, "y_min": .1, "y_max": .9})
    assert failing["pass"] is False
    assert any(row["code"] == "subject_id_outside_safe_frame" for row in failing["hard_errors"])


def test_missing_anchor_is_explicitly_blocked():
    model = _reference_model()
    model["fixed_objects"] = [row for row in model["fixed_objects"]
                              if row["semantic_role"] not in {"hood", "hob"}]
    result = generate_reference_camera_candidates(model, JUSTEASY_REFERENCE_CONTRACT, max_per_slot=1)
    assert result["status"] == "blocked"
    kitchen = next(pool for pool in result["slot_pools"] if pool["slot_id"] == "kitchen_cookline_elevation")
    assert kitchen["status"] == "blocked"
    assert any(row["code"] == "reference_anchor_group_missing" for row in kitchen["hard_errors"])


def test_opening_crosses_nearby_parallel_wall_faces_but_not_a_deeper_wall():
    opening = {"id": "window", "wall_id": "source", "kind": "window",
               "offset_m": 1.0, "width_m": 1.0, "review_status": "accepted"}
    anchor = {"anchor_id": "window", "anchor_kind": "opening", "role": "window",
              "position": {"x": 0, "z": 1.5}, "opening": opening}
    walls = [
        {"id": "source", "start": {"x": 0, "z": 0}, "end": {"x": 0, "z": 4}},
        {"id": "finish", "start": {"x": .24, "z": 0}, "end": {"x": .24, "z": 4}},
    ]
    model = {"walls": walls, "openings": [opening], "fixed_objects": []}
    assert _anchor_visible(model, (1.5, 1.5), anchor, 270, 24, "4:3")[0] is True
    model["walls"].append(
        {"id": "deep", "start": {"x": .96, "z": 0}, "end": {"x": .96, "z": 4}})
    passed, reason, _ = _anchor_visible(model, (1.5, 1.5), anchor, 270, 24, "4:3")
    assert passed is False
    assert reason == "wall_occlusion"


def test_optional_reference_subject_scores_when_visible_but_never_forces_invention():
    model = {"walls": [], "openings": [], "fixed_objects": []}
    groups = [{"subject": "bed", "required": True, "alternatives": [{
        "anchor_id": "bed", "anchor_kind": "fixed_object", "role": "bed",
        "position": {"x": 0, "z": 3},
    }]}, {"subject": "optional window", "required": False, "alternatives": [{
        "anchor_id": "window", "anchor_kind": "opening", "role": "window",
        "position": {"x": 3, "z": 0},
    }]}]
    result = _yaw_evaluation(model, (0, 0), groups, 0, 35, "4:3")
    assert result["pass"] is True
    assert [row["subject"] for row in result["subjects"]] == ["bed"]
    assert result["omitted_optional_subjects"][0]["subject"] == "optional window"


def test_wall_free_open_plan_overlap_becomes_reference_only_corridor_anchor():
    living = {
        "id": "living", "semantic_profile": "living_room", "reference_room_profile": "living_room",
        "polygon": [{"x": 3, "z": 2}, {"x": 10, "z": 2},
                    {"x": 10, "z": 7}, {"x": 3, "z": 7}],
    }
    kitchen = {
        "id": "kitchen", "semantic_profile": "kitchen", "reference_room_profile": "kitchen",
        "polygon": [{"x": 0, "z": 0}, {"x": 5, "z": 0},
                    {"x": 5, "z": 3}, {"x": 0, "z": 3}],
    }
    model = {"rooms": [living, kitchen], "walls": [], "fixed_objects": [], "openings": []}
    anchors = _living_open_semantic_boundaries(model, living)
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor["anchor_kind"] == "cad_open_semantic_boundary"
    assert anchor["adjacent_room_id"] == "kitchen"
    assert anchor["derivation"] == "audited_cad_wall_free_semantic_overlap_v1"

    # A real CAD wall through the overlap removes the derived open passage.
    model["walls"] = [
        {"id": f"closed_{index}", "start": {"x": 3 + index * .2, "z": 2},
         "end": {"x": 3 + index * .2, "z": 3}}
        for index in range(11)
    ]
    assert _living_open_semantic_boundaries(model, living) == []


def _png_data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _capture_fixture(monkeypatch, tmp_path):
    model = _reference_model()
    proposal = generate_reference_camera_candidates(
        model, JUSTEASY_REFERENCE_CONTRACT, max_per_slot=1, project_revision=7)
    candidate = next(row for row in proposal["candidates"] if row["slot_id"] == "living_tv_window_axis")
    project = {
        "project_id": "reference-capture", "verified": True, "revision": 7,
        "model": model, "floorplan_path": "", "captures": [], "operations": [],
        "reference_contract": copy.deepcopy(JUSTEASY_REFERENCE_CONTRACT),
        "reference_camera_proposals": [proposal],
    }
    monkeypatch.setattr(routes_whole_home, "_project_entry", lambda project_id: project)
    monkeypatch.setattr(routes_whole_home, "_persist_project", lambda value: None)
    monkeypatch.setattr(routes_whole_home, "save_camera_plan_overlay", lambda *args: "overlay.png")
    monkeypatch.setattr(whole_home_engine, "ASSET_DIR", str(tmp_path / "captures"))
    monkeypatch.setattr(whole_home_engine, "to_url", lambda path: f"/fixture/{path}" if path else "")
    return project, proposal, candidate


def test_reference_capture_recomputes_subject_pixels_and_rejects_client_bounds(monkeypatch, tmp_path):
    project, proposal, candidate = _capture_fixture(monkeypatch, tmp_path)
    subjects = candidate["camera"]["reference_contract_validation"]["must_show_subjects"]
    legend_rows = []
    client_bounds = []
    image = Image.new("RGB", (64, 48), (0, 0, 0))
    for index, subject in enumerate(subjects, 1):
        color = [0, 0, index]
        legend_rows.append({**subject, "color": color})
        x0 = 10 + (index - 1) * 12
        for y in range(12, 25):
            for x in range(x0, x0 + 7):
                image.putpixel((x, y), tuple(color))
        client_bounds.append({"subject": subject["subject"], "anchor_id": subject["anchor_id"],
                              "x_min": x0 / 64, "x_max": (x0 + 7) / 64,
                              "y_min": 12 / 48, "y_max": 25 / 48})
    camera = copy.deepcopy(candidate["camera"])
    camera["candidate_id"] = candidate["candidate_id"]
    camera["render_gate"] = {"version": "fixture", "pass": True, "status": "pass", "profile": "living_room"}
    camera["reference_contract_validation"]["must_show_bounds"] = client_bounds
    blank = _png_data_url(Image.new("RGB", (64, 48), "white"))
    request = server_schemas.WholeHomeCaptureRequest(
        camera=camera, rgb_data_url=blank, depth_data_url=blank, normal_data_url=blank,
        semantic_data_url=blank, subject_id_data_url=_png_data_url(image),
        subject_id_legend={"version": "whole-home-subject-id-v1", "pixel_origin": "top-left",
                           "subjects": legend_rows},
        room_id=candidate["room_id"], plan_id=proposal["proposal_id"],
        candidate_id=candidate["candidate_id"], reference_slot_id=candidate["slot_id"],
        reference_proposal_id=proposal["proposal_id"], reference_proposal_hash=proposal["proposal_hash"],
    )
    view = routes_whole_home.save_whole_home_capture("reference-capture", request)
    evidence = view["captures"][-1]["camera"]["reference_contract_validation"]
    assert evidence["safe_frame_pass"] is True
    assert evidence["pixel_origin"] == "top-left"
    assert evidence["buffer_sha"]
    assert all(row["x_min"] > 0 and row["x_max"] < 1 for row in evidence["must_show_bounds"])
    assert view["captures"][-1]["subject_id_url"]
    assert project["captures"][-1]["reference_proposal_id"] == proposal["proposal_id"]
    tampered = request.model_copy(deep=True)
    tampered.camera["reference_contract_validation"]["must_show_bounds"][0]["x_min"] = 0
    with pytest.raises(HTTPException) as error:
        routes_whole_home.save_whole_home_capture("reference-capture", tampered)
    assert error.value.detail["code"] == "reference_subject_bounds_tampered"


@pytest.mark.parametrize("tamper", ["position", "focal", "proposal_hash", "stale_revision"])
def test_reference_capture_rejects_camera_proposal_and_stale_tampering(monkeypatch, tmp_path, tamper):
    project, proposal, candidate = _capture_fixture(monkeypatch, tmp_path)
    camera = copy.deepcopy(candidate["camera"])
    proposal_hash = proposal["proposal_hash"]
    if tamper == "position":
        camera["position"]["x"] += .01
    elif tamper == "focal":
        camera["focal_length_mm"] += 1
    elif tamper == "proposal_hash":
        proposal_hash = "0" * 64
    else:
        project["revision"] += 1
    request = server_schemas.WholeHomeCaptureRequest(
        camera=camera, rgb_data_url="data:image/png;base64," + "A" * 32,
        depth_data_url="data:image/png;base64," + "A" * 32,
        normal_data_url="data:image/png;base64," + "A" * 32,
        semantic_data_url="data:image/png;base64," + "A" * 32,
        subject_id_data_url="data:image/png;base64," + "A" * 32,
        candidate_id=candidate["candidate_id"], reference_slot_id=candidate["slot_id"],
        reference_proposal_id=proposal["proposal_id"], reference_proposal_hash=proposal_hash,
    )
    with pytest.raises(HTTPException) as error:
        routes_whole_home.save_whole_home_capture("reference-capture", request)
    assert error.value.status_code == 409
    assert error.value.detail["code"] in {
        "reference_camera_tampered", "reference_proposal_not_found_or_tampered", "reference_proposal_stale",
    }


def test_reference_candidate_endpoint_is_local_and_persists_slot_proposal(monkeypatch, tmp_path):
    contract = copy.deepcopy(JUSTEASY_REFERENCE_CONTRACT)
    for index, slot in enumerate(contract["slots"]):
        path = tmp_path / f"reference-{index}.jpg"
        path.write_bytes(b"fixture")
        slot["reference_asset"].update(status="verified", local_path=str(path))
    project = {
        "project_id": "reference-endpoint", "verified": True, "revision": 4,
        "model": _reference_model(), "reference_contract": contract,
        "operations": [],
    }
    persisted = []
    monkeypatch.setattr(routes_whole_home, "_project_entry", lambda project_id: project)
    monkeypatch.setattr(routes_whole_home, "_persist_project", lambda value: persisted.append(copy.deepcopy(value)))
    monkeypatch.setattr(routes_whole_home, "load_config", lambda: pytest.fail("reference candidate preflight cannot read Gemini credentials"))
    request = server_schemas.WholeHomeCameraCandidatesRequest(
        mode="reference", contract_id=JUSTEASY_REFERENCE_CONTRACT["contract_id"],
        aspect_ratio="4:3", max_per_room=1,
    )
    result = routes_whole_home.create_whole_home_camera_candidates("reference-endpoint", request)
    assert result["status"] == "ready"
    assert len(result["slot_pools"]) == 9
    assert project["reference_camera_proposals"][-1]["proposal_hash"] == result["proposal_hash"]
    assert persisted[-1]["operations"][-1]["type"] == "reference_camera_candidates_local"


def test_project_view_exposes_safe_reference_asset_url_and_nested_mapping(monkeypatch, tmp_path):
    contract = copy.deepcopy(JUSTEASY_REFERENCE_CONTRACT)
    asset_path = tmp_path / "asset.jpg"
    Image.new("RGB", (2, 2), "white").save(asset_path)
    contract["slots"][0]["reference_asset"].update(status="verified", local_path=str(asset_path))
    project = {"project_id": "asset-view", "reference_contract": contract, "model": {}, "captures": []}
    view = whole_home_engine.project_view(project)
    first = view["reference_contract"]["slots"][0]
    assert "local_path" not in first["reference_asset"]
    assert first["reference_asset"]["url"].endswith("/reference-assets/living_openplan_axis")
    assert first["reference_asset"]["status"] == "verified"
    assert first["reference_viewpoint"]["point_mapping"]["status"] == "not_available"


def test_reference_run_gate_honors_slot_subject_safe_frame_overrides():
    contract = copy.deepcopy(JUSTEASY_REFERENCE_CONTRACT)
    slot = next(row for row in contract["slots"]
                if row["slot_id"] == "living_openplan_axis")
    # Simulate a project created before subject-specific framing was added.
    slot.pop("subject_safe_frame_overrides", None)
    capture = {
        "reference_slot_id": slot["slot_id"],
        "camera": {
            "reference_slot_id": slot["slot_id"],
            "position": {"x": 1.0, "y": 1.45, "z": 1.0},
            "target": {"x": 3.0, "y": 1.45, "z": 1.0},
            "focal_length_mm": 24,
            "reference_contract_validation": {
                "slot_id": slot["slot_id"],
                "scene_id": str(slot["reference_viewpoint"]["scene_id"]),
                "landing_policy_mode": "cad_semantic_relative_region",
                "landing_source": "inferred_from_reference_visual_and_cad_anchors",
                "cad_position_pass": True,
                "collision_pass": True,
                "visibility_pass": True,
                "safe_frame_pass": True,
                "must_show_subjects": [
                    {"subject": "CAD-authentic corridor"},
                ],
                "must_show_bounds": [{
                    "subject": "CAD-authentic corridor",
                    "x_min": .1, "x_max": .3, "y_min": .2, "y_max": 1.0,
                }],
            },
        },
    }
    routes_whole_home._assert_reference_slot_camera(
        contract, slot, capture, slot["slot_id"])


def test_kitchen_worktop_may_touch_only_the_bottom_frame_edge():
    contract = split_reference_contract(JUSTEASY_REFERENCE_CONTRACT)
    slot = next(row for row in contract["slots"]
                if row["slot_id"] == "kitchen_cookline_elevation")
    assert slot["subject_safe_frame_overrides"] == {
        "worktop": {"x_min": .08, "x_max": .92, "y_min": .08, "y_max": 1.0},
    }
    assert "hob" not in slot["subject_safe_frame_overrides"]
    assert "hood" not in slot["subject_safe_frame_overrides"]


def test_reference_room_profile_binding_keeps_one_cad_bathroom_as_one_suite():
    assert routes_whole_home._reference_room_profile_binding(
        "bathroom_master", "bathroom_master") == "exact_profile"
    assert routes_whole_home._reference_room_profile_binding(
        "bathroom_master", "bathroom_secondary") == "shared_cad_wet_dry_suite"
    assert routes_whole_home._reference_room_profile_binding(
        "bathroom_master", "dry_vanity") == "shared_cad_wet_dry_suite"
    assert routes_whole_home._reference_room_profile_binding(
        "living_room", "bathroom_secondary") == ""


def test_reference_capture_batch_is_created_before_render_and_resumes_by_slot(
        monkeypatch):
    proposal = {
        "proposal_id": "proposal-resume", "proposal_hash": "a" * 64,
        "project_revision": 3, "cad_facts_hash": "cad-hash",
        "model_facts_hash": "model-hash",
        "slot_pools": [{
            "slot_id": "secondary-bath", "candidates": [{
                "candidate_id": "candidate-one", "room_id": "bathroom",
                "camera": {"id": "camera-one", "reference_contract_validation": {}},
            }],
        }],
    }
    project = {
        "project_id": "resume-project", "verified": True, "revision": 3,
        "model": {"cad_facts_hash": "cad-hash"}, "captures": [], "operations": [],
        "reference_contract": {"slots": []},
        "reference_camera_proposals": [{
            "proposal_id": proposal["proposal_id"],
            "proposal_hash": proposal["proposal_hash"],
        }],
    }

    def project_entry(_project_id):
        return project

    def cas_update(_project_id, mutator, **_kwargs):
        before = whole_home_engine.state_hash(project)
        updated = mutator(copy.deepcopy(project))
        project.clear()
        project.update(updated)
        return copy.deepcopy(project), before, whole_home_engine.state_hash(project)

    monkeypatch.setattr(routes_whole_home, "_project_entry", project_entry)
    monkeypatch.setattr(routes_whole_home, "cas_update_project", cas_update)
    monkeypatch.setattr(routes_whole_home, "load_reference_camera_proposal", lambda row: proposal)
    monkeypatch.setattr(routes_whole_home, "reference_model_facts_hash", lambda model: "model-hash")
    monkeypatch.setattr(routes_whole_home, "split_reference_contract", lambda contract: contract)
    monkeypatch.setattr(routes_whole_home, "project_view", lambda value: copy.deepcopy(value))
    request = server_schemas.WholeHomeReferenceCaptureBatchRequest(
        reference_proposal_id=proposal["proposal_id"],
        reference_proposal_hash=proposal["proposal_hash"], width=192, height=144)

    monkeypatch.setattr(
        routes_whole_home, "render_reference_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("renderer crashed")))
    with pytest.raises(RuntimeError, match="renderer crashed"):
        routes_whole_home.render_whole_home_reference_captures("resume-project", request)
    batches = project["reference_software_capture_batches"]
    assert len(batches) == 1
    batch_id = batches[0]["batch_id"]
    assert batches[0]["status"] == "running"
    assert batches[0]["slots"][0]["status"] == "pending"

    image = Image.new("RGB", (16, 12), "white")
    rendered = {
        "pass": True, "renderer": "numpy_zbuffer_v1",
        "render_gate": {
            "version": "whole-home-reference-render-gate-v3-software", "pass": True},
        "subject_evidence": {
            "width": 16, "height": 12, "must_show_bounds": [],
            "version": "whole-home-subject-pixel-gate-v2"},
        "images": {key: image for key in (
            "rgb", "depth", "normal", "edge", "semantic", "subject_id")},
        "legend": {"subjects": []},
    }
    monkeypatch.setattr(
        routes_whole_home, "render_reference_candidate", lambda *args, **kwargs: rendered)

    def save_capture(_project_id, capture_request):
        project["captures"].append({
            "capture_id": "capture-resumed", "status": "confirmed",
            "candidate_id": capture_request.candidate_id,
            "reference_slot_id": capture_request.reference_slot_id,
            "reference_proposal_id": capture_request.reference_proposal_id,
            "reference_proposal_hash": capture_request.reference_proposal_hash,
            "camera": copy.deepcopy(capture_request.camera),
        })
        return copy.deepcopy(project)

    monkeypatch.setattr(routes_whole_home, "save_whole_home_capture", save_capture)
    response = routes_whole_home.render_whole_home_reference_captures(
        "resume-project", request)
    assert len(project["reference_software_capture_batches"]) == 1
    assert response["batch"]["batch_id"] == batch_id
    assert response["batch"]["status"] == "ready"
    assert response["batch"]["slots"][0]["status"] == "saved"
    assert response["batch"]["saved"] == [{
        "slot_id": "secondary-bath", "candidate_id": "candidate-one",
        "capture_id": "capture-resumed"}]
