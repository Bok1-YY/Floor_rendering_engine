import copy
import asyncio

import pytest
from fastapi import HTTPException

from Floor_engine_server import routes_whole_home, server_schemas


def _project():
    return {
        "project_id": "professional-route-home", "revision": 2,
        "source_type": "floorplan", "input_grade": "raster_human_locked",
        "status": "verified", "stage": "locked", "error": "", "summary": "",
        "created_at": 1.0, "updated_at": 2.0, "verified": True, "verified_revision": 2,
        "source_registration": {
            "source_hash": "1" * 64, "registration_hash": "2" * 64,
        },
        "model": {
            "schema_version": 2, "coordinate_system": "metres-y-up",
            "width_m": 10, "depth_m": 8, "wall_height_m": 2.8,
            "input_grade": "raster_human_locked", "model_facts_hash": "3" * 64,
            "walls": [
                {"id": "w1", "start": {"x": 0, "z": 0}, "end": {"x": 10, "z": 0}, "thickness_m": .2, "height_m": 2.8, "kind": "exterior", "source": "human", "confidence": 1},
                {"id": "w2", "start": {"x": 10, "z": 0}, "end": {"x": 10, "z": 8}, "thickness_m": .2, "height_m": 2.8, "kind": "exterior", "source": "human", "confidence": 1},
                {"id": "w3", "start": {"x": 10, "z": 8}, "end": {"x": 0, "z": 8}, "thickness_m": .2, "height_m": 2.8, "kind": "exterior", "source": "human", "confidence": 1},
                {"id": "w4", "start": {"x": 0, "z": 8}, "end": {"x": 0, "z": 0}, "thickness_m": .2, "height_m": 2.8, "kind": "exterior", "source": "human", "confidence": 1},
            ],
            "rooms": [{
                "id": "living", "label": "客厅", "room_type": "living_room",
                "polygon": [{"x": .2, "z": .2}, {"x": 9.8, "z": .2}, {"x": 9.8, "z": 7.8}, {"x": .2, "z": 7.8}],
                "area_m2": 72.96, "selected": True, "semantic_status": "complete",
                "source": "human", "confidence": 1,
            }],
            "openings": [], "fixed_objects": [], "cameras": [], "uncertainties": [],
            "geometry_report": {"hard_errors": [], "warnings": []},
            "semantic_report": {"status": "complete", "hard_errors": [], "warnings": []},
        },
        "captures": [], "pano_captures": [], "operations": [],
    }


def _values():
    return {
        "wall_height_m": 2.8, "interior_door_height_m": 2.1,
        "window_sill_height_m": .9, "window_head_height_m": 2.1,
        "floor_finish_thickness_m": .015, "ceiling_drop_m": .08,
        "skirting_height_m": .08,
    }


@pytest.fixture
def store(monkeypatch):
    value = _project()
    rows = {value["project_id"]: copy.deepcopy(value)}
    monkeypatch.setattr(routes_whole_home, "load_project", lambda project_id: copy.deepcopy(rows.get(project_id)))
    monkeypatch.setattr(routes_whole_home, "_project_entry", lambda project_id: copy.deepcopy(rows.get(project_id)))

    def persist(project):
        rows[project["project_id"]] = copy.deepcopy(project)

    monkeypatch.setattr(routes_whole_home, "_persist_project", persist)
    return rows


def test_professional_routes_confirm_generate_lock_and_publish_proposal(store):
    project_id = "professional-route-home"
    capabilities = routes_whole_home.get_whole_home_professional_capabilities()
    assert capabilities["primary_inputs"][0] == "png"

    graph = routes_whole_home.get_whole_home_floorplan_graph(project_id)
    assert graph["review"]["status"] == "locked"

    draft_profile = routes_whole_home.get_whole_home_construction_profile(project_id)
    assert draft_profile["status"] == "assumptions_pending"
    profile_request = server_schemas.WholeHomeConstructionProfileRequest(
        base_revision=2, operation_id="profile_route_001", reviewer="sales", values=_values())
    routes_whole_home.confirm_whole_home_construction_profile(project_id, profile_request)
    assert store[project_id]["construction_profile"]["status"] == "confirmed"
    assert store[project_id]["revision"] == 2
    assert store[project_id]["verified"] is True

    preview = routes_whole_home.preview_whole_home_scene_recipe(
        project_id, server_schemas.WholeHomeSceneRecipePreviewRequest(variant_index=1))
    assert preview["status"] == "draft"
    assert store[project_id].get("scene_recipes") is None

    create_request = server_schemas.WholeHomeSceneRecipeCommitRequest(
        base_revision=2, operation_id="recipe_route_001", reviewer="sales", variant_index=1)
    routes_whole_home.create_whole_home_scene_recipe(project_id, create_request)
    recipe_id = store[project_id]["active_scene_recipe_id"]
    assert store[project_id]["scene_recipes"][0]["status"] == "draft"

    review_request = server_schemas.WholeHomeSceneRecipeReviewRequest(
        base_revision=2, operation_id="review_route_001", reviewer="sales",
        note="布局和通道已在灰模中复核", action="lock")
    routes_whole_home.review_whole_home_scene_recipe(project_id, recipe_id, review_request)
    assert store[project_id]["scene_recipes"][0]["status"] == "locked"
    assert routes_whole_home.get_whole_home_marketing_proposal(project_id)["status"] == "draft"
    assert "certified_panorama_count_below_3" in routes_whole_home.get_whole_home_marketing_proposal(project_id)["blockers"]


