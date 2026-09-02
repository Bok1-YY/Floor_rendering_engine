"""Build a numeric, fail-closed OP002 Layer3B Blender display plan."""
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
from tools.goal_loop_v2.build_op002_vertical_provenance_audit import validate as validate_vertical_audit

SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
AUDIT = ROOT / "reports/op002_vertical_provenance_20260903/audit.json"
LAYER1_MANIFEST = ROOT / "artifacts/goal_loop_v2/1308/research_source_faithful_v001/artifact_manifest.json"
OUT = ROOT / "reports/op002_vertical_display_plan_20260903"
BRANCH_ID = "1308-op002-layer3b-vertical-research-v001"
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
LABELS = [
    "OP002 LAYER 3B — VERTICAL RESEARCH DISPLAY",
    "WALL HEIGHT 2.8m — UNVERIFIED RESEARCH ASSUMPTION",
    "HEAD 2.1m — UNVERIFIED RESEARCH ASSUMPTION",
    "SILL — UNKNOWN / NOT AUTHORIZED",
    "VISUAL DOOR CANDIDATE ONLY",
    "NO SOURCE VERTICAL CONFIRMATION",
    "NO EFFECTIVE-VOID CONFIRMATION",
    "NO TRAVERSABILITY / ADJACENCY",
    "NO SOURCE CORRECTION",
    "RESEARCH ONLY",
    "NOT FOR CONSTRUCTION",
]
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
    manifest_path: Path,
    *,
    source_structure_hash: str,
    source_document_sha256: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = _read_json(manifest_path)
    if (
        manifest.get("schema") != "blender-research-wall-layer-artifact-manifest-v1"
        or manifest.get("branch_id") != "1308-source-wall-layer-v001"
        or manifest.get("source_structure_hash") != source_structure_hash
        or manifest.get("source_document_sha256") != source_document_sha256
        or manifest.get("wall_object_count") != 35
        or manifest.get("opening_cuts") != 0
        or manifest.get("research_only") is not True
        or manifest.get("not_for_construction") is not True
        or manifest.get("formal_build_authorized") is not False
    ):
        raise ValueError("Layer1 display-plan baseline drift")
    artifacts = {}
    for row in manifest.get("artifacts", []):
        kind = row.get("kind")
        path = Path(row.get("path", ""))
        if kind in artifacts or not path.is_file():
            raise ValueError("Layer1 display-plan artifact coverage drift")
        if path.stat().st_size != row.get("bytes") or _file_hash(path) != row.get("sha256"):
            raise ValueError("Layer1 display-plan artifact bytes/hash drift")
        artifacts[str(kind)] = dict(row)
    if set(artifacts) != {
        "checkpoint_blend",
        "blender_source",
        "portable_glb",
        "render_top",
        "render_northeast",
        "render_northwest",
        "structural_validation",
    }:
        raise ValueError("Layer1 display-plan artifact kind drift")
    return manifest, artifacts


