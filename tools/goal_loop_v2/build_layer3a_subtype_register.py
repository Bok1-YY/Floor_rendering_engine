"""Build the complete Layer3A visual-subtype coverage register."""
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
from tools.goal_loop_v2.build_clean_subtype_bundle import validate as validate_generic_bundle
from tools.goal_loop_v2.build_op002_clean_subtype_bundle import validate as validate_op002_bundle

OUT = ROOT / "reports/layer3a_subtype_register_20260903"
OPENING_IDS = ("OP001", "OP002", "OP003", "OP004", "OP006", "OP007", "OP008", "OP009", "OP010")
EXCLUDED_IDS = ("OP005", "OP011", "PORTAL-WB011-WB006-01", "OP012")
GENERIC_DIRS = {
    opening_id: ROOT / f"reports/{opening_id.lower()}_clean_subtype_20260903"
    for opening_id in OPENING_IDS
    if opening_id != "OP002"
}
OP002_DIR = ROOT / "reports/op002_clean_subtype_20260903"
DISPOSITIONS = {
    "OP001": {
        "downstream_use_status": "accepted_with_quarantine",
        "quarantine": ["entry_label", "building_exterior_root", "unit_root", "traversability", "adjacency"],
    },
    "OP002": {
        "downstream_use_status": "accepted_with_quarantine",
        "quarantine": ["vertical_parameters", "effective_void", "traversability", "adjacency"],
    },
    "OP003": {
        "downstream_use_status": "accepted_with_quarantine",
        "quarantine": ["return_wall", "room_pair", "traversability", "adjacency"],
    },
    "OP004": {
        "downstream_use_status": "accepted_with_quarantine",
        "quarantine": ["vertical_parameters", "room_pair", "traversability", "adjacency"],
    },
    "OP006": {
        "downstream_use_status": "needs_tighter_crop",
        "quarantine": ["neighboring_swing_arc", "endpoint_context", "traversability", "adjacency"],
    },
    "OP007": {
        "downstream_use_status": "accepted_with_quarantine",
        "quarantine": ["public_wc_semantics", "room_pair", "traversability", "adjacency"],
    },
    "OP008": {
        "downstream_use_status": "needs_tighter_crop",
        "quarantine": ["neighboring_swing_arc", "room_labels", "return_wall", "room_pair"],
    },
    "OP009": {
        "downstream_use_status": "accepted_with_quarantine",
        "quarantine": ["sliding_operation", "access_traversability", "room_pair", "adjacency"],
    },
    "OP010": {
        "downstream_use_status": "accepted_with_quarantine",
        "quarantine": ["broad_balcony_interface", "exterior_root", "traversability", "adjacency"],
    },
}
FAIL_CLOSED = (
    "source_subtype_confirmation",
    "effective_void_confirmation",
    "source_vertical_confirmation",
    "traversability_confirmation",
    "pair_confirmation",
    "adjacency_confirmation",
    "root_confirmation",
    "source_correction_authorized",
    "semantic_promotion",
    "vertical_entry_authorized",
    "build_authorized",
    "ready",
)


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _normalize_visual_candidate(opening_id: str, visual_kind: str) -> dict[str, Any]:
    if visual_kind == "glazed_interface_or_sliding_access":
        return {
            "visual_kind_family": "glazed_interface",
            "sliding_access_operation": "unconfirmed",
            "access_traversability": "unconfirmed",
        }
    if visual_kind == "door":
        return {
            "visual_kind_family": "door_like",
            "door_operation": "visual_cue_only",
            "access_traversability": "unconfirmed",
        }
    return {
        "visual_kind_family": visual_kind,
        "access_traversability": "unconfirmed",
    }


def _load_row(opening_id: str) -> dict[str, Any]:
    if opening_id == "OP002":
        directory = OP002_DIR
        bundle_path = directory / "bundle.json"
        selected_path = directory / "selected-result.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        validate_op002_bundle(bundle, result_path=selected_path)
        accepted = bundle["accepted_for_layer3a_op002_visual_subtype_research"]
    else:
        directory = GENERIC_DIRS[opening_id]
        bundle_path = directory / "bundle.json"
        selected_path = directory / "selected-result.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        validate_generic_bundle(
            bundle,
            opening_id,
            result_path=selected_path,
        )
        accepted = bundle["accepted_for_layer3a_visual_subtype_research"]
    selected = bundle["selected_result"]
    parsed = selected["parsed"]
    disposition = deepcopy(DISPOSITIONS[opening_id])
    return {
        "opening_id": opening_id,
        "bundle_schema": bundle["schema"],
        "bundle_file_sha256": _file_hash(bundle_path),
        "bundle_candidate_hash": bundle["candidate_hash"],
        "selected_result_file_sha256": _file_hash(selected_path),
        "selected_raw_response_sha256": selected["raw_response_sha256"],
        "selected_review_cost_usd": bundle["selected_review_cost_usd"],
        "parsed_visual_advisory": deepcopy(parsed),
        "visual_subtype_candidate": bundle["visual_subtype_candidate"],
        "normalized_visual_candidate": _normalize_visual_candidate(
            opening_id,
            bundle["visual_subtype_candidate"],
        ),
        "bundle_accepted_for_layer3a_visual_subtype_research": bool(accepted),
        **disposition,
        "explicit_unresolved": disposition["downstream_use_status"] == "needs_tighter_crop",
        "vertical_parameters_reviewed": False,
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
    }


