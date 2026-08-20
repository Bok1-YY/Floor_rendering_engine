import asyncio
import os

import pytest
import numpy as np
from fastapi import HTTPException
from PIL import Image

from Floor_engine_server import records, routes_panorama, server_helpers, server_state
from Floor_engine_server.models import (
    add_model_candidate,
    ensure_model_runs,
    new_job,
)
from Floor_engine_server.pure_render_pano import (
    PURE_RENDER_PANO_HEIGHT,
    PURE_RENDER_PANO_WIDTH,
    build_pure_render_pano_prompt,
    build_architecture_repair_mask,
    create_paid_preview,
    gate_visual_pano,
    pure_render_review_status,
    validate_paid_preview,
)
from Floor_engine_server.server_schemas import (
    PanoramaCommitRequest,
    PanoramaPaidPreviewRequest,
)
from Floor_engine_server.task_registry import TaskRegistry


def test_pure_render_prompt_replaces_perspective_contract():
    prompt = build_pure_render_pano_prompt({
        "room_type": "客餐厅一体",
        "style_type": "现代极简",
        "angle": "28mm lens (Wide)",
        "aspect_ratio": "4:3",
        "floor_tone": "暖灰橡木",
    }, source_label="Pro")

    assert "360 degrees" in prompt
    assert "3840x1920" in prompt
    assert "horizontal midpoint" in prompt
    assert "28mm" not in prompt
    assert "4:3" not in prompt
    assert "暖灰橡木" in prompt
    assert "one global flooring coordinate system" in prompt
    assert "No barrel distortion" in prompt


def test_paid_preview_binds_hash_source_and_expiry():
    row = create_paid_preview(
        job_id="job_1", action="generate", source_model="pro", source_index=2,
        source_hash="a" * 64, provider="fal", endpoint="openai/gpt-image-2/edit",
        model_id="gpt-image-2-2026-04-21", prompt="panorama", now=100.0,
    )
    validate_paid_preview(
        row, preview_hash=row["preview_hash"], job_id="job_1",
        source_hash="a" * 64, now=101.0)

    with pytest.raises(ValueError, match="hash_mismatch"):
        validate_paid_preview(
            row, preview_hash="b" * 64, job_id="job_1",
            source_hash="a" * 64, now=101.0)
    with pytest.raises(ValueError, match="source_changed"):
        validate_paid_preview(
            row, preview_hash=row["preview_hash"], job_id="job_1",
            source_hash="c" * 64, now=101.0)
    with pytest.raises(ValueError, match="expired"):
        validate_paid_preview(
            row, preview_hash=row["preview_hash"], job_id="job_1",
            source_hash="a" * 64, now=1000.1)


def test_visual_pano_gate_accepts_exact_seamless_erp(tmp_path):
    path = tmp_path / "seamless.png"
    Image.new(
        "RGB", (PURE_RENDER_PANO_WIDTH, PURE_RENDER_PANO_HEIGHT), (120, 130, 140)
    ).save(path)

    gate = gate_visual_pano(str(path))

    assert gate["status"] == "passed"
    assert gate["gate_pass"] is True
    assert gate["geometry_locked"] is False
    assert gate["delivery_scope"] == "ai_expanded_single_hotspot"
    assert gate["version"] == "visual_pano_v2"
    assert next(row for row in gate["checks"] if row["check_id"] == "architecture_views")


def test_architecture_repair_mask_protects_floor_and_wraps_yaw():
    gate = {
        "checks": [{
            "check_id": "architecture_views",
            "failure_yaws": [180, 0],
        }],
    }
    mask = np.asarray(build_architecture_repair_mask(gate, 720, 360))
    assert np.mean(mask[:180] > 0) > 0.2
    assert not np.any(mask[260:])
    assert np.any(mask[:, :5]) and np.any(mask[:, -5:])


def test_visual_pano_gate_recommends_repair_for_wrap_jump(tmp_path):
    path = tmp_path / "broken.png"
    image = Image.new(
        "RGB", (PURE_RENDER_PANO_WIDTH, PURE_RENDER_PANO_HEIGHT), (120, 130, 140)
    )
    pixels = image.load()
    for y in range(image.height):
        pixels[0, y] = (0, 0, 0)
        pixels[image.width - 1, y] = (255, 255, 255)
    image.save(path)

    gate = gate_visual_pano(str(path))

    assert gate["status"] == "repair_recommended"
    assert gate["gate_pass"] is False
    assert gate["hard_fail"] is False
    assert "wrap_seam" in gate["failures"]


