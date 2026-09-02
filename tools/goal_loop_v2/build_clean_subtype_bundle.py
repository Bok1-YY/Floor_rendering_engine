"""Build a generic fail-closed clean-crop visual-subtype advisory bundle."""
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
from tools.goal_loop_v2.build_opening_xy_clean_evidence import (
    EXPECTED_INCLUDED,
    validate as validate_evidence,
)
from tools.goal_loop_v2.fal_clean_subtype_review import parse

EVIDENCE = ROOT / "reports/opening_xy_clean_evidence_20260902/evidence.json"
FAIL_CLOSED = (
    "source_subtype_confirmation",
    "effective_void_confirmation",
    "traversability_confirmation",
    "pair_confirmation",
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


def _expected_bindings(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "role": role,
            "semantic_authority": role == "raw_crop",
            "filename": Path(row["artifacts"][role]["path"]).name,
            "bytes": row["artifacts"][role]["bytes"],
            "sha256": row["artifacts"][role]["sha256"],
        }
        for role in ("raw_crop", "locator")
    ]


def _kind_specific_cue(parsed: Mapping[str, Any]) -> bool:
    kind = parsed["visual_kind"]
    if kind == "door":
        return parsed["swing_arc_visible"] == "yes" or parsed["sliding_track_visible"] == "yes"
    if kind in {"window_or_fixed_glazing", "open_passage", "wall_gap"}:
        return parsed["wall_break_visible"] == "yes"
    return False


def _validate_selected(
    result_path: Path,
    result: Mapping[str, Any],
    evidence: Mapping[str, Any],
    opening_id: str,
) -> dict[str, Any]:
    selected = deepcopy(dict(result))
    _assert_fail_closed(selected, context=f"{opening_id} selected result")
    row = next(item for item in evidence["openings"] if item["opening_id"] == opening_id)
    if (
        selected.get("schema") != "fal-clean-subtype-review-v1"
        or selected.get("opening_id") != opening_id
        or selected.get("model") != "google/gemini-2.5-flash"
        or selected.get("http_status") != 200
        or selected.get("usable_advisory") is not True
        or selected.get("validation_error") is not None
        or selected.get("transport_error") is not None
        or selected.get("evidence_file_sha256") != _file_hash(EVIDENCE)
        or selected.get("evidence_candidate_hash") != evidence["candidate_hash"]
        or selected.get("source_structure_hash") != evidence["source_structure_hash"]
        or selected.get("host_atom_id") != row["host_atom_id"]
        or selected.get("source_segment_m") != row["segment_m"]
        or selected.get("image_bindings") != _expected_bindings(row)
        or selected.get("visual_subtype_candidate_only") is not True
        or selected.get("vertical_parameters_reviewed") is not False
    ):
        raise ValueError(f"{opening_id} selected result identity/evidence drift")
    raw_response = selected.get("raw_response")
    if not isinstance(raw_response, dict) or selected.get("raw_response_sha256") != _candidate_hash(raw_response):
        raise ValueError(f"{opening_id} selected raw response hash drift")
    parsed = parse(raw_response["choices"][0]["message"]["content"], opening_id)
    if parsed != selected.get("parsed"):
        raise ValueError(f"{opening_id} selected parsed/raw drift")
    cost = (selected.get("usage") or {}).get("cost")
    if not isinstance(cost, (int, float)) or cost < 0:
        raise ValueError(f"{opening_id} selected review cost missing")
    accepted = (
        parsed["visual_kind"] != "unknown"
        and parsed["wall_break_visible"] == "yes"
        and parsed["confidence"] in {"high", "medium"}
        and _kind_specific_cue(parsed)
    )
    return {
        "result_file_sha256": _file_hash(result_path),
        "raw_response_sha256": selected["raw_response_sha256"],
        "parsed": parsed,
        "visual_subtype_candidate": parsed["visual_kind"],
        "kind_specific_visual_cue_present": _kind_specific_cue(parsed),
        "accepted_for_layer3a_visual_subtype_research": accepted,
        "cost_usd": float(cost),
        "canonical_result": selected,
    }


