from pathlib import Path
import json
import pytest
from tools.goal_loop_v2.human_entry_protocol import build_annotation, validate_annotation


def _fixture(tmp_path: Path):
    src = tmp_path / "source.json"; src.write_text('{"x":1}', encoding="utf-8")
    img = tmp_path / "plan.png"; img.write_bytes(b"png")
    doc = {"sample_id": "1308", "structure_hash": "a" * 64}
    return doc, src, img


def test_click_annotation_is_bound_and_fail_closed(tmp_path):
    doc, src, img = _fixture(tmp_path)
    c = build_annotation(doc, src, image_file=img, clicks=[{"x": 1101.5, "y": 912, "role": "entrance_point"}, {"x": 1080, "y": 912, "role": "inside_side"}])
    out = validate_annotation(c, doc)
    assert out["policy"]["entrance_confirmation"] is False
    assert out["annotation_frame"]["space"] == "canonical_px"
    assert out["source"]["image"]["sha256"]


def test_annotation_rejects_promotion_and_unknown_role(tmp_path):
    doc, src, img = _fixture(tmp_path)
    with pytest.raises(ValueError):
        build_annotation(doc, src, image_file=img, clicks=[{"x": 1, "y": 2, "role": "confirmed_entrance"}])
    c = build_annotation(doc, src, image_file=img, clicks=[{"x": 1, "y": 2}])
    c["policy"]["entrance_confirmation"] = True
    with pytest.raises(ValueError, match="unsafe"):
        validate_annotation(c, doc)
