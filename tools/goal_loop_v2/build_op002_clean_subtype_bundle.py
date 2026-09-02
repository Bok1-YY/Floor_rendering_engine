"""Bind the selected OP002 clean-crop Gemini subtype advisory."""
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
from tools.goal_loop_v2.build_opening_xy_clean_evidence import validate as validate_evidence
from tools.goal_loop_v2.fal_op002_clean_subtype_review import parse

EVIDENCE = ROOT / "reports/opening_xy_clean_evidence_20260902/evidence.json"
OUT = ROOT / "reports/op002_clean_subtype_20260903"
SELECTED_RESULT = OUT / "selected-result.json"
EXTERNAL_RESULT = Path(
    r"C:/Users/1_1/Desktop/goal_loop_v2_1308_op002_clean_subtype_gemini_20260903/result.json"
)
FAIL_CLOSED = (
    "source_subtype_confirmation",
    "effective_void_confirmation",
    "traversability_confirmation",
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


def _validate_selected(
    result_path: Path,
    result: Mapping[str, Any],
    evidence: Mapping[str, Any],
    evidence_file_sha256: str,
) -> dict[str, Any]:
    selected = deepcopy(dict(result))
    _assert_fail_closed(selected, context="OP002 selected result")
    row = next(item for item in evidence["openings"] if item["opening_id"] == "OP002")
    if [binding.get("role") for binding in selected.get("image_bindings", [])] != ["raw_crop", "locator"]:
        raise ValueError("OP002 selected result lost raw-first image order")
    if [binding.get("semantic_authority") for binding in selected["image_bindings"]] != [True, False]:
        raise ValueError("OP002 selected result semantic image authority drift")
    if (
        selected.get("schema") != "fal-op002-clean-subtype-review-v2"
        or selected.get("opening_id") != "OP002"
        or selected.get("model") != "google/gemini-2.5-flash"
        or selected.get("http_status") != 200
        or selected.get("usable_advisory") is not True
        or selected.get("validation_error") is not None
        or selected.get("transport_error") is not None
        or selected.get("evidence_file_sha256") != evidence_file_sha256
        or selected.get("evidence_candidate_hash") != evidence["candidate_hash"]
        or selected.get("source_structure_hash") != evidence["source_structure_hash"]
        or selected.get("host_atom_id") != row["host_atom_id"]
        or selected.get("source_segment_m") != row["segment_m"]
        or selected.get("image_bindings") != _expected_bindings(row)
        or selected.get("visual_subtype_candidate_only") is not True
    ):
        raise ValueError("OP002 selected result identity/evidence drift")
    raw_response = selected.get("raw_response")
    if not isinstance(raw_response, dict) or selected.get("raw_response_sha256") != _candidate_hash(raw_response):
        raise ValueError("OP002 selected raw response hash drift")
    parsed = parse(raw_response["choices"][0]["message"]["content"])
    if parsed != selected.get("parsed"):
        raise ValueError("OP002 selected parsed/raw drift")
    cost = (selected.get("usage") or {}).get("cost")
    if not isinstance(cost, (int, float)) or cost < 0:
        raise ValueError("OP002 selected review cost missing")
    accepted = (
        parsed["visual_kind"] == "door"
        and parsed["wall_break_visible"] == "yes"
        and parsed["swing_arc_visible"] == "yes"
        and parsed["sliding_track_visible"] == "no"
        and parsed["confidence"] in {"high", "medium"}
    )
    return {
        "result_file_sha256": _file_hash(result_path),
        "raw_response_sha256": selected["raw_response_sha256"],
        "parsed": parsed,
        "visual_subtype_candidate": parsed["visual_kind"],
        "accepted_for_layer3a_op002_visual_subtype_research": accepted,
        "cost_usd": float(cost),
        "canonical_result": selected,
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
    validate_evidence(evidence, rebuild=False)
    evidence_file_sha256 = _file_hash(evidence_path)
    selected = _validate_selected(
        result_path,
        json.loads(result_path.read_text(encoding="utf-8")),
        evidence,
        evidence_file_sha256,
    )
    result = {
        "schema": "op002-clean-subtype-bundle-v1",
        "opening_id": "OP002",
        "evidence_file_sha256": evidence_file_sha256,
        "evidence_candidate_hash": evidence["candidate_hash"],
        "source_structure_hash": evidence["source_structure_hash"],
        "selected_result": selected,
        "visual_subtype_candidate": selected["visual_subtype_candidate"],
        "accepted_for_layer3a_op002_visual_subtype_research": selected[
            "accepted_for_layer3a_op002_visual_subtype_research"
        ],
        "selected_review_cost_usd": selected["cost_usd"],
        "review_scope": "clean_source_visual_subtype_advisory_only",
        "vertical_parameters_reviewed": False,
        "source_subtype_confirmation": False,
        "effective_void_confirmation": False,
        "traversability_confirmation": False,
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
        raise ValueError("OP002 subtype bundle evidence/derivation drift")
    _assert_fail_closed(actual, context="OP002 subtype bundle")
    if (
        actual.get("schema") != "op002-clean-subtype-bundle-v1"
        or actual.get("opening_id") != "OP002"
        or actual.get("review_scope") != "clean_source_visual_subtype_advisory_only"
        or actual.get("visual_subtype_candidate") != "door"
        or actual.get("accepted_for_layer3a_op002_visual_subtype_research") is not True
        or actual.get("vertical_parameters_reviewed") is not False
    ):
        raise ValueError("OP002 subtype bundle scope drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("OP002 subtype bundle candidate hash drift")
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
    (args.out / "bundle.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.out / "REPORT.md").write_text(
        "# OP002 clean visual subtype pilot\n\n"
        "Gemini classified the byte-exact clean source crop as a visual door candidate with a visible wall break and "
        "swing arc, no sliding-track cue, and high confidence. The locator was navigation-only. This does not confirm "
        "source subtype, effective void, head/sill/Z, rooms, traversability, adjacency, source correction, score, or "
        "formal Blender/IFC build readiness.\n",
        encoding="utf-8",
    )
    (args.out / "REVIEW_CARD_ZH.md").write_text(
        "# OP002 干净原图视觉类型试点\n\n"
        "Gemini 仅依据无标注的原始裁剪图，把 OP002 识别为视觉上的门候选：墙体断开和门扇摆弧可见，"
        "未见滑轨，置信度高。定位图只用于导航。该结果不确认源数据门类型、有效洞口、高度、房间、"
        "通行、邻接、源修订、评分或正式建模。\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_candidate_hash"]
