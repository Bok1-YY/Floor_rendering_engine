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


def test_prompt_makes_source_structural_authority(design_store):
    project = confirmed_project(design_store)
    prompt = design.build_design_prompt(project, phase="draft", direction_index=2)
    assert "Image 1 is always the structural authority" in prompt
    assert "Strict vertical overhead orthographic view" in prompt
    assert "No labels" in prompt
    assert "现代暖木自然风" in prompt


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


def test_lock_requires_final_4k_and_both_reviews(design_store):
    project = confirmed_project(design_store)
    final_path = design._save_candidate_image(
        project["project_id"], "final_good", Image.new("RGB", (3600, 2700), (220, 210, 195)))
    candidate = {
        "candidate_id": "final_good", "phase": "final", "status": "done", "stale": False,
        "path": final_path, "image_size": [3600, 2700], "source_hash": project["source_hash"],
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
