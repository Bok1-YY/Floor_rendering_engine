"""Build a fail-closed combined full-height XY-gap research plan.

The plan combines nine independently reviewed XY-only gap variants into one
wall set. It deliberately makes no door/window, Z, room, connectivity,
source-correction, score, or formal-build claim.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.build_opening_gap_variant_plans import (
    EXPECTED_EXCLUDED,
    build as build_gap_plans,
    validate as validate_gap_plans,
)
from tools.goal_loop_v2.build_opening_xy_clean_evidence import EXPECTED_INCLUDED
from tools.goal_loop_v2.build_registered_gap_review_bundle import (
    build as build_registered_review,
    validate as validate_registered_review,
)

SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
GAP_PLANS = ROOT / "reports/opening_gap_variant_plans_20260902/plans.json"
REGISTERED_REVIEW = ROOT / "reports/registered_gap_review_bundle_20260903/bundle.json"
LAYER1_MANIFEST = ROOT / "artifacts/goal_loop_v2/1308/research_source_faithful_v001/artifact_manifest.json"
VARIANTS_BASE = ROOT / "artifacts/goal_loop_v2/1308/opening_xy_variants_v001"
OUT = ROOT / "reports/combined_gap_plan_20260903"

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
VARIANT_MANIFEST_FAIL_CLOSED = (
    "xy_experiment_confirmation",
    "semantic_promotion",
    "build_authorized",
)
VARIANT_VALIDATION_FAIL_CLOSED = (
    "xy_experiment_confirmation",
    "cut_confirmation",
    "pair_confirmation",
    "adjacency_confirmation",
    "semantic_promotion",
    "build_authorized",
)
REQUIRED_LAYER1_ARTIFACTS = {
    "checkpoint_blend",
    "blender_source",
    "portable_glb",
    "render_top",
    "render_northeast",
    "render_northwest",
    "structural_validation",
}
REQUIRED_VARIANT_ARTIFACTS = {
    "checkpoint_blend",
    "blender_source",
    "portable_glb",
    "render_top",
    "render_northeast",
    "render_closeup_top",
    "validation",
}
MANDATORY_LABELS = [
    "COMBINED LAYER 2",
    "XY GAP RESEARCH ONLY",
    "NINE CANDIDATE GAPS",
    "FULL-HEIGHT VISUALIZATION ONLY",
    "NOT SOURCE-CONFIRMED",
    "NO DOOR/WINDOW SEMANTICS",
    "NO Z/HEAD/SILL CLAIM",
    "NO TRAVERSABILITY / ADJACENCY",
    "NOT FOR CONSTRUCTION",
]


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _artifact_rows(
    manifest: Mapping[str, Any],
    *,
    expected_parent: Path,
    required_kinds: set[str],
) -> dict[str, dict[str, Any]]:
    rows = manifest.get("artifacts")
    if not isinstance(rows, list):
        raise ValueError("artifact manifest rows missing")
    by_kind: dict[str, dict[str, Any]] = {}
    expected_parent = expected_parent.resolve()
    for raw in rows:
        row = dict(raw)
        kind = row.get("kind")
        if kind in by_kind:
            raise ValueError(f"duplicate artifact kind: {kind}")
        path = Path(row.get("path", ""))
        if path.parent.resolve() != expected_parent:
            raise ValueError(f"artifact escaped expected directory: {kind}")
        if not path.is_file():
            raise ValueError(f"artifact missing: {kind}")
        if path.stat().st_size != row.get("bytes") or _file_hash(path) != row.get("sha256"):
            raise ValueError(f"artifact bytes/hash drift: {kind}")
        by_kind[str(kind)] = row
    if set(by_kind) != required_kinds:
        raise ValueError("artifact kind coverage drift")
    return by_kind


def _validate_layer1_manifest(
    manifest_path: Path,
    *,
    source_structure_hash: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = _read_json(manifest_path)
    if (
        manifest.get("schema") != "blender-research-wall-layer-artifact-manifest-v1"
        or manifest.get("branch_id") != "1308-source-wall-layer-v001"
        or manifest.get("source_structure_hash") != source_structure_hash
        or manifest.get("wall_object_count") != 35
        or manifest.get("opening_cuts") != 0
        or manifest.get("research_only") is not True
        or manifest.get("not_for_construction") is not True
        or manifest.get("formal_build_authorized") is not False
    ):
        raise ValueError("Layer1 manifest identity/authorization drift")
    artifacts = _artifact_rows(
        manifest,
        expected_parent=manifest_path.parent,
        required_kinds=REQUIRED_LAYER1_ARTIFACTS,
    )
    return manifest, artifacts


def _assert_fail_closed(
    value: Mapping[str, Any],
    *,
    context: str,
    required_fields: tuple[str, ...] = FAIL_CLOSED,
) -> None:
    for key in required_fields:
        if value.get(key) is not False:
            raise ValueError(f"{context} promoted {key}")
    if value.get("score_effect") != "none":
        raise ValueError(f"{context} score drift")


def _assert_piece_complement(plan: Mapping[str, Any]) -> None:
    parameters = [float(value) for value in plan["host_parameters"]]
    if len(parameters) != 2:
        raise ValueError("gap parameter count drift")
    gap_lo, gap_hi = sorted(parameters)
    if gap_lo < -1e-9 or gap_hi > 1.0 + 1e-9 or gap_hi - gap_lo <= 1e-9:
        raise ValueError("gap interval leaves host or is empty")

    expected_intervals = []
    if gap_lo > 1e-9:
        expected_intervals.append([0.0, gap_lo])
    if 1.0 - gap_hi > 1e-9:
        expected_intervals.append([gap_hi, 1.0])
    actual_intervals = [piece["host_parameter_interval"] for piece in plan["remaining_host_pieces"]]
    if len(actual_intervals) != len(expected_intervals):
        raise ValueError("remaining host piece count does not complement gap")
    for actual, expected in zip(actual_intervals, expected_intervals):
        if len(actual) != 2 or not all(
            math.isclose(float(a), float(e), abs_tol=1e-9)
            for a, e in zip(actual, expected)
        ):
            raise ValueError("remaining host interval does not complement gap")
        start, end = map(float, actual)
        if end <= start or (end > gap_lo + 1e-9 and start < gap_hi - 1e-9):
            raise ValueError("remaining host piece overlaps gap")


def _validate_variant_artifact(
    manifest_path: Path,
    *,
    plan: Mapping[str, Any],
    source_structure_hash: str,
    source_document_sha256: str,
    gap_plans_candidate_hash: str,
    gap_plans_file_sha256: str,
) -> dict[str, Any]:
    opening_id = plan["opening_id"]
    manifest = _read_json(manifest_path)
    _assert_fail_closed(
        manifest,
        context=f"{opening_id} manifest",
        required_fields=VARIANT_MANIFEST_FAIL_CLOSED,
    )
    if (
        manifest.get("schema") != "blender-opening-gap-variant-artifact-manifest-v1"
        or manifest.get("opening_id") != opening_id
        or manifest.get("branch_id") != f"1308-gap-{opening_id}-v001"
        or manifest.get("variant_hash") != plan["variant_hash"]
        or manifest.get("source_structure_hash") != source_structure_hash
        or manifest.get("wall_piece_count") != plan["expected_wall_object_count"]
        or manifest.get("opening_elements") != 0
        or manifest.get("one_opening_only") is not True
        or manifest.get("gap_z_policy") != "full_height_visualization_only"
        or manifest.get("research_only") is not True
        or manifest.get("not_for_construction") is not True
    ):
        raise ValueError(f"{opening_id} variant manifest drift")
    artifacts = _artifact_rows(
        manifest,
        expected_parent=manifest_path.parent,
        required_kinds=REQUIRED_VARIANT_ARTIFACTS,
    )
    validation_path = Path(artifacts["validation"]["path"])
    validation = _read_json(validation_path)
    _assert_fail_closed(
        validation,
        context=f"{opening_id} validation",
        required_fields=VARIANT_VALIDATION_FAIL_CLOSED,
    )
    if (
        validation.get("schema") != "blender-opening-gap-variant-validation-v2"
        or validation.get("branch_id") != manifest["branch_id"]
        or validation.get("opening_id") != opening_id
        or validation.get("variant_hash") != plan["variant_hash"]
        or validation.get("plan_bundle_candidate_hash") != gap_plans_candidate_hash
        or validation.get("source_structure_hash") != source_structure_hash
        or validation.get("source_document_sha256") != source_document_sha256
        or validation.get("plans_file_sha256") != gap_plans_file_sha256
        or validation.get("expected_wall_piece_count") != plan["expected_wall_object_count"]
        or validation.get("actual_wall_piece_count") != plan["expected_wall_object_count"]
        or validation.get("expected_host_piece_count") != len(plan["remaining_host_pieces"])
        or validation.get("actual_host_piece_count") != len(plan["remaining_host_pieces"])
        or validation.get("non_host_atom_count_errors") != []
        or validation.get("topology_errors") != []
        or validation.get("gap_overlap_errors") != []
        or validation.get("source_gap_segment_m") != plan["source_gap_segment_m"]
        or validation.get("projected_gap_segment_m") != plan["projected_segment_m"]
        or validation.get("projection_mode") != plan["projection_mode"]
        or validation.get("wall_height_m") != 2.8
        or validation.get("opening_elements") != 0
        or validation.get("one_opening_only") is not True
        or validation.get("gap_z_policy") != "full_height_visualization_only"
        or validation.get("research_only") is not True
        or validation.get("not_for_construction") is not True
        or validation.get("pass") is not True
    ):
        raise ValueError(f"{opening_id} variant validation drift")
    return {
        "opening_id": opening_id,
        "branch_id": manifest["branch_id"],
        "variant_hash": plan["variant_hash"],
        "manifest_path": str(manifest_path.relative_to(ROOT)).replace("\\", "/"),
        "manifest_file_sha256": _file_hash(manifest_path),
        "validation_path": str(validation_path.relative_to(ROOT)).replace("\\", "/"),
        "validation_file_sha256": _file_hash(validation_path),
        "wall_piece_count": manifest["wall_piece_count"],
        "blender_source_sha256": artifacts["blender_source"]["sha256"],
        "portable_glb_sha256": artifacts["portable_glb"]["sha256"],
        "render_closeup_top_sha256": artifacts["render_closeup_top"]["sha256"],
    }


def build(
    *,
    source_path: Path = SOURCE,
    gap_plans_path: Path = GAP_PLANS,
    registered_review_path: Path = REGISTERED_REVIEW,
    layer1_manifest_path: Path = LAYER1_MANIFEST,
    variants_base: Path = VARIANTS_BASE,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    source_path = Path(source_path)
    gap_plans_path = Path(gap_plans_path)
    registered_review_path = Path(registered_review_path)
    layer1_manifest_path = Path(layer1_manifest_path)
    variants_base = Path(variants_base)

    document = validate_v21_document(_read_json(source_path))
    gap_plans = _read_json(gap_plans_path)
    validate_gap_plans(gap_plans)
    if gap_plans != build_gap_plans():
        raise ValueError("gap plan file does not equal rebuilt upstream")
    registered_review = _read_json(registered_review_path)
    validate_registered_review(registered_review)
    if registered_review != build_registered_review():
        raise ValueError("registered review file does not equal rebuilt upstream")
    layer1, layer1_artifacts = _validate_layer1_manifest(
        layer1_manifest_path,
        source_structure_hash=document["structure_hash"],
    )

    included = list(registered_review["accepted_for_isolated_xy_variant"])
    if included != list(EXPECTED_INCLUDED) or included != list(gap_plans["opening_ids"]):
        raise ValueError("combined accepted coverage/order drift")
    excluded = list(gap_plans["excluded_opening_ids"])
    if excluded != list(EXPECTED_EXCLUDED) or set(included) & set(excluded):
        raise ValueError("combined exclusion coverage drift")

    by_opening = {row["opening_id"]: row for row in gap_plans["plans"]}
    if set(by_opening) != set(included):
        raise ValueError("combined gap plan row coverage drift")
    plans = [deepcopy(by_opening[opening_id]) for opening_id in included]
    host_ids = [plan["host_atom_id"] for plan in plans]
    atoms = {atom["id"]: atom for atom in document["wall_graph"]["atoms"]}
    if len(set(host_ids)) != len(host_ids) or any(host_id not in atoms for host_id in host_ids):
        raise ValueError("combined host distinctness/existence drift")

    for plan in plans:
        _assert_fail_closed(plan, context=plan["opening_id"])
        if plan.get("gap_z_policy") != "full_height_visualization_only":
            raise ValueError("combined plan Z policy drift")
        payload = {key: value for key, value in plan.items() if key != "variant_hash"}
        if plan.get("variant_hash") != _candidate_hash(payload):
            raise ValueError("combined variant hash drift")
        _assert_piece_complement(plan)

    source_document_sha256 = _file_hash(source_path)
    gap_plans_file_sha256 = _file_hash(gap_plans_path)
    variant_bindings = [
        _validate_variant_artifact(
            variants_base / plan["opening_id"] / "artifact_manifest.json",
            plan=plan,
            source_structure_hash=document["structure_hash"],
            source_document_sha256=source_document_sha256,
            gap_plans_candidate_hash=gap_plans["candidate_hash"],
            gap_plans_file_sha256=gap_plans_file_sha256,
        )
        for plan in plans
    ]

    untouched_atom_count = len(atoms) - len(set(host_ids))
    host_piece_count = sum(len(plan["remaining_host_pieces"]) for plan in plans)
    expected_wall_piece_count = untouched_atom_count + host_piece_count
    op003 = by_opening["OP003"]
    op003_parameters = sorted(float(value) for value in op003["host_parameters"])
    op001 = by_opening["OP001"]

    result = {
        "schema": "combined-gap-plan-v3",
        "branch_kind": "combined_full_height_xy_gap_research_after_isolated_review",
        "branch_id": "1308-combined-xy-gap-research-v001",
        "source_structure_hash": document["structure_hash"],
        "source_document_sha256": source_document_sha256,
        "gap_plans_file_sha256": gap_plans_file_sha256,
        "gap_plans_candidate_hash": gap_plans["candidate_hash"],
        "registered_review_file_sha256": _file_hash(registered_review_path),
        "registered_review_candidate_hash": registered_review["candidate_hash"],
        "layer1_manifest_file_sha256": _file_hash(layer1_manifest_path),
        "layer1_blender_source_sha256": layer1_artifacts["blender_source"]["sha256"],
        "layer1_wall_object_count": layer1["wall_object_count"],
        "included_opening_ids": included,
        "excluded_opening_ids": excluded,
        "host_atom_ids": host_ids,
        "source_wall_atom_count": len(atoms),
        "untouched_atom_count": untouched_atom_count,
        "host_piece_count": host_piece_count,
        "expected_wall_piece_count": expected_wall_piece_count,
        "plans": plans,
        "variant_artifact_bindings": variant_bindings,
        "op001_projection_source_distinction": {
            "projection_mode": op001["projection_mode"],
            "source_gap_segment_m": deepcopy(op001["source_gap_segment_m"]),
            "projected_gap_segment_m": deepcopy(op001["projected_segment_m"]),
            "maximum_perpendicular_offset_m": op001["maximum_perpendicular_offset_m"],
            "source_segment_preserved_as_provenance": op001["source_segment_preserved_as_provenance"],
            "segments_are_distinct": op001["source_gap_segment_m"] != op001["projected_segment_m"],
        },
        "op003_endpoint_residual_rule": {
            "gap_starts_at_host_parameter_zero": math.isclose(op003_parameters[0], 0.0, abs_tol=1e-9),
            "zero_length_endpoint_residual_omitted": True,
            "remaining_host_piece_count": len(op003["remaining_host_pieces"]),
            "short_residual_piece_count_below_0_05m": op003["short_residual_piece_count_below_0_05m"],
        },
        "reproducibility_scope": "current_evidence_workspace",
        "portable_bundle": False,
        "portability_blocker": "upstream_manifests_embed_machine_absolute_artifact_paths",
        "artifact_labels": list(MANDATORY_LABELS),
        "gap_z_policy": "full_height_visualization_only",
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
        source_path=source_path,
        gap_plans_path=gap_plans_path,
        registered_review_path=registered_review_path,
        layer1_manifest_path=layer1_manifest_path,
        variants_base=variants_base,
    )


def validate(
    candidate: Mapping[str, Any],
    *,
    source_path: Path = SOURCE,
    gap_plans_path: Path = GAP_PLANS,
    registered_review_path: Path = REGISTERED_REVIEW,
    layer1_manifest_path: Path = LAYER1_MANIFEST,
    variants_base: Path = VARIANTS_BASE,
) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(
        source_path=source_path,
        gap_plans_path=gap_plans_path,
        registered_review_path=registered_review_path,
        layer1_manifest_path=layer1_manifest_path,
        variants_base=variants_base,
        _skip_validate=True,
    )
    if actual != expected:
        raise ValueError("combined gap plan evidence/derivation drift")
    _assert_fail_closed(actual, context="combined plan")
    if actual.get("artifact_labels") != MANDATORY_LABELS:
        raise ValueError("combined artifact label drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("combined candidate hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT / "plan.json")
    args = parser.parse_args(argv)
    result = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output.parent / "REPORT.md").write_text(
        "# Combined XY-gap research plan v3\n\n"
        "Nine independently reviewed XY-only variants are combined into one 43-piece wall plan that is "
        "reproducible inside the current evidence workspace. It is not yet a portable evidence bundle because "
        "upstream manifests embed machine-absolute artifact paths. "
        "The plan binds the validated v21 source, gap plans, registered Gemini review, Layer 1 artifacts, and "
        "all nine isolated Blender manifests/validations. OP003 has one residual piece because its gap starts at "
        "host parameter zero; no short wall was silently discarded. This remains full-height research visualization "
        "only and makes no type, Z, room, traversability, adjacency, source-correction, score, or formal-build claim.\n",
        encoding="utf-8",
    )
    (args.output.parent / "REVIEW_CARD_ZH.md").write_text(
        "# 组合二维间隙研究计划 v3\n\n"
        "这份候选把九个已独立审查的二维间隙放进同一个墙体研究模型，共 43 段墙。它只保证在当前证据"
        "工作区内可复核；由于上游清单仍含本机绝对路径，目前不是可随意搬迁的证据包。它绑定原始 v21 "
        "结构、间隙计划、Gemini 注册评审、第一层模型和九个独立 Blender 变体的真实文件哈希。"
        "OP003 只有一段残墙，是因为间隙恰好从宿主墙端点开始，另一侧残段长度为零，并非人为删除短墙。"
        "OP005、OP011、历史门户和 OP012 仍被排除。该模型不确认门窗类型、洞口高度、房间、通行、"
        "邻接、源修订、评分或正式施工，仅供可逆研究与下一轮视觉复核。\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_candidate_hash", "_assert_piece_complement"]
