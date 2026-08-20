import json
import zipfile

import pytest
from fastapi import HTTPException
from PIL import Image

from Floor_engine_server import floorplan_annotations, floorplan_engine, routes_floorplans, routes_inpaint
from Floor_engine_server.server_schemas import (
    FloorplanCamera, FloorplanConfirmRequest, FloorplanSpatialPlanUpdateRequest, FloorplanSuiteRequest,
    InpaintTarget, SuiteColorMatchRequest,
)


def _room(room_id="living_1", label="客厅", room_type="living"):
    return {
        "id": room_id,
        "label": label,
        "room_type": room_type,
        "polygon": [
            {"x": 100, "y": 100}, {"x": 500, "y": 100},
            {"x": 500, "y": 500}, {"x": 100, "y": 500},
        ],
        "adjacent_room_ids": [],
        "dimensions_text": "4.2m × 3.8m",
        "confidence": 0.91,
    }


def test_normalize_floorplan_payload_converts_thousand_coordinates():
    result = floorplan_engine.normalize_floorplan_payload({
        "summary": "two rooms",
        "orientation": "north up",
        "rooms": [_room()],
        "openings": [{
            "id": "window_1", "kind": "window",
            "points": [{"x": 100, "y": 100}, {"x": 500, "y": 100}],
            "room_ids": ["living_1"], "confidence": 0.8,
        }],
    })
    assert result["rooms"][0]["polygon"][1] == {"x": 0.5, "y": 0.1}
    assert result["openings"][0]["points"][1]["x"] == 0.5
    assert result["rooms"][0]["selected"] is True


@pytest.mark.parametrize("label", ["卫生间", "主卫", "Balcony", "Shower room"])
def test_normalize_floorplan_payload_disables_wet_rooms(label):
    room = _room("wet_1", label, "bathroom")
    result = floorplan_engine.normalize_floorplan_payload({"rooms": [room], "openings": []})
    assert result["rooms"][0]["selected"] is False
    assert result["rooms"][0]["apply_floor"] is False


def test_normalize_floorplan_payload_rejects_invalid_polygons():
    room = _room()
    room["polygon"] = [{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}]
    result = floorplan_engine.normalize_floorplan_payload({"rooms": [room], "openings": []})
    assert result["rooms"] == []


def test_choose_anchor_prefers_living_room():
    bedroom = floorplan_engine.normalize_floorplan_payload({
        "rooms": [_room("bed_1", "主卧", "bedroom"), _room()], "openings": [],
    })["rooms"]
    assert floorplan_engine.choose_anchor_room(bedroom)["id"] == "living_1"


def test_build_room_prompt_labels_every_image_role(tmp_path):
    proxy = tmp_path / "proxy.png"
    floor = tmp_path / "floor.png"
    style = tmp_path / "style.png"
    anchor = tmp_path / "anchor.png"
    for path in (proxy, floor, style, anchor):
        path.write_bytes(b"image")
    room = floorplan_engine.normalize_floorplan_payload({"rooms": [_room()], "openings": []})["rooms"][0]
    room["camera"] = {"position": {"x": .2, "y": .4}, "target": {"x": .5, "y": .3}}
    suite = {
        "rooms": [room], "openings": [], "floor_path": str(floor),
        "style_ref_path": str(style), "anchor_path": str(anchor),
        "style": "现代自然", "lighting": "自然光", "style_brief": "统一木色", "prompt": "真实生活感",
    }
    room["view_proxy_path"] = str(proxy)
    prompt, paths = floorplan_engine.build_room_prompt(suite, room, str(tmp_path / "annotated.png"))
    assert paths == [str(proxy), str(style), str(anchor)]
    assert "Image 1 is the approved clay-render camera proxy" in prompt
    assert "Image 3 is an approved home reference" in prompt
    assert "Never add, remove or move a wall" in prompt
    assert str(floor) not in paths
    assert "annotated floor plan" not in prompt


