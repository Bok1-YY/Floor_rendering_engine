"""Audit OP002 vertical values and separate facts from research assumptions."""
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
from tools.goal_loop_v2.build_op002_clean_subtype_bundle import validate as validate_subtype_bundle
from tools.goal_loop_v2.registration import _apply, _inverse

SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
SOURCE_IMAGE = ROOT / "data/goal_loop_v2/references/1308/canonical-raw-portrait.png"
SUBTYPE_BUNDLE = ROOT / "reports/op002_clean_subtype_20260903/bundle.json"
VERTICAL_EVIDENCE = ROOT / "reports/op002_vertical_evidence_20260901/op002-vertical-evidence.json"
OUT = ROOT / "reports/op002_vertical_provenance_20260903"
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


def _validate_vertical_evidence(
    evidence_path: Path,
    evidence: Mapping[str, Any],
    *,
    source_document_sha256: str,
    source_structure_hash: str,
    source_image_path: Path,
    metric_registration: list[list[float]],
    source_segment_m: list[list[float]],
    host_atom_id: str,
) -> dict[str, Any]:
    value = deepcopy(dict(evidence))
    source_image_sha256 = _file_hash(source_image_path)
    metric_to_pixel = _inverse(metric_registration)
    recomputed_pixels = [list(_apply(metric_to_pixel, point)) for point in source_segment_m]
    if (
        value.get("schema") != "op002-door-evidence-v2"
        or value.get("sample_id") != "1308"
        or value.get("opening_id") != "OP002"
        or value.get("source_document_sha256") != source_document_sha256
        or value.get("source_structure_hash") != source_structure_hash
        or value.get("source_sha256") != source_image_sha256
        or Path(value.get("source_path", "")).name != source_image_path.name
        or value.get("metric_segment_m") != source_segment_m
        or value.get("host_atom_id") != host_atom_id
        or value.get("semantic_status") != "candidate_only"
        or value.get("traversable_status") != "candidate_only"
        or value.get("semantic_promotion") is not False
        or value.get("build_authorized") is not False
        or value.get("ready") is not False
    ):
        raise ValueError("OP002 vertical evidence identity/authority drift")
    registration = value.get("registration_validation", {})
    if (
        registration.get("max_endpoint_error_px", math.inf) > registration.get("tolerance_px", -math.inf)
        or registration.get("max_endpoint_error_px") != 0.0
        or any(
            math.dist(actual, expected) > 1e-6
            for actual, expected in zip(value.get("source_segment_px", []), recomputed_pixels)
        )
        or any(
            math.dist(actual, expected) > 1e-6
            for actual, expected in zip(registration.get("expected_pixel_segment", []), recomputed_pixels)
        )
        or len(value.get("source_segment_px", [])) != 2
        or len(registration.get("expected_pixel_segment", [])) != 2
    ):
        raise ValueError("OP002 vertical evidence registration drift")
    observations = value.get("observations")
    if (
        not isinstance(observations, list)
        or not any("does not promote door type, jamb, height, adjacency, or build authorization" in item for item in observations)
    ):
        raise ValueError("OP002 vertical evidence lost no-height-promotion disclosure")
    artifacts = {}
    for kind in ("crop_overlay", "full_overlay"):
        artifact = value.get("artifacts", {}).get(kind)
        if not isinstance(artifact, dict):
            raise ValueError("OP002 vertical evidence artifact missing")
        declared_path = Path(artifact["path"])
        local_path = evidence_path.parent / declared_path.name
        path = local_path if local_path.is_file() else declared_path
        if (
            not path.is_file()
            or _file_hash(path) != artifact.get("sha256")
        ):
            raise ValueError("OP002 vertical evidence artifact drift")
        artifacts[kind] = {
            "relative_path": path.name,
            "bytes": path.stat().st_size,
            "sha256": artifact["sha256"],
            "size": artifact["size"],
        }
    return {
        "file_sha256": _file_hash(evidence_path),
        "source_image_sha256": source_image_sha256,
        "source_segment_px": recomputed_pixels,
        "registration_max_endpoint_error_px": registration["max_endpoint_error_px"],
        "explicit_no_height_promotion_disclosure": True,
        "artifacts": artifacts,
    }


