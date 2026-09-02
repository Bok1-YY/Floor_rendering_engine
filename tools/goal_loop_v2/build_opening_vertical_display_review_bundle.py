"""Bind a generic Gemini no-cut vertical-display review."""
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
from tools.goal_loop_v2.build_opening_vertical_display_evidence import default_inputs, validate as validate_evidence
from tools.goal_loop_v2.fal_opening_vertical_display_review import parse

FAIL_CLOSED = (
    "source_vertical_confirmation",
    "source_subtype_confirmation",
    "effective_void_confirmation",
    "traversability_confirmation",
    "pair_confirmation",
    "adjacency_confirmation",
    "root_confirmation",
    "source_correction_authorized",
    "semantic_promotion",
    "build_authorized",
    "ready",
)


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _assert_false(value: Mapping[str, Any], context: str) -> None:
    for key in FAIL_CLOSED:
        if value.get(key) is not False:
            raise ValueError(f"{context} promoted {key}")
    if value.get("score_effect") != "none":
        raise ValueError(f"{context} score drift")


def build(
    opening_id: str,
    *,
    evidence_path: Path,
    result_path: Path,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    evidence_path, result_path = Path(evidence_path), Path(result_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    validate_evidence(evidence, opening_id, out_dir=evidence_path.parent, **default_inputs(opening_id))
    selected = json.loads(result_path.read_text(encoding="utf-8"))
    _assert_false(selected, "selected vertical display result")
    if (
        selected.get("schema") != "fal-opening-vertical-display-review-v1"
        or selected.get("opening_id") != opening_id
        or selected.get("model") != "google/gemini-2.5-flash"
        or selected.get("http_status") != 200
        or selected.get("usable_advisory") is not True
        or selected.get("validation_error") is not None
        or selected.get("transport_error") is not None
        or selected.get("evidence_file_sha256") != _file_hash(evidence_path)
        or selected.get("evidence_candidate_hash") != evidence["candidate_hash"]
        or selected.get("display_plan_candidate_hash") != evidence["display_plan_candidate_hash"]
        or selected.get("display_manifest_file_sha256") != evidence["display_manifest_file_sha256"]
        or selected.get("display_clarity_advisory_only") is not True
    ):
        raise ValueError("selected vertical display result identity/evidence drift")
    raw_response = selected["raw_response"]
    if selected.get("raw_response_sha256") != _candidate_hash(raw_response):
        raise ValueError("selected vertical display raw hash drift")
    parsed = parse(raw_response["choices"][0]["message"]["content"], opening_id)
    if parsed != selected.get("parsed"):
        raise ValueError("selected vertical display parsed/raw drift")
    cost = (selected.get("usage") or {}).get("cost")
    if not isinstance(cost, (int, float)) or cost < 0:
        raise ValueError("selected vertical display cost missing")
    accepted = (
        parsed["intact_wall_baseline_visible"] == "yes"
        and parsed["blue_xy_locator_visible"] == "yes"
        and parsed["orange_unbound_head_guide_visible"] == "yes"
        and parsed["guides_visually_distinct_from_wall"] == "yes"
        and parsed["floor_to_head_opening_cut_visible"] == "no"
        and parsed["door_leaf_threshold_or_sill_geometry_visible"] == "no"
        and parsed["labels_state_unbound_head_and_unknown_sill"] == "yes"
        and parsed["display_misleading_as_confirmed_opening"] == "no"
        and parsed["recommendation"] == "accept_research_display"
    )
    result = {
        "schema": "opening-layer3b-display-review-bundle-v1",
        "opening_id": opening_id,
        "evidence_file_sha256": _file_hash(evidence_path),
        "evidence_candidate_hash": evidence["candidate_hash"],
        "display_plan_candidate_hash": evidence["display_plan_candidate_hash"],
        "display_manifest_file_sha256": evidence["display_manifest_file_sha256"],
        "selected_result": {
            "result_file_sha256": _file_hash(result_path),
            "raw_response_sha256": selected["raw_response_sha256"],
            "parsed": parsed,
            "accepted_for_layer3b_research_display": accepted,
            "cost_usd": float(cost),
            "canonical_result": selected,
        },
        "accepted_for_layer3b_research_display": accepted,
        "selected_review_cost_usd": float(cost),
        "review_scope": "unbound_guide_display_clarity_only",
        **{key: False for key in FAIL_CLOSED},
        "score_effect": "none",
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _candidate_hash({key: value for key, value in result.items() if key != "candidate_hash"})
    return result if _skip_validate else validate(result, opening_id, evidence_path=evidence_path, result_path=result_path)


def validate(candidate: Mapping[str, Any], opening_id: str, *, evidence_path: Path, result_path: Path) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(opening_id, evidence_path=Path(evidence_path), result_path=Path(result_path), _skip_validate=True)
    if actual != expected:
        raise ValueError("vertical display review bundle evidence/derivation drift")
    _assert_false(actual, "vertical display review bundle")
    if (
        actual.get("opening_id") != opening_id
        or actual.get("accepted_for_layer3b_research_display") is not True
        or actual.get("review_scope") != "unbound_guide_display_clarity_only"
    ):
        raise ValueError("vertical display review bundle scope drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("vertical display review bundle hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opening-id", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    selected_path = args.out / "selected-result.json"
    selected_path.write_text(json.dumps(json.loads(args.result.read_text(encoding="utf-8")), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = build(args.opening_id, evidence_path=args.evidence, result_path=selected_path)
    (args.out / "bundle.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out / "REPORT.md").write_text(
        f"# {args.opening_id} Layer3B display review\n\n"
        "Gemini accepted only the clarity of the intact-wall/unbound-guide research display. No source vertical, "
        "effective void, subtype, room pair, traversability, adjacency, root, score, or formal build is confirmed.\n",
        encoding="utf-8",
    )
    (args.out / "REVIEW_CARD_ZH.md").write_text(
        f"# {args.opening_id} Layer3B 显示审查\n\n"
        "Gemini 只接受完整墙体与未绑定 guide 的研究显示清晰度；不确认源垂直参数、有效洞口、类型、房间对、"
        "通行、邻接、root、评分或正式建模。\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_candidate_hash"]
