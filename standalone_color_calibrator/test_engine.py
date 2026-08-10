import numpy as np
from PIL import Image

from standalone_color_calibrator.engine import match_sample_color


def _solid(size, color):
    return Image.new("RGB", size, color)


def test_color_only_moves_chroma_toward_reference_and_keeps_luminance():
    source = _solid((160, 90), (80, 115, 165))
    reference = _solid((40, 40), (165, 125, 70))
    output, report = match_sample_color(
        source, reference, strength=1.0, preserve_luminance=True
    )
    source_lab = np.asarray(source.convert("LAB"), dtype=np.int16)
    output_lab = np.asarray(output.convert("LAB"), dtype=np.int16)
    reference_lab = np.asarray(reference.convert("LAB"), dtype=np.int16)
    assert np.abs(output_lab[..., 0] - source_lab[..., 0]).mean() < 2.0
    before = np.linalg.norm(source_lab[..., 1:].mean((0, 1)) - reference_lab[..., 1:].mean((0, 1)))
    after = np.linalg.norm(output_lab[..., 1:].mean((0, 1)) - reference_lab[..., 1:].mean((0, 1)))
    assert after < before * 0.2
    assert report.selected_rect == (0.0, 0.0, 1.0, 1.0)


def test_selected_clean_area_drives_whole_image_transform():
    source = _solid((200, 100), (175, 140, 95))
    source.paste(_solid((100, 100), (30, 180, 40)), (100, 0))
    reference = _solid((50, 50), (120, 105, 85))
    output, _ = match_sample_color(
        source, reference, source_rect=(0, 0, 0.5, 1), strength=1.0
    )
    source_arr = np.asarray(source, dtype=np.int16)
    output_arr = np.asarray(output, dtype=np.int16)
    assert np.abs(output_arr[:, :100] - source_arr[:, :100]).mean() > 5
    assert np.abs(output_arr[:, 100:] - source_arr[:, 100:]).mean() > 5


def test_zero_strength_is_pixel_identical():
    source = _solid((87, 53), (72, 103, 151))
    output, _ = match_sample_color(
        source, _solid((20, 20), (170, 120, 65)), strength=0
    )
    assert np.array_equal(np.asarray(output), np.asarray(source))


def test_extracted_color_path_agrees_with_floor_engine_core():
    """The standalone extraction retains the established local-mode result."""
    from color_match import match_color_masked

    source = _solid((96, 64), (82, 117, 166))
    reference = _solid((32, 24), (168, 126, 72))
    standalone, _ = match_sample_color(
        source, reference, strength=1.0, preserve_luminance=True
    )
    core = match_color_masked(
        source, reference, Image.new("L", source.size, 255),
        strength=1.0, mask_feather=0,
    )
    delta = np.abs(
        np.asarray(standalone, dtype=np.int16) - np.asarray(core, dtype=np.int16)
    )
    assert delta.max() <= 1