def build(
    *,
    source_path: Path = SOURCE,
    source_image_path: Path = SOURCE_IMAGE,
    subtype_bundle_path: Path = SUBTYPE_BUNDLE,
    vertical_evidence_path: Path = VERTICAL_EVIDENCE,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    source_path = Path(source_path)
    source_image_path = Path(source_image_path)
    subtype_bundle_path = Path(subtype_bundle_path)
    vertical_evidence_path = Path(vertical_evidence_path)
    document = validate_v21_document(_read_json(source_path))
    subtype = _read_json(subtype_bundle_path)
    validate_subtype_bundle(subtype)
    if (
        subtype.get("opening_id") != "OP002"
        or subtype.get("visual_subtype_candidate") != "door"
        or subtype.get("accepted_for_layer3a_op002_visual_subtype_research") is not True
        or subtype.get("vertical_parameters_reviewed") is not False
        or subtype.get("source_subtype_confirmation") is not False
    ):
        raise ValueError("OP002 subtype-to-vertical handoff drift")

    try:
        opening = next(
            item
            for item in document["opening_contract"]["openings"]
            if item["id"] == "OP002"
        )
        assumption = next(
            item
            for item in document["assumptions"]["items"]
            if item["id"] == "ASSUME-Z-RESEARCH"
        )
    except StopIteration as exc:
        raise ValueError("OP002 opening or Z assumption is missing") from exc
    effective_void = opening["effective_void"]
    if (
        opening.get("status") != "candidate"
        or opening.get("source_observation", {}).get("status") != "candidate"
        or effective_void.get("status") != "candidate"
        or "ASSUME-Z-RESEARCH" not in opening.get("assumption_ids", [])
        or assumption.get("basis") != "research_default"
        or assumption.get("status") != "unverified"
        or assumption.get("category") != "z_geometry"
        or assumption.get("build_policy") != "allow_research_only"
        or assumption.get("value", {}).get("door_head_m") != 2.1
        or assumption.get("value", {}).get("wall_height_m") != 2.8
        or "sill_m" in assumption.get("value", {})
        or effective_void.get("head_m") != assumption["value"]["door_head_m"]
        or effective_void.get("sill_m") != 0.0
    ):
        raise ValueError("OP002 vertical source/assumption contract drift")

    source_document_sha256 = _file_hash(source_path)
    vertical_binding = _validate_vertical_evidence(
        vertical_evidence_path,
        _read_json(vertical_evidence_path),
        source_document_sha256=source_document_sha256,
        source_structure_hash=document["structure_hash"],
        source_image_path=source_image_path,
        metric_registration=document["source"]["metric_registration"]["canonical_px_to_metric_3x3"],
        source_segment_m=effective_void["segment_m"],
        host_atom_id=opening["host"]["owning_wall_atom_id"],
    )
    result = {
        "schema": "op002-vertical-provenance-audit-v2",
        "opening_id": "OP002",
        "source_structure_hash": document["structure_hash"],
        "source_document_sha256": source_document_sha256,
        "clean_subtype_bundle_file_sha256": _file_hash(subtype_bundle_path),
        "clean_subtype_bundle_candidate_hash": subtype["candidate_hash"],
        "vertical_evidence": vertical_binding,
        "opening_candidate_state": {
            "opening_status": opening["status"],
            "source_observation_status": opening["source_observation"]["status"],
            "effective_void_status": effective_void["status"],
            "assumption_ids": deepcopy(opening["assumption_ids"]),
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
                "observed_value": 2.8,
                "provenance_class": "research_assumption",
                "assumption_id": "ASSUME-Z-RESEARCH",
                "source_explicit": False,
                "human_authorized_default": False,
                "usable_for_reversible_research_display": True,
                "eligible_for_source_promotion": False,
            },
            "head_m": {
                "observed_value": effective_void["head_m"],
                "provenance_class": "research_assumption",
                "assumption_id": "ASSUME-Z-RESEARCH",
                "source_explicit": False,
                "human_authorized_default": False,
                "usable_for_reversible_research_display": True,
                "eligible_for_source_promotion": False,
            },
            "sill_m": {
                "observed_value": effective_void["sill_m"],
                "provenance_class": "unsupported_candidate_value",
                "assumption_id": None,
                "source_explicit": False,
                "human_authorized_default": False,
                "treatment": "unknown",
                "usable_for_reversible_research_display": False,
                "eligible_for_source_promotion": False,
            },
        },
        "visual_subtype_candidate": subtype["visual_subtype_candidate"],
        "visual_subtype_advisory_is_vertical_authority": False,
        "vertical_evidence_supports_height": False,
        "isolated_blender_research_display": {
            "policy_status": "pending_policy_guardian",
            "wall_height_m": 2.8,
            "head_m": 2.1,
            "sill_m": None,
            "must_label_head_as_research_assumption": True,
            "must_label_sill_as_unknown": True,
            "opening_geometry_authorized": False,
        },
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
        source_image_path=source_image_path,
        subtype_bundle_path=subtype_bundle_path,
        vertical_evidence_path=vertical_evidence_path,
    )