def test_spatial_plan_camera_math_overrides_ai_frame_guess():
    plan = floorplan_engine.normalize_spatial_plan({
        "space_summary": "客餐厅一体",
        "camera_view": {"expected_composition": "面向东侧"},
        "architecture": {"required_openings": ["北墙窗户"]},
        "zones": [
            {"name": "餐区", "function": "用餐", "plan_position": {"x": .5, "y": .1},
             "frame_position": "background_right", "depth": "far", "required_visible": True},
            {"name": "玄关", "function": "进入", "plan_position": {"x": .1, "y": .5},
             "frame_position": "foreground_center", "depth": "near", "required_visible": False},
        ],
        "furniture": [], "hard_constraints": ["北墙窗户必须保留"],
        "must_not_appear": [], "uncertainties": [],
    })
    room = {"camera": {"position": {"x": .2, "y": .5}, "target": {"x": .8, "y": .5}}}
    compiled = floorplan_engine.compile_spatial_plan(plan, room)
    assert compiled["zones"][0]["computed_frame_position"] == "midground_left"
    assert compiled["zones"][1]["computed_frame_position"] == "behind_camera"
    assert "overrides conflicting AI prose" in compiled["camera_math"]["rule"]


def test_two_pass_prompts_isolate_geometry_from_floor_material(tmp_path):
    proxy = tmp_path / "proxy.jpg"
    structure = tmp_path / "structure.jpg"
    floor = tmp_path / "floor.jpg"
    for path in (proxy, structure, floor):
        path.write_bytes(b"image")
    room = floorplan_engine.normalize_floorplan_payload({"rooms": [_room()], "openings": []})["rooms"][0]
    room["camera"] = {"position": {"x": .2, "y": .5}, "target": {"x": .8, "y": .5}}
    room["spatial_plan"] = floorplan_engine.normalize_spatial_plan({
        "space_summary": "客厅",
        "camera_view": {}, "architecture": {"required_openings": ["东墙门洞"]},
        "zones": [], "furniture": [], "hard_constraints": ["东墙门洞必须保留"],
        "must_not_appear": ["相机背后的卧室"], "uncertainties": [],
    })
    room["view_proxy_path"] = str(proxy)
    suite = {"rooms": [room], "openings": [], "floor_path": str(floor)}
    prompt, paths = floorplan_engine.build_room_prompt(suite, room, str(tmp_path / "annotated.jpg"))
    assert paths == [str(proxy)]
    assert "clay-render camera proxy" in prompt
    assert "相机背后的卧室" in prompt
    material_prompt, material_paths = floorplan_engine.build_floor_material_prompt(suite, room, str(structure))
    assert material_paths == [str(structure), str(floor)]
    assert "Replace only the visible floor finish" in material_prompt
    assert "Do not paste the sample as an inset" in material_prompt


def test_evaluation_weights_layout_and_material_first(monkeypatch, tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"x")
    checks = [{**item, "status": "pass", "evidence": "verified"}
              for item in floorplan_engine._qa_constraints({})]
    monkeypatch.setattr(floorplan_engine, "call_gemini_json", lambda *args, **kwargs: ({
        "layout_fidelity": 80,
        "material_fidelity": 90,
        "camera_match": 75,
        "visual_quality": 60,
        "suite_consistency": 100,
        "hard_fail": False,
        "checks": checks,
        "warnings": [],
        "summary": "usable",
    }, None))
    result, error = floorplan_engine.evaluate_candidate("key", str(image), str(image), str(image))
    assert error is None
    assert result["total"] == 80
    assert result["eligible_for_recommendation"] is True


