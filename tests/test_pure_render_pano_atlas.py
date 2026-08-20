import asyncio
import hashlib
import os

import numpy as np
import pytest
from PIL import Image

from Floor_engine_server import routes_panorama_direct, server_state
from Floor_engine_server.models import add_model_candidate, ensure_model_runs, new_job
from Floor_engine_server.panorama_local_geometry import build_geometry_contract
from Floor_engine_server.pure_render_pano_atlas import (
    DIRECT_ATLAS_HEIGHT,
    DIRECT_ATLAS_WIDTH,
    DIRECT_FACE_SIZE,
    DIRECT_PANO_ROUTE,
    build_atlas_template,
    build_cube_boundary_repair_mask,
    build_room_geometry_faces,
    build_room_geometry_guide,
    build_room_geometry_guide_erp,
    create_direct_paid_preview,
    cube_to_erp_chunked,
    gate_atlas_faces,
    register_and_split_atlas,
    validate_direct_paid_preview,
)
from Floor_engine_server.whole_home_pano_render import cube_to_erp
from Floor_engine_server.server_schemas import (
    DirectPanoramaCommitRequest,
    DirectPanoramaPreviewRequest,
    GenParams,
)
from Floor_engine_server.task_registry import TaskRegistry


def test_direct_atlas_template_contract():
    template, mask = build_atlas_template()

    assert template.size == (DIRECT_ATLAS_WIDTH, DIRECT_ATLAS_HEIGHT)
    assert mask.size == template.size
    assert mask.mode == "L"
    # Cell interiors are editable while the registration rails stay protected.
    assert mask.getpixel((DIRECT_FACE_SIZE // 2, DIRECT_FACE_SIZE // 2)) == 255
    assert mask.getpixel((0, 0)) == 0
    assert mask.getpixel((DIRECT_FACE_SIZE, DIRECT_FACE_SIZE // 2)) == 0


def test_room_geometry_guides_share_one_level_cuboid_contract():
    faces = build_room_geometry_faces(256)
    atlas = build_room_geometry_guide(256)
    erp = build_room_geometry_guide_erp(768, 384)

    assert list(faces) == ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]
    assert all(image.size == (256, 256) for image in faces.values())
    assert atlas.size == (768, 512)
    assert erp.size == (768, 384)
    # Down face is the warm floor proxy; up face is the neutral ceiling proxy.
    down = np.asarray(faces["-Y"], dtype=np.float32).mean(axis=(0, 1))
    up = np.asarray(faces["+Y"], dtype=np.float32).mean(axis=(0, 1))
    assert down[0] > down[2]
    assert abs(float(up[0] - up[2])) < abs(float(down[0] - down[2]))


def test_registers_provider_4k_3_by_2_variant_and_splits_six_faces():
    # Nano Banana's official 4K 3:2 size is not an exact 3N x 2N grid.
    atlas = Image.new("RGB", (5056, 3392), (80, 90, 100))
    faces, manifest = register_and_split_atlas(atlas)

    assert list(faces) == ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]
    assert all(face.size == (DIRECT_FACE_SIZE, DIRECT_FACE_SIZE) for face in faces.values())
    assert manifest["source_size"] == {"width": 5056, "height": 3392}
    assert manifest["registration_mode"] == "centered_ratio_crop"
    assert manifest["layout"] == [["+X", "-X", "+Y"], ["-Y", "+Z", "-Z"]]


def test_atlas_gate_hard_fails_duplicated_faces():
    axis = np.linspace(20, 230, DIRECT_FACE_SIZE, dtype=np.uint8)
    gradient = np.broadcast_to(axis[None, :, None], (DIRECT_FACE_SIZE, DIRECT_FACE_SIZE, 3)).copy()
    repeated = Image.fromarray(gradient, mode="RGB")
    faces = {face: repeated.copy() for face in ("+X", "-X", "+Y", "-Y", "+Z", "-Z")}

    gate = gate_atlas_faces(faces)

    assert gate["status"] == "failed"
    assert gate["hard_fail"] is True
    assert "duplicate_faces" in gate["failures"]


def _textured_face(base, seed):
    rng = np.random.default_rng(seed)
    arr = np.full((256, 256, 3), base, dtype=np.int16)
    arr += rng.integers(-18, 19, size=arr.shape, dtype=np.int16)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def test_atlas_gate_rejects_floor_texture_in_wrong_axis_face():
    floor = _textured_face((176, 132, 88), 1)
    faces = {
        "+X": _textured_face((100, 130, 160), 2),
        "-X": _textured_face((145, 150, 155), 3),
        "+Y": _textured_face((218, 215, 205), 4),
        "-Y": _textured_face((125, 150, 170), 5),
        "+Z": _textured_face((130, 165, 130), 6),
        "-Z": floor.copy(),
    }

    gate = gate_atlas_faces(faces, floor_reference=floor)

    assert gate["hard_fail"] is True
    assert "axis_floor_face_semantics" in gate["failures"]
    check = next(row for row in gate["checks"] if row["check_id"] == "axis_floor_face_semantics")
    assert check["detected_floor_face"] == "-Z"


def test_atlas_gate_accepts_floor_texture_in_minus_y_face():
    floor = _textured_face((176, 132, 88), 7)
    faces = {
        "+X": _textured_face((100, 130, 160), 8),
        "-X": _textured_face((145, 150, 155), 9),
        "+Y": _textured_face((218, 215, 205), 10),
        "-Y": floor.copy(),
        "+Z": _textured_face((130, 165, 130), 11),
        "-Z": _textured_face((160, 130, 150), 12),
    }

    gate = gate_atlas_faces(faces, floor_reference=floor)
    check = next(row for row in gate["checks"] if row["check_id"] == "axis_floor_face_semantics")
    assert check["status"] == "pass"
    assert check["detected_floor_face"] == "-Y"


def test_cube_boundary_repair_mask_covers_wrap_and_internal_edges():
    mask = np.asarray(build_cube_boundary_repair_mask(768, 384, band_px=4))

    assert mask.shape == (384, 768)
    assert np.all(mask[:, 0] == 255)
    assert np.all(mask[:, -1] == 255)
    assert 0 < float((mask > 0).mean()) < .35
    # Six-face boundaries must add masked pixels away from the longitude seam.
    assert np.any(mask[:, 100:-100] > 0)


def test_chunked_projection_matches_project_cube_transform():
    colours = {
        "+X": (255, 0, 0), "-X": (0, 255, 0), "+Y": (0, 0, 255),
        "-Y": (255, 255, 0), "+Z": (255, 0, 255), "-Z": (0, 255, 255),
    }
    faces = {face: Image.new("RGB", (32, 32), colour) for face, colour in colours.items()}

    expected = np.asarray(cube_to_erp(faces, 160, 80), dtype=np.int16)
    actual = np.asarray(cube_to_erp_chunked(faces, 160, 80, chunk_rows=7), dtype=np.int16)

    assert np.max(np.abs(expected - actual)) <= 1


def test_direct_paid_preview_binds_params_source_and_prompts(tmp_path):
    source = tmp_path / "floor.png"
    Image.new("RGB", (32, 32), "tan").save(source)
    engines = [{
        "key": "b2_atlas", "label": "B2", "provider": "fal",
        "endpoint": "fal-ai/nano-banana-2/edit", "model_id": "b2",
    }, {
        "key": "gpt_atlas", "label": "GPT", "provider": "fal",
        "endpoint": "openai/gpt-image-2/edit", "model_id": "gpt-image-2",
    }]
    row = create_direct_paid_preview(
        source_path=str(source), source_hash="a" * 64,
        params={"workflow_mode": "球面效果图", "room_type": "客厅"},
        engines=engines, estimated_costs={}, now=100.0)
    validate_direct_paid_preview(
        row, preview_hash=row["preview_hash"], source_hash="a" * 64, now=101.0)

    row["params"]["room_type"] = "卧室"
    with pytest.raises(ValueError, match="tampered"):
        validate_direct_paid_preview(
            row, preview_hash=row["preview_hash"], source_hash="a" * 64, now=101.0)


def test_direct_preview_binds_optional_room_reference_geometry(tmp_path):
    floor = tmp_path / "floor.png"
    room = tmp_path / "room.png"
    Image.new("RGB", (32, 32), "tan").save(floor)
    room_image = Image.new("RGB", (640, 360), "white")
    pixels = np.asarray(room_image).copy()
    pixels[:, 100:105] = 0
    pixels[:, 520:525] = 0
    pixels[180:185] = 0
    Image.fromarray(pixels).save(room)
    params = {"workflow_mode": "球面效果图", "room_type": "客厅", "angle": "35mm lens"}
    contract = build_geometry_contract(str(room), params, reference_role="direct_room_reference")
    reference_hash = hashlib.sha256(room.read_bytes()).hexdigest()
    engines = [{
        "key": "b2_atlas", "label": "B2", "provider": "fal",
        "endpoint": "fal-ai/nano-banana-2/edit", "model_id": "b2",
    }]
    row = create_direct_paid_preview(
        source_path=str(floor), source_hash="a" * 64, params=params,
        engines=engines, estimated_costs={}, geometry_contract=contract,
        room_reference_path=str(room), room_reference_hash=reference_hash, now=100.0)

    validate_direct_paid_preview(
        row, preview_hash=row["preview_hash"], source_hash="a" * 64, now=101.0)
    assert row["geometry_contract_hash"] == contract["contract_hash"]
    assert "authoritative +Z/front-view geometry anchor" in row["prompts"]["b2_atlas"]

    Image.new("RGB", (640, 360), "black").save(room)
    with pytest.raises(ValueError, match="tampered"):
        validate_direct_paid_preview(
            row, preview_hash=row["preview_hash"], source_hash="a" * 64, now=101.0)


def test_direct_preview_commit_is_idempotent(tmp_path, monkeypatch):
    source = tmp_path / "floor.png"
    Image.new("RGB", (32, 32), "tan").save(source)
    registry = TaskRegistry(
        "direct-pano-test", max_entries=10,
        is_terminal=server_state.job_is_terminal, newest_first=True)
    monkeypatch.setattr(server_state, "JOBS", registry)
    monkeypatch.setattr(
        routes_panorama_direct, "require_upload_image_path",
        lambda path, _label, required=False: str(source))
    monkeypatch.setattr(
        routes_panorama_direct, "load_config",
        lambda: {"fal_api_key": "configured"})
    monkeypatch.setattr(routes_panorama_direct, "get_usage_prices", lambda: {})
    with routes_panorama_direct._PREVIEW_LOCK:
        routes_panorama_direct._PREVIEWS.clear()

    spawned = []

    def fake_spawn(coro):
        spawned.append(coro)
        coro.close()

    monkeypatch.setattr(server_state, "spawn", fake_spawn)
    preview = routes_panorama_direct.preview_direct_panorama(
        DirectPanoramaPreviewRequest(
            image_path=str(source),
            params=GenParams(workflow_mode="球面效果图 (六面图集直出 VR)"),
        ))
    commit = DirectPanoramaCommitRequest(
        preview_id=preview["preview_id"], preview_hash=preview["preview_hash"])

    first = asyncio.run(routes_panorama_direct.commit_direct_panorama(commit))
    second = asyncio.run(routes_panorama_direct.commit_direct_panorama(commit))

    assert first["delivery_mode"] == DIRECT_PANO_ROUTE
    assert first["model_targets"] == ["vr360"]
    assert second["operation_status"] == "running"
    assert len(spawned) == 1


def test_direct_paid_preview_exposes_hashed_manufacturer_film_contract(tmp_path, monkeypatch):
    source = tmp_path / "floor.png"
    film = tmp_path / "film.png"
    Image.new("RGB", (96, 64), "tan").save(source)
    yy, xx = np.mgrid[:189, :98]
    film_pixels = np.stack((
        150 + 15 * np.sin(xx / 5),
        120 + 10 * np.sin(xx / 5),
        90 + 7 * np.sin(xx / 5),
    ), axis=-1)
    Image.fromarray(np.clip(film_pixels, 0, 255).astype(np.uint8), "RGB").save(film)
    monkeypatch.setattr(
        routes_panorama_direct, "require_upload_image_path",
        lambda path, _label, required=False: str(path or source))
    monkeypatch.setattr(
        routes_panorama_direct, "load_config",
        lambda: {"fal_api_key": "configured"})
    monkeypatch.setattr(routes_panorama_direct, "get_usage_prices", lambda: {})
    with routes_panorama_direct._PREVIEW_LOCK:
        routes_panorama_direct._PREVIEWS.clear()

    preview = routes_panorama_direct.preview_direct_panorama(
        DirectPanoramaPreviewRequest(
            image_path=str(source),
            params=GenParams(
                workflow_mode="球面效果图 (六面图集直出 VR)",
                floor_size="长窄板直拼：1900 x 136 mm",
                film_path=str(film), film_width_mm=984,
                film_repeat_length_mm=1890,
            ),
        ))

    contract = preview["film_contract"]
    assert contract["manifest"]["status"] == "ready"
    assert contract["manifest"]["slitting"]["lane_count"] == 7
    assert contract["manifest"]["effective_board_states"] == 1323
    assert contract["guide_b64"]
    assert len(contract["manifest"]["manifest_hash"]) == 64


def test_direct_worker_keeps_two_engine_candidates(tmp_path, monkeypatch):
    registry = TaskRegistry(
        "direct-worker-test", max_entries=10,
        is_terminal=server_state.job_is_terminal, newest_first=True)
    monkeypatch.setattr(server_state, "JOBS", registry)
    monkeypatch.setattr(server_state, "model_semaphores", {"vr360": asyncio.Semaphore(1)})
    monkeypatch.setattr(routes_panorama_direct, "MAIN_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(routes_panorama_direct, "save_api_result_png",
                        lambda *_args, **_kwargs: str(tmp_path / "template.png"))

    job = new_job("direct", "now", "custom")
    job.workflow_mode = "球面效果图 (六面图集直出 VR)"
    job.delivery_mode = DIRECT_PANO_ROUTE
    job.model_targets = ["vr360"]
    ensure_model_runs(job)
    preview_id = "vrdirect_test"
    preview = {
        "preview_id": preview_id,
        "source_path": str(tmp_path / "floor.png"),
        "engines": [
            {"key": "b2_atlas", "label": "B2"},
            {"key": "gpt_atlas", "label": "GPT Image 2"},
        ],
        "branches": {
            "b2_atlas": {"status": "queued"},
            "gpt_atlas": {"status": "queued"},
        },
    }
    job.panorama_previews = {preview_id: preview}
    registry.add(job.job_id, job)
    Image.new("RGB", (16, 16), "tan").save(preview["source_path"])

    async def fake_prepare(target_job, _preview):
        target_job.json_path = str(tmp_path / "record.json")
        target_job.record_id = "record"
        target_job.png_path = preview["source_path"]
        return target_job.json_path, target_job.record_id, target_job.png_path

    async def fake_branch(target_job, row, engine, *_args, **_kwargs):
        path = str(tmp_path / f"{engine['key']}.png")
        Image.new("RGB", (32, 16), engine["key"] == "b2_atlas" and "red" or "blue").save(path)
        index = add_model_candidate(target_job, "vr360", path, {
            "projection": "equirectangular",
            "engine_label": engine["label"],
            "panorama": {"projection": "equirectangular", "engine_label": engine["label"]},
        })
        row["branches"][engine["key"]].update(
            status="succeeded", candidate_index=index, gate_status="passed")
        return {"candidate_index": index, "gate_status": "passed"}

    monkeypatch.setattr(routes_panorama_direct, "_prepare_job_record", fake_prepare)
    monkeypatch.setattr(routes_panorama_direct, "_process_direct_branch", fake_branch)

    asyncio.run(routes_panorama_direct._run_direct_panorama_bg(job, preview_id, resume=False))

    assert job.status == "done"
    assert job.operation_status == "done"
    assert len(job.model_runs["vr360"]["paths"]) == 2
    assert preview["status"] == "succeeded"
