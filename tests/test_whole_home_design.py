import asyncio
import json
import os
import zipfile

import pytest
from fastapi import HTTPException
from PIL import Image, ImageDraw

from Floor_engine_server import routes_whole_home_design as routes
from Floor_engine_server import whole_home_design as design


@pytest.fixture
def design_store(tmp_path, monkeypatch):
    root = tmp_path / "whole_home_design"
    projects = root / "projects"
    assets = root / "assets"
    bundles = root / "bundles"
    for folder in (projects, assets, bundles):
        folder.mkdir(parents=True)
    monkeypatch.setattr(design, "ROOT", str(root))
    monkeypatch.setattr(design, "PROJECT_ROOT", str(projects))
    monkeypatch.setattr(design, "ASSET_ROOT", str(assets))
    monkeypatch.setattr(design, "BUNDLE_ROOT", str(bundles))
    design._LOCKS.clear()
    source = tmp_path / "plan.png"
    image = Image.new("RGB", (1200, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 1100, 800), outline="black", width=20)
    draw.line((600, 100, 600, 800), fill="black", width=12)
    image.save(source)
    return source


def confirmed_project(source):
    project = design.create_project(str(source), "unit-plan.png")
    project["plan_summary"] = {
        **design.empty_plan_summary("human"),
        "room_count": 2,
        "rooms": [
            {"id": "living", "label": "客厅", "room_type": "living", "coarse_location": "left", "adjacent_room_ids": ["bed"]},
            {"id": "bed", "label": "卧室", "room_type": "bedroom", "coarse_location": "right", "adjacent_room_ids": ["living"]},
        ],
        "dimension_evidence": ["overall width 10m"],
    }
    project["plan_summary_confirmed"] = True
    project["brief"] = {
        "requirements_text": "现代暖木自然风，真实家具和自然日光",
        "reference_paths": [],
        "reference_hashes": [],
    }
    project["brief_hash"] = design._brief_hash(project)
    project["status"] = "ready"
    design.save_project(project)
    return project


def test_normalization_trims_only_white_margin_and_pads_supported_ratio(design_store):
    project = design.create_project(str(design_store), "plan.png")
    assert project["normalization"]["crop_policy"] == "near-white-margin-only"
    assert project["normalization"]["aspect_ratio"] in {row[0] for row in design.SUPPORTED_RATIOS}
    assert os.path.isfile(project["normalized_path"])
    with Image.open(project["normalized_path"]) as image:
        assert image.width >= project["normalization"]["content_size"][0]
        assert image.height >= project["normalization"]["content_size"][1]


def test_generation_crop_excludes_detached_thin_detail_but_keeps_main_plan(tmp_path, monkeypatch):
    root = tmp_path / "design"
    assets = root / "assets"
    assets.mkdir(parents=True)
    monkeypatch.setattr(design, "ASSET_ROOT", str(assets))
    source = tmp_path / "sheet.png"
    image = Image.new("RGB", (1200, 900), "white")
    draw = ImageDraw.Draw(image)
    # Detached cabinet/detail diagram: deliberately thin and not architecture.
    draw.rectangle((40, 300, 240, 520), outline="black", width=1)
    draw.line((50, 350, 230, 350), fill="black", width=1)
    # Main plan: thick connected walls with an internal partition and balcony.
    draw.rectangle((400, 120, 1080, 800), outline="black", width=18)
    draw.line((720, 130, 720, 790), fill="black", width=14)
    draw.rectangle((500, 55, 800, 130), outline="black", width=12)
    image.save(source)
    path, metadata = design.extract_generation_plan(str(source), "crop_case")
    assert os.path.isfile(path)
    assert metadata["fallback_reason"] == ""
    assert metadata["crop_box"][0] > 300
    assert metadata["crop_box"][2] >= 1080


def test_annotation_cleanup_erases_only_safe_boxes(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setattr(design, "ASSET_ROOT", str(assets))
    raw = tmp_path / "raw.png"
    image = Image.new("RGB", (500, 500), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 460, 460), outline="black", width=14)
    draw.rectangle((200, 220, 300, 250), fill="black")
    image.save(raw)
    cleaned, metadata = design.clean_generation_annotations(str(raw), "clean_case", [{
        "label": "下沉:430", "kind": "elevation_text", "box_2d": [430, 390, 520, 610],
        "confidence": 0.98, "safe_to_erase": True,
    }])
    with Image.open(cleaned) as result:
        assert result.getpixel((250, 235)) == (255, 255, 255)
        assert result.getpixel((40, 100)) == (0, 0, 0)
    assert metadata["applied_count"] == 1


def test_prompt_makes_source_structural_authority(design_store):
    project = confirmed_project(design_store)
    prompt = design.build_design_prompt(project, phase="draft", direction_index=2)
    assert "Image 1 is always the structural authority" in prompt
    assert "Strict vertical overhead orthographic view" in prompt
    assert "No labels" in prompt
    assert "现代暖木自然风" in prompt