def build(*, _skip_validate: bool = False) -> dict[str, Any]:
    rows = [_load_row(opening_id) for opening_id in OPENING_IDS]
    unresolved = [
        row["opening_id"]
        for row in rows
        if row["explicit_unresolved"]
    ]
    downstream_accepted = [
        row["opening_id"]
        for row in rows
        if row["downstream_use_status"] == "accepted_with_quarantine"
    ]
    result = {
        "schema": "layer3a-visual-subtype-register-v1",
        "opening_ids": list(OPENING_IDS),
        "excluded_opening_ids": list(EXCLUDED_IDS),
        "rows": rows,
        "coverage_count": len(rows),
        "research_bundle_acceptance_count": sum(
            row["bundle_accepted_for_layer3a_visual_subtype_research"]
            for row in rows
        ),
        "downstream_accepted_with_quarantine_ids": downstream_accepted,
        "explicit_unresolved_ids": unresolved,
        "layer3a_visual_advisory_coverage_complete": len(rows) == len(OPENING_IDS),
        "all_subtypes_source_confirmed": False,
        "all_subtypes_downstream_ready": False,
        "batch_gate_status": "coverage_complete_with_explicit_unresolved",
        "selected_review_cost_usd": round(
            sum(float(row["selected_review_cost_usd"]) for row in rows),
            10,
        ),
        "failed_attempts_preserved_where_present": True,
        "source_subtype_confirmation": False,
        "effective_void_confirmation": False,
        "source_vertical_confirmation": False,
        "traversability_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
        "root_confirmation": False,
        "source_correction_authorized": False,
        "semantic_promotion": False,
        "vertical_entry_authorized": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _candidate_hash(
        {key: value for key, value in result.items() if key != "candidate_hash"}
    )
    return result if _skip_validate else validate(result)


def validate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(_skip_validate=True)
    if actual != expected:
        raise ValueError("Layer3A subtype register evidence/derivation drift")
    if (
        actual.get("schema") != "layer3a-visual-subtype-register-v1"
        or actual.get("opening_ids") != list(OPENING_IDS)
        or actual.get("excluded_opening_ids") != list(EXCLUDED_IDS)
        or actual.get("coverage_count") != 9
        or actual.get("explicit_unresolved_ids") != ["OP006", "OP008"]
        or actual.get("layer3a_visual_advisory_coverage_complete") is not True
        or actual.get("all_subtypes_source_confirmed") is not False
        or actual.get("all_subtypes_downstream_ready") is not False
        or actual.get("batch_gate_status") != "coverage_complete_with_explicit_unresolved"
    ):
        raise ValueError("Layer3A subtype register scope/coverage drift")
    for key in FAIL_CLOSED:
        if actual.get(key) is not False:
            raise ValueError(f"Layer3A subtype register promoted {key}")
    if actual.get("score_effect") != "none":
        raise ValueError("Layer3A subtype register score drift")
    for row in actual["rows"]:
        if row.get("downstream_use_status") not in {"accepted_with_quarantine", "needs_tighter_crop"}:
            raise ValueError("Layer3A row downstream status enum drift")
        if row.get("explicit_unresolved") is not (
            row["downstream_use_status"] == "needs_tighter_crop"
        ):
            raise ValueError("Layer3A row unresolved/status drift")
        for key in (
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
        ):
            if row.get(key) is not False:
                raise ValueError(f"Layer3A row promoted {key}")
        if row.get("vertical_parameters_reviewed") is not False or row.get("score_effect") != "none":
            raise ValueError("Layer3A row vertical/score drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("Layer3A subtype register candidate hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT / "register.json")
    args = parser.parse_args(argv)
    result = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output.parent / "REPORT.md").write_text(
        "# Layer3A visual subtype register v1\n\n"
        "All nine admitted XY openings now have independent raw-first visual-subtype advisory bundles. Seven are "
        "accepted for downstream research only with explicit quarantine; OP006 and OP008 remain explicit unresolved "
        "items requiring tighter crops before downstream subtype use. OP009 is normalized to the glazed-interface "
        "family with sliding operation and access traversability unconfirmed. Coverage complete does not mean source "
        "subtypes confirmed, downstream-ready openings, vertical authorization, score change, or formal build.\n",
        encoding="utf-8",
    )
    (args.output.parent / "REVIEW_CARD_ZH.md").write_text(
        "# Layer3A 视觉类型覆盖表 v1\n\n"
        "九个纳入的 XY 候选均已完成独立 raw-first 视觉审查。七个只能在隔离条件下用于后续研究；OP006 和 "
        "OP008 因邻近摆弧污染需 tighter crop，明确保持 unresolved。OP009 归入 glazed-interface 视觉家族，"
        "滑动操作和通行均未确认。覆盖完成不等于源类型确认、垂直授权、评分提升或正式建模。\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_candidate_hash", "OPENING_IDS", "EXCLUDED_IDS", "DISPOSITIONS"]
