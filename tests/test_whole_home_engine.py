import base64
import asyncio
import copy
import io
import json
import os
import subprocess

import pytest
from Floor_engine_server import (
    routes_whole_home, server_schemas, whole_home_dev_lock, whole_home_engine,
)
from PIL import Image, ImageDraw


def _metric_model():
    return {
        "coordinate_system": "metres-y-up",
        "width_m": 10,
        "depth_m": 8,
        "wall_height_m": 2.8,
        "wall_thickness_m": .12,
        "walls": [
            {"id": "w1", "start": {"x": .5, "z": .5}, "end": {"x": 9.5, "z": .5}},
            {"id": "w2", "start": {"x": 9.5, "z": .5}, "end": {"x": 9.5, "z": 7.5}},
            {"id": "w3", "start": {"x": 9.5, "z": 7.5}, "end": {"x": .5, "z": 7.5}},
            {"id": "w4", "start": {"x": .5, "z": 7.5}, "end": {"x": .5, "z": .5}},
        ],
        "rooms": [{
            "id": "living", "label": "客厅", "room_type": "living",
            "polygon": [{"x": .5, "z": .5}, {"x": 9.5, "z": .5}, {"x": 9.5, "z": 7.5}, {"x": .5, "z": 7.5}],
        }],
        "openings": [{
            "id": "door1", "wall_id": "w1", "kind": "door", "offset_m": 2,
            "width_m": .9, "height_m": 2.1, "sill_height_m": 0,
            "review_status": "accepted",
        }],
        "fixed_objects": [], "cameras": [],
    }


