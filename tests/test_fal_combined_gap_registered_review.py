from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import requests

from tools.goal_loop_v2.build_combined_gap_registered_evidence import OUT
from tools.goal_loop_v2.fal_combined_gap_registered_review import (
    EXPECTED_IDS,
    execute,
    parse,
)


def _parsed() -> dict:
    return {
        "full_plan_registration_readable": "yes",
        "global_wall_alignment_plausible": "yes",
        "all_nine_candidate_ids_locatable": "yes",
        "unexpected_extra_full_height_gap_visible": "no",
        "combined_xy_visual_result_valid": "yes",
        "recommendation": "accept_combined_xy_research",
        "confidence": "high",
        "per_opening": [
            {
                "opening_id": opening_id,
                "model_gap_centered_on_visible_source_opening": "yes",
                "model_gap_width_matches_source_xy": "yes",
                "neighboring_wall_or_junction_obstruction": "no",
            }
            for opening_id in EXPECTED_IDS
        ],
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
    secret = "COMBINED-REGISTERED-SECRET"
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


def test_success_contract_image_order_and_secret(
    tmp_path: Path,
    evidence_path: Path,
    monkeypatch,
) -> None:
    config, output, secret = _inputs(tmp_path)
    raw = {
        "choices": [{"message": {"content": json.dumps(_parsed())}}],
        "usage": {"cost": 0.003},
    }
    seen = {}

    def post(*args, **kwargs):
        seen.update(kwargs)
        return Response(raw)

    monkeypatch.setattr(requests, "post", post)
    result = execute(
        config,
        evidence_path,
        output,
        "google/gemini-2.5-flash",
    )
    assert result["usable_advisory"] is True
    assert result["parsed"] == _parsed()
    assert [item["role"] for item in result["image_bindings"]] == [
        "full_registered_composite",
        "nine_gap_contact_sheet",
        "full_registered_source_clean",
        "combined_model_top_clean",
    ]
    assert seen["proxies"] == {
        "http": "http://proxy",
        "https": "http://proxy",
    }
    assert seen["verify"] is False
    assert secret not in output.read_text(encoding="utf-8")
    assert all(
        result[key] is False
        for key in (
            "source_correction_authorized",
            "xy_experiment_confirmation",
            "cut_confirmation",
            "pair_confirmation",
            "adjacency_confirmation",
            "semantic_promotion",
            "build_authorized",
            "ready",
        )
    )


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not-json", "bare JSON"),
        (
            json.dumps(
                {
                    **_parsed(),
                    "per_opening": _parsed()["per_opening"][:-1],
                }
            ),
            "count",
        ),
        (
            json.dumps(
                {
                    **_parsed(),
                    "recommendation": "maybe",
                }
            ),
            "recommendation",
        ),
    ],
)
def test_bad_json_row_count_and_enum_fail(
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
        lambda *args, **kwargs: Response(
            {"choices": [{"message": {"content": content}}]}
        ),
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
    body = b"combined registered gateway body"
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: Response(
            status=status,
            json_error=True,
            content=body,
        ),
    )
    result = execute(config, evidence_path, output)
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
