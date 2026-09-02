from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import requests

from tools.goal_loop_v2.build_op002_vertical_display_evidence import OUT
from tools.goal_loop_v2.fal_op002_vertical_display_review import execute, parse


def _parsed() -> dict:
    return {
        "opening_id": "OP002",
        "intact_wall_baseline_visible": "yes",
        "blue_xy_locator_visible": "yes",
        "orange_head_assumption_guide_visible": "yes",
        "guides_visually_distinct_from_wall": "yes",
        "floor_to_head_opening_cut_visible": "no",
        "door_leaf_threshold_or_sill_geometry_visible": "no",
        "display_labels_state_assumptions_and_unknown_sill": "yes",
        "display_misleading_as_confirmed_opening": "no",
        "recommendation": "accept_layer3b_research_display",
        "confidence": "high",
    }


class Response:
    def __init__(self, payload=None, status=200, json_error=False, content=b""):
        self.payload = payload
        self.status_code = status
        self.json_error = json_error
        self.content = content

    def json(self):
        if self.json_error:
            raise ValueError("not json")
        return self.payload


@pytest.fixture(scope="module")
def evidence_path() -> Path:
    path = OUT / "evidence.json"
    assert path.is_file()
    return path


def _inputs(tmp_path: Path) -> tuple[Path, Path, str]:
    secret = "OP002-LAYER3B-DISPLAY-SECRET"
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"fal_api_key": secret, "proxy": "http://proxy", "tls_verify": False}),
        encoding="utf-8",
    )
    return config, tmp_path / "nested/result.json", secret


def test_success_contract_images_proxy_and_secret(
    tmp_path: Path,
    evidence_path: Path,
    monkeypatch,
) -> None:
    config, output, secret = _inputs(tmp_path)
    raw = {
        "choices": [{"message": {"content": json.dumps(_parsed())}}],
        "usage": {"cost": 0.001},
    }
    seen = {}

    def post(*args, **kwargs):
        seen.update(kwargs)
        return Response(raw)

    monkeypatch.setattr(requests, "post", post)
    result = execute(config, evidence_path, output, "google/gemini-2.5-flash")
    assert result["usable_advisory"] is True
    assert result["parsed"] == _parsed()
    assert [item["role"] for item in result["image_bindings"]] == [
        "labeled_composite",
        "front_closeup",
        "top",
        "northeast",
    ]
    assert seen["proxies"] == {"http": "http://proxy", "https": "http://proxy"}
    assert seen["verify"] is False
    assert secret not in output.read_text(encoding="utf-8")
    assert result["display_clarity_advisory_only"] is True
    assert result["source_vertical_confirmation"] is False
    assert result["semantic_promotion"] is False
    assert result["build_authorized"] is False
    assert result["ready"] is False


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not-json", "bare JSON"),
        (json.dumps({**_parsed(), "opening_id": "OP003"}), "schema/id"),
        (json.dumps({**_parsed(), "recommendation": "maybe"}), "recommendation"),
        (json.dumps({**_parsed(), "extra": True}), "schema/id"),
    ],
)
def test_bad_json_wrong_id_recommendation_and_extra_fail(
    tmp_path: Path,
    evidence_path: Path,
    monkeypatch,
    content: str,
    message: str,
) -> None:
    config, output, _ = _inputs(tmp_path)
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: Response({"choices": [{"message": {"content": content}}]}),
    )
    result = execute(config, evidence_path, output)
    assert result["usable_advisory"] is False
    assert message in result["validation_error"]


@pytest.mark.parametrize("status", [200, 502])
def test_non_json_is_hash_only(
    tmp_path: Path,
    evidence_path: Path,
    monkeypatch,
    status: int,
) -> None:
    config, output, _ = _inputs(tmp_path)
    body = b"op002 layer3b display gateway body"
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: Response(status=status, json_error=True, content=body),
    )
    result = execute(config, evidence_path, output)
    assert result["usable_advisory"] is False
    assert result["raw_response"] == {"non_json_sha256": hashlib.sha256(body).hexdigest()}
    assert body.decode() not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "exc",
    [requests.ConnectionError("offline"), requests.Timeout("slow")],
)
def test_transport_failure_writes_result(
    tmp_path: Path,
    evidence_path: Path,
    monkeypatch,
    exc,
) -> None:
    config, output, _ = _inputs(tmp_path)
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(exc),
    )
    result = execute(config, evidence_path, output)
    assert result["usable_advisory"] is False
    assert type(exc).__name__ in result["transport_error"]
    assert output.is_file()


def test_exact_parser_contract() -> None:
    assert parse(json.dumps(_parsed())) == _parsed()
    with pytest.raises(ValueError, match="bare JSON"):
        parse("not-json")
