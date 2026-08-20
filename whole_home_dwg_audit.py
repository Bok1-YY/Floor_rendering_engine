"""Independent truth and immutable live-audit records for the DWG regression set.

The production CAD parser is deliberately not imported here.  Truth is either an
explicitly pending annotation manifest or independently reviewed geometry.  A
live audit may reference production outputs, but it can only pass when the truth
is verified, every deterministic metric and visual check passes, and all five
comparison images are present and checksum-bound.
"""
from __future__ import annotations

import argparse
import json
import math
import mimetypes
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, ImageChops, ImageDraw

from .config import MAIN_OUTPUT_DIR
from .records import load_records_file, record_file_lock, save_records_file
from .whole_home_dataset import sha256_file
from .whole_home_geometry import canonical_hash, validate_geometry_manifest


TRUTH_SCHEMA_NAME = "DwgGeometryTruthV1"
LIVE_AUDIT_SCHEMA_NAME = "DwgLiveGeometryAuditV2"
LEGACY_LIVE_AUDIT_SCHEMA_NAME = "DwgLiveGeometryAuditV1"
DWG_AUDIT_PUBLISHER_VERSION = "dwg-live-geometry-audit-v2"
CAMERA_CONTRACT_NAME = "whole_home_sky_down_orthographic_v2"
MODEL_COORDINATE_SYSTEM_V2 = "right-handed-y-up-x-east-z-south-v2"
TRUTH_COORDINATE_SYSTEM_V2 = "cad-meters-x-east-y-north-v2"

TRUTH_STATUSES = frozenset({"pending", "verified"})
LIVE_STATUSES = frozenset({"pending", "passed", "failed"})
LEVELS = frozenset({"L1", "L2", "L3", "L4", "L5"})
EVIDENCE_ROLES = (
    "cad_preview",
    "structure_truth",
    "model_topdown",
    "overlay",
    "diff",
)
VISUAL_CHECK_IDS = (
    "camera_sky_to_ground",
    "axis_orientation",
    "top_face_visibility",
    "candidate_view",
    "outer_footprint",
    "interior_walls",
    "openings",
    "room_topology",
    "forbidden_entities",
)

_EVIDENCE_LABELS = {
    "cad_preview": "CAD 原图",
    "structure_truth": "独立结构真值",
    "model_topdown": "3D 正交俯视",
    "overlay": "CAD / 3D 叠加",
    "diff": "几何差分",
    "software_model_topdown": "软件正交顶投影（非 WebGL）",
    "software_overlay": "软件 CAD / 模型叠加（非 WebGL）",
    "software_diff": "软件几何差分（非 WebGL）",
    "software_projection_report": "软件正交投影合同",
    "failure_screenshot": "失败页面截图",
    "audit_report": "DWG 审计报告",
}
_VISUAL_LABELS = {
    "camera_sky_to_ground": "相机从天空朝地面观察",
    "axis_orientation": "CAD +X 向右、+Y 向屏幕上方",
    "top_face_visibility": "屋顶/墙顶面可见且未从地底反看",
    "candidate_view": "平面候选选择正确",
    "outer_footprint": "外轮廓一致",
    "interior_walls": "内墙位置与连续性一致",
    "openings": "门窗位置与尺寸一致",
    "room_topology": "房间数量与连通关系一致",
    "forbidden_entities": "家具设备未误建为墙",
}
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,127}$")
_FORBIDDEN_TRUTH_KEYS = frozenset({
    "production_parser",
    "production_parse",
    "selected_candidate_id",
    "model_facts",
    "model_facts_hash",
    "geometry_manifest_hash",
})