def test_professional_mutations_are_idempotent_and_reject_body_reuse(store):
    project_id = "professional-route-home"
    request = server_schemas.WholeHomeConstructionProfileRequest(
        base_revision=2, operation_id="profile_route_002", reviewer="sales", values=_values())
    routes_whole_home.confirm_whole_home_construction_profile(project_id, request)
    revision = store[project_id]["professional_revision"]
    routes_whole_home.confirm_whole_home_construction_profile(project_id, request)
    assert store[project_id]["professional_revision"] == revision

    changed = _values()
    changed["wall_height_m"] = 3.0
    with pytest.raises(HTTPException) as error:
        routes_whole_home.confirm_whole_home_construction_profile(
            project_id, server_schemas.WholeHomeConstructionProfileRequest(
                base_revision=2, operation_id="profile_route_002", reviewer="sales", values=changed))
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "professional_operation_id_conflict"


def test_style_pack_run_binds_locked_scene_and_rejects_old_capture(store, monkeypatch):
    project_id = "professional-route-home"
    routes_whole_home.confirm_whole_home_construction_profile(
        project_id, server_schemas.WholeHomeConstructionProfileRequest(
            base_revision=2, operation_id="profile_style_run", reviewer="sales", values=_values()))
    routes_whole_home.create_whole_home_scene_recipe(
        project_id, server_schemas.WholeHomeSceneRecipeCommitRequest(
            base_revision=2, operation_id="recipe_style_run", reviewer="sales", variant_index=1))
    recipe_id = store[project_id]["active_scene_recipe_id"]
    routes_whole_home.review_whole_home_scene_recipe(
        project_id, recipe_id, server_schemas.WholeHomeSceneRecipeReviewRequest(
            base_revision=2, operation_id="lock_style_run", reviewer="sales",
            note="3D checked", action="lock"))
    recipe = store[project_id]["scene_recipes"][0]
    store[project_id].update(floorplan_path="floorplan.png")
    store[project_id]["captures"] = [{
        "capture_id": "capture-scene", "camera_id": "camera-scene",
        "camera": {
            "id": "camera-scene", "name": "客厅机位", "room_id": "living",
            "position": {"x": 2, "y": 1.55, "z": 2},
            "target": {"x": 4, "y": 1.2, "z": 4}, "focal_length_mm": 24,
        },
        "room_id": "living", "status": "confirmed", "aspect_ratio": "4:3",
        "scene_recipe_id": recipe_id, "scene_hash": recipe["scene_hash"],
        "rgb_path": "rgb.png", "depth_path": "depth.png", "normal_path": "normal.png",
        "edge_path": "edge.png", "semantic_path": "semantic.png",
    }]
    monkeypatch.setattr(routes_whole_home, "_assert_cad_project_gate", lambda project: None)
    monkeypatch.setattr(routes_whole_home, "_assert_geometry_production_gate", lambda project: None)
    monkeypatch.setattr(routes_whole_home, "_valid_capture", lambda *args: True)
    monkeypatch.setattr(routes_whole_home, "load_config", lambda: {"gemini_api_key": "test-key"})
    monkeypatch.setattr(routes_whole_home, "ensure_replay_snapshot", lambda *args: ({}, {"snapshot_id": "s", "snapshot_hash": "h"}))
    monkeypatch.setattr(routes_whole_home, "_persist_run", lambda run: None)
    monkeypatch.setattr(routes_whole_home.state, "spawn", lambda coroutine: coroutine.close())

    request = server_schemas.WholeHomeRunRequest(
        project_id=project_id, capture_ids=["capture-scene"], material_mode="style_pack",
        scene_recipe_id=recipe_id, model_keys=["b2"], idempotency_key="style-run-one")
    response = asyncio.run(routes_whole_home._create_whole_home_run(request))
    try:
        stored = routes_whole_home._ACTIVE_RUNS[response["run_id"]]
        assert stored["scene_recipe_id"] == recipe_id
        assert stored["scene_hash"] == recipe["scene_hash"]
        assert stored["scene_recipe_snapshot"]["recipe_hash"] == recipe["recipe_hash"]
        assert stored["floor_path"] == ""
    finally:
        routes_whole_home._ACTIVE_RUNS.pop(response["run_id"], None)
        routes_whole_home._RUN_KEYS.pop(response["run_id"], None)

    store[project_id]["captures"][0]["scene_hash"] = "old-scene"
    stale_request = server_schemas.WholeHomeRunRequest(
        project_id=project_id, capture_ids=["capture-scene"], material_mode="style_pack",
        scene_recipe_id=recipe_id, model_keys=["b2"], idempotency_key="style-run-stale")
    with pytest.raises(HTTPException) as stale:
        asyncio.run(routes_whole_home._create_whole_home_run(stale_request))
    assert stale.value.detail["code"] == "capture_scene_recipe_mismatch"
