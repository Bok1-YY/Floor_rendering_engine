"""Build a generic no-cut vertical-research display plan from a provenance audit."""
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
from tools.goal_loop_v2.build_opening_vertical_provenance_audit import validate as validate_vertical_audit

SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
LAYER1_MANIFEST = ROOT / "artifacts/goal_loop_v2/1308/research_source_faithful_v001/artifact_manifest.json"
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
FORBIDDEN_OBJECT_ROLES = [
    "floor_cut",
    "opening_volume",
    "door_leaf",
    "window",
    "threshold",
    "sill_geometry",
    "lintel_structural_element",
    "ifc_opening",
    "ifc_void",
    "ifc_fill",
    "traversability_edge",
    "adjacency_edge",
    "root_edge",
]


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


def _validate_layer1(
    path: Path,
    *,
    structure_hash: str,
    source_sha256: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = _read_json(path)
    if (
        manifest.get("schema") != "blender-research-wall-layer-artifact-manifest-v1"
        or manifest.get("source_structure_hash") != structure_hash
        or manifest.get("source_document_sha256") != source_sha256
        or manifest.get("wall_object_count") != 35
        or manifest.get("opening_cuts") != 0
        or manifest.get("research_only") is not True
        or manifest.get("not_for_construction") is not True
        or manifest.get("formal_build_authorized") is not False
    ):
        raise ValueError("generic vertical display Layer1 baseline drift")
    artifacts = {}
    for row in manifest.get("artifacts", []):
        artifact_path = Path(row["path"])
        if row["kind"] in artifacts or not artifact_path.is_file():
            raise ValueError("generic vertical display Layer1 artifact coverage drift")
        if artifact_path.stat().st_size != row["bytes"] or _file_hash(artifact_path) != row["sha256"]:
            raise ValueError("generic vertical display Layer1 artifact drift")
        artifacts[row["kind"]] = dict(row)
    if "blender_source" not in artifacts or "structural_validation" not in artifacts:
        raise ValueError("generic vertical display Layer1 core artifact missing")
    return manifest, artifacts


def build(
    opening_id: str,
    *,
    audit_path: Path,
    subtype_bundle_path: Path,
    subtype_result_path: Path,
    source_path: Path = SOURCE,
    layer1_manifest_path: Path = LAYER1_MANIFEST,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    source_path = Path(source_path)
    audit_path = Path(audit_path)
    layer1_manifest_path = Path(layer1_manifest_path)
    document = validate_v21_document(_read_json(source_path))
    audit = _read_json(audit_path)
    validate_vertical_audit(
        audit,
        opening_id,
        subtype_bundle_path=Path(subtype_bundle_path),
        subtype_result_path=Path(subtype_result_path),
        source_path=source_path,
    )
    _assert_fail_closed(audit, context=f"{opening_id} vertical audit")
    if (
        audit["vertical_parameters"]["head_m"]["provenance_class"]
        != "research_assumption_unbound_to_opening_geometry"
        or audit["vertical_parameters"]["sill_m"]["treatment"] != "unknown"
        or audit["isolated_blender_research_display"]["head_guide_binding"] != "unbound_research_default"
        or audit["isolated_blender_research_display"]["opening_geometry_authorized"] is not False
        or audit["isolated_blender_research_display"]["must_keep_source_walls_intact"] is not True
    ):
        raise ValueError("generic vertical audit display handoff drift")
    source_sha256 = _file_hash(source_path)
    layer1, artifacts = _validate_layer1(
        layer1_manifest_path,
        structure_hash=document["structure_hash"],
        source_sha256=source_sha256,
    )
    binding = audit["xy_research_binding"]
    host_id = binding["host_atom_id"]
    host = next(atom for atom in document["wall_graph"]["atoms"] if atom["id"] == host_id)
    segment = deepcopy(binding["segment_m"])
    (x0, y0), (x1, y1) = segment
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        raise ValueError("generic vertical display segment is empty")
    normal = [-dy / length, dx / length]
    wall_height = float(audit["vertical_parameters"]["wall_height_m"]["research_default_value"])
    head = float(audit["vertical_parameters"]["head_m"]["research_default_value"])
    offset = float(host["thickness_m"]) / 2.0 + 0.03
    head_segment = [
        [point[0] + normal[0] * offset, point[1] + normal[1] * offset]
        for point in segment
    ]
    branch_id = f"1308-{opening_id.lower()}-layer3b-vertical-research-v001"
    labels = [
        f"{opening_id} LAYER 3B — VERTICAL RESEARCH DISPLAY",
        "WALL HEIGHT 2.8m — UNVERIFIED RESEARCH ASSUMPTION",
        "HEAD GUIDE 2.1m — UNBOUND RESEARCH DEFAULT / NOT OPENING GEOMETRY",
        "SILL — UNKNOWN / NOT AUTHORIZED",
        f"{audit['visual_subtype_candidate'].upper()} VISUAL CANDIDATE ONLY",
        "NO SOURCE VERTICAL CONFIRMATION",
        "NO EFFECTIVE-VOID CONFIRMATION",
        "SOURCE WALLS INTACT / ZERO CUTS",
        "NO TRAVERSABILITY / ADJACENCY / ROOT",
        "RESEARCH ONLY",
        "NOT FOR CONSTRUCTION",
    ]
    result = {
        "schema": "opening-vertical-display-plan-v1",
        "opening_id": opening_id,
        "branch_id": branch_id,
        "branch_kind": "unbound_vertical_guide_research_display_without_opening_cut",
        "source_structure_hash": document["structure_hash"],
        "source_document_sha256": source_sha256,
        "vertical_audit_file_sha256": _file_hash(audit_path),
        "vertical_audit_candidate_hash": audit["candidate_hash"],
        "layer1_manifest_file_sha256": _file_hash(layer1_manifest_path),
        "layer1_blender_source_sha256": artifacts["blender_source"]["sha256"],
        "baseline": {
            "source_wall_atom_count": len(document["wall_graph"]["atoms"]),
            "intact_source_wall_count": layer1["wall_object_count"],
            "opening_cuts": 0,
        },
        "xy_binding": {
            "host_atom_id": host_id,
            "segment_m": segment,
            "length_m": length,
            "host_thickness_m": float(host["thickness_m"]),
            "display_face_normal_xy": normal,
            "vertical_authority": False,
        },
        "vertical_assumptions": {
            "wall_height_m": {
                "value": wall_height,
                "provenance_class": "research_assumption",
                "assumption_id": "ASSUME-Z-RESEARCH",
            },
            "head_guide_m": {
                "value": head,
                "provenance_class": "research_assumption_unbound_to_opening_geometry",
                "assumption_id": "ASSUME-Z-RESEARCH",
                "binding": "unbound_research_default",
            },
            "sill_m": {
                "value": None,
                "provenance_class": "unknown",
                "reason": "no_source_or_authorized_default",
            },
        },
        "guide_specs": [
            {
                "object_name": f"GEO-RESEARCH-{opening_id}-XY-LOCATOR",
                "role": "nonsemantic_xy_locator",
                "centerline_m": segment,
                "xy_thickness_m": 0.04,
                "z_min_m": wall_height + 0.01,
                "z_max_m": wall_height + 0.03,
                "source_fact": False,
                "opening_geometry": False,
            },
            {
                "object_name": f"GEO-RESEARCH-{opening_id}-HEAD-GUIDE",
                "role": "nonsemantic_unbound_head_guide",
                "centerline_m": head_segment,
                "xy_thickness_m": 0.03,
                "face_offset_m": offset,
                "z_center_m": head,
                "z_min_m": head - 0.02,
                "z_max_m": head + 0.02,
                "source_fact": False,
                "opening_geometry": False,
            },
        ],
        "labels": labels,
        "forbidden_object_roles": list(FORBIDDEN_OBJECT_ROLES),
        "guide_object_count": 2,
        "opening_geometry_created": False,
        "floor_cut_created": False,
        "sill_geometry_created": False,
        "door_leaf_created": False,
        "lintel_structural_element_created": False,
        "ifc_opening_created": False,
        "ifc_void_or_fill_created": False,
        "research_only": True,
        "not_for_construction": True,
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
        audit_path=audit_path,
        subtype_bundle_path=Path(subtype_bundle_path),
        subtype_result_path=Path(subtype_result_path),
        source_path=source_path,
        layer1_manifest_path=layer1_manifest_path,
    )


def validate(
    candidate: Mapping[str, Any],
    opening_id: str,
    *,
    audit_path: Path,
    subtype_bundle_path: Path,
    subtype_result_path: Path,
    source_path: Path = SOURCE,
    layer1_manifest_path: Path = LAYER1_MANIFEST,
) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(
        opening_id,
        audit_path=Path(audit_path),
        subtype_bundle_path=Path(subtype_bundle_path),
        subtype_result_path=Path(subtype_result_path),
        source_path=Path(source_path),
        layer1_manifest_path=Path(layer1_manifest_path),
        _skip_validate=True,
    )
    if actual != expected:
        raise ValueError("generic vertical display plan evidence/derivation drift")
    _assert_fail_closed(actual, context=f"{opening_id} display plan")
    if (
        actual.get("schema") != "opening-vertical-display-plan-v1"
        or actual["baseline"] != {
            "source_wall_atom_count": 35,
            "intact_source_wall_count": 35,
            "opening_cuts": 0,
        }
        or actual.get("guide_object_count") != 2
        or actual["vertical_assumptions"]["sill_m"]["value"] is not None
        or actual["vertical_assumptions"]["head_guide_m"]["binding"] != "unbound_research_default"
        or any(
            actual.get(key) is not False
            for key in (
                "opening_geometry_created",
                "floor_cut_created",
                "sill_geometry_created",
                "door_leaf_created",
                "lintel_structural_element_created",
                "ifc_opening_created",
                "ifc_void_or_fill_created",
            )
        )
    ):
        raise ValueError("generic vertical display plan geometry/policy drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("generic vertical display plan candidate hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opening-id", required=True)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--subtype-bundle", required=True, type=Path)
    parser.add_argument("--subtype-result", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--layer1-manifest", type=Path, default=LAYER1_MANIFEST)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    result = build(
        args.opening_id,
        audit_path=args.audit,
        subtype_bundle_path=args.subtype_bundle,
        subtype_result_path=args.subtype_result,
        source_path=args.source,
        layer1_manifest_path=args.layer1_manifest,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output.parent / "REPORT.md").write_text(
        f"# {args.opening_id} no-cut vertical display plan\n\n"
        "All 35 source wall atoms remain intact. A blue XY locator is placed above the wall and an orange 2.1 m "
        "guide is placed in front of the wall, explicitly unbound to opening geometry. Sill is null/unknown. No cut, "
        "opening, door leaf, lintel, IFC relation, traversability, adjacency, root, score, or formal build is allowed.\n",
        encoding="utf-8",
    )
    (args.output.parent / "REVIEW_CARD_ZH.md").write_text(
        f"# {args.opening_id} 无切洞垂直研究显示计划\n\n"
        "35 段源墙全部保持完整；蓝色 XY locator 位于墙顶上方，橙色 2.1 m guide 位于墙面前方并明确未绑定"
        "到 opening geometry；sill 为 null/unknown。不创建切洞、门扇、结构过梁、IFC、通行、邻接、root、"
        "评分或正式模型。\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_candidate_hash", "FORBIDDEN_OBJECT_ROLES"]