def test_visual_pano_gate_hard_fails_wrong_size(tmp_path):
    path = tmp_path / "wrong.png"
    Image.new("RGB", (1024, 512), "gray").save(path)

    gate = gate_visual_pano(str(path))

    assert gate["status"] == "failed"
    assert gate["hard_fail"] is True
    assert gate["failures"] == ["size_contract"]


def test_review_acceptance_requires_gate_and_all_six_passes():
    checklist = {
        "wrap_seam": "pass",
        "horizon_and_lines": "pass",
        "object_integrity": "pass",
        "floor_and_material": "pass",
        "lighting_continuity": "pass",
        "poles": "pass",
    }
    assert pure_render_review_status({"status": "passed"}, checklist) == "accepted"
    assert pure_render_review_status(
        {"status": "repair_recommended"}, checklist) == "needs_review"
    assert pure_render_review_status(
        {"status": "passed"}, {**checklist, "poles": "uncertain"}) == "needs_review"
    assert pure_render_review_status(
        {"status": "passed"}, {**checklist, "object_integrity": "fail"}) == "rejected"


def test_model_candidate_metadata_stays_aligned():
    job = new_job("demo", "now", "pro")
    job.model_targets = ["pro"]
    ensure_model_runs(job)

    add_model_candidate(job, "vr360", "one.png", {"projection": "equirectangular"})
    add_model_candidate(job, "vr360", "two.png", {"projection": "equirectangular", "n": 2})

    run = job.model_runs["vr360"]
    assert run["paths"] == ["one.png", "two.png"]
    assert run["candidate_meta"][0]["projection"] == "equirectangular"
    assert run["candidate_meta"][1]["n"] == 2


