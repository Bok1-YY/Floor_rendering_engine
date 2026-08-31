import asyncio
import io
import json
import os
import zipfile
from copy import deepcopy

import pymupdf
import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image, ImageDraw

from Floor_engine_server import routes_whole_home_design as routes
from Floor_engine_server import whole_home_design as design
from Floor_engine_server.tools.fastloop_research.contract import compute_anchor_set_hash, compute_structure_hash


def test_invalid_design_pdf_upload_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "UPLOAD_DIR", str(tmp_path))
    upload = UploadFile(filename="broken.pdf", file=io.BytesIO(b"not a pdf"))
    with pytest.raises(HTTPException):
        routes.upload_design_floorplan(upload)
    assert [path for path in tmp_path.iterdir() if path.is_file()] == []


def test_project_create_route_forwards_explicit_orientation_policy(monkeypatch):
    captured = {}
    monkeypatch.setattr(routes, "require_upload_image_path", lambda value, *_args, **_kwargs: value)
    monkeypatch.setattr(routes, "create_project", lambda source, name, *, orientation_policy: captured.update(source=source, name=name, policy=orientation_policy) or {"project_id": "P"})
    monkeypatch.setattr(routes, "public_project", lambda project: project)
    result = asyncio.run(routes.create_design_project(routes.ProjectCreateRequest(
        floorplan_path="C:/fixture.jpg", source_name="fixture",
        orientation_policy="ignore_invalid_exif_user_confirmed_raw",
    )))
    assert result == {"project_id": "P"}
    assert captured["policy"] == "ignore_invalid_exif_user_confirmed_raw"


def test_oversized_design_pdf_cleans_source_and_partial_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "MAX_DESIGN_PDF_PAGE_PIXELS", 1)
    document = pymupdf.open()
    document.new_page(width=100, height=100)
    payload = document.tobytes()
    document.close()
    upload = UploadFile(filename="large.pdf", file=io.BytesIO(payload))
    with pytest.raises(HTTPException) as exc:
        routes.upload_design_floorplan(upload)
    assert exc.value.status_code == 413
    assert [path for path in tmp_path.iterdir() if path.is_file()] == []


