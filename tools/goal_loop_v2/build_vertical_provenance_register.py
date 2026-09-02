"""Build the fail-closed Layer3B vertical-provenance register.

The register records what each admitted opening currently says about Z geometry.
It does not authorize an opening cut, a semantic door/window, or a formal build.
"""
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
from tools.goal_loop_v2.build_layer3a_subtype_register import (
    EXCLUDED_IDS,
    OPENING_IDS,
    validate as validate_layer3a,
)
from tools.goal_loop_v2.build_opening_vertical_provenance_audit import (
    validate as validate_generic_vertical_audit,
)
from tools.goal_loop_v2.build_opening_xy_clean_evidence import (
    validate as validate_clean_xy,
)
from tools.goal_loop_v2.build_op002_vertical_provenance_audit import (
    validate as validate_op002_vertical_audit,
)
from tools.goal_loop_v2.op001_unit_scope_candidate import (
    validate_op001_unit_scope_candidate,
)

SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
LAYER3A = ROOT / "reports/layer3a_subtype_register_20260903/register.json"
CLEAN_XY = ROOT / "reports/opening_xy_clean_evidence_20260902/evidence.json"
OP001_SCOPE = ROOT / "reports/op001_unit_scope_candidate_20260902/op001-unit-scope-candidate.json"
OP002_AUDIT = ROOT / "reports/op002_vertical_provenance_20260903/audit.json"
OP004_AUDIT = ROOT / "reports/op004_vertical_provenance_20260903/audit.json"
OP004_SUBTYPE_BUNDLE = ROOT / "reports/op004_clean_subtype_20260903/bundle.json"
OP004_SUBTYPE_RESULT = ROOT / "reports/op004_clean_subtype_20260903/selected-result.json"
OUT = ROOT / "reports/layer3b_vertical_provenance_register_20260903"

ROW_FAIL_CLOSED = (
    "source_vertical_confirmation",
    "source_subtype_confirmation",
    "effective_void_confirmation",
    "traversability_confirmation",
    "pair_confirmation",
    "adjacency_confirmation",
    "building_exterior_root_confirmation",
    "unit_root_confirmation",
    "root_confirmation",
    "source_correction_authorized",
    "semantic_promotion",
    "build_authorized",
    "ready",
)

BATCH_FAIL_CLOSED = (
    "all_subtypes_source_confirmed",
    "all_verticals_source_confirmed",
    "all_effective_voids_confirmed",
    "all_traversability_confirmed",
    "all_pairs_confirmed",
    "all_adjacency_confirmed",
    "all_roots_confirmed",
    "source_vertical_confirmation",
    "source_subtype_confirmation",
    "effective_void_confirmation",
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


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _without_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "candidate_hash"}


def _assert_fail_closed(value: Mapping[str, Any], keys: tuple[str, ...], *, context: str) -> None:
    for key in keys:
        if value.get(key) is not False:
            raise ValueError(f"{context} promoted or omitted {key}")
    if value.get("score_effect") != "none":
        raise ValueError(f"{context} score drift")


def _validate_assumption(document: Mapping[str, Any]) -> dict[str, Any]:
    try:
        assumption = next(
            item
            for item in document["assumptions"]["items"]
            if item["id"] == "ASSUME-Z-RESEARCH"
        )
    except StopIteration as exc:
        raise ValueError("ASSUME-Z-RESEARCH is missing") from exc
    if (
        assumption.get("category") != "z_geometry"
        or assumption.get("basis") != "research_default"
        or assumption.get("status") != "unverified"
        or assumption.get("build_policy") != "allow_research_only"
        or assumption.get("value") != {"door_head_m": 2.1, "wall_height_m": 2.8}
    ):
        raise ValueError("ASSUME-Z-RESEARCH contract drift")
    return deepcopy(dict(assumption))


