import hashlib
import json
from pathlib import Path

import pytest
import requests

from tools.goal_loop_v2.build_opening_xy_clean_evidence import build
from tools.goal_loop_v2.fal_opening_xy_clean_review import FIELDS, execute, parse


def _parsed(opening_id="OP004"):
    return {
        "opening_id": opening_id,
        "segment_on_visible_opening": "yes",
        "visible_opening_endpoints": "yes",
        "continuous_wall_across_segment": "no",
        "door_leaf_or_swing_visible": "yes",
        "glazed_interface_visible": "no",
        "xy_gap_plausible": "yes",
        "confidence": "high",
    }


class Response:
    status_code = 200
    content = b""

    def __init__(self, payload=None, json_error=False, status=200, content=b""):
        self.payload, self.json_error, self.status_code, self.content = payload, json_error, status, content

    def json(self):
        if self.json_error:
            raise ValueError("not json")
        return self.payload


def _inputs(tmp_path):
    evidence_dir = tmp_path / "evidence"
    evidence = build(out_dir=evidence_dir)
    evidence_path = evidence_dir / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    secret = "TOP-SECRET-TEST-KEY"
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"fal_api_key": secret, "fal_queue_proxy": "http://proxy", "tls_verify": False}), encoding="utf-8")
    return evidence_path, config, tmp_path / "nested/result.json", secret


def test_valid_result_binds_contract_and_stays_fail_closed(tmp_path, monkeypatch):
    evidence, config, output, secret = _inputs(tmp_path)
    raw = {"choices": [{"message": {"content": json.dumps(_parsed())}}], "usage": {"cost": 0.001}}
    seen = {}

    def post(*args, **kwargs):
        seen.update(kwargs)
        return Response(raw)

    monkeypatch.setattr(requests, "post", post)
    result = execute(config, evidence, output, "OP004", "google/gemini-2.5-flash")
    assert result["usable_advisory"] is True and result["parsed"] == _parsed()
    assert [row["role"] for row in result["image_bindings"]] == ["locator", "raw_crop"]
    assert seen["proxies"] == {"http": "http://proxy", "https": "http://proxy"} and seen["verify"] is False
    assert all(result[key] is False for key in ("cut_confirmation", "pair_confirmation", "adjacency_confirmation", "semantic_promotion", "build_authorized"))
    assert secret not in output.read_text(encoding="utf-8")
    assert len(result["request_contract_sha256"]) == len(result["raw_response_sha256"]) == 64


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("```json\n{}\n```", "bare JSON"),
        (json.dumps({**_parsed(), "opening_id": "OP003"}), "schema/id"),
        (json.dumps({**_parsed(), "xy_gap_plausible": "maybe"}), "enum"),
        ("{bad", "bare JSON"),
    ],
)
def test_fence_wrong_id_enum_and_malformed_content_fail(tmp_path, monkeypatch, content, message):
    evidence, config, output, _ = _inputs(tmp_path)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response({"choices": [{"message": {"content": content}}]}))
    result = execute(config, evidence, output, "OP004")
    assert result["usable_advisory"] is False and result["parsed"] is None and message in result["validation_error"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [(200, "non-JSON provider response"), (502, "fal HTTP 502; non-JSON provider response")],
)
def test_non_json_http_is_hash_only_and_fail_closed(tmp_path, monkeypatch, status, expected):
    evidence, config, output, secret = _inputs(tmp_path)
    body = b"gateway text that is not retained"
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response(json_error=True, status=status, content=body))
    result = execute(config, evidence, output, "OP004")
    assert result["usable_advisory"] is False and result["validation_error"] == expected
    assert result["raw_response"] == {"non_json_sha256": hashlib.sha256(body).hexdigest()}
    saved = output.read_text(encoding="utf-8")
    assert body.decode() not in saved and secret not in saved


@pytest.mark.parametrize("exception", [requests.ConnectionError("offline"), requests.Timeout("slow")])
def test_transport_failure_writes_result(tmp_path, monkeypatch, exception):
    evidence, config, output, _ = _inputs(tmp_path)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(exception))
    result = execute(config, evidence, output, "OP004")
    assert result["usable_advisory"] is False and result["http_status"] is None
    assert type(exception).__name__ in result["transport_error"] and output.is_file()


def test_parse_requires_exact_bare_json():
    assert parse(json.dumps(_parsed()), "OP004") == _parsed()
    assert len(FIELDS) == 8
    with pytest.raises(ValueError, match="bare JSON"):
        parse("```json {} ```", "OP004")