def _preview_data_url(color: str) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (160, 120), color).save(buffer, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _camera_candidate(candidate_id: str, score: float, color: str) -> dict:
    return {
        "candidate_id": candidate_id, "room_id": "living", "room_label": "客厅",
        "local_score": score, "metrics": {
            "wall_clearance_m": 1.2, "depth_m": 6.5,
            "semantic_gate": True, "safety_gate": True,
            "render_gate": {
                "version": "whole-home-render-gate-v2", "pass": True, "status": "pass",
                "profile": "living_room", "denominator_pixels": 10000,
                "matched_pixels": 9000, "unmatched_pixels": 1000,
                "floor_fraction": .08, "wall_fraction": .5,
                "peak_semantic_role": "sofa", "peak_semantic_role_fraction": .12,
                "semantic_role_fractions": {"floor": .08, "wall": .5, "sofa": .12, "tv": .03},
                "required_groups": [], "reasons": [],
            },
        },
        "preview_data_url": _preview_data_url(color),
        "camera": {
            "id": f"camera_{candidate_id}", "name": candidate_id,
            "position": {"x": 2, "y": 1.55, "z": 2},
            "target": {"x": 7, "y": 1.25, "z": 5},
            "focal_length_mm": 24, "room_id": "living", "enabled": True,
            "source": "auto_geometry",
        },
    }


def _state_machine_fixture(tmp_path, capture_count=3):
    image_path = tmp_path / "input.png"
    Image.new("RGB", (64, 48), "white").save(image_path)
    model = whole_home_engine.normalize_model(_metric_model())
    project = {"project_id": "project-state", "model": model, "floorplan_path": str(image_path)}
    captures = []
    for index in range(capture_count):
        camera = {
            "id": f"camera-{index + 1}", "name": f"camera {index + 1}",
            "position": {"x": 2 + index, "y": 1.55, "z": 2},
            "target": {"x": 6, "y": 1.2, "z": 5}, "focal_length_mm": 24,
            "room_id": "living", "enabled": True, "source": "auto_geometry",
        }
        captures.append({
            "capture_id": f"capture-{index + 1}", "camera_id": camera["id"],
            "camera": camera, "room_id": "living", "aspect_ratio": "4:3",
            "rgb_path": str(image_path), "depth_path": str(image_path),
            "normal_path": str(image_path), "edge_path": str(image_path),
            "semantic_path": str(image_path), "plan_overlay_path": str(image_path),
        })
    run = {
        "run_id": "run-state", "floor_path": str(image_path), "style_ref_path": "",
        "style": "modern", "lighting": "daylight", "prompt": "", "resolution": "4K",
        "aspect_ratio": "4:3", "call_ledger": [], "results": [],
    }
    result = {
        "result_id": "result-state", "room_id": "living",
        "capture_ids": [row["capture_id"] for row in captures],
        "capture_id": captures[0]["capture_id"], "camera_id": captures[0]["camera_id"],
        "camera_name": "living", "model_key": "b2", "candidate_index": 1,
        "status": "queued", "outcome": "queued", "deliverable": False,
        "stage": "", "error": "", "path": "", "structure_path": "",
        "api_original_path": "", "material_path": "", "corrected_path": "",
        "final_path": "", "evaluation": None, "attempts": [], "trace": [],
    }
    run["results"] = [result]
    return project, captures, run, result, image_path


def _passing_local_gate(phase: str) -> dict:
    return {
        "version": f"{phase}-local-test-v1", "phase": phase,
        "status": "done", "verdict": "pass", "gate_pass": True,
        "thresholds": {}, "missing_buffers": [], "invalid_buffers": [],
        "overlay_path": "", "summary": "local gate passed",
    }


def test_atomic_json_retries_windows_permission_replace_then_succeeds(monkeypatch, tmp_path):
    target = tmp_path / "state.json"
    target.write_text('{"old": true}', encoding="utf-8")
    real_replace = whole_home_engine.os.replace
    sleeps = []
    calls = {"count": 0}

    def flaky_replace(source, destination):
        calls["count"] += 1
        if calls["count"] <= 3:
            error = OSError(5, "shared by another process", destination)
            error.winerror = 5
            raise error
        real_replace(source, destination)

    monkeypatch.setattr(whole_home_engine.os, "replace", flaky_replace)
    monkeypatch.setattr(whole_home_engine.time, "sleep", lambda seconds: sleeps.append(seconds))
    whole_home_engine._atomic_json(str(target), {"new": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    assert calls["count"] == 4
    assert sleeps == list(whole_home_dev_lock._ATOMIC_REPLACE_DELAYS[:3])
    assert list(tmp_path.glob("state.json.*.tmp")) == []


def test_atomic_json_final_replace_failure_preserves_old_target_and_cleans_tmp(monkeypatch, tmp_path):
    target = tmp_path / "state.json"
    original = '{"old": true}'
    target.write_text(original, encoding="utf-8")
    sleeps = []

    def blocked_replace(source, destination):
        raise PermissionError(13, "still shared", destination)

    monkeypatch.setattr(whole_home_engine.os, "replace", blocked_replace)
    monkeypatch.setattr(whole_home_engine.time, "sleep", lambda seconds: sleeps.append(seconds))
    try:
        whole_home_engine._atomic_json(str(target), {"new": True})
        assert False, "final replace failure must be raised"
    except PermissionError:
        pass

    assert target.read_text(encoding="utf-8") == original
    assert sleeps == list(whole_home_dev_lock._ATOMIC_REPLACE_DELAYS)
    assert list(tmp_path.glob("state.json.*.tmp")) == []


def test_browser_render_gate_and_visible_edge_contract_executes():
    root = os.path.dirname(os.path.dirname(__file__))
    completed = subprocess.run(
        ["node", "--test", "tests/whole-home-render-gate.test.mjs"],
        cwd=os.path.join(root, "web"), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_metric_coordinates_below_one_are_not_rescaled():
    model = whole_home_engine.normalize_model(_metric_model())
    assert model["walls"][0]["start"] == {"x": .5, "z": .5}
    assert model["rooms"][0]["polygon"][0] == {"x": .5, "z": .5}


def test_normalized_ai_coordinates_are_scaled_once():
    payload = _metric_model()
    payload.pop("coordinate_system")
    payload["_coordinates_normalized"] = True
    payload["walls"][0]["start"] = {"x": .1, "z": .2}
    model = whole_home_engine.normalize_model(payload)
    assert model["walls"][0]["start"] == {"x": 1.0, "z": 1.6}


def test_opening_is_attached_and_validated_in_shared_wall_graph():
    model = whole_home_engine.normalize_model(_metric_model())
    report = whole_home_engine.validate_model(model)
    assert report["hard_errors"] == []
    assert model["openings"][0]["wall_id"] == "w1"
    assert model["openings"][0]["offset_m"] == 2


def test_model_hash_ignores_other_cameras_but_tracks_geometry():
    model = whole_home_engine.normalize_model(_metric_model())
    original = whole_home_engine.model_hash(model)
    model["cameras"].append({
        "id": "cam1", "name": "机位 1", "position": {"x": 2, "y": 1.55, "z": 2},
        "target": {"x": 5, "y": 1.2, "z": 2}, "focal_length_mm": 24,
        "room_id": "living", "enabled": True, "source": "human_3d",
    })
    assert whole_home_engine.model_hash(model) == original
    model["walls"][0]["end"]["x"] = 9
    assert whole_home_engine.model_hash(model) != original


def test_model_hash_tracks_semantic_layout_but_not_validation_timestamps():
    payload = _metric_model()
    payload["fixed_objects"] = [
        {
            "id": "sofa", "name": "Sofa", "kind": "sofa",
            "position": {"x": 3, "y": 0, "z": 3},
            "size": {"x": 2.2, "y": .85, "z": .9}, "rotation_y_deg": 0,
            "room_id": "living", "source": "human", "confidence": 1,
        },
        {
            "id": "tv", "name": "TV", "kind": "tv",
            "position": {"x": 7, "y": 0, "z": 6},
            "size": {"x": 1.6, "y": 1.0, "z": .3}, "rotation_y_deg": 0,
            "room_id": "living", "source": "human", "confidence": 1,
        },
    ]
    model = whole_home_engine.normalize_model(payload)
    original = whole_home_engine.model_hash(model)
    model["semantic_report"]["checked_at"] += 500
    assert whole_home_engine.model_hash(model) == original
    model["fixed_objects"][0]["position"]["x"] += .4
    assert whole_home_engine.model_hash(model) != original


def test_generation_prompt_labels_geometry_buffers(tmp_path):
    files = {}
    for name in ("rgb", "depth", "normal", "edge", "semantic", "plan", "floor"):
        path = tmp_path / f"{name}.png"
        path.write_bytes(b"image")
        files[name] = str(path)
    model = whole_home_engine.normalize_model(_metric_model())
    project = {"model": model, "floorplan_path": files["plan"]}
    capture = {
        "rgb_path": files["rgb"], "depth_path": files["depth"], "normal_path": files["normal"],
        "edge_path": files["edge"], "semantic_path": files["semantic"],
        "camera": {"position": {"x": 2, "y": 1.55, "z": 2}, "target": {"x": 5, "y": 1.2, "z": 2}, "focal_length_mm": 24},
    }
    run = {"floor_path": files["floor"], "style": "现代自然", "lighting": "自然光"}
    prompt, paths = whole_home_engine.build_generation_prompt(project, capture, run, pass_name="structure")
    assert paths == [files["rgb"], files["depth"], files["normal"], files["edge"], files["semantic"]]
    assert "depth buffer" in prompt
    assert "CURRENT ROOM" in prompt
    assert "living_room" in prompt
    assert files["plan"] not in paths


def test_structure_local_alignment_gate_records_metrics_and_overlay(monkeypatch, tmp_path):
    monkeypatch.setattr(whole_home_engine, "ASSET_DIR", str(tmp_path / "assets"))
    normal_path = tmp_path / "normal.png"
    semantic_path = tmp_path / "semantic.png"
    candidate_path = tmp_path / "candidate.png"
    for path, color in ((normal_path, "#55aaff"), (semantic_path, "#ec4899"), (candidate_path, "white")):
        image = Image.new("RGB", (640, 480), "black")
        draw = ImageDraw.Draw(image)
        draw.rectangle((80, 70, 560, 410), outline=color, width=18)
        draw.rectangle((210, 170, 430, 360), outline=color, width=14)
        image.save(path)
    capture = {
        "capture_id": "capture-gate", "normal_path": str(normal_path),
        "semantic_path": str(semantic_path),
    }
    gate = whole_home_engine.evaluate_structure_local_gate(
        {"project_id": "project-gate"}, capture, str(candidate_path), "attempt-1")
    repeated = whole_home_engine.evaluate_structure_local_gate(
        {"project_id": "project-gate"}, capture, str(candidate_path), "attempt-1")
    assert gate["version"] == "structure-local-alignment-v1"
    assert gate["gate_pass"] is True
    assert gate["semantic_coverage_12"] >= .8
    assert gate["semantic_mean_distance"] <= 9
    assert gate["normal_coverage_12"] >= .62
    assert gate["missing_buffers"] == []
    assert os.path.isfile(gate["overlay_path"])
    assert repeated["overlay_path"] != gate["overlay_path"]
    assert os.path.isfile(repeated["overlay_path"])


def test_structure_local_alignment_gate_missing_buffer_is_fail_closed(tmp_path):
    candidate = tmp_path / "candidate.png"
    Image.new("RGB", (80, 60), "white").save(candidate)
    gate = whole_home_engine.evaluate_structure_local_gate(
        {"project_id": "p"}, {"capture_id": "c"}, str(candidate), "attempt")
    assert gate["gate_pass"] is False
    assert gate["verdict"] == "fail"
    assert gate["missing_buffers"] == ["normal", "semantic"]


def test_structure_local_gate_overlay_failure_is_structured_and_fail_closed(monkeypatch, tmp_path):
    image = tmp_path / "edges.png"
    canvas = Image.new("RGB", (200, 150), "black")
    ImageDraw.Draw(canvas).rectangle((20, 20, 180, 130), outline="white", width=10)
    canvas.save(image)
    monkeypatch.setattr(
        whole_home_engine, "_save_local_gate_overlay",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("blocked")),
    )
    gate = whole_home_engine.evaluate_structure_local_gate(
        {"project_id": "p"}, {
            "capture_id": "c", "normal_path": str(image), "semantic_path": str(image),
        }, str(image), "attempt")
    assert gate["gate_pass"] is False
    assert gate["verdict"] == "fail"
    assert gate["artifact_error"] == "blocked"
    assert "证据图保存失败" in gate["summary"]


def test_final_local_geometry_gate_rejects_redrawn_space(monkeypatch, tmp_path):
    monkeypatch.setattr(whole_home_engine, "ASSET_DIR", str(tmp_path / "assets"))
    structure = tmp_path / "structure.png"
    matching = tmp_path / "matching.png"
    drifted = tmp_path / "drifted.png"
    image = Image.new("RGB", (640, 480), "#ddd8ce")
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 60, 570, 420), outline="black", width=16)
    draw.line((310, 60, 310, 420), fill="black", width=14)
    image.save(structure)
    image.save(matching)
    drift = Image.new("RGB", (640, 480), "#ddd8ce")
    ImageDraw.Draw(drift).ellipse((160, 100, 520, 430), outline="black", width=16)
    drift.save(drifted)
    project = {"project_id": "project-final"}
    capture = {"capture_id": "capture-final"}

    passed = whole_home_engine.evaluate_final_local_gate(
        project, capture, str(structure), str(matching), "material-1")
    failed = whole_home_engine.evaluate_final_local_gate(
        project, capture, str(structure), str(drifted), "material-2")
    assert passed["gate_pass"] is True
    assert failed["gate_pass"] is False
    assert failed["structure_coverage_12"] < failed["thresholds"]["structure_coverage_12_min"]
    assert os.path.isfile(failed["overlay_path"])


def test_qa_missing_mandatory_checks_fails_closed_without_score_inflation(monkeypatch, tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    monkeypatch.setattr(whole_home_engine, "call_gemini_json", lambda *args, **kwargs: ({
        "geometry_score": 68, "camera_score": 70, "opening_score": 68, "material_score": 66,
        "room_identity_score": 72, "fixed_object_score": 74,
        "hard_fail": False, "summary": "all constraints match",
        "checks": [
            {"constraint_id": "C003", "constraint": "geometry", "status": "pass", "severity": "hard", "evidence": "matched"},
        ],
    }, None))
    capture = {
        "rgb_path": str(image), "depth_path": str(image),
        "camera": {"position": {"x": 2, "y": 1.55, "z": 2}, "target": {"x": 5, "y": 1.2, "z": 2}},
    }
    result, error = whole_home_engine.evaluate_whole_home_result(
        "key", {"model": whole_home_engine.normalize_model(_metric_model())}, capture, str(image), str(image))
    assert error is None
    assert result["geometry_score"] == 68
    assert result["hard_fail"] is True
    assert result["verification_incomplete"] is True
    assert result["eligible_for_recommendation"] is False


def test_topology_validator_blocks_dangling_shell_and_missing_enclosed_room_wall():
    payload = _metric_model()
    payload["rooms"][0]["room_type"] = "bedroom"
    for wall in payload["walls"]:
        wall["kind"] = "exterior"
    payload["walls"][0]["end"] = {"x": 8.0, "z": .5}
    model = whole_home_engine.normalize_model(payload)
    report = whole_home_engine.validate_model(model)
    codes = {item["code"] for item in report["hard_errors"]}
    assert "open_exterior_endpoint" in codes
    assert "enclosed_room_boundary_gap" in codes
    gap = next(item for item in report["hard_errors"] if item["code"] == "enclosed_room_boundary_gap")
    assert gap["start"]["x"] == 8.0
    assert gap["end"]["x"] == 9.5


def test_locked_cad_manifest_uses_wall_face_gate_instead_of_legacy_centerline_gap():
    payload = _metric_model()
    payload["rooms"][0]["room_type"] = "bedroom"
    payload["walls"][0]["end"] = {"x": 8.0, "z": .5}
    model = whole_home_engine.normalize_model(payload)
    model["cad_facts_hash"] = "cad-facts"
    model["geometry_manifest"] = {"manifest_hash": "locked-manifest"}

    report = whole_home_engine.validate_model(model)

    assert "enclosed_room_boundary_gap" not in {
        item["code"] for item in report["hard_errors"]}
    assert "cad_room_boundary_uses_wall_face_not_centerline" in {
        item["code"] for item in report["warnings"]}


def test_confirmed_vector_cad_treats_missing_furniture_roles_as_generation_targets():
    model = whole_home_engine.normalize_model(_metric_model())
    model["rooms"][0].update(
        semantic_profile="living_room", room_type="living_room", selected=True)
    model["room_contracts"] = [{
        "room_id": model["rooms"][0]["id"],
        "required_role_groups": [["sofa"], ["tv"]],
    }]
    model["fixed_objects"] = []
    model["cad_facts_hash"] = "cad-facts"
    model["input_grade"] = "vector_authoritative"
    model["space_confirmation"] = {"status": "confirmed"}

    report = whole_home_engine.validate_semantic_layout(model)

    assert report["hard_errors"] == []
    assert {row["code"] for row in report["warnings"]} == {
        "cad_generation_role_unobserved"}


def test_ai_analysis_runs_second_topology_pass_and_keeps_better_graph(monkeypatch, tmp_path):
    plan = tmp_path / "plan.png"
    Image.new("RGB", (100, 80), "white").save(plan)

    def ai_payload(top_right_x):
        return {
            "summary": "test", "estimated_width_m": 10, "estimated_depth_m": 8,
            "scale_evidence": "test dimensions",
            "walls": [
                {"id": "north", "start": {"x": .1, "z": .1}, "end": {"x": top_right_x, "z": .1}, "kind": "exterior", "thickness_m": .2, "confidence": .9},
                {"id": "east", "start": {"x": .9, "z": .1}, "end": {"x": .9, "z": .9}, "kind": "exterior", "thickness_m": .2, "confidence": .9},
                {"id": "south", "start": {"x": .9, "z": .9}, "end": {"x": .1, "z": .9}, "kind": "exterior", "thickness_m": .2, "confidence": .9},
                {"id": "west", "start": {"x": .1, "z": .9}, "end": {"x": .1, "z": .1}, "kind": "exterior", "thickness_m": .2, "confidence": .9},
            ],
            "rooms": [{"id": "bed", "label": "卧室", "room_type": "bedroom", "polygon": [
                {"x": .1, "z": .1}, {"x": .9, "z": .1}, {"x": .9, "z": .9}, {"x": .1, "z": .9},
            ], "confidence": .9}],
            "openings": [], "fixed_objects": [], "uncertainties": [],
            "_floor_engine_model": "gemini-test",
        }

    responses = [
        (ai_payload(.8), None),
        (ai_payload(.9), None),
        ({"openings": [], "uncertainties": [], "_floor_engine_model": "gemini-openings"}, None),
    ]
    prompts = []

    def fake_call(api_key, prompt, image_paths, schema, max_output_tokens=0):
        prompts.append(prompt)
        if len(prompts) >= 2:
            assert len(image_paths) == 2
            assert image_paths[1] != str(plan)
        return responses.pop(0)

    monkeypatch.setattr(whole_home_engine, "call_gemini_json", fake_call)
    model, error, model_name = whole_home_engine.analyze_whole_home("key", str(plan))
    assert error is None
    assert len(prompts) == 3
    assert "SECOND-PASS TOPOLOGY AUDIT" in prompts[1]
    assert "Audit ONLY architectural openings" in prompts[2]
    assert model["geometry_report"]["hard_errors"] == []
    assert "topology audit" in model_name
    assert "opening audit" in model_name


def test_plan_alignment_score_prefers_wall_lines_on_source_ink(tmp_path):
    plan = tmp_path / "ink-plan.png"
    image = Image.new("RGB", (200, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 189, 149), outline="black", width=5)
    image.save(plan)
    aligned = whole_home_engine.normalize_model(_metric_model())
    shifted = copy.deepcopy(aligned)
    for wall in shifted["walls"]:
        wall["start"]["x"] = min(10, wall["start"]["x"] + 1)
        wall["end"]["x"] = min(10, wall["end"]["x"] + 1)
    aligned_score = whole_home_engine.plan_alignment_score(aligned, str(plan))
    shifted_score = whole_home_engine.plan_alignment_score(shifted, str(plan))
    assert aligned_score > shifted_score + 15


def test_topology_audit_keeps_dropped_opening_as_pending_candidate():
    payload = _metric_model()
    payload["openings"].append({
        "id": "window-east", "wall_id": "w2", "kind": "window", "offset_m": 2,
        "width_m": 1.2, "height_m": 1.2, "sill_height_m": .9,
        "review_status": "pending", "confidence": .8,
    })
    initial = whole_home_engine.normalize_model(payload, source="ai")
    repaired = whole_home_engine.normalize_model(_metric_model(), source="ai")
    merged, added = whole_home_engine.merge_audit_opening_candidates(initial, repaired)
    assert added == 1
    candidate = next(item for item in merged["openings"] if item["kind"] == "window")
    assert candidate["wall_id"] == "w2"
    assert candidate["review_status"] == "pending"
    assert any("仅首轮识别" in item for item in merged["uncertainties"])


def test_ai_wall_with_rooms_on_both_sides_is_reclassified_as_interior():
    payload = {
        "width_m": 10, "depth_m": 8, "walls": [{
            "id": "shared", "start": {"x": 1, "z": 4}, "end": {"x": 9, "z": 4},
            "kind": "exterior", "source": "ai", "confidence": .9,
        }],
        "rooms": [
            {"id": "top", "label": "卧室", "room_type": "bedroom", "polygon": [
                {"x": 1, "z": 1}, {"x": 9, "z": 1}, {"x": 9, "z": 4}, {"x": 1, "z": 4},
            ]},
            {"id": "bottom", "label": "客厅", "room_type": "living", "polygon": [
                {"x": 1, "z": 4}, {"x": 9, "z": 4}, {"x": 9, "z": 7}, {"x": 1, "z": 7},
            ]},
        ], "openings": [], "fixed_objects": [], "cameras": [],
    }
    model = whole_home_engine.normalize_model(payload, source="ai")
    assert model["walls"][0]["kind"] == "interior"
    assert model["walls"][0]["source"] == "ai_edited"
    assert any("AI 外墙候选改为内墙" in item for item in model["uncertainties"])


def test_same_plan_history_prevents_lower_alignment_regression(tmp_path):
    plan = tmp_path / "history-plan.png"
    image = Image.new("RGB", (200, 160), "white")
    ImageDraw.Draw(image).rectangle((10, 10, 189, 149), outline="black", width=5)
    image.save(plan)
    aligned = whole_home_engine.normalize_model(_metric_model())
    aligned["geometry_report"]["image_alignment_score"] = whole_home_engine.plan_alignment_score(aligned, str(plan))
    shifted = copy.deepcopy(aligned)
    for wall in shifted["walls"]:
        wall["start"]["x"] = min(10, wall["start"]["x"] + 1)
        wall["end"]["x"] = min(10, wall["end"]["x"] + 1)
    shifted["geometry_report"] = whole_home_engine.validate_model(shifted, str(plan))
    selected, project_id = whole_home_engine.prefer_historical_geometry(shifted, [{
        "project_id": "better-history", "floorplan_path": str(plan), "model": aligned,
        "verified": False,
    }], str(plan))
    assert project_id == "better-history"
    assert selected["walls"][0]["start"]["x"] == .5
    assert any("历史更优墙图" in item for item in selected["uncertainties"])


def test_auto_camera_plan_persists_candidates_and_uses_only_ai_selected_id(monkeypatch, tmp_path):
    asset_dir = tmp_path / "assets"
    plan_image = tmp_path / "plan.png"
    Image.new("RGB", (300, 220), "white").save(plan_image)
    monkeypatch.setattr(whole_home_engine, "ASSET_DIR", str(asset_dir))

    def fake_call(api_key, prompt, image_paths, schema, max_output_tokens=0):
        assert api_key == "key"
        assert "never invent or modify coordinates" in prompt
        assert len(image_paths) == 2
        assert all(os.path.isfile(path) for path in image_paths)
        return ({
            "summary": "选择了更有纵深的客厅镜头",
            "selections": [{
                "candidate_id": "living_b", "room_id": "living", "rank": 1,
                "visual_score": 94, "reason": "门洞和地面纵深更完整",
                "strengths": ["纵深清晰"], "risks": ["右侧墙略多"],
            }],
            "_floor_engine_model": "gemini-camera-test",
        }, None)

    monkeypatch.setattr(whole_home_engine, "call_gemini_json", fake_call)
    project = {
        "project_id": "project-camera-ai", "floorplan_path": str(plan_image),
        "model": whole_home_engine.normalize_model(_metric_model()),
    }
    result = whole_home_engine.rank_auto_camera_plan(
        "key", project,
        [_camera_candidate("living_a", 91, "#aaaaaa"), _camera_candidate("living_b", 84, "#dddddd")],
        shots_per_room=1, aspect_ratio="4:3",
    )
    assert result["ai_model"] == "gemini-camera-test"
    assert result["selected_cameras"][0]["candidate_id"] == "living_b"
    assert result["selected_cameras"][0]["source"] == "ai_selected"
    assert result["selections"][0]["reason"] == "门洞和地面纵深更完整"
    assert all(os.path.isfile(row["preview_path"]) for row in result["candidates"])
    assert os.path.isfile(result["contact_sheets"][0]["path"])


def test_auto_camera_plan_keeps_local_fallback_when_gemini_fails(monkeypatch, tmp_path):
    plan_image = tmp_path / "plan.png"
    Image.new("RGB", (300, 220), "white").save(plan_image)
    monkeypatch.setattr(whole_home_engine, "ASSET_DIR", str(tmp_path / "assets"))
    monkeypatch.setattr(whole_home_engine, "call_gemini_json", lambda *args, **kwargs: (None, "quota test"))
    project = {
        "project_id": "project-camera-fallback", "floorplan_path": str(plan_image),
        "model": whole_home_engine.normalize_model(_metric_model()),
    }
    result = whole_home_engine.rank_auto_camera_plan(
        "key", project,
        [_camera_candidate("living_low", 65, "#777777"), _camera_candidate("living_high", 92, "#eeeeee")],
        shots_per_room=1, aspect_ratio="4:3",
    )
    assert result["ai_error"] == "quota test"
    assert result["selections"][0]["selection_source"] == "local_fallback"
    assert result["selected_cameras"][0]["candidate_id"] == "living_high"
    assert result["selected_cameras"][0]["source"] == "auto_geometry"


def test_semantic_camera_candidates_are_gated_diverse_and_never_use_18mm():
    payload = _metric_model()
    payload["fixed_objects"] = [
        {
            "id": "sofa", "name": "Sofa", "kind": "sofa", "semantic_role": "sofa",
            "position": {"x": 4.2, "y": 0, "z": 4.2},
            "size": {"x": 2.2, "y": .85, "z": .9}, "rotation_y_deg": 0,
            "room_id": "living", "source": "human", "confidence": 1,
            "purpose": "layout_proxy", "observed": False, "review_status": "accepted",
        },
        {
            "id": "tv", "name": "TV", "kind": "tv", "semantic_role": "tv",
            "position": {"x": 8.4, "y": 0, "z": 4.2},
            "size": {"x": 1.4, "y": 1, "z": .2}, "rotation_y_deg": 0,
            "room_id": "living", "source": "human", "confidence": 1,
            "purpose": "layout_proxy", "observed": False, "review_status": "accepted",
        },
    ]
    model = whole_home_engine.normalize_model(payload)
    proposal = whole_home_engine.generate_semantic_camera_candidates(model, aspect_ratio="4:3")

    assert proposal["status"] == "ready"
    assert 1 <= len(proposal["candidates"]) <= 8
    assert proposal["blocked_rooms"] == []
    assert len(proposal["room_pools"][0]["candidate_ids"]) <= 3
    assert all(row["metrics"]["semantic_gate"] is True for row in proposal["candidates"])
    assert all(row["metrics"]["safety_gate"] is True for row in proposal["candidates"])
    assert all(row["camera"]["focal_length_mm"] in (20, 24, 28) for row in proposal["candidates"])
    assert all(row["camera"]["focal_length_mm"] != 18 for row in proposal["candidates"])
    assert all("sofa" in row["metrics"]["visible_roles"] for row in proposal["candidates"])


def _portal_camera_regression_model():
    def rectangle(min_x, min_z, max_x, max_z):
        return [
            {"x": min_x, "z": min_z}, {"x": max_x, "z": min_z},
            {"x": max_x, "z": max_z}, {"x": min_x, "z": max_z},
        ]

    def opening(object_id, wall_id, kind, offset_m, width_m, *, confidence=.8, source="ai"):
        return {
            "id": object_id, "wall_id": wall_id, "kind": kind,
            "offset_m": offset_m, "width_m": width_m,
            "height_m": 2.1 if kind == "door" else 1.3 if kind == "window" else 2.4,
            "sill_height_m": .8 if kind == "window" else 0,
            "review_status": "accepted", "source": source, "confidence": confidence,
        }

    def fixed(object_id, name, kind, role, room_id, x, z, sx, sy, sz,
              *, rotation=0, observed=False, blocks=True):
        return {
            "id": object_id, "name": name, "kind": kind, "semantic_role": role,
            "position": {"x": x, "y": 0, "z": z},
            "size": {"x": sx, "y": sy, "z": sz}, "rotation_y_deg": rotation,
            "room_id": room_id, "source": "ai",
            "purpose": "observed_architecture" if observed else "layout_proxy",
            "observed": observed, "review_status": "accepted", "blocks_camera": blocks,
        }

    model = whole_home_engine.normalize_model({
        "coordinate_system": "metres-y-up", "width_m": 9.5, "depth_m": 6,
        "wall_height_m": 2.8, "wall_thickness_m": .12,
        "rooms": [
            {
                "id": "room_kitchen", "label": "厨房", "room_type": "kitchen",
                "polygon": rectangle(.276, .203, 2.852, 2.436), "selected": True,
            },
            {
                "id": "room_secondary_bedroom", "label": "次卧", "room_type": "bedroom",
                "polygon": rectangle(2.852, .203, 4.968, 2.958), "selected": True,
            },
            {
                "id": "room_master_bedroom", "label": "主卧", "room_type": "bedroom",
                "polygon": rectangle(4.968, .203, 8.372, 2.958), "selected": True,
            },
            {
                "id": "room_entryway", "label": "玄关", "room_type": "hallway", "selected": True,
                "polygon": [
                    {"x": .276, "z": 2.436}, {"x": 2.852, "z": 2.436},
                    {"x": 2.852, "z": 4.118}, {"x": .46, "z": 4.118},
                    {"x": .46, "z": 4.06}, {"x": .276, "z": 2.436},
                ],
            },
            {
                "id": "room_bathroom", "label": "卫生间", "room_type": "bathroom",
                "polygon": rectangle(.46, 4.118, 2.852, 5.51), "selected": True,
            },
            {
                "id": "room_living_room", "label": "客厅", "room_type": "living_room",
                "polygon": rectangle(2.852, 2.958, 7.084, 5.51), "selected": True,
            },
            {
                "id": "room_balcony", "label": "阳台", "room_type": "balcony",
                "polygon": rectangle(7.084, 2.958, 8.924, 5.51), "selected": True,
            },
        ],
        "walls": [
            {"id": "wall_top_ext", "kind": "exterior", "thickness_m": .24, "start": {"x": .276, "z": .203}, "end": {"x": 8.372, "z": .203}},
            {"id": "wall_master_east", "kind": "exterior", "thickness_m": .24, "start": {"x": 8.372, "z": .203}, "end": {"x": 8.372, "z": 2.958}},
            {"id": "wall_balcony_north", "kind": "exterior", "thickness_m": .24, "start": {"x": 8.372, "z": 2.958}, "end": {"x": 8.924, "z": 2.958}},
            {"id": "wall_right_ext", "kind": "exterior", "thickness_m": .24, "start": {"x": 8.924, "z": 2.958}, "end": {"x": 8.924, "z": 5.51}},
            {"id": "wall_bottom_ext", "kind": "exterior", "thickness_m": .24, "start": {"x": .46, "z": 5.51}, "end": {"x": 8.924, "z": 5.51}},
            {"id": "wall_bath_west", "kind": "exterior", "thickness_m": .24, "start": {"x": .46, "z": 4.06}, "end": {"x": .46, "z": 5.51}},
            {"id": "wall_entry_west", "kind": "exterior", "thickness_m": .24, "start": {"x": .276, "z": 2.436}, "end": {"x": .46, "z": 4.06}},
            {"id": "wall_kitchen_west", "kind": "exterior", "thickness_m": .24, "start": {"x": .276, "z": .203}, "end": {"x": .276, "z": 2.436}},
            {"id": "wall_kitchen_south", "kind": "interior", "thickness_m": .15, "start": {"x": .276, "z": 2.436}, "end": {"x": 2.852, "z": 2.436}},
            {"id": "wall_kitchen_east", "kind": "interior", "thickness_m": .15, "start": {"x": 2.852, "z": .203}, "end": {"x": 2.852, "z": 2.958}},
            {"id": "wall_sec_bed_east", "kind": "interior", "thickness_m": .12, "start": {"x": 4.968, "z": .203}, "end": {"x": 4.968, "z": 2.958}},
            {"id": "wall_hall_sec_bed", "kind": "interior", "thickness_m": .15, "start": {"x": 2.852, "z": 2.958}, "end": {"x": 4.968, "z": 2.958}},
            {"id": "wall_hall_master_bed", "kind": "interior", "thickness_m": .15, "start": {"x": 4.968, "z": 2.958}, "end": {"x": 7.084, "z": 2.958}},
            {"id": "wall_master_balcony_sep", "kind": "interior", "thickness_m": .15, "start": {"x": 7.084, "z": 2.958}, "end": {"x": 8.372, "z": 2.958}},
            {"id": "wall_bath_north", "kind": "interior", "thickness_m": .12, "start": {"x": .46, "z": 4.118}, "end": {"x": 2.852, "z": 4.118}},
            {"id": "wall_bath_east", "kind": "interior", "thickness_m": .12, "start": {"x": 2.852, "z": 4.118}, "end": {"x": 2.852, "z": 5.51}},
            {"id": "wall_balcony_west", "kind": "interior", "thickness_m": .15, "start": {"x": 7.084, "z": 2.958}, "end": {"x": 7.084, "z": 5.51}},
        ],
        "openings": [
            opening("door_main_entrance", "wall_entry_west", "door", .3672, .9),
            opening("door_kitchen_south", "wall_kitchen_south", "door", 1.624, .8),
            opening("door_bathroom_north", "wall_bath_north", "door", .729, .75),
            opening("door_sec_bed", "wall_hall_sec_bed", "door", .612, .8, confidence=.94),
            opening("door_master_bed", "wall_hall_master_bed", "door", .888, .8),
            opening("open_living_balcony", "wall_balcony_west", "open_connection", .376, 1.8),
            opening("window_kitchen_west", "wall_kitchen_west", "window", .773, .6),
            opening("window_balcony_east", "wall_right_ext", "window", .676, 1.2),
            # Exact locked v5 extra candidates: source=ai_edited.  This opening's
            # clear span z=2.158..2.958 crosses the kitchen/entry room junction.
            opening("open_kitchen_foyer", "wall_kitchen_east", "open_connection", 1.955, .8, confidence=.31, source="ai_edited"),
            opening("door_bed2", "wall_hall_sec_bed", "door", 1.158, .8, confidence=.42, source="ai_edited"),
            opening("door_bed1", "wall_master_balcony_sep", "door", .076, .8, confidence=.45, source="ai_edited"),
        ],
        "fixed_objects": [
            fixed("counter_kitchen", "Kitchen Counter", "counter", "kitchen_run", "room_kitchen", 1.26, 2.136, 1.56, .85, .6, observed=True),
            fixed("vanity_bath", "Bathroom Sink", "sanitary", "sink", "room_bathroom", 1.26, 5.26, .6, .8, .5, observed=True),
            fixed("sink_kitchen", "Kitchen Sink", "sink", "sink", "room_kitchen", 1.104, .696, .6, .85, .5),
            fixed("hob_kitchen", "Cooking Stove", "hob", "hob", "room_kitchen", 2.024, .696, .7, .85, .5),
            fixed("fridge_kitchen", "Refrigerator", "fridge", "fridge", "room_kitchen", .736, 1.45, .6, 1.8, .6),
            fixed("bed_sec", "Secondary Bedroom Bed", "bed", "bed", "room_secondary_bedroom", 3.91, 1.276, 1.5, 1, 2),
            fixed("wardrobe_sec", "Secondary Bedroom Wardrobe", "wardrobe", "wardrobe", "room_secondary_bedroom", 4.49, 2.46431, .9, 2.2, .5),
            fixed("bed_master", "Master Bed", "bed", "bed", "room_master_bedroom", 6.67, 1.276, 1.8, 1, 2),
            fixed("wardrobe_master", "Master Bedroom Wardrobe", "wardrobe", "wardrobe", "room_master_bedroom", 6.67, 2.552, 1.6, 2.2, .5),
            fixed("shoe_cabinet_entry", "Shoe Cabinet", "entry_storage", "entry_storage", "room_entryway", 1.104, 3.77, .8, 1, .35),
            fixed("toilet_bath", "Toilet", "toilet", "toilet", "room_bathroom", 2.024, 4.756, .5, .75, .7),
            fixed("shower_bath", "Shower Zone", "shower_zone", "shower_zone", "room_bathroom", 1.104, 4.64, .7, 2, .7, blocks=False),
            fixed("basin_bath", "Bathroom Basin", "basin", "basin", "room_bathroom", 2.024, 5.22, .5, .85, .4),
            fixed("sofa_living", "Living Room Sofa", "sofa", "sofa", "room_living_room", 4.968, 4.93, 2.2, .85, .9, rotation=180),
            fixed("tv_cabinet_living", "TV Unit", "tv", "tv", "room_living_room", 4.968, 3.364, 1.6, .5, .4),
            fixed("dining_table_living", "Dining Set", "dining_table", "dining_table", "room_living_room", 3.496, 3.77, 1.2, .75, .75),
            fixed("washer_balcony", "Washing Machine", "balcony_rail", "balcony_rail", "room_balcony", 8.004, 4.93, .65, .85, .65),
        ], "cameras": [],
    }, source="ai")
    assert len(model["rooms"]) == 7
    assert len(model["walls"]) == 17
    assert len(model["openings"]) == 11
    assert len(model["fixed_objects"]) == 17
    return model


def test_bathroom_sink_alias_deduplicates_only_ai_proxy_and_survives_semantic_rebuild():
    model = whole_home_engine.upgrade_model_v2(_portal_camera_regression_model())
    objects = {row["id"]: row for row in model["fixed_objects"]}

    # Exact v5 evidence: the observed Bathroom Sink is authoritative and is
    # canonicalized to basin; the inferred basin proxy is retained for audit
    # but rejected instead of becoming a second visible washbasin.
    vanity = objects["vanity_bath"]
    basin_proxy = objects["basin_bath"]
    assert vanity["name"] == "Bathroom Sink"
    assert vanity["kind"] == "sanitary"
    assert vanity["semantic_role"] == "basin"
    assert vanity["purpose"] == "observed_architecture"
    assert vanity["source"] == "ai" and vanity["observed"] is True
    assert vanity["review_status"] == "accepted"
    assert vanity["position"] == {"x": 1.26, "y": 0.0, "z": 5.26}
    assert basin_proxy["review_status"] == "rejected"
    assert basin_proxy["semantic_deduplication"]["duplicate_of"] == "vanity_bath"
    assert basin_proxy["semantic_deduplication"]["canonical_role"] == "basin"

    prompt_contract = whole_home_engine._semantic_prompt_contract(model)
    observed = {row["id"]: row for row in prompt_contract["observed_objects"]}
    assert observed["vanity_bath"]["semantic_role"] == "basin"
    assert "basin_bath" not in {
        row["id"] for row in prompt_contract["existing_layout_proxies"]
    }

    rebuilt = whole_home_engine._model_with_semantic_payload(model, {
        "objects": [{
            "id": "gemini-basin", "room_id": "room_bathroom",
            "semantic_role": "sink", "name": "Bathroom Sink",
            "center": {"x": .22, "z": .88}, "width_m": .55,
            "height_m": .85, "depth_m": .45, "confidence": .9,
        }],
        "room_assumptions": [], "uncertainties": [],
    })
    active_bath_basins = [
        row for row in rebuilt["fixed_objects"]
        if row["room_id"] == "room_bathroom"
        and row["semantic_role"] == "basin"
        and row["review_status"] != "rejected"
    ]
    assert [row["id"] for row in active_bath_basins] == ["vanity_bath"]
    assert active_bath_basins[0]["observed"] is True


def test_bathroom_alias_keeps_two_observed_or_human_objects_for_manual_resolution():
    model = _portal_camera_regression_model()
    second_observed = copy.deepcopy(next(
        row for row in model["fixed_objects"] if row["id"] == "vanity_bath"))
    second_observed.update(
        id="imported-second-basin", source="imported",
        purpose="observed_architecture", observed=True, review_status="accepted",
        position={"x": 2.024, "y": 0, "z": 5.22},
    )
    model["fixed_objects"].append(second_observed)

    upgraded = whole_home_engine.upgrade_model_v2(model)
    retained = [
        row for row in upgraded["fixed_objects"]
        if row["room_id"] == "room_bathroom"
        and row["semantic_role"] == "basin"
        and row["review_status"] != "rejected"
    ]
    assert {row["id"] for row in retained} == {"vanity_bath", "imported-second-basin"}
    assert all("semantic_deduplication" not in row for row in retained)


def test_opening_audit_rejects_ai_duplicate_and_flags_bedroom_kitchen_pass_through():
    source = _portal_camera_regression_model()
    audited = whole_home_engine.upgrade_model_v2(source)
    openings = {row["id"]: row for row in audited["openings"]}

    assert openings["door_sec_bed"]["review_status"] == "accepted"
    duplicate = openings["door_bed2"]
    assert duplicate["source"] == "ai_edited"
    assert duplicate["review_status"] == "rejected"
    assert duplicate["duplicate_of"] == "door_sec_bed"
    assert duplicate["opening_deduplication"]["overlap_m"] == .254
    assert duplicate["opening_deduplication"]["action"] == "rejected_ai_duplicate"
    implausible = openings["open_kitchen_foyer"]
    assert implausible["source"] == "ai_edited"
    assert implausible["offset_m"] == 1.955 and implausible["width_m"] == .8
    assert implausible["review_status"] == "pending"
    assert implausible["opening_topology_review"]["status"] == "manual_review_required"
    assert implausible["opening_topology_review"]["code"] == "opening_spans_room_junction"
    assert implausible["opening_topology_review"]["room_ids"] == [
        "room_entryway", "room_kitchen", "room_secondary_bedroom",
    ]
    samples = implausible["opening_topology_review"]["samples"]
    assert [row["label"] for row in samples] == ["start", "center", "end"]
    assert set(samples[0]["negative_room_ids"] + samples[0]["positive_room_ids"]) == {
        "room_kitchen", "room_secondary_bedroom",
    }
    assert set(samples[1]["negative_room_ids"] + samples[1]["positive_room_ids"]) == {
        "room_entryway", "room_secondary_bedroom",
    }
    assert set(samples[2]["negative_room_ids"] + samples[2]["positive_room_ids"]) == {
        "room_entryway", "room_secondary_bedroom",
    }
    assert openings["door_bed1"]["review_status"] == "accepted"
    assert openings["door_bed1"]["source"] == "ai_edited"

    direct = whole_home_engine._portal_direct_origin_room_ids(
        audited, "room_secondary_bedroom", implausible)
    assert direct == []
    assert "opening_spans_room_junction" in {
        row["code"] for row in whole_home_engine.validate_model(audited)["hard_errors"]
    }
    assert all(
        whole_home_engine._opening_spans_room_junction(
            audited, {**implausible, "kind": kind, "review_status": "accepted"})
        for kind in ("door", "window", "open_connection")
    )

    proposal = whole_home_engine.generate_semantic_camera_candidates(source)
    assert all(
        row.get("portal_opening_id") != "open_kitchen_foyer"
        for row in proposal["candidates"]
    )
    stale_capture = {
        "room_id": "room_secondary_bedroom",
        "camera": {
            "room_id": "room_secondary_bedroom", "origin_scope": "adjacent_portal",
            "portal_opening_id": "open_kitchen_foyer",
            "origin_room_ids": ["room_entryway"],
            "position": {"x": 2.502, "y": 1.55, "z": 2.878},
            "target": {"x": 3.91, "y": .62, "z": 1.276},
            "focal_length_mm": 24,
        },
    }
    with pytest.raises(ValueError, match="语义拓扑无效|未接受"):
        whole_home_engine.build_room_generation_contract(
            {"model": audited}, stale_capture)

    # Human/imported overlaps are never silently rewritten. They remain hard
    # geometry errors until a person chooses which physical opening is real.
    human = copy.deepcopy(source)
    for opening in human["openings"]:
        if opening["id"] in ("door_sec_bed", "door_bed2"):
            opening["source"] = "human"
            opening["review_status"] = "accepted"
        if opening["id"] == "open_kitchen_foyer":
            opening["source"] = "imported"
            opening["review_status"] = "accepted"
    preserved = whole_home_engine.upgrade_model_v2(human)
    assert all(
        next(row for row in preserved["openings"] if row["id"] == opening_id)["review_status"] == "accepted"
        for opening_id in ("door_sec_bed", "door_bed2")
    )
    assert "overlapping_accepted_openings_same_wall" in {
        row["code"] for row in whole_home_engine.validate_model(preserved)["hard_errors"]
    }
    imported_junction = next(
        row for row in preserved["openings"] if row["id"] == "open_kitchen_foyer")
    assert imported_junction["review_status"] == "accepted"
    assert "opening_spans_room_junction" in {
        row["code"] for row in whole_home_engine.validate_model(preserved)["hard_errors"]
    }
    preserved_proposal = whole_home_engine.generate_semantic_camera_candidates(preserved)
    assert all(
        row.get("portal_opening_id") != "open_kitchen_foyer"
        for row in preserved_proposal["candidates"]
    )
    with pytest.raises(ValueError, match="语义拓扑无效"):
        whole_home_engine.build_room_generation_contract(
            {"model": preserved}, stale_capture)


def test_narrow_bedroom_and_balcony_use_audited_portal_candidates_with_rejection_counts(monkeypatch, tmp_path):
    model = _portal_camera_regression_model()
    proposal = whole_home_engine.generate_semantic_camera_candidates(model, aspect_ratio="4:3")

    assert proposal["status"] == "ready"
    assert proposal["blocked_rooms"] == []
    assert model["fixed_objects"][-1]["semantic_role"] == "washing_machine"
    bedroom_rows = [
        row for row in proposal["candidates"]
        if row["room_id"] == "room_secondary_bedroom"
    ]
    assert 1 <= len(bedroom_rows) <= 8
    assert all(row["origin_scope"] == "doorway_inside" for row in bedroom_rows)
    assert all(row["entry_opening_id"] == "door_sec_bed" for row in bedroom_rows)
    assert all(not row.get("portal_opening_id") for row in bedroom_rows)
    assert all(row["camera"]["room_id"] == "room_secondary_bedroom" for row in bedroom_rows)
    assert all(row["camera"]["origin_scope"] == "doorway_inside" for row in bedroom_rows)
    assert {row["camera"]["focal_length_mm"] for row in bedroom_rows} == {20, 24}
    target_room = next(
        row for row in model["rooms"] if row["id"] == "room_secondary_bedroom")
    assert all(
        whole_home_engine._point_in_polygon(row["camera"]["position"], target_room["polygon"])
        for row in bedroom_rows
    )
    assert all(
        not any(
            other["id"] != "room_secondary_bedroom"
            and whole_home_engine._point_in_polygon(
                row["camera"]["position"], other["polygon"])
            for other in model["rooms"]
        )
        for row in bedroom_rows
    )
    assert all(
        not whole_home_engine._point_blocked_by_object(
            row["camera"]["position"],
            [item for item in model["fixed_objects"] if item["room_id"] == "room_secondary_bedroom"],
        )
        for row in bedroom_rows
    )
    balcony_rows = [row for row in proposal["candidates"] if row["room_id"] == "room_balcony"]
    assert 1 <= len(balcony_rows) <= 8
    assert all(row["origin_scope"] == "adjacent_portal" for row in balcony_rows)
    assert all(row["camera"]["room_id"] == "room_balcony" for row in balcony_rows)
    assert {row["camera"]["focal_length_mm"] for row in balcony_rows} == {20, 24}
    assert all(
        row["metrics"]["deferred_focal"] is (row["camera"]["focal_length_mm"] == 20)
        for row in balcony_rows
    )
    assert all(row["camera"]["focal_length_mm"] != 18 for row in balcony_rows)
    assert all(row["metrics"]["semantic_gate"] is True for row in balcony_rows)
    assert all(row["metrics"]["safety_gate"] is True for row in balcony_rows)
    assert all(row["origin_room_ids"] for row in balcony_rows)
    balcony_pool = next(row for row in proposal["room_pools"] if row["room_id"] == "room_balcony")
    assert balcony_pool["rejection_summary"] == proposal["rejection_summary"]["room_balcony"]
    assert all(
        row["metrics"]["rejection_summary"] == balcony_pool["rejection_summary"]
        for row in balcony_rows
    )
    assert proposal["rejection_summary"]["room_balcony"]["doorway_samples"] == 0
    assert all(
        not whole_home_engine._point_blocked_by_object(
            row["camera"]["position"],
            [item for item in model["fixed_objects"] if item["review_status"] != "rejected"],
        )
        for row in balcony_rows
    )

    bedroom_summary = proposal["rejection_summary"]["room_secondary_bedroom"]
    assert bedroom_summary["position_samples"] == 49
    assert bedroom_summary["position_inset_rejected"] == 24
    assert bedroom_summary["position_object_collision"] == 25
    assert bedroom_summary["raw_accepted"] == 0
    assert bedroom_summary["portal_openings"] == 1
    assert bedroom_summary["portal_samples"] == 60
    assert bedroom_summary["portal_object_collision"] == 60
    assert bedroom_summary["portal_accepted_raw"] == 0
    assert bedroom_summary["doorway_openings"] == 1
    assert bedroom_summary["doorway_samples"] == 40
    assert bedroom_summary["doorway_inside_room"] == 40
    assert bedroom_summary["doorway_outside_room"] == 0
    assert bedroom_summary["doorway_other_room"] == 0
    assert bedroom_summary["doorway_object_collision"] == 24
    assert bedroom_summary["doorway_los_blocked"] == 0
    assert bedroom_summary["doorway_accepted_raw"] == 32
    assert bedroom_summary["base_focal_accepted_raw"] == 16
    assert bedroom_summary["deferred_20mm_accepted_raw"] == 16
    assert all(
        row.get("entry_opening_id") != "open_kitchen_foyer"
        for row in bedroom_rows
    )
    bedroom_pool = next(
        row for row in proposal["room_pools"] if row["room_id"] == "room_secondary_bedroom")
    assert bedroom_pool["status"] == "ready"
    assert bedroom_pool["rejection_summary"] == bedroom_summary
    assert all("balcony_rail" not in row["metrics"]["visible_roles"] for row in balcony_rows)
    assert all(
        set(row["metrics"]["visible_roles"]).intersection({"window", "washing_machine"})
        for row in balcony_rows
    )
    balcony_camera = balcony_rows[0]["camera"]
    normalized_camera = whole_home_engine.normalize_model({
        **model, "cameras": [copy.deepcopy(balcony_camera)],
    })["cameras"][0]
    assert normalized_camera["origin_scope"] == "adjacent_portal"
    assert normalized_camera["portal_opening_id"] == balcony_camera["portal_opening_id"]
    assert normalized_camera["origin_room_ids"] == balcony_camera["origin_room_ids"]

    actual_origin_rooms = sorted(
        row["id"] for row in model["rooms"]
        if row["id"] != "room_balcony"
        and whole_home_engine._point_in_polygon(balcony_camera["position"], row["polygon"])
    )
    assert actual_origin_rooms == balcony_camera["origin_room_ids"]

    bedroom_camera = bedroom_rows[0]["camera"]
    normalized_bedroom_camera = whole_home_engine.normalize_model({
        **model, "cameras": [copy.deepcopy(bedroom_camera)],
    })["cameras"][0]
    assert normalized_bedroom_camera["origin_scope"] == "doorway_inside"
    assert normalized_bedroom_camera["entry_opening_id"] == "door_sec_bed"

    monkeypatch.setattr(whole_home_engine, "ASSET_DIR", str(tmp_path / "portal-overlay"))
    plan = tmp_path / "plan.png"
    Image.new("RGB", (500, 360), "white").save(plan)
    overlay = whole_home_engine.save_camera_plan_overlay(
        "locked-r5", "portal-capture", str(plan), model, balcony_camera,
    )
    assert os.path.isfile(overlay)


def test_portal_line_of_sight_must_cross_the_nominated_wall_opening():
    model = _portal_camera_regression_model()
    room = next(row for row in model["rooms"] if row["id"] == "room_secondary_bedroom")
    door = next(row for row in model["openings"] if row["id"] == "door_sec_bed")
    samples = whole_home_engine._portal_origin_samples(model, room, door)
    assert len(samples) == 30
    assert {row["distance_m"] for row in samples} == {.35, .5, .65, .8, 1.0, 1.2}
    assert max(abs(row["tangent_offset_m"]) for row in samples) <= door["width_m"] / 2 - .08 + 1e-9
    start = samples[0]["position"]
    bed = next(row for row in model["fixed_objects"] if row["id"] == "bed_sec")

    assert whole_home_engine._line_of_sight_via_opening(model, start, bed["position"], door) is True
    wrong_hole = {**door, "id": "wrong-hole", "offset_m": 0, "width_m": .45}
    assert whole_home_engine._line_of_sight_via_opening(model, start, bed["position"], wrong_hole) is False
    assert whole_home_engine._portal_origin_samples(
        model, room, {**door, "kind": "window"},
    ) == []


def test_doorway_inside_fallback_contract_stays_inside_and_is_not_portal_qa(tmp_path):
    model = whole_home_engine.upgrade_model_v2(_portal_camera_regression_model())
    room = next(row for row in model["rooms"] if row["id"] == "room_secondary_bedroom")
    door = next(row for row in model["openings"] if row["id"] == "door_sec_bed")
    invalid = next(row for row in model["openings"] if row["id"] == "open_kitchen_foyer")
    samples = whole_home_engine._doorway_inside_samples(model, room, door)
    assert len(samples) == 20
    assert {row["inset_m"] for row in samples} == {.10, .18, .25, .32}
    assert {row["tangent_offset_m"] for row in samples} == {0, -.144, .144, -.288, .288}
    assert max(abs(row["tangent_offset_m"]) for row in samples) <= door["width_m"] / 2 - .08
    assert all(whole_home_engine._point_in_polygon(row["position"], room["polygon"])
               for row in samples)
    assert whole_home_engine._doorway_inside_samples(model, room, invalid) == []

    proposal = whole_home_engine.generate_semantic_camera_candidates(model)
    candidate = next(
        row for row in proposal["candidates"]
        if row["room_id"] == "room_secondary_bedroom"
        and row["origin_scope"] == "doorway_inside"
    )
    image = tmp_path / "buffer.png"
    image.write_bytes(b"image")
    capture = {
        "capture_id": "capture-bedroom-entry", "room_id": "room_secondary_bedroom",
        "candidate_id": candidate["candidate_id"], "camera": copy.deepcopy(candidate["camera"]),
        "rgb_path": str(image), "depth_path": str(image), "normal_path": str(image),
        "edge_path": str(image), "semantic_path": str(image), "structure_path": str(image),
    }
    project = {
        "project_id": "doorway-inside-project", "model": model,
        "auto_camera_plans": [{"candidates": proposal["candidates"]}],
    }
    contract = whole_home_engine.build_room_generation_contract(project, capture)
    assert contract["portal_preservation"] is None
    entry = contract["entry_opening_audit"]
    assert entry["entry_opening_id"] == "door_sec_bed"
    assert entry["opening"]["kind"] == "door"
    assert entry["camera_inside_target_room"] is True
    assert entry["traverses_opening"] is False
    assert not any(
        row["constraint_id"] == "C008"
        for row in whole_home_engine._whole_home_qa_constraints(contract, "structure")
    )
    run = {"floor_path": str(image), "style": "现代自然", "lighting": "自然光"}
    structure_prompt, _ = whole_home_engine.build_generation_prompt(
        project, capture, run, pass_name="structure")
    material_prompt, _ = whole_home_engine.build_generation_prompt(
        project, capture, run, pass_name="material")
    assert "DOORWAY-INSIDE CAMERA AUDIT" in structure_prompt
    assert "does not traverse an adjacent-room portal" in structure_prompt
    assert "DOORWAY-INSIDE CAMERA AUDIT" in material_prompt
    assert "MANDATORY PORTAL PRESERVATION" not in structure_prompt

    outside = copy.deepcopy(capture)
    outside["camera"]["position"] = {"x": 3.576, "y": 1.55, "z": 3.2}
    with pytest.raises(ValueError, match="未严格位于目标房间"):
        whole_home_engine.build_room_generation_contract(project, outside)


def test_leafless_portal_is_in_generation_contract_prompts_and_fail_closed_qa(monkeypatch, tmp_path):
    model = whole_home_engine.upgrade_model_v2(_portal_camera_regression_model())
    proposal = whole_home_engine.generate_semantic_camera_candidates(model)
    candidate = next(
        row for row in proposal["candidates"]
        if row["room_id"] == "room_balcony" and row["origin_scope"] == "adjacent_portal"
    )
    image = tmp_path / "buffer.png"
    image.write_bytes(b"image")
    capture = {
        "capture_id": "capture-balcony-portal", "room_id": "room_balcony",
        "candidate_id": candidate["candidate_id"], "camera": copy.deepcopy(candidate["camera"]),
        "rgb_path": str(image), "depth_path": str(image), "normal_path": str(image),
        "edge_path": str(image), "semantic_path": str(image),
        "structure_path": str(image), "plan_overlay_path": str(image),
    }
    project = {
        "project_id": "portal-contract-project", "model": model,
        "auto_camera_plans": [{"candidates": proposal["candidates"]}],
    }
    contract = whole_home_engine.build_room_generation_contract(project, capture)
    portal = contract["portal_preservation"]
    assert portal["portal_opening_id"] == "open_living_balcony"
    assert portal["opening"]["kind"] == "open_connection"
    assert portal["opening"]["width_m"] == 1.8
    assert portal["origin_room_ids"] == ["room_living_room"]
    assert portal["direct_origin_room_ids"] == ["room_living_room"]
    assert portal["leafless_pass_through"] is True
    assert any(
        row["id"] == "open_living_balcony" and row["kind"] == "open_connection"
        for row in contract["accepted_openings"]
    )
    legacy_capture = copy.deepcopy(capture)
    legacy_capture["camera"].pop("origin_room_ids", None)
    assert whole_home_engine.build_room_generation_contract(
        project, legacy_capture)["portal_preservation"]["origin_room_ids"] == ["room_living_room"]

    run = {"floor_path": str(image), "style": "现代自然", "lighting": "自然光"}
    structure_prompt, _ = whole_home_engine.build_generation_prompt(
        project, capture, run, pass_name="structure")
    material_prompt, _ = whole_home_engine.build_generation_prompt(
        project, capture, run, pass_name="material")
    exact_rules = (
        "leafless pass-through", "door slab", "hinges", "threshold", "floor step",
        "change of kind",
    )
    assert all(rule in structure_prompt for rule in exact_rules)
    assert all(rule in material_prompt for rule in exact_rules)

    expected = whole_home_engine._whole_home_qa_constraints(contract, "structure")
    portal_check = next(row for row in expected if row["constraint_id"] == "C008")
    assert portal_check["category"] == "portal_preservation"
    assert portal_check["severity"] == "hard"
    assert "leafless pass-through" in portal_check["constraint"]
    assert "report uncertain" in portal_check["constraint"]
    answers = [
        {**row, "status": "pass", "evidence": "directly compared"}
        for row in expected if row["constraint_id"] != "C008"
    ]
    monkeypatch.setattr(whole_home_engine, "call_gemini_json", lambda *args, **kwargs: ({
        "geometry_score": 100, "camera_score": 100, "opening_score": 100,
        "material_score": 100, "room_identity_score": 100, "fixed_object_score": 100,
        "hard_fail": False, "summary": "looks perfect", "checks": answers,
    }, None))
    evaluation, error = whole_home_engine.evaluate_whole_home_phase(
        "key", project, capture, str(image), str(image), phase="structure")
    assert error is None
    assert evaluation["hard_fail"] is True
    assert evaluation["verification_incomplete"] is True
    assert evaluation["gate_pass"] is False
    omitted = next(row for row in evaluation["checks"] if row["constraint_id"] == "C008")
    assert omitted["status"] == "uncertain"
    assert "omitted" in omitted["evidence"]


def test_real_master_bedroom_candidates_filter_roles_occluded_by_other_objects():
    model = _portal_camera_regression_model()
    proposal = whole_home_engine.generate_semantic_camera_candidates(model)
    master_rows = [
        row for row in proposal["candidates"]
        if row["room_id"] == "room_master_bedroom"
    ]
    bed = next(row for row in model["fixed_objects"] if row["id"] == "bed_master")
    blockers = [
        row for row in model["fixed_objects"]
        if row["room_id"] == "room_master_bedroom" and row["id"] != "bed_master"
        and row["review_status"] != "rejected" and row["blocks_camera"]
    ]

    assert master_rows
    assert master_rows[0]["camera"]["position"] != {"x": 5.37648, "y": 1.55, "z": .8642}
    assert all("bed" in row["metrics"]["visible_roles"] for row in master_rows)
    assert all("occluded_roles" in row["metrics"] for row in master_rows)
    assert all(
        whole_home_engine._segment_object_entry(
            row["camera"]["position"], bed["position"], blocker,
        ) is None
        for row in master_rows for blocker in blockers
    )
    summary = proposal["rejection_summary"]["room_master_bedroom"]
    assert summary["occluded_role_hits"] > 0
    assert summary["occlusion_gate_rejected"] > 0
    cluttered = [
        row for row in master_rows
        if row["camera"]["position"] == {"x": 5.37648, "y": 1.55, "z": .8642}
    ]
    assert cluttered == []
    master_pool = next(
        row for row in proposal["room_pools"] if row["room_id"] == "room_master_bedroom")
    assert all(
        row["camera"]["position"] != {"x": 5.37648, "y": 1.55, "z": .8642}
        for row in master_rows if row["candidate_id"] in master_pool["candidate_ids"]
    )

    isolated = {**model, "walls": []}
    visible, occluded = whole_home_engine._visible_camera_roles(
        isolated,
        next(row for row in model["rooms"] if row["id"] == "room_master_bedroom"),
        {"x": 6.67, "z": 3.2}, bed["position"], 28, "4:3",
        [bed, *blockers], [],
    )
    assert "bed" not in visible
    assert "bed" in occluded


def test_portal_fallback_is_disabled_when_strict_room_candidate_exists():
    payload = _metric_model()
    payload["fixed_objects"] = [
        {
            "id": "sofa", "name": "sofa", "kind": "sofa", "semantic_role": "sofa",
            "position": {"x": 4.2, "y": 0, "z": 4.2},
            "size": {"x": 2.2, "y": .85, "z": .9}, "rotation_y_deg": 0,
            "room_id": "living", "source": "human", "purpose": "layout_proxy",
            "observed": False, "review_status": "accepted",
        },
        {
            "id": "tv", "name": "tv", "kind": "tv", "semantic_role": "tv",
            "position": {"x": 8.4, "y": 0, "z": 4.2},
            "size": {"x": 1.4, "y": 1, "z": .2}, "rotation_y_deg": 0,
            "room_id": "living", "source": "human", "purpose": "layout_proxy",
            "observed": False, "review_status": "accepted",
        },
    ]
    proposal = whole_home_engine.generate_semantic_camera_candidates(
        whole_home_engine.normalize_model(payload))

    assert proposal["candidates"]
    assert all(row["origin_scope"] == "inside_room" for row in proposal["candidates"])
    assert proposal["rejection_summary"]["living"]["portal_samples"] == 0
    assert proposal["rejection_summary"]["living"]["portal_accepted_raw"] == 0
    assert proposal["rejection_summary"]["living"]["doorway_samples"] == 0
    assert proposal["rejection_summary"]["living"]["doorway_accepted_raw"] == 0


def test_balcony_rail_requires_rail_evidence_and_washer_has_its_own_role():
    model = _portal_camera_regression_model()
    washer = next(row for row in model["fixed_objects"] if row["id"] == "washer_balcony")
    assert washer["semantic_role"] == "washing_machine"
    balcony_contract = next(
        row for row in model["room_contracts"] if row["room_id"] == "room_balcony")
    assert "washing_machine" in balcony_contract["preferred_roles"]

    rail = copy.deepcopy(washer)
    rail.update(id="actual-rail", name="阳台栏板", kind="parapet", semantic_role="balcony_rail")
    assert whole_home_engine._canonical_object_role(rail) == "balcony_rail"
    generic = copy.deepcopy(washer)
    generic.update(id="generic", name="fixed object", kind="appliance", semantic_role="balcony_rail")
    assert whole_home_engine._canonical_object_role(generic) != "balcony_rail"


def test_semantic_camera_candidates_block_room_without_contract_and_have_no_center_fallback():
    model = whole_home_engine.normalize_model(_metric_model(), source="ai")
    proposal = whole_home_engine.generate_semantic_camera_candidates(model)

    assert proposal["status"] == "blocked"
    assert proposal["candidates"] == []
    assert proposal["blocked_rooms"][0]["room_id"] == "living"
    assert any("缺少必需语义角色" in reason for reason in proposal["blocked_rooms"][0]["reasons"])


def test_camera_rank_discards_nonsemantic_candidate_and_builds_primary_backup_pool(monkeypatch, tmp_path):
    asset_dir = tmp_path / "semantic-camera-assets"
    plan_image = tmp_path / "semantic-camera-plan.png"
    Image.new("RGB", (300, 220), "white").save(plan_image)
    monkeypatch.setattr(whole_home_engine, "ASSET_DIR", str(asset_dir))
    monkeypatch.setattr(whole_home_engine, "call_gemini_json", lambda *args, **kwargs: (None, "offline"))
    project = {
        "project_id": "semantic-camera-rank", "floorplan_path": str(plan_image),
        "model": whole_home_engine.normalize_model(_metric_model()),
    }
    valid_a = _camera_candidate("living_valid_a", 91, "#aaaaaa")
    valid_b = _camera_candidate("living_valid_b", 84, "#bbbbbb")
    invalid = _camera_candidate("living_invalid", 99, "#ffffff")
    invalid["metrics"]["semantic_gate"] = False

    result = whole_home_engine.rank_auto_camera_plan(
        "key", project, [invalid, valid_a, valid_b], shots_per_room=1, aspect_ratio="4:3",
        requested_room_pools=[{
            "room_id": "living", "status": "ready", "candidate_ids": [],
            "rejection_summary": {"render_gate_rejected": 1},
        }],
    )

    assert {row["candidate_id"] for row in result["candidates"]} == {"living_valid_a", "living_valid_b"}
    pool = result["room_pools"][0]
    assert pool["candidate_ids"] == ["living_valid_a", "living_valid_b"]
    assert result["selected_cameras"][0]["is_primary"] is True
    assert result["selected_cameras"][0]["pool_rank"] == 1
    assert result["selected_cameras"][0]["render_gate"]["pass"] is True
    assert result["selected_cameras"][1]["is_primary"] is False
    assert result["selected_cameras"][1]["pool_rank"] == 2
    assert pool["rejection_summary"]["render_gate_rejected"] == 1


def test_camera_rank_rejects_render_invalid_and_defers_20mm_until_base_fails(monkeypatch, tmp_path):
    plan_image = tmp_path / "render-camera-plan.png"
    Image.new("RGB", (300, 220), "white").save(plan_image)
    monkeypatch.setattr(whole_home_engine, "ASSET_DIR", str(tmp_path / "render-camera-assets"))
    monkeypatch.setattr(whole_home_engine, "call_gemini_json", lambda *args, **kwargs: (None, "offline"))
    project = {
        "project_id": "render-camera-rank", "floorplan_path": str(plan_image),
        "model": whole_home_engine.normalize_model(_metric_model()),
    }
    base = _camera_candidate("base_24", 70, "#aaaaaa")
    deferred = _camera_candidate("deferred_20", 99, "#bbbbbb")
    deferred["camera"]["focal_length_mm"] = 20
    invalid = _camera_candidate("invalid_28", 100, "#cccccc")
    invalid["camera"]["focal_length_mm"] = 28
    invalid["metrics"]["render_gate"]["pass"] = False
    invalid["metrics"]["render_gate"]["status"] = "blocked"
    invalid["metrics"]["render_gate"]["reasons"] = ["地板仅 0.00%"]

    result = whole_home_engine.rank_auto_camera_plan(
        "key", project, [invalid, deferred, base], shots_per_room=1, aspect_ratio="4:3",
        requested_room_pools=[{"room_id": "living", "status": "ready", "candidate_ids": []}],
    )
    assert [row["candidate_id"] for row in result["candidates"]] == ["base_24"]
    assert result["selected_cameras"][0]["focal_length_mm"] == 24

    base["metrics"]["render_gate"]["pass"] = False
    base["metrics"]["render_gate"]["status"] = "blocked"
    fallback = whole_home_engine.rank_auto_camera_plan(
        "key", project, [invalid, deferred, base], shots_per_room=1, aspect_ratio="4:3",
        requested_room_pools=[{"room_id": "living", "status": "ready", "candidate_ids": []}],
    )
    assert [row["candidate_id"] for row in fallback["candidates"]] == ["deferred_20"]
    assert fallback["selected_cameras"][0]["focal_length_mm"] == 20


def test_candidate_target_height_is_profile_specific_and_audited():
    proposal = whole_home_engine.generate_semantic_camera_candidates(
        _portal_camera_regression_model(), aspect_ratio="4:3")
    expected = {
        "bedroom": .65,
        "living_room": .75,
        "kitchen": .75,
        "bathroom": .72,
        "foyer": .72,
        "balcony": .72,
    }
    assert proposal["candidates"]
    for row in proposal["candidates"]:
        profile = row["metrics"]["room_profile"]
        target_height = row["metrics"]["target_height_m"]
        if profile == "bedroom" and row["metrics"]["focus_kind"] == "object:bed":
            assert target_height == .62
        else:
            assert target_height == expected[profile]
        assert row["camera"]["target"]["y"] == target_height
        assert target_height < 1.1


def test_capture_persists_semantic_buffer_metadata_and_plan_overlay(monkeypatch, tmp_path):
    plan_image = tmp_path / "capture-plan.png"
    Image.new("RGB", (300, 220), "white").save(plan_image)
    monkeypatch.setattr(whole_home_engine, "ASSET_DIR", str(tmp_path / "capture-assets"))
    payload = _metric_model()
    payload["fixed_objects"] = [
        {"id": "sofa", "name": "Sofa", "kind": "sofa", "position": {"x": 4, "y": 0, "z": 4}, "size": {"x": 2, "y": .8, "z": .8}, "rotation_y_deg": 0, "room_id": "living", "source": "human", "confidence": 1},
        {"id": "tv", "name": "TV", "kind": "tv", "position": {"x": 8, "y": 0, "z": 4}, "size": {"x": 1.4, "y": 1, "z": .2}, "rotation_y_deg": 0, "room_id": "living", "source": "human", "confidence": 1},
    ]
    model = whole_home_engine.normalize_model(payload)
    project = {
        "project_id": "capture-semantic", "verified": True, "revision": 2,
        "floorplan_path": str(plan_image), "model": model, "captures": [], "operations": [],
    }
    persisted = []
    monkeypatch.setattr(routes_whole_home, "_project_entry", lambda project_id: project)
    monkeypatch.setattr(routes_whole_home, "_persist_project", lambda value: persisted.append(copy.deepcopy(value)))
    camera = {
        "id": "camera-primary", "name": "客厅主机位",
        "position": {"x": 2, "y": 1.55, "z": 2}, "target": {"x": 7, "y": 1.1, "z": 4},
        "focal_length_mm": 28, "room_id": "living", "enabled": True,
        "source": "ai_selected", "candidate_id": "living-a", "pool_rank": 1, "is_primary": True,
        "render_gate": copy.deepcopy(_camera_candidate("gate", 90, "#888888")["metrics"]["render_gate"]),
    }
    image_data = _preview_data_url("#888888")
    request = routes_whole_home.WholeHomeCaptureRequest(
        camera=camera, aspect_ratio="4:3", rgb_data_url=image_data, depth_data_url=image_data,
        normal_data_url=image_data, edge_data_url=image_data, semantic_data_url=image_data,
        semantic_legend={"sofa": "#ec4899"}, room_id="living", plan_id="plan-1",
        candidate_id="living-a", pool_rank=1, is_primary=True,
    )

    result = routes_whole_home.save_whole_home_capture("capture-semantic", request)
    capture = result["captures"][-1]

    assert capture["semantic_legend"] == {"sofa": "#ec4899"}
    assert capture["room_id"] == "living"
    assert capture["plan_id"] == "plan-1"
    assert capture["candidate_id"] == "living-a"
    assert capture["pool_rank"] == 1 and capture["is_primary"] is True
    assert capture["camera"]["render_gate"]["version"] == "whole-home-render-gate-v2"
    assert capture["camera"]["render_gate"]["pass"] is True
    assert os.path.isfile(capture["semantic_path"])
    assert os.path.isfile(capture["plan_overlay_path"])
    assert persisted


def test_automatic_capture_fails_closed_without_render_gate(monkeypatch, tmp_path):
    plan_image = tmp_path / "capture-gate-plan.png"
    Image.new("RGB", (120, 80), "white").save(plan_image)
    project = {
        "project_id": "capture-gate", "verified": True, "revision": 2,
        "floorplan_path": str(plan_image),
        "model": whole_home_engine.normalize_model(_metric_model()),
        "captures": [], "operations": [],
    }
    monkeypatch.setattr(routes_whole_home, "_project_entry", lambda project_id: project)
    request = routes_whole_home.WholeHomeCaptureRequest(
        camera={
            "id": "invalid-auto", "name": "invalid auto",
            "position": {"x": 2, "y": 1.55, "z": 2},
            "target": {"x": 7, "y": .75, "z": 4},
            "focal_length_mm": 24, "room_id": "living", "enabled": True,
            "source": "auto_geometry",
        },
        aspect_ratio="4:3", rgb_data_url=_preview_data_url("#888888"),
        depth_data_url=_preview_data_url("#888888"),
        normal_data_url=_preview_data_url("#888888"),
        semantic_data_url=_preview_data_url("#888888"),
        room_id="living", plan_id="plan", candidate_id="candidate",
    )
    try:
        routes_whole_home.save_whole_home_capture("capture-gate", request)
        assert False, "automatic capture without a passing render gate must fail closed"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
        assert "灰模渲染门禁" in str(getattr(exc, "detail", exc))
    assert project["captures"] == []


def test_whole_home_qa_retries_transient_failure_and_keeps_attempt_history(monkeypatch):
    responses = [
        ({"status": "unavailable", "hard_fail": False, "total": None, "summary": "timeout", "checks": []}, "timeout"),
        ({"status": "done", "hard_fail": False, "total": 88, "summary": "recovered", "checks": []}, None),
    ]
    monkeypatch.setattr(routes_whole_home, "evaluate_whole_home_phase", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(routes_whole_home, "_QA_SEMAPHORE", asyncio.Semaphore(1))
    evaluation, error, history = asyncio.run(routes_whole_home._evaluate_with_retries(
        "key", {}, {}, "result.jpg", "floor.jpg", phase="structure", attempts=2))
    assert error is None
    assert evaluation["status"] == "done"
    assert [row["status"] for row in history] == ["unavailable", "done"]
    assert history[0]["error"] == "timeout"


def test_structure_gate_failure_never_calls_material(monkeypatch, tmp_path):
    project, captures, run, result, _ = _state_machine_fixture(tmp_path, capture_count=1)
    calls = []

    async def fake_generate(*args):
        pass_name = args[7]
        attempt = args[2]
        calls.append((pass_name, attempt["capture_id"]))
        return Image.new("RGB", (64, 48), "white"), None, "google"

    async def fake_evaluate(*args, **kwargs):
        return ({"status": "done", "gate_pass": False, "hard_fail": True,
                 "checks": [{"constraint_id": "C001", "constraint": "room", "status": "fail", "evidence": "wrong"}]}, None, [])

    monkeypatch.setattr(routes_whole_home, "_call_generation", fake_generate)
    monkeypatch.setattr(routes_whole_home, "evaluate_structure_local_gate", lambda *args: _passing_local_gate("structure"))
    monkeypatch.setattr(routes_whole_home, "_evaluate_with_retries", fake_evaluate)
    monkeypatch.setattr(routes_whole_home, "save_api_result_jpg", lambda *args: str(tmp_path / "structure.jpg"))
    monkeypatch.setattr(routes_whole_home, "_persist_run", lambda run: None)
    asyncio.run(routes_whole_home._generate_one(
        run, project, {row["capture_id"]: row for row in captures}, result, "key"))

    assert calls == [("structure", "capture-1"), ("structure", "capture-1")]
    assert result["outcome"] == "structure_rejected"
    assert result["deliverable"] is False
    assert result["final_path"] == ""


def test_state_machine_retries_primary_then_uses_ordered_backup(monkeypatch, tmp_path):
    project, captures, run, result, _ = _state_machine_fixture(tmp_path, capture_count=3)
    calls = []

    async def fake_generate(*args):
        pass_name = args[7]
        attempt = args[2]
        capture_id = attempt.get("capture_id") or "material"
        calls.append((pass_name, capture_id))
        return Image.new("RGB", (64, 48), "white"), None, "google"

    async def fake_evaluate(api_key, project_value, capture, *args, **kwargs):
        passed = kwargs["phase"] == "final" or capture["capture_id"] == "capture-2"
        return ({"status": "done", "gate_pass": passed, "hard_fail": not passed,
                 "checks": [] if passed else [{"constraint_id": "C002", "constraint": "camera", "status": "fail", "evidence": "drift"}]}, None, [])

    monkeypatch.setattr(routes_whole_home, "_call_generation", fake_generate)
    monkeypatch.setattr(routes_whole_home, "evaluate_structure_local_gate", lambda *args: _passing_local_gate("structure"))
    monkeypatch.setattr(routes_whole_home, "evaluate_final_local_gate", lambda *args: _passing_local_gate("final"))
    monkeypatch.setattr(routes_whole_home, "_evaluate_with_retries", fake_evaluate)
    monkeypatch.setattr(routes_whole_home, "save_api_result_jpg", lambda *args: str(tmp_path / f"saved-{len(calls)}.jpg"))
    monkeypatch.setattr(routes_whole_home, "load_config", lambda: {})
    monkeypatch.setattr(routes_whole_home, "_persist_run", lambda run: None)
    asyncio.run(routes_whole_home._generate_one(
        run, project, {row["capture_id"]: row for row in captures}, result, "key"))

    assert calls[:3] == [
        ("structure", "capture-1"), ("structure", "capture-1"), ("structure", "capture-2")]
    assert calls[3][0] == "material"
    assert result["capture_id"] == "capture-2"
    assert result["outcome"] == "accepted"
    assert result["deliverable"] is True
    assert result["final_path"]


def test_local_structure_failure_skips_gemini_qa_and_material(monkeypatch, tmp_path):
    project, captures, run, result, _ = _state_machine_fixture(tmp_path, capture_count=1)
    generation_phases = []

    async def fake_generate(*args):
        generation_phases.append(args[7])
        return Image.new("RGB", (64, 48), "white"), None, "google"

    async def forbidden_qa(*args, **kwargs):
        raise AssertionError("Gemini QA must not run after a local structure failure")

    failed_gate = {
        **_passing_local_gate("structure"), "verdict": "fail", "gate_pass": False,
        "semantic_coverage_12": .4, "semantic_mean_distance": 16,
        "normal_coverage_12": .3, "summary": "local structure mismatch",
    }
    monkeypatch.setattr(routes_whole_home, "_call_generation", fake_generate)
    monkeypatch.setattr(routes_whole_home, "evaluate_structure_local_gate", lambda *args: copy.deepcopy(failed_gate))
    monkeypatch.setattr(routes_whole_home, "_evaluate_with_retries", forbidden_qa)
    monkeypatch.setattr(routes_whole_home, "save_api_result_jpg", lambda *args: str(tmp_path / "structure.jpg"))
    monkeypatch.setattr(routes_whole_home, "_persist_run", lambda run: None)
    asyncio.run(routes_whole_home._generate_one(
        run, project, {row["capture_id"]: row for row in captures}, result, "key"))

    assert generation_phases == ["structure", "structure"]
    assert result["outcome"] == "structure_rejected"
    assert result["final_path"] == ""
    assert all(attempt["structure_evaluation"] is None for attempt in result["attempts"])
    assert len([row for row in run["call_ledger"] if row["kind"] == "local_gate"]) == 2


def test_local_final_geometry_failure_retries_material_without_final_qa(monkeypatch, tmp_path):
    project, captures, run, result, _ = _state_machine_fixture(tmp_path, capture_count=1)
    generation_phases = []
    qa_phases = []

    async def fake_generate(*args):
        generation_phases.append(args[7])
        return Image.new("RGB", (64, 48), "white"), None, "google"

    async def fake_qa(*args, **kwargs):
        qa_phases.append(kwargs["phase"])
        return ({"status": "done", "gate_pass": True, "hard_fail": False, "checks": []}, None, [])

    failed_final = {
        **_passing_local_gate("final"), "verdict": "fail", "gate_pass": False,
        "structure_coverage_12": .2, "structure_mean_distance": 24,
        "summary": "material redrew the room",
    }
    monkeypatch.setattr(routes_whole_home, "_call_generation", fake_generate)
    monkeypatch.setattr(routes_whole_home, "evaluate_structure_local_gate", lambda *args: _passing_local_gate("structure"))
    monkeypatch.setattr(routes_whole_home, "evaluate_final_local_gate", lambda *args: copy.deepcopy(failed_final))
    monkeypatch.setattr(routes_whole_home, "_evaluate_with_retries", fake_qa)
    monkeypatch.setattr(routes_whole_home, "save_api_result_jpg", lambda *args: str(tmp_path / f"saved-{len(generation_phases)}.jpg"))
    monkeypatch.setattr(routes_whole_home, "load_config", lambda: {})
    monkeypatch.setattr(routes_whole_home, "_persist_run", lambda run: None)
    asyncio.run(routes_whole_home._generate_one(
        run, project, {row["capture_id"]: row for row in captures}, result, "key"))

    assert generation_phases == ["structure", "material", "material"]
    assert qa_phases == ["structure"]
    assert result["outcome"] == "material_rejected"
    assert result["deliverable"] is False and result["final_path"] == ""
    material_attempts = result["attempts"][0]["material_attempts"]
    assert len(material_attempts) == 2
    assert all(row["final_path"] and row["status"] == "rejected_local" for row in material_attempts)


def test_new_groups_create_one_independent_result_per_model_and_legacy_repeats_candidates():
    grouped = server_schemas.WholeHomeRunRequest(
        project_id="p", capture_groups=[{
            "room_id": "living", "primary_capture_id": "c1", "fallback_capture_ids": ["c2", "c3"],
        }], floor_path="floor.jpg", model_keys=["b2", "pro"], candidates_per_camera=2,
    )
    groups = [{
        "room_id": "living", "room_label": "客厅", "primary_capture_id": "c1",
        "fallback_capture_ids": ["c2", "c3"], "primary_camera_id": "cam1", "camera_name": "客厅",
    }]
    new_rows = routes_whole_home._result_rows(grouped, groups, legacy=False)
    legacy_rows = routes_whole_home._result_rows(grouped, groups, legacy=True)
    assert [(row["model_key"], row["candidate_index"]) for row in new_rows] == [("b2", 1), ("pro", 1)]
    assert len(legacy_rows) == 4
    assert all(row["capture_ids"] == ["c1", "c2", "c3"] for row in new_rows)


def test_run_creation_persists_immutable_snapshots_and_call_bounds(monkeypatch, tmp_path):
    project, captures, _, _, image_path = _state_machine_fixture(tmp_path, capture_count=3)
    project.update({
        "verified": True, "verified_revision": 4, "revision": 4,
        "captures": captures, "auto_camera_plans": [{"plan_id": "plan-1", "room_pools": []}],
    })
    for index, capture in enumerate(captures):
        capture.update(
            status="confirmed", source_hash="hash", plan_id="plan-1",
            pool_rank=index + 1, is_primary=index == 0,
        )
    monkeypatch.setattr(routes_whole_home, "_project_entry", lambda project_id: copy.deepcopy(project))
    monkeypatch.setattr(routes_whole_home, "require_upload_image_path", lambda *args, **kwargs: str(image_path))
    monkeypatch.setattr(routes_whole_home, "require_ref_image_path", lambda path: path)
    monkeypatch.setattr(routes_whole_home, "load_config", lambda: {"gemini_api_key": "secret-key"})
    monkeypatch.setattr(routes_whole_home, "_valid_capture", lambda *args: True)
    monkeypatch.setattr(routes_whole_home, "_persist_run", lambda run: None)

    def discard_spawn(coroutine):
        coroutine.close()

    monkeypatch.setattr(routes_whole_home.state, "spawn", discard_spawn)
    request = server_schemas.WholeHomeRunRequest(
        project_id="project-state", capture_groups=[{
            "room_id": "living", "primary_capture_id": "capture-1",
            "fallback_capture_ids": ["capture-2", "capture-3"],
        }], floor_path=str(image_path), model_keys=["b2", "pro"], resolution="4K",
    )
    response = asyncio.run(routes_whole_home.create_whole_home_run(request))
    try:
        stored = routes_whole_home._ACTIVE_RUNS[response["run_id"]]
        assert len(stored["results"]) == 2
        assert stored["estimated_minimum_model_calls"] == 4
        assert stored["estimated_model_calls"] == 12
        assert stored["estimated_qa_calls"] == 24
        assert stored["model_snapshot"]["schema_version"] == 2
        assert len(stored["capture_snapshots"]) == 3
        assert stored["room_contract_snapshots"][0]["room_id"] == "living"
        assert stored["camera_plan_snapshot"]["plan_id"] == "plan-1"
        assert stored["request_prompt_sha256"]
        assert "api_key" not in stored
    finally:
        routes_whole_home._ACTIVE_RUNS.pop(response["run_id"], None)
        routes_whole_home._RUN_KEYS.pop(response["run_id"], None)


def test_uncertain_mandatory_check_is_fail_closed(monkeypatch, tmp_path):
    project, captures, _, _, image_path = _state_machine_fixture(tmp_path, capture_count=1)
    capture = captures[0]
    contract = whole_home_engine.build_room_generation_contract(project, capture)
    expected = whole_home_engine._whole_home_qa_constraints(contract, "structure")
    checks = [{**row, "status": "pass", "evidence": "matched"} for row in expected]
    checks[0]["status"] = "uncertain"
    monkeypatch.setattr(whole_home_engine, "call_gemini_json", lambda *args, **kwargs: ({
        "geometry_score": 95, "camera_score": 95, "opening_score": 95, "material_score": 95,
        "room_identity_score": 95, "fixed_object_score": 95, "hard_fail": False,
        "summary": "looks plausible", "checks": checks,
    }, None))
    evaluation, error = whole_home_engine.evaluate_whole_home_phase(
        "key", project, capture, str(image_path), str(image_path), phase="structure")
    assert error is None
    assert evaluation["hard_fail"] is True
    assert evaluation["verification_incomplete"] is True
    assert evaluation["gate_pass"] is False


def test_reference_qa_requires_one_hard_check_per_visible_slot_subject(monkeypatch, tmp_path):
    project, captures, _, _, image_path = _state_machine_fixture(tmp_path, capture_count=1)
    capture = captures[0]
    capture.update(material_mode="reference", reference_slot_id="secondary_bath_toilet_shower")
    capture.setdefault("camera", {}).update(
        reference_slot_id="secondary_bath_toilet_shower",
        reference_contract_validation={
            "must_show_subjects": [
                {"subject": "toilet", "anchor_id": "toilet-1"},
                {"subject": "shower", "anchor_id": "shower-1"},
            ],
        },
    )
    project["reference_contract"] = {
        "contract_id": "reference-test",
        "reference_role": "style_and_composition_only",
        "geometry_authority": "cad",
        "slots": [{
            "slot_id": "secondary_bath_toilet_shower",
            "room_profile": "living_room",
            "reference_asset": {
                "status": "verified", "local_path": str(image_path),
                "asset_id": "bath-ref", "sha256": "a" * 64,
            },
            "reference_viewpoint": {"scene_id": 1},
        }],
    }
    next(room for room in project["model"]["rooms"]
         if room["id"] == capture["room_id"])["reference_room_profile"] = "living_room"
    contract = whole_home_engine.build_room_generation_contract(project, capture)
    expected = whole_home_engine._whole_home_qa_constraints(
        contract, "structure", "reference")
    subject_rows = [row for row in expected if row["constraint_id"].startswith("C2")]
    assert [(row["constraint_id"], row["category"]) for row in subject_rows] == [
        ("C201", "reference_required_subject"),
        ("C202", "reference_required_subject"),
    ]
    assert '"toilet"' in subject_rows[0]["constraint"]
    assert '"shower"' in subject_rows[1]["constraint"]

    checks = [{**row, "status": "pass", "evidence": "directly visible"}
              for row in expected]
    checks[-2]["status"] = "fail"
    checks[-2]["evidence"] = "toilet is absent from the candidate"
    monkeypatch.setattr(whole_home_engine, "call_gemini_json", lambda *args, **kwargs: ({
        "geometry_score": 95, "camera_score": 95, "opening_score": 95,
        "material_score": 95, "room_identity_score": 95, "fixed_object_score": 95,
        "hard_fail": False, "summary": "bathroom looks plausible", "checks": checks,
    }, None))
    evaluation, error = whole_home_engine.evaluate_whole_home_phase(
        "key", project, capture, str(image_path), "", phase="structure")
    assert error is None
    assert evaluation["gate_pass"] is False
    assert evaluation["hard_fail"] is True
    assert next(row for row in evaluation["checks"]
                if row["constraint_id"] == "C201")["status"] == "fail"


def test_cancelled_result_never_exposes_final_path(monkeypatch, tmp_path):
    project, captures, run, result, _ = _state_machine_fixture(tmp_path, capture_count=1)
    monkeypatch.setattr(routes_whole_home, "_persist_run", lambda run: None)
    routes_whole_home._CANCELLED.add(run["run_id"])
    try:
        asyncio.run(routes_whole_home._generate_one(
            run, project, {row["capture_id"]: row for row in captures}, result, "key"))
    finally:
        routes_whole_home._CANCELLED.discard(run["run_id"])
    assert result["outcome"] == "cancelled"
    assert result["deliverable"] is False
    assert result["path"] == result["final_path"] == ""


def test_gather_exception_is_converted_to_result_failure(monkeypatch, tmp_path):
    project, captures, run, result, _ = _state_machine_fixture(tmp_path, capture_count=1)

    async def explode(*args, **kwargs):
        raise RuntimeError("worker exploded")

    monkeypatch.setattr(routes_whole_home, "_generate_one", explode)
    monkeypatch.setattr(routes_whole_home, "_persist_run", lambda run: None)
    asyncio.run(routes_whole_home._run_generation(run, project, captures, "key"))
    assert result["status"] == "failed"
    assert result["outcome"] == "failed"
    assert result["final_path"] == ""


def test_review_manifest_keeps_failed_attempt_artifacts_without_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(whole_home_engine, "REVIEW_DIR", str(tmp_path / "reviews"))
    run = {
        "run_id": "run-review", "project_id": "project-review", "api_key": "must-not-leak",
        "created_at": 1, "updated_at": 2, "model_snapshot": {"schema_version": 2},
        "call_ledger": [{"call_id": "call-1", "status": "failed"}],
        "results": [{
            "result_id": "result-1", "attempts": [{"attempt_id": "attempt-1", "structure_path": "failed-structure.jpg"}],
            "deliverable": False, "outcome": "structure_rejected",
        }],
    }
    path = whole_home_engine.save_review_manifest(run)
    payload = json.loads(open(path, encoding="utf-8").read())
    assert payload["results"][0]["attempts"][0]["structure_path"] == "failed-structure.jpg"
    assert "must-not-leak" not in json.dumps(payload)


def test_v1_project_is_upgraded_in_memory_without_rewriting_source_and_old_capture_is_stale():
    legacy_model = _metric_model()
    legacy_model["schema_version"] = 1
    legacy_model["fixed_objects"] = [{
        "id": "legacy-sofa", "name": "Sofa", "kind": "sofa",
        "position": {"x": 3, "y": 0, "z": 3},
        "size": {"x": 2.2, "y": .85, "z": .9}, "rotation_y_deg": 0,
        "room_id": "living", "source": "ai", "confidence": .8,
    }]
    source = {
        "project_id": "legacy-project", "model": legacy_model,
        "captures": [{"capture_id": "old-capture", "status": "confirmed"}],
    }
    source_before = copy.deepcopy(source)
    runtime = whole_home_engine.runtime_project_copy(source)

    assert source == source_before
    assert runtime["model"]["schema_version"] == 2
    assert runtime["model"]["migrated_from_schema_version"] == 1
    assert runtime["model"]["fixed_objects"][0]["semantic_role"] == "sofa"
    assert runtime["model"]["room_contracts"][0]["profile"] == "living_room"
    assert runtime["captures"][0]["status"] == "stale"
    assert runtime["captures"][0]["stale_reason"] == "schema_v2_semantic_layout_required"


def _observed_repair_regression_model():
    return whole_home_engine.normalize_model({
        "coordinate_system": "metres-y-up", "width_m": 6, "depth_m": 6,
        "wall_height_m": 2.8, "wall_thickness_m": .12, "walls": [], "openings": [],
        "rooms": [
            {
                "id": "kitchen", "label": "厨房", "room_type": "kitchen",
                "polygon": [
                    {"x": .1, "z": .203}, {"x": 2.5, "z": .203},
                    {"x": 2.5, "z": 2.436}, {"x": .1, "z": 2.436},
                ],
            },
            {
                "id": "bath", "label": "卫生间", "room_type": "bathroom",
                "polygon": [
                    {"x": .4, "z": 4.118}, {"x": 2.12, "z": 4.118},
                    {"x": 2.12, "z": 5.51}, {"x": .4, "z": 5.51},
                ],
            },
        ],
        "fixed_objects": [
            {
                "id": "counter_kitchen", "name": "counter_kitchen", "kind": "counter_kitchen",
                "position": {"x": 1.26, "y": 0, "z": 2.584},
                "size": {"x": 1.56, "y": .9, "z": .6}, "rotation_y_deg": 0,
                "room_id": "kitchen", "source": "ai", "confidence": .8,
                "purpose": "observed_architecture", "observed": True, "review_status": "pending",
            },
            {
                "id": "vanity_bath", "name": "vanity_bath", "kind": "vanity_bath",
                "position": {"x": 1.26, "y": 0, "z": 5.8},
                "size": {"x": .6, "y": .8, "z": .5}, "rotation_y_deg": 0,
                "room_id": "bath", "source": "ai", "confidence": .8,
                "purpose": "observed_architecture", "observed": True, "review_status": "pending",
            },
        ], "cameras": [],
    }, source="ai")


def test_ai_observed_architecture_is_minimally_projected_before_semantic_prompt():
    original = _observed_repair_regression_model()
    repaired = whole_home_engine.repair_ai_observed_architecture(original)
    objects = {row["id"]: row for row in repaired["fixed_objects"]}

    counter = objects["counter_kitchen"]
    vanity = objects["vanity_bath"]
    assert counter["semantic_role"] == "kitchen_run"
    assert vanity["semantic_role"] == "basin"
    assert counter["original_position"]["z"] == 2.584
    assert vanity["original_position"]["z"] == 5.8
    assert counter["position"]["z"] == 2.136
    assert vanity["position"]["z"] == 5.26
    assert counter["geometry_repair"]["status"] == "translated"
    assert counter["geometry_repair"]["method"] == "deterministic_room_projection_v1"
    assert counter["geometry_repair"]["distance_m"] == .448
    assert vanity["geometry_repair"]["distance_m"] == .54
    assert counter["source"] == "ai" and counter["observed"] is True
    assert not any(
        issue["code"] == "semantic_object_outside_room"
        for issue in repaired["semantic_report"]["hard_errors"]
    )

    contract = whole_home_engine._semantic_prompt_contract(repaired)
    prompt_facts = {row["id"]: row for row in contract["observed_objects"]}
    assert prompt_facts["counter_kitchen"]["center"]["z"] == round(2.136 / 6, 5)
    assert prompt_facts["counter_kitchen"]["original_center"]["z"] == round(2.584 / 6, 5)
    assert prompt_facts["counter_kitchen"]["geometry_repair"]["status"] == "translated"

    round_trip = whole_home_engine.normalize_model(repaired, source="ai")
    persisted = {row["id"]: row for row in round_trip["fixed_objects"]}["counter_kitchen"]
    assert persisted["original_position"]["z"] == 2.584
    assert persisted["geometry_repair"]["translation_m"]["z"] == -.448


def test_observed_geometry_repair_never_moves_human_or_imported_objects():
    model = _observed_repair_regression_model()
    model["fixed_objects"][0]["source"] = "human"
    model["fixed_objects"][1]["source"] = "imported"
    positions = {row["id"]: copy.deepcopy(row["position"]) for row in model["fixed_objects"]}

    repaired = whole_home_engine.repair_ai_observed_architecture(model)

    assert {row["id"]: row["position"] for row in repaired["fixed_objects"]} == positions
    assert all("geometry_repair" not in row for row in repaired["fixed_objects"])
    outside = [
        issue for issue in repaired["semantic_report"]["hard_errors"]
        if issue["code"] == "semantic_object_outside_room"
    ]
    assert {row["object_id"] for row in outside} == {"counter_kitchen", "vanity_bath"}


def test_unfit_ai_observed_object_keeps_original_geometry_and_auditable_hard_error():
    model = whole_home_engine.normalize_model({
        "width_m": 2, "depth_m": 2, "walls": [], "openings": [], "cameras": [],
        "rooms": [{
            "id": "tiny", "label": "卫生间", "room_type": "bathroom",
            "polygon": [
                {"x": .2, "z": .2}, {"x": .6, "z": .2},
                {"x": .6, "z": .6}, {"x": .2, "z": .6},
            ],
        }],
        "fixed_objects": [{
            "id": "wide-vanity", "name": "vanity", "kind": "vanity",
            "position": {"x": .4, "y": 0, "z": .4},
            "size": {"x": .8, "y": .8, "z": .5}, "rotation_y_deg": 0,
            "room_id": "tiny", "source": "ai", "purpose": "observed_architecture",
            "observed": True, "review_status": "pending",
        }],
    }, source="ai")

    repaired = whole_home_engine.repair_ai_observed_architecture(model)
    vanity = repaired["fixed_objects"][0]
    assert vanity["position"] == model["fixed_objects"][0]["position"]
    assert vanity["original_position"] == model["fixed_objects"][0]["position"]
    assert vanity["geometry_repair"]["status"] == "failed"
    assert vanity["geometry_repair"]["reason"] == "object_larger_than_room_bounds"
    assert any(
        issue["code"] == "semantic_object_outside_room"
        for issue in repaired["semantic_report"]["hard_errors"]
    )


def test_ai_proxy_allows_five_centimetre_quantization_but_not_visible_overrun():
    payload = {
        "width_m": 4, "depth_m": 3, "walls": [], "openings": [], "cameras": [],
        "rooms": [{
            "id": "bedroom", "label": "次卧", "room_type": "bedroom",
            "polygon": [
                {"x": .1, "z": .203}, {"x": 3.9, "z": .203},
                {"x": 3.9, "z": 2.8}, {"x": .1, "z": 2.8},
            ],
        }],
        "fixed_objects": [{
            "id": "bed", "name": "bed", "kind": "bed", "semantic_role": "bed",
            "position": {"x": 1.3, "y": 0, "z": .96},
            "size": {"x": 2, "y": .55, "z": 1.6}, "rotation_y_deg": 0,
            "room_id": "bedroom", "source": "ai", "purpose": "layout_proxy",
            "observed": False, "review_status": "pending",
        }],
    }
    model = whole_home_engine.normalize_model(payload, source="ai")
    assert not any(
        issue["code"] == "semantic_object_outside_room"
        for issue in whole_home_engine.validate_semantic_layout(model)["hard_errors"]
    )

    model["fixed_objects"][0]["position"]["z"] = .94
    assert any(
        issue["code"] == "semantic_object_outside_room"
        for issue in whole_home_engine.validate_semantic_layout(model)["hard_errors"]
    )


def test_local_acceptance_is_per_object_while_remaining_hard_error_still_blocks_layout():
    payload = {
        "width_m": 4, "depth_m": 3, "walls": [], "openings": [], "cameras": [],
        "rooms": [{
            "id": "bedroom", "label": "主卧", "room_type": "bedroom",
            "polygon": [
                {"x": .1, "z": .1}, {"x": 3.9, "z": .1},
                {"x": 3.9, "z": 2.9}, {"x": .1, "z": 2.9},
            ],
        }],
        "fixed_objects": [
            {
                "id": "valid-bed", "name": "bed", "kind": "bed", "semantic_role": "bed",
                "position": {"x": 1.5, "y": 0, "z": 1.5},
                "size": {"x": 2, "y": .55, "z": 1.6}, "rotation_y_deg": 0,
                "room_id": "bedroom", "source": "ai", "purpose": "layout_proxy",
                "observed": False, "review_status": "pending",
            },
            {
                "id": "bad-wardrobe", "name": "wardrobe", "kind": "wardrobe",
                "position": {"x": 3.9, "y": 0, "z": 2.5},
                "size": {"x": 1.2, "y": 2.2, "z": .6}, "rotation_y_deg": 0,
                "room_id": "bedroom", "source": "ai", "purpose": "layout_proxy",
                "observed": False, "review_status": "pending",
            },
        ],
    }
    accepted = whole_home_engine._accept_locally_valid_ai_layout(
        whole_home_engine.normalize_model(payload, source="ai"))
    objects = {row["id"]: row for row in accepted["fixed_objects"]}
    assert objects["valid-bed"]["review_status"] == "accepted"
    assert objects["valid-bed"]["semantic_acceptance"]["status"] == "accepted_object_only"
    assert objects["bad-wardrobe"]["review_status"] == "pending"
    assert accepted["semantic_report"]["status"] == "needs_review"
    assert any(
        issue["object_id"] == "bad-wardrobe"
        for issue in accepted["semantic_report"]["hard_errors"]
        if issue["code"] == "semantic_object_outside_room"
    )


def test_observed_sink_role_prevents_duplicate_inferred_sink_anywhere_in_room():
    payload = {
        "width_m": 4, "depth_m": 3, "walls": [], "openings": [], "cameras": [],
        "rooms": [{
            "id": "kitchen", "label": "厨房", "room_type": "kitchen",
            "polygon": [
                {"x": .1, "z": .1}, {"x": 3.9, "z": .1},
                {"x": 3.9, "z": 2.9}, {"x": .1, "z": 2.9},
            ],
        }],
        "fixed_objects": [{
            "id": "observed-sink", "name": "sink_kitchen", "kind": "sink_kitchen",
            "position": {"x": .6, "y": 0, "z": .5},
            "size": {"x": .6, "y": .9, "z": .5}, "rotation_y_deg": 0,
            "room_id": "kitchen", "source": "ai", "purpose": "observed_architecture",
            "observed": True, "review_status": "pending",
        }],
    }
    base = whole_home_engine.normalize_model(payload, source="ai")
    semantic_payload = {
        "objects": [{
            "id": "duplicate-sink", "room_id": "kitchen", "semantic_role": "sink",
            "name": "sink", "center": {"x": .85, "z": .75},
            "width_m": .6, "depth_m": .5, "height_m": .9,
            "rotation_y_deg": 0, "confidence": .8,
        }],
    }
    merged = whole_home_engine._model_with_semantic_payload(base, semantic_payload)
    sinks = [row for row in merged["fixed_objects"] if row["semantic_role"] == "sink"]
    assert len(sinks) == 1
    assert sinks[0]["id"] == "observed-sink"


def _semantic_proxy_repair_regression_model():
    return whole_home_engine.normalize_model({
        "coordinate_system": "metres-y-up", "width_m": 6, "depth_m": 6,
        "wall_height_m": 2.8, "wall_thickness_m": .12,
        "walls": [
            {
                "id": "wall_bathroom_north", "kind": "interior",
                "start": {"x": .4, "z": 4.118}, "end": {"x": 2.12, "z": 4.118},
            },
            {
                "id": "wall_sec_bed_south", "kind": "interior",
                "start": {"x": 2.7, "z": 2.936}, "end": {"x": 5.2, "z": 2.936},
            },
        ],
        "rooms": [
            {
                "id": "room_bathroom", "label": "卫生间", "room_type": "bathroom",
                "polygon": [
                    {"x": .4, "z": 4.118}, {"x": 2.12, "z": 4.118},
                    {"x": 2.12, "z": 5.51}, {"x": .4, "z": 5.51},
                ],
            },
            {
                "id": "room_secondary_bedroom", "label": "次卧", "room_type": "bedroom",
                "polygon": [
                    {"x": 2.7, "z": .203}, {"x": 5.2, "z": .203},
                    {"x": 5.2, "z": 2.936}, {"x": 2.7, "z": 2.936},
                ],
            },
        ],
        "openings": [
            {
                "id": "door_bathroom_north", "wall_id": "wall_bathroom_north", "kind": "door",
                "offset_m": .354, "width_m": .7, "height_m": 2.1, "sill_height_m": 0,
                "review_status": "accepted",
            },
            {
                "id": "door_sec_bed", "wall_id": "wall_sec_bed_south", "kind": "door",
                "offset_m": .81, "width_m": .8, "height_m": 2.1, "sill_height_m": 0,
                "review_status": "accepted",
            },
        ],
        "fixed_objects": [
            {
                "id": "vanity_bath", "name": "vanity", "kind": "vanity", "semantic_role": "basin",
                "position": {"x": 1.26, "y": 0, "z": 5.26},
                "size": {"x": .6, "y": .8, "z": .5}, "rotation_y_deg": 0,
                "room_id": "room_bathroom", "source": "ai", "purpose": "observed_architecture",
                "observed": True, "review_status": "accepted",
            },
            {
                "id": "shower_bath", "name": "shower", "kind": "shower", "semantic_role": "shower_zone",
                "position": {"x": 1.104, "y": 0, "z": 4.35},
                "size": {"x": .7, "y": 2.1, "z": .7}, "rotation_y_deg": 0,
                "room_id": "room_bathroom", "source": "ai", "purpose": "layout_proxy",
                "observed": False, "review_status": "pending", "clearance_m": .25,
            },
            {
                "id": "wardrobe_sec", "name": "wardrobe", "kind": "wardrobe",
                "position": {"x": 3.91, "y": 0, "z": 2.668},
                "size": {"x": 1.2, "y": 2.2, "z": .5}, "rotation_y_deg": 0,
                "room_id": "room_secondary_bedroom", "source": "ai", "purpose": "layout_proxy",
                "observed": False, "review_status": "pending", "clearance_m": .25,
            },
        ], "cameras": [],
    }, source="ai")


def test_invalid_ai_proxies_are_projected_away_from_real_room_and_door_failures():
    model = _semantic_proxy_repair_regression_model()
    initial = whole_home_engine.validate_semantic_layout(model)
    shower_codes = whole_home_engine._object_semantic_issue_codes(initial, "shower_bath")
    wardrobe_codes = whole_home_engine._object_semantic_issue_codes(initial, "wardrobe_sec")
    assert shower_codes == ["semantic_object_blocks_door", "semantic_object_outside_room"]
    assert wardrobe_codes == ["semantic_object_blocks_door"]

    original_hash = whole_home_engine.model_hash(model)
    repaired = whole_home_engine.repair_ai_semantic_proxies(model)
    objects = {row["id"]: row for row in repaired["fixed_objects"]}
    shower, wardrobe = objects["shower_bath"], objects["wardrobe_sec"]
    report = repaired["semantic_report"]

    assert shower["position"] != {"x": 1.104, "y": 0, "z": 4.35}
    assert wardrobe["position"] != {"x": 3.91, "y": 0, "z": 2.668}
    assert shower["original_position"] == {"x": 1.104, "y": 0.0, "z": 4.35}
    assert wardrobe["original_position"] == {"x": 3.91, "y": 0.0, "z": 2.668}
    assert shower["geometry_repair"]["method"] == "deterministic_semantic_proxy_projection_v1"
    assert shower["geometry_repair"]["trigger_codes"] == shower_codes
    assert wardrobe["geometry_repair"]["trigger_codes"] == wardrobe_codes
    assert shower["geometry_repair"]["result_position"] == shower["position"]
    assert shower["source"] == "ai" and shower["review_status"] == "pending"
    for object_id in ("shower_bath", "wardrobe_sec"):
        assert whole_home_engine._object_semantic_issue_codes(report, object_id) == []
    assert not any(
        issue["code"] == "semantic_object_overlap"
        and "shower_bath" in {issue.get("object_id"), issue.get("other_object_id")}
        for issue in report["hard_errors"]
    )
    assert whole_home_engine.model_hash(repaired) != original_hash

    round_trip = whole_home_engine.normalize_model(repaired, source="ai")
    persisted = {row["id"]: row for row in round_trip["fixed_objects"]}["shower_bath"]
    assert persisted["original_position"] == shower["original_position"]
    assert persisted["geometry_repair"]["trigger_codes"] == shower_codes
    prompt_proxy = {
        row["id"]: row
        for row in whole_home_engine._semantic_prompt_contract(round_trip)["existing_layout_proxies"]
    }["shower_bath"]
    assert prompt_proxy["geometry_repair"]["method"] == "deterministic_semantic_proxy_projection_v1"
    assert prompt_proxy["center"]["z"] == round(shower["position"]["z"] / 6, 5)


def test_proxy_repair_never_moves_human_imported_edited_or_accepted_objects():
    model = _semantic_proxy_repair_regression_model()
    objects = {row["id"]: row for row in model["fixed_objects"]}
    objects["shower_bath"]["source"] = "human"
    objects["wardrobe_sec"]["source"] = "ai_edited"
    accepted = copy.deepcopy(objects["shower_bath"])
    accepted.update(id="accepted-proxy", source="ai", review_status="accepted")
    model["fixed_objects"].append(accepted)
    imported = copy.deepcopy(objects["wardrobe_sec"])
    imported.update(id="imported-proxy", source="imported")
    model["fixed_objects"].append(imported)
    positions = {row["id"]: copy.deepcopy(row["position"]) for row in model["fixed_objects"]}

    repaired = whole_home_engine.repair_ai_semantic_proxies(model)

    assert {row["id"]: row["position"] for row in repaired["fixed_objects"]} == positions
    protected = {"shower_bath", "wardrobe_sec", "accepted-proxy", "imported-proxy"}
    assert all(
        "geometry_repair" not in row
        for row in repaired["fixed_objects"] if row["id"] in protected
    )


def test_proxy_repair_keeps_hard_failure_when_door_leaves_no_feasible_position():
    model = whole_home_engine.normalize_model({
        "width_m": 1.2, "depth_m": 1.2,
        "walls": [{
            "id": "north", "start": {"x": .1, "z": .1}, "end": {"x": .8, "z": .1},
        }],
        "rooms": [{
            "id": "tiny", "label": "卫生间", "room_type": "bathroom",
            "polygon": [
                {"x": .1, "z": .1}, {"x": .8, "z": .1},
                {"x": .8, "z": .8}, {"x": .1, "z": .8},
            ],
        }],
        "openings": [{
            "id": "only-door", "wall_id": "north", "kind": "door",
            "offset_m": .05, "width_m": .6, "height_m": 2.1, "sill_height_m": 0,
            "review_status": "accepted",
        }],
        "fixed_objects": [{
            "id": "trapped-shower", "name": "shower", "kind": "shower",
            "position": {"x": .45, "y": 0, "z": .45},
            "size": {"x": .6, "y": 2.1, "z": .6}, "rotation_y_deg": 0,
            "room_id": "tiny", "source": "ai", "purpose": "layout_proxy",
            "observed": False, "review_status": "pending", "clearance_m": .25,
        }], "cameras": [],
    }, source="ai")
    original = copy.deepcopy(model["fixed_objects"][0]["position"])

    repaired = whole_home_engine.repair_ai_semantic_proxies(model)
    shower = repaired["fixed_objects"][0]

    assert shower["position"] == original
    assert shower["review_status"] == "pending"
    assert shower["geometry_repair"]["status"] == "failed"
    assert shower["geometry_repair"]["reason"] == "no_feasible_position_avoiding_room_door_overlap"
    assert "semantic_object_blocks_door" in shower["geometry_repair"]["trigger_codes"]
    assert any(
        issue["code"] == "semantic_object_blocks_door"
        and issue["object_id"] == "trapped-shower"
        for issue in repaired["semantic_report"]["hard_errors"]
    )


def test_proxy_repair_avoids_door_and_existing_object_overlap_in_same_search():
    model = _semantic_proxy_repair_regression_model()
    shower = next(row for row in model["fixed_objects"] if row["id"] == "shower_bath")
    vanity = next(row for row in model["fixed_objects"] if row["id"] == "vanity_bath")
    vanity["position"] = {"x": 1.104, "y": 0, "z": 4.82}
    vanity["size"] = {"x": .9, "y": .8, "z": .7}

    repaired = whole_home_engine.repair_ai_semantic_proxies(model)
    objects = {row["id"]: row for row in repaired["fixed_objects"]}
    report = repaired["semantic_report"]

    assert objects["shower_bath"]["position"] != shower["position"]
    assert not any(
        issue["code"] in {"semantic_object_blocks_door", "semantic_object_outside_room", "semantic_object_overlap"}
        and "shower_bath" in {issue.get("object_id"), issue.get("other_object_id")}
        for issue in report["hard_errors"]
    )


def test_semantic_validator_reports_missing_roles_outside_room_and_blocked_door():
    payload = _metric_model()
    payload["fixed_objects"] = [{
        "id": "bad-sofa", "name": "Sofa", "kind": "sofa", "semantic_role": "sofa",
        "position": {"x": 2.45, "y": 0, "z": .55},
        "size": {"x": 2.4, "y": .85, "z": 1.0}, "rotation_y_deg": 0,
        "room_id": "living", "source": "ai", "confidence": 1,
        "purpose": "layout_proxy", "observed": False, "review_status": "pending",
    }]
    model = whole_home_engine.normalize_model(payload)
    report = whole_home_engine.validate_semantic_layout(model)
    codes = {row["code"] for row in report["hard_errors"]}
    assert "semantic_required_role_missing" in codes
    assert "semantic_object_outside_room" in codes
    assert "semantic_object_blocks_door" in codes
    missing = [row for row in report["hard_errors"] if row["code"] == "semantic_required_role_missing"]
    assert any(row["role_group"] == ["tv"] for row in missing)
    blocked = whole_home_engine._accept_locally_valid_ai_layout(model)
    assert blocked["fixed_objects"][0]["review_status"] == "pending"
    assert blocked["semantic_report"]["hard_errors"]


def test_semantic_layout_runs_one_repair_and_keeps_the_locally_valid_candidate(monkeypatch, tmp_path):
    plan = tmp_path / "semantic-plan.png"
    Image.new("RGB", (240, 180), "white").save(plan)
    payload = _metric_model()
    payload["rooms"][0]["label"] = "主卧"
    payload["rooms"][0]["room_type"] = "bedroom"
    payload["fixed_objects"] = []
    base = whole_home_engine.normalize_model(payload, source="ai")

    def layout(center):
        return {
            "summary": "bedroom layout",
            "objects": [{
                "id": "bed-proxy", "room_id": "living", "semantic_role": "bed", "name": "Bed",
                "center": center, "width_m": 2.0, "depth_m": 1.6, "height_m": .55,
                "rotation_y_deg": 0, "confidence": .9, "assumption": "required bedroom proxy",
            }],
            "room_assumptions": [{"room_id": "living", "assumptions": ["bed faces the clear wall"]}],
            "uncertainties": [], "_floor_engine_model": "gemini-semantic-test",
        }

    responses = [(layout({"x": .99, "z": .99}), None), (layout({"x": .5, "z": .5}), None)]
    calls = []

    def fake_call(api_key, prompt, image_paths, schema, max_output_tokens=0):
        calls.append((prompt, image_paths))
        assert api_key == "key"
        assert len(image_paths) == 2
        assert all(os.path.isfile(path) for path in image_paths)
        return responses.pop(0)

    monkeypatch.setattr(whole_home_engine, "call_gemini_json", fake_call)
    model, error, model_name = whole_home_engine.analyze_semantic_layout("key", str(plan), base)

    assert error is None
    assert len(calls) == 2
    assert "Local semantic failures" in calls[1][0]
    assert model["semantic_report"]["audit_passes"] == 2
    assert model["semantic_report"]["hard_errors"] == []
    assert not any(row["code"] == "semantic_object_pending" for row in model["semantic_report"]["warnings"])
    bed = next(row for row in model["fixed_objects"] if row["semantic_role"] == "bed")
    assert bed["purpose"] == "layout_proxy"
    assert bed["source"] == "ai"
    assert bed["observed"] is False
    assert bed["review_status"] == "accepted"
    assert bed["semantic_acceptance"]["method"] == "local_semantic_rules_v1"
    assert bed["semantic_acceptance"]["scope"] == "placement_and_contract_only"
    assert bed["position"]["x"] == 5
    assert "semantic repair" in model_name


def test_locally_valid_ai_semantic_layout_passes_verify_gate_without_semantic_ack(monkeypatch, tmp_path):
    plan = tmp_path / "verified-semantic-plan.png"
    Image.new("RGB", (240, 180), "white").save(plan)
    payload = _metric_model()
    payload["rooms"][0]["label"] = "主卧"
    payload["rooms"][0]["room_type"] = "bedroom"
    payload["fixed_objects"] = [{
        "id": "bed-proxy", "name": "Bed", "kind": "bed", "semantic_role": "bed",
        "position": {"x": 5, "y": 0, "z": 4},
        "size": {"x": 2, "y": .55, "z": 1.6}, "rotation_y_deg": 0,
        "room_id": "living", "source": "ai", "confidence": .9,
        "purpose": "layout_proxy", "observed": False, "review_status": "pending",
    }]
    model = whole_home_engine._accept_locally_valid_ai_layout(
        whole_home_engine.normalize_model(payload, source="ai"))
    geometry_report = whole_home_engine.validate_model(model, str(plan))
    project = {
        "project_id": "verify-semantic", "revision": 1, "verified": False,
        "floorplan_path": str(plan), "model": model, "captures": [], "operations": [],
    }
    persisted = []
    monkeypatch.setattr(routes_whole_home, "_project_entry", lambda project_id: copy.deepcopy(project))
    monkeypatch.setattr(routes_whole_home, "_persist_project", lambda value: persisted.append(copy.deepcopy(value)))
    request = routes_whole_home.WholeHomeVerifyRequest(
        base_revision=1,
        acknowledged_warning_codes=[row["code"] for row in geometry_report["warnings"]],
    )

    result = routes_whole_home.verify_whole_home_model("verify-semantic", request)

    assert result["verified"] is True
    assert persisted[-1]["model"]["semantic_report"]["hard_errors"] == []
    assert persisted[-1]["model"]["semantic_report"]["warnings"] == []


def test_semantic_layout_failure_keeps_v2_shell_and_reports_missing_contract(monkeypatch, tmp_path):
    plan = tmp_path / "semantic-failure.png"
    Image.new("RGB", (240, 180), "white").save(plan)
    monkeypatch.setattr(whole_home_engine, "call_gemini_json", lambda *args, **kwargs: (None, "quota"))
    base = whole_home_engine.normalize_model(_metric_model(), source="ai")

    model, error, model_name = whole_home_engine.analyze_semantic_layout("key", str(plan), base)

    assert error == "quota"
    assert model_name == ""
    assert model["schema_version"] == 2
    assert model["walls"] == base["walls"]
    assert model["semantic_report"]["audit_passes"] == 0
    assert model["semantic_report"]["status"] == "needs_review"