def test_panorama_preview_and_commit_are_idempotent(tmp_path, monkeypatch):
    output_dir = tmp_path / "output_files"
    output_dir.mkdir(exist_ok=True)
    source_path = output_dir / "source.png"
    Image.new("RGB", (64, 48), "tan").save(source_path)

    registry = TaskRegistry(
        "pano-test", max_entries=10,
        is_terminal=server_state.job_is_terminal, newest_first=True)
    monkeypatch.setattr(server_state, "JOBS", registry)
    monkeypatch.setattr(routes_panorama, "MAIN_OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(server_helpers, "MAIN_OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(
        routes_panorama, "load_config",
        lambda: {"fal_api_key": "configured", "fal_gpt_image_endpoint": "openai/gpt-image-2/edit"},
    )
    monkeypatch.setattr(routes_panorama, "get_usage_prices", lambda: {})

    spawned = []

    def fake_spawn(coro):
        spawned.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(server_state, "spawn", fake_spawn)

    job = new_job("demo", "now", "pro")
    job.workflow_mode = "纯效果图 (生成全新空间)"
    job.status = "done"
    job.operation_status = "done"
    job.model_targets = ["pro"]
    ensure_model_runs(job)
    add_model_candidate(job, "pro", str(source_path))
    registry.add(job.job_id, job)

    preview = routes_panorama.preview_job_panorama(
        job.job_id,
        PanoramaPaidPreviewRequest(
            action="generate", source_model="pro", source_index=0),
    )
    request = PanoramaCommitRequest(
        preview_id=preview["preview_id"], preview_hash=preview["preview_hash"])

    first = asyncio.run(routes_panorama.commit_job_panorama(job.job_id, request))
    second = asyncio.run(routes_panorama.commit_job_panorama(job.job_id, request))

    assert first["operation"] == "panorama_generate"
    assert second["operation_status"] == "running"
    assert len(spawned) == 1
    assert job.panorama_previews[preview["preview_id"]]["status"] == "running"


def test_panorama_background_run_persists_candidate_and_record(tmp_path, monkeypatch):
    output_dir = tmp_path / "output_files"
    output_dir.mkdir(exist_ok=True)
    source_path = output_dir / "source.png"
    Image.new("RGB", (64, 48), "tan").save(source_path)
    record_path = output_dir / "demo_记录.json"
    records.save_records_file(str(record_path), [{
        "id": "record_1",
        "workflow_mode": "纯效果图 (生成全新空间)",
        "gen_context": {"params": {"room_type": "客厅", "style_type": "现代极简"}},
        "results": [{
            "result_id": "source_result_1",
            "result_image_file": "source.png",
            "model_label": "Pro",
        }],
    }])

    registry = TaskRegistry(
        "pano-test", max_entries=10,
        is_terminal=server_state.job_is_terminal, newest_first=True)
    monkeypatch.setattr(server_state, "JOBS", registry)
    monkeypatch.setattr(routes_panorama, "MAIN_OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(server_helpers, "MAIN_OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(
        routes_panorama, "load_config",
        lambda: {"fal_api_key": "configured", "fal_gpt_image_endpoint": "openai/gpt-image-2/edit"},
    )
    monkeypatch.setattr(routes_panorama, "get_usage_prices", lambda: {})
    monkeypatch.setattr(routes_panorama, "record_usage", lambda *args, **kwargs: None)

    def fake_gpt_image_edit(*args, on_submitted=None, **kwargs):
        assert kwargs["size"] == "3840x1920"
        assert kwargs["provider"] == "fal"
        if on_submitted:
            on_submitted({"request_id": "fal-request-1"})
        return Image.new(
            "RGB", (PURE_RENDER_PANO_WIDTH, PURE_RENDER_PANO_HEIGHT), (120, 130, 140)
        ), ""

    monkeypatch.setattr(routes_panorama, "call_gpt_image_edit", fake_gpt_image_edit)

    job = new_job("demo", "now", "pro")
    job.workflow_mode = "纯效果图 (生成全新空间)"
    job.status = "done"
    job.operation_status = "done"
    job.model_targets = ["pro"]
    job.json_path = str(record_path)
    job.record_id = "record_1"
    ensure_model_runs(job)
    add_model_candidate(job, "pro", str(source_path))
    registry.add(job.job_id, job)

    async def scenario():
        monkeypatch.setattr(
            server_state, "model_semaphores", {"vr360": asyncio.Semaphore(1)})
        tasks = []

        def spawn(coro):
            task = asyncio.create_task(coro)
            tasks.append(task)
            return task

        monkeypatch.setattr(server_state, "spawn", spawn)
        preview = routes_panorama.preview_job_panorama(
            job.job_id,
            PanoramaPaidPreviewRequest(
                action="generate", source_model="pro", source_index=0),
        )
        await routes_panorama.commit_job_panorama(
            job.job_id,
            PanoramaCommitRequest(
                preview_id=preview["preview_id"], preview_hash=preview["preview_hash"]),
        )
        await asyncio.gather(*tasks)

    asyncio.run(scenario())

    vr_run = job.model_runs["vr360"]
    assert vr_run["status"] == "done"
    assert len(vr_run["paths"]) == 1
    assert os.path.isfile(vr_run["paths"][0])
    panorama = vr_run["candidate_meta"][0]["panorama"]
    assert panorama["projection"] == "equirectangular"
    # The mocked ERP is a uniform image unrelated to the source.  V2 keeps the
    # candidate but correctly requires local geometry calibration instead of
    # claiming a false source lock.
    assert panorama["gate"]["status"] == "repair_recommended"
    assert "source_geometry_registration" in panorama["gate"]["failures"]
    assert panorama["source_sha256"]

    saved_record = records.load_records_file(str(record_path))[0]
    assert len(saved_record["results"]) == 2
    saved_pano = saved_record["results"][1]
    assert saved_pano["source_result_id"] == "source_result_1"
    assert saved_pano["generation_metadata"]["panorama"]["gate"]["status"] == "repair_recommended"
    assert saved_pano["result_image_file"].endswith(".png")


def test_panorama_preview_rejects_non_pure_workflow(tmp_path, monkeypatch):
    output_dir = tmp_path / "output_files"
    output_dir.mkdir(exist_ok=True)
    source_path = output_dir / "source.png"
    Image.new("RGB", (32, 32), "tan").save(source_path)
    registry = TaskRegistry(
        "pano-test", max_entries=10,
        is_terminal=server_state.job_is_terminal, newest_first=True)
    monkeypatch.setattr(server_state, "JOBS", registry)
    job = new_job("demo", "now", "pro")
    job.workflow_mode = "地板替换"
    job.status = "done"
    job.model_targets = ["pro"]
    ensure_model_runs(job)
    add_model_candidate(job, "pro", str(source_path))
    registry.add(job.job_id, job)

    with pytest.raises(HTTPException) as exc:
        routes_panorama.preview_job_panorama(
            job.job_id,
            PanoramaPaidPreviewRequest(
                action="generate", source_model="pro", source_index=0),
        )
    assert exc.value.status_code == 422
