"""Bind the selected Gemini combined-gap review without promoting source semantics."""
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
from tools.goal_loop_v2.build_combined_gap_registered_evidence import (
    EXPECTED_IDS,
    validate as validate_evidence,
)
from tools.goal_loop_v2.fal_combined_gap_registered_review import parse

EVIDENCE = ROOT / "reports/combined_gap_registered_evidence_20260903/evidence.json"
OUT = ROOT / "reports/combined_gap_registered_review_20260903"
SELECTED_RESULT = OUT / "selected-result.json"
EXTERNAL_RESULT = Path(
    r"C:/Users/1_1/Desktop/goal_loop_v2_1308_combined_registered_gemini_20260903/result.json"
)
FAIL_CLOSED = (
    "source_correction_authorized",
    "xy_experiment_confirmation",
    "cut_confirmation",
    "pair_confirmation",
    "adjacency_confirmation",
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


def _expected_image_bindings(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    specifications = (
        ("full_registered_composite", evidence["full_plan"]["composite"]),
        ("nine_gap_contact_sheet", evidence["contact_sheet"]),
        ("full_registered_source_clean", evidence["full_plan"]["registered_source"]),
        ("combined_model_top_clean", evidence["full_plan"]["combined_model_top"]),
    )
    return [
        {
            "role": role,
            "filename": Path(artifact.get("relative_path") or artifact.get("path")).name,
            "bytes": artifact["bytes"],
            "sha256": artifact["sha256"],
        }
        for role, artifact in specifications
    ]


def _validate_result(
    result_path: Path,
    value: Mapping[str, Any],
    evidence: Mapping[str, Any],
    evidence_file_sha256: str,
) -> dict[str, Any]:
    result = deepcopy(dict(value))
    _assert_fail_closed(result, context="combined Gemini result")
    if (
        result.get("schema") != "fal-combined-gap-registered-review-v1"
        or result.get("model") != "google/gemini-2.5-flash"
        or result.get("http_status") != 200
        or result.get("usable_advisory") is not True
        or result.get("validation_error") is not None
        or result.get("transport_error") is not None
        or result.get("evidence_file_sha256") != evidence_file_sha256
        or result.get("evidence_candidate_hash") != evidence["candidate_hash"]
        or result.get("combined_plan_candidate_hash") != evidence["plan_candidate_hash"]
        or result.get("combined_manifest_file_sha256") != evidence["combined_manifest_file_sha256"]
        or result.get("image_bindings") != _expected_image_bindings(evidence)
    ):
        raise ValueError("combined Gemini result identity/evidence drift")
    raw_response = result.get("raw_response")
    if not isinstance(raw_response, dict) or result.get("raw_response_sha256") != _candidate_hash(raw_response):
        raise ValueError("combined Gemini raw response hash drift")
    parsed = parse(raw_response["choices"][0]["message"]["content"])
    if parsed != result.get("parsed"):
        raise ValueError("combined Gemini parsed/raw drift")
    cost = (result.get("usage") or {}).get("cost")
    if not isinstance(cost, (int, float)) or cost < 0:
        raise ValueError("combined Gemini review cost missing")
    accepted = (
        parsed["full_plan_registration_readable"] == "yes"
        and parsed["global_wall_alignment_plausible"] == "yes"
        and parsed["all_nine_candidate_ids_locatable"] == "yes"
        and parsed["unexpected_extra_full_height_gap_visible"] == "no"
        and parsed["combined_xy_visual_result_valid"] == "yes"
        and parsed["recommendation"] == "accept_combined_xy_research"
        and [row["opening_id"] for row in parsed["per_opening"]] == list(EXPECTED_IDS)
        and all(
            row["model_gap_centered_on_visible_source_opening"] == "yes"
            and row["model_gap_width_matches_source_xy"] == "yes"
            and row["neighboring_wall_or_junction_obstruction"] == "no"
            for row in parsed["per_opening"]
        )
    )
    return {
        "result_file_sha256": _file_hash(result_path),
        "raw_response_sha256": result["raw_response_sha256"],
        "parsed": parsed,
        "accepted_for_combined_xy_research": accepted,
        "cost_usd": float(cost),
        "canonical_result": result,
    }


def build(
    *,
    evidence_path: Path = EVIDENCE,
    result_path: Path = SELECTED_RESULT,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    evidence_path = Path(evidence_path)
    result_path = Path(result_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    validate_evidence(evidence, out_dir=evidence_path.parent, rebuild=False)
    evidence_file_sha256 = _file_hash(evidence_path)
    selected = _validate_result(
        result_path,
        json.loads(result_path.read_text(encoding="utf-8")),
        evidence,
        evidence_file_sha256,
    )
    result = {
        "schema": "combined-gap-registered-review-bundle-v1",
        "evidence_file_sha256": evidence_file_sha256,
        "evidence_candidate_hash": evidence["candidate_hash"],
        "plan_candidate_hash": evidence["plan_candidate_hash"],
        "combined_manifest_file_sha256": evidence["combined_manifest_file_sha256"],
        "combined_validation_file_sha256": evidence["combined_validation_file_sha256"],
        "opening_ids": list(EXPECTED_IDS),
        "selected_result": selected,
        "accepted_for_combined_xy_research": selected["accepted_for_combined_xy_research"],
        "selected_review_cost_usd": selected["cost_usd"],
        "review_scope": "combined_xy_visual_research_only",
        "source_correction_authorized": False,
        "xy_experiment_confirmation": False,
        "cut_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
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
        result_path=result_path,
    )


def validate(
    candidate: Mapping[str, Any],
    *,
    evidence_path: Path = EVIDENCE,
    result_path: Path = SELECTED_RESULT,
) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(
        evidence_path=Path(evidence_path),
        result_path=Path(result_path),
        _skip_validate=True,
    )
    if actual != expected:
        raise ValueError("combined registered review bundle evidence/derivation drift")
    _assert_fail_closed(actual, context="combined registered review bundle")
    if (
        actual.get("schema") != "combined-gap-registered-review-bundle-v1"
        or actual.get("opening_ids") != list(EXPECTED_IDS)
        or actual.get("review_scope") != "combined_xy_visual_research_only"
        or actual.get("accepted_for_combined_xy_research") is not True
    ):
        raise ValueError("combined registered review bundle scope/acceptance drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("combined registered review bundle candidate hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    parser.add_argument("--result", type=Path, default=EXTERNAL_RESULT)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    selected_path = args.out / "selected-result.json"
    selected_value = json.loads(args.result.read_text(encoding="utf-8"))
    selected_path.write_text(
        json.dumps(selected_value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result = build(evidence_path=args.evidence, result_path=selected_path)
    bundle_path = args.out / "bundle.json"
    bundle_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.out / "REPORT.md").write_text(
        "# Combined registered Gemini review bundle v1\n\n"
        "Gemini accepted the single combined 43-piece model for XY visual research: the full-plan registration was "
        "readable, global wall alignment was plausible, all nine IDs were locatable, every local gap matched center "
        "and width without neighboring obstruction, and no unexpected extra full-height gap was reported. This is "
        "advisory visual acceptance only and does not confirm type, Z, rooms, traversability, adjacency, source "
        "correction, score, or formal Blender/IFC build readiness.\n",
        encoding="utf-8",
    )
    (args.out / "REVIEW_CARD_ZH.md").write_text(
        "# 组合模型 Gemini 复合审查\n\n"
        "Gemini 对同一 43 段墙体模型的全景同窗和九个局部同窗给出 XY 研究接受建议：九处中心与宽度均匹配，"
        "未见邻墙遮挡或意外额外缺口。该结论只说明二维可视化候选通过复合图审查，不确认门窗类型、洞口高度、"
        "房间、通行、邻接、源修订、评分或正式施工。\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_candidate_hash"]