def _validate_op001_scope(
    document: Mapping[str, Any],
    scope: Mapping[str, Any],
    *,
    scope_path: Path,
) -> dict[str, Any]:
    validated = validate_op001_unit_scope_candidate(document, scope)
    if (
        validated.get("source_structure_hash") != document["structure_hash"]
        or validated.get("building_scope_fact", {}).get("intersects_confirmed_outer_boundary") is not False
        or validated.get("building_scope_fact", {}).get("building_exterior_root_confirmation") is not False
        or validated.get("unit_scope_hypothesis", {}).get("unit_root_candidate") is not True
        or validated.get("unit_scope_confirmation") is not False
        or validated.get("pair_confirmation") is not False
        or validated.get("traversability_confirmation") is not False
        or validated.get("adjacency_confirmation") is not False
    ):
        raise ValueError("OP001 unit-scope policy drift")
    return {
        "file_sha256": _file_hash(scope_path),
        "candidate_hash": validated["candidate_hash"],
        "building_outer_boundary_intersection": False,
        "building_exterior_root_confirmation": False,
        "unit_root_candidate": "hypothesis",
        "unit_root_confirmation": False,
        "common_side_space_id": validated["unit_scope_hypothesis"]["common_side_space_id"],
        "unit_side_space_id": validated["unit_scope_hypothesis"]["unit_side_space_id"],
    }


def _validate_op002_audit(audit: Mapping[str, Any], *, audit_path: Path) -> dict[str, Any]:
    validated = validate_op002_vertical_audit(audit)
    if (
        validated.get("opening_id") != "OP002"
        or validated.get("visual_subtype_advisory_is_vertical_authority") is not False
        or validated.get("vertical_evidence_supports_height") is not False
        or validated.get("source_vertical_confirmation") is not False
        or validated.get("effective_void_confirmation") is not False
    ):
        raise ValueError("OP002 vertical audit authority drift")
    return {
        "schema": validated["schema"],
        "file_sha256": _file_hash(audit_path),
        "candidate_hash": validated["candidate_hash"],
        "validated": True,
        "vertical_evidence_supports_height": False,
        "raw_vertical_parameters": deepcopy(validated["vertical_parameters"]),
    }


def _validate_op004_audit(audit: Mapping[str, Any], *, audit_path: Path) -> dict[str, Any]:
    validated = validate_generic_vertical_audit(
        audit,
        "OP004",
        subtype_bundle_path=OP004_SUBTYPE_BUNDLE,
        subtype_result_path=OP004_SUBTYPE_RESULT,
    )
    if (
        validated.get("visual_subtype_advisory_is_vertical_authority") is not False
        or validated.get("vertical_evidence_present") is not False
        or validated.get("source_vertical_confirmation") is not False
        or validated.get("effective_void_confirmation") is not False
        or validated.get("isolated_blender_research_display", {}).get("opening_geometry_authorized") is not False
    ):
        raise ValueError("OP004 vertical audit authority drift")
    return {
        "schema": validated["schema"],
        "file_sha256": _file_hash(audit_path),
        "candidate_hash": validated["candidate_hash"],
        "validated": True,
        "vertical_evidence_present": False,
        "raw_vertical_parameters": deepcopy(validated["vertical_parameters"]),
    }


def _normalized_vertical_parameters(
    opening_id: str,
    opening: Mapping[str, Any],
    assumption: Mapping[str, Any],
) -> dict[str, Any]:
    effective = opening.get("effective_void") if isinstance(opening.get("effective_void"), dict) else None
    source_head = effective.get("head_m") if effective else None
    source_sill = effective.get("sill_m") if effective else None
    wall_height = assumption["value"]["wall_height_m"]
    door_head = assumption["value"]["door_head_m"]
    head_class = (
        "research_assumption_bound_to_candidate_source_record"
        if opening_id in {"OP001", "OP002"}
        else "research_assumption_unbound_to_opening_geometry"
    )
    return {
        "wall_height_m": {
            "source_record_value": None,
            "research_default_value": wall_height,
            "provenance_class": "research_assumption",
            "assumption_id": assumption["id"],
            "source_explicit": False,
            "human_authorized_default": False,
            "eligible_for_source_promotion": False,
        },
        "head_m": {
            "source_record_value": source_head,
            "research_default_value": door_head,
            "provenance_class": head_class,
            "assumption_id": assumption["id"],
            "source_explicit": False,
            "human_authorized_default": False,
            "eligible_for_source_promotion": False,
        },
        "sill_m": {
            "source_record_value": source_sill,
            "research_default_value": None,
            "provenance_class": "unsupported_candidate_value" if source_sill is not None else "unknown",
            "assumption_id": None,
            "source_explicit": False,
            "human_authorized_default": False,
            "treatment": "unknown",
            "usable_for_reversible_research_display": False,
            "eligible_for_source_promotion": False,
        },
    }