def test_room_normalization_adds_missing_adjacency_placeholder_and_symmetry():
    rooms = design.normalize_plan_rooms([{
        "id": "Bedroom 1", "label": "卧室", "room_type": "bedroom",
        "coarse_location": "north", "adjacent_room_ids": ["hallway"],
        "confidence": 0.8, "evidence": "door to corridor", "needs_confirmation": False,
    }])
    assert [room["id"] for room in rooms] == ["bedroom_1", "hallway"]
    hallway = rooms[1]
    assert hallway["label"] == "过道"
    assert hallway["needs_confirmation"] is True
    assert hallway["adjacent_room_ids"] == ["bedroom_1"]


def test_automatic_summary_prefills_evidence_confidence_and_title_facts(design_store, monkeypatch):
    project = design.create_project(str(design_store), "plan.png")
    payload = {
        "room_count": 2,
        "rooms": [
            {"id": "living", "label": "客厅", "room_type": "living", "coarse_location": "left",
             "adjacent_room_ids": ["bed"], "confidence": 0.92, "evidence": "large central space", "needs_confirmation": False},
            {"id": "bed", "label": "卧室", "room_type": "bedroom", "coarse_location": "right",
             "adjacent_room_ids": ["living"], "confidence": 0.7, "evidence": "bay window and AC", "needs_confirmation": True},
        ],
        "declared_layout": {"bedrooms": 1, "halls": 1, "bathrooms": 0,
                            "source_text": "1房1厅", "confidence": 0.98},
        "declared_area_m2": 60.0,
        "overall_dimensions_mm": {"width": 10000, "depth": 6000,
                                  "evidence": ["top 10000"], "confidence": 0.9},
        "summary_confidence": 0.81,
        "review_items": [{"id": "role-bed", "kind": "room_role", "label": "确认卧室",
                          "evidence": "inferred from symbols", "confidence": 0.7,
                          "status": "needs_confirmation"}],
        "entrances": ["south"], "openings_summary": ["east bay window"],
        "wet_zones": [], "balconies": [], "dimension_evidence": ["10000mm"],
        "must_preserve": ["east bay window"], "uncertainties": ["bedroom role"],
    }
    monkeypatch.setattr(design, "call_gemini_json", lambda *_args, **_kwargs: (payload, None))
    result = design.analyze_plan(project["project_id"])
    summary = result["plan_summary"]
    assert summary["declared_layout"]["source_text"] == "1房1厅"
    assert summary["overall_dimensions_mm"]["width"] == 10000
    assert summary["rooms"][1]["needs_confirmation"] is True
    assert summary["review_items"][0]["status"] == "needs_confirmation"
    assert any(item.get("room_id") == "bed" for item in summary["review_items"])


def test_empty_plan_summary_cannot_be_confirmed(design_store):
    project = design.create_project(str(design_store), "plan.png")
    request = routes.PlanSummaryPutRequest(
        base_revision=project["revision"], room_count=0, rooms=[], confirmed=True)
    with pytest.raises(HTTPException) as exc:
        routes.save_plan_summary(project["project_id"], request)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "empty_plan_summary"


def test_retry_analysis_runs_from_async_route_and_invalidates_confirmation(design_store, monkeypatch):
    project = confirmed_project(design_store)
    scheduled = []
    monkeypatch.setattr(routes, "load_config", lambda: {"gemini_api_key": "configured"})
    def capture(coro):
        scheduled.append(coro)
        coro.close()
        return None
    monkeypatch.setattr(routes, "_track", capture)
    response = asyncio.run(routes.retry_design_plan_analysis(
        project["project_id"], routes.PreviewRequest(base_revision=project["revision"])))
    assert response["status"] == "analyzing_plan"
    assert response["plan_summary_confirmed"] is False
    assert response["revision"] == project["revision"] + 1
    assert len(scheduled) == 1


def test_draft_commit_schedules_once_and_idempotent_retry_reuses_candidates(design_store, monkeypatch):
    project = confirmed_project(design_store)
    preview = routes._create_preview(project, kind="drafts")
    scheduled = []
    def capture(coro):
        scheduled.append(coro)
        coro.close()
        return None
    monkeypatch.setattr(routes, "_track", capture)
    request = routes.CommitRequest(
        base_revision=project["revision"], preview_id=preview["preview_id"],
        preview_hash=preview["preview_hash"], confirmation_phrase=preview["confirmation_phrase"],
        idempotency_key="draft-validation-idempotency",
    )
    first = asyncio.run(routes.commit_design_drafts(project["project_id"], request))
    second = asyncio.run(routes.commit_design_drafts(project["project_id"], request))
    assert len(first["candidates"]) == 2
    assert [row["candidate_id"] for row in first["candidates"]] == [
        row["candidate_id"] for row in second["candidates"]]
    assert len(scheduled) == 1


def test_input_change_marks_candidates_and_bundles_stale(design_store):
    project = confirmed_project(design_store)
    project["candidates"] = [{"candidate_id": "draft_1", "status": "done", "stale": False}]
    project["bundles"] = [{"bundle_id": "bundle_1", "stale": False}]
    project["locked_candidate_id"] = "draft_1"
    design.mark_candidates_stale(project, "brief changed")
    assert project["candidates"][0]["stale"] is True
    assert project["bundles"][0]["stale"] is True
    assert project["locked_candidate_id"] == ""


