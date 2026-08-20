import base64
import io

import numpy as np
from PIL import Image

from Floor_engine_server import spherical_floor_renderer as spherical_module
from Floor_engine_server.floor_segmentation import SegmentResult
from Floor_engine_server.local_floor_semantics import FloorSemanticResult
from Floor_engine_Linux.spherical_floor_renderer import (
    FLOOR_MASK_VIEWS,
    SphericalFloorRecipe,
    combine_view_masks,
    encode_png_b64,
    render_spherical_floor,
)
from Floor_engine_Linux.film_repeat_floor import analyze_film_repeat


def _texture(width=256, height=128):
    yy, xx = np.mgrid[:height, :width]
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    arr[..., 0] = 145 + (xx % 64) // 4
    arr[..., 1] = 100 + (yy % 24) // 3
    arr[..., 2] = 62
    arr[::24] = (72, 48, 30)
    return Image.fromarray(arr, "RGB")


def _all_view_masks(selected=True):
    value = 255 if selected else 0
    rows = []
    for view in FLOOR_MASK_VIEWS:
        mask = Image.new("L", (view.width, view.height), value)
        rows.append({"id": view.id, "mask_b64": encode_png_b64(mask)})
    return rows


def test_five_view_masks_cover_only_lower_hemisphere():
    mask = np.asarray(combine_view_masks(_all_view_masks(), 512, 256))
    assert not np.any(mask[:124])
    assert np.mean(mask[134:] >= 128) > 0.95
    assert np.mean(mask[-8:] >= 128) == 1.0


def test_near_frontal_positive_overlap_rescues_owner_view_floor_hole():
    rows = []
    for view in FLOOR_MASK_VIEWS:
        if view.id not in {"front", "right"}:
            continue
        value = 255 if view.id == "right" else 0
        rows.append({"id": view.id, "mask_b64": encode_png_b64(
            Image.new("L", (view.width, view.height), value))})
    result = np.asarray(combine_view_masks(rows, 720, 360))
    # yaw~44°, pitch~-5° is owned by the negative front view, but is also a
    # near-frontal positive in the right view. The overlap rule fills it.
    assert result[190, 448] == 255
    # yaw~20° is too oblique in the right view and must stay protected.
    assert result[190, 400] == 0


def test_side_views_use_semantics_and_nadir_uses_multi_point_not_fixed_band(monkeypatch):
    calls = []

    def fake_segment(image, cache_key, *, positive_b64="", negative_b64="",
                     previous_b64="", auto_seed=True):
        calls.append((cache_key, bool(positive_b64), auto_seed))
        mask = np.ones((image.height, image.width), dtype=bool)
        return image, SegmentResult(mask, .99, "ok", [], "mobile_sam")

    monkeypatch.setattr(spherical_module, "segment_floor", fake_segment)
    monkeypatch.setattr(
        spherical_module, "predict_floor_semantics",
        lambda _image: FloorSemanticResult(None, "unavailable", "test", ("floor",), "disabled"))
    monkeypatch.setattr(
        spherical_module, "erp_to_perspective",
        lambda _erp, _yaw, _pitch, _fov, width, height: Image.new("RGB", (width, height), "tan"))

    rows = spherical_module.prepare_floor_mask_views(
        Image.new("RGB", (512, 256), "tan"), cache_key="nadir-contract")

    assert len(rows) == 5
    assert all(not has_positive and auto_seed for _, has_positive, auto_seed in calls[:4])
    assert calls[4][1:] == (True, False)


def test_spherical_floor_preserves_every_pixel_outside_mask():
    scene = np.zeros((256, 512, 3), dtype=np.uint8)
    scene[..., 0] = np.linspace(190, 105, 256, dtype=np.uint8)[:, None]
    scene[..., 1] = 170
    scene[..., 2] = 150
    mask = np.zeros((256, 512), dtype=np.uint8)
    mask[150:, 40:472] = 255
    output, meta = render_spherical_floor(
        Image.fromarray(scene, "RGB"), _texture(), Image.fromarray(mask, "L"),
        SphericalFloorRecipe(
            camera_height_m=1.55, rotation_deg=27, scale=1.2,
            texture_width_mm=2200, texture_height_mm=1100,
            feather=0.01,
        ))
    result = np.asarray(output)
    selected = mask >= 128
    assert np.array_equal(result[~selected], scene[~selected])
    assert np.mean(np.abs(result[selected].astype(float) - scene[selected])) > 3
    assert meta["outside_mask_byte_identical"] is True
    assert meta["model"] == "spherical-floor-render-v4"
    assert meta["texture_periodicized"] is True


def test_spherical_projection_is_deterministic():
    scene = Image.new("RGB", (384, 192), (164, 152, 139))
    mask = Image.new("L", scene.size, 0)
    pixels = np.asarray(mask).copy()
    pixels[105:] = 255
    mask = Image.fromarray(pixels, "L")
    recipe = SphericalFloorRecipe(texture_width_mm=1900, texture_height_mm=1200)
    first, first_meta = render_spherical_floor(scene, _texture(), mask, recipe)
    second, second_meta = render_spherical_floor(scene, _texture(), mask, recipe)
    assert np.array_equal(np.asarray(first), np.asarray(second))
    assert first_meta["recipe"] == second_meta["recipe"]


def test_mask_encoder_is_plain_base64_png():
    encoded = encode_png_b64(Image.new("L", (7, 5), 255))
    assert not encoded.startswith("data:")
    decoded = Image.open(io.BytesIO(base64.b64decode(encoded)))
    assert decoded.size == (7, 5)


def test_spherical_floor_uses_manufacturer_film_without_whole_image_tiling():
    yy, xx = np.mgrid[:240, :180]
    film_arr = np.stack((
        150 + 18 * np.sin(xx / 7),
        118 + 12 * np.sin(xx / 7),
        88 + 8 * np.sin(xx / 7),
    ), axis=-1)
    film = Image.fromarray(np.clip(film_arr, 0, 255).astype(np.uint8), "RGB")
    manifest = analyze_film_repeat(
        film, film_width_mm=984, repeat_length_mm=1890,
        plank_width_mm=136, plank_length_mm=1900,
        seam_type="无缝拼接", floor_size="长窄板直拼：1900 x 136 mm")
    scene = Image.new("RGB", (384, 192), (164, 152, 139))
    mask_arr = np.zeros((192, 384), dtype=np.uint8)
    mask_arr[105:] = 255
    mask = Image.fromarray(mask_arr, "L")

    output, metadata = render_spherical_floor(
        scene, film, mask,
        SphericalFloorRecipe(
            texture_width_mm=1900, texture_height_mm=984,
            plank_width_mm=136, plank_length_mm=1900),
        film_image=film, film_manifest=manifest)

    assert output.size == scene.size
    assert metadata["material_source"] == "manufacturer_repeat_film"
    assert metadata["texture_periodicized"] is False
    assert metadata["film_repeat"]["effective_board_states"] == 1323
    assert metadata["outside_mask_byte_identical"] is True