def _display_policy(opening_id: str) -> dict[str, Any]:
    if opening_id == "OP002":
        status = "existing_no_cut_display_reviewed"
        head_guide = "bound_research_assumption"
    elif opening_id == "OP004":
        status = "existing_no_cut_display_reviewed"
        head_guide = "unbound_research_default"
    elif opening_id == "OP001":
        status = "candidate_requires_dedicated_plan_and_review"
        head_guide = "bound_research_assumption"
    else:
        status = "not_authorized_without_opening_specific_audit"
        head_guide = "not_authorized"
    return {
        "status": status,
        "wall_height_research_display": opening_id in {"OP001", "OP002", "OP004"},
        "head_guide_binding": head_guide,
        "sill_display_authorized": False,
        "must_keep_source_walls_intact": True,
        "opening_geometry_authorized": False,
        "semantic_objects_authorized": False,
        "ifc_authorized": False,
    }


def _remaining_blockers(opening_id: str) -> list[str]:
    common = [
        "SOURCE_VERTICAL_AUTHORITY_MISSING",
        "SILL_AUTHORITY_MISSING",
        "EFFECTIVE_VOID_CONFIRMATION_MISSING",
        "TRAVERSABILITY_CONFIRMATION_MISSING",
        "ADJACENCY_CONFIRMATION_MISSING",
        "HUMAN_ACCEPTANCE_MISSING",
    ]
    if opening_id == "OP001":
        return ["BUILDING_EXTERIOR_ROOT_REJECTED", "UNIT_ROOT_SOURCE_AUTHORITY_MISSING", *common]
    if opening_id in {"OP003", "OP006", "OP007", "OP008", "OP009", "OP010"}:
        return ["OPENING_SPECIFIC_VERTICAL_AUDIT_MISSING", "SOURCE_HOST_EFFECTIVE_VOID_MISSING", *common]
    if opening_id == "OP004":
        return ["SOURCE_HOST_EFFECTIVE_VOID_MISSING", *common]
    return common