def build(
    opening_id: str,
    *,
    evidence_path: Path = EVIDENCE,
    result_path: Path,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    if opening_id not in EXPECTED_INCLUDED:
        raise ValueError("opening is not admitted to clean subtype bundle")
    evidence_path = Path(evidence_path)
    result_path = Path(result_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    validate_evidence(evidence, rebuild=False)
    selected = _validate_selected(
        result_path,
        json.loads(result_path.read_text(encoding="utf-8")),
        evidence,
        opening_id,
    )
    result = {
        "schema": "clean-subtype-bundle-v1",
        "opening_id": opening_id,
        "evidence_file_sha256": _file_hash(evidence_path),
        "evidence_candidate_hash": evidence["candidate_hash"],
        "source_structure_hash": evidence["source_structure_hash"],
        "selected_result": selected,
        "visual_subtype_candidate": selected["visual_subtype_candidate"],
        "accepted_for_layer3a_visual_subtype_research": selected[
            "accepted_for_layer3a_visual_subtype_research"
        ],
        "selected_review_cost_usd": selected["cost_usd"],
        "review_scope": "clean_source_visual_subtype_advisory_only",
        "vertical_parameters_reviewed": False,
        "source_subtype_confirmation": False,
        "effective_void_confirmation": False,
        "traversability_confirmation": False,
        "pair_confirmation": False,
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
        opening_id,
        evidence_path=evidence_path,
        result_path=result_path,
    )


def validate(
    candidate: Mapping[str, Any],
    opening_id: str,
    *,
    evidence_path: Path = EVIDENCE,
    result_path: Path,
) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(
        opening_id,
        evidence_path=Path(evidence_path),
        result_path=Path(result_path),
        _skip_validate=True,
    )
    if actual != expected:
        raise ValueError(f"{opening_id} subtype bundle evidence/derivation drift")
    _assert_fail_closed(actual, context=f"{opening_id} subtype bundle")
    if (
        actual.get("schema") != "clean-subtype-bundle-v1"
        or actual.get("opening_id") != opening_id
        or actual.get("review_scope") != "clean_source_visual_subtype_advisory_only"
        or actual.get("accepted_for_layer3a_visual_subtype_research") is not True
        or actual.get("vertical_parameters_reviewed") is not False
    ):
        raise ValueError(f"{opening_id} subtype bundle scope drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError(f"{opening_id} subtype bundle candidate hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opening-id", required=True)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    selected_path = args.out / "selected-result.json"
    selected_value = json.loads(args.result.read_text(encoding="utf-8"))
    selected_path.write_text(
        json.dumps(selected_value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result = build(
        args.opening_id,
        evidence_path=args.evidence,
        result_path=selected_path,
    )
    (args.out / "bundle.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    parsed = result["selected_result"]["parsed"]
    (args.out / "REPORT.md").write_text(
        f"# {args.opening_id} clean visual subtype pilot\n\n"
        f"Gemini classified the byte-exact clean source crop as visual_kind={parsed['visual_kind']} with wall_break="
        f"{parsed['wall_break_visible']}, swing_arc={parsed['swing_arc_visible']}, sliding_track="
        f"{parsed['sliding_track_visible']}, confidence={parsed['confidence']}. The locator was navigation-only. "
        "This is a visual research candidate only and does not confirm source subtype, effective void, vertical "
        "parameters, room pair, traversability, adjacency, source correction, score, or formal build.\n",
        encoding="utf-8",
    )
    (args.out / "REVIEW_CARD_ZH.md").write_text(
        f"# {args.opening_id} 干净原图视觉类型试点\n\n"
        f"Gemini 只依据无标注原始裁剪图给出视觉候选：{parsed['visual_kind']}；墙体断开 "
        f"{parsed['wall_break_visible']}，摆弧 {parsed['swing_arc_visible']}，滑轨 "
        f"{parsed['sliding_track_visible']}，置信度 {parsed['confidence']}。定位图仅作导航。"
        "结果不确认源数据类型、有效洞口、垂直参数、房间对、通行、邻接、源修订、评分或正式建模。\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_candidate_hash"]