def test_evaluation_low_material_is_not_recommended(monkeypatch, tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(floorplan_engine, "call_gemini_json", lambda *args, **kwargs: ({
        "layout_fidelity": 95, "material_fidelity": 40, "camera_match": 90,
        "visual_quality": 90, "suite_consistency": 100, "warnings": [], "summary": "wrong floor",
    }, None))
    result, _ = floorplan_engine.evaluate_candidate("key", str(image), str(image), str(image))
    assert result["eligible_for_recommendation"] is False
    assert any("地板材料" in warning for warning in result["warnings"])


def test_evaluation_hard_constraint_failure_blocks_recommendation(monkeypatch, tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(floorplan_engine, "call_gemini_json", lambda *args, **kwargs: ({
        "layout_fidelity": 96, "material_fidelity": 95, "camera_match": 94,
        "visual_quality": 92, "suite_consistency": 100, "hard_fail": False,
        "checks": [{
            "constraint": "东墙门洞必须保留", "status": "fail", "severity": "hard",
            "evidence": "生成图将门洞改成了完整实墙",
        }],
        "warnings": [], "summary": "画面漂亮但结构错误",
    }, None))
    result, error = floorplan_engine.evaluate_candidate(
        "key", str(image), str(image), str(image), spatial_plan={"hard_constraints": ["东墙门洞必须保留"]},
    )
    assert error is None
    assert result["hard_fail"] is True
    assert result["layout_fidelity"] == 55
    assert result["eligible_for_recommendation"] is False
    assert any("禁止系统推荐" in warning for warning in result["warnings"])


def test_evaluation_omitted_hard_check_pauses_recommendation(monkeypatch, tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(floorplan_engine, "call_gemini_json", lambda *args, **kwargs: ({
        "layout_fidelity": 90, "material_fidelity": 90, "camera_match": 90,
        "visual_quality": 90, "suite_consistency": 100, "hard_fail": False,
        "checks": [], "warnings": [], "summary": "evaluator forgot the checklist",
    }, None))
    result, _ = floorplan_engine.evaluate_candidate(
        "key", str(image), str(image), str(image),
        spatial_plan={"hard_constraints": ["北墙窗户必须保留"]},
    )
    assert result["hard_fail"] is False
    assert result["verification_incomplete"] is True
    assert result["eligible_for_recommendation"] is False
    assert result["checks"][0]["constraint_id"] == "C001"
    assert result["checks"][0]["status"] == "uncertain"


def test_spatial_plan_cannot_lock_without_testable_hard_constraint(monkeypatch):
    room = {
        **_room(), "selected": True, "cameras": [_camera()],
        "primary_camera_id": "camera_1",
    }
    entry = {
        "analysis_id": "analysis_1", "revision": 2, "verified_revision": 2,
        "annotation_status": "verified", "confirmed": True,
        "rooms": [room], "openings": [],
        "spatial_plans": {"camera_1": {"room_id": "living_1", "annotation_revision": 2}},
    }
    monkeypatch.setattr(routes_floorplans, "_analysis_entry", lambda _analysis_id: entry)
    request = FloorplanSpatialPlanUpdateRequest(space_summary="客厅", hard_constraints=[], status="locked")
    with pytest.raises(HTTPException) as exc_info:
        routes_floorplans.update_floorplan_spatial_plan("analysis_1", "camera_1", request)
    assert exc_info.value.status_code == 400
    assert "至少一条" in str(exc_info.value.detail)


def test_confirm_schema_requires_three_polygon_points():
    payload = {
        "rooms": [{
            "id": "room_1", "label": "客厅", "polygon": [{"x": 0, "y": 0}, {"x": 1, "y": 1}],
        }],
        "openings": [],
    }
    with pytest.raises(Exception):
        FloorplanConfirmRequest.model_validate(payload)


def test_suite_schema_enforces_two_or_three_candidates():
    base = {"analysis_id": "analysis_1", "floor_path": "x.png"}
    legacy = FloorplanSuiteRequest.model_validate({**base, "candidates_per_room": 2})
    assert legacy.candidates_per_room == 2
    assert legacy.generation_mode == "fast"
    assert legacy.model_keys == ["pro"]
    dual = FloorplanSuiteRequest.model_validate({**base, "model_keys": ["b2", "pro"]})
    assert dual.model_keys == ["b2", "pro"]
    assert dual.model_key == "b2"
    with pytest.raises(Exception):
        FloorplanSuiteRequest.model_validate({**base, "candidates_per_room": 4})


def test_dual_model_suite_creates_candidates_per_model_and_camera():
    request = FloorplanSuiteRequest.model_validate({
        "analysis_id": "analysis_1", "floor_path": "x.png",
        "model_keys": ["b2", "pro"], "candidates_per_room": 2,
    })
    candidates = routes_floorplans._candidate_records(
        {"id": "room_1__cam__camera_1", "annotation_room_id": "room_1",
         "camera_id": "camera_1", "selected": True},
        request, {"verified_revision": 7},
    )
    assert len(candidates) == 4
    assert [(item["model_key"], item["model_index"]) for item in candidates] == [
        ("b2", 1), ("b2", 2), ("pro", 1), ("pro", 2),
    ]
    assert all(item["annotation_revision"] == 7 for item in candidates)


def test_persisted_suite_view_never_exposes_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(floorplan_engine, "SUITE_DIR", str(tmp_path))
    entry = {"suite_id": "suite_1", "rooms": [], "floorplan_path": "", "floor_path": ""}
    floorplan_engine.save_suite(entry)
    raw = json.loads((tmp_path / "suite_1.json").read_text(encoding="utf-8"))
    assert "api_key" not in raw


def test_suite_color_match_schema_carries_candidate_identity():
    request = SuiteColorMatchRequest.model_validate({
        "suite_id": "suite_1", "room_id": "living_1", "result_id": "candidate_1",
        "image_rel": "_floorplan_suites/image.png", "ref_path": "floor.png",
        "rect": {"x": 0.1, "y": 0.6, "w": 0.8, "h": 0.3},
    })
    assert (request.suite_id, request.room_id, request.result_id) == (
        "suite_1", "living_1", "candidate_1",
    )


def test_inpaint_resolves_only_the_owned_suite_candidate(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate.png"
    Image.new("RGB", (24, 16), "#89725c").save(candidate)
    suite = {
        "suite_id": "suite_1", "status": "done",
        "rooms": [{
            "id": "living_1",
            "candidates": [{"result_id": "candidate_1", "path": str(candidate)}],
        }],
    }
    monkeypatch.setattr(routes_inpaint, "load_suite", lambda _suite_id: suite)
    monkeypatch.setattr(routes_inpaint, "require_output_image_rel", lambda _rel: str(candidate))
    target = InpaintTarget(
        kind="suite", suite_id="suite_1", room_id="living_1",
        result_id="candidate_1", image_rel="candidate.png",
    )
    image, workflow, operation = routes_inpaint._resolve_inpaint_source(target)
    assert image.size == (24, 16)
    assert (workflow, operation) == ("户型套图", "suite_inpaint")

    target.result_id = "someone_elses_candidate"
    with pytest.raises(HTTPException) as exc_info:
        routes_inpaint._resolve_inpaint_source(target)
    assert exc_info.value.status_code == 404


def _camera(camera_id="camera_1", x=.2, y=.2, tx=.5, ty=.5):
    return {
        "id": camera_id, "name": "主机位", "position": {"x": x, "y": y},
        "target": {"x": tx, "y": ty}, "height_m": None, "focal_length_mm": None,
        "purpose": "wide", "source": "manual", "confirmed": True,
        "enabled_for_generation": True,
    }


def test_camera_source_preserves_ai_acceptance_vs_human_edit():
    accepted = FloorplanCamera(
        id="camera_ai", position={"x": .2, "y": .2}, target={"x": .5, "y": .5},
        source="ai_suggested", confirmed=True,
    )
    edited = accepted.model_copy(update={"source": "ai_edited"})
    assert accepted.source == "ai_suggested"
    assert edited.source == "ai_edited"


def test_annotation_v3_adapts_legacy_camera_without_training_permission():
    entry = {"rooms": [{**_room(), "camera": {"position": {"x": .2, "y": .2}, "target": {"x": .4, "y": .4}}}]}
    floorplan_annotations.ensure_annotation_v2(entry)
    assert entry["schema_version"] == 3
    assert entry["training_eligible"] is False
    assert entry["rooms"][0]["cameras"][0]["source"] == "legacy"
    assert entry["view_proxies"] == {}
    assert entry["openings_review_status"] == "pending"


def test_pending_ai_opening_blocks_annotation_verification():
    room = {**_room(), "selected": True, "cameras": [_camera()]}
    room["polygon"] = [
        {"x": .1, "y": .1}, {"x": .8, "y": .1},
        {"x": .8, "y": .8}, {"x": .1, "y": .8},
    ]
    report = floorplan_annotations.validate_annotation({
        "rooms": [room], "openings_review_status": "pending",
        "openings": [{
            "id": "door_ai", "kind": "door", "points": [{"x": .2, "y": .1}, {"x": .3, "y": .1}],
            "room_ids": ["living_1"], "source": "ai_suggested", "review_status": "pending",
        }],
    })
    assert any(error["code"] == "openings_unreviewed" for error in report["hard_errors"])


def test_view_proxy_hash_changes_with_camera_opening_or_aspect_ratio():
    room = {**_room(), "polygon": [{"x": .1, "y": .1}, {"x": .8, "y": .1}, {"x": .8, "y": .8}]}
    camera = _camera()
    plan = {"spatial_plan_id": "spatial_1", "status": "locked", "hard_constraints": ["保留北墙"]}
    entry = {
        "revision": 4, "verified_revision": 4, "rooms": [room], "openings_review_status": "confirmed",
        "openings": [{
            "id": "door_1", "kind": "door", "points": [{"x": .2, "y": .1}, {"x": .3, "y": .1}],
            "room_ids": ["living_1"], "source": "manual", "review_status": "accepted",
        }],
    }
    base = floorplan_annotations.view_proxy_source_hash(entry, room, camera, plan, "4:3")
    moved_camera = {**camera, "target": {"x": .7, "y": .5}}
    assert floorplan_annotations.view_proxy_source_hash(entry, room, moved_camera, plan, "4:3") != base
    assert floorplan_annotations.view_proxy_source_hash(entry, room, camera, plan, "16:9") != base
    entry["openings"][0]["points"][1]["x"] = .4
    assert floorplan_annotations.view_proxy_source_hash(entry, room, camera, plan, "4:3") != base


def test_geometry_validation_blocks_overlapping_rooms():
    first = {**_room("a", "客厅", "living"), "selected": True, "cameras": [_camera()]}
    first["polygon"] = [
        {"x": .1, "y": .1}, {"x": .5, "y": .1}, {"x": .5, "y": .5}, {"x": .1, "y": .5},
    ]
    second = {**_room("b", "榻榻米", "tatami"), "selected": False, "cameras": []}
    second["polygon"] = [
        {"x": .3, "y": .3}, {"x": .6, "y": .3}, {"x": .6, "y": .6}, {"x": .3, "y": .6},
    ]
    report = floorplan_annotations.validate_annotation({"rooms": [first, second]})
    overlap = next(error for error in report["hard_errors"] if error["code"] == "room_overlap")
    assert overlap["overlap_ratio"] > .1


def test_geometry_validation_accepts_shared_boundary_and_multiple_cameras():
    first = {**_room("a", "餐厅", "dining"), "selected": True, "cameras": [_camera("c1"), _camera("c2", .3, .3, .45, .45)]}
    first["polygon"] = [{"x": .1, "y": .1}, {"x": .4, "y": .1}, {"x": .4, "y": .5}, {"x": .1, "y": .5}]
    second = {**_room("b", "客厅", "living"), "selected": False, "cameras": []}
    second["polygon"] = [{"x": .4, "y": .1}, {"x": .8, "y": .1}, {"x": .8, "y": .5}, {"x": .4, "y": .5}]
    report = floorplan_annotations.validate_annotation({"rooms": [first, second]})
    assert not [error for error in report["hard_errors"] if error["code"] == "room_overlap"]


def test_operation_log_is_append_only(tmp_path, monkeypatch):
    monkeypatch.setattr(floorplan_annotations, "ANNOTATION_DIR", str(tmp_path))
    floorplan_annotations.append_operations("analysis_1", 1, [{"type": "add_room", "room_id": "room_1"}], "boki")
    floorplan_annotations.append_operations("analysis_1", 2, [{"type": "add_camera", "camera_id": "camera_1"}], "boki")
    rows = floorplan_annotations.load_operations("analysis_1")
    assert [row["revision"] for row in rows] == [1, 2]
    assert [row["type"] for row in rows] == ["add_room", "add_camera"]


def test_prepare_annotation_source_copies_image_and_hashes_it(tmp_path, monkeypatch):
    source = tmp_path / "upload.png"
    Image.new("RGB", (32, 20), "white").save(source)
    monkeypatch.setattr(floorplan_annotations, "ANNOTATION_DIR", str(tmp_path / "annotations"))
    metadata = floorplan_annotations.prepare_annotation_source("analysis_1", str(source))
    assert metadata["width"] == 32 and metadata["height"] == 20
    assert len(metadata["sha256"]) == 64
    assert metadata["path"] != str(source)


def test_dataset_export_contains_only_verified_consented_annotations(tmp_path, monkeypatch):
    analysis_dir = tmp_path / "analyses"
    suite_dir = tmp_path / "suites"
    annotation_dir = tmp_path / "annotations"
    export_dir = tmp_path / "exports"
    for folder in (analysis_dir, suite_dir, annotation_dir, export_dir):
        folder.mkdir()
    monkeypatch.setattr(floorplan_annotations, "ANALYSIS_DIR", str(analysis_dir))
    monkeypatch.setattr(floorplan_annotations, "SUITE_DIR", str(suite_dir))
    monkeypatch.setattr(floorplan_annotations, "ANNOTATION_DIR", str(annotation_dir))
    monkeypatch.setattr(floorplan_annotations, "EXPORT_DIR", str(export_dir))
    source = tmp_path / "source.png"
    Image.new("RGB", (40, 30), "white").save(source)
    room = {
        **_room("living_1", "客厅", "living"),
        "polygon": [{"x": .1, "y": .1}, {"x": .8, "y": .1}, {"x": .8, "y": .8}, {"x": .1, "y": .8}],
        "selected": True, "cameras": [_camera()], "primary_camera_id": "camera_1",
    }
    eligible = {
        "analysis_id": "analysis_ok", "schema_version": 2, "revision": 3,
        "annotation_status": "verified", "training_consent": True, "training_eligible": True,
        "verified_at": 1, "verified_by": "boki", "source": {
            "path": str(source), "sha256": "abc", "width": 40, "height": 30,
        },
        "floorplan_path": str(source), "rooms": [room], "openings": [],
        "geometry_report": {"hard_errors": [], "warnings": []},
    }
    excluded = {**eligible, "analysis_id": "analysis_no_consent", "training_consent": False}
    (analysis_dir / "analysis_ok.json").write_text(json.dumps(eligible), encoding="utf-8")
    (analysis_dir / "analysis_no_consent.json").write_text(json.dumps(excluded), encoding="utf-8")
    path, summary = floorplan_annotations.export_dataset_zip()
    assert summary == {"floorplans": 1, "cameras": 1, "path": path}
    with zipfile.ZipFile(path) as archive:
        manifest = archive.read("manifest.jsonl").decode("utf-8")
        cameras = archive.read("camera_samples.jsonl").decode("utf-8")
        assert "analysis_ok" in manifest and "analysis_no_consent" not in manifest
        assert "camera_1" in cameras
