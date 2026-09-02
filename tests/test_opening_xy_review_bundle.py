from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tools.fastloop_research.contract import canonical_json
from tools.goal_loop_v2.build_opening_xy_clean_evidence import EXPECTED_INCLUDED, build as build_evidence
from tools.goal_loop_v2.build_opening_xy_review_bundle import build, validate


def _parsed(opening_id, **overrides):
    value = {
        "opening_id": opening_id,
        "segment_on_visible_opening": "yes",
        "visible_opening_endpoints": "yes",
        "continuous_wall_across_segment": "no",
        "door_leaf_or_swing_visible": "yes",
        "glazed_interface_visible": "no",
        "xy_gap_plausible": "yes",
        "confidence": "high",
    }
    value.update(overrides)
    return value


def _canon(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _write_result(path, evidence_path, evidence, row, model, parsed, cost):
    raw = {"choices": [{"message": {"content": json.dumps(parsed)}}], "usage": {"cost": cost}}
    bindings = [{"role": role, "filename": Path(row["artifacts"][role]["path"]).name, "bytes": row["artifacts"][role]["bytes"], "sha256": row["artifacts"][role]["sha256"]} for role in ("locator", "raw_crop")]
    result = {
        "schema": "fal-opening-xy-clean-review-v2",
        "opening_id": row["opening_id"],
        "endpoint": "https://fal.run/openrouter/router/openai/v1/chat/completions",
        "model": model,
        "request_contract_sha256": "a" * 64,
        "prompt_sha256": "b" * 64,
        "system_sha256": "c" * 64,
        "evidence_file_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "evidence_candidate_hash": evidence["candidate_hash"],
        "image_bindings": bindings,
        "local_segment_px": row["local_segment_px"],
        "http_status": 200,
        "parsed": parsed,
        "validation_error": None,
        "transport_error": None,
        "usable_advisory": True,
        "usage": {"cost": cost},
        "raw_response": raw,
        "raw_response_sha256": _canon(raw),
        "cut_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result), encoding="utf-8")


def _inputs(tmp_path):
    evidence_dir = tmp_path / "evidence"
    evidence = build_evidence(out_dir=evidence_dir)
    evidence_path = evidence_dir / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    gemini, openai = tmp_path / "gemini", tmp_path / "openai"
    rows = {row["opening_id"]: row for row in evidence["openings"]}
    for opening_id in EXPECTED_INCLUDED:
        g = _parsed(opening_id)
        o = _parsed(opening_id)
        if opening_id == "OP004":
            o["visible_opening_endpoints"] = "no"
        if opening_id == "OP009":
            o["door_leaf_or_swing_visible"] = "no"
            o["glazed_interface_visible"] = "yes"
        if opening_id == "OP010":
            g["door_leaf_or_swing_visible"] = "no"
        _write_result(gemini / opening_id / "result.json", evidence_path, evidence, rows[opening_id], "google/gemini-2.5-flash", g, 0.001)
        _write_result(openai / opening_id / ("retry-result.json" if opening_id == "OP004" else "result.json"), evidence_path, evidence, rows[opening_id], "openai/gpt-4o-mini", o, 0.002)
    failed = json.loads((openai / "OP004/retry-result.json").read_text(encoding="utf-8"))
    failed.update({"http_status": None, "parsed": None, "validation_error": "SSLError: eof", "transport_error": "SSLError: eof", "usable_advisory": False, "usage": None, "raw_response": None, "raw_response_sha256": None})
    (openai / "OP004/result.json").write_text(json.dumps(failed), encoding="utf-8")
    return evidence_path, gemini, openai


def test_bundle_derives_consensus_disagreements_cost_and_failed_attempt(tmp_path):
    evidence, gemini, openai = _inputs(tmp_path)
    result = build(evidence_path=evidence, gemini_base=gemini, openai_base=openai)
    assert result["included_for_xy_experiment"] == list(EXPECTED_INCLUDED)
    assert result["cue_disagreements_by_opening"] == {
        "OP004": ["visible_opening_endpoints"],
        "OP009": ["door_leaf_or_swing_visible", "glazed_interface_visible"],
        "OP010": ["door_leaf_or_swing_visible"],
    }
    assert result["selected_review_cost_usd"] == pytest.approx(0.027)
    assert len(result["failed_attempts"]) == 1 and result["failed_attempts"][0]["opening_id"] == "OP004"
    assert validate(result, evidence_path=evidence, gemini_base=gemini, openai_base=openai) == result


@pytest.mark.parametrize(
    "attack",
    [
        "parsed_empty",
        "image_binding",
        "raw_hash",
        "forced_inclusion",
        "hardcoded_disagreement",
        "promotion",
        "retry_misuse",
    ],
)
def test_result_and_bundle_attacks_fail(tmp_path, attack):
    evidence, gemini, openai = _inputs(tmp_path)
    if attack in {"parsed_empty", "image_binding", "raw_hash", "retry_misuse"}:
        path = openai / "OP004/retry-result.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if attack == "parsed_empty":
            value["parsed"] = {}
        elif attack == "image_binding":
            value["image_bindings"].reverse()
        elif attack == "raw_hash":
            value["raw_response_sha256"] = "0" * 64
        else:
            value["usable_advisory"] = False
        path.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ValueError):
            build(evidence_path=evidence, gemini_base=gemini, openai_base=openai)
        return
    result = deepcopy(build(evidence_path=evidence, gemini_base=gemini, openai_base=openai))
    if attack == "forced_inclusion":
        result["included_for_xy_experiment"].append("OP011")
    elif attack == "hardcoded_disagreement":
        result["cue_disagreements_by_opening"] = {}
    else:
        result["build_authorized"] = True
    result["candidate_hash"] = _canon({key: value for key, value in result.items() if key != "candidate_hash"})
    with pytest.raises(ValueError):
        validate(result, evidence_path=evidence, gemini_base=gemini, openai_base=openai)
