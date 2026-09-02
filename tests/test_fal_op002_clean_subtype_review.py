from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import requests

from tools.goal_loop_v2.fal_op002_clean_subtype_review import (
    FIELDS,
    execute,
    parse,
)


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "reports/opening_xy_clean_evidence_20260902/evidence.json"


def _parsed() -> dict:
    return {
        "opening_id": "OP002",
        "visual_kind": "door",
        "wall_break_visible": "yes",
        "swing_arc_visible": "yes",
        "sliding_track_visible": "no",
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


def _inputs(tmp_path: Path) -> tuple[Path, Path, str]:
    secret = "OP002-CLEAN-SUBTYPE-SECRET"
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "fal_api_key": secret,
                "proxy": "http://proxy",
                "tls_verify": False,
            }
        ),
        encoding="utf-8",
    )
    return config, tmp_path / "nested/result.json", secret


def test_success_raw_first_locator_navigation_and_secret(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config, output, secret = _inputs(tmp_path)
    raw = {
        "choices": [{"message": {"content": json.dumps(_parsed())}}],
        "usage": {"cost": 0.0005},
    }
    seen = {}

    def post(*args, **kwargs):
        seen.update(kwargs)
        return Response(raw)

    monkeypatch.setattr(requests, "post", post)
    result = execute(
        config,
        EVIDENCE,
        output,
        "google/gemini-2.5-flash",
    )
    assert result["usable_advisory"] is True
    assert result["parsed"] == _parsed()
    assert [item["role"] for item in result["image_bindings"]] == [
        "raw_crop",
        "locator",
    ]
    assert [item["semantic_authority"] for item in result["image_bindings"]] == [
        True,
        False,
    ]
    assert result["host_atom_id"] == "ATOM-WB006-02"
    assert seen["proxies"] == {
        "http": "http://proxy",
        "https": "http://proxy",
    }
    assert seen["verify"] is False
    assert secret not in output.read_text(encoding="utf-8")
    assert result["visual_subtype_candidate_only"] is True
    assert result["source_subtype_confirmation"] is False
    assert result["effective_void_confirmation"] is False
    assert result["traversability_confirmation"] is False
    assert result["semantic_promotion"] is False
    assert result["score_effect"] == "none"
    assert result["build_authorized"] is False
    assert result["ready"] is False


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not-json", "bare JSON"),
        (
            json.dumps({**_parsed(), "opening_id": "OP003"}),
            "schema/id",
        ),
        (
            json.dumps({**_parsed(), "visual_kind": "maybe"}),
            "visual-kind",
        ),
        (
            json.dumps({**_parsed(), "extra": True}),
            "schema/id",
        ),
    ],
)
def test_bad_json_wrong_id_enum_and_extra_key_fail(
    tmp_path: Path,
    monkeypatch,
    content: str,
    message: str,
) -> None:
    config, output, _ = _inputs(tmp_path)
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: Response(
            {"choices": [{"message": {"content": content}}]}
        ),
    )
    result = execute(config, EVIDENCE, output)
    assert result["usable_advisory"] is False
    assert message in result["validation_error"]
    assert output.is_file()


@pytest.mark.parametrize("status", [200, 502])
def test_non_json_is_hash_only(
    tmp_path: Path,
    monkeypatch,
    status: int,
) -> None:
    config, output, _ = _inputs(tmp_path)
    body = b"op002 clean subtype gateway body"
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: Response(
            status=status,
            json_error=True,
            content=body,
        ),
    )
    result = execute(config, EVIDENCE, output)
    assert result["usable_advisory"] is False
    assert result["raw_response"] == {
        "non_json_sha256": hashlib.sha256(body).hexdigest()
    }
    assert body.decode() not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "exc",
    [requests.ConnectionError("offline"), requests.Timeout("slow")],
)
def test_transport_failure_writes_result(
    tmp_path: Path,
    monkeypatch,
    exc,
) -> None:
    config, output, _ = _inputs(tmp_path)
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(exc),
    )
    result = execute(config, EVIDENCE, output)
    assert result["usable_advisory"] is False
    assert type(exc).__name__ in result["transport_error"]
    assert output.is_file()


def test_exact_parser_contract() -> None:
    assert len(FIELDS) == 6
    assert parse(json.dumps(_parsed())) == _parsed()
    with pytest.raises(ValueError, match="bare JSON"):
        parse("not-json")


def test_locator_cannot_be_rebound_as_semantic_raw_crop(tmp_path: Path) -> None:
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    row = next(item for item in evidence["openings"] if item["opening_id"] == "OP002")
    locator = dict(row["artifacts"]["locator"])
    locator["role"] = "byte_exact_source_crop"
    locator["source_pixels_untouched"] = True
    row["artifacts"]["raw_crop"] = locator
    tampered = tmp_path / "evidence.json"
    tampered.write_text(json.dumps(evidence), encoding="utf-8")
    config, output, _ = _inputs(tmp_path)
    with pytest.raises(ValueError):
        execute(config, tampered, output)