class DwgAuditError(RuntimeError):
    """Raised when truth, run evidence, or archive integrity is invalid."""


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DwgAuditError(f"cannot read DWG audit JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DwgAuditError(f"DWG audit JSON must contain an object: {path}")
    return value


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", str(value).strip())
    return cleaned.strip("._") or "dwg_case"


def _require_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DwgAuditError(f"{field} is required")
    return text


def _require_hash(value: Any, field: str) -> str:
    digest = str(value or "").strip().lower()
    if not _HASH_RE.fullmatch(digest):
        raise DwgAuditError(f"{field} must be a lowercase SHA-256 hex digest")
    return digest


def _require_case_id(value: Any) -> str:
    case_id = _require_text(value, "case_id")
    if not _SAFE_ID_RE.fullmatch(case_id):
        raise DwgAuditError("case_id must be a portable 1-128 character identifier")
    return case_id


def _deepcopy_json(value: Any, field: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise DwgAuditError(f"{field} must contain finite JSON values") from exc


def _find_forbidden_truth_key(value: Any, prefix: str = "truth") -> str:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if key_text in _FORBIDDEN_TRUTH_KEYS:
                return f"{prefix}.{key_text}"
            found = _find_forbidden_truth_key(child, f"{prefix}.{key_text}")
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_forbidden_truth_key(child, f"{prefix}[{index}]")
            if found:
                return found
    return ""


def _point(value: Any, field: str) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        raise DwgAuditError(f"{field} must be an [x, y] point")
    result = [float(value[0]), float(value[1])]
    if not all(math.isfinite(number) for number in result):
        raise DwgAuditError(f"{field} contains NaN or infinity")
    return result


def _point3(value: Any, field: str) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        raise DwgAuditError(f"{field} must be an [x, y, z] point")
    result = [float(value[0]), float(value[1]), float(value[2])]
    if not all(math.isfinite(number) for number in result):
        raise DwgAuditError(f"{field} contains NaN or infinity")
    return result


def validate_orthographic_camera_contract_v2(value: Any) -> dict[str, Any]:
    """Validate the browser capture camera instead of trusting a free-form hash."""
    if not isinstance(value, Mapping):
        raise DwgAuditError("passed live audit requires camera_contract v2")
    raw = _deepcopy_json(dict(value), "camera_contract")
    if int(raw.get("schema_version") or 0) != 2:
        raise DwgAuditError("camera_contract schema_version must be 2")
    exact = {
        "contract": CAMERA_CONTRACT_NAME,
        "coordinate_system": MODEL_COORDINATE_SYSTEM_V2,
        "projection": "orthographic",
        "renderer": "threejs_webgl",
    }
    for field, expected in exact.items():
        if raw.get(field) != expected:
            raise DwgAuditError(f"camera_contract.{field} must be {expected}")
    if raw.get("webgl_capture") is not True:
        raise DwgAuditError("camera_contract.webgl_capture must be true")
    if int(raw.get("model_coordinate_contract_version") or 0) != 2:
        raise DwgAuditError("camera_contract requires model coordinate contract v2")
    vectors = {
        "view_direction": ([0.0, -1.0, 0.0], _point3(raw.get("view_direction"), "camera_contract.view_direction")),
        "camera_up": ([0.0, 0.0, -1.0], _point3(raw.get("camera_up"), "camera_contract.camera_up")),
        "screen_right": ([1.0, 0.0, 0.0], _point3(raw.get("screen_right"), "camera_contract.screen_right")),
    }
    for field, (expected, actual) in vectors.items():
        if max(abs(left - right) for left, right in zip(expected, actual)) > 1e-9:
            raise DwgAuditError(f"camera_contract.{field} has the wrong orientation")
    if raw.get("cad_axis_mapping") != {
        "cad_x": "+screen_right", "cad_y": "+screen_up",
    }:
        raise DwgAuditError("camera_contract.cad_axis_mapping is invalid")
    eye = _point3(raw.get("eye"), "camera_contract.eye")
    target = _point3(raw.get("target"), "camera_contract.target")
    if eye[1] <= target[1] or abs(eye[0] - target[0]) > 1e-9 or abs(eye[2] - target[2]) > 1e-9:
        raise DwgAuditError("camera_contract eye must be vertically above its target")
    frustum = raw.get("frustum") if isinstance(raw.get("frustum"), Mapping) else {}
    try:
        normalized_frustum = {field: float(frustum[field]) for field in (
            "left", "right", "top", "bottom", "near", "far")}
    except (KeyError, TypeError, ValueError) as exc:
        raise DwgAuditError("camera_contract.frustum is incomplete") from exc
    if not all(math.isfinite(number) for number in normalized_frustum.values()):
        raise DwgAuditError("camera_contract.frustum contains NaN or infinity")
    if not (normalized_frustum["left"] < normalized_frustum["right"]
            and normalized_frustum["bottom"] < normalized_frustum["top"]
            and 0 < normalized_frustum["near"] < normalized_frustum["far"]):
        raise DwgAuditError("camera_contract.frustum is reflected or invalid")
    viewport = raw.get("viewport")
    if viewport != [1600, 1600]:
        raise DwgAuditError("camera_contract.viewport must be [1600, 1600]")
    padding = float(raw.get("padding_per_side", -1))
    if abs(padding - 0.05) > 1e-9:
        raise DwgAuditError("camera_contract.padding_per_side must be 0.05")
    return {
        "schema_version": 2,
        **exact,
        "webgl_capture": True,
        "model_coordinate_contract_version": 2,
        "view_direction": vectors["view_direction"][1],
        "camera_up": vectors["camera_up"][1],
        "screen_right": vectors["screen_right"][1],
        "cad_axis_mapping": {"cad_x": "+screen_right", "cad_y": "+screen_up"},
        "eye": eye, "target": target, "frustum": normalized_frustum,
        "viewport": [1600, 1600], "padding_per_side": padding,
    }


def _polygon(value: Any, field: str) -> list[list[float]]:
    if not isinstance(value, list) or len(value) < 3:
        raise DwgAuditError(f"{field} must contain at least three polygon vertices")
    points = [_point(point, f"{field}[{index}]") for index, point in enumerate(value)]
    unique = {(round(point[0], 9), round(point[1], 9)) for point in points}
    if len(unique) < 3:
        raise DwgAuditError(f"{field} must contain at least three distinct vertices")
    return points


def _normalize_geometry(value: Any, *, verified: bool) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    walls: list[dict[str, Any]] = []
    for index, item in enumerate(raw.get("walls") or []):
        if not isinstance(item, Mapping):
            raise DwgAuditError(f"geometry.walls[{index}] must be an object")
        wall = {
            "wall_id": _require_text(item.get("wall_id"), f"geometry.walls[{index}].wall_id"),
            "polygon_m": _polygon(item.get("polygon_m"), f"geometry.walls[{index}].polygon_m"),
            "source_handles": sorted({str(handle).strip() for handle in item.get("source_handles") or [] if str(handle).strip()}),
        }
        walls.append(wall)
    rooms: list[dict[str, Any]] = []
    for index, item in enumerate(raw.get("rooms") or []):
        if not isinstance(item, Mapping):
            raise DwgAuditError(f"geometry.rooms[{index}] must be an object")
        room = {
            "room_id": _require_text(item.get("room_id"), f"geometry.rooms[{index}].room_id"),
            "polygon_m": _polygon(item.get("polygon_m"), f"geometry.rooms[{index}].polygon_m"),
        }
        if str(item.get("label") or "").strip():
            room["label"] = str(item["label"]).strip()
        rooms.append(room)
    openings: list[dict[str, Any]] = []
    for index, item in enumerate(raw.get("openings") or []):
        if not isinstance(item, Mapping):
            raise DwgAuditError(f"geometry.openings[{index}] must be an object")
        opening_type = str(item.get("type") or "opening").strip().lower()
        if opening_type not in {"door", "window", "opening"}:
            raise DwgAuditError(f"geometry.openings[{index}].type is invalid")
        width_m = float(item.get("width_m") or 0.0)
        if not math.isfinite(width_m) or width_m <= 0:
            raise DwgAuditError(f"geometry.openings[{index}].width_m must be positive")
        opening = {
            "opening_id": _require_text(item.get("opening_id"), f"geometry.openings[{index}].opening_id"),
            "type": opening_type,
            "center_m": _point(item.get("center_m"), f"geometry.openings[{index}].center_m"),
            "width_m": width_m,
        }
        if str(item.get("wall_id") or "").strip():
            opening["wall_id"] = str(item["wall_id"]).strip()
        openings.append(opening)
    forbidden_handles = sorted({
        str(handle).strip() for handle in raw.get("forbidden_entity_handles") or []
        if str(handle).strip()
    })
    if verified and (not walls or not rooms):
        raise DwgAuditError("verified truth must contain at least one wall and one room")
    ids = [row["wall_id"] for row in walls]
    ids += [row["room_id"] for row in rooms]
    ids += [row["opening_id"] for row in openings]
    if len(ids) != len(set(ids)):
        raise DwgAuditError("geometry ids must be unique across walls, rooms, and openings")
    return {
        "walls": walls,
        "rooms": rooms,
        "openings": openings,
        "forbidden_entity_handles": forbidden_handles,
    }


def _normalize_truth_coordinate_contract(value: Any, *, verified: bool) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    if not verified and int(raw.get("schema_version") or 0) != 2:
        return _deepcopy_json(raw, "truth.coordinate_contract")
    expected = {
        "schema_version": 2,
        "coordinate_system": TRUTH_COORDINATE_SYSTEM_V2,
        "topdown_view": "sky-to-ground",
        "screen_mapping": {"cad_x": "+screen_right", "cad_y": "+screen_up"},
    }
    for field, expected_value in expected.items():
        if raw.get(field) != expected_value:
            raise DwgAuditError(
                f"verified truth coordinate_contract.{field} must be {expected_value}")
    return expected


@dataclass(frozen=True)
class DwgGeometryTruthV1:
    """Validated, canonical representation of independently authored DWG truth."""

    data: dict[str, Any]

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any], *, source_path: Path | str | None = None,
    ) -> "DwgGeometryTruthV1":
        raw = _deepcopy_json(dict(value), "truth")
        if raw.get("schema_name") != TRUTH_SCHEMA_NAME or int(raw.get("schema_version") or 0) != 1:
            raise DwgAuditError(f"truth schema must be {TRUTH_SCHEMA_NAME} version 1")
        forbidden = _find_forbidden_truth_key(raw)
        if forbidden:
            raise DwgAuditError(
                f"independent truth cannot contain production-derived field {forbidden}"
            )
        case_id = _require_case_id(raw.get("case_id"))
        level = str(raw.get("level") or "").strip().upper()
        if level not in LEVELS:
            raise DwgAuditError("truth.level must be one of L1-L5")
        status = str(raw.get("status") or "").strip().lower()
        if status not in TRUTH_STATUSES:
            raise DwgAuditError("truth.status must be pending or verified")
        source = raw.get("source") if isinstance(raw.get("source"), Mapping) else {}
        source_sha = _require_hash(source.get("sha256"), "truth.source.sha256")
        size_bytes = int(source.get("size_bytes") or 0)
        if size_bytes <= 0:
            raise DwgAuditError("truth.source.size_bytes must be positive")
        source_value = {
            "file_name": _require_text(source.get("file_name"), "truth.source.file_name"),
            "sha256": source_sha,
            "size_bytes": size_bytes,
        }
        if source.get("dwg_signature"):
            source_value["dwg_signature"] = str(source["dwg_signature"])
        if source.get("insunits") is not None:
            source_value["insunits"] = int(source["insunits"])
        independence = raw.get("independence") if isinstance(raw.get("independence"), Mapping) else {}
        if independence.get("production_parser_derived") is not False:
            raise DwgAuditError("truth.independence.production_parser_derived must be false")
        independence_value = {
            "method": _require_text(independence.get("method"), "truth.independence.method"),
            "production_parser_derived": False,
            "reviewer": str(independence.get("reviewer") or "").strip(),
            "reviewed_at": str(independence.get("reviewed_at") or "").strip(),
            "note": str(independence.get("note") or "").strip(),
        }
        if status == "verified" and (
            not independence_value["reviewer"] or not independence_value["reviewed_at"]
        ):
            raise DwgAuditError("verified truth requires reviewer and reviewed_at")
        geometry = _normalize_geometry(raw.get("geometry"), verified=status == "verified")
        reference = raw.get("reference_evidence") if isinstance(raw.get("reference_evidence"), Mapping) else {}
        reference_value: dict[str, Any] = {}
        if reference:
            reference_value = {
                "file_name": _require_text(reference.get("file_name"), "truth.reference_evidence.file_name"),
                "sha256": _require_hash(reference.get("sha256"), "truth.reference_evidence.sha256"),
                "size_bytes": int(reference.get("size_bytes") or 0),
                "role": str(reference.get("role") or "cad_preview"),
            }
            if reference_value["size_bytes"] <= 0:
                raise DwgAuditError("truth.reference_evidence.size_bytes must be positive")
        normalized = {
            "schema_name": TRUTH_SCHEMA_NAME,
            "schema_version": 1,
            "case_id": case_id,
            "title": str(raw.get("title") or case_id).strip() or case_id,
            "level": level,
            "status": status,
            "source": source_value,
            "coordinate_contract": _normalize_truth_coordinate_contract(
                raw.get("coordinate_contract"), verified=status == "verified"),
            "independence": independence_value,
            "geometry": geometry,
            "reference_evidence": reference_value,
        }
        truth_hash = canonical_hash(normalized)
        supplied_hash = str(raw.get("truth_hash") or "").strip().lower()
        if supplied_hash and supplied_hash != truth_hash:
            raise DwgAuditError("truth_hash does not match canonical truth content")
        normalized["truth_hash"] = truth_hash
        if source_path is not None:
            actual = Path(source_path).resolve()
            if not actual.is_file():
                raise DwgAuditError(f"truth source file does not exist: {actual}")
            if actual.stat().st_size != size_bytes or sha256_file(actual) != source_sha:
                raise DwgAuditError("truth source file does not match locked size/SHA-256")
        return cls(normalized)

    @classmethod
    def load(
        cls, path: Path | str, *, source_path: Path | str | None = None,
    ) -> "DwgGeometryTruthV1":
        return cls.from_mapping(_read_object(Path(path).resolve()), source_path=source_path)

    @property
    def truth_hash(self) -> str:
        return str(self.data["truth_hash"])

    def to_dict(self) -> dict[str, Any]:
        return _deepcopy_json(self.data, "truth")


