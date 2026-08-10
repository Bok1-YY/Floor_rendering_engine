import numpy as np
from PIL import Image

from standalone_color_calibrator.engine import match_sample_color
from standalone_color_calibrator.advanced import delta_e_ciede2000, signed_lab_array


def _solid(size, color):
    return Image.new("RGB", size, color)


def _from_signed_lab(values):
    encoded = values.copy()
    encoded[..., 1:] = np.where(encoded[..., 1:] < 0, encoded[..., 1:] + 256, encoded[..., 1:])
    return Image.fromarray(np.rint(encoded).astype(np.uint8), mode="LAB").convert("RGB")


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


def test_ciede2000_matches_published_reference_pair():
    first = np.array([50.0, 2.6772, -79.7751])
    second = np.array([50.0, 0.0, -82.7485])
    assert abs(float(delta_e_ciede2000(first, second)) - 2.0425) < 0.0001


def test_distribution_mode_handles_a_complex_bimodal_palette_better_than_classic():
    height, width = 200, 300
    y, x = np.mgrid[:height, :width]
    noise_a = 5 * np.sin(x * 0.13) + 4 * np.cos(y * 0.17)
    noise_b = 4 * np.cos(x * 0.07 + y * 0.11)
    source_lab = np.zeros((height, width, 3), dtype=np.float32)
    source_lab[..., 0] = 150 + 2 * np.sin(x * 0.1)
    source_lab[..., 1] = np.where(x < width * 0.7, -5, 30) + noise_a
    source_lab[..., 2] = np.where(x < width * 0.7, 18, -8) + noise_b
    reference_lab = np.zeros_like(source_lab)
    reference_lab[..., 0] = 150 + 2 * np.cos(y * 0.1)
    reference_lab[..., 1] = np.where(x < width * 0.35, -12, 22) + noise_b
    reference_lab[..., 2] = np.where(x < width * 0.35, 26, -3) + noise_a
    source = _from_signed_lab(source_lab)
    reference = _from_signed_lab(reference_lab)

    classic, _ = match_sample_color(source, reference, strength=1, algorithm="classic")
    detailed, report = match_sample_color(source, reference, strength=1, algorithm="distribution")
    ref_lab = signed_lab_array(reference)
    quantiles = np.linspace(0.02, 0.98, 99)

    def distribution_distance(image):
        lab = signed_lab_array(image)
        return sum(float(np.abs(
            np.quantile(lab[..., channel], quantiles)
            - np.quantile(ref_lab[..., channel], quantiles)
        ).mean()) for channel in (1, 2))

    assert distribution_distance(detailed) < distribution_distance(classic) * 0.8
    assert report.quality is not None
    assert report.quality.algorithm == "distribution"


def test_chroma_illumination_flattens_spatial_cast_and_is_strip_invariant():
    height, width = 192, 256
    y, x = np.mgrid[:height, :width]
    texture = 2 * np.sin(x / 11) + 1.5 * np.cos(y / 9)
    source_lab = np.zeros((height, width, 3), dtype=np.float32)
    source_lab[..., 0] = 155 + texture
    source_lab[..., 1] = 12 + (x / (width - 1) - 0.5) * 16 + texture * 0.15
    source_lab[..., 2] = 22 + (y / (height - 1) - 0.5) * 12
    reference_lab = np.zeros_like(source_lab)
    reference_lab[..., 0] = 155 + texture
    reference_lab[..., 1] = 12 + texture * 0.15
    reference_lab[..., 2] = 22
    source = _from_signed_lab(source_lab)
    reference = _from_signed_lab(reference_lab)

    corrected, report = match_sample_color(
        source, reference, strength=1, algorithm="distribution",
        illumination_mode="chroma", strip_rows=31,
    )
    corrected_single_strip, _ = match_sample_color(
        source, reference, strength=1, algorithm="distribution",
        illumination_mode="chroma", strip_rows=1000,
    )
    before = signed_lab_array(source)
    after = signed_lab_array(corrected)

    def edge_cast(lab):
        horizontal = np.linalg.norm(lab[:, -24:, 1:].mean((0, 1)) - lab[:, :24, 1:].mean((0, 1)))
        vertical = np.linalg.norm(lab[-24:, :, 1:].mean((0, 1)) - lab[:24, :, 1:].mean((0, 1)))
        return float(horizontal + vertical)

    assert edge_cast(after) < edge_cast(before) * 0.2
    assert np.array_equal(np.asarray(corrected), np.asarray(corrected_single_strip))
    assert report.quality is not None
    assert report.quality.applied_illumination_mode == "chroma"