def validate(
    candidate: Mapping[str, Any],
    *,
    source_path: Path = SOURCE,
    source_image_path: Path = SOURCE_IMAGE,
    subtype_bundle_path: Path = SUBTYPE_BUNDLE,
    vertical_evidence_path: Path = VERTICAL_EVIDENCE,
) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(
        source_path=Path(source_path),
        source_image_path=Path(source_image_path),
        subtype_bundle_path=Path(subtype_bundle_path),
        vertical_evidence_path=Path(vertical_evidence_path),
        _skip_validate=True,
    )
    if actual != expected:
        raise ValueError("OP002 vertical provenance evidence/derivation drift")
    _assert_fail_closed(actual, context="OP002 vertical provenance audit")
    if (
        actual.get("schema") != "op002-vertical-provenance-audit-v2"
        or actual.get("opening_id") != "OP002"
        or actual.get("visual_subtype_advisory_is_vertical_authority") is not False
        or actual.get("vertical_evidence_supports_height") is not False
        or actual["vertical_parameters"]["sill_m"]["treatment"] != "unknown"
        or actual["isolated_blender_research_display"]["policy_status"] != "pending_policy_guardian"
        or actual["isolated_blender_research_display"]["opening_geometry_authorized"] is not False
    ):
        raise ValueError("OP002 vertical provenance scope drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("OP002 vertical provenance candidate hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--source-image", type=Path, default=SOURCE_IMAGE)
    parser.add_argument("--subtype-bundle", type=Path, default=SUBTYPE_BUNDLE)
    parser.add_argument("--vertical-evidence", type=Path, default=VERTICAL_EVIDENCE)
    parser.add_argument("--output", type=Path, default=OUT / "audit.json")
    args = parser.parse_args(argv)
    result = build(
        source_path=args.source,
        source_image_path=args.source_image,
        subtype_bundle_path=args.subtype_bundle,
        vertical_evidence_path=args.vertical_evidence,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output.parent / "REPORT.md").write_text(
        "# OP002 vertical provenance audit v2\n\n"
        "The current 2.8 m wall height and 2.1 m head are unverified ASSUME-Z-RESEARCH defaults, not explicit source "
        "dimensions. The candidate 0.0 m sill has no matching assumption-registry authorization and is treated as "
        "unknown. The earlier vertical evidence verifies XY registration and explicitly disclaims height promotion. "
        "The visual door advisory has no vertical authority. No source, effective-void, traversability, adjacency, "
        "score, semantic, or formal-build state is promoted.\n",
        encoding="utf-8",
    )
    (args.output.parent / "REVIEW_CARD_ZH.md").write_text(
        "# OP002 垂直参数来源审计 v2\n\n"
        "当前墙高 2.8 m 和门头 2.1 m 来自未验证的 ASSUME-Z-RESEARCH 研究默认值，并非图纸明确尺寸。"
        "候选 sill=0.0 m 在 assumption registry 中没有授权来源，因此按 unknown 处理。旧 vertical evidence "
        "只证明二维坐标注册，并明确不提升高度。视觉上的门候选不具备垂直参数权限；源数据、有效洞口、通行、"
        "邻接、评分和正式建模均保持未确认。\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_candidate_hash"]
