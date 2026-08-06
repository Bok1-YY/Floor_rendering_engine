import numpy as np
import pytest
from fastapi import HTTPException
from PIL import Image

from Floor_engine_server import floor_segmentation, routes_inpaint, server_schemas


def test_mask_rle_round_trip_is_row_major():
    mask = np.zeros((5, 7), dtype=bool)
    mask[1:4, 2:6] = True
    mask[2, 3] = False

    encoded = floor_segmentation.encode_mask_rle(mask)
    decoded = floor_segmentation.decode_mask_rle(encoded, (7, 5))

    assert encoded[0] > 0
    assert np.array_equal(decoded, mask)


def _fake_runtime(monkeypatch):
    runtime = floor_segmentation._RUNTIME
    monkeypatch.setattr(runtime, '_encoder', object())
    monkeypatch.setattr(runtime, '_decoder', object())
    monkeypatch.setattr(runtime, '_error', '')

    def predict_raw(image, cache_key, points, labels, previous=None):
        x, y = np.rint(points[0]).astype(int)
        yy, xx = np.ogrid[:image.height, :image.width]
        logits = np.full((image.height, image.width), -2.0, dtype=np.float32)
        logits[(xx - x) ** 2 + (yy - y) ** 2 <= 12 ** 2] = 2.0
        return logits[None], [0.93]

    monkeypatch.setattr(runtime, 'predict_raw', predict_raw)
    floor_segmentation._OBJECT_SCAN_CACHE.clear()


def test_object_scan_filters_and_caps_candidates(monkeypatch):
    _fake_runtime(monkeypatch)
    image = Image.new('RGB', (320, 240), (120, 120, 120))

    result = floor_segmentation.scan_object_masks(image, 'scan-test')

    assert result.status == 'ok'
    assert len(result.candidates) == floor_segmentation.OBJECT_SCAN_MAX_CANDIDATES
    assert all(candidate.area > 0 and candidate.rle for candidate in result.candidates)
    assert all(candidate.confidence == pytest.approx(0.93) for candidate in result.candidates)


def test_point_segment_returns_click_region(monkeypatch):
    _fake_runtime(monkeypatch)
    image = Image.new('RGB', (200, 120), (120, 120, 120))

    result = floor_segmentation.segment_mask_at_point(image, 'point-test', 0.5, 0.5)

    assert result.status == 'ok'
    assert len(result.candidates) == 1
    mask = floor_segmentation.decode_mask_rle(result.candidates[0].rle, result.size)
    assert mask[result.size[1] // 2, result.size[0] // 2]


def test_inpaint_point_segment_requires_point(monkeypatch):
    monkeypatch.setattr(
        routes_inpaint, '_resolve_inpaint_source',
        lambda target: (Image.new('RGB', (32, 24)), '', ''),
    )
    request = server_schemas.InpaintSegmentRequest(
        target=server_schemas.InpaintTarget(kind='room', room_path='unused.png'),
        strategy='point',
    )

    with pytest.raises(HTTPException) as error:
        routes_inpaint.inpaint_segment(request)

    assert error.value.status_code == 422


def test_inpaint_segment_serializes_candidates(monkeypatch):
    monkeypatch.setattr(
        routes_inpaint, '_resolve_inpaint_source',
        lambda target: (Image.new('RGB', (32, 24)), '', ''),
    )
    candidate = floor_segmentation.MaskCandidate(
        id='object-1', rle=[3, 4, 5], bbox=(1, 2, 3, 4), area=4,
        confidence=0.91234, stability=0.87654,
    )
    monkeypatch.setattr(
        routes_inpaint, 'scan_object_masks',
        lambda image, cache_key: floor_segmentation.SmartSegmentResult(
            (32, 24), [candidate], 'ok', []),
    )
    request = server_schemas.InpaintSegmentRequest(
        target=server_schemas.InpaintTarget(kind='room', room_path='unused.png'),
        strategy='scan_objects',
    )

    response = routes_inpaint.inpaint_segment(request)

    assert response['width'] == 32 and response['height'] == 24
    assert response['candidates'][0] == {
        'id': 'object-1', 'rle': [3, 4, 5], 'bbox': [1, 2, 3, 4], 'area': 4,
        'confidence': 0.9123, 'stability': 0.8765,
    }
