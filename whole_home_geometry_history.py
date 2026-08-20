"""Publish immutable Plan-to-3D gold runs into the application's history view.

The benchmark truth remains isolated below ``data/external_datasets`` while this
module creates a checksum-bound, read-only audit copy below ``output_files``.
Production modelling code never consumes the copied truth; it is exposed only
through the records/audit UI so a human can inspect the exact evidence that made
a level pass or fail.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from .config import MAIN_OUTPUT_DIR
from .records import load_records_file, record_file_lock, save_records_file
from .whole_home_dataset import sha256_file
from .whole_home_geometry import canonical_hash
from .whole_home_ifc_gold import RASTER_GOLD_CHECKS


HISTORY_SCHEMA_VERSION = 1
HISTORY_PUBLISHER_VERSION = "plan-to-3d-history-v1"

_METRIC_LABELS = {
    "wall_footprint_iou": "墙体轮廓 IoU",
    "wall_boundary_p95_m": "墙体边界 p95",
    "room_footprint_iou": "房间轮廓 IoU",
    "opening_precision": "门窗精确率",
    "opening_recall": "门窗召回率",
    "opening_center_p95_m": "门窗中心偏差 p95",
    "opening_width_p95_m": "门窗宽度偏差 p95",
    "wall_assembly_coverage": "WallAssembly 覆盖率",
    "scale_anchor_count": "真实尺寸锚点数量",
    "scale_disagreement": "尺寸锚点比例分歧",
    "registration_roundtrip_px": "图像配准往返误差",
    "wall_centerline_p95_m": "墙中心线 p95",
    "wall_ink_support_ratio": "墙体墨迹支持率",
    "room_iou": "房间 IoU",
    "truth_opening_count": "真值门窗数量",
    "observed_opening_count": "识别门窗数量",
    "matched_opening_count": "匹配门窗数量",
    "truth_wall_count": "真值墙体数量",
    "model_wall_assembly_count": "模型墙体装配数量",
    "model_opening_count": "模型门窗数量",
}

_ARTIFACT_LABELS = {
    "source.ifc": "公开 IFC 原始真值",
    "source_snapshot.png": "公开来源官方快照",
    "case_manifest.json": "同源派生清单",
    "gold_result.json": "CAD 金标准报告",
    "raster_gold_result.json": "普通户型图金标准报告",
    "raster_compressed_gold_result.json": "压缩户型图金标准报告",
    "input_dimensioned.png": "带尺寸普通户型图",
    "input_compressed.png": "确定性压缩户型图",
    "input_double_line.dxf": "双线墙 CAD 输入",
    "truth_geometry.json": "独立 IFC 平面真值",
    "truth_geometry_manifest.json": "独立 IFC 三角网格真值",
    "truth_gray_model.obj": "独立 3D 灰模 OBJ",
    "truth_gray_preview.png": "独立 3D 灰模预览",
}

_PREVIEW_FILES = (
    ("source_snapshot.png", "公开来源官方快照"),
    ("input_dimensioned.png", "带真实尺寸户型图"),
    ("truth_gray_preview.png", "独立 3D 灰模真值"),
)

_CASE_LICENSES = {
    "ifcbench_fantasy_residential_building_1": "MIT",
    "ifcbench_samuel_macalister_sample_house": "GPL-3.0-or-later",
}


class GeometryHistoryError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GeometryHistoryError(f"cannot read audit JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GeometryHistoryError(f"audit JSON must be an object: {path}")
    return value


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    return cleaned.strip("._") or "geometry_case"


def _timestamp(path: Path) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))


def _display_number(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.8g}"
    return str(value)


def _metric_unit(field: str) -> str:
    if field.endswith("_m"):
        return "m"
    if field.endswith("_px"):
        return "px"
    if field.endswith("_iou") or field in {
        "opening_precision", "opening_recall", "wall_assembly_coverage",
        "scale_disagreement", "wall_ink_support_ratio", "room_iou",
    }:
        return "ratio"
    return "count" if field.endswith("_count") else ""


def _threshold_pass(actual: float, operator: str, threshold: float) -> bool:
    return actual >= threshold if operator == ">=" else actual <= threshold


def _cad_metric_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    metrics = report.get("metrics") if isinstance(report.get("metrics"), Mapping) else {}
    thresholds = report.get("thresholds") if isinstance(report.get("thresholds"), Mapping) else {}
    rows: list[dict[str, Any]] = []
    for threshold_field, threshold_value in thresholds.items():
        name = str(threshold_field)
        if name.endswith("_min"):
            field, operator = name[:-4], ">="
        elif name.endswith("_max"):
            field, operator = name[:-4], "<="
        else:
            continue
        if field not in metrics:
            continue
        actual = float(metrics[field])
        threshold = float(threshold_value)
        rows.append({
            "metric_id": f"cad.{field}", "field": field,
            "label": _METRIC_LABELS.get(field, field), "actual": actual,
            "actual_display": _display_number(actual), "operator": operator,
            "threshold": threshold, "threshold_display": _display_number(threshold),
            "unit": _metric_unit(field),
            "status": "passed" if _threshold_pass(actual, operator, threshold) else "failed",
        })
    return rows


def _raster_metric_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    metrics = report.get("metrics") if isinstance(report.get("metrics"), Mapping) else {}
    thresholds = report.get("thresholds") if isinstance(report.get("thresholds"), Mapping) else {}
    fallback = {field: {"operator": operator, "value": value}
                for field, operator, value in RASTER_GOLD_CHECKS}
    checks = thresholds or fallback
    rows: list[dict[str, Any]] = []
    for field, raw in checks.items():
        if field not in metrics or not isinstance(raw, Mapping):
            continue
        operator = str(raw.get("operator") or "<=")
        threshold = float(raw.get("value"))
        actual = float(metrics[field])
        rows.append({
            "metric_id": f"raster.{field}", "field": field,
            "label": _METRIC_LABELS.get(field, field), "actual": actual,
            "actual_display": _display_number(actual), "operator": operator,
            "threshold": threshold, "threshold_display": _display_number(threshold),
            "unit": _metric_unit(field),
            "status": "passed" if _threshold_pass(actual, operator, threshold) else "failed",
        })
    return rows


def _file_evidence(
    logical_name: str, path: Path, *, expected_sha256: str = "",
    expected_size: int | None = None,
) -> dict[str, Any]:
    exists = path.is_file()
    actual_sha = sha256_file(path) if exists else ""
    actual_size = path.stat().st_size if exists else 0
    problems: list[str] = []
    if not exists:
        problems.append("missing")
    if expected_sha256 and actual_sha != expected_sha256:
        problems.append("sha256_mismatch")
    if expected_size is not None and actual_size != expected_size:
        problems.append("size_mismatch")
    return {
        "artifact_id": logical_name,
        "label": _ARTIFACT_LABELS.get(logical_name, logical_name),
        "source_path": str(path.resolve()),
        "file_name": path.name,
        "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "size_bytes": actual_size,
        "sha256": actual_sha,
        "expected_sha256": expected_sha256 or actual_sha,
        "integrity_status": "passed" if not problems else "failed",
        "integrity_problems": problems,
    }


def build_geometry_audit_payload(
    gold_dir: Path | str, *, level: str, case_title: str = "",
) -> dict[str, Any]:
    """Build and verify a portable audit payload without writing product data."""
    root = Path(gold_dir).resolve()
    case_manifest_path = root / "case_manifest.json"
    cad_path = root / "gold_result.json"
    raster_path = root / "raster_gold_result.json"
    raster_variant_paths = sorted(root.glob("raster_*_gold_result.json"))
    manifest = _read_json(case_manifest_path)
    cad = _read_json(cad_path)
    raster = _read_json(raster_path)
    case_id = str(manifest.get("case_id") or cad.get("case_id") or "")
    if not case_id or cad.get("case_id") != case_id or raster.get("case_id") != case_id:
        raise GeometryHistoryError("case id differs between case manifest and gold reports")

    source_path = Path(str(manifest.get("source_path") or "")).resolve()
    source_expected = str(manifest.get("source_sha256") or "")
    artifacts: list[dict[str, Any]] = [
        _file_evidence("source.ifc", source_path, expected_sha256=source_expected),
        _file_evidence("case_manifest.json", case_manifest_path),
        _file_evidence("gold_result.json", cad_path),
        _file_evidence("raster_gold_result.json", raster_path),
    ]
    artifacts.extend(
        _file_evidence(path.name, path) for path in raster_variant_paths
        if path.name != raster_path.name
    )
    snapshot = source_path.with_name("snapshot.png")
    if snapshot.is_file():
        artifacts.append(_file_evidence("source_snapshot.png", snapshot))
    locked = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), Mapping) else {}
    for file_name, lock in sorted(locked.items()):
        if not isinstance(lock, Mapping):
            continue
        artifacts.append(_file_evidence(
            str(file_name), root / str(file_name),
            expected_sha256=str(lock.get("sha256") or ""),
            expected_size=int(lock["size_bytes"]) if lock.get("size_bytes") is not None else None,
        ))

    integrity_failures = [
        {"artifact_id": row["artifact_id"], "problems": row["integrity_problems"]}
        for row in artifacts if row["integrity_status"] != "passed"
    ]
    cad_rows = _cad_metric_rows(cad)
    raster_rows = _raster_metric_rows(raster)
    production_parse = cad.get("production_parse") if isinstance(cad.get("production_parse"), Mapping) else {}
    hard_errors = production_parse.get("hard_errors") if isinstance(production_parse.get("hard_errors"), list) else []
    channel_failed = (
        cad.get("status") != "passed" or raster.get("status") != "passed"
        or any(row["status"] != "passed" for row in cad_rows + raster_rows)
        or bool(hard_errors)
    )
    status = "failed" if integrity_failures or channel_failed else "passed"
    payload: dict[str, Any] = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "publisher_version": HISTORY_PUBLISHER_VERSION,
        "audit_kind": "plan_to_3d_geometry_gold",
        "level": level.upper(), "case_id": case_id,
        "title": case_title or case_id,
        "status": status,
        "executed_at": _timestamp(max((cad_path, raster_path), key=lambda path: path.stat().st_mtime)),
        "runner_version": str(cad.get("gold_runner_version") or manifest.get("derivation_version") or ""),
        "source": {
            "dataset": "IFC-Bench V2",
            "license": _CASE_LICENSES.get(case_id, "CC BY 4.0"),
            "source_sha256": source_expected,
            "ifcopenshell_version": manifest.get("ifcopenshell_version"),
            "storey": manifest.get("storey"),
            "counts": manifest.get("counts"),
            "coordinate_contract": manifest.get("coordinate_contract"),
            "metadata_unit_scale_to_m": manifest.get("metadata_unit_scale_to_m"),
            "extraction_warnings": manifest.get("extraction_warnings") or [],
            "raster_variants": manifest.get("raster_variants") or {},
        },
        "channels": [
            {
                "channel_id": "cad", "label": "CAD / DXF 生产解析链",
                "status": str(cad.get("status") or "failed"),
                "source_sha256": cad.get("source_sha256"),
                "derived_source_sha256": cad.get("derived_dxf_sha256"),
                "model_facts_hash": production_parse.get("model_facts_hash"),
                "hard_errors": hard_errors,
                "warning_count": int(production_parse.get("warning_count") or 0),
                "metrics": cad_rows,
            },
            {
                "channel_id": "raster", "label": "普通户型图配准链",
                "status": str(raster.get("status") or "failed"),
                "source_sha256": raster.get("source_sha256"),
                "registration_hash": raster.get("registration_hash"),
                "evidence_hash": raster.get("evidence_hash"),
                "metrics": raster_rows,
            },
        ],
        "artifacts": artifacts,
        "integrity": {
            "status": "passed" if not integrity_failures else "failed",
            "checked_count": len(artifacts), "failures": integrity_failures,
        },
        "issues": list(cad.get("issues") or []) + list(raster.get("issues") or []),
        "review": {
            "checked_metric_ids": [], "reviewer": "", "note": "", "reviewed_at": "",
        },
    }
    payload["audit_hash"] = canonical_hash({
        key: value for key, value in payload.items() if key not in {"executed_at", "review"}
    })
    return payload


def _copy_audit_artifacts(
    payload: dict[str, Any], *, output_root: Path, record_id: str,
) -> None:
    destination = output_root / "geometry_audits" / _safe_name(payload["case_id"]) / record_id
    destination.mkdir(parents=True, exist_ok=True)
    for artifact in payload["artifacts"]:
        if artifact["integrity_status"] != "passed":
            artifact["available"] = False
            continue
        source = Path(artifact["source_path"])
        target = destination / f"{_safe_name(artifact['artifact_id'])}__{source.name}"
        if not target.is_file() or sha256_file(target) != artifact["sha256"]:
            shutil.copy2(source, target)
        artifact["relative_path"] = target.relative_to(output_root).as_posix()
        artifact["available"] = True


def archive_geometry_gold_history(
    gold_dir: Path | str, *, level: str, case_title: str = "",
    output_root: Path | str = MAIN_OUTPUT_DIR,
) -> dict[str, Any]:
    """Create an immutable, idempotent record and return its public locator."""
    payload = build_geometry_audit_payload(gold_dir, level=level, case_title=case_title)
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    record_id = f"geometry_audit_{payload['level'].lower()}_{payload['audit_hash'][:16]}"
    _copy_audit_artifacts(payload, output_root=root, record_id=record_id)

    artifact_by_id = {row["artifact_id"]: row for row in payload["artifacts"]}
    results: list[dict[str, Any]] = []
    for artifact_id, label in _PREVIEW_FILES:
        artifact = artifact_by_id.get(artifact_id)
        if not artifact or not artifact.get("available"):
            continue
        results.append({
            "result_id": f"{record_id}_{_safe_name(artifact_id)}",
            "result_image_file": artifact["relative_path"],
            "model": "geometry-audit", "model_label": label,
            "comment": f"SHA-256 {artifact['sha256']}",
            "favorite": False, "best": False,
            "review_status": "pass" if payload["status"] == "passed" else "unreviewed",
            "review_tags": [payload["level"], "几何验收证据"],
            "review_note": "只读证据预览",
            "result_timestamp": payload["executed_at"],
        })
    record = {
        "id": record_id,
        "timestamp": payload["executed_at"],
        "room_type": payload["level"],
        "workflow_mode": "Plan-to-3D 几何验收",
        "immutable_audit": True,
        "geometry_audit": payload,
        "results": results,
    }
    directory = root / "geometry_audits"
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"Plan-to-3D_{payload['level']}_{_safe_name(payload['case_id'])}_记录.json"
    with record_file_lock(str(json_path)):
        records = load_records_file(str(json_path)) if json_path.exists() else []
        if not isinstance(records, list):
            records = []
        existing = next((row for row in records if isinstance(row, dict) and row.get("id") == record_id), None)
        if existing is None:
            records.insert(0, record)
        else:
            # Evidence is immutable; only repair missing copied files/URLs on an idempotent publish.
            preserved_review = ((existing.get("geometry_audit") or {}).get("review")
                                if isinstance(existing.get("geometry_audit"), Mapping) else None)
            if isinstance(preserved_review, Mapping):
                record["geometry_audit"]["review"] = dict(preserved_review)
            records[records.index(existing)] = record
        save_records_file(str(json_path), records)
    return {
        "record_id": record_id, "json_path": str(json_path),
        "status": payload["status"], "audit_hash": payload["audit_hash"],
        "artifact_count": len(payload["artifacts"]), "preview_count": len(results),
        "created": existing is None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gold_dir", type=Path)
    parser.add_argument("--level", required=True, choices=("L1", "L2", "L3", "L4", "L5"))
    parser.add_argument("--case-title", default="")
    parser.add_argument("--output-root", type=Path, default=Path(MAIN_OUTPUT_DIR))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        result = archive_geometry_gold_history(
            arguments.gold_dir, level=arguments.level, case_title=arguments.case_title,
            output_root=arguments.output_root,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["status"] == "passed" else 2
    except (GeometryHistoryError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "HISTORY_SCHEMA_VERSION", "HISTORY_PUBLISHER_VERSION", "GeometryHistoryError",
    "build_geometry_audit_payload", "archive_geometry_gold_history", "main",
]
