from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import requests

from tools.goal_loop_v2.fal_targeted_subtype_review import execute, parse


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "reports/op006_targeted_subtype_evidence_20260903/evidence.json"


def _parsed() -> dict:
    return {
        "opening_id": "OP006",
        "visual_kind": "door",
        "wall_break_visible": "yes",
        "swing_arc_visible": "yes",
        "sliding_track_visible": "no",
        "neighboring_opening_cue_visible": "no",
        "target_swing_cue_attributable_to_target": "yes",
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
    secret = "TARGETED-SUBTYPE-SECRET"
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"fal_api_key": secret, "proxy": "http://proxy", "tls_verify": False}), encoding="utf-8")
    return config, tmp_path / "result.json", secret


def test_success_authority_order_proxy_and_secret(tmp_path: Path, monkeypatch) -> None:
    config, output, secret = _inputs(tmp_path)
    raw = {"choices": [{"message": {"content": json.dumps(_parsed())}}], "usage": {"cost": 0.001}}
    seen = {}

    def post(*args, **kwargs):
        seen.update(kwargs)
        return Response(raw)

    monkeypatch.setattr(requests, "post", post)
    result = execute(config, EVIDENCE, output, "OP006", "google/gemini-2.5-flash")
    assert result["usable_advisory"] is True
    assert result["parsed"] == _parsed()
    assert [row["role"] for row in result["image_bindings"]] == ["targeted_raw_crop", "locator"]
    assert [row["semantic_authority"] for row in result["image_bindings"]] == [True, False]
    assert seen["proxies"] == {"http": "http://proxy", "https": "http://proxy"}
    assert secret not in output.read_text(encoding="utf-8")
    assert result["source_subtype_confirmation"] is False
    assert result["build_authorized"] is False


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not-json", "bare JSON"),
        (json.dumps({**_parsed(), "opening_id": "OP008"}), "schema/id"),
        (json.dumps({**_parsed(), "target_swing_cue_attributable_to_target": "maybe"}), "enum"),
        (json.dumps({**_parsed(), "extra": True}), "schema/id"),
    ],
)
def test_bad_results_fail(tmp_path: Path, monkeypatch, content: str, message: str) -> None:
    config, output, _ = _inputs(tmp_path)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response({"choices": [{"message": {"content": content}}]}))
    result = execute(config, EVIDENCE, output, "OP006")
    assert result["usable_advisory"] is False
    assert message in result["validation_error"]


@pytest.mark.parametrize("status", [200, 502])
def test_non_json_hash_only(tmp_path: Path, monkeypatch, status: int) -> None:
    config, output, _ = _inputs(tmp_path)
    body = b"targeted subtype body"
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response(status=status, json_error=True, content=body))
    result = execute(config, EVIDENCE, output, "OP006")
    assert result["usable_advisory"] is False
    assert result["raw_response"] == {"non_json_sha256": hashlib.sha256(body).hexdigest()}


@pytest.mark.parametrize("exc", [requests.ConnectionError("offline"), requests.Timeout("slow")])
def test_transport_failure_writes(tmp_path: Path, monkeypatch, exc) -> None:
    config, output, _ = _inputs(tmp_path)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(exc))
    result = execute(config, EVIDENCE, output, "OP006")
    assert result["usable_advisory"] is False
    assert output.is_file()


def test_parser_strict() -> None:
    assert parse(json.dumps(_parsed()), "OP006") == _parsed()
    with pytest.raises(ValueError, match="schema/id"):
        parse(json.dumps({**_parsed(), "opening_id": "OP008"}), "OP006")
