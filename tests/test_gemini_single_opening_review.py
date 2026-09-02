import json

import pytest

from tools.goal_loop_v2.gemini_single_opening_review import EXPECTED_KEYS, _image_part, build_prompt, parse_review_text


def _valid(opening_id="OP009"):
    return {
        "opening_id": opening_id,
        "review_status": "indeterminate",
        "visual_kind": "glazed_interface",
        "swing": "unknown",
        "side_a": "known",
        "side_b": "unknown",
        "confidence": "low",
    }


def test_prompt_is_single_opening_and_prohibits_room_and_entrance_claims():
    prompt = build_prompt("OP009")
    assert "Review ONLY opening OP009" in prompt
    assert "never name a room" in prompt
    assert "never assert an entrance" in prompt


def test_strict_result_accepts_only_exact_schema_and_target():
    result = parse_review_text(json.dumps(_valid(), separators=(",", ":")), "OP009")
    assert set(result) == EXPECTED_KEYS
    extra = _valid()
    extra["room_name"] = "forbidden"
    with pytest.raises(ValueError, match="schema mismatch"):
        parse_review_text(json.dumps(extra), "OP009")
    with pytest.raises(ValueError, match="opening mismatch"):
        parse_review_text(json.dumps(_valid("OP010")), "OP009")


def test_markdown_or_invalid_enum_fails_closed():
    with pytest.raises(ValueError, match="bare JSON"):
        parse_review_text("```json\n" + json.dumps(_valid()) + "\n```", "OP009")
    invalid = _valid()
    invalid["review_status"] = "confirmed"
    with pytest.raises(ValueError, match="enum mismatch"):
        parse_review_text(json.dumps(invalid), "OP009")


def test_rest_image_part_uses_google_camel_case_fields(tmp_path):
    image = tmp_path / "x.png"
    image.write_bytes(b"png-bytes")
    part, binding = _image_part(image)
    assert part["inlineData"]["mimeType"] == "image/png"
    assert binding["bytes"] == 9