def build(
    *,
    source_path: Path = SOURCE,
    audit_path: Path = AUDIT,
    layer1_manifest_path: Path = LAYER1_MANIFEST,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    source_path = Path(source_path)
    audit_path = Path(audit_path)
    layer1_manifest_path = Path(layer1_manifest_path)
    document = validate_v21_document(_read_json(source_path))
    audit = _read_json(audit_path)
    validate_vertical_audit(audit)
    _assert_fail_closed(audit, context="vertical provenance audit")
    if (
        audit.get("opening_id") != "OP002"
        or audit["vertical_parameters"]["wall_height_m"]["provenance_class"] != "research_assumption"
        or audit["vertical_parameters"]["head_m"]["provenance_class"] != "research_assumption"
        or audit["vertical_parameters"]["sill_m"]["treatment"] != "unknown"
        or audit["vertical_parameters"]["sill_m"]["usable_for_reversible_research_display"] is not False
        or audit["isolated_blender_research_display"]["opening_geometry_authorized"] is not False
    ):
        raise ValueError("vertical audit display handoff drift")
    source_document_sha256 = _file_hash(source_path)
    layer1, layer1_artifacts = _validate_layer1(
        layer1_manifest_path,
        source_structure_hash=document["structure_hash"],
        source_document_sha256=source_document_sha256,
    )
    opening = next(
        row
        for row in document["opening_contract"]["openings"]
        if row["id"] == "OP002"
    )
    host_id = opening["host"]["owning_wall_atom_id"]
    host = next(atom for atom in document["wall_graph"]["atoms"] if atom["id"] == host_id)
    segment = deepcopy(opening["effective_void"]["segment_m"])
    (x0, y0), (x1, y1) = segment
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        raise ValueError("OP002 display segment is empty")
    normal = [-dy / length, dx / length]
    wall_height = float(audit["vertical_parameters"]["wall_height_m"]["observed_value"])
    head = float(audit["vertical_parameters"]["head_m"]["observed_value"])
    host_half_thickness = float(host["thickness_m"]) / 2.0
    head_face_offset = host_half_thickness + 0.03
    head_display_segment = [
        [point[0] + normal[0] * head_face_offset, point[1] + normal[1] * head_face_offset]
        for point in segment
    ]
    result = {
        "schema": "op002-vertical-display-plan-v2",
        "branch_id": BRANCH_ID,
        "branch_kind": "vertical_parameter_research_display_without_opening_cut",
        "source_structure_hash": document["structure_hash"],
        "source_document_sha256": source_document_sha256,
        "vertical_audit_file_sha256": _file_hash(audit_path),
        "vertical_audit_candidate_hash": audit["candidate_hash"],
        "layer1_manifest_file_sha256": _file_hash(layer1_manifest_path),
        "layer1_blender_source_sha256": layer1_artifacts["blender_source"]["sha256"],
        "baseline": {
            "source_wall_atom_count": len(document["wall_graph"]["atoms"]),
            "intact_source_wall_count": layer1["wall_object_count"],
            "opening_cuts": 0,
            "wall_geometry_source": "v21_wall_atoms_rebuilt_without_splits",
        },
        "op002_xy_binding": {
            "host_atom_id": host_id,
            "source_segment_m": segment,
            "source_segment_length_m": length,
            "host_centerline_m": deepcopy(host["centerline_m"]),
            "host_thickness_m": float(host["thickness_m"]),
            "display_face_normal_xy": normal,
            "semantic": False,
        },
        "vertical_assumptions": {
            "wall_height_m": {
                "value": wall_height,
                "provenance_class": "research_assumption",
                "assumption_id": "ASSUME-Z-RESEARCH",
                "source_explicit": False,
                "human_authorized_default": False,
            },
            "head_m": {
                "value": head,
                "provenance_class": "research_assumption",
                "assumption_id": "ASSUME-Z-RESEARCH",
                "source_explicit": False,
                "human_authorized_default": False,
            },
            "sill_m": {
                "value": None,
                "provenance_class": "unknown",
                "reason": "unsupported_candidate_value_not_authorized",
                "source_explicit": False,
                "human_authorized_default": False,
            },
        },
        "guide_specs": [
            {
                "object_name": "GEO-RESEARCH-OP002-XY-LOCATOR",
                "role": "nonsemantic_xy_locator",
                "centerline_m": segment,
                "xy_thickness_m": 0.04,
                "z_min_m": wall_height + 0.01,
                "z_max_m": wall_height + 0.03,
                "source_fact": False,
                "opening_geometry": False,
            },
            {
                "object_name": "GEO-RESEARCH-OP002-HEAD-GUIDE",
                "role": "nonsemantic_head_assumption_guide",
                "centerline_m": head_display_segment,
                "xy_thickness_m": 0.03,
                "face_offset_m": head_face_offset,
                "z_center_m": head,
                "z_min_m": head - 0.02,
                "z_max_m": head + 0.02,
                "source_fact": False,
                "opening_geometry": False,
            },
        ],
        "view_contracts": [
            {"view_id": "top", "projection": "orthographic", "purpose": "verify unchanged XY source walls and locator"},
            {"view_id": "northeast", "projection": "orthographic_axonometric", "purpose": "show wall/head assumptions without a cut"},
            {"view_id": "front_closeup", "projection": "orthographic", "camera_side": "positive_display_face_normal", "purpose": "show head guide and intact host wall"},
        ],
        "labels": list(LABELS),
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
        source_path=source_path,
        audit_path=audit_path,
        layer1_manifest_path=layer1_manifest_path,
    )


def validate(
    candidate: Mapping[str, Any],
    *,
    source_path: Path = SOURCE,
    audit_path: Path = AUDIT,
    layer1_manifest_path: Path = LAYER1_MANIFEST,
) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(
        source_path=Path(source_path),
        audit_path=Path(audit_path),
        layer1_manifest_path=Path(layer1_manifest_path),
        _skip_validate=True,
    )
    if actual != expected:
        raise ValueError("OP002 vertical display plan evidence/derivation drift")
    _assert_fail_closed(actual, context="OP002 vertical display plan")
    if (
        actual.get("schema") != "op002-vertical-display-plan-v2"
        or actual["baseline"] != {
            "source_wall_atom_count": 35,
            "intact_source_wall_count": 35,
            "opening_cuts": 0,
            "wall_geometry_source": "v21_wall_atoms_rebuilt_without_splits",
        }
        or actual.get("guide_object_count") != 2
        or actual.get("labels") != LABELS
        or actual.get("forbidden_object_roles") != FORBIDDEN_OBJECT_ROLES
        or any(actual.get(key) is not False for key in (
            "opening_geometry_created",
            "floor_cut_created",
            "sill_geometry_created",
            "door_leaf_created",
            "lintel_structural_element_created",
            "ifc_opening_created",
            "ifc_void_or_fill_created",
        ))
    ):
        raise ValueError("OP002 vertical display plan geometry/policy drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("OP002 vertical display plan candidate hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--audit", type=Path, default=AUDIT)
    parser.add_argument("--layer1-manifest", type=Path, default=LAYER1_MANIFEST)
    parser.add_argument("--output", type=Path, default=OUT / "plan.json")
    args = parser.parse_args(argv)
    result = build(
        source_path=args.source,
        audit_path=args.audit,
        layer1_manifest_path=args.layer1_manifest,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output.parent / "REPORT.md").write_text(
        "# OP002 Layer3B vertical research display plan v2\n\n"
        "The display rebuilds all 35 source wall atoms intact and creates zero opening cuts. A blue XY locator is "
        "placed above the wall top and an orange non-semantic head guide is placed at the 2.1 m research-assumption "
        "level in front of the wall face. Sill remains null/unknown; no floor cut, opening volume, door leaf, threshold, "
        "structural lintel, IFC relation, traversability, adjacency, source correction, score, or formal build is allowed.\n",
        encoding="utf-8",
    )
    (args.output.parent / "REVIEW_CARD_ZH.md").write_text(
        "# OP002 Layer3B 垂直研究显示计划 v2\n\n"
        "计划重建 35 段完整源墙，开口切割数为 0。蓝色 XY 定位条放在墙顶上方，橙色 head guide 以非语义"
        "标尺形式放在墙面前方 z=2.1 m；2.8 m 与 2.1 m 均是未验证研究假设，sill 保持 null/unknown。"
        "不创建落地洞、门扇、门槛、结构过梁、IFC、通行、邻接、源修订、评分或正式模型。\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_candidate_hash", "BRANCH_ID", "LABELS", "FORBIDDEN_OBJECT_ROLES"]