def test_no_gemini_requires_manual_structure_review(design_store, monkeypatch):
    project = confirmed_project(design_store)
    candidate = design._save_candidate_image(project["project_id"], "draft", Image.new("RGB", (2000, 1500), "white"))
    monkeypatch.setattr(design, "load_config", lambda: {})
    qa = design.evaluate_structure(project, candidate)
    assert qa["status"] == "manual_required"
    assert qa["hard_fail"] is False


def test_automatic_hard_fail_cannot_be_human_overridden(design_store):
    project = confirmed_project(design_store)
    candidate = {
        "candidate_id": "final_bad", "phase": "final", "status": "done", "stale": False,
        "structure_qa": {"status": "failed", "hard_fail": True, "summary": "missing room", "checks": []},
        "human_review": {"status": "pending", "checks": {}},
    }
    project["candidates"] = [candidate]
    design.save_project(project)
    request = routes.StructureReviewRequest(
        base_revision=project["revision"],
        checks={item: True for item in design.STRUCTURE_REVIEW_ITEMS},
        reviewer="tester",
        note="",
    )
    with pytest.raises(HTTPException) as exc:
        routes.review_candidate_structure(project["project_id"], "final_bad", request)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "automated_structure_hard_fail"


def test_manual_review_can_record_non_overridable_structure_failure(design_store):
    project = confirmed_project(design_store)
    path = design._save_candidate_image(project["project_id"], "draft_bad", Image.new("RGB", (2000, 1500), "white"))
    project["candidates"] = [{
        "candidate_id": "draft_bad", "phase": "draft", "status": "done", "stale": False,
        "path": path, "structure_qa": {"status": "manual_required", "hard_fail": False, "checks": []},
        "human_review": {"status": "pending", "checks": {}},
    }]
    design.save_project(project)
    request = routes.StructureReviewRequest(
        base_revision=project["revision"], checks={}, decision="fail", reviewer="tester",
        note="新增了原图不存在的楼梯")
    result = routes.review_candidate_structure(project["project_id"], "draft_bad", request)
    candidate = result["candidates"][0]
    assert candidate["human_review"]["status"] == "failed"
    assert candidate["structure_qa"]["hard_fail"] is True
    assert "楼梯" in candidate["structure_qa"]["summary"]


def test_lock_requires_final_4k_and_both_reviews(design_store):
    project = confirmed_project(design_store)
    final_path = design._save_candidate_image(
        project["project_id"], "final_good", Image.new("RGB", (3600, 2700), (220, 210, 195)))
    candidate = {
        "candidate_id": "final_good", "phase": "final", "status": "done", "stale": False,
        "path": final_path, "image_size": [3600, 2700], "source_hash": project["source_hash"],
        "generation_hash": project["generation_hash"],
        "brief_hash": project["brief_hash"], "result_hash": design.file_sha256(final_path),
        "structure_qa": {"status": "passed", "hard_fail": False, "checks": []},
        "human_review": {"status": "passed", "checks": {item: True for item in design.STRUCTURE_REVIEW_ITEMS}},
    }
    project["candidates"] = [candidate]
    design.save_project(project)
    result = routes.lock_design_candidate(
        project["project_id"], "final_good", routes.CandidateActionRequest(base_revision=project["revision"]))
    assert result["status"] == "locked"
    assert result["locked_candidate_id"] == "final_good"


def test_modeling_bundle_has_authority_and_no_secrets(design_store):
    project = confirmed_project(design_store)
    final_path = design._save_candidate_image(
        project["project_id"], "final", Image.new("RGB", (3600, 2700), "white"))
    candidate = {
        "candidate_id": "final", "parent_candidate_id": "", "path": final_path,
        "result_hash": design.file_sha256(final_path), "prompt_version": design.PROMPT_VERSION,
        "prompt": "safe prompt", "provider": "fal", "model_id": "model", "endpoint": "endpoint",
        "structure_qa": {"status": "passed", "hard_fail": False},
        "human_review": {"status": "passed", "checks": {item: True for item in design.STRUCTURE_REVIEW_ITEMS}},
    }
    bundle = design.build_modeling_bundle(project, candidate)
    assert os.path.isfile(bundle["path"])
    with zipfile.ZipFile(bundle["path"]) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "AGENT_TASK.md" in names
        assert "blender/acceptance.json" in names
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["target_profile"] == "blender-mcp-v1"
        assert manifest["blocked_when_scale_missing"] is True
        content = b"".join(archive.read(name) for name in names if name.endswith((".json", ".md", ".txt")))
        assert b"api_key" not in content.lower()
        assert str(design_store).encode() not in content


def test_public_project_redacts_queue_handle(design_store):
    project = confirmed_project(design_store)
    project["candidates"] = [{
        "candidate_id": "draft", "path": "", "queue_handle": {"request_id": "secret-provider-id"},
    }]
    public = design.public_project(project)
    assert "queue_handle" not in public["candidates"][0]
    assert "source_path" not in public
    assert "normalized_path" not in public
