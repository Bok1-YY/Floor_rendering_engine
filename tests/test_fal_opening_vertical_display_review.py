from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import requests

from tools.goal_loop_v2.fal_opening_vertical_display_review import execute, parse


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "reports/op004_vertical_display_evidence_20260903/evidence.json"


def _parsed() -> dict:
    return {
        "opening_id": "OP004",
        "intact_wall_baseline_visible": "yes",
        "blue_xy_locator_visible": "yes",
        "orange_unbound_head_guide_visible": "yes",
        "guides_visually_distinct_from_wall": "yes",
        "floor_to_head_opening_cut_visible": "no",
        "door_leaf_threshold_or_sill_geometry_visible": "no",
        "labels_state_unbound_head_and_unknown_sill": "yes",
        "display_misleading_as_confirmed_opening": "no",
        "recommendation": "accept_research_display",
        "confidence": "high",
    }


class Response:
    def __init__(self, payload=None, status=200, json_error=False, content=b""):
        self.payload, self.status_code, self.json_error, self.content = payload, status, json_error, content

    def json(self):
        if self.json_error:
            raise ValueError("not json")
        return self.payload


def _inputs(tmp_path: Path):
    secret = "GENERIC-VERTICAL-DISPLAY-SECRET"
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"fal_api_key": secret, "proxy": "http://proxy", "tls_verify": False}), encoding="utf-8")
    return config, tmp_path / "result.json", secret


def test_op004_success_contract_and_secret(tmp_path: Path, monkeypatch) -> None:
    config, output, secret = _inputs(tmp_path)
    raw = {"choices": [{"message": {"content": json.dumps(_parsed())}}], "usage": {"cost": 0.001}}
    seen = {}

    def post(*args, **kwargs):
        seen.update(kwargs)
        return Response(raw)

    monkeypatch.setattr(requests, "post", post)
    result = execute(config, EVIDENCE, output, "OP004", "google/gemini-2.5-flash")
    assert result["usable_advisory"] is True
    assert result["parsed"] == _parsed()
    assert [item["role"] for item in result["image_bindings"]] == ["labeled_composite", "front_closeup", "top", "northeast"]
    assert seen["proxies"] == {"http": "http://proxy", "https": "http://proxy"}
    assert secret not in output.read_text(encoding="utf-8")
    assert result["source_vertical_confirmation"] is False
    assert result["build_authorized"] is False


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not-json", "bare JSON"),
        (json.dumps({**_parsed(), "opening_id": "OP003"}), "schema/id"),
        (json.dumps({**_parsed(), "recommendation": "maybe"}), "recommendation"),
        (json.dumps({**_parsed(), "extra": True}), "schema/id"),
    ],
)
def test_bad_responses_fail(tmp_path: Path, monkeypatch, content: str, message: str) -> None:
    config, output, _ = _inputs(tmp_path)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response({"choices": [{"message": {"content": content}}]}))
    result = execute(config, EVIDENCE, output, "OP004")
    assert result["usable_advisory"] is False
    assert message in result["validation_error"]


@pytest.mark.parametrize("status", [200, 502])
def test_non_json_hash_only(tmp_path: Path, monkeypatch, status: int) -> None:
    config, output, _ = _inputs(tmp_path)
    body = b"generic vertical display body"
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response(status=status, json_error=True, content=body))
    result = execute(config, EVIDENCE, output, "OP004")
    assert result["usable_advisory"] is False
    assert result["raw_response"] == {"non_json_sha256": hashlib.sha256(body).hexdigest()}


@pytest.mark.parametrize("exc", [requests.ConnectionError("offline"), requests.Timeout("slow")])
def test_transport_failures_write_result(tmp_path: Path, monkeypatch, exc) -> None:
    config, output, _ = _inputs(tmp_path)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(exc))
    result = execute(config, EVIDENCE, output, "OP004")
    assert result["usable_advisory"] is False
    assert output.is_file()


def test_parser_strict() -> None:
    assert parse(json.dumps(_parsed()), "OP004") == _parsed()
    with pytest.raises(ValueError, match="schema/id"):
        parse(json.dumps({**_parsed(), "opening_id": "OP003"}), "OP004")