def seed_pending_truths(
    dataset_manifest: Path | str, truth_dir: Path | str, *, overwrite: bool = False,
) -> list[dict[str, Any]]:
    """Create annotation manifests only; never copy production-parser claims into truth."""
    manifest_path = Path(dataset_manifest).resolve()
    manifest = _read_object(manifest_path)
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise DwgAuditError("dataset manifest.files must be a non-empty list")
    destination = Path(truth_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for row in files:
        if not isinstance(row, Mapping):
            raise DwgAuditError("dataset manifest file entries must be objects")
        case_id = _require_case_id(row.get("id"))
        source_path = manifest_path.parent / _require_text(row.get("filename"), "manifest filename")
        preview_path = manifest_path.parent / _require_text(row.get("preview"), "manifest preview")
        locked_sha = _require_hash(row.get("sha256"), f"manifest.files[{case_id}].sha256")
        if not source_path.is_file() or source_path.stat().st_size != int(row.get("bytes") or 0):
            raise DwgAuditError(f"dataset source file missing or size mismatch: {source_path}")
        if sha256_file(source_path) != locked_sha:
            raise DwgAuditError(f"dataset source SHA-256 mismatch: {source_path}")
        if not preview_path.is_file():
            raise DwgAuditError(f"dataset preview is missing: {preview_path}")
        raw_truth = {
            "schema_name": TRUTH_SCHEMA_NAME,
            "schema_version": 1,
            "case_id": case_id,
            "title": source_path.stem,
            "level": str(row.get("difficulty") or "").upper(),
            "status": "pending",
            "source": {
                "file_name": source_path.name,
                "sha256": locked_sha,
                "size_bytes": source_path.stat().st_size,
                "dwg_signature": row.get("dwg_signature"),
                "insunits": row.get("insunits"),
            },
            "coordinate_contract": {
                "schema_version": 2,
                "coordinate_system": TRUTH_COORDINATE_SYSTEM_V2,
                "topdown_view": "sky-to-ground",
                "screen_mapping": {"cad_x": "+screen_right", "cad_y": "+screen_up"},
                "source_insunits": row.get("insunits"),
            },
            "independence": {
                "method": "independent_manual_or_external_annotation",
                "production_parser_derived": False,
                "reviewer": "",
                "reviewed_at": "",
                "note": "Pending independent wall/room/opening annotation; no production parser claims imported.",
            },
            "geometry": {
                "walls": [], "rooms": [], "openings": [],
                "forbidden_entity_handles": [],
            },
            "reference_evidence": {
                "file_name": preview_path.name,
                "sha256": sha256_file(preview_path),
                "size_bytes": preview_path.stat().st_size,
                "role": "cad_preview",
            },
        }
        truth = DwgGeometryTruthV1.from_mapping(raw_truth, source_path=source_path)
        output_path = destination / f"{_safe_name(case_id)}.truth.json"
        encoded = json.dumps(truth.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        created = not output_path.exists()
        if output_path.exists() and not overwrite:
            if output_path.read_text(encoding="utf-8") != encoded:
                raise DwgAuditError(f"truth file exists with different content: {output_path}")
        else:
            output_path.write_text(encoded, encoding="utf-8")
        results.append({
            "case_id": case_id,
            "truth_path": str(output_path),
            "truth_hash": truth.truth_hash,
            "status": "pending",
            "created": created,
        })
    return results


def _artifact(logical_name: str, path: Path) -> dict[str, Any]:
    if not _SAFE_ID_RE.fullmatch(logical_name):
        raise DwgAuditError(f"invalid evidence artifact id: {logical_name}")
    resolved = path.resolve()
    if not resolved.is_file():
        raise DwgAuditError(f"evidence file does not exist: {resolved}")
    return {
        "artifact_id": logical_name,
        "label": _EVIDENCE_LABELS.get(logical_name, logical_name),
        "source_path": str(resolved),
        "file_name": resolved.name,
        "media_type": mimetypes.guess_type(resolved.name)[0] or "application/octet-stream",
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
        "evidence_kind": (
            "software_projection"
            if logical_name.startswith("software_") else
            "webgl_capture" if logical_name == "model_topdown" else
            "derived_comparison" if logical_name in {"overlay", "diff"} else
            "independent_truth" if logical_name == "structure_truth" else
            "source_reference" if logical_name == "cad_preview" else "supporting_evidence"
        ),
        "integrity_status": "passed",
        "integrity_problems": [],
    }


def _software_transform(value: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    if int(raw.get("schema_version") or 0) != 2:
        raise DwgAuditError("software projection requires model_to_truth coordinate contract v2")
    if raw.get("source_coordinate_system") != MODEL_COORDINATE_SYSTEM_V2:
        raise DwgAuditError("software projection source coordinate system must be model v2")
    if raw.get("target_coordinate_system") != TRUTH_COORDINATE_SYSTEM_V2:
        raise DwgAuditError("software projection target coordinate system must be CAD truth v2")
    scale = float(raw.get("scale", 1.0))
    rotation_deg = float(raw.get("rotation_deg", 0.0))
    translation = _point(raw.get("translation_m", [0.0, 0.0]), "model_to_truth.translation_m")
    if not math.isfinite(scale) or scale <= 0 or not math.isfinite(rotation_deg):
        raise DwgAuditError("model_to_truth scale/rotation must be finite and scale positive")
    result = {
        "scale": scale,
        "rotation_deg": rotation_deg,
        "translation_m": translation,
        "flip_y": bool(raw.get("flip_y", False)),
        "schema_version": 2,
        "source_coordinate_system": MODEL_COORDINATE_SYSTEM_V2,
        "target_coordinate_system": TRUTH_COORDINATE_SYSTEM_V2,
    }
    if result["flip_y"] is not True:
        raise DwgAuditError("software projection v2 must map model +Z to CAD -Y")
    return result


def software_model_to_truth_contract_v2(model_to_cad: Mapping[str, Any]) -> dict[str, Any]:
    """Build the only accepted diagnostic projection from a parser V2 inverse."""
    if int(model_to_cad.get("schema_version") or 0) != 2:
        raise DwgAuditError("model_to_cad schema_version must be 2")
    if abs(float(model_to_cad.get("x_scale", 1)) - 1.0) > 1e-9:
        raise DwgAuditError("software projection supports the canonical model X scale only")
    if abs(float(model_to_cad.get("z_scale", 0)) + 1.0) > 1e-9:
        raise DwgAuditError("model_to_cad must map model +Z to CAD -Y")
    return {
        "schema_version": 2,
        "source_coordinate_system": MODEL_COORDINATE_SYSTEM_V2,
        "target_coordinate_system": TRUTH_COORDINATE_SYSTEM_V2,
        "scale": 1.0, "rotation_deg": 0.0,
        "translation_m": [float(model_to_cad.get("x") or 0),
                          float(model_to_cad.get("z") or 0)],
        "flip_y": True,
    }


def _transform_model_point(point: Sequence[float], contract: Mapping[str, Any]) -> list[float]:
    x = float(point[0]) * float(contract["scale"])
    y = float(point[1]) * float(contract["scale"])
    if contract["flip_y"]:
        y = -y
    radians = math.radians(float(contract["rotation_deg"]))
    cos_value, sin_value = math.cos(radians), math.sin(radians)
    return [
        x * cos_value - y * sin_value + float(contract["translation_m"][0]),
        x * sin_value + y * cos_value + float(contract["translation_m"][1]),
    ]


def _save_png(image: Image.Image, path: Path) -> None:
    image.save(path, format="PNG", optimize=False, compress_level=9)


def render_software_orthographic_evidence(
    truth: DwgGeometryTruthV1 | Mapping[str, Any],
    geometry_manifest: Mapping[str, Any] | Path | str,
    output_dir: Path | str,
    *,
    model_to_truth: Mapping[str, Any] | None = None,
    canvas_size: int = 1600,
    padding_ratio: float = 0.05,
) -> dict[str, Any]:
    """Render deterministic X/Z wall masks without claiming a WebGL capture.

    This renderer is an independent diagnostic fallback.  Its artifact ids and
    report explicitly say ``software_projection``; a passed live audit still
    requires a separate ``model_topdown`` WebGL capture plus canonical
    ``overlay``/``diff`` derivatives.
    """
    truth_value = truth if isinstance(truth, DwgGeometryTruthV1) else DwgGeometryTruthV1.from_mapping(truth)
    truth_data = truth_value.to_dict()
    if truth_data["status"] != "verified":
        raise DwgAuditError("software projection requires verified geometry truth")
    if isinstance(geometry_manifest, (str, Path)):
        manifest = validate_geometry_manifest(_read_object(Path(geometry_manifest).resolve()))
    else:
        manifest = validate_geometry_manifest(geometry_manifest)
    size = int(canvas_size)
    padding = float(padding_ratio)
    if not 256 <= size <= 4096:
        raise DwgAuditError("software projection canvas_size must be between 256 and 4096")
    if not math.isfinite(padding) or not 0 <= padding <= 0.25:
        raise DwgAuditError("software projection padding_ratio must be between 0 and 0.25")
    transform = _software_transform(model_to_truth)

    truth_polygons = [row["polygon_m"] for row in truth_data["geometry"]["walls"]]
    vertices = manifest["vertices"]
    model_triangles: list[list[list[float]]] = []
    for part in manifest["wall_parts"]:
        indices = part["indices"]
        for offset in range(0, len(indices), 3):
            triangle = []
            for index in indices[offset:offset + 3]:
                vertex = vertices[index]
                triangle.append(_transform_model_point([vertex[0], vertex[2]], transform))
            model_triangles.append(triangle)
    if not model_triangles:
        raise DwgAuditError("geometry manifest contains no wall triangles to project")
    all_points = [point for polygon in truth_polygons for point in polygon]
    all_points += [point for triangle in model_triangles for point in triangle]
    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_y = max(point[1] for point in all_points)
    width_m, height_m = max_x - min_x, max_y - min_y
    if width_m <= 1e-9 or height_m <= 1e-9:
        raise DwgAuditError("software projection bounds are degenerate")
    usable = size * (1.0 - 2.0 * padding)
    pixels_per_m = min(usable / width_m, usable / height_m)
    drawn_width, drawn_height = width_m * pixels_per_m, height_m * pixels_per_m
    offset_x = (size - drawn_width) / 2.0
    offset_y = (size - drawn_height) / 2.0

    def pixel(point: Sequence[float]) -> tuple[float, float]:
        return (
            offset_x + (float(point[0]) - min_x) * pixels_per_m,
            size - (offset_y + (float(point[1]) - min_y) * pixels_per_m),
        )

    truth_mask = Image.new("L", (size, size), 0)
    model_mask = Image.new("L", (size, size), 0)
    truth_draw, model_draw = ImageDraw.Draw(truth_mask), ImageDraw.Draw(model_mask)
    for polygon in truth_polygons:
        truth_draw.polygon([pixel(point) for point in polygon], fill=255)
    for triangle in model_triangles:
        model_draw.polygon([pixel(point) for point in triangle], fill=255)

    intersection = ImageChops.multiply(truth_mask, model_mask)
    union = ImageChops.lighter(truth_mask, model_mask)
    truth_only = ImageChops.subtract(truth_mask, model_mask)
    model_only = ImageChops.subtract(model_mask, truth_mask)
    truth_pixels = truth_mask.histogram()[255]
    model_pixels = model_mask.histogram()[255]
    intersection_pixels = intersection.histogram()[255]
    union_pixels = union.histogram()[255]
    iou = intersection_pixels / union_pixels if union_pixels else 0.0

    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    paths = {
        "structure_truth": destination / "structure_truth.png",
        "software_model_topdown": destination / "software_model_topdown.png",
        "software_overlay": destination / "software_overlay.png",
        "software_diff": destination / "software_diff.png",
    }
    truth_image = Image.new("RGB", (size, size), "white")
    truth_image.paste((35, 35, 35), mask=truth_mask)
    model_image = Image.new("RGB", (size, size), "white")
    model_image.paste((22, 74, 128), mask=model_mask)
    overlay_image = Image.new("RGB", (size, size), "white")
    overlay_image.paste((215, 55, 55), mask=truth_only)
    overlay_image.paste((45, 115, 220), mask=model_only)
    overlay_image.paste((35, 145, 75), mask=intersection)
    diff_image = Image.new("RGB", (size, size), "white")
    diff_image.paste((220, 35, 35), mask=truth_only)
    diff_image.paste((35, 90, 230), mask=model_only)
    diff_image.paste((225, 225, 225), mask=intersection)
    for image, path in (
        (truth_image, paths["structure_truth"]),
        (model_image, paths["software_model_topdown"]),
        (overlay_image, paths["software_overlay"]),
        (diff_image, paths["software_diff"]),
    ):
        _save_png(image, path)

    contract = {
        "renderer": "software_projection",
        "renderer_version": "dwg-software-orthographic-v2",
        "webgl_capture": False,
        "projection": "orthographic_model_xz_to_cad_xy_sky_down_v2",
        "canvas_size": [size, size],
        "padding_ratio": padding,
        "model_to_truth": transform,
        "world_bounds_m": [min_x, min_y, max_x, max_y],
        "pixels_per_m": pixels_per_m,
        "truth_hash": truth_value.truth_hash,
        "geometry_manifest_hash": manifest["manifest_hash"],
    }
    report = {
        "schema_name": "DwgSoftwareOrthographicEvidenceV2",
        "schema_version": 2,
        "contract": contract,
        "contract_hash": canonical_hash(contract),
        "metrics": {
            "truth_pixels": truth_pixels,
            "model_pixels": model_pixels,
            "intersection_pixels": intersection_pixels,
            "union_pixels": union_pixels,
            "wall_footprint_pixel_iou": iou,
        },
        "artifacts": {
            artifact_id: {
                "file_name": path.name,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "evidence_kind": "software_projection" if artifact_id.startswith("software_") else "independent_truth",
            }
            for artifact_id, path in paths.items()
        },
        "limitations": [
            "This is a deterministic software X/Z projection, not a browser WebGL screenshot.",
            "Its software_* artifacts cannot satisfy the canonical model_topdown/overlay/diff roles of a passed live audit.",
        ],
    }
    report["report_hash"] = canonical_hash(report)
    report_path = destination / "software_projection_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    evidence_files = {artifact_id: str(path) for artifact_id, path in paths.items()}
    evidence_files["software_projection_report"] = str(report_path)
    return {
        "renderer": "software_projection",
        "webgl_capture": False,
        "contract_hash": report["contract_hash"],
        "report_hash": report["report_hash"],
        "metrics": report["metrics"],
        "evidence_files": evidence_files,
    }


def _normalize_metric(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise DwgAuditError(f"metrics[{index}] must be an object")
    metric_id = _require_text(item.get("metric_id"), f"metrics[{index}].metric_id")
    operator = str(item.get("operator") or "").strip()
    if operator not in {">=", "<="}:
        raise DwgAuditError(f"metrics[{index}].operator must be >= or <=")
    actual, threshold = float(item.get("actual")), float(item.get("threshold"))
    if not math.isfinite(actual) or not math.isfinite(threshold):
        raise DwgAuditError(f"metrics[{index}] contains NaN or infinity")
    passed = actual >= threshold if operator == ">=" else actual <= threshold
    return {
        "metric_id": metric_id,
        "field": str(item.get("field") or metric_id),
        "label": str(item.get("label") or metric_id),
        "actual": actual,
        "actual_display": f"{actual:.8g}",
        "operator": operator,
        "threshold": threshold,
        "threshold_display": f"{threshold:.8g}",
        "unit": str(item.get("unit") or ""),
        "status": "passed" if passed else "failed",
    }


def _normalize_visual_check(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise DwgAuditError(f"visual_checks[{index}] must be an object")
    check_id = _require_text(item.get("check_id"), f"visual_checks[{index}].check_id")
    if check_id not in VISUAL_CHECK_IDS:
        raise DwgAuditError(f"unknown visual check: {check_id}")
    status = str(item.get("status") or "").strip().lower()
    if status not in {"passed", "failed", "pending"}:
        raise DwgAuditError(f"visual_checks[{index}].status is invalid")
    return {
        "check_id": check_id,
        "label": str(item.get("label") or _VISUAL_LABELS[check_id]),
        "status": status,
        "note": str(item.get("note") or "").strip(),
    }


@dataclass(frozen=True)
class DwgLiveGeometryAuditV1:
    """Checksum-bound V2 live-run audit (legacy class alias kept for callers)."""

    data: dict[str, Any]

    @classmethod
    def build(
        cls,
        truth: DwgGeometryTruthV1 | Mapping[str, Any],
        run: Mapping[str, Any],
        evidence_files: Mapping[str, Path | str],
    ) -> "DwgLiveGeometryAuditV1":
        truth_value = truth if isinstance(truth, DwgGeometryTruthV1) else DwgGeometryTruthV1.from_mapping(truth)
        truth_data = truth_value.to_dict()
        raw = _deepcopy_json(dict(run), "live audit")
        requested_schema = raw.get("schema_name")
        if requested_schema not in {None, LIVE_AUDIT_SCHEMA_NAME, LEGACY_LIVE_AUDIT_SCHEMA_NAME}:
            raise DwgAuditError(f"live audit schema must be {LIVE_AUDIT_SCHEMA_NAME}")
        case_id = _require_case_id(raw.get("case_id"))
        if case_id != truth_data["case_id"]:
            raise DwgAuditError("live audit case_id differs from truth")
        level = str(raw.get("level") or truth_data["level"]).strip().upper()
        if level != truth_data["level"]:
            raise DwgAuditError("live audit level differs from truth")
        status = str(raw.get("status") or "").strip().lower()
        if status not in LIVE_STATUSES:
            raise DwgAuditError("live audit status must be pending, passed, or failed")
        source_sha = _require_hash(raw.get("source_sha256"), "live audit source_sha256")
        model_facts_hash = str(raw.get("model_facts_hash") or "").strip().lower()
        manifest_hash = str(raw.get("geometry_manifest_hash") or "").strip().lower()
        raw_camera_contract = raw.get("camera_contract")
        camera_contract: dict[str, Any] = {}
        if isinstance(raw_camera_contract, Mapping):
            camera_contract = validate_orthographic_camera_contract_v2(raw_camera_contract)
        camera_hash = canonical_hash(camera_contract) if camera_contract else ""
        supplied_camera_hash = str(raw.get("camera_contract_hash") or "").strip().lower()
        if supplied_camera_hash:
            _require_hash(supplied_camera_hash, "live audit camera_contract_hash")
            if supplied_camera_hash != camera_hash:
                raise DwgAuditError("camera_contract_hash does not match canonical camera_contract v2")
        for field, value in (
            ("model_facts_hash", model_facts_hash),
            ("geometry_manifest_hash", manifest_hash),
        ):
            if value:
                _require_hash(value, f"live audit {field}")
        metrics = [_normalize_metric(item, index) for index, item in enumerate(raw.get("metrics") or [])]
        if len({row["metric_id"] for row in metrics}) != len(metrics):
            raise DwgAuditError("metric_id values must be unique")
        checks = [_normalize_visual_check(item, index) for index, item in enumerate(raw.get("visual_checks") or [])]
        if len({row["check_id"] for row in checks}) != len(checks):
            raise DwgAuditError("visual check ids must be unique")
        artifacts = [_artifact(str(artifact_id), Path(path)) for artifact_id, path in sorted(evidence_files.items())]
        artifact_ids = {row["artifact_id"] for row in artifacts}
        issues = _deepcopy_json(raw.get("issues") or [], "live audit issues")
        if not isinstance(issues, list):
            raise DwgAuditError("live audit issues must be a list")
        source_matches = source_sha == truth_data["source"]["sha256"]
        if not source_matches:
            issues.append({
                "code": "dwg_truth_source_hash_mismatch",
                "severity": "hard",
                "expected_sha256": truth_data["source"]["sha256"],
                "actual_sha256": source_sha,
            })
        if status == "passed":
            if requested_schema == LEGACY_LIVE_AUDIT_SCHEMA_NAME:
                raise DwgAuditError("DwgLiveGeometryAuditV1 records cannot pass after camera contract v2")
            if truth_data["status"] != "verified":
                raise DwgAuditError("a passed live audit requires verified independent truth")
            if not source_matches:
                raise DwgAuditError("a passed live audit source must match truth SHA-256")
            missing_artifacts = sorted(set(EVIDENCE_ROLES) - artifact_ids)
            if missing_artifacts:
                raise DwgAuditError(f"passed live audit is missing evidence: {', '.join(missing_artifacts)}")
            if not metrics or any(row["status"] != "passed" for row in metrics):
                raise DwgAuditError("all deterministic metrics must pass")
            by_check = {row["check_id"]: row for row in checks}
            missing_checks = [check_id for check_id in VISUAL_CHECK_IDS if check_id not in by_check]
            if missing_checks:
                raise DwgAuditError(f"passed live audit is missing visual checks: {', '.join(missing_checks)}")
            if any(by_check[check_id]["status"] != "passed" for check_id in VISUAL_CHECK_IDS):
                raise DwgAuditError("all multimodal visual checks must pass")
            if any(str(issue.get("severity") or "").lower() in {"hard", "error"}
                   for issue in issues if isinstance(issue, Mapping)):
                raise DwgAuditError("passed live audit cannot contain hard/error issues")
            if not camera_contract:
                raise DwgAuditError("passed live audit requires camera_contract v2")
            for field, value in (
                ("model_facts_hash", model_facts_hash),
                ("geometry_manifest_hash", manifest_hash),
                ("camera_contract_hash", camera_hash),
            ):
                if not value:
                    raise DwgAuditError(f"passed live audit requires {field}")

        visual_metric_rows = [{
            "metric_id": f"visual.{row['check_id']}",
            "field": row["check_id"],
            "label": row["label"],
            "actual": 1.0 if row["status"] == "passed" else 0.0,
            "actual_display": "通过" if row["status"] == "passed" else ("待核对" if row["status"] == "pending" else "失败"),
            "operator": ">=", "threshold": 1.0, "threshold_display": "通过",
            "unit": "boolean", "status": row["status"],
        } for row in checks]
        normalized = {
            "schema_name": LIVE_AUDIT_SCHEMA_NAME,
            "schema_version": 2,
            "publisher_version": DWG_AUDIT_PUBLISHER_VERSION,
            "audit_kind": "dwg_live_geometry",
            "case_id": case_id,
            "title": str(raw.get("title") or truth_data["title"]),
            "level": level,
            "status": status,
            "executed_at": str(raw.get("executed_at") or time.strftime("%Y-%m-%d %H:%M:%S")),
            "source": {
                "file_name": truth_data["source"]["file_name"],
                "source_sha256": source_sha,
                "truth_source_sha256": truth_data["source"]["sha256"],
                "matches_truth": source_matches,
            },
            "truth": {
                "schema_name": TRUTH_SCHEMA_NAME,
                "truth_hash": truth_value.truth_hash,
                "status": truth_data["status"],
                "independence": truth_data["independence"],
                "counts": {
                    "walls": len(truth_data["geometry"]["walls"]),
                    "rooms": len(truth_data["geometry"]["rooms"]),
                    "openings": len(truth_data["geometry"]["openings"]),
                    "forbidden_entities": len(truth_data["geometry"]["forbidden_entity_handles"]),
                },
            },
            "run": {
                "project_id": str(raw.get("project_id") or ""),
                "revision": str(raw.get("revision") or ""),
                "model_facts_hash": model_facts_hash,
                "geometry_manifest_hash": manifest_hash,
                "camera_contract_hash": camera_hash,
                "camera_contract": camera_contract,
            },
            "channels": [
                {"channel_id": "geometry", "label": "DWG / 3D 定量对照", "status": status, "metrics": metrics},
                {"channel_id": "multimodal", "label": "俯视图多模态核对", "status": status, "metrics": visual_metric_rows},
            ],
            "visual_checks": checks,
            "artifacts": artifacts,
            "integrity": {"status": "passed", "checked_count": len(artifacts), "failures": []},
            "issues": issues,
            "review": {"checked_metric_ids": [], "reviewer": "", "note": "", "reviewed_at": ""},
        }
        hash_payload = _deepcopy_json(normalized, "live audit hash payload")
        hash_payload.pop("executed_at", None)
        hash_payload.pop("review", None)
        for artifact in hash_payload["artifacts"]:
            artifact.pop("source_path", None)
            artifact.pop("file_name", None)
        normalized["audit_hash"] = canonical_hash(hash_payload)
        return cls(normalized)

    @property
    def audit_hash(self) -> str:
        return str(self.data["audit_hash"])

    def to_dict(self) -> dict[str, Any]:
        return _deepcopy_json(self.data, "live audit")


def _copy_artifacts(payload: dict[str, Any], *, output_root: Path, record_id: str) -> None:
    destination = output_root / "geometry_audits" / "dwg" / _safe_name(payload["case_id"]) / record_id
    destination.mkdir(parents=True, exist_ok=True)
    for artifact in payload["artifacts"]:
        source = Path(artifact["source_path"])
        suffix = source.suffix.lower()
        target = destination / f"{_safe_name(artifact['artifact_id'])}{suffix}"
        if not target.is_file() or sha256_file(target) != artifact["sha256"]:
            shutil.copy2(source, target)
        artifact["relative_path"] = target.relative_to(output_root).as_posix()
        artifact["available"] = True


def archive_dwg_live_geometry_audit(
    truth: DwgGeometryTruthV1 | Mapping[str, Any] | Path | str,
    run: Mapping[str, Any],
    evidence_files: Mapping[str, Path | str],
    *,
    output_root: Path | str = MAIN_OUTPUT_DIR,
    source_path: Path | str | None = None,
) -> dict[str, Any]:
    """Archive passed, failed, or pending live runs in the existing records UI."""
    if isinstance(truth, (str, Path)):
        truth_value = DwgGeometryTruthV1.load(truth, source_path=source_path)
    elif isinstance(truth, DwgGeometryTruthV1):
        truth_value = truth
    else:
        truth_value = DwgGeometryTruthV1.from_mapping(truth, source_path=source_path)
    audit = DwgLiveGeometryAuditV1.build(truth_value, run, evidence_files)
    payload = audit.to_dict()
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    record_id = f"dwg_geometry_audit_{_safe_name(payload['case_id'])}_{payload['audit_hash'][:16]}"
    _copy_artifacts(payload, output_root=root, record_id=record_id)

    artifact_by_id = {row["artifact_id"]: row for row in payload["artifacts"]}
    ordered_ids = [artifact_id for artifact_id in EVIDENCE_ROLES if artifact_id in artifact_by_id]
    ordered_ids += sorted(set(artifact_by_id) - set(EVIDENCE_ROLES))
    results: list[dict[str, Any]] = []
    for artifact_id in ordered_ids:
        artifact = artifact_by_id[artifact_id]
        if not str(artifact.get("media_type") or "").startswith("image/"):
            continue
        results.append({
            "result_id": f"{record_id}_{_safe_name(artifact_id)}",
            "result_image_file": artifact["relative_path"],
            "model": "dwg-geometry-audit",
            "model_label": artifact["label"],
            "comment": f"SHA-256 {artifact['sha256']}",
            "favorite": False,
            "best": False,
            "review_status": "pass" if payload["status"] == "passed" else "unreviewed",
            "review_tags": [payload["level"], "DWG真机审计", artifact["label"]],
            "review_note": "只读校验和证据",
            "result_timestamp": payload["executed_at"],
        })
    record = {
        "id": record_id,
        "timestamp": payload["executed_at"],
        "room_type": f"DWG {payload['level']}",
        "workflow_mode": "Plan-to-3D DWG真机审计",
        "immutable_audit": True,
        "geometry_audit": payload,
        "results": results,
    }
    directory = root / "geometry_audits"
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"Plan-to-3D_DWG_{_safe_name(payload['case_id'])}_记录.json"
    with record_file_lock(str(json_path)):
        records = load_records_file(str(json_path)) if json_path.exists() else []
        if not isinstance(records, list):
            records = []
        existing = next((row for row in records if isinstance(row, dict) and row.get("id") == record_id), None)
        if existing is None:
            records.insert(0, record)
        else:
            previous_audit = existing.get("geometry_audit") if isinstance(existing, Mapping) else None
            previous_review = previous_audit.get("review") if isinstance(previous_audit, Mapping) else None
            if isinstance(previous_review, Mapping):
                record["geometry_audit"]["review"] = dict(previous_review)
            records[records.index(existing)] = record
        save_records_file(str(json_path), records)
    return {
        "record_id": record_id,
        "json_path": str(json_path),
        "case_id": payload["case_id"],
        "status": payload["status"],
        "truth_status": payload["truth"]["status"],
        "truth_hash": payload["truth"]["truth_hash"],
        "audit_hash": payload["audit_hash"],
        "artifact_count": len(payload["artifacts"]),
        "preview_count": len(results),
        "created": existing is None,
    }


def _parse_evidence(values: Sequence[str], *, base: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        artifact_id, separator, raw_path = value.partition("=")
        if not separator or not artifact_id.strip() or not raw_path.strip():
            raise DwgAuditError("--evidence must use artifact_id=path")
        path = Path(raw_path.strip())
        result[artifact_id.strip()] = path if path.is_absolute() else (base / path)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    seed = subparsers.add_parser("seed-truths", help="create explicit pending truth manifests")
    seed.add_argument("dataset_manifest", type=Path)
    seed.add_argument("truth_dir", type=Path)
    seed.add_argument("--overwrite", action="store_true")
    validate = subparsers.add_parser("validate-truth", help="validate one independent truth file")
    validate.add_argument("truth_json", type=Path)
    validate.add_argument("--source", type=Path)
    archive = subparsers.add_parser("archive", help="archive one passed/failed live audit")
    archive.add_argument("truth_json", type=Path)
    archive.add_argument("run_json", type=Path)
    archive.add_argument("--source", type=Path)
    archive.add_argument("--evidence", action="append", default=[], metavar="ID=PATH")
    archive.add_argument("--output-root", type=Path, default=Path(MAIN_OUTPUT_DIR))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "seed-truths":
            result: Any = seed_pending_truths(
                arguments.dataset_manifest, arguments.truth_dir, overwrite=arguments.overwrite,
            )
        elif arguments.command == "validate-truth":
            truth = DwgGeometryTruthV1.load(arguments.truth_json, source_path=arguments.source)
            result = {
                "ok": True, "case_id": truth.data["case_id"],
                "status": truth.data["status"], "truth_hash": truth.truth_hash,
            }
        else:
            run_path = arguments.run_json.resolve()
            run = _read_object(run_path)
            evidence = _parse_evidence(arguments.evidence, base=run_path.parent)
            result = archive_dwg_live_geometry_audit(
                arguments.truth_json, run, evidence,
                output_root=arguments.output_root, source_path=arguments.source,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        # ``archive`` records an observed run whose audit outcome may itself
        # be ``failed``.  That is still a successful archival operation.  A
        # non-zero process status is reserved for malformed input, missing
        # evidence, checksum failures, or an I/O error handled below.
        return 0
    except (DwgAuditError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "TRUTH_SCHEMA_NAME",
    "LIVE_AUDIT_SCHEMA_NAME",
    "DWG_AUDIT_PUBLISHER_VERSION",
    "EVIDENCE_ROLES",
    "VISUAL_CHECK_IDS",
    "DwgAuditError",
    "DwgGeometryTruthV1",
    "DwgLiveGeometryAuditV1",
    "seed_pending_truths",
    "render_software_orthographic_evidence",
    "archive_dwg_live_geometry_audit",
    "main",
]