def build(
    *,
    source_path: Path = SOURCE,
    layer3a_path: Path = LAYER3A,
    clean_xy_path: Path = CLEAN_XY,
    op001_scope_path: Path = OP001_SCOPE,
    op002_audit_path: Path = OP002_AUDIT,
    op004_audit_path: Path = OP004_AUDIT,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    source_path = Path(source_path)
    layer3a_path = Path(layer3a_path)
    clean_xy_path = Path(clean_xy_path)
    op001_scope_path = Path(op001_scope_path)
    op002_audit_path = Path(op002_audit_path)
    op004_audit_path = Path(op004_audit_path)

    document = validate_v21_document(_read_json(source_path))
    layer3a = validate_layer3a(_read_json(layer3a_path))
    clean_xy = validate_clean_xy(_read_json(clean_xy_path), rebuild=False)
    assumption = _validate_assumption(document)
    op001_scope = _validate_op001_scope(document, _read_json(op001_scope_path), scope_path=op001_scope_path)
    op002_audit = _validate_op002_audit(_read_json(op002_audit_path), audit_path=op002_audit_path)
    op004_audit = _validate_op004_audit(_read_json(op004_audit_path), audit_path=op004_audit_path)

    if (
        tuple(layer3a["opening_ids"]) != OPENING_IDS
        or tuple(layer3a["excluded_opening_ids"]) != EXCLUDED_IDS
        or layer3a.get("vertical_entry_authorized") is not False
        or layer3a.get("all_subtypes_source_confirmed") is not False
        or tuple(item["opening_id"] for item in clean_xy["openings"]) != OPENING_IDS
    ):
        raise ValueError("Layer3A/clean-XY coverage or authority drift")

    openings = {item["id"]: item for item in document["opening_contract"]["openings"]}
    layer3a_rows = {item["opening_id"]: item for item in layer3a["rows"]}
    xy_rows = {item["opening_id"]: item for item in clean_xy["openings"]}
    rows: list[dict[str, Any]] = []

    for opening_id in OPENING_IDS:
        opening = openings[opening_id]
        layer3a_row = layer3a_rows[opening_id]
        xy_row = xy_rows[opening_id]
        effective = opening.get("effective_void") if isinstance(opening.get("effective_void"), dict) else None
        source_host = (
            opening.get("host", {}).get("owning_wall_atom_id")
            if isinstance(opening.get("host"), dict)
            else None
        )
        if (
            opening.get("status") != "candidate"
            or "ASSUME-Z-RESEARCH" not in opening.get("assumption_ids", [])
            or layer3a_row.get("vertical_parameters_reviewed") is not False
            or layer3a_row.get("source_subtype_confirmation") is not False
            or xy_row.get("source_pixels_untouched") is not True
            or xy_row.get("authority")
            != (
                "source_active"
                if opening_id in {"OP001", "OP002"}
                else "registered_evidence_candidate"
            )
        ):
            raise ValueError(f"{opening_id} source/Layer3A/XY input drift")

        audit_binding = None
        if opening_id == "OP002":
            audit_binding = op002_audit
        elif opening_id == "OP004":
            audit_binding = op004_audit

        row = {
            "opening_id": opening_id,
            "source_structure_hash": document["structure_hash"],
            "source_record_state": {
                "opening_status": opening["status"],
                "build_disposition": opening["build_disposition"],
                "build_kind": opening["build_kind"],
                "source_observation_kind": opening["source_observation"]["kind"],
                "source_observation_status": opening["source_observation"]["status"],
                "source_host_atom_id": source_host,
                "effective_void_record_present": effective is not None,
                "effective_void_record_status": effective.get("status") if effective else None,
                "effective_void_record_head_m": effective.get("head_m") if effective else None,
                "effective_void_record_sill_m": effective.get("sill_m") if effective else None,
                "traversable_record_value": opening["traversable"],
                "assumption_ids": deepcopy(opening["assumption_ids"]),
                "record_fields_are_register_confirmations": False,
            },
            "assumption_binding": {
                "id": assumption["id"],
                "basis": assumption["basis"],
                "status": assumption["status"],
                "build_policy": assumption["build_policy"],
                "value": deepcopy(assumption["value"]),
            },
            "layer3a_binding": {
                "register_file_sha256": _file_hash(layer3a_path),
                "register_candidate_hash": layer3a["candidate_hash"],
                "bundle_candidate_hash": layer3a_row["bundle_candidate_hash"],
                "selected_result_file_sha256": layer3a_row["selected_result_file_sha256"],
                "visual_subtype_candidate": layer3a_row["visual_subtype_candidate"],
                "downstream_use_status": layer3a_row["downstream_use_status"],
                "vertical_parameters_reviewed": False,
                "vertical_authority": False,
            },
            "xy_binding": {
                "evidence_file_sha256": _file_hash(clean_xy_path),
                "evidence_candidate_hash": clean_xy["candidate_hash"],
                "authority": xy_row["authority"],
                "classification": xy_row["classification"],
                "matrix_cuttable": xy_row["matrix_cuttable"],
                "host_atom_id": xy_row["host_atom_id"],
                "segment_m": deepcopy(xy_row["segment_m"]),
                "source_pixels_untouched": True,
                "vertical_authority": False,
                "effective_void_authority": False,
            },
            "opening_specific_audit_binding": audit_binding,
            "op001_unit_scope_binding": op001_scope if opening_id == "OP001" else None,
            "vertical_parameters": _normalized_vertical_parameters(opening_id, opening, assumption),
            "vertical_display_policy": _display_policy(opening_id),
            "human_readable_boundary": {
                "source_record_status_is_not_register_confirmation": True,
                "effective_void_record_status_does_not_authorize_a_cut": True,
                "entry_label_is_source_pixel_context_only": opening_id == "OP001",
                "numeric_z_values_are_unverified_research_assumptions": True,
                "coverage_means_provenance_rows_only": True,
            },
            "remaining_blockers": _remaining_blockers(opening_id),
            "source_vertical_confirmation": False,
            "source_subtype_confirmation": False,
            "effective_void_confirmation": False,
            "traversability_confirmation": False,
            "pair_confirmation": False,
            "adjacency_confirmation": False,
            "building_exterior_root_confirmation": False,
            "unit_root_candidate": "hypothesis" if opening_id == "OP001" else "not_assessed",
            "unit_root_confirmation": False,
            "root_confirmation": False,
            "source_correction_authorized": False,
            "semantic_promotion": False,
            "score_effect": "none",
            "build_authorized": False,
            "ready": False,
        }
        row["candidate_hash"] = _candidate_hash(_without_hash(row))
        rows.append(row)

    result = {
        "schema": "layer3b-vertical-provenance-register-v3",
        "source_structure_hash": document["structure_hash"],
        "source_document_sha256": _file_hash(source_path),
        "layer3a_register_file_sha256": _file_hash(layer3a_path),
        "layer3a_register_candidate_hash": layer3a["candidate_hash"],
        "clean_xy_evidence_file_sha256": _file_hash(clean_xy_path),
        "clean_xy_evidence_candidate_hash": clean_xy["candidate_hash"],
        "assumption_registry_entry": assumption,
        "opening_ids": list(OPENING_IDS),
        "excluded_opening_ids": list(EXCLUDED_IDS),
        "rows": rows,
        "coverage_count": len(rows),
        "opening_specific_audit_count": sum(row["opening_specific_audit_binding"] is not None for row in rows),
        "source_confirmed_vertical_count": 0,
        "unknown_sill_treatment_count": sum(
            row["vertical_parameters"]["sill_m"]["treatment"] == "unknown" for row in rows
        ),
        "batch_gate_status": "coverage_complete_provenance_only_all_vertical_gates_closed",
        "all_subtypes_source_confirmed": False,
        "all_verticals_source_confirmed": False,
        "all_effective_voids_confirmed": False,
        "all_traversability_confirmed": False,
        "all_pairs_confirmed": False,
        "all_adjacency_confirmed": False,
        "all_roots_confirmed": False,
        "source_vertical_confirmation": False,
        "source_subtype_confirmation": False,
        "effective_void_confirmation": False,
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
    result["candidate_hash"] = _candidate_hash(_without_hash(result))
    return result if _skip_validate else validate(
        result,
        source_path=source_path,
        layer3a_path=layer3a_path,
        clean_xy_path=clean_xy_path,
        op001_scope_path=op001_scope_path,
        op002_audit_path=op002_audit_path,
        op004_audit_path=op004_audit_path,
    )


def validate(
    candidate: Mapping[str, Any],
    *,
    source_path: Path = SOURCE,
    layer3a_path: Path = LAYER3A,
    clean_xy_path: Path = CLEAN_XY,
    op001_scope_path: Path = OP001_SCOPE,
    op002_audit_path: Path = OP002_AUDIT,
    op004_audit_path: Path = OP004_AUDIT,
) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(
        source_path=Path(source_path),
        layer3a_path=Path(layer3a_path),
        clean_xy_path=Path(clean_xy_path),
        op001_scope_path=Path(op001_scope_path),
        op002_audit_path=Path(op002_audit_path),
        op004_audit_path=Path(op004_audit_path),
        _skip_validate=True,
    )
    if actual != expected:
        raise ValueError("Layer3B vertical register evidence/derivation drift")
    if (
        actual.get("schema") != "layer3b-vertical-provenance-register-v3"
        or actual.get("opening_ids") != list(OPENING_IDS)
        or actual.get("excluded_opening_ids") != list(EXCLUDED_IDS)
        or actual.get("coverage_count") != 9
        or actual.get("opening_specific_audit_count") != 2
        or actual.get("source_confirmed_vertical_count") != 0
        or actual.get("unknown_sill_treatment_count") != 9
        or actual.get("batch_gate_status")
        != "coverage_complete_provenance_only_all_vertical_gates_closed"
    ):
        raise ValueError("Layer3B vertical register coverage/scope drift")
    _assert_fail_closed(actual, BATCH_FAIL_CLOSED, context="Layer3B vertical register")
    if actual.get("candidate_hash") != _candidate_hash(_without_hash(actual)):
        raise ValueError("Layer3B vertical register candidate hash drift")

    for row in actual["rows"]:
        opening_id = row["opening_id"]
        _assert_fail_closed(row, ROW_FAIL_CLOSED, context=f"{opening_id} vertical row")
        if (
            row.get("candidate_hash") != _candidate_hash(_without_hash(row))
            or row["layer3a_binding"].get("vertical_authority") is not False
            or row["xy_binding"].get("vertical_authority") is not False
            or row["xy_binding"].get("effective_void_authority") is not False
            or row["vertical_parameters"]["head_m"].get("source_explicit") is not False
            or row["vertical_parameters"]["sill_m"].get("treatment") != "unknown"
            or row["vertical_parameters"]["sill_m"].get("usable_for_reversible_research_display") is not False
            or row["vertical_display_policy"].get("must_keep_source_walls_intact") is not True
            or row["vertical_display_policy"].get("opening_geometry_authorized") is not False
            or row["vertical_display_policy"].get("semantic_objects_authorized") is not False
            or row["vertical_display_policy"].get("ifc_authorized") is not False
            or row["source_record_state"].get("record_fields_are_register_confirmations") is not False
            or row["human_readable_boundary"].get("source_record_status_is_not_register_confirmation") is not True
            or row["human_readable_boundary"].get("effective_void_record_status_does_not_authorize_a_cut") is not True
            or row["human_readable_boundary"].get("numeric_z_values_are_unverified_research_assumptions") is not True
            or row["human_readable_boundary"].get("coverage_means_provenance_rows_only") is not True
            or not row["xy_binding"].get("segment_m")
        ):
            raise ValueError(f"{opening_id} vertical row authority/hash drift")

    rows_by_id = {row["opening_id"]: row for row in actual["rows"]}
    op001 = rows_by_id["OP001"]
    if (
        op001["source_record_state"]["effective_void_record_status"] != "confirmed"
        or op001["effective_void_confirmation"] is not False
        or op001["vertical_parameters"]["head_m"]["provenance_class"]
        != "research_assumption_bound_to_candidate_source_record"
        or op001["vertical_parameters"]["sill_m"]["source_record_value"] != 0.0
        or op001["unit_root_candidate"] != "hypothesis"
        or op001["building_exterior_root_confirmation"] is not False
        or op001["unit_root_confirmation"] is not False
        or op001["root_confirmation"] is not False
    ):
        raise ValueError("OP001 candidate/root/vertical quarantine drift")
    for opening_id in ("OP002", "OP004"):
        if rows_by_id[opening_id]["opening_specific_audit_binding"] is None:
            raise ValueError(f"{opening_id} opening-specific audit binding missing")
    for opening_id in set(OPENING_IDS) - {"OP002", "OP004"}:
        if rows_by_id[opening_id]["opening_specific_audit_binding"] is not None:
            raise ValueError(f"{opening_id} cross-opening audit binding")
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
        "# Layer3B vertical provenance register v3\n\n"
        "Nine admitted openings are covered as provenance-only research rows. OP001 preserves the source-record "
        "effective-void status while independently keeping effective-void, vertical, building-root, and unit-root "
        "confirmation false. OP002 and OP004 bind and validate their own audits; no audit is reused across openings. "
        "Every XY segment is present but explicitly has no vertical authority. The 2.8 m wall height and 2.1 m head "
        "are unverified ASSUME-Z-RESEARCH defaults; every sill is treated as unknown. No new Blender/API execution, "
        "source correction, score change, semantic promotion, or formal build is authorized. A confirmed value in "
        "source_record_state is only the source record's internal status; it is not this register's confirmation and "
        "does not authorize a cut. The ENTRY label is source-pixel context only.\n",
        encoding="utf-8",
    )
    (args.output.parent / "REVIEW_CARD_ZH.md").write_text(
        "# Layer3B 垂直来源登记 v3\n\n"
        "九个纳入开口均已进入“来源登记”，没有进入“建筑事实”。OP001 保留源契约中的二维 effective-void "
        "记录，但有效洞口、垂直尺寸、楼栋入口和单位入口仍全部未确认；OP002、OP004 分别绑定并验证自己的"
        "审计，不跨开口复用。每行均保留可重算 XY 段，同时明确 XY 没有垂直权限。墙高 2.8 m 和门头 2.1 m "
        "只是未验证研究假设，所有 sill 均按 unknown 处理。未授权切墙、门窗/IFC 语义对象、源修正、评分变化"
        "或正式 Blender/IFC 建模。特别说明：source_record_state 中的 confirmed 只是源记录内部状态，不是本"
        "登记的确认结论，也不授权切洞；ENTRY 文字只作为源图像素上下文。\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BATCH_FAIL_CLOSED",
    "ROW_FAIL_CLOSED",
    "_candidate_hash",
    "build",
    "validate",
]
