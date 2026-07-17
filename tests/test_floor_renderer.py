import asyncio
import base64
import io
import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from Floor_engine_server.floor_renderer import (
    RenderRecipe,
    render_floor,
    texture_quality_warnings,
    validate_calibration_quad,
)
from Floor_engine_server import records, server_api


QUAD = [(0.12, 0.38), (0.88, 0.38), (0.98, 0.98), (0.02, 0.98)]


def _scene(width=240, height=180):
    y = np.linspace(210, 110, height, dtype=np.uint8)[:, None]
    rgb = np.repeat(y, width, axis=1)
    return Image.fromarray(np.dstack([rgb, rgb, rgb]), "RGB")


def _texture(width=96, height=72):
    image = Image.new("RGB", (width, height), (175, 125, 75))
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 12):
        draw.line((x, 0, x, height), fill=(75, 42, 20), width=2)
    for y in range(0, height, 18):
        draw.line((0, y, width, y), fill=(235, 195, 135), width=2)
    return image


def _mask(width=240, height=180):
    image = Image.new("L", (width, height), 0)
    ImageDraw.Draw(image).polygon([(28, 70), (212, 70), (239, 179), (0, 179)], fill=255)
    # Furniture/occlusion hole must remain untouched.
    ImageDraw.Draw(image).rectangle((105, 105, 135, 179), fill=0)
    return image


def test_render_is_byte_identical_outside_mask_and_occlusion():
    scene = _scene()
    mask = _mask()
    result, metadata = render_floor(
        scene,
        _texture(),
        mask,
        QUAD,
        RenderRecipe(illumination_strength=0.6, shadow_strength=0.8, feather=0),
    )
    before = np.asarray(scene)
    after = np.asarray(result)
    selected = np.asarray(mask) >= 128
    assert np.array_equal(before[~selected], after[~selected])
    assert np.any(before[selected] != after[selected])
    assert metadata["provider"] == "local"
    assert metadata["model"] == "deterministic-floor-render-v1"
    assert len(metadata["texture_sha256"]) == 64


def test_feather_is_inward_only():
    scene = _scene()
    mask = _mask()
    hard, _ = render_floor(scene, _texture(), mask, QUAD, RenderRecipe(feather=0))
    soft, _ = render_floor(scene, _texture(), mask, QUAD, RenderRecipe(feather=0.03))
    before = np.asarray(scene)
    hard_arr = np.asarray(hard)
    soft_arr = np.asarray(soft)
    selected = np.asarray(mask) >= 128
    assert np.array_equal(before[~selected], soft_arr[~selected])
    # At least part of the inner edge is closer to the source than the hard composite.
    hard_delta = np.abs(hard_arr.astype(int) - before.astype(int)).sum(axis=2)
    soft_delta = np.abs(soft_arr.astype(int) - before.astype(int)).sum(axis=2)
    assert np.any((soft_delta < hard_delta) & selected)


@pytest.mark.parametrize(
    "quad",
    [
        [(0.1, 0.1), (0.9, 0.1), (0.1, 0.9), (0.9, 0.9)],  # self crossing
        [(0.1, 0.1), (0.1, 0.1), (0.9, 0.9), (0.1, 0.9)],  # duplicate
        [(-0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)],  # outside
    ],
)
def test_invalid_calibration_quad_is_rejected(quad):
    with pytest.raises(ValueError):
        validate_calibration_quad(quad)


def test_empty_mask_is_rejected():
    with pytest.raises(ValueError, match="遮罩为空"):
        render_floor(_scene(), _texture(), Image.new("L", (240, 180)), QUAD)


def test_low_detail_texture_returns_actionable_warning():
    warnings = texture_quality_warnings(Image.new("RGB", (500, 320), (150, 120, 90)))
    assert any("高分辨率" in warning for warning in warnings)
    assert any("不会凭空" in warning for warning in warnings)


def test_record_apply_saves_lossless_png_and_generation_metadata(tmp_path, monkeypatch):
    output = tmp_path / "output_files"
    uploads = tmp_path / "_ng_uploads"
    output.mkdir(exist_ok=True)
    uploads.mkdir(exist_ok=True)
    monkeypatch.setattr(records, "MAIN_OUTPUT_DIR", str(output))
    monkeypatch.setattr(server_api, "MAIN_OUTPUT_DIR", str(output))
    monkeypatch.setattr(server_api, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(server_api, "load_config", lambda: {})
    # Unit-test the endpoint orchestration inline.  Production FastAPI keeps one
    # long-lived event loop; short-lived asyncio.run() executors are unreliable
    # in the packaged test interpreter during executor shutdown.
    async def inline_to_thread(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(server_api.asyncio, "to_thread", inline_to_thread)

    scene_path = output / "scene.png"
    texture_path = uploads / "texture.png"
    _scene(160, 120).save(scene_path)
    _texture(96, 72).save(texture_path)
    record_path = output / "oak_记录.json"
    record_path.write_text(json.dumps([{
        "id": "r1",
        "results": [{"result_id": "source", "result_image_file": "scene.png"}],
    }]), encoding="utf-8")

    mask = Image.new("L", (160, 120), 0)
    ImageDraw.Draw(mask).polygon([(18, 44), (142, 44), (159, 119), (0, 119)], fill=255)
    buffer = io.BytesIO()
    mask.save(buffer, format="PNG")
    request = server_api.FloorVisualizeRequest(
        target=server_api.FloorVisualizeTarget(
            kind="record", json_path=str(record_path), record_id="r1", result_id="source"),
        texture_path=str(texture_path),
        mask_b64=base64.b64encode(buffer.getvalue()).decode(),
        calibration_quad=[server_api.FloorPoint(x=x, y=y) for x, y in QUAD],
    )
    response = asyncio.run(server_api.floor_visualize_apply(request))
    assert response["ok"] is True
    assert response["result_url"].startswith("/outputs/")

    saved = records.load_records_file(str(record_path))[0]["results"][-1]
    assert saved["model_label"] == "真实纹理投影"
    assert saved["generation_metadata"]["provider"] == "local"
    assert saved["generation_metadata"]["model"] == "deterministic-floor-render-v1"
    png_path = output / saved["result_image_file"]
    with Image.open(png_path) as image:
        embedded = json.loads(image.info["floor_engine"])
    assert embedded["texture_sha256"] == saved["generation_metadata"]["texture_sha256"]
