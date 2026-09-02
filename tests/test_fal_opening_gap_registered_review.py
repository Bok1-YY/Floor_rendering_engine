import hashlib
import json

import pytest
import requests

from tools.goal_loop_v2.build_opening_gap_registered_composites import build
from tools.goal_loop_v2.fal_opening_gap_registered_review import FIELDS, execute, parse


def _parsed(opening_id="OP004"):
    return {"opening_id": opening_id, "source_segment_visible": "yes", "model_gap_centered_on_source_segment": "yes", "model_gap_width_matches_source_xy": "yes", "junction_or_neighbor_obstruction": "no", "xy_variant_visually_valid": "yes", "recommendation": "accept_xy_variant", "confidence": "high"}


class Response:
    def __init__(self, payload=None, status=200, json_error=False, content=b""):
        self.payload, self.status_code, self.json_error, self.content = payload, status, json_error, content

    def json(self):
        if self.json_error:
            raise ValueError("not json")
        return self.payload


@pytest.fixture(scope="module")
def evidence_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("registered-gap-review")
    evidence = build(out_dir=root)
    path = root / "registered-composites.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    return path


def _inputs(tmp_path, evidence_path):
    secret = "REGISTERED-SECRET-TEST"
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"fal_api_key": secret, "proxy": "http://proxy", "tls_verify": False}), encoding="utf-8")
    return config, tmp_path / "nested/result.json", secret


def test_success_same_metric_contract_image_order_and_secret(tmp_path, evidence_path, monkeypatch):
    config, output, secret = _inputs(tmp_path, evidence_path)
    raw = {"choices": [{"message": {"content": json.dumps(_parsed())}}], "usage": {"cost": 0.001}}
    seen = {}

    def post(*args, **kwargs):
        seen.update(kwargs)
        return Response(raw)

    monkeypatch.setattr(requests, "post", post)
    result = execute(config, evidence_path, output, "OP004", "google/gemini-2.5-flash")
    assert result["usable_advisory"] is True and result["parsed"] == _parsed()
    assert [item["role"] for item in result["image_bindings"]] == ["composite", "registered_source", "model_closeup"]
    assert result["metric_window"]["resolution_px"] == [1200, 1200]
    assert seen["proxies"] == {"http": "http://proxy", "https": "http://proxy"} and seen["verify"] is False
    assert secret not in output.read_text(encoding="utf-8")
    assert all(result[key] is False for key in ("xy_experiment_confirmation", "cut_confirmation", "pair_confirmation", "adjacency_confirmation", "semantic_promotion", "build_authorized"))


@pytest.mark.parametrize(
    ("content", "message"),
    [("```json {} ```", "bare JSON"), (json.dumps({**_parsed(), "opening_id": "OP003"}), "schema/id"), (json.dumps({**_parsed(), "recommendation": "maybe"}), "enum")],
)
def test_fence_wrong_id_and_enum_fail(tmp_path, evidence_path, monkeypatch, content, message):
    config, output, _ = _inputs(tmp_path, evidence_path)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response({"choices": [{"message": {"content": content}}]}))
    result = execute(config, evidence_path, output, "OP004")
    assert result["usable_advisory"] is False and message in result["validation_error"]


@pytest.mark.parametrize("status", [200, 502])
def test_non_json_is_hash_only(tmp_path, evidence_path, monkeypatch, status):
    config, output, _ = _inputs(tmp_path, evidence_path)
    body = b"registered gateway body"
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response(status=status, json_error=True, content=body))
    result = execute(config, evidence_path, output, "OP004")
    assert result["usable_advisory"] is False and result["raw_response"] == {"non_json_sha256": hashlib.sha256(body).hexdigest()}
    assert body.decode() not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("exc", [requests.ConnectionError("offline"), requests.Timeout("slow")])
def test_transport_failure_writes_result(tmp_path, evidence_path, monkeypatch, exc):
    config, output, _ = _inputs(tmp_path, evidence_path)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(exc))
    result = execute(config, evidence_path, output, "OP004")
    assert result["usable_advisory"] is False and type(exc).__name__ in result["transport_error"] and output.is_file()


def test_exact_parser_contract():
    assert len(FIELDS) == 8 and parse(json.dumps(_parsed()), "OP004") == _parsed()
    with pytest.raises(ValueError, match="bare JSON"):
        parse("```json {} ```", "OP004")