@pytest.fixture
def design_store(tmp_path, monkeypatch):
    root = tmp_path / "whole_home_design"
    projects = root / "projects"
    assets = root / "assets"
    bundles = root / "bundles"
    models = root / "models"
    for folder in (projects, assets, bundles, models):
        folder.mkdir(parents=True)
    monkeypatch.setattr(design, "ROOT", str(root))
    monkeypatch.setattr(design, "PROJECT_ROOT", str(projects))
    monkeypatch.setattr(design, "ASSET_ROOT", str(assets))
    monkeypatch.setattr(design, "BUNDLE_ROOT", str(bundles))
    monkeypatch.setattr(design, "MODEL_ROOT", str(models))
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
    anchors = design.validate_anchor_set(project, {
        "coordinate_space": "normalized-evidence-1000-v1",
        "source_hash": project["source_hash"], "confirmed_complete": True,
        "anchors": [
            {"anchor_id": "P01", "kind": "space", "label": "客厅", "note": "", "points": [{"x": 300, "y": 500}]},
            {"anchor_id": "P02", "kind": "space", "label": "卧室", "note": "", "points": [{"x": 700, "y": 500}]},
            {"anchor_id": "P03", "kind": "entrance", "label": "入户门", "note": "", "points": [{"x": 240, "y": 800}]},
            {"anchor_id": "P04", "kind": "scale", "label": "总宽 10m", "note": "", "distance_mm": 10000, "points": [{"x": 100, "y": 100}, {"x": 900, "y": 100}]},
        ],
    })
    overlay, overlay_hash = design.render_anchor_overlay(project, anchors)
    project["anchor_set"] = anchors
    project["anchor_overlay_path"] = overlay
    project["anchor_overlay_hash"] = overlay_hash
    project["anchor_verification"] = {"status": "verified", "conflicts": [], "changes": [], "inferred_anchor_gaps": []}
    project["plan_summary"] = {
        **design.empty_plan_summary("human"),
        "room_count": 2,
        "rooms": [
            {"id": "living", "label": "客厅", "room_type": "living", "coarse_location": "left", "adjacent_room_ids": ["bed"], "anchor_ids": ["P01"], "source": "human_anchor"},
            {"id": "bed", "label": "卧室", "room_type": "bedroom", "coarse_location": "right", "adjacent_room_ids": ["living"], "anchor_ids": ["P02"], "source": "human_anchor"},
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


def test_floorplan_orientation_is_explicit_and_can_ignore_confirmed_invalid_exif(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setattr(design, "ASSET_ROOT", str(assets))
    source = tmp_path / "stale-orientation.jpg"
    image = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(image).rectangle((10, 20, 190, 80), outline="black", width=5)
    exif = Image.Exif()
    exif[274] = 8
    image.save(source, exif=exif)
    _, default_meta = design.normalize_floorplan(str(source), "default")
    ignored_path, ignored_meta = design.normalize_floorplan(str(source), "ignored", orientation_policy="ignore_invalid_exif_user_confirmed_raw")
    assert default_meta["original_size"] == [100, 200]
    assert ignored_meta["original_size"] == [200, 100]
    assert ignored_meta["exif_orientation"] == 8
    assert ignored_meta["orientation_policy"] == "ignore_invalid_exif_user_confirmed_raw"
    project = design.create_project(str(source), "stale-orientation.jpg", orientation_policy="ignore_invalid_exif_user_confirmed_raw")
    assert project["source_hash"] == design.file_sha256(str(source))
    assert project["source_orientation"]["raw_pixel_hash"] == ignored_meta["raw_pixel_hash"]
    assert project["source_orientation"]["normalized_hash"] == design.file_sha256(ignored_path) or project["source_orientation"]["normalized_hash"] == design.file_sha256(project["normalized_path"])


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


def test_anchor_contract_and_overlay_are_deterministic(design_store):
    project = design.create_project(str(design_store), "plan.png")
    payload = {
        "coordinate_space": "normalized-evidence-1000-v1",
        "source_hash": project["source_hash"], "confirmed_complete": True,
        "anchors": [
            {"anchor_id": "P01", "kind": "space", "label": "主卧", "note": "人工确认", "points": [{"x": 750, "y": 600}]},
            {"anchor_id": "P02", "kind": "entrance", "label": "入户门", "note": "", "points": [{"x": 120, "y": 800}, {"x": 180, "y": 800}]},
            {"anchor_id": "P03", "kind": "scale", "label": "总宽 10m", "note": "", "distance_mm": 10000, "points": [{"x": 100, "y": 100}, {"x": 900, "y": 100}]},
        ],
    }
    anchors = design.validate_anchor_set(project, payload)
    first_path, first_hash = design.render_anchor_overlay(project, anchors)
    second_path, second_hash = design.render_anchor_overlay(project, anchors)
    assert first_path == second_path and first_hash == second_hash
    assert anchors["anchors"][0]["label"] == "主卧"
    with Image.open(first_path) as overlay:
        with Image.open(project["normalized_path"]) as source:
            assert overlay.width > source.width and overlay.height == source.height


def test_anchor_route_invalidates_old_candidates_and_requires_entrance(design_store):
    project = design.create_project(str(design_store), "plan.png")
    project["candidates"] = [{"candidate_id": "old", "stale": False}]
    design.save_project(project)
    bad = routes.AnchorSetRequest(
        base_revision=project["revision"], source_hash=project["source_hash"], confirmed_complete=True,
        anchors=[routes.AnchorRequest(anchor_id="P01", kind="space", label="客厅", points=[routes.AnchorPointRequest(x=300, y=400)])],
    )
    with pytest.raises(HTTPException) as exc:
        routes.save_design_anchors(project["project_id"], bad)
    assert exc.value.status_code == 422

    good = routes.AnchorSetRequest(
        base_revision=project["revision"], source_hash=project["source_hash"], confirmed_complete=True,
        anchors=[
            routes.AnchorRequest(anchor_id="P01", kind="space", label="客厅", points=[routes.AnchorPointRequest(x=300, y=400)]),
            routes.AnchorRequest(anchor_id="P02", kind="entrance", label="入户门", points=[routes.AnchorPointRequest(x=120, y=800)]),
            routes.AnchorRequest(anchor_id="P03", kind="scale", label="总宽 10m", distance_mm=10000, points=[routes.AnchorPointRequest(x=100, y=100), routes.AnchorPointRequest(x=900, y=100)]),
        ],
    )
    result = routes.save_design_anchors(project["project_id"], good)
    assert result["anchor_set"]["confirmed_complete"] is True
    assert result["candidates"][0]["stale"] is True
    assert result["anchor_overlay_hash"]
    stored = design.load_project(project["project_id"])
    assert stored and os.path.isfile(stored["anchor_overlay_path"])


def test_prompt_makes_source_structural_authority(design_store):
    project = confirmed_project(design_store)
    prompt = design.build_design_prompt(project, phase="draft", direction_index=2)
    assert "Image 1 is always the structural authority" in prompt
    assert "Strict vertical overhead orthographic view" in prompt
    assert "No labels" in prompt
    assert "现代暖木自然风" in prompt


def test_room_normalization_drops_unknown_adjacency_without_inventing_rooms():
    rooms = design.normalize_plan_rooms([{
        "id": "Bedroom 1", "label": "卧室", "room_type": "bedroom",
        "coarse_location": "north", "adjacent_room_ids": ["hallway"],
        "confidence": 0.8, "evidence": "door to corridor", "needs_confirmation": False,
    }])
    assert [room["id"] for room in rooms] == ["bedroom_1"]
    assert rooms[0]["adjacent_room_ids"] == []


def test_automatic_summary_prefills_evidence_confidence_and_title_facts(design_store, monkeypatch):
    project = design.create_project(str(design_store), "plan.png")
    anchored = confirmed_project(design_store)
    project["anchor_set"] = anchored["anchor_set"]
    project["anchor_overlay_path"] = anchored["anchor_overlay_path"]
    project["anchor_overlay_hash"] = anchored["anchor_overlay_hash"]
    design.save_project(project)
    payload = {
        "room_count": 2,
        "rooms": [
            {"id": "living", "label": "客厅", "room_type": "living", "coarse_location": "left",
             "adjacent_room_ids": ["bed"], "anchor_ids": ["P01"], "source": "human_anchor", "confidence": 0.92, "evidence": "large central space", "needs_confirmation": False},
            {"id": "bed", "label": "卧室", "room_type": "bedroom", "coarse_location": "right",
             "adjacent_room_ids": ["living"], "anchor_ids": ["P02"], "source": "human_anchor", "confidence": 0.7, "evidence": "bay window and AC", "needs_confirmation": True},
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
        "verification": {"status": "verified", "conflicts": [], "changes": [], "inferred_anchor_gaps": []},
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
    monkeypatch.setattr(routes, "load_config", lambda: {"gemini_api_key": "configured"})
    preview = routes._create_preview(project)
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


def test_lock_accepts_2k_draft_after_both_reviews(design_store):
    project = confirmed_project(design_store)
    concept_path = design._save_candidate_image(
        project["project_id"], "draft_good", Image.new("RGB", (2000, 1500), (220, 210, 195)))
    candidate = {
        "candidate_id": "draft_good", "phase": "draft", "status": "done", "stale": False,
        "path": concept_path, "image_size": [2000, 1500], "resolution": "2K",
        "source_hash": project["source_hash"],
        "generation_hash": project["generation_hash"],
        "brief_hash": project["brief_hash"], "result_hash": design.file_sha256(concept_path),
        "structure_qa": {"status": "passed", "hard_fail": False, "checks": []},
        "human_review": {"status": "passed", "checks": {item: True for item in design.STRUCTURE_REVIEW_ITEMS}},
    }
    project["candidates"] = [candidate]
    design.save_project(project)
    result = routes.lock_design_candidate(
        project["project_id"], "draft_good", routes.CandidateActionRequest(base_revision=project["revision"]))
    assert result["status"] == "locked"
    assert result["locked_candidate_id"] == "draft_good"


def test_modeling_bundle_has_authority_and_no_secrets(design_store):
    project = confirmed_project(design_store)
    concept_path = design._save_candidate_image(
        project["project_id"], "draft", Image.new("RGB", (2000, 1500), "white"))
    candidate = {
        "candidate_id": "draft", "phase": "draft", "resolution": "2K", "path": concept_path,
        "result_hash": design.file_sha256(concept_path), "prompt_version": design.PROMPT_VERSION,
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
        assert "design/locked-concept-2k.png" in names
        assert "prompts/concept-prompt-snapshot.json" in names
        assert "design/final-locked.png" not in names
        assert "prompts/refine-prompt-snapshot.json" not in names
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["target_profile"] == "blender-mcp-v1"
        assert manifest["blocked_when_scale_missing"] is True
        assert manifest["appearance_authority"][0] == "design/locked-concept-2k.png"
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
    assert public["plan_summary"]["source"] == "human"

    listed = design.public_project(project, list_mode=True)
    assert "plan_summary" not in listed
    assert "brief" not in listed


def _two_room_structure_seed():
    return {
        "outer_boundary": [{"x": 100, "y": 100}, {"x": 900, "y": 100}, {"x": 900, "y": 800}, {"x": 100, "y": 800}],
        "walls": [
            {"id": "W1", "a": {"x": 100, "y": 100}, "b": {"x": 900, "y": 100}, "thickness_m": .2, "height_m": 2.8, "left_space_id": "exterior", "right_space_id": "bed", "confidence": .95, "evidence": "top wall"},
            {"id": "W2", "a": {"x": 900, "y": 100}, "b": {"x": 900, "y": 800}, "thickness_m": .2, "height_m": 2.8, "left_space_id": "exterior", "right_space_id": "bed", "confidence": .95, "evidence": "right wall"},
            {"id": "W3", "a": {"x": 900, "y": 800}, "b": {"x": 100, "y": 800}, "thickness_m": .2, "height_m": 2.8, "left_space_id": "exterior", "right_space_id": "living", "confidence": .95, "evidence": "bottom wall"},
            {"id": "W4", "a": {"x": 100, "y": 800}, "b": {"x": 100, "y": 100}, "thickness_m": .2, "height_m": 2.8, "left_space_id": "exterior", "right_space_id": "living", "confidence": .95, "evidence": "left wall"},
            {"id": "W5", "a": {"x": 500, "y": 100}, "b": {"x": 500, "y": 800}, "thickness_m": .12, "height_m": 2.8, "left_space_id": "living", "right_space_id": "bed", "confidence": .9, "evidence": "partition"},
        ],
        "openings": [
            {"id": "O1", "kind": "entrance", "a": {"x": 200, "y": 800}, "b": {"x": 280, "y": 800}, "owning_wall_id": "W3", "sill_m": 0, "head_m": 2.1, "side_a_space_id": "exterior", "side_b_space_id": "living", "swing_direction": "not_shown", "confidence": .9, "evidence": "entry"},
            {"id": "O2", "kind": "door", "a": {"x": 500, "y": 430}, "b": {"x": 500, "y": 500}, "owning_wall_id": "W5", "sill_m": 0, "head_m": 2.1, "side_a_space_id": "living", "side_b_space_id": "bed", "swing_direction": "not_shown", "confidence": .9, "evidence": "room door"},
            {"id": "O3", "kind": "window", "a": {"x": 650, "y": 100}, "b": {"x": 760, "y": 100}, "owning_wall_id": "W1", "sill_m": .9, "head_m": 2.1, "side_a_space_id": "exterior", "side_b_space_id": "bed", "swing_direction": "none", "confidence": .9, "evidence": "window"},
        ],
        "adjacencies": [
            {"id": "A1", "space_a_id": "exterior", "space_b_id": "living", "kind": "door", "opening_id": "O1", "confidence": .9},
            {"id": "A2", "space_a_id": "living", "space_b_id": "bed", "kind": "door", "opening_id": "O2", "confidence": .9},
        ],
        "unresolved": [],
    }


def test_structure_review_compiles_metric_bundle_from_human_scale(design_store):
    project = confirmed_project(design_store)
    review = design.prepare_structure_review(project, payload_override=_two_room_structure_seed())
    assert review["status"] == "needs_answers"
    answers = {
        "Q01_ANNOTATIONS": "annotations", "Q02_PARALLEL_LINES": "floor_feature",
        "Q03_GLAZING": "mostly_openings", "Q04_ENTRANCE": "yes",
        "Q05_LOW_FEATURES": "not_walls", "Q06_BALCONIES": "connected",
        "Q07_MISSING_OPENINGS": "none", "Q08_ROOM_LIST": "complete", "Q09_READY": "yes",
    }
    completed = design.submit_structure_review(project, answers)
    assert completed["status"] == "verified"
    bundle = completed["structure_bundle"]
    assert bundle["schema"] == "research-structure-bundle-v1"
    assert len(bundle["wall_branch_graph"]["walls"]) == 7
    assert all(junction["kind"] != "X" or len(junction["incident_wall_ids"]) >= 4 for junction in bundle["wall_branch_graph"]["junctions"])
    assert len(bundle["opening_contract"]["openings"]) == 3
    assert bundle["structure_hash"] == completed["structure_hash"]
    first = design.create_model_run_record(project)
    second = design.create_model_run_record(project)
    assert first["run_id"] == second["run_id"]


def test_structure_review_keeps_external_failure_out_of_product_failure(design_store, monkeypatch):
    project = confirmed_project(design_store)
    monkeypatch.setattr(design, "call_gemini_json", lambda *args, **kwargs: (None, "Gemini HTTP 400: region"))
    review = design.prepare_structure_review(project)
    assert review["status"] == "external_review_pending"
    assert review["provider"] == "gemini_unavailable"
    assert len(review["questions"]) == 9
    assert project["status"] == "ready"


def test_invalid_structure_contract_becomes_professional_review_not_product_failure(design_store):
    project = confirmed_project(design_store)
    seed = _two_room_structure_seed()
    seed["openings"][0]["owning_wall_id"] = "missing-wall"
    design.prepare_structure_review(project, payload_override=seed)
    answers = {
        "Q01_ANNOTATIONS": "annotations", "Q02_PARALLEL_LINES": "floor_feature",
        "Q03_GLAZING": "mostly_openings", "Q04_ENTRANCE": "yes",
        "Q05_LOW_FEATURES": "not_walls", "Q06_BALCONIES": "connected",
        "Q07_MISSING_OPENINGS": "none", "Q08_ROOM_LIST": "complete", "Q09_READY": "yes",
    }
    review = design.submit_structure_review(project, answers)
    assert review["status"] == "needs_professional_review"
    assert review["structure_hash"] == ""
    assert any("所属墙" in item for item in review["unresolved"])
    assert project["status"] == "ready"


def test_professional_bundle_is_bound_to_current_project_source_and_revision(design_store):
    first = confirmed_project(design_store)
    design.prepare_structure_review(first, payload_override=_two_room_structure_seed())
    answers = {
        "Q01_ANNOTATIONS": "annotations", "Q02_PARALLEL_LINES": "floor_feature",
        "Q03_GLAZING": "mostly_openings", "Q04_ENTRANCE": "yes",
        "Q05_LOW_FEATURES": "not_walls", "Q06_BALCONIES": "connected",
        "Q07_MISSING_OPENINGS": "none", "Q08_ROOM_LIST": "complete", "Q09_READY": "yes",
    }
    verified = design.submit_structure_review(first, answers)
    foreign_bundle = json.loads(json.dumps(verified["structure_bundle"]))
    second = confirmed_project(design_store)
    design.prepare_structure_review(second, payload_override=_two_room_structure_seed())
    result = design.submit_structure_review(second, answers, technical_bundle=foreign_bundle)
    assert result["status"] == "needs_professional_review"
    assert result["structure_hash"] == ""
    assert any("当前项目" in item for item in result["unresolved"])


@pytest.mark.parametrize("field,value", [
    ("raw_pixel_hash", "f" * 64),
    ("exif_orientation", 8),
    ("orientation_policy", "ignore_invalid_exif_user_confirmed_raw"),
    ("canonical_visible_size", [1, 1]),
    ("normalized_hash", "e" * 64),
])
def test_professional_bundle_binds_every_source_orientation_field(design_store, field, value):
    project = confirmed_project(design_store)
    design.prepare_structure_review(project, payload_override=_two_room_structure_seed())
    answers = {
        "Q01_ANNOTATIONS": "annotations", "Q02_PARALLEL_LINES": "floor_feature",
        "Q03_GLAZING": "mostly_openings", "Q04_ENTRANCE": "yes",
        "Q05_LOW_FEATURES": "not_walls", "Q06_BALCONIES": "connected",
        "Q07_MISSING_OPENINGS": "none", "Q08_ROOM_LIST": "complete", "Q09_READY": "yes",
    }
    verified = design.submit_structure_review(project, answers)
    assert verified["status"] == "verified"
    tampered = deepcopy(verified["structure_bundle"])
    tampered["source"][field] = value
    result = design.submit_structure_review(project, answers, technical_bundle=tampered)
    assert result["status"] == "needs_professional_review"
    assert any("来源方向/像素证据" in item or "规范化证据图" in item for item in result["unresolved"])


def test_professional_bundle_cannot_rewrite_current_human_anchor_geometry(design_store):
    project = confirmed_project(design_store)
    design.prepare_structure_review(project, payload_override=_two_room_structure_seed())
    answers = {
        "Q01_ANNOTATIONS": "annotations", "Q02_PARALLEL_LINES": "floor_feature",
        "Q03_GLAZING": "mostly_openings", "Q04_ENTRANCE": "yes",
        "Q05_LOW_FEATURES": "not_walls", "Q06_BALCONIES": "connected",
        "Q07_MISSING_OPENINGS": "none", "Q08_ROOM_LIST": "complete", "Q09_READY": "yes",
    }
    verified = design.submit_structure_review(project, answers)
    bundle = deepcopy(verified["structure_bundle"])
    source_anchor = next(anchor for anchor in bundle["source"]["anchors"] if anchor["anchor_id"] == "P03")
    source_anchor["points_norm"] = [[400, 800]]
    matrix = bundle["source"]["normalized_to_metric_3x3"]
    source_anchor["points_metric_m"] = [[matrix[0][0] * 400 + matrix[0][1] * 800 + matrix[0][2], matrix[1][0] * 400 + matrix[1][1] * 800 + matrix[1][2]]]
    bundle["source"]["anchor_set_hash"] = compute_anchor_set_hash(bundle["source"]["coordinate_space"], bundle["source_hash"], bundle["source"]["normalized_hash"], bundle["source"]["anchors"])
    opening = next(item for item in bundle["opening_contract"]["openings"] if item["id"] == "O1")
    opening["segment_m"] = [[3.25, 0.0], [4.25, 0.0]]
    opening["width_m"] = 1.0
    opening["jamb_before_support"].update(face_distance_m=3.25, effective_support_m=3.25)
    opening["jamb_after_support"].update(face_distance_m=0.75, effective_support_m=0.75)
    bundle["structure_hash"] = compute_structure_hash(bundle)
    assert next(anchor for anchor in project["anchor_set"]["anchors"] if anchor["anchor_id"] == "P03")["points"] == [{"x": 240, "y": 800}]
    result = design.submit_structure_review(project, answers, technical_bundle=bundle)
    assert result["status"] == "needs_professional_review"
    assert any("人工锚点几何" in item for item in result["unresolved"])


def test_legacy_project_lazily_recomputes_anchor_hash_from_project_truth(design_store):
    project = confirmed_project(design_store)
    expected = project["anchor_set"].pop("anchor_set_hash")
    design.save_project(project)
    loaded = design.load_project(project["project_id"])
    assert loaded["anchor_set"]["anchor_set_hash"] == expected


def test_gemini_entrance_must_geometrically_match_human_anchor(design_store):
    project = confirmed_project(design_store)
    seed = _two_room_structure_seed()
    seed["openings"][0]["a"] = {"x": 800, "y": 200}
    seed["openings"][0]["b"] = {"x": 860, "y": 200}
    design.prepare_structure_review(project, payload_override=seed)
    answers = {
        "Q01_ANNOTATIONS": "annotations", "Q02_PARALLEL_LINES": "floor_feature",
        "Q03_GLAZING": "mostly_openings", "Q04_ENTRANCE": "yes",
        "Q05_LOW_FEATURES": "not_walls", "Q06_BALCONIES": "connected",
        "Q07_MISSING_OPENINGS": "none", "Q08_ROOM_LIST": "complete", "Q09_READY": "yes",
    }
    result = design.submit_structure_review(project, answers)
    assert result["status"] == "needs_professional_review"
    assert any("人工entrance锚点" in item for item in result["unresolved"])


def test_two_point_human_opening_rejects_ai_segment_with_wrong_length(design_store):
    project = confirmed_project(design_store)
    entrance = next(anchor for anchor in project["anchor_set"]["anchors"] if anchor["kind"] == "entrance")
    entrance["points"] = [{"x": 200, "y": 800}, {"x": 280, "y": 800}]
    seed = _two_room_structure_seed()
    seed["openings"][0]["b"] = {"x": 560, "y": 800}
    design.prepare_structure_review(project, payload_override=seed)
    answers = {
        "Q01_ANNOTATIONS": "annotations", "Q02_PARALLEL_LINES": "floor_feature",
        "Q03_GLAZING": "mostly_openings", "Q04_ENTRANCE": "yes",
        "Q05_LOW_FEATURES": "not_walls", "Q06_BALCONIES": "connected",
        "Q07_MISSING_OPENINGS": "none", "Q08_ROOM_LIST": "complete", "Q09_READY": "yes",
    }
    result = design.submit_structure_review(project, answers)
    assert result["status"] == "needs_professional_review"
    assert any("人工entrance锚点" in item for item in result["unresolved"])


def test_product_adapter_runs_real_blender_and_keeps_missing_gemini_as_external_wait(design_store, monkeypatch):
    project = confirmed_project(design_store)
    design.prepare_structure_review(project, payload_override=_two_room_structure_seed())
    answers = {
        "Q01_ANNOTATIONS": "annotations", "Q02_PARALLEL_LINES": "floor_feature",
        "Q03_GLAZING": "mostly_openings", "Q04_ENTRANCE": "yes",
        "Q05_LOW_FEATURES": "not_walls", "Q06_BALCONIES": "connected",
        "Q07_MISSING_OPENINGS": "none", "Q08_ROOM_LIST": "complete", "Q09_READY": "yes",
    }
    design.submit_structure_review(project, answers)
    row = design.create_model_run_record(project)
    design.save_project(project)
    monkeypatch.setattr(design, "evaluate_structure", lambda *_args, **_kwargs: {
        "version": design.QA_PROMPT_VERSION, "status": "manual_required", "hard_fail": False,
        "summary": "Gemini route unavailable", "checks": [], "provider": "gemini_unavailable",
    })
    completed = design.run_model_job(project["project_id"], row["run_id"])
    assert completed["status"] == "external_review_pending"
    artifact_kinds = {item["kind"] for item in completed["artifacts"]}
    assert {"blend", "glb", "ifc", "top", "north_east", "north_west"} <= artifact_kinds
    public = design.public_project(design.load_project(project["project_id"]))
    public_run = public["model_runs"][0]
    assert "structure_bundle" not in public_run and "output_root" not in public_run
    assert all(item["download_url"].startswith("/api/whole-home-design/") for item in public_run["artifacts"])


def test_ifc_dependency_block_cannot_be_promoted_by_gemini_pass(design_store, monkeypatch):
    from Floor_engine_server.tools import fastloop_research as kernel
    project = confirmed_project(design_store)
    design.prepare_structure_review(project, payload_override=_two_room_structure_seed())
    answers = {
        "Q01_ANNOTATIONS": "annotations", "Q02_PARALLEL_LINES": "floor_feature",
        "Q03_GLAZING": "mostly_openings", "Q04_ENTRANCE": "yes",
        "Q05_LOW_FEATURES": "not_walls", "Q06_BALCONIES": "connected",
        "Q07_MISSING_OPENINGS": "none", "Q08_ROOM_LIST": "complete", "Q09_READY": "yes",
    }
    design.submit_structure_review(project, answers)
    row = design.create_model_run_record(project)
    design.save_project(project)
    blocked_folder = design_store.parent / "blocked-ifc"
    blocked_folder.mkdir()
    top = blocked_folder / "top.png"
    Image.new("RGB", (32, 32), "white").save(top)
    monkeypatch.setattr(kernel, "run_research_model", lambda *_args, **_kwargs: {
        "status": "blocked_dependency_missing", "message": "IfcOpenShell missing",
        "artifacts": {"top.png": {"path": str(top), "bytes": top.stat().st_size, "sha256": design.file_sha256(str(top))}},
    })
    monkeypatch.setattr(design, "evaluate_structure", lambda *_args, **_kwargs: {
        "version": design.QA_PROMPT_VERSION, "status": "passed", "hard_fail": False,
        "summary": "visual pass", "checks": [], "provider": "fixture",
    })
    completed = design.run_model_job(project["project_id"], row["run_id"])
    assert completed["status"] == "blocked_dependency_missing"
    assert "IFC" in completed["stage"]


def test_recovery_hides_interrupted_local_model_run(design_store):
    project = confirmed_project(design_store)
    project["model_runs"] = [{
        "run_id": "model_interrupted", "status": "building", "stage": "building", "error": "",
        "structure_hash": "a" * 64, "artifacts": [], "unresolved": [], "stale": False,
    }]
    design.save_project(project)
    design.recover_interrupted_projects()
    stored = design.load_project(project["project_id"])
    assert stored["model_runs"][0]["status"] == "interrupted"
    assert stored["model_runs"][0]["stale"] is True


def test_structure_and_model_run_routes_form_one_revision_bound_fast_lane(design_store, monkeypatch):
    project = confirmed_project(design_store)
    monkeypatch.setattr(routes, "prepare_structure_review", lambda value: design.prepare_structure_review(value, payload_override=_two_room_structure_seed()))
    prepared = asyncio.run(routes.prepare_design_structure_review(
        project["project_id"], routes.PreviewRequest(base_revision=project["revision"])))
    assert prepared["structure_review"]["status"] == "needs_answers"
    answers = {
        "Q01_ANNOTATIONS": "annotations", "Q02_PARALLEL_LINES": "floor_feature",
        "Q03_GLAZING": "mostly_openings", "Q04_ENTRANCE": "yes",
        "Q05_LOW_FEATURES": "not_walls", "Q06_BALCONIES": "connected",
        "Q07_MISSING_OPENINGS": "none", "Q08_ROOM_LIST": "complete", "Q09_READY": "yes",
    }
    compiled = routes.save_design_structure_guidance(project["project_id"], routes.StructureGuidancePutRequest(
        base_revision=prepared["revision"], answers=answers))
    assert compiled["structure_review"]["status"] == "verified"
    captured = []
    def no_background(coro):
        captured.append(coro)
        coro.close()
        return None
    monkeypatch.setattr(routes, "_track", no_background)
    started = asyncio.run(routes.create_design_model_run(project["project_id"], routes.ModelRunCreateRequest(
        base_revision=compiled["revision"], structure_hash=compiled["structure_review"]["structure_hash"],
        idempotency_key="route-fast-lane-001")))
    assert started["model_runs"][0]["status"] == "queued"
    assert len(captured) == 1
