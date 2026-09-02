"""Audit vertical provenance for a Layer3A opening without inventing geometry."""
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
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.build_clean_subtype_bundle import validate as validate_subtype_bundle
from tools.goal_loop_v2.build_opening_xy_clean_evidence import validate as validate_clean_evidence

SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
CLEAN_EVIDENCE = ROOT / "reports/opening_xy_clean_evidence_20260902/evidence.json"
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


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _assert_fail_closed(value: Mapping[str, Any], *, context: str) -> None:
    for key in FAIL_CLOSED:
        if value.get(key) is not False:
            raise ValueError(f"{context} promoted or omitted {key}")
    if value.get("score_effect") != "none":
        raise ValueError(f"{context} score drift")


def build(
    opening_id: str,
    *,
    subtype_bundle_path: Path,
    subtype_result_path: Path,
    source_path: Path = SOURCE,
    clean_evidence_path: Path = CLEAN_EVIDENCE,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    source_path = Path(source_path)
    clean_evidence_path = Path(clean_evidence_path)
    subtype_bundle_path = Path(subtype_bundle_path)
    subtype_result_path = Path(subtype_result_path)
    document = validate_v21_document(_read_json(source_path))
    subtype = _read_json(subtype_bundle_path)
    validate_subtype_bundle(
        subtype,
        opening_id,
        result_path=subtype_result_path,
    )
    if (
        subtype.get("opening_id") != opening_id
        or subtype.get("accepted_for_layer3a_visual_subtype_research") is not True
        or subtype.get("vertical_parameters_reviewed") is not False
        or subtype.get("source_subtype_confirmation") is not False
    ):
        raise ValueError("vertical audit subtype handoff drift")
    clean = _read_json(clean_evidence_path)
    validate_clean_evidence(clean, rebuild=False)
    try:
        opening = next(
            item
            for item in document["opening_contract"]["openings"]
            if item["id"] == opening_id
        )
        clean_row = next(
            item
            for item in clean["openings"]
            if item["opening_id"] == opening_id
        )
        assumption = next(
            item
            for item in document["assumptions"]["items"]
            if item["id"] == "ASSUME-Z-RESEARCH"
        )
    except StopIteration as exc:
        raise ValueError("vertical audit opening/evidence/assumption missing") from exc
    if (
        opening.get("status") != "candidate"
        or opening.get("source_observation", {}).get("status") != "candidate"
        or "ASSUME-Z-RESEARCH" not in opening.get("assumption_ids", [])
        or assumption.get("basis") != "research_default"
        or assumption.get("status") != "unverified"
        or assumption.get("build_policy") != "allow_research_only"
        or assumption.get("value", {}).get("wall_height_m") != 2.8
        or assumption.get("value", {}).get("door_head_m") != 2.1
        or "sill_m" in assumption.get("value", {})
        or clean_row.get("source_pixels_untouched") is not True
        or clean_row.get("matrix_cuttable") is not True
    ):
        raise ValueError("vertical audit source/assumption/XY contract drift")

    effective_void = opening.get("effective_void")
    effective_void_present = isinstance(effective_void, dict)
    source_head = effective_void.get("head_m") if effective_void_present else None
    source_sill = effective_void.get("sill_m") if effective_void_present else None
    source_host = (
        opening.get("host", {}).get("owning_wall_atom_id")
        if isinstance(opening.get("host"), dict)
        else None
    )
    result = {
        "schema": "opening-vertical-provenance-audit-v1",
        "opening_id": opening_id,
        "source_structure_hash": document["structure_hash"],
        "source_document_sha256": _file_hash(source_path),
        "clean_evidence_file_sha256": _file_hash(clean_evidence_path),
        "clean_evidence_candidate_hash": clean["candidate_hash"],
        "subtype_bundle_file_sha256": _file_hash(subtype_bundle_path),
        "subtype_bundle_candidate_hash": subtype["candidate_hash"],
        "subtype_selected_result_file_sha256": _file_hash(subtype_result_path),
        "visual_subtype_candidate": subtype["visual_subtype_candidate"],
        "visual_subtype_advisory_is_vertical_authority": False,
        "opening_candidate_state": {
            "opening_status": opening["status"],
            "source_observation_status": opening["source_observation"]["status"],
            "build_disposition": opening["build_disposition"],
            "build_kind": opening["build_kind"],
            "effective_void_present": effective_void_present,
            "source_host_present": source_host is not None,
            "assumption_ids": deepcopy(opening["assumption_ids"]),
        },
        "xy_research_binding": {
            "host_atom_id": clean_row["host_atom_id"],
            "segment_m": deepcopy(clean_row["segment_m"]),
            "authority": clean_row["authority"],
            "source_pixels_untouched": clean_row["source_pixels_untouched"],
            "vertical_authority": False,
            "effective_void_authority": False,
        },
        "assumption_registry_entry": {
            "id": assumption["id"],
            "category": assumption["category"],
            "basis": assumption["basis"],
            "status": assumption["status"],
            "build_policy": assumption["build_policy"],
            "disclosure": assumption["disclosure"],
            "value": deepcopy(assumption["value"]),
        },
        "vertical_parameters": {
            "wall_height_m": {
                "source_observed_value": None,
                "research_default_value": assumption["value"]["wall_height_m"],
                "provenance_class": "research_assumption",
                "assumption_id": "ASSUME-Z-RESEARCH",
                "source_explicit": False,
                "human_authorized_default": False,
                "usable_for_reversible_research_display": True,
                "eligible_for_source_promotion": False,
            },
            "head_m": {
                "source_observed_value": source_head,
                "research_default_value": assumption["value"]["door_head_m"],
                "provenance_class": (
                    "research_assumption_bound_to_candidate_effective_void"
                    if source_head == assumption["value"]["door_head_m"]
                    else "research_assumption_unbound_to_opening_geometry"
                ),
                "assumption_id": "ASSUME-Z-RESEARCH",
                "source_explicit": False,
                "human_authorized_default": False,
                "usable_for_reversible_research_guide": True,
                "eligible_for_source_promotion": False,
            },
            "sill_m": {
                "source_observed_value": source_sill,
                "research_default_value": None,
                "provenance_class": (
                    "unsupported_candidate_value"
                    if source_sill is not None
                    else "unknown"
                ),
                "assumption_id": None,
                "source_explicit": False,
                "human_authorized_default": False,
                "treatment": "unknown",
                "usable_for_reversible_research_display": False,
                "eligible_for_source_promotion": False,
            },
        },
        "vertical_evidence_present": False,
        "isolated_blender_research_display": {
            "policy_status": "pending_policy_guardian",
            "wall_height_m": assumption["value"]["wall_height_m"],
            "head_guide_m": assumption["value"]["door_head_m"],
            "head_guide_binding": "unbound_research_default",
            "sill_m": None,
            "opening_geometry_authorized": False,
            "must_keep_source_walls_intact": True,
        },
        "source_vertical_confirmation": False,
        "source_subtype_confirmation": False,
        "effective_void_confirmation": False,
        "traversability_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
        "root_confirmation": False,
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
        subtype_bundle_path=subtype_bundle_path,
        subtype_result_path=subtype_result_path,
        source_path=source_path,
        clean_evidence_path=clean_evidence_path,
    )


def validate(
    candidate: Mapping[str, Any],
    opening_id: str,
    *,
    subtype_bundle_path: Path,
    subtype_result_path: Path,
    source_path: Path = SOURCE,
    clean_evidence_path: Path = CLEAN_EVIDENCE,
) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(
        opening_id,
        subtype_bundle_path=Path(subtype_bundle_path),
        subtype_result_path=Path(subtype_result_path),
        source_path=Path(source_path),
        clean_evidence_path=Path(clean_evidence_path),
        _skip_validate=True,
    )
    if actual != expected:
        raise ValueError("opening vertical provenance evidence/derivation drift")
    _assert_fail_closed(actual, context=f"{opening_id} vertical provenance")
    if (
        actual.get("schema") != "opening-vertical-provenance-audit-v1"
        or actual.get("opening_id") != opening_id
        or actual.get("visual_subtype_advisory_is_vertical_authority") is not False
        or actual["vertical_parameters"]["sill_m"]["treatment"] != "unknown"
        or actual["isolated_blender_research_display"]["policy_status"] != "pending_policy_guardian"
        or actual["isolated_blender_research_display"]["opening_geometry_authorized"] is not False
    ):
        raise ValueError("opening vertical provenance scope drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("opening vertical provenance candidate hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opening-id", required=True)
    parser.add_argument("--subtype-bundle", required=True, type=Path)
    parser.add_argument("--subtype-result", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--clean-evidence", type=Path, default=CLEAN_EVIDENCE)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    result = build(
        args.opening_id,
        subtype_bundle_path=args.subtype_bundle,
        subtype_result_path=args.subtype_result,
        source_path=args.source,
        clean_evidence_path=args.clean_evidence,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output.parent / "REPORT.md").write_text(
        f"# {args.opening_id} vertical provenance audit\n\n"
        "The current source opening has no effective-void/head/sill geometry. Wall height 2.8 m is an unverified "
        "research default. Head 2.1 m is only an unbound research-default guide value, not an opening dimension. "
        "Sill is unknown. The Layer3A visual subtype and XY evidence have no vertical authority. No source, effective "
        "void, room pair, traversability, adjacency, score, semantic, or formal-build state is promoted.\n",
        encoding="utf-8",
    )
    (args.output.parent / "REVIEW_CARD_ZH.md").write_text(
        f"# {args.opening_id} 垂直参数来源审计\n\n"
        "当前源 opening 没有 effective void、head 或 sill 几何。墙高 2.8 m 是未验证研究默认；head 2.1 m "
        "只是未绑定到该 opening geometry 的研究标尺值，不是开口尺寸；sill 完全 unknown。Layer3A 视觉类型"
        "和 XY evidence 均无垂直权限，源数据、有效洞口、房间对、通行、邻接、评分和正式建模保持未确认。\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_candidate_hash"]
