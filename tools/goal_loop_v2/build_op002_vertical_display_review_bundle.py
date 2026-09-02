"""Bind the failed-first and selected-retry Gemini Layer3B display reviews."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.goal_loop_v2.build_op002_vertical_display_evidence import validate as validate_evidence
from tools.goal_loop_v2.fal_op002_vertical_display_review import parse

EVIDENCE = ROOT / "reports/op002_vertical_display_evidence_20260903/evidence.json"
OUT = ROOT / "reports/op002_vertical_display_review_20260903"
SELECTED_RESULT = OUT / "selected-result.json"
HISTORICAL_RESULT = OUT / "historical-schema-rejected-result.json"
EXTERNAL_SELECTED = Path(
    r"C:/Users/1_1/Desktop/goal_loop_v2_1308_op002_layer3b_display_gemini_20260903/retry-result.json"
)
EXTERNAL_HISTORICAL = Path(
    r"C:/Users/1_1/Desktop/goal_loop_v2_1308_op002_layer3b_display_gemini_20260903/result.json"
)
FAIL_CLOSED = (
    "source_vertical_confirmation",
    "source_subtype_confirmation",
    "effective_void_confirmation",
    "traversability_confirmation",
    "adjacency_confirmation",
    "source_correction_authorized",
    "semantic_promotion",
    "build_authorized",
    "ready",
)


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _assert_fail_closed(value: Mapping[str, Any], *, context: str) -> None:
    for key in FAIL_CLOSED:
        if value.get(key) is not False:
            raise ValueError(f"{context} promoted or omitted {key}")
    if value.get("score_effect") != "none":
        raise ValueError(f"{context} score drift")


def _validate_common(
    path: Path,
    result: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    value = deepcopy(dict(result))
    _assert_fail_closed(value, context=path.name)
    if (
        value.get("schema") != "fal-op002-layer3b-display-review-v1"
        or value.get("opening_id") != "OP002"
        or value.get("model") != "google/gemini-2.5-flash"
        or value.get("http_status") != 200
        or value.get("evidence_file_sha256") != _file_hash(EVIDENCE)
        or value.get("evidence_candidate_hash") != evidence["candidate_hash"]
        or value.get("display_plan_candidate_hash") != evidence["display_plan_candidate_hash"]
        or value.get("display_manifest_file_sha256") != evidence["display_manifest_file_sha256"]
        or value.get("display_clarity_advisory_only") is not True
    ):
        raise ValueError("Layer3B Gemini result identity/evidence drift")
    raw_response = value.get("raw_response")
    if not isinstance(raw_response, dict) or value.get("raw_response_sha256") != _candidate_hash(raw_response):
        raise ValueError("Layer3B Gemini raw response hash drift")
    cost = (value.get("usage") or {}).get("cost")
    if not isinstance(cost, (int, float)) or cost < 0:
        raise ValueError("Layer3B Gemini result cost missing")
    return {
        "result_file_sha256": _file_hash(path),
        "raw_response_sha256": value["raw_response_sha256"],
        "cost_usd": float(cost),
        "canonical_result": value,
    }


def _validate_selected(
    path: Path,
    result: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    selected = _validate_common(path, result, evidence)
    value = selected["canonical_result"]
    if (
        value.get("usable_advisory") is not True
        or value.get("validation_error") is not None
        or value.get("transport_error") is not None
    ):
        raise ValueError("selected Layer3B Gemini result is unusable")
    parsed = parse(value["raw_response"]["choices"][0]["message"]["content"])
    if parsed != value.get("parsed"):
        raise ValueError("selected Layer3B Gemini parsed/raw drift")
    accepted = (
        parsed["intact_wall_baseline_visible"] == "yes"
        and parsed["blue_xy_locator_visible"] == "yes"
        and parsed["orange_head_assumption_guide_visible"] == "yes"
        and parsed["guides_visually_distinct_from_wall"] == "yes"
        and parsed["floor_to_head_opening_cut_visible"] == "no"
        and parsed["door_leaf_threshold_or_sill_geometry_visible"] == "no"
        and parsed["display_labels_state_assumptions_and_unknown_sill"] == "yes"
        and parsed["display_misleading_as_confirmed_opening"] == "no"
        and parsed["recommendation"] == "accept_layer3b_research_display"
    )
    return {
        **selected,
        "parsed": parsed,
        "accepted_for_layer3b_research_display": accepted,
    }


def _validate_historical(
    path: Path,
    result: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    historical = _validate_common(path, result, evidence)
    value = historical["canonical_result"]
    raw_content = value["raw_response"]["choices"][0]["message"]["content"]
    raw_value = json.loads(raw_content)
    if (
        value.get("usable_advisory") is not False
        or value.get("parsed") is not None
        or value.get("validation_error") != "OP002 Layer3B display review schema/id mismatch"
        or value.get("transport_error") is not None
        or raw_value.get("opening_id") != "OP002 Layer3B"
        or set(raw_value) != {
            "opening_id",
            "intact_wall_baseline_visible",
            "blue_xy_locator_visible",
            "orange_head_assumption_guide_visible",
            "guides_visually_distinct_from_wall",
            "floor_to_head_opening_cut_visible",
            "door_leaf_threshold_or_sill_geometry_visible",
            "display_labels_state_assumptions_and_unknown_sill",
            "display_misleading_as_confirmed_opening",
            "recommendation",
            "confidence",
        }
    ):
        raise ValueError("historical Layer3B schema rejection drift")
    return {
        **historical,
        "rejection_reason": value["validation_error"],
        "raw_opening_id": raw_value["opening_id"],
        "usable_advisory": False,
    }


def build(
    *,
    evidence_path: Path = EVIDENCE,
    selected_path: Path = SELECTED_RESULT,
    historical_path: Path = HISTORICAL_RESULT,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    evidence_path = Path(evidence_path)
    selected_path = Path(selected_path)
    historical_path = Path(historical_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    validate_evidence(evidence, out_dir=evidence_path.parent)
    selected = _validate_selected(
        selected_path,
        json.loads(selected_path.read_text(encoding="utf-8")),
        evidence,
    )
    historical = _validate_historical(
        historical_path,
        json.loads(historical_path.read_text(encoding="utf-8")),
        evidence,
    )
    result = {
        "schema": "op002-layer3b-display-review-bundle-v1",
        "opening_id": "OP002",
        "evidence_file_sha256": _file_hash(evidence_path),
        "evidence_candidate_hash": evidence["candidate_hash"],
        "display_plan_candidate_hash": evidence["display_plan_candidate_hash"],
        "display_manifest_file_sha256": evidence["display_manifest_file_sha256"],
        "historical_schema_rejected_result": historical,
        "selected_result": selected,
        "historical_failure_preserved": True,
        "accepted_for_layer3b_research_display": selected["accepted_for_layer3b_research_display"],
        "selected_review_cost_usd": selected["cost_usd"],
        "total_review_cost_usd": round(selected["cost_usd"] + historical["cost_usd"], 10),
        "review_scope": "display_clarity_and_nonmisleading_research_presentation_only",
        "source_vertical_confirmation": False,
        "source_subtype_confirmation": False,
        "effective_void_confirmation": False,
        "traversability_confirmation": False,
        "adjacency_confirmation": False,
        "source_correction_authorized": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _candidate_hash(
        {key: value for key, value in result.items() if key != "candidate_hash"}
    )
    return result if _skip_validate else validate(
        result,
        evidence_path=evidence_path,
        selected_path=selected_path,
        historical_path=historical_path,
    )


def validate(
    candidate: Mapping[str, Any],
    *,
    evidence_path: Path = EVIDENCE,
    selected_path: Path = SELECTED_RESULT,
    historical_path: Path = HISTORICAL_RESULT,
) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(
        evidence_path=Path(evidence_path),
        selected_path=Path(selected_path),
        historical_path=Path(historical_path),
        _skip_validate=True,
    )
    if actual != expected:
        raise ValueError("Layer3B display review bundle evidence/derivation drift")
    _assert_fail_closed(actual, context="Layer3B display review bundle")
    if (
        actual.get("schema") != "op002-layer3b-display-review-bundle-v1"
        or actual.get("historical_failure_preserved") is not True
        or actual.get("accepted_for_layer3b_research_display") is not True
        or actual.get("review_scope") != "display_clarity_and_nonmisleading_research_presentation_only"
    ):
        raise ValueError("Layer3B display review bundle scope drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("Layer3B display review bundle candidate hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    parser.add_argument("--selected", type=Path, default=EXTERNAL_SELECTED)
    parser.add_argument("--historical", type=Path, default=EXTERNAL_HISTORICAL)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    selected_path = args.out / "selected-result.json"
    historical_path = args.out / "historical-schema-rejected-result.json"
    selected_path.write_text(
        json.dumps(json.loads(args.selected.read_text(encoding="utf-8")), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    historical_path.write_text(
        json.dumps(json.loads(args.historical.read_text(encoding="utf-8")), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result = build(
        evidence_path=args.evidence,
        selected_path=selected_path,
        historical_path=historical_path,
    )
    (args.out / "bundle.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.out / "REPORT.md").write_text(
        "# OP002 Layer3B Gemini display review bundle v1\n\n"
        "The first HTTP-200 response was correctly rejected because it returned opening_id='OP002 Layer3B' instead of "
        "the exact OP002 schema value. The failed result and cost are preserved. After changing the request schema to "
        "a single-value enum and explicitly forbidding an ID suffix, the retry passed: intact wall, blue XY locator, "
        "orange head-assumption guide, no floor-to-head cut, no door/sill/threshold geometry, clear assumption labels, "
        "and no misleading confirmed-opening presentation. Acceptance is display clarity only.\n",
        encoding="utf-8",
    )
    (args.out / "REVIEW_CARD_ZH.md").write_text(
        "# OP002 Layer3B Gemini 显示审查\n\n"
        "第一次 HTTP 200 响应因 opening_id 错写为 OP002 Layer3B 而被严格拒绝，失败记录和费用均保留。"
        "修正单值 enum 与提示后，重试确认：墙体完整，蓝色 XY locator 与橙色 head 假设标尺清楚，无落地切洞、"
        "门扇、门槛或 sill 几何，标签明确且不会冒充已确认开口。该接受仅针对研究显示的清晰度。\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_candidate_hash"]
