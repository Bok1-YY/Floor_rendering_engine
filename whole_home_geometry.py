# -*- coding: utf-8 -*-
"""Pure contracts for locking a 2D plan to the whole-home 3D shell.

This module intentionally has no FastAPI, filesystem, renderer, CAD parser or
AI dependencies.  Routes may use it as the single policy layer while tests and
offline dataset tooling can exercise exactly the same rules.

The public constructors return ordinary ``dict`` subclasses so existing whole
home project/model dictionaries can adopt the contracts incrementally.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from typing import Any


GEOMETRY_CONTRACT_VERSION = 1
GEOMETRY_MODEL_VERSION = 3
SOURCE_REGISTRATION_VERSION = 2
REGISTRATION_HASH_FIELDS = frozenset({
    "registration_hash", "created_at",
    # Artifact locations are deployment/storage details.  Their content is
    # already bound by source/normalized/canonical SHA-256 fields and moving a
    # project directory must not stale an otherwise identical registration.
    "original_artifact_path", "canonical_artifact_path",
})
MANIFEST_HASH_FIELDS = frozenset({"manifest_hash"})
REPORT_HASH_FIELDS = frozenset({"report_hash"})

INPUT_GRADES = frozenset({
    "vector_authoritative", "raster_draft", "raster_human_locked",
    "legacy_unproven",
})
WALL_REPRESENTATIONS = frozenset({
    "centerline", "paired_faces", "closed_footprint",
    "human_confirmed_ambiguous", "redundant_evidence", "junction_evidence",
    "global_topology_evidence", "global_topology_connector_evidence",
    "global_topology_boundary_evidence", "global_topology_micro_evidence",
    "global_topology_piecewise_evidence", "opening_host_stitch",
    "projected_detail_evidence", "projected_topology_boundary_evidence",
    "projected_geometry_dependency_evidence",
    "detached_site_boundary_evidence",
    "nonspace_projected_geometry_evidence",
    "global_topology_opening_host",
    "frame_geometry_opening_host",
    "door_swing_geometry_opening_host",
    "repeated_window_frame_opening_host",
    "window_frame_host_extension",
    "terminal_open_connection_host",
    "collinear_face_continuation",
    "opening_evidence",
})
WALL_REVIEW_STATUSES = frozenset({"pending", "confirmed", "rejected"})
REPORT_STATUSES = frozenset({"passed", "needs_human_review", "blocked", "stale"})
ISSUE_SEVERITIES = frozenset({"fatal", "hard", "review", "warning"})
BLOCKING_SEVERITIES = frozenset({"fatal", "hard", "review"})


class GeometryContractError(ValueError):
    """A stable, machine-readable contract violation."""

    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **copy.deepcopy(self.details)}


def _contract_error(code: str, message: str, **details: Any) -> None:
    raise GeometryContractError(code, message, **details)


def canonicalize(value: Any, *, float_digits: int = 9) -> Any:
    """Return a JSON-safe, deterministic copy.

    Dict key order never affects the encoded form, tuples become arrays,
    dataclasses/Pydantic models are supported, finite floats are rounded and
    negative zero is removed.  Set ordering is canonicalized by encoded value.
    """
    if hasattr(value, "model_dump") and callable(value.model_dump):
        value = value.model_dump(mode="python")
    elif is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, (str, int, float, bool)) and key is not None:
                _contract_error("canonical_key_invalid", "canonical JSON keys must be scalar", key=repr(key))
            normalized_key = str(key)
            if normalized_key in normalized:
                _contract_error(
                    "canonical_key_collision", "distinct mapping keys collapse to the same JSON key",
                    key=normalized_key,
                )
            normalized[normalized_key] = canonicalize(item, float_digits=float_digits)
        return normalized
    if isinstance(value, (set, frozenset)):
        rows = [canonicalize(item, float_digits=float_digits) for item in value]
        return sorted(rows, key=lambda item: canonical_json(item, float_digits=float_digits))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [canonicalize(item, float_digits=float_digits) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, (float, Decimal)):
        number = float(value)
        if not math.isfinite(number):
            _contract_error("canonical_number_invalid", "NaN and infinity are not valid geometry facts")
        number = round(number, float_digits)
        return 0.0 if number == 0 else number
    _contract_error("canonical_type_invalid", "value cannot be encoded as canonical JSON", type=type(value).__name__)


def canonical_json(value: Any, *, float_digits: int = 9) -> str:
    """Encode a value using the project's geometry canonical JSON format."""
    return json.dumps(
        canonicalize(value, float_digits=float_digits), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    )


def canonical_hash(
    value: Any, *, exclude_fields: Sequence[str] = (), float_digits: int = 9,
) -> str:
    """SHA-256 of canonical JSON, optionally excluding top-level fields."""
    payload = copy.deepcopy(dict(value)) if isinstance(value, Mapping) else value
    if isinstance(payload, dict):
        for field in exclude_fields:
            payload.pop(field, None)
    return hashlib.sha256(canonical_json(payload, float_digits=float_digits).encode("utf-8")).hexdigest()


def normalize_review_status(value: Any, *, legacy_missing: bool = False) -> str:
    """Bridge persisted ``accepted`` to the v1 contract's ``confirmed`` state.

    Old projects predate entity review state.  Callers may opt into the
    ``legacy_missing`` bridge only while rendering those unenrolled projects;
    new geometry contracts always treat a missing state as pending.
    """
    status = str(value or "").strip().lower()
    if status in {"accepted", "confirmed"}:
        return "confirmed"
    if status in {"rejected", "reject"}:
        return "rejected"
    if not status and legacy_missing:
        return "confirmed"
    return "pending"


def geometry_entity_confirmed(value: Mapping[str, Any], *, legacy_missing: bool = False) -> bool:
    """Return whether an entity is allowed into locked geometry."""
    return normalize_review_status(value.get("review_status"), legacy_missing=legacy_missing) == "confirmed"


def _fact_point(value: Any) -> list[float]:
    if isinstance(value, Mapping):
        return [round(_number(value.get("x"), "point.x"), 8),
                round(_number(value.get("z"), "point.z"), 8)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 2:
        return [round(_number(value[0], "point.x"), 8),
                round(_number(value[1], "point.z"), 8)]
    _contract_error("geometry_point_invalid", "geometry fact point must contain x and z")


def _canonical_path(value: Any, *, closed: bool) -> list[list[float]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    points = [_fact_point(point) for point in value]
    compact: list[list[float]] = []
    for point in points:
        if not compact or point != compact[-1]:
            compact.append(point)
    if closed and len(compact) > 1 and compact[0] == compact[-1]:
        compact.pop()
    if len(compact) < (3 if closed else 2):
        return compact
    if closed:
        variants: list[list[list[float]]] = []
        for candidate in (compact, list(reversed(compact))):
            for index in range(len(candidate)):
                variants.append(candidate[index:] + candidate[:index])
        return min(variants, key=canonical_json)
    reverse = list(reversed(compact))
    return min((compact, reverse), key=canonical_json)


def _raw_centerline(row: Mapping[str, Any]) -> list[list[float]]:
    centerline = row.get("centerline") or row.get("opening_axis") or []
    if isinstance(centerline, Mapping):
        centerline = [centerline.get("start"), centerline.get("end")]
    if isinstance(centerline, Sequence) and not isinstance(centerline, (str, bytes)) and len(centerline) >= 2:
        return [_fact_point(point) for point in centerline]
    start, end = row.get("start"), row.get("end")
    return [_fact_point(start), _fact_point(end)] if start is not None and end is not None else []


def _path_point_at(path: Sequence[Sequence[float]], distance: float) -> list[float] | None:
    remaining = max(0.0, distance)
    for first, second in zip(path, path[1:]):
        length = math.dist(first, second)
        if length <= 1e-12:
            continue
        if remaining <= length:
            ratio = remaining / length
            return [round(first[0] + (second[0] - first[0]) * ratio, 8),
                    round(first[1] + (second[1] - first[1]) * ratio, 8)]
        remaining -= length
    return [round(float(axis), 8) for axis in path[-1]] if path else None


def geometry_facts_payload(model: Mapping[str, Any]) -> dict[str, Any]:
    """Return only normalized wall/room/opening geometry and its topology.

    Project ids, revisions, timestamps, labels, review actors, reports, cameras,
    render state and fixed-object decoration are deliberately absent.  Entity
    ids are converted to geometry-sorted tokens so harmless id regeneration or
    list ordering cannot invalidate an otherwise identical locked shell.
    """
    if not isinstance(model, Mapping):
        _contract_error("geometry_model_invalid", "geometry facts require a model object")
    grade = str(model.get("input_grade") or "")
    strict = grade != "legacy_unproven" and bool(
        int(model.get("geometry_schema_version", 0) or 0) >= GEOMETRY_MODEL_VERSION
        or grade in {"vector_authoritative", "raster_human_locked"}
    )
    assemblies = [row for row in model.get("wall_assemblies") or [] if isinstance(row, Mapping)]
    assembly_ids = {str(row.get("id") or "") for row in assemblies}
    wall_sources: list[tuple[Mapping[str, Any], bool]] = [
        (row, False) for row in assemblies if geometry_entity_confirmed(row)
    ]
    wall_sources.extend(
        (row, not strict) for row in model.get("walls") or [] if isinstance(row, Mapping)
        and str(row.get("wall_assembly_id") or "") not in assembly_ids
        and geometry_entity_confirmed(row, legacy_missing=not strict)
    )

    wall_entries: list[tuple[str, dict[str, Any], Mapping[str, Any]]] = []
    for row, _ in wall_sources:
        path = _raw_centerline(row)
        footprint = _canonical_path(row.get("footprint_polygon") or [], closed=True)
        # An arbitrary closed footprint already contains the full wall volume
        # in plan and may legitimately have no single scalar thickness.  CAD
        # stores that explicit fact as ``thickness_m: null``.  Preserve it in
        # the stable payload instead of treating it as malformed; centerline
        # walls still require a finite measured/default thickness.
        raw_thickness = row.get("thickness_m")
        thickness = (None if raw_thickness is None and footprint else round(
            _number(0.12 if raw_thickness is None else raw_thickness,
                    "wall.thickness_m"), 8))
        fact = {
            "footprint": footprint,
            "centerline": _canonical_path(path, closed=False),
            "thickness_m": thickness,
            "height_m": round(_number(row.get("height_m", model.get("wall_height_m", 2.8)), "wall.height_m"), 8),
            "floor_elevation_m": round(_number(row.get("floor_elevation_m", 0), "wall.floor_elevation_m"), 8),
        }
        geometry_key = canonical_json(fact)
        wall_entries.append((geometry_key, fact, row))
    wall_entries.sort(key=lambda item: (item[0], str(item[2].get("id") or "")))
    wall_tokens: dict[str, str] = {}
    walls: list[dict[str, Any]] = []
    raw_wall_paths: dict[str, list[list[float]]] = {}
    for index, (_, fact, row) in enumerate(wall_entries):
        token = f"w{index:05d}"
        walls.append({"token": token, **fact})
        for raw_id in (row.get("id"), row.get("wall_assembly_id")):
            if raw_id:
                wall_tokens[str(raw_id)] = token
                raw_wall_paths[str(raw_id)] = _raw_centerline(row)

    room_rows = [row for row in (model.get("rooms") or model.get("physical_spaces") or [])
                 if isinstance(row, Mapping)]
    room_entries: list[tuple[str, dict[str, Any], Mapping[str, Any]]] = []
    for row in room_rows:
        fact = {
            "polygon": _canonical_path(row.get("polygon") or [], closed=True),
            "floor_elevation_m": round(_number(row.get("floor_elevation_m", 0), "room.floor_elevation_m"), 8),
            "ceiling_height_m": round(_number(
                row.get("ceiling_height_m", model.get("wall_height_m", 2.8)), "room.ceiling_height_m"), 8),
        }
        room_entries.append((canonical_json(fact), fact, row))
    room_entries.sort(key=lambda item: (item[0], str(item[2].get("id") or "")))
    room_tokens: dict[str, str] = {}
    rooms: list[dict[str, Any]] = []
    for index, (_, fact, row) in enumerate(room_entries):
        token = f"r{index:05d}"
        rooms.append({"token": token, **fact})
        if row.get("id"):
            room_tokens[str(row.get("id"))] = token

    legacy_opening_missing = not strict and not assemblies
    openings: list[dict[str, Any]] = []
    for row in model.get("openings") or []:
        if not isinstance(row, Mapping) or not geometry_entity_confirmed(
                row, legacy_missing=legacy_opening_missing):
            continue
        target_id = str(row.get("wall_assembly_id") or row.get("wall_id") or "")
        offset = _number(row.get("offset_m", row.get("start_offset_m", 0)), "opening.offset_m")
        width = _number(row.get("width_m"), "opening.width_m")
        path = raw_wall_paths.get(target_id) or []
        interval = [_path_point_at(path, offset), _path_point_at(path, offset + width)] if path else []
        normalized_interval = _canonical_path(
            [point for point in interval if point is not None], closed=False) if interval else []
        room_ids = row.get("room_ids") or row.get("adjacent_room_ids") or []
        if row.get("room_id"):
            room_ids = [*room_ids, row.get("room_id")]
        openings.append({
            "wall": wall_tokens.get(target_id, "orphan"),
            "kind": str(row.get("kind") or "opening"),
            "interval": normalized_interval,
            **({} if normalized_interval else {"offset_m": round(offset, 8)}),
            "width_m": round(width, 8),
            "height_m": round(_number(row.get("height_m", 2.1), "opening.height_m"), 8),
            "sill_height_m": round(_number(row.get("sill_height_m", 0), "opening.sill_height_m"), 8),
            "rooms": sorted({room_tokens.get(str(room_id), "unbound") for room_id in room_ids if room_id}),
        })
    openings.sort(key=canonical_json)

    topology = []
    for row in wall_sources:
        source = row[0]
        wall_token = wall_tokens.get(str(source.get("id") or ""))
        room_ids = source.get("room_ids") or source.get("adjacent_room_ids") or []
        if source.get("room_id"):
            room_ids = [*room_ids, source.get("room_id")]
        linked = sorted({room_tokens.get(str(room_id), "unbound") for room_id in room_ids if room_id})
        if wall_token and linked:
            topology.append({"wall": wall_token, "rooms": linked})
    topology.sort(key=canonical_json)
    global_wall_shells = []
    for row in model.get("global_wall_footprints") or []:
        if not isinstance(row, Mapping):
            continue
        exterior = _canonical_path(row.get("points") or [], closed=True)
        if len(exterior) < 4:
            continue
        holes = sorted([
            _canonical_path(ring, closed=True)
            for ring in row.get("interior_rings") or []
            if isinstance(ring, Sequence) and not isinstance(ring, (str, bytes))
        ], key=canonical_json)
        global_wall_shells.append({
            "footprint": exterior,
            "interior_rings": holes,
            "floor_elevation_m": round(_number(
                row.get("floor_elevation_m", 0),
                "global_wall_footprint.floor_elevation_m"), 8),
            "height_m": round(_number(
                row.get("height_m", model.get("wall_height_m", 2.8)),
                "global_wall_footprint.height_m"), 8),
        })
    global_wall_shells.sort(key=canonical_json)
    return canonicalize({
        "schema": "whole-home-plan-geometry-facts-v1",
        "coordinate_system": str(model.get("coordinate_system") or "metres-y-up"),
        "walls": walls, "global_wall_shells": global_wall_shells,
        "rooms": rooms, "openings": openings, "topology": topology,
    }, float_digits=8)


def geometry_facts_hash(model: Mapping[str, Any]) -> str:
    """Stable CAD/plan geometry fingerprint, independent of project metadata."""
    return canonical_hash(geometry_facts_payload(model), float_digits=8)


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        _contract_error("number_invalid", f"{field} must be a finite number", field=field)
    try:
        result = float(value)
    except (TypeError, ValueError):
        _contract_error("number_invalid", f"{field} must be a finite number", field=field)
    if not math.isfinite(result):
        _contract_error("number_invalid", f"{field} must be a finite number", field=field)
    return result


def _matrix3(value: Any, field: str) -> list[list[float]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        _contract_error("matrix_shape_invalid", f"{field} must be a 3x3 matrix", field=field)
    result: list[list[float]] = []
    for row in value:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) != 3:
            _contract_error("matrix_shape_invalid", f"{field} must be a 3x3 matrix", field=field)
        result.append([_number(item, field) for item in row])
    return result


def _matrix4(value: Any, field: str) -> list[list[float]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        _contract_error("matrix_shape_invalid", f"{field} must be a 4x4 matrix", field=field)
    result: list[list[float]] = []
    for row in value:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) != 4:
            _contract_error("matrix_shape_invalid", f"{field} must be a 4x4 matrix", field=field)
        result.append([_number(item, field) for item in row])
    return result


def _det3(matrix: list[list[float]]) -> float:
    a, b, c = matrix[0]
    d, e, f = matrix[1]
    g, h, i = matrix[2]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def invert_matrix3(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    """Invert a finite 3x3 matrix or raise ``matrix_singular``."""
    m = _matrix3(matrix, "matrix")
    determinant = _det3(m)
    if abs(determinant) <= 1e-12:
        _contract_error("matrix_singular", "registration matrix must be invertible")
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    inverse = [
        [(e * i - f * h), (c * h - b * i), (b * f - c * e)],
        [(f * g - d * i), (a * i - c * g), (c * d - a * f)],
        [(d * h - e * g), (b * g - a * h), (a * e - b * d)],
    ]
    return [[item / determinant for item in row] for row in inverse]


def invert_matrix4(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    """Invert a finite 4x4 matrix with deterministic Gauss-Jordan elimination."""
    rows = _matrix4(matrix, "matrix")
    augmented = [
        list(row) + [1.0 if index == column else 0.0 for column in range(4)]
        for index, row in enumerate(rows)
    ]
    for column in range(4):
        pivot = max(range(column, 4), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) <= 1e-12:
            _contract_error("matrix_singular", "registration matrix must be invertible")
        if pivot != column:
            augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(4):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                augmented[row][index] - factor * augmented[column][index]
                for index in range(8)
            ]
    return [row[4:] for row in augmented]


def _matmul3(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    return [[sum(left[row][k] * right[k][col] for k in range(3)) for col in range(3)] for row in range(3)]


def _identity_error(forward: list[list[float]], inverse: list[list[float]]) -> float:
    errors = []
    for product in (_matmul3(forward, inverse), _matmul3(inverse, forward)):
        for row in range(3):
            for column in range(3):
                errors.append(abs(product[row][column] - (1.0 if row == column else 0.0)))
    return max(errors, default=0.0)


def _identity_error4(forward: list[list[float]], inverse: list[list[float]]) -> float:
    def multiply(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
        return [[sum(left[row][k] * right[k][column] for k in range(4))
                 for column in range(4)] for row in range(4)]

    errors = []
    for product in (multiply(forward, inverse), multiply(inverse, forward)):
        for row in range(4):
            for column in range(4):
                errors.append(abs(product[row][column] - (1.0 if row == column else 0.0)))
    return max(errors, default=0.0)


def _validate_similarity4(matrix: list[list[float]], field: str) -> float:
    if max(abs(matrix[3][0]), abs(matrix[3][1]), abs(matrix[3][2]),
           abs(matrix[3][3] - 1.0)) > 1e-9:
        _contract_error("model_transform_not_affine", f"{field} must be an affine 3D transform", field=field)
    columns = [tuple(matrix[row][column] for row in range(3)) for column in range(3)]
    scales = [math.sqrt(sum(axis * axis for axis in column)) for column in columns]
    if min(scales) <= 1e-12:
        _contract_error("model_scale_invalid", f"{field} has zero scale", field=field)
    if (max(scales) - min(scales)) / max(scales) > 1e-6:
        _contract_error(
            "model_scale_non_uniform", f"{field} must use one uniform scale and rotation",
            field=field, scales=scales,
        )
    for first in range(3):
        for second in range(first + 1, 3):
            dot = sum(columns[first][axis] * columns[second][axis] for axis in range(3))
            if abs(dot) > 1e-6 * scales[first] * scales[second]:
                _contract_error(
                    "model_scale_non_uniform", f"{field} axes must remain orthogonal",
                    field=field,
                )
    determinant = _det3([[matrix[row][column] for column in range(3)] for row in range(3)])
    if determinant <= 0:
        _contract_error("model_transform_mirrored", f"{field} must preserve 3D handedness", field=field)
    return sum(scales) / 3.0


def _validate_similarity(matrix: list[list[float]], field: str) -> float:
    if max(abs(matrix[2][0]), abs(matrix[2][1]), abs(matrix[2][2] - 1.0)) > 1e-9:
        _contract_error("model_transform_not_affine", f"{field} must be an affine 2D transform", field=field)
    first = (matrix[0][0], matrix[1][0])
    second = (matrix[0][1], matrix[1][1])
    scale_x = math.hypot(*first)
    scale_y = math.hypot(*second)
    if min(scale_x, scale_y) <= 1e-12:
        _contract_error("model_scale_invalid", f"{field} has zero scale", field=field)
    relative_difference = abs(scale_x - scale_y) / max(scale_x, scale_y)
    dot = first[0] * second[0] + first[1] * second[1]
    if relative_difference > 1e-6 or abs(dot) > 1e-6 * scale_x * scale_y:
        _contract_error(
            "model_scale_non_uniform", f"{field} must use one uniform scale and rotation",
            field=field, scale_x=scale_x, scale_y=scale_y,
        )
    determinant = first[0] * second[1] - second[0] * first[1]
    if determinant <= 0:
        _contract_error("model_transform_mirrored", f"{field} must not mirror the source", field=field)
    return (scale_x + scale_y) / 2.0


def _projective_orientation(matrix: list[list[float]]) -> float:
    # Local Jacobian orientation at the origin.  Registration normalization
    # requires the image/cad origin to stay in the finite chart.
    denominator = matrix[2][2]
    if abs(denominator) <= 1e-12:
        _contract_error("registration_origin_infinite", "registration maps its origin to infinity")
    return _det3(matrix) / (denominator ** 3)


def _anchor_scale(anchor: Mapping[str, Any], index: int) -> float:
    if "meters_per_pixel" in anchor:
        scale = _number(anchor.get("meters_per_pixel"), f"scale_anchors[{index}].meters_per_pixel")
    else:
        actual = _number(anchor.get("actual_length_m"), f"scale_anchors[{index}].actual_length_m")
        pixel_length = anchor.get("pixel_length")
        if pixel_length is None:
            start, end = anchor.get("start"), anchor.get("end")
            if not (isinstance(start, Sequence) and isinstance(end, Sequence) and len(start) >= 2 and len(end) >= 2):
                _contract_error("scale_anchor_invalid", "scale anchor needs pixel_length or start/end", index=index)
            pixel_length = math.hypot(
                _number(end[0], "anchor.end.x") - _number(start[0], "anchor.start.x"),
                _number(end[1], "anchor.end.y") - _number(start[1], "anchor.start.y"),
            )
        pixel_length = _number(pixel_length, f"scale_anchors[{index}].pixel_length")
        if actual <= 0 or pixel_length <= 0:
            _contract_error("scale_anchor_invalid", "scale anchor lengths must be positive", index=index)
        scale = actual / pixel_length
    if scale <= 0:
        _contract_error("scale_anchor_invalid", "scale anchor scale must be positive", index=index)
    return scale


def validate_source_registration(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a source registration.

    Version 1 remains readable for raster projects and historical records.
    CAD production geometry uses version 2, whose 4x4 transform explicitly
    maps CAD XY plus vertical elevation into the right-handed model XYZ frame.
    """
    if not isinstance(value, Mapping):
        _contract_error("registration_invalid", "source registration must be an object")
    result = copy.deepcopy(dict(value))
    supplied_registration_hash = str(result.get("registration_hash") or "")
    version = int(result.get("version", GEOMETRY_CONTRACT_VERSION))
    if version not in {GEOMETRY_CONTRACT_VERSION, SOURCE_REGISTRATION_VERSION}:
        _contract_error("registration_version_unsupported", "unsupported source registration version", version=version)
    result["version"] = version
    source_type = str(result.get("source_type") or "").strip().lower()
    input_grade = str(result.get("input_grade") or "").strip()
    source_hash = str(result.get("source_hash") or "").strip().lower()
    if not source_type:
        _contract_error("registration_source_type_missing", "source_type is required")
    if input_grade not in INPUT_GRADES - {"legacy_unproven"}:
        _contract_error("registration_input_grade_invalid", "registration needs a current input grade", input_grade=input_grade)
    if len(source_hash) != 64 or any(character not in "0123456789abcdef" for character in source_hash):
        _contract_error("registration_source_hash_invalid", "source_hash must be a lowercase SHA-256")
    result.update(source_type=source_type, input_grade=input_grade, source_hash=source_hash)

    source_to_canonical = _matrix3(result.get("source_to_canonical"), "source_to_canonical")
    source_inverse = invert_matrix3(source_to_canonical)
    if _projective_orientation(source_to_canonical) <= 0:
        _contract_error("registration_transform_mirrored", "source_to_canonical must preserve orientation")
    supplied_source_inverse = result.get("canonical_to_source")
    if supplied_source_inverse is not None:
        candidate = _matrix3(supplied_source_inverse, "canonical_to_source")
        if _identity_error(source_to_canonical, candidate) > 1e-8:
            _contract_error("registration_inverse_mismatch", "canonical_to_source is not the forward inverse")

    if version == SOURCE_REGISTRATION_VERSION:
        if source_type != "cad" or input_grade != "vector_authoritative":
            _contract_error(
                "registration_v2_scope_invalid",
                "source registration v2 is currently reserved for authoritative CAD input",
                source_type=source_type, input_grade=input_grade,
            )
        canonical_xyz_to_model = _matrix4(
            result.get("canonical_xyz_to_model"), "canonical_xyz_to_model")
        model_to_canonical_xyz = invert_matrix4(canonical_xyz_to_model)
        uniform_scale = _validate_similarity4(canonical_xyz_to_model, "canonical_xyz_to_model")
        supplied_model_inverse = result.get("model_to_canonical_xyz")
        if supplied_model_inverse is not None:
            candidate = _matrix4(supplied_model_inverse, "model_to_canonical_xyz")
            if _identity_error4(canonical_xyz_to_model, candidate) > 1e-8:
                _contract_error(
                    "registration_inverse_mismatch",
                    "model_to_canonical_xyz is not the forward inverse")
        axis_mapping = result.get("axis_mapping")
        expected_axis_mapping = {
            "cad_x": "+model_x", "cad_y": "-model_z", "elevation": "+model_y",
        }
        if axis_mapping != expected_axis_mapping:
            _contract_error(
                "registration_axis_mapping_invalid",
                "CAD registration v2 must declare the sky-down right-handed axis mapping",
                expected=expected_axis_mapping, actual=axis_mapping,
            )
        result.update(
            source_to_canonical=source_to_canonical,
            canonical_to_source=source_inverse,
            canonical_xyz_to_model=canonical_xyz_to_model,
            model_to_canonical_xyz=model_to_canonical_xyz,
            uniform_scale=uniform_scale,
        )
        model_matrix_error = _identity_error4(canonical_xyz_to_model, model_to_canonical_xyz)
    else:
        canonical_to_model = _matrix3(result.get("canonical_to_model"), "canonical_to_model")
        model_inverse = invert_matrix3(canonical_to_model)
        uniform_scale = _validate_similarity(canonical_to_model, "canonical_to_model")
        supplied_model_inverse = result.get("model_to_canonical")
        if supplied_model_inverse is not None:
            candidate = _matrix3(supplied_model_inverse, "model_to_canonical")
            if _identity_error(canonical_to_model, candidate) > 1e-8:
                _contract_error("registration_inverse_mismatch", "model_to_canonical is not the forward inverse")
        result.update(
            source_to_canonical=source_to_canonical,
            canonical_to_source=source_inverse,
            canonical_to_model=canonical_to_model,
            model_to_canonical=model_inverse,
            uniform_scale=uniform_scale,
        )
        model_matrix_error = _identity_error(canonical_to_model, model_inverse)

    is_raster = input_grade.startswith("raster_")
    if not is_raster:
        _validate_similarity(source_to_canonical, "source_to_canonical")
    matrix_error = max(
        _identity_error(source_to_canonical, source_inverse),
        model_matrix_error,
    )
    measured_error = _number(result.get("measured_roundtrip_error", matrix_error), "measured_roundtrip_error")
    threshold = 0.25 if is_raster else 1e-6
    if measured_error > threshold:
        _contract_error(
            "registration_roundtrip_exceeded", "registration roundtrip error exceeds the source threshold",
            measured=measured_error, threshold=threshold, unit="px" if is_raster else "m",
        )
    result["roundtrip_error"] = measured_error
    result["roundtrip_threshold"] = threshold

    anchors = result.get("scale_anchors") or []
    if not isinstance(anchors, list):
        _contract_error("scale_anchors_invalid", "scale_anchors must be an array")
    anchor_scales = [_anchor_scale(anchor, index) for index, anchor in enumerate(anchors) if isinstance(anchor, Mapping)]
    if len(anchor_scales) != len(anchors):
        _contract_error("scale_anchor_invalid", "every scale anchor must be an object")
    disagreement = 0.0
    if len(anchor_scales) > 1:
        disagreement = (max(anchor_scales) - min(anchor_scales)) / (sum(anchor_scales) / len(anchor_scales))
        if disagreement > 0.02:
            _contract_error(
                "scale_anchor_disagreement", "independent raster scale anchors disagree by more than 2%",
                disagreement=disagreement, threshold=0.02,
            )
    if input_grade == "raster_human_locked" and not anchor_scales:
        _contract_error("raster_scale_anchor_required", "a locked raster needs at least one real dimension anchor")
    if input_grade == "vector_authoritative":
        cad_units = str(result.get("cad_units") or result.get("source_space") or "").strip().lower()
        aliases = {"millimeter": "mm", "millimeters": "mm", "centimeter": "cm", "centimeters": "cm",
                   "meter": "m", "meters": "m"}
        cad_units = aliases.get(cad_units, cad_units)
        if cad_units not in {"mm", "cm", "m"}:
            _contract_error("cad_units_unconfirmed", "authoritative vector input requires confirmed mm/cm/m units")
        result["cad_units"] = cad_units
    result["scale_anchor_count"] = len(anchor_scales)
    result["scale_disagreement"] = disagreement
    expected_registration_hash = canonical_hash(result, exclude_fields=REGISTRATION_HASH_FIELDS)
    if supplied_registration_hash and supplied_registration_hash != expected_registration_hash:
        _contract_error(
            "registration_hash_mismatch", "registration_hash does not cover the current transforms and scale",
            expected=expected_registration_hash, actual=supplied_registration_hash,
        )
    result["registration_hash"] = expected_registration_hash
    return canonicalize(result)


class SourceRegistration(dict):
    """Validated dictionary representation of SourceRegistration v1/v2."""

    def __init__(self, value: Mapping[str, Any] | None = None, **kwargs: Any):
        payload = {**dict(value or {}), **kwargs}
        super().__init__(validate_source_registration(payload))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceRegistration":
        return cls(value)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))


def _point2(value: Any, field: str) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) < 2:
        _contract_error("point_invalid", f"{field} must contain x and z", field=field)
    return [_number(value[0], f"{field}.x"), _number(value[1], f"{field}.z")]


def _polyline(value: Any, field: str, minimum: int) -> list[list[float]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        _contract_error("polyline_invalid", f"{field} must be an array", field=field)
    points = [_point2(point, field) for point in value]
    if len(points) < minimum:
        _contract_error("polyline_too_short", f"{field} has too few points", field=field, minimum=minimum)
    return points


def _polygon_area(points: list[list[float]]) -> float:
    loop = points[:-1] if points[0] == points[-1] else points
    return abs(sum(
        loop[index][0] * loop[(index + 1) % len(loop)][1]
        - loop[(index + 1) % len(loop)][0] * loop[index][1]
        for index in range(len(loop))
    )) / 2.0


def _polygon_self_intersects(points: list[list[float]]) -> bool:
    loop = points[:-1] if points[0] == points[-1] else points
    count = len(loop)

    def orientation(a: list[float], b: list[float], c: list[float]) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def intersects(a: list[float], b: list[float], c: list[float], d: list[float]) -> bool:
        first, second = orientation(a, b, c), orientation(a, b, d)
        third, fourth = orientation(c, d, a), orientation(c, d, b)
        return first * second < -1e-12 and third * fourth < -1e-12

    for first in range(count):
        a, b = loop[first], loop[(first + 1) % count]
        for second in range(first + 1, count):
            # Adjacent edges share an intentional endpoint.
            if second in {first, (first + 1) % count} or first == (second + 1) % count:
                continue
            c, d = loop[second], loop[(second + 1) % count]
            if intersects(a, b, c, d):
                return True
    return False


def validate_wall_assembly(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one source-backed canonical wall footprint."""
    if not isinstance(value, Mapping):
        _contract_error("wall_assembly_invalid", "wall assembly must be an object")
    result = copy.deepcopy(dict(value))
    result["version"] = int(result.get("version", GEOMETRY_CONTRACT_VERSION))
    if result["version"] != GEOMETRY_CONTRACT_VERSION:
        _contract_error("wall_assembly_version_unsupported", "unsupported wall assembly version")
    wall_id = str(result.get("id") or "").strip()
    representation = str(result.get("source_representation") or "").strip()
    review_status = normalize_review_status(result.get("review_status"))
    if not wall_id:
        _contract_error("wall_assembly_id_missing", "wall assembly id is required")
    if representation not in WALL_REPRESENTATIONS:
        _contract_error("wall_representation_invalid", "unknown wall source representation", representation=representation)
    if review_status not in WALL_REVIEW_STATUSES:
        _contract_error("wall_review_status_invalid", "unknown wall review status", review_status=review_status)
    handles = [str(item).strip() for item in (result.get("source_entity_handles") or []) if str(item).strip()]
    if not handles:
        _contract_error("wall_provenance_missing", "a wall assembly must retain at least one source entity handle")
    if representation == "paired_faces" and len(set(handles)) < 2:
        _contract_error("paired_wall_faces_missing", "paired_faces needs at least two distinct source handles")
    if representation == "human_confirmed_ambiguous" and review_status != "confirmed":
        _contract_error("ambiguous_wall_unconfirmed", "ambiguous wall representation must be explicitly confirmed")

    if representation == "redundant_evidence":
        if review_status != "rejected":
            _contract_error(
                "redundant_wall_evidence_not_rejected",
                "redundant wall evidence must have rejected review status",
            )
        if result.get("footprint_polygon") or result.get("centerline"):
            _contract_error(
                "redundant_wall_evidence_has_geometry",
                "redundant wall evidence cannot carry production geometry",
            )
        if result.get("thickness_m") not in {None, ""}:
            _contract_error(
                "redundant_wall_evidence_has_thickness",
                "redundant wall evidence cannot carry production thickness",
            )
        reason_codes = [str(item) for item in result.get("reason_codes") or []]
        proof = result.get("redundancy_evidence")
        if ("cad_wall_source_redundant_with_accepted_footprint" not in reason_codes
                or not isinstance(proof, Mapping)
                or not str(proof.get("accepted_wall_assembly_id") or "").strip()
                or _number(proof.get("uncovered_length_m"), "uncovered_length_m") > 1e-7
                or _number(proof.get("coverage_ratio"), "coverage_ratio") < 1.0 - 1e-9
                or _number(proof.get("axis_angle_difference_deg"),
                           "axis_angle_difference_deg") > 1.0 + 1e-9):
            _contract_error(
                "redundant_wall_evidence_proof_invalid",
                "redundant wall evidence requires a complete full-coverage proof",
            )
        result.update(
            id=wall_id, source_representation=representation,
            review_status=review_status, source_entity_handles=handles,
        )
        return canonicalize(result)

    if representation == "detached_site_boundary_evidence":
        if review_status != "rejected":
            _contract_error(
                "detached_site_boundary_evidence_not_rejected",
                "detached site-boundary evidence must be audit-only",
            )
        if (result.get("footprint_polygon") or result.get("centerline")
                or result.get("opening_axis")
                or result.get("thickness_m") not in {None, ""}):
            _contract_error(
                "detached_site_boundary_evidence_has_geometry",
                "detached site-boundary evidence cannot emit wall geometry",
            )
        reason_codes = [str(item) for item in result.get("reason_codes") or []]
        proof = result.get("detached_site_boundary_evidence")
        method = str((proof or {}).get("method") or "") \
            if isinstance(proof, Mapping) else ""
        valid = bool(
            "cad_detached_site_boundary_not_physical_space_boundary"
            in reason_codes
            and isinstance(proof, Mapping)
            and method in {
                "cad_detached_site_boundary_component_v1",
                "cad_oversized_coalesced_site_boundary_clip_v1",
            }
            and int(proof.get("physical_space_count") or 0) >= 1
            and int(proof.get("original_wall_component_count") or 0) >= 2
            and _number(
                proof.get("occupied_x_span_overlap_ratio"),
                "detached_site_boundary_evidence.occupied_x_span_overlap_ratio",
            ) >= .75 - 1e-9
            and _number(
                proof.get("occupied_z_span_overlap_ratio"),
                "detached_site_boundary_evidence.occupied_z_span_overlap_ratio",
            ) >= .75 - 1e-9
            and _number(
                proof.get("maximum_component_to_occupied_span_ratio"),
                "detached_site_boundary_evidence.maximum_component_to_occupied_span_ratio",
            ) >= 1.25 - 1e-9
            and _number(
                proof.get("component_boundary_length_m"),
                "detached_site_boundary_evidence.component_boundary_length_m",
            ) >= 8.0 - 1e-9
            and isinstance(proof.get("source_wall_geometry"), Mapping)
            and bool(
                proof["source_wall_geometry"].get("centerline")
                or proof["source_wall_geometry"].get("footprint_polygon"))
        )
        if valid:
            occupied_boundary_length = _number(
                proof.get("occupied_space_boundary_length_m"),
                "detached_site_boundary_evidence.occupied_space_boundary_length_m",
            )
            component_boundary_length = _number(
                proof.get("component_boundary_length_m"),
                "detached_site_boundary_evidence.component_boundary_length_m",
            )
            if method == "cad_detached_site_boundary_component_v1":
                valid = bool(
                    occupied_boundary_length > 0
                    and _number(
                        proof.get("component_to_physical_space_distance_m"),
                        "detached_site_boundary_evidence.component_to_physical_space_distance_m",
                    ) >= .35 - 1e-9
                    and _number(
                        proof.get("maximum_component_to_occupied_span_ratio"),
                        "detached_site_boundary_evidence.maximum_component_to_occupied_span_ratio",
                    ) >= 1.25 - 1e-9
                    and component_boundary_length
                    >= occupied_boundary_length * .75 - 1e-9
                )
            else:
                outside_total_length = _number(
                    proof.get("outside_source_assembly_total_length_m"),
                    "detached_site_boundary_evidence.outside_source_assembly_total_length_m",
                )
                valid = bool(
                    occupied_boundary_length > 0
                    and _number(
                        proof.get("maximum_component_to_occupied_span_ratio"),
                        "detached_site_boundary_evidence.maximum_component_to_occupied_span_ratio",
                    ) >= 1.50 - 1e-9
                    and component_boundary_length
                    >= occupied_boundary_length * 2.0 - 1e-9
                    and int(proof.get(
                        "outside_source_assembly_count") or 0) >= 3
                    and outside_total_length >= 12.0 - 1e-9
                    and outside_total_length
                    >= occupied_boundary_length * .50 - 1e-9
                    and _number(
                        proof.get("outside_source_x_span_overlap_ratio"),
                        "detached_site_boundary_evidence.outside_source_x_span_overlap_ratio",
                    ) >= .75 - 1e-9
                    and _number(
                        proof.get("outside_source_z_span_overlap_ratio"),
                        "detached_site_boundary_evidence.outside_source_z_span_overlap_ratio",
                    ) >= .75 - 1e-9
                )
        if not valid:
            _contract_error(
                "detached_site_boundary_evidence_proof_invalid",
                "detached site-boundary evidence requires a measured, oversized, non-space-adjacent component proof",
            )
        result.update(
            id=wall_id, source_representation=representation,
            review_status=review_status, source_entity_handles=handles,
        )
        return canonicalize(result)

    if representation == "nonspace_projected_geometry_evidence":
        if review_status != "rejected":
            _contract_error(
                "nonspace_projected_geometry_evidence_not_rejected",
                "non-space projected geometry evidence must be audit-only",
            )
        if (result.get("footprint_polygon") or result.get("centerline")
                or result.get("opening_axis")
                or result.get("thickness_m") not in {None, ""}):
            _contract_error(
                "nonspace_projected_geometry_evidence_has_geometry",
                "non-space projected geometry evidence cannot emit wall geometry",
            )
        reason_codes = [str(item) for item in result.get("reason_codes") or []]
        proof = result.get("nonspace_projected_geometry_evidence")
        site_proof = ((proof or {}).get("oversized_site_component_proof")
                      if isinstance(proof, Mapping) else None)
        source_geometry = ((proof or {}).get("source_wall_geometry")
                           if isinstance(proof, Mapping) else None)
        valid = bool(
            "cad_projected_geometry_not_adjacent_to_physical_space"
            in reason_codes
            and isinstance(proof, Mapping)
            and proof.get("method")
            == "cad_nonspace_geometry_within_oversized_site_plan_v1"
            and _number(
                proof.get("source_to_physical_space_distance_m"),
                "nonspace_projected_geometry_evidence.source_to_physical_space_distance_m",
            ) >= .35 - 1e-9
            and abs(_number(
                proof.get("physical_space_neighbourhood_m"),
                "nonspace_projected_geometry_evidence.physical_space_neighbourhood_m",
            ) - .35) <= 1e-9
            and _number(
                proof.get("source_length_m"),
                "nonspace_projected_geometry_evidence.source_length_m",
            ) >= .02 - 1e-9
            and isinstance(source_geometry, Mapping)
            and bool(source_geometry.get("centerline")
                     or source_geometry.get("footprint_polygon"))
            and isinstance(site_proof, Mapping)
            and site_proof.get("method")
            == "cad_oversized_coalesced_site_boundary_clip_v1"
            and int(site_proof.get("physical_space_count") or 0) >= 1
            and int(site_proof.get("original_wall_component_count") or 0) >= 2
            and _number(
                site_proof.get("occupied_x_span_overlap_ratio"),
                "nonspace_projected_geometry_evidence.oversized_site_component_proof.occupied_x_span_overlap_ratio",
            ) >= .75 - 1e-9
            and _number(
                site_proof.get("occupied_z_span_overlap_ratio"),
                "nonspace_projected_geometry_evidence.oversized_site_component_proof.occupied_z_span_overlap_ratio",
            ) >= .75 - 1e-9
            and _number(
                site_proof.get("maximum_component_to_occupied_span_ratio"),
                "nonspace_projected_geometry_evidence.oversized_site_component_proof.maximum_component_to_occupied_span_ratio",
            ) >= 1.50 - 1e-9
        )
        if valid:
            occupied_boundary_length = _number(
                site_proof.get("occupied_space_boundary_length_m"),
                "nonspace_projected_geometry_evidence.oversized_site_component_proof.occupied_space_boundary_length_m",
            )
            component_boundary_length = _number(
                site_proof.get("component_boundary_length_m"),
                "nonspace_projected_geometry_evidence.oversized_site_component_proof.component_boundary_length_m",
            )
            outside_total_length = _number(
                site_proof.get("outside_source_assembly_total_length_m"),
                "nonspace_projected_geometry_evidence.oversized_site_component_proof.outside_source_assembly_total_length_m",
            )
            valid = bool(
                occupied_boundary_length > 0
                and component_boundary_length
                >= max(16.0, occupied_boundary_length * 2.0) - 1e-9
                and int(site_proof.get(
                    "outside_source_assembly_count") or 0) >= 3
                and outside_total_length
                >= max(12.0, occupied_boundary_length * .50) - 1e-9
                and _number(
                    site_proof.get("outside_source_x_span_overlap_ratio"),
                    "nonspace_projected_geometry_evidence.oversized_site_component_proof.outside_source_x_span_overlap_ratio",
                ) >= .75 - 1e-9
                and _number(
                    site_proof.get("outside_source_z_span_overlap_ratio"),
                    "nonspace_projected_geometry_evidence.oversized_site_component_proof.outside_source_z_span_overlap_ratio",
                ) >= .75 - 1e-9
            )
        if not valid:
            _contract_error(
                "nonspace_projected_geometry_evidence_proof_invalid",
                "non-space projected geometry evidence requires a measured, oversized-site and space-distance proof",
            )
        result.update(
            id=wall_id, source_representation=representation,
            review_status=review_status, source_entity_handles=handles,
        )
        return canonicalize(result)

    if representation == "projected_detail_evidence":
        if review_status != "rejected":
            _contract_error(
                "projected_detail_evidence_not_rejected",
                "projected detail evidence must have rejected review status",
            )
        if (result.get("footprint_polygon") or result.get("centerline")
                or result.get("opening_axis")
                or result.get("thickness_m") not in {None, ""}):
            _contract_error(
                "projected_detail_evidence_has_geometry",
                "projected detail evidence cannot carry production geometry",
            )
        reason_codes = [str(item) for item in result.get("reason_codes") or []]
        proof = result.get("projected_detail_evidence")
        if ("cad_projected_detail_topology_invariant" not in reason_codes
                or not isinstance(proof, Mapping)
                or proof.get("method")
                != "cad_projected_detail_topology_invariance_v1"
                or int(proof.get("original_space_count") or 0) < 1
                or int(proof.get("trial_space_count") or 0)
                != int(proof.get("original_space_count") or 0)
                or _number(proof.get("space_union_iou"),
                           "projected_detail_evidence.space_union_iou")
                < .995 - 1e-9
                or _number(proof.get("minimum_matched_space_iou"),
                           "projected_detail_evidence.minimum_matched_space_iou")
                < .99 - 1e-9
                or not 0.0 <= _number(
                    proof.get("wall_area_reduction_ratio"),
                    "projected_detail_evidence.wall_area_reduction_ratio",
                ) <= .05 + 1e-9
                or (int(proof.get(
                    "trial_unresolved_wall_assembly_count", -1)) != 0
                    and (int(proof.get(
                        "trial_unresolved_removed_source_count", -1)) != 0
                        or int(proof.get(
                            "trial_new_unresolved_source_count", -1)) != 0))
                or int(proof.get("excluded_entity_count") or 0) < 1
                or not proof.get("source_entity_indexes")):
            _contract_error(
                "projected_detail_evidence_proof_invalid",
                "projected detail rejection requires a complete topology-invariance proof",
            )
        result.update(
            id=wall_id, source_representation=representation,
            review_status=review_status, source_entity_handles=handles,
        )
        return canonicalize(result)

    if representation == "projected_topology_boundary_evidence":
        if review_status != "rejected":
            _contract_error(
                "projected_topology_boundary_evidence_not_rejected",
                "projected topology boundary evidence must be audit-only",
            )
        if (result.get("footprint_polygon") or result.get("centerline")
                or result.get("opening_axis")
                or result.get("thickness_m") not in {None, ""}):
            _contract_error(
                "projected_topology_boundary_evidence_has_geometry",
                "projected topology boundary evidence cannot emit wall geometry",
            )
        reason_codes = [str(item) for item in result.get("reason_codes") or []]
        proof = result.get("projected_topology_boundary_evidence")
        valid = bool(
            "cad_projected_topology_boundary_counterfactual" in reason_codes
            and isinstance(proof, Mapping)
            and proof.get("method")
            == "cad_projected_topology_boundary_counterfactual_v1"
            and int(proof.get("source_entity_index", -1)) >= 0
            and isinstance(proof.get("source_entity_indexes"), list)
            and bool(proof.get("source_entity_indexes"))
            and int(proof.get("source_entity_index")) in {
                int(value) for value in proof.get("source_entity_indexes")
                if isinstance(value, int) or str(value).isdigit()
            }
            and len(str(proof.get("safe_excluded_scope_hash") or "")) == 64
            and int(proof.get("reference_physical_space_count") or 0) >= 1
            and _number(
                proof.get("reference_space_union_iou"),
                "projected_topology_boundary_evidence.reference_space_union_iou",
            ) >= .995 - 1e-9
            and _number(
                proof.get("reference_minimum_matched_space_iou"),
                "projected_topology_boundary_evidence.reference_minimum_matched_space_iou",
            ) >= .99 - 1e-9
            and proof.get("counterfactual_status") == "unresolved"
            and proof.get("counterfactual_reason") in {
                "trial_physical_space_count_changed",
                "trial_physical_topology_changed",
            }
        )
        if valid:
            reference_count = int(proof.get(
                "reference_physical_space_count") or 0)
            counterfactual_count = int(proof.get(
                "counterfactual_physical_space_count") or 0)
            physical_change = counterfactual_count != reference_count
            if not physical_change and proof.get(
                    "counterfactual_space_union_iou") is not None:
                physical_change = _number(
                    proof.get("counterfactual_space_union_iou"),
                    "projected_topology_boundary_evidence.counterfactual_space_union_iou",
                ) < .995 - 1e-9
            if not physical_change and proof.get(
                    "counterfactual_minimum_matched_space_iou") is not None:
                physical_change = _number(
                    proof.get("counterfactual_minimum_matched_space_iou"),
                    "projected_topology_boundary_evidence.counterfactual_minimum_matched_space_iou",
                ) < .99 - 1e-9
            valid = physical_change
        if not valid:
            _contract_error(
                "projected_topology_boundary_evidence_proof_invalid",
                "projected topology evidence requires a measured physical-room counterfactual",
            )
        result.update(
            id=wall_id, source_representation=representation,
            review_status=review_status, source_entity_handles=handles,
        )
        return canonicalize(result)

    if representation == "projected_geometry_dependency_evidence":
        if review_status != "rejected":
            _contract_error(
                "projected_geometry_dependency_evidence_not_rejected",
                "projected geometry dependency evidence must be audit-only",
            )
        if (result.get("footprint_polygon") or result.get("centerline")
                or result.get("opening_axis")
                or result.get("thickness_m") not in {None, ""}):
            _contract_error(
                "projected_geometry_dependency_evidence_has_geometry",
                "projected geometry dependency evidence cannot emit wall geometry",
            )
        reason_codes = [str(item) for item in result.get("reason_codes") or []]
        proof = result.get("projected_geometry_dependency_evidence")
        valid = bool(
            "cad_projected_geometry_dependency_counterfactual" in reason_codes
            and isinstance(proof, Mapping)
            and proof.get("method")
            == "cad_projected_geometry_dependency_counterfactual_v1"
            and isinstance(proof.get("source_entity_indexes"), list)
            and bool(proof.get("source_entity_indexes"))
            and len(str(proof.get("safe_excluded_scope_hash") or "")) == 64
            and int(proof.get("reference_physical_space_count") or 0) >= 1
            and _number(
                proof.get("reference_space_union_iou"),
                "projected_geometry_dependency_evidence.reference_space_union_iou",
            ) >= .995 - 1e-9
            and _number(
                proof.get("reference_minimum_matched_space_iou"),
                "projected_geometry_dependency_evidence.reference_minimum_matched_space_iou",
            ) >= .99 - 1e-9
            and proof.get("counterfactual_status") == "unresolved"
            and proof.get("counterfactual_reason")
            == "trial_wall_assembly_decisions_remain_unresolved"
            and int(proof.get(
                "counterfactual_trial_unresolved_wall_assembly_count") or 0)
            >= 1
            and bool(
                proof.get("counterfactual_removed_source_indexes_still_unresolved")
                or proof.get("counterfactual_new_unresolved_source_indexes"))
        )
        if not valid:
            _contract_error(
                "projected_geometry_dependency_evidence_proof_invalid",
                "projected dependency evidence requires a measured unresolved-decision counterfactual",
            )
        result.update(
            id=wall_id, source_representation=representation,
            review_status=review_status, source_entity_handles=handles,
        )
        return canonicalize(result)

    if representation == "junction_evidence":
        if review_status != "rejected":
            _contract_error(
                "junction_wall_evidence_not_rejected",
                "junction evidence must have rejected review status",
            )
        if (result.get("footprint_polygon") or result.get("centerline")
                or result.get("thickness_m") not in {None, ""}):
            _contract_error(
                "junction_wall_evidence_has_geometry",
                "junction evidence cannot carry production wall geometry",
            )
        proof = result.get("junction_evidence")
        supports = proof.get("supports") if isinstance(proof, Mapping) else []
        support_method = str((proof or {}).get("support_method") or "") \
            if isinstance(proof, Mapping) else ""
        complete_footprint_support = bool(
            isinstance(proof, Mapping)
            and _number(proof.get("uncovered_length_m"),
                        "junction_evidence.uncovered_length_m") <= 1e-7
            and _number(proof.get("coverage_ratio"),
                        "junction_evidence.coverage_ratio") >= 1.0 - 1e-9)
        endpoint_support = bool(
            support_method == "two_endpoint_distinct_accepted_wall_support_v1"
            and isinstance(proof, Mapping)
            and _number(proof.get("endpoint_support_ratio"),
                        "junction_evidence.endpoint_support_ratio") >= 1.0 - 1e-9
            and len({str(row.get("wall_assembly_id") or "")
                     for row in supports if isinstance(row, Mapping)}) >= 2
            and {int(row.get("endpoint_index", -1))
                 for row in supports if isinstance(row, Mapping)} == {0, 1})
        face_cap_support = bool(
            support_method == "single_accepted_wall_face_cap_v1"
            and isinstance(proof, Mapping)
            and len(supports) == 1
            and isinstance(supports[0], Mapping)
            and _number(supports[0].get("length_difference_m"),
                        "junction_evidence.length_difference_m") <= .02 + 1e-9
            and _number(supports[0].get("axis_angle_difference_deg"),
                        "junction_evidence.axis_angle_difference_deg") >= 89.0 - 1e-9
            and abs(
                _number(supports[0].get(
                    "axis_endpoint_to_source_midpoint_m"),
                    "junction_evidence.axis_endpoint_to_source_midpoint_m")
                - _number(supports[0].get(
                    "expected_half_thickness_offset_m"),
                    "junction_evidence.expected_half_thickness_offset_m")
            ) <= .02 + 1e-9)
        opening_jamb_support = bool(
            support_method == "accepted_opening_axis_endpoint_jamb_v1"
            and isinstance(proof, Mapping)
            and len(supports) == 1
            and isinstance(supports[0], Mapping)
            and str(supports[0].get("candidate_id") or "").strip()
            and .06 - 1e-9 <= _number(
                proof.get("source_length_m"),
                "junction_evidence.source_length_m") <= .60 + 1e-9
            and _number(supports[0].get("axis_angle_difference_deg"),
                        "junction_evidence.axis_angle_difference_deg") >= 89.0 - 1e-9
            and _number(supports[0].get("endpoint_distance_m"),
                        "junction_evidence.endpoint_distance_m") <= .015 + 1e-9)
        global_corner_support = bool(
            support_method == "proved_global_topology_corner_chain_v1"
            and isinstance(proof, Mapping)
            and len(supports) == 2
            and len({str(row.get("wall_assembly_id") or "")
                     for row in supports if isinstance(row, Mapping)}) == 2
            and {int(row.get("endpoint_index", -1))
                 for row in supports if isinstance(row, Mapping)} == {0, 1}
            and all(_number(row.get("endpoint_distance_m"),
                            "junction_evidence.endpoint_distance_m") <= .02 + 1e-9
                    for row in supports if isinstance(row, Mapping))
            and all(_number(
                        row.get("source_to_support_axis_angle_difference_deg"),
                        "junction_evidence.source_to_support_axis_angle_difference_deg",
                    ) <= 1.5 + 1e-9
                    for row in supports if isinstance(row, Mapping))
            and len({str(value) for value in
                     proof.get("chain_source_handles") or [] if str(value)}) >= 2
            and _number(proof.get("shared_endpoint_distance_m"),
                        "junction_evidence.shared_endpoint_distance_m") <= .02 + 1e-9
            and _number(proof.get("axis_angle_difference_deg"),
                        "junction_evidence.axis_angle_difference_deg") >= 87.0 - 1e-9
            and len(proof.get("source_wall_mask_coverage_ratios") or []) == 2
            and min(_number(value, "junction_evidence.wall_mask_coverage_ratio")
                    for value in proof.get("source_wall_mask_coverage_ratios") or [])
            >= .995 - 1e-9
            and re.fullmatch(r"[0-9a-f]{64}",
                             str(proof.get("global_topology_hash") or "")) is not None
        )
        global_face_extension = bool(
            support_method == "accepted_wall_face_global_corner_extension_v1"
            and isinstance(proof, Mapping)
            and len(supports) == 1
            and isinstance(supports[0], Mapping)
            and .10 - 1e-9 <= _number(
                proof.get("source_length_m"),
                "junction_evidence.source_length_m") <= .80 + 1e-9
            and _number(proof.get("source_wall_mask_coverage_ratio"),
                        "junction_evidence.source_wall_mask_coverage_ratio")
            >= .995 - 1e-9
            and _number(proof.get("source_wall_mask_boundary_coverage_ratio"),
                        "junction_evidence.source_wall_mask_boundary_coverage_ratio")
            >= .80 - 1e-9
            and _number(supports[0].get(
                "source_to_support_axis_angle_difference_deg"),
                "junction_evidence.source_to_support_axis_angle_difference_deg")
            <= 1.5 + 1e-9
            and .06 <= _number(supports[0].get("support_wall_thickness_m"),
                               "junction_evidence.support_wall_thickness_m") <= .60
            and _number(supports[0].get("face_offset_delta_m"),
                        "junction_evidence.face_offset_delta_m") <= .02 + 1e-9
            and _number(supports[0].get("axial_gap_m"),
                        "junction_evidence.axial_gap_m")
            <= _number(supports[0].get("support_wall_thickness_m"),
                       "junction_evidence.support_wall_thickness_m") + .02 + 1e-9
            and _number(supports[0].get("support_endpoint_distance_m"),
                        "junction_evidence.support_endpoint_distance_m")
            <= _number(supports[0].get("support_wall_thickness_m"),
                       "junction_evidence.support_wall_thickness_m") + .02 + 1e-9
            and _number(supports[0].get("terminal_global_boundary_distance_m"),
                        "junction_evidence.terminal_global_boundary_distance_m")
            <= .015 + 1e-9
            and supports[0].get("terminal_forward_outside_samples") == [True, True]
            and int(supports[0].get("terminal_normal_inside_sample_count") or 0) >= 1
            and {int(supports[0].get("support_source_endpoint_index", -1)),
                 int(supports[0].get("terminal_source_endpoint_index", -1))} == {0, 1}
            and re.fullmatch(r"[0-9a-f]{64}",
                             str(proof.get("global_topology_hash") or "")) is not None
        )
        if (not supports or not (
                    complete_footprint_support or endpoint_support
                    or face_cap_support or opening_jamb_support
                    or global_corner_support or global_face_extension)
                or any(not isinstance(row, Mapping)
                       or not str(row.get("wall_assembly_id") or "").strip()
                       for row in supports)):
            _contract_error(
                "junction_wall_evidence_proof_invalid",
                "junction evidence requires complete transverse footprint support",
            )
        result.update(
            id=wall_id, source_representation=representation,
            review_status=review_status, source_entity_handles=handles,
        )
        return canonicalize(result)

    if representation == "opening_evidence":
        if review_status != "rejected":
            _contract_error(
                "opening_wall_evidence_not_rejected",
                "opening-axis evidence must have rejected review status",
            )
        if (result.get("footprint_polygon") or result.get("centerline")
                or result.get("thickness_m") not in {None, ""}):
            _contract_error(
                "opening_wall_evidence_has_geometry",
                "opening-axis evidence cannot carry production wall geometry",
            )
        proof = result.get("opening_evidence")
        thresholds = proof.get("thresholds") if isinstance(proof, Mapping) else {}
        method = str((proof or {}).get("method") or "") \
            if isinstance(proof, Mapping) else ""
        exact_source_ownership = bool(
            isinstance(proof, Mapping)
            and method == "accepted_opening_source_handle_ownership_v1"
            and str(proof.get("candidate_id") or "").strip()
            and str(proof.get("accepted_wall_assembly_id") or "").strip()
            and {str(value) for value in proof.get("owned_source_handles") or []
                 if str(value)}.issubset(set(handles))
            and bool([value for value in proof.get("owned_source_handles") or []
                      if str(value)])
            and .06 - 1e-9 <= _number(
                proof.get("source_length_m"),
                "opening_evidence.source_length_m") <= .30 + 1e-9
            and len(_polyline(proof.get("opening_axis_cad_m"),
                              "opening_evidence.opening_axis_cad_m", 2)) == 2
        )
        bidirectional_axis_coverage = bool(
            isinstance(proof, Mapping)
            and not method
            and str(proof.get("candidate_id") or "").strip()
            and _number(proof.get("source_coverage_ratio"),
                        "opening_evidence.source_coverage_ratio") >= .995 - 1e-9
            and _number(proof.get("opening_axis_coverage_ratio"),
                        "opening_evidence.opening_axis_coverage_ratio") >= .995 - 1e-9
            and _number(proof.get("maximum_distance_m"),
                        "opening_evidence.maximum_distance_m") <= .015 + 1e-9
            and _number(proof.get("axis_angle_difference_deg"),
                        "opening_evidence.axis_angle_difference_deg") <= 1.0 + 1e-9
            and isinstance(thresholds, Mapping)
        )
        overhang = proof.get("source_axis_overhang_m") \
            if isinstance(proof, Mapping) else None
        contained_threshold_axis = bool(
            isinstance(proof, Mapping)
            and method == "accepted_opening_contained_threshold_axis_v1"
            and str(proof.get("candidate_id") or "").strip()
            and str(proof.get("accepted_wall_assembly_id") or "").strip()
            and _number(proof.get("source_coverage_ratio"),
                        "opening_evidence.source_coverage_ratio") >= .94 - 1e-9
            and _number(proof.get("opening_axis_coverage_ratio"),
                        "opening_evidence.opening_axis_coverage_ratio") >= .995 - 1e-9
            and _number(proof.get("maximum_lateral_offset_m"),
                        "opening_evidence.maximum_lateral_offset_m") <= .015 + 1e-9
            and _number(proof.get("axis_angle_difference_deg"),
                        "opening_evidence.axis_angle_difference_deg") <= 1.0 + 1e-9
            and _number(proof.get("length_difference_m"),
                        "opening_evidence.length_difference_m") <= .06 + 1e-9
            and isinstance(overhang, list) and len(overhang) == 2
            and max(_number(value, "opening_evidence.source_axis_overhang_m")
                    for value in overhang) <= .05 + 1e-9
            and sum(_number(value, "opening_evidence.source_axis_overhang_m")
                    for value in overhang) <= .06 + 1e-9
            and _number(proof.get("source_length_m"),
                        "opening_evidence.source_length_m") >=
                _number(proof.get("opening_axis_length_m"),
                        "opening_evidence.opening_axis_length_m") - 1e-9
            and isinstance(thresholds, Mapping)
        )
        return_path = proof.get("degenerate_return_path_evidence") \
            if isinstance(proof, Mapping) else None
        parallel_opening_wall_face = bool(
            isinstance(proof, Mapping)
            and method == "accepted_opening_parallel_wall_face_v1"
            and str(proof.get("candidate_id") or "").strip()
            and str(proof.get("accepted_wall_assembly_id") or "").strip()
            and len(_polyline(proof.get("opening_axis_cad_m"),
                              "opening_evidence.opening_axis_cad_m", 2)) == 2
            and len(_polyline(proof.get("source_axis_model_m"),
                              "opening_evidence.source_axis_model_m", 2)) == 2
            and isinstance(return_path, Mapping)
            and return_path.get("method")
                == "cad_closed_two_point_return_path_v1"
            and int(return_path.get("unique_point_count") or 0) == 2
            and .06 - 1e-9 <= _number(
                proof.get("host_wall_thickness_m"),
                "opening_evidence.host_wall_thickness_m") <= .60 + 1e-9
            and abs(
                _number(proof.get("expected_half_thickness_m"),
                        "opening_evidence.expected_half_thickness_m")
                - _number(proof.get("host_wall_thickness_m"),
                          "opening_evidence.host_wall_thickness_m") / 2
            ) <= 1e-7
            and .03 - 1e-9 <= _number(
                proof.get("measured_lateral_offset_m"),
                "opening_evidence.measured_lateral_offset_m") <= .30 + 1e-9
            and _number(proof.get("lateral_offset_spread_m"),
                        "opening_evidence.lateral_offset_spread_m") <= .005 + 1e-9
            and abs(
                _number(proof.get("measured_lateral_offset_m"),
                        "opening_evidence.measured_lateral_offset_m")
                - _number(proof.get("expected_half_thickness_m"),
                          "opening_evidence.expected_half_thickness_m")
            ) <= .02 + 1e-9
            and abs(
                _number(proof.get("half_thickness_offset_delta_m"),
                        "opening_evidence.half_thickness_offset_delta_m")
                - abs(
                    _number(proof.get("measured_lateral_offset_m"),
                            "opening_evidence.measured_lateral_offset_m")
                    - _number(proof.get("expected_half_thickness_m"),
                              "opening_evidence.expected_half_thickness_m")
                )
            ) <= 1e-7
            and _number(proof.get("source_axial_coverage_ratio"),
                        "opening_evidence.source_axial_coverage_ratio")
                >= .995 - 1e-9
            and _number(proof.get("opening_axis_axial_coverage_ratio"),
                        "opening_evidence.opening_axis_axial_coverage_ratio")
                >= .995 - 1e-9
            and _number(proof.get("axis_angle_difference_deg"),
                        "opening_evidence.axis_angle_difference_deg")
                <= 1.0 + 1e-9
            and _number(proof.get("length_difference_m"),
                        "opening_evidence.length_difference_m")
                <= max(.02, _number(
                    proof.get("opening_axis_length_m"),
                    "opening_evidence.opening_axis_length_m") * .02) + 1e-9
            and isinstance(thresholds, Mapping)
        )
        companion_axis = (_polyline(
            proof.get("source_axis_model_m"),
            "opening_evidence.source_axis_model_m", 2)
            if isinstance(proof, Mapping)
            and method == "accepted_opening_frame_companion_rail_v1"
            else [])
        frame_bounds = proof.get("source_frame_bbox_model_m") \
            if isinstance(proof, Mapping) else []
        frame_geometry = proof.get("frame_geometry") \
            if isinstance(proof, Mapping) else {}
        frame_short_span = (_number(
            proof.get("frame_short_span_m"),
            "opening_evidence.frame_short_span_m")
            if isinstance(proof, Mapping)
            and method == "accepted_opening_frame_companion_rail_v1"
            else 0.0)
        companion_lateral_offset = (_number(
            proof.get("measured_lateral_offset_m"),
            "opening_evidence.measured_lateral_offset_m")
            if isinstance(proof, Mapping)
            and method == "accepted_opening_frame_companion_rail_v1"
            else 0.0)
        companion_frame_rail = bool(
            isinstance(proof, Mapping)
            and method == "accepted_opening_frame_companion_rail_v1"
            and str(proof.get("candidate_id") or "").strip()
            and str(proof.get("accepted_wall_assembly_id") or "").strip()
            and len(_polyline(proof.get("opening_axis_cad_m"),
                              "opening_evidence.opening_axis_cad_m", 2)) == 2
            and len(companion_axis) == 2
            and isinstance(frame_bounds, list) and len(frame_bounds) == 4
            and all(math.isfinite(_number(
                value, "opening_evidence.source_frame_bbox_model_m"))
                    for value in frame_bounds)
            and all(
                _number(frame_bounds[0], "opening_evidence.frame_min_x")
                    - .015 - 1e-9 <= point[0]
                    <= _number(frame_bounds[2], "opening_evidence.frame_max_x")
                    + .015 + 1e-9
                and _number(frame_bounds[1], "opening_evidence.frame_min_z")
                    - .015 - 1e-9 <= point[1]
                    <= _number(frame_bounds[3], "opening_evidence.frame_max_z")
                    + .015 + 1e-9
                for point in companion_axis)
            and isinstance(frame_geometry, Mapping)
            and int(frame_geometry.get("long_rail_count") or 0) >= 2
            and int(frame_geometry.get("cross_member_count") or 0) >= 2
            and bool(frame_geometry.get("opposite_wall_face_support"))
            and .04 - 1e-9 <= frame_short_span <= .60 + 1e-9
            and .02 - 1e-9 <= companion_lateral_offset
                <= min(.30, frame_short_span / 2.0 + .015) + 1e-9
            and _number(proof.get("lateral_offset_spread_m"),
                        "opening_evidence.lateral_offset_spread_m")
                <= .005 + 1e-9
            and _number(proof.get("source_axial_coverage_ratio"),
                        "opening_evidence.source_axial_coverage_ratio")
                >= .995 - 1e-9
            and _number(proof.get("opening_axis_axial_coverage_ratio"),
                        "opening_evidence.opening_axis_axial_coverage_ratio")
                >= .995 - 1e-9
            and _number(proof.get("axis_angle_difference_deg"),
                        "opening_evidence.axis_angle_difference_deg")
                <= 1.0 + 1e-9
            and abs(_number(proof.get("source_length_m"),
                            "opening_evidence.source_length_m")
                    - _number(proof.get("opening_axis_length_m"),
                              "opening_evidence.opening_axis_length_m"))
                <= max(.02, _number(proof.get("opening_axis_length_m"),
                                    "opening_evidence.opening_axis_length_m")
                       * .02) + 1e-9
            and isinstance(thresholds, Mapping)
        )
        if not (exact_source_ownership or bidirectional_axis_coverage
                or contained_threshold_axis or parallel_opening_wall_face
                or companion_frame_rail):
            _contract_error(
                "opening_wall_evidence_proof_invalid",
                "opening-axis evidence requires exact ownership, bidirectional coverage, a bounded containing threshold, or a measured frame-face proof",
            )
        result.update(
            id=wall_id, source_representation=representation,
            review_status=review_status, source_entity_handles=handles,
        )
        return canonicalize(result)

    if representation in {
            "global_topology_connector_evidence",
            "global_topology_boundary_evidence"}:
        if review_status != "rejected":
            _contract_error(
                "global_topology_short_evidence_not_rejected",
                "short global-topology wall evidence must be audit-only",
            )
        if (result.get("footprint_polygon") or result.get("centerline")
                or result.get("thickness_m") not in {None, ""}):
            _contract_error(
                "global_topology_short_evidence_has_geometry",
                "short global-topology evidence cannot duplicate wall geometry",
            )
        proof_key = (
            "global_topology_connector_evidence"
            if representation == "global_topology_connector_evidence"
            else "global_topology_boundary_evidence")
        expected_method = (
            "proved_global_wall_transverse_connector_v1"
            if representation == "global_topology_connector_evidence"
            else "proved_global_wall_short_boundary_face_v1")
        proof = result.get(proof_key)
        source_length = (_number(
            proof.get("source_length_m"), f"{proof_key}.source_length_m")
            if isinstance(proof, Mapping) else 0.0)
        reference_width = (_number(
            proof.get("reference_wall_width_m"),
            f"{proof_key}.reference_wall_width_m")
            if isinstance(proof, Mapping) else 0.0)
        endpoint_distances = [
            _number(value, f"{proof_key}.endpoint_wall_boundary_distances_m")
            for value in (proof.get("endpoint_wall_boundary_distances_m") or [])
        ] if isinstance(proof, Mapping) else []
        midpoint_distance = (_number(
            proof.get("midpoint_wall_boundary_distance_m"),
            f"{proof_key}.midpoint_wall_boundary_distance_m")
            if isinstance(proof, Mapping) else 0.0)
        common_valid = bool(
            isinstance(proof, Mapping)
            and str(proof.get("method") or "") == expected_method
            and str(proof.get("global_topology_method") or "")
                == "cad-global-wall-topology-v1"
            and str(proof.get("global_topology_status") or "") == "proved"
            and re.fullmatch(r"[0-9a-f]{64}",
                             str(proof.get("global_topology_hash") or ""))
                is not None
            and [str(value) for value in
                 proof.get("global_wall_footprint_ids") or [] if str(value)]
            and .06 - 1e-9 <= source_length <= .60 + 1e-9
            and .06 - 1e-9 <= reference_width <= .60 + 1e-9
            and abs(source_length - reference_width) <= .02 + 1e-9
            and _number(proof.get("source_wall_mask_coverage_ratio"),
                        f"{proof_key}.source_wall_mask_coverage_ratio")
                >= .995 - 1e-9
            and len(endpoint_distances) == 2
            and max(endpoint_distances) <= .02 + 1e-9
            and int(proof.get("valid_perpendicular_cross_section_count") or 0)
                == 0)
        positional_valid = bool(
            midpoint_distance >= max(.025, source_length * .20) - 1e-9
            if representation == "global_topology_connector_evidence"
            else midpoint_distance <= .02 + 1e-9)
        if not common_valid or not positional_valid:
            _contract_error(
                "global_topology_short_evidence_proof_invalid",
                "short global-topology evidence requires a measured-width positional proof",
            )
        result.update(
            id=wall_id, source_representation=representation,
            review_status=review_status, source_entity_handles=handles,
        )
        return canonicalize(result)

    if representation == "global_topology_micro_evidence":
        if review_status != "rejected":
            _contract_error(
                "global_topology_micro_evidence_not_rejected",
                "embedded micro wall evidence must be audit-only",
            )
        if (result.get("footprint_polygon") or result.get("centerline")
                or result.get("thickness_m") not in {None, ""}):
            _contract_error(
                "global_topology_micro_evidence_has_geometry",
                "embedded micro evidence cannot duplicate wall geometry",
            )
        proof = result.get("global_topology_micro_evidence")
        endpoint_distances = [
            _number(value,
                    "global_topology_micro_evidence.endpoint_distance_m")
            for value in (proof.get("endpoint_wall_boundary_distances_m") or [])
        ] if isinstance(proof, Mapping) else []
        proof_method = str(proof.get("method") or "") \
            if isinstance(proof, Mapping) else ""
        positional_valid = bool(
            len(endpoint_distances) == 2
            and (
                proof_method == "proved_global_wall_embedded_micro_detail_v1"
                and min(endpoint_distances) >= max(
                    .015, _number(
                        proof.get("source_length_m"),
                        "global_topology_micro_evidence.source_length_m")) - 1e-9
                and _number(proof.get("midpoint_wall_boundary_distance_m"),
                            "global_topology_micro_evidence.midpoint_distance")
                    >= .025 - 1e-9
                or proof_method
                    == "proved_global_wall_boundary_micro_detail_v1"
                and max(endpoint_distances) <= .02 + 1e-9
                and _number(proof.get("midpoint_wall_boundary_distance_m"),
                            "global_topology_micro_evidence.midpoint_distance")
                    <= .02 + 1e-9))
        valid = bool(
            isinstance(proof, Mapping)
            and proof_method in {
                "proved_global_wall_embedded_micro_detail_v1",
                "proved_global_wall_boundary_micro_detail_v1",
            }
            and proof.get("global_topology_method")
                == "cad-global-wall-topology-v1"
            and proof.get("global_topology_status") == "proved"
            and re.fullmatch(r"[0-9a-f]{64}", str(
                proof.get("global_topology_hash") or "")) is not None
            and [str(value) for value in
                 proof.get("global_wall_footprint_ids") or [] if str(value)]
            and .02 - 1e-9 <= _number(
                proof.get("source_length_m"),
                "global_topology_micro_evidence.source_length_m")
                < .06 - 1e-9
            and _number(proof.get("source_wall_mask_coverage_ratio"),
                        "global_topology_micro_evidence.coverage")
                >= .995 - 1e-9
            and positional_valid)
        if not valid:
            _contract_error(
                "global_topology_micro_evidence_proof_invalid",
                "micro evidence requires independent embedding in proved wall material",
            )
        result.update(
            id=wall_id, source_representation=representation,
            review_status=review_status, source_entity_handles=handles,
        )
        return canonicalize(result)

    if representation == "global_topology_piecewise_evidence":
        if review_status != "rejected":
            _contract_error(
                "global_topology_piecewise_evidence_not_rejected",
                "piecewise wall source evidence must be audit-only",
            )
        if (result.get("footprint_polygon") or result.get("centerline")
                or result.get("thickness_m") not in {None, ""}):
            _contract_error(
                "global_topology_piecewise_evidence_has_geometry",
                "piecewise source evidence cannot duplicate wall geometry",
            )
        proof = result.get("global_topology_piecewise_evidence")
        sections = proof.get("classified_cross_sections") \
            if isinstance(proof, Mapping) else []
        inferred_width = (_number(
            proof.get("inferred_single_run_width_m"),
            "global_topology_piecewise_evidence.inferred_width_m")
            if isinstance(proof, Mapping) else 0.0)
        section_validity = []
        for section in sections or []:
            if not isinstance(section, Mapping):
                section_validity.append(False)
                continue
            width = _number(
                section.get("width_m"),
                "global_topology_piecewise_evidence.width_m")
            signed_min = _number(
                section.get("signed_min_offset_m"),
                "global_topology_piecewise_evidence.signed_min_offset_m")
            signed_max = _number(
                section.get("signed_max_offset_m"),
                "global_topology_piecewise_evidence.signed_max_offset_m")
            midpoint = _number(
                section.get("section_midpoint_offset_m"),
                "global_topology_piecewise_evidence.midpoint_offset_m")
            role = str(section.get("source_role") or "")
            role_valid = bool(
                role == "centerline"
                and abs(width - inferred_width) <= .02 + 1e-9
                and abs(midpoint) <= .015 + 1e-9
                or role == "positive_boundary_face"
                and abs(signed_min) <= .015 + 1e-9
                and signed_max >= .06 - 1e-9
                or role == "negative_boundary_face"
                and abs(signed_max) <= .015 + 1e-9
                and signed_min <= -.06 + 1e-9)
            section_validity.append(
                .06 - 1e-9 <= width <= .60 + 1e-9 and role_valid)
        fractions = [_number(
            row.get("fraction"),
            "global_topology_piecewise_evidence.fraction")
            for row in sections or [] if isinstance(row, Mapping)]
        source_roles = {
            str(row.get("source_role") or "")
            for row in sections or [] if isinstance(row, Mapping)
            and str(row.get("source_role") or "")}
        duplicate_source_ids = [
            str(value) for value in
            (proof.get("collinear_duplicate_source_ids") or [])
            if str(value)
        ] if isinstance(proof, Mapping) else []
        piecewise_valid = bool(
            isinstance(proof, Mapping)
            and proof.get("method") == "proved_global_wall_piecewise_role_v1"
            and proof.get("global_topology_method")
                == "cad-global-wall-topology-v1"
            and proof.get("global_topology_status") == "proved"
            and re.fullmatch(r"[0-9a-f]{64}", str(
                proof.get("global_topology_hash") or "")) is not None
            and [str(value) for value in
                 proof.get("global_wall_footprint_ids") or [] if str(value)]
            and _number(proof.get("source_length_m"),
                        "global_topology_piecewise_evidence.source_length_m")
                >= .15 - 1e-9
            and _number(proof.get("source_wall_mask_coverage_ratio"),
                        "global_topology_piecewise_evidence.coverage")
                >= .995 - 1e-9
            and .06 <= inferred_width <= .20
            and len(sections or []) >= 6
            and len(fractions) == len(sections or [])
            and all(section_validity)
            and max(fractions) - min(fractions) >= .50 - 1e-9
            and (len(source_roles) >= 2 or len(duplicate_source_ids) == 1)
            and int(proof.get("classified_cross_section_count") or 0)
                == len(sections or [])
            and int(proof.get("valid_cross_section_count") or 0)
                - len(sections or []) <= 1)
        if not piecewise_valid:
            _contract_error(
                "global_topology_piecewise_evidence_proof_invalid",
                "piecewise evidence requires six spread centerline/boundary samples",
            )
        result.update(
            id=wall_id, source_representation=representation,
            review_status=review_status, source_entity_handles=handles,
        )
        return canonicalize(result)

    if representation == "global_topology_evidence":
        if review_status != "rejected":
            _contract_error(
                "global_topology_wall_evidence_not_rejected",
                "global topology source evidence must be audit-only",
            )
        if (result.get("footprint_polygon") or result.get("centerline")
                or result.get("thickness_m") not in {None, ""}):
            _contract_error(
                "global_topology_wall_evidence_has_geometry",
                "global topology evidence cannot duplicate canonical wall geometry",
            )
        proof = result.get("global_topology_evidence")
        cross_sections = proof.get("cross_sections") \
            if isinstance(proof, Mapping) else []
        widths = [
            _number(row.get("width_m"), "global_topology_evidence.width_m")
            for row in cross_sections if isinstance(row, Mapping)
        ]
        topology_hash = str((proof or {}).get("global_topology_hash") or "") \
            if isinstance(proof, Mapping) else ""
        method = str((proof or {}).get("method") or "") \
            if isinstance(proof, Mapping) else ""
        common_invalid = bool(
            not isinstance(proof, Mapping)
                or method not in {
                    "accepted_space_boundary_stable_wall_cross_section_v1",
                    "proved_global_wall_strip_role_v1",
                }
                or str(proof.get("global_topology_method") or "")
                != "cad-global-wall-topology-v1"
                or str(proof.get("global_topology_status") or "") != "proved"
                or not re.fullmatch(r"[0-9a-f]{64}", topology_hash)
                or not [str(value) for value in
                        proof.get("global_wall_footprint_ids") or []
                        if str(value)]
                or _number(proof.get("source_length_m"),
                           "global_topology_evidence.source_length_m") < .15 - 1e-9
                or _number(proof.get("source_wall_mask_coverage_ratio"),
                           "global_topology_evidence.source_wall_mask_coverage_ratio")
                < .995 - 1e-9
                or len(cross_sections) != 3 or len(widths) != 3
                or any(not .06 <= width <= .60 for width in widths)
                or max(widths) - min(widths) > .06 + 1e-9)
        space_boundary_invalid = bool(
            method == "accepted_space_boundary_stable_wall_cross_section_v1"
            and (
                _number(proof.get("space_boundary_coverage_ratio"),
                        "global_topology_evidence.space_boundary_coverage_ratio")
                < .80 - 1e-9
                or _number(proof.get("nearest_space_boundary_distance_m"),
                           "global_topology_evidence.nearest_space_boundary_distance_m")
                > max(widths) + .025 + 1e-9))
        strip_role = str((proof or {}).get("strip_role") or "") \
            if isinstance(proof, Mapping) else ""
        reference_width = (_number(
            proof.get("reference_width_m"),
            "global_topology_evidence.reference_width_m")
            if method == "proved_global_wall_strip_role_v1" else 0.0)
        wall_boundary_coverage = (_number(
            proof.get("wall_mask_boundary_coverage_ratio"),
            "global_topology_evidence.wall_mask_boundary_coverage_ratio")
            if method == "proved_global_wall_strip_role_v1" else 0.0)
        section_midpoints = [
            _number(row.get("section_midpoint_offset_m"),
                    "global_topology_evidence.section_midpoint_offset_m")
            for row in cross_sections if isinstance(row, Mapping)
        ] if method == "proved_global_wall_strip_role_v1" else []
        signed_ranges = [(
            _number(row.get("signed_min_offset_m"),
                    "global_topology_evidence.signed_min_offset_m"),
            _number(row.get("signed_max_offset_m"),
                    "global_topology_evidence.signed_max_offset_m"),
        ) for row in cross_sections if isinstance(row, Mapping)] \
            if method == "proved_global_wall_strip_role_v1" else []
        centered_strip_valid = bool(
            strip_role == "inferred_single_run_centerline"
            and .06 <= reference_width <= .20
            and wall_boundary_coverage <= .20 + 1e-9
            and len(section_midpoints) == 3
            and all(abs(value) <= .015 + 1e-9 for value in section_midpoints)
            and all(abs(width - reference_width) <= .02 + 1e-9
                    for width in widths))
        boundary_sides = []
        for signed_min, signed_max in signed_ranges:
            if abs(signed_min) <= .015 + 1e-9 and signed_max >= .06 - 1e-9:
                boundary_sides.append("positive")
            elif abs(signed_max) <= .015 + 1e-9 and signed_min <= -.06 + 1e-9:
                boundary_sides.append("negative")
            else:
                boundary_sides.append("")
        boundary_face_valid = bool(
            strip_role == "measured_wall_boundary_face"
            and .06 <= reference_width <= .60
            and wall_boundary_coverage >= .80 - 1e-9
            and len(boundary_sides) == 3
            and len(set(boundary_sides)) == 1 and boundary_sides[0]
            and str(proof.get("consistent_wall_side") or "") == boundary_sides[0]
            and all(abs(width - reference_width) <= .06 + 1e-9
                    for width in widths))
        independent_sections = (proof.get("independent_cross_sections") or []) \
            if isinstance(proof, Mapping) else []
        independent_widths = [
            _number(row.get("width_m"),
                    "global_topology_evidence.independent_width_m")
            for row in independent_sections if isinstance(row, Mapping)
        ]
        independent_midpoints = [
            _number(row.get("section_midpoint_offset_m"),
                    "global_topology_evidence.independent_midpoint_offset_m")
            for row in independent_sections if isinstance(row, Mapping)
        ]
        independent_common_valid = bool(
            len(independent_sections) >= 5
            and len(independent_widths) == len(independent_sections)
            and all(.09 <= width <= .60 for width in independent_widths)
            and max(independent_widths) - min(independent_widths)
                <= .02 + 1e-9
            and all(abs(width - reference_width) <= .02 + 1e-9
                    for width in independent_widths))
        local_centerline_valid = bool(
            strip_role == "independently_supported_local_centerline"
            and independent_common_valid
            and wall_boundary_coverage <= .20 + 1e-9
            and len(independent_midpoints) == len(independent_sections)
            and all(abs(value) <= .015 + 1e-9
                    for value in independent_midpoints))
        local_boundary_coverage = (_number(
            proof.get("local_wall_boundary_coverage_ratio"),
            "global_topology_evidence.local_wall_boundary_coverage_ratio")
            if strip_role == "independently_supported_local_boundary_face"
            else 0.0)
        local_boundary_sides = []
        for row in independent_sections:
            if not isinstance(row, Mapping):
                continue
            signed_min = _number(
                row.get("signed_min_offset_m"),
                "global_topology_evidence.independent_signed_min_offset_m")
            signed_max = _number(
                row.get("signed_max_offset_m"),
                "global_topology_evidence.independent_signed_max_offset_m")
            if abs(signed_min) <= .04 + 1e-9 and signed_max >= .09 - 1e-9:
                local_boundary_sides.append("positive")
            elif abs(signed_max) <= .04 + 1e-9 and signed_min <= -.09 + 1e-9:
                local_boundary_sides.append("negative")
            else:
                local_boundary_sides.append("")
        local_boundary_face_valid = bool(
            strip_role == "independently_supported_local_boundary_face"
            and independent_common_valid
            and local_boundary_coverage >= .80 - 1e-9
            and len(local_boundary_sides) == len(independent_sections)
            and len(set(local_boundary_sides)) == 1
            and local_boundary_sides[0]
            and str(proof.get("consistent_wall_side") or "")
                == local_boundary_sides[0])
        strip_invalid = bool(
            method == "proved_global_wall_strip_role_v1"
            and not (centered_strip_valid or boundary_face_valid
                     or local_centerline_valid or local_boundary_face_valid))
        if common_invalid or space_boundary_invalid or strip_invalid:
            _contract_error(
                "global_topology_wall_evidence_proof_invalid",
                "global topology evidence requires stable wall sections and a closed-space boundary",
            )
        result.update(
            id=wall_id, source_representation=representation,
            review_status=review_status, source_entity_handles=handles,
        )
        return canonicalize(result)

    if representation == "global_topology_opening_host":
        proof = result.get("global_topology_opening_evidence")
        if not isinstance(proof, Mapping):
            _contract_error(
                "global_opening_host_proof_missing",
                "global topology opening host requires wall-mask evidence",
            )
        if (_number(proof.get("wall_mask_axis_coverage_ratio"),
                    "wall_mask_axis_coverage_ratio") < .90
                or not .06 <= _number(
                    proof.get("wall_cross_section_thickness_m"),
                    "wall_cross_section_thickness_m") <= .60
                or not str(proof.get("candidate_id") or "").strip()
                or len(_polyline(proof.get("opening_axis_cad_m"),
                                 "opening_axis_cad_m", 2)) != 2):
            _contract_error(
                "global_opening_host_proof_invalid",
                "global topology opening host proof is incomplete",
            )

    if representation == "frame_geometry_opening_host":
        proof = result.get("frame_geometry_opening_evidence")
        thresholds = proof.get("thresholds") if isinstance(proof, Mapping) else {}
        signed_offsets = proof.get("signed_wall_face_offsets_m") \
            if isinstance(proof, Mapping) else []
        endpoint_support = proof.get("wall_endpoint_support_distance_m") \
            if isinstance(proof, Mapping) else []
        mask_endpoint_distance = proof.get("wall_mask_endpoint_distance_m") \
            if isinstance(proof, Mapping) else []
        method = str(proof.get("method") or "") if isinstance(proof, Mapping) else ""
        common_invalid = (not isinstance(proof, Mapping)
                or str(proof.get("kind") or "") != "window"
                or not str(proof.get("candidate_id") or "").strip()
                or len(_polyline(proof.get("opening_axis_cad_m"),
                                  "frame_geometry_opening_axis_cad_m", 2)) != 2
                or not isinstance(signed_offsets, list)
                or not any(float(value) < -.02 for value in signed_offsets)
                or not any(float(value) > .02 for value in signed_offsets)
                or max(float(value) for value in signed_offsets)
                - min(float(value) for value in signed_offsets)
                < .06 - 1e-9
                or not isinstance(thresholds, Mapping)
                or review_status != "confirmed")
        regular_invalid = (method == "cad_window_frame_measured_host_v1" and (
                len(set(str(item) for item in
                        proof.get("opening_source_handles") or [] if str(item))) < 4
                or int(proof.get("long_rail_count") or 0) < 2
                or int(proof.get("cross_member_count") or 0) < 2
                or _number(proof.get("interior_wall_overlap_ratio"),
                           "interior_wall_overlap_ratio") < .90 - 1e-9
                or not isinstance(endpoint_support, list)
                or len(endpoint_support) != 2
                or max(_number(value, "wall_endpoint_support_distance_m")
                       for value in endpoint_support) > .12 + 1e-9
                or not isinstance(mask_endpoint_distance, list)
                or len(mask_endpoint_distance) != 2
                or max(_number(value, "wall_mask_endpoint_distance_m")
                       for value in mask_endpoint_distance) > .15 + 1e-9))
        sparse_mask_distances = (proof.get(
            "canonical_wall_mask_endpoint_distance_m")
            if isinstance(proof, Mapping) else [])
        sparse_invalid = (method == "cad_sparse_window_frame_wall_face_host_v1" and (
                len(set(str(item) for item in
                        proof.get("opening_source_handles") or [] if str(item))) < 2
                or not 2 <= int(proof.get("source_row_count") or 0) <= 3
                or int(proof.get("negative_wall_face_support_count") or 0) < 2
                or int(proof.get("positive_wall_face_support_count") or 0) < 2
                or int(proof.get("long_rail_count") or 0) < 2
                or _number(proof.get("interior_wall_overlap_ratio"),
                           "interior_wall_overlap_ratio") > .20 + 1e-9
                or not .06 <= _number(proof.get("supported_wall_face_span_m"),
                                      "supported_wall_face_span_m") <= .60
                or abs(_number(proof.get("wall_band_midpoint_offset_m"),
                               "wall_band_midpoint_offset_m")) > .08 + 1e-9
                or not isinstance(endpoint_support, list)
                or len(endpoint_support) != 2
                or max(_number(value, "wall_endpoint_support_distance_m")
                       for value in endpoint_support) > .09 + 1e-9
                or not isinstance(sparse_mask_distances, list)
                or len(sparse_mask_distances) != 2
                or max(_number(value,
                               "canonical_wall_mask_endpoint_distance_m")
                       for value in sparse_mask_distances) > .15 + 1e-9))
        root_mask_distances = (proof.get(
            "canonical_wall_mask_endpoint_distance_m")
            if isinstance(proof, Mapping) else [])
        root_face_span = (_number(proof.get("supported_wall_face_span_m"),
                                  "supported_wall_face_span_m")
                          if isinstance(proof, Mapping) and method ==
                          "cad_root_window_frame_wall_face_host_v1" else 0.0)
        root_frame_short_span = (_number(proof.get("frame_short_span_m"),
                                         "frame_short_span_m")
                                 if isinstance(proof, Mapping) and method ==
                                 "cad_root_window_frame_wall_face_host_v1" else 0.0)
        root_invalid = (method == "cad_root_window_frame_wall_face_host_v1" and (
                len(set(str(item) for item in
                        proof.get("opening_source_handles") or [] if str(item))) != 1
                or not 4 <= int(proof.get("source_row_count") or 0) <= 64
                or int(proof.get("negative_wall_face_support_count") or 0) < 2
                or int(proof.get("positive_wall_face_support_count") or 0) < 2
                or int(proof.get("long_rail_count") or 0) < 2
                or int(proof.get("cross_member_count") or 0) < 2
                or _number(proof.get("interior_wall_overlap_ratio"),
                           "interior_wall_overlap_ratio") > .20 + 1e-9
                or not .06 <= root_face_span <= .60
                or not .06 <= root_frame_short_span <= .60
                or abs(root_frame_short_span - root_face_span) > .03 + 1e-9
                or abs(_number(proof.get("wall_band_midpoint_offset_m"),
                               "wall_band_midpoint_offset_m")) > .08 + 1e-9
                or abs(_number(result.get("thickness_m"), "thickness_m")
                       - root_face_span) > 1e-8
                or not isinstance(endpoint_support, list)
                or len(endpoint_support) != 2
                or max(_number(value, "wall_endpoint_support_distance_m")
                       for value in endpoint_support) > .12 + 1e-9
                or not isinstance(root_mask_distances, list)
                or len(root_mask_distances) != 2
                or max(_number(value,
                               "canonical_wall_mask_endpoint_distance_m")
                       for value in root_mask_distances) > .15 + 1e-9))
        if (common_invalid
                or method not in {"cad_window_frame_measured_host_v1",
                                  "cad_sparse_window_frame_wall_face_host_v1",
                                  "cad_root_window_frame_wall_face_host_v1"}
                or regular_invalid or sparse_invalid or root_invalid):
            _contract_error(
                "frame_geometry_opening_host_proof_invalid",
                "window-frame host requires measured opposing rails, two jambs and nearby wall-mask endpoints",
            )

    if representation == "door_swing_geometry_opening_host":
        proof = result.get("door_swing_geometry_opening_evidence")
        endpoint_distances = proof.get("wall_mask_endpoint_distance_m") \
            if isinstance(proof, Mapping) else []
        jamb_widths = proof.get("jamb_cross_section_width_m") \
            if isinstance(proof, Mapping) else []
        reason_codes = {str(value) for value in
                        (proof.get("source_reason_codes") or [])} \
            if isinstance(proof, Mapping) else set()
        method = str(proof.get("method") or "") \
            if isinstance(proof, Mapping) else ""
        arc_methods = {
            "cad_door_swing_unique_jamb_host_v1",
            "cad_door_swing_unique_terminal_wall_support_v1",
            "cad_door_swing_wall_pair_transverse_jamb_host_v1",
        }
        leaf_methods = {
            "cad_door_leaf_unique_jamb_host_v1",
            "cad_door_leaf_unique_terminal_wall_support_v1",
            "cad_door_leaf_unique_source_face_jamb_host_v1",
            "cad_door_leaf_unique_wall_gap_axis_host_v1",
        }
        source_face_methods = {
            "cad_door_leaf_unique_source_face_jamb_host_v1",
        }
        unique_gap_methods = {
            "cad_door_leaf_unique_wall_gap_axis_host_v1",
        }
        terminal_methods = {
            "cad_door_swing_unique_terminal_wall_support_v1",
            "cad_door_leaf_unique_terminal_wall_support_v1",
        }
        projected_arc_methods = {
            "cad_door_swing_wall_pair_transverse_jamb_host_v1",
        }
        leaf_source = proof.get("parallel_leaf_without_arc_evidence") \
            if isinstance(proof, Mapping) and isinstance(proof.get(
                "parallel_leaf_without_arc_evidence"), Mapping) else {}
        leaf_source_invalid = bool(method in leaf_methods and (
            not isinstance(leaf_source, Mapping)
            or leaf_source.get("method")
            != "cad_parallel_door_leaf_without_arc_v1"
            or not 3 <= int(leaf_source.get("source_row_count") or 0) <= 5
            or int(leaf_source.get("parallel_rail_count") or 0)
            != int(leaf_source.get("source_row_count") or 0)
            or len(set(str(value) for value in
                       proof.get("opening_source_handles") or [] if str(value))) < 3
            or _number(leaf_source.get("leaf_angle_spread_deg"),
                       "door_leaf.leaf_angle_spread_deg") > 1.0 + 1e-9
            or _number(leaf_source.get("leaf_length_spread_m"),
                       "door_leaf.leaf_length_spread_m")
            > max(.02, _number(proof.get("opening_width_m"),
                               "opening_width_m") * .02) + 1e-9
            or _number(leaf_source.get("hinge_endpoint_cluster_radius_m"),
                       "door_leaf.hinge_endpoint_cluster_radius_m") > .08 + 1e-9
            or _number(leaf_source.get("free_endpoint_cluster_radius_m"),
                       "door_leaf.free_endpoint_cluster_radius_m") > .08 + 1e-9
            or _number(leaf_source.get("hinge_wall_distance_m"),
                       "door_leaf.hinge_wall_distance_m") > .12 + 1e-9
            or _number(leaf_source.get("free_endpoint_wall_distance_m"),
                       "door_leaf.free_endpoint_wall_distance_m") < .20 - 1e-9
            or not .06 <= _number(leaf_source.get(
                "selected_wall_face_separation_m"),
                "door_leaf.selected_wall_face_separation_m") <= .60
            or len(leaf_source.get("axis_candidates") or []) < 1
        ))
        source_geometry_invalid = bool(
            (method in arc_methods and {
                "circular_swing_arc", "radial_door_leaf",
                "wall_network_supported"}.difference(reason_codes))
            or (method in leaf_methods and (
                {"parallel_door_leaf_rails", "hinge_endpoint_wall_supported",
                 "swing_leaf_without_arc",
                 "wall_network_supported"}.difference(reason_codes)
                or leaf_source_invalid)))
        terminal_supports = proof.get("terminal_wall_supports") \
            if isinstance(proof, Mapping) else []
        source_face_supports = proof.get("source_face_jamb_supports") \
            if isinstance(proof, Mapping) else []
        source_face_support_valid = bool(
            method in source_face_methods
            and isinstance(source_face_supports, list)
            and len(source_face_supports) == 2
            and {int(row.get("endpoint_index", -1)) for row in source_face_supports
                 if isinstance(row, Mapping)} == {0, 1}
            and all(
                isinstance(row, Mapping)
                and row.get("method")
                == "cad_source_wall_face_pair_at_door_jamb_v1"
                and len(set(str(value) for value in
                            row.get("wall_face_source_handles") or [] if str(value))) >= 2
                and .06 <= _number(row.get("face_separation_m"),
                                   "door_source_face.face_separation_m") <= .60
                and abs(_number(row.get("wall_band_midpoint_offset_m"),
                                "door_source_face.wall_band_midpoint_offset_m"))
                <= .08 + 1e-9
                and len(row.get("wall_face_endpoint_distance_m") or []) == 2
                and max(_number(value,
                                "door_source_face.wall_face_endpoint_distance_m")
                        for value in row.get(
                            "wall_face_endpoint_distance_m") or [])
                <= _number(row.get("face_separation_m"),
                           "door_source_face.face_separation_m") / 2 + .12 + 1e-9
                and len(row.get("wall_face_outward_extension_m") or []) == 2
                and min(_number(value,
                                "door_source_face.wall_face_outward_extension_m")
                        for value in row.get(
                            "wall_face_outward_extension_m") or []) >= .05 - 1e-9
                and len(row.get("wall_face_axis_angle_difference_deg") or []) == 2
                and max(_number(value,
                                "door_source_face.wall_face_axis_angle_difference_deg")
                        for value in row.get(
                            "wall_face_axis_angle_difference_deg") or []) <= 1.0 + 1e-9
                for row in source_face_supports)
            and max(_number(row.get("face_separation_m"),
                            "door_source_face.face_separation_m")
                    for row in source_face_supports if isinstance(row, Mapping))
            - min(_number(row.get("face_separation_m"),
                          "door_source_face.face_separation_m")
                  for row in source_face_supports if isinstance(row, Mapping))
            <= .04 + 1e-9)
        unique_gap = proof.get("unique_wall_gap_axis_evidence") \
            if isinstance(proof, Mapping) else {}
        unique_gap_valid = bool(
            method in unique_gap_methods
            and isinstance(unique_gap, Mapping)
            and unique_gap.get("method")
            == "cad_parallel_leaf_unique_wall_gap_axis_v1"
            and int(unique_gap.get("axis_candidate_count") or 0) >= 1
            and len(set(str(value) for value in unique_gap.get(
                "wall_face_source_handles") or [] if str(value))) >= 2
            and .06 <= _number(unique_gap.get("wall_face_separation_m"),
                               "door_unique_gap.wall_face_separation_m") <= .60
            and len(unique_gap.get(
                "source_endpoint_wall_support_distance_m") or []) == 2
            and max(_number(value,
                            "door_unique_gap.source_endpoint_wall_support_distance_m")
                    for value in unique_gap.get(
                        "source_endpoint_wall_support_distance_m") or []) <= .151 + 1e-9
            and len(unique_gap.get("wall_mask_endpoint_distance_m") or []) == 2
            and max(_number(value,
                            "door_unique_gap.wall_mask_endpoint_distance_m")
                    for value in unique_gap.get(
                        "wall_mask_endpoint_distance_m") or []) <= .10 + 1e-9
            and _number(unique_gap.get("axis_midpoint_wall_clearance_m"),
                        "door_unique_gap.axis_midpoint_wall_clearance_m")
            >= max(.06, _number(unique_gap.get("wall_face_separation_m"),
                                "door_unique_gap.wall_face_separation_m") / 2
                   - .03) - 1e-9
            and _number(unique_gap.get("axis_midpoint_wall_clearance_m"),
                        "door_unique_gap.axis_midpoint_wall_clearance_m")
            >= _number(unique_gap.get(
                "minimum_axis_midpoint_wall_clearance_m"),
                "door_unique_gap.minimum_axis_midpoint_wall_clearance_m") - 1e-9
            and (int(unique_gap.get("axis_candidate_count") or 0) == 1
                 or _number(unique_gap.get(
                     "axis_clearance_selection_margin_m"),
                     "door_unique_gap.axis_clearance_selection_margin_m")
                 >= .10 - 1e-9)
            and abs(_number(result.get("thickness_m"), "thickness_m")
                    - _number(unique_gap.get("wall_face_separation_m"),
                              "door_unique_gap.wall_face_separation_m")) <= 1e-8)
        projected_arc = proof.get("projected_arc_transverse_jamb_evidence") \
            if isinstance(proof, Mapping) else {}
        projected_arc_valid = bool(
            method in projected_arc_methods
            and isinstance(projected_arc, Mapping)
            and projected_arc.get("method")
            == "cad_arc_leaf_wall_pair_transverse_jamb_projection_v1"
            and len({int(value) for value in projected_arc.get(
                "wall_face_entity_indexes") or []}) == 2
            and int(projected_arc.get("transverse_jamb_entity_index") or -1)
            not in {int(value) for value in projected_arc.get(
                "wall_face_entity_indexes") or []}
            and len(set(str(value) for value in projected_arc.get(
                "wall_face_source_handles") or [] if str(value))) >= 3
            and .06 <= _number(projected_arc.get("wall_face_separation_m"),
                               "door_projected_arc.wall_face_separation_m") <= .60
            and _number(projected_arc.get("hinge_to_wall_centerline_offset_m"),
                        "door_projected_arc.hinge_to_wall_centerline_offset_m")
            <= .20 + 1e-9
            and _number(projected_arc.get("transverse_jamb_snap_distance_m"),
                        "door_projected_arc.transverse_jamb_snap_distance_m")
            <= .20 + 1e-9
            and _number(projected_arc.get(
                "transverse_jamb_angle_difference_deg"),
                "door_projected_arc.transverse_jamb_angle_difference_deg")
            >= 88.5 - 1e-9
            and len(_polyline(projected_arc.get("axis_segment_cad_m"),
                              "door_projected_arc.axis_segment_cad_m", 2)) == 2
            and abs(_number(result.get("thickness_m"), "thickness_m")
                    - _number(projected_arc.get("wall_face_separation_m"),
                              "door_projected_arc.wall_face_separation_m")) <= 1e-8)
        terminal_support_valid = bool(
            method in terminal_methods
            and isinstance(terminal_supports, list)
            and len(terminal_supports) == 2
            and {int(row.get("endpoint_index", -1)) for row in terminal_supports
                 if isinstance(row, Mapping)} == {0, 1}
            and len({str(row.get("wall_assembly_id") or "")
                     for row in terminal_supports if isinstance(row, Mapping)}) == 2
            and any(str(row.get("orientation") or "") == "collinear"
                    for row in terminal_supports if isinstance(row, Mapping))
            and all(str(row.get("orientation") or "")
                    in {"collinear", "transverse_terminal"}
                    and _number(row.get("endpoint_footprint_distance_m"),
                                "door_terminal.endpoint_footprint_distance_m")
                    <= .04 + 1e-9
                    and _number(row.get("endpoint_axis_terminal_distance_m"),
                                "door_terminal.endpoint_axis_terminal_distance_m")
                    <= _number(row.get("endpoint_axis_terminal_distance_limit_m"),
                               "door_terminal.endpoint_axis_terminal_distance_limit_m")
                    + 1e-9
                    for row in terminal_supports if isinstance(row, Mapping))
            and max(_number(row.get("wall_thickness_m"),
                            "door_terminal.wall_thickness_m")
                    for row in terminal_supports if isinstance(row, Mapping))
            - min(_number(row.get("wall_thickness_m"),
                          "door_terminal.wall_thickness_m")
                  for row in terminal_supports if isinstance(row, Mapping))
            <= .02 + 1e-9)
        if (not isinstance(proof, Mapping)
                or method not in {
                    "cad_door_swing_unique_jamb_host_v1",
                    "cad_door_swing_unique_terminal_wall_support_v1",
                    "cad_door_swing_wall_pair_transverse_jamb_host_v1",
                    "cad_door_leaf_unique_jamb_host_v1",
                    "cad_door_leaf_unique_terminal_wall_support_v1",
                    "cad_door_leaf_unique_source_face_jamb_host_v1",
                    "cad_door_leaf_unique_wall_gap_axis_host_v1",
                }
                or str(proof.get("kind") or "") != "door"
                or not str(proof.get("candidate_id") or "").strip()
                or not [str(value) for value in
                        proof.get("opening_source_handles") or [] if str(value)]
                or source_geometry_invalid
                or len(_polyline(proof.get("opening_axis_cad_m"),
                                 "door_swing_opening_axis_cad_m", 2)) != 2
                or not isinstance(endpoint_distances, list)
                or len(endpoint_distances) != 2
                or max(_number(value, "wall_mask_endpoint_distance_m")
                       for value in endpoint_distances)
                > (.04 if method in terminal_methods else
                   .05 if method in projected_arc_methods else
                   .25 if method in source_face_methods else
                   .10 if method in unique_gap_methods else .15)
                + 1e-9
                or not isinstance(jamb_widths, list) or len(jamb_widths) != 2
                or any(not .06 <= _number(value, "jamb_cross_section_width_m") <= .60
                       for value in jamb_widths)
                or max(float(value) for value in jamb_widths)
                - min(float(value) for value in jamb_widths) > .04 + 1e-9
                or (method in terminal_methods
                    and not terminal_support_valid)
                or (method in source_face_methods
                    and not source_face_support_valid)
                or (method in unique_gap_methods
                    and not unique_gap_valid)
                or (method in projected_arc_methods
                    and not projected_arc_valid)
                or int(proof.get("viable_axis_count") or 0) < 1
                or review_status != "confirmed"):
            _contract_error(
                "door_swing_geometry_opening_host_proof_invalid",
                "door-swing host requires a unique axis with two matching source-backed jambs",
            )

    if representation == "repeated_window_frame_opening_host":
        proof = result.get("repeated_window_frame_opening_evidence")
        endpoint_mask = proof.get("wall_mask_endpoint_distance_m") \
            if isinstance(proof, Mapping) else []
        endpoint_support = proof.get("wall_endpoint_support_distance_m") \
            if isinstance(proof, Mapping) else []
        reference_thickness = (_number(
            proof.get("reference_wall_thickness_m"),
            "repeated_window.reference_wall_thickness_m")
            if isinstance(proof, Mapping) else 0.0)
        if (not isinstance(proof, Mapping)
                or str(proof.get("method") or "")
                != "cad_repeated_collinear_window_frame_host_v1"
                or str(proof.get("kind") or "") != "window"
                or not str(proof.get("candidate_id") or "").strip()
                or not str(proof.get("reference_candidate_id") or "").strip()
                or not str(proof.get("reference_wall_assembly_id") or "").strip()
                or len(set(str(value) for value in
                            proof.get("opening_source_handles") or [] if str(value))) < 4
                or len(set(str(value) for value in
                            proof.get("reference_opening_source_handles") or []
                            if str(value))) < 4
                or len(_polyline(proof.get("opening_axis_cad_m"),
                                 "repeated_window.opening_axis_cad_m", 2)) != 2
                or len(_polyline(proof.get("reference_axis_cad_m"),
                                 "repeated_window.reference_axis_cad_m", 2)) != 2
                or int(proof.get("long_rail_count") or 0) < 3
                or int(proof.get("cross_member_count") or 0) < 2
                or not isinstance(endpoint_mask, list) or len(endpoint_mask) != 2
                or max(_number(value, "repeated_window.wall_mask_endpoint_distance_m")
                       for value in endpoint_mask) > .05 + 1e-9
                or not isinstance(endpoint_support, list)
                or len(endpoint_support) != 2
                or max(_number(value, "repeated_window.wall_endpoint_support_distance_m")
                       for value in endpoint_support) > reference_thickness + 1e-9
                or not .06 <= reference_thickness <= .60
                or abs(_number(result.get("thickness_m"), "thickness_m")
                       - reference_thickness) > 1e-6
                or _number(proof.get("axis_angle_difference_deg"),
                           "repeated_window.axis_angle_difference_deg") > 1.0 + 1e-9
                or _number(proof.get("axis_transverse_offset_m"),
                           "repeated_window.axis_transverse_offset_m") > .005 + 1e-9
                or _number(proof.get("opening_width_difference_m"),
                           "repeated_window.opening_width_difference_m") > .01 + 1e-9
                or _number(proof.get("frame_rail_separation_difference_m"),
                           "repeated_window.frame_rail_separation_difference_m")
                > .01 + 1e-9
                or not .10 - 1e-9 <= _number(
                    proof.get("axis_interval_gap_m"),
                    "repeated_window.axis_interval_gap_m") <= 2.0 + 1e-9
                or review_status != "confirmed"):
            _contract_error(
                "repeated_window_frame_opening_host_proof_invalid",
                "repeated window host requires one unique collinear measured reference window",
            )

    if representation == "opening_host_stitch":
        proof = result.get("opening_host_evidence")
        if not isinstance(proof, Mapping):
            _contract_error(
                "opening_host_stitch_proof_missing",
                "opening host stitch requires source-backed gap evidence",
            )
        source_wall_ids = [str(item).strip() for item in
                           proof.get("source_wall_assembly_ids") or [] if str(item).strip()]
        candidate_id = str(proof.get("candidate_id") or "").strip()
        opening_axis = _polyline(proof.get("opening_axis_cad_m"),
                                 "opening_host_evidence.opening_axis_cad_m", 2)
        gap_interval = proof.get("gap_interval_m") or []
        jamb_offsets = proof.get("jamb_offsets_m") or []
        try:
            gap_start, gap_end = float(gap_interval[0]), float(gap_interval[1])
            jamb_first, jamb_second = float(jamb_offsets[0]), float(jamb_offsets[1])
        except (TypeError, ValueError, IndexError):
            _contract_error(
                "opening_host_stitch_proof_invalid",
                "opening host stitch gap and jamb evidence must be finite pairs",
            )
        max_jamb = _number(proof.get("max_jamb_offset_m"), "max_jamb_offset_m")
        max_gap_delta = _number(
            proof.get("max_gap_width_delta_m"), "max_gap_width_delta_m")
        opening_width = sum(math.dist(first, second)
                            for first, second in zip(opening_axis, opening_axis[1:]))
        gap_width = abs(gap_end - gap_start)
        if (len(set(source_wall_ids)) != 2 or not candidate_id
                or not all(math.isfinite(value) and value >= 0
                           for value in (jamb_first, jamb_second, max_jamb, max_gap_delta))
                or max_jamb > .08 + 1e-9
                or max(jamb_first, jamb_second) > max_jamb + 1e-9
                or max_gap_delta > .16 + 1e-9
                or abs(gap_width - opening_width) > max_gap_delta + 1e-9
                or review_status != "confirmed"):
            _contract_error(
                "opening_host_stitch_proof_invalid",
                "opening host stitch requires one confirmed, bounded, two-wall gap proof",
            )

    if representation == "terminal_open_connection_host":
        proof = result.get("terminal_open_connection_evidence")
        thresholds = proof.get("thresholds") if isinstance(proof, Mapping) else {}
        source_wall_ids = [str(item).strip() for item in
                           proof.get("source_wall_assembly_ids") or []
                           if str(item).strip()] if isinstance(proof, Mapping) else []
        source_handles = [str(item).strip() for item in
                          proof.get("source_handles") or []
                          if str(item).strip()] if isinstance(proof, Mapping) else []
        thickness_samples = proof.get("wall_thickness_samples_m") \
            if isinstance(proof, Mapping) else []
        closed_anchor_ids = proof.get("closed_space_semantic_anchor_ids") \
            if isinstance(proof, Mapping) else []
        if (not isinstance(proof, Mapping)
                or str(proof.get("method") or "")
                != "cad_labeled_terminal_open_connection_v1"
                or not str(proof.get("candidate_id") or "").strip()
                or len(set(source_wall_ids)) != 2
                or len(set(source_handles)) < 2
                or len(_polyline(proof.get("opening_axis_cad_m"),
                                 "terminal_opening.opening_axis_cad_m", 2)) != 2
                or not .35 - 1e-9 <= _number(
                    proof.get("clear_gap_width_m"),
                    "terminal_opening.clear_gap_width_m") <= 1.50 + 1e-9
                or not .35 - 1e-9 <= _number(
                    proof.get("terminal_axis_extension_m"),
                    "terminal_opening.terminal_axis_extension_m") <= 1.80 + 1e-9
                or _number(proof.get("terminal_transverse_angle_deg"),
                           "terminal_opening.terminal_transverse_angle_deg")
                < 89.0 - 1e-9
                or not isinstance(thickness_samples, list)
                or len(thickness_samples) != 2
                or any(not .06 <= _number(
                    value, "terminal_opening.wall_thickness_samples_m") <= .60
                       for value in thickness_samples)
                or _number(proof.get("wall_thickness_spread_m"),
                           "terminal_opening.wall_thickness_spread_m")
                > .04 + 1e-9
                or _number(proof.get("intermediate_wall_coverage_m"),
                           "terminal_opening.intermediate_wall_coverage_m")
                > .01 + 1e-9
                or int(proof.get("unique_transverse_support_count") or 0) != 1
                or str(proof.get("storage_anchor_profile") or "") != "storage"
                or str(proof.get("kitchen_anchor_profile") or "") != "kitchen"
                or not str(proof.get("storage_anchor_id") or "").strip()
                or not str(proof.get("kitchen_anchor_id") or "").strip()
                or int(proof.get("topology_space_count_delta") or 0) != 1
                or len(closed_anchor_ids) != 1
                or str(closed_anchor_ids[0])
                != str(proof.get("storage_anchor_id") or "")
                or _number(proof.get("closed_storage_space_area_m2"),
                           "terminal_opening.closed_storage_space_area_m2") < .50
                or not isinstance(thresholds, Mapping)
                or review_status != "confirmed"):
            _contract_error(
                "terminal_open_connection_host_proof_invalid",
                "terminal open connection requires a unique measured gap and one topology-proved storage face",
            )

    if representation == "collinear_face_continuation":
        proof = result.get("collinear_face_continuation_evidence")
        supports = proof.get("terminal_supports") \
            if isinstance(proof, Mapping) else []
        if (not isinstance(proof, Mapping)
                or str(proof.get("method") or "")
                != "bounded_staggered_paired_faces_v1"
                or not str(proof.get("source_wall_assembly_id") or "").strip()
                or not str(proof.get("continuation_face_handle") or "").strip()
                or not str(proof.get("mate_face_handle") or "").strip()
                or len(set(handles)) < 2
                or abs(_number(proof.get("face_separation_m"),
                               "face_separation_m")
                       - _number(proof.get("wall_thickness_m"),
                                 "wall_thickness_m")) > .005 + 1e-9
                or _number(proof.get("continuation_face_gap_m"),
                           "continuation_face_gap_m")
                > _number(proof.get("wall_thickness_m"),
                          "wall_thickness_m") + .02 + 1e-9
                or _number(proof.get("continuation_face_collinear_distance_m"),
                           "continuation_face_collinear_distance_m") > .005 + 1e-9
                or _number(proof.get("projected_overlap_length_m"),
                           "projected_overlap_length_m") < .30 - 1e-9
                or _number(proof.get("projected_overlap_ratio"),
                           "projected_overlap_ratio") < .60 - 1e-9
                or _number(proof.get("occupied_overlap_length_m"),
                           "occupied_overlap_length_m") > .02 + 1e-9
                or not isinstance(supports, list) or len(supports) != 2
                or len({str(row.get("wall_assembly_id") or "")
                        for row in supports if isinstance(row, Mapping)}) != 2
                or any(
                    not isinstance(row, Mapping)
                    or _number(row.get("axis_angle_difference_deg"),
                               "axis_angle_difference_deg") < 89.0 - 1e-9
                    or _number(row.get("axis_extension_m"),
                               "axis_extension_m")
                    > _number(row.get("axis_extension_limit_m"),
                              "axis_extension_limit_m") + 1e-9
                    or _number(row.get("support_axis_extension_m"),
                               "support_axis_extension_m")
                    > _number(row.get("support_axis_extension_limit_m"),
                              "support_axis_extension_limit_m") + 1e-9
                    for row in supports)
                or review_status != "confirmed"):
            _contract_error(
                "collinear_face_continuation_proof_invalid",
                "staggered wall faces require one measured pair and two unique transverse terminals",
            )

    if representation == "window_frame_host_extension":
        proof = result.get("window_frame_host_evidence")
        if not isinstance(proof, Mapping):
            _contract_error(
                "window_frame_host_proof_missing",
                "window frame host extension requires source-backed rail and wall-face evidence",
            )
        source_wall_id = str(proof.get("source_wall_assembly_id") or "").strip()
        source_face_handle = str(proof.get("source_face_handle") or "").strip()
        candidate_id = str(proof.get("candidate_id") or "").strip()
        opening_handles = [str(item).strip() for item in
                           proof.get("opening_source_handles") or [] if str(item).strip()]
        opening_axis = _polyline(
            proof.get("opening_axis_cad_m"),
            "window_frame_host_evidence.opening_axis_cad_m", 2)
        opening_width = sum(math.dist(first, second)
                            for first, second in zip(opening_axis, opening_axis[1:]))
        frame_separation = _number(
            proof.get("frame_rail_separation_m"), "frame_rail_separation_m")
        measured_wall_thickness = _number(
            proof.get("wall_thickness_m"), "wall_thickness_m")
        opening_overlap = _number(proof.get("opening_overlap_m"), "opening_overlap_m")
        max_axis_offset = _number(proof.get("max_axis_offset_m"), "max_axis_offset_m")
        max_thickness_delta = _number(
            proof.get("max_thickness_delta_m"), "max_thickness_delta_m")
        max_source_jamb = _number(
            proof.get("max_source_face_jamb_m"), "max_source_face_jamb_m")
        source_face_interval = proof.get("source_face_interval_m") or []
        host_interval = proof.get("host_interval_m") or []
        try:
            source_start, source_end = sorted(
                (float(source_face_interval[0]), float(source_face_interval[1])))
            host_start, host_end = sorted((float(host_interval[0]), float(host_interval[1])))
        except (TypeError, ValueError, IndexError):
            _contract_error(
                "window_frame_host_proof_invalid",
                "window frame host source-face and host intervals must be finite pairs",
            )
        if (not source_wall_id or not source_face_handle or not candidate_id
                or len(set(opening_handles)) < 4
                or not .40 <= opening_width <= 3.00
                or not .06 <= frame_separation <= .60
                or not .06 <= measured_wall_thickness <= .60
                or max_axis_offset > .005 + 1e-9
                or max_thickness_delta > .005 + 1e-9
                or max_source_jamb > .08 + 1e-9
                or abs(frame_separation - measured_wall_thickness)
                > max_thickness_delta + 1e-9
                or opening_overlap < .40 - 1e-9
                or source_end - source_start < opening_width - 2 * max_source_jamb - 1e-9
                or host_start > source_start + 1e-9
                or host_end < source_end - 1e-9
                or review_status != "confirmed"):
            _contract_error(
                "window_frame_host_proof_invalid",
                "window frame host needs one confirmed wall, matching rails, and a covering source face",
            )

    footprint = _polyline(result.get("footprint_polygon"), "footprint_polygon", 3)
    if footprint[0] != footprint[-1]:
        footprint.append(copy.deepcopy(footprint[0]))
    area = _polygon_area(footprint)
    if area <= 1e-8:
        _contract_error("wall_footprint_degenerate", "wall footprint must have positive area")
    if _polygon_self_intersects(footprint):
        _contract_error("wall_footprint_self_intersection", "wall footprint must be a simple polygon")
    centerline = _polyline(result.get("centerline"), "centerline", 2)
    if sum(math.dist(start, end) for start, end in zip(centerline, centerline[1:])) <= 1e-8:
        _contract_error("wall_centerline_degenerate", "wall centerline must have positive length")
    thickness = _number(result.get("thickness_m"), "thickness_m")
    height = _number(result.get("height_m"), "height_m")
    if thickness <= 0 or height <= 0:
        _contract_error("wall_dimensions_invalid", "wall thickness and height must be positive")
    if representation == "paired_faces" and not 0.06 <= thickness <= 0.60:
        _contract_error("paired_wall_thickness_invalid", "paired wall separation must be between 0.06m and 0.60m")
    if representation in {
            "opening_host_stitch", "window_frame_host_extension",
            "frame_geometry_opening_host", "door_swing_geometry_opening_host",
            "repeated_window_frame_opening_host", "terminal_open_connection_host",
    } and not 0.06 <= thickness <= 0.60:
        _contract_error(
            "opening_host_thickness_invalid",
            "opening host thickness must retain a measured residential wall range",
        )
    if representation == "collinear_face_continuation" and not 0.06 <= thickness <= 0.60:
        _contract_error(
            "collinear_face_continuation_thickness_invalid",
            "staggered paired wall thickness must retain a measured residential wall range",
        )
    thickness_source = str(result.get("thickness_source") or "").strip()
    height_source = str(result.get("height_source") or "").strip()
    if not thickness_source or not height_source:
        _contract_error("wall_dimension_source_missing", "wall dimensions must identify their evidence source")
    if representation == "centerline" and thickness_source == "project_default_assumption" and review_status != "confirmed":
        _contract_error("centerline_thickness_unconfirmed", "default centerline thickness must be explicitly confirmed")
    if representation == "opening_host_stitch" and (
            thickness_source != "matched_adjacent_cad_wall_assemblies"
            or height_source != "matched_adjacent_cad_wall_assemblies"):
        _contract_error(
            "opening_host_stitch_dimension_source_invalid",
            "opening host stitch dimensions must come from both matched source wall assemblies",
        )
    if representation == "terminal_open_connection_host" and (
            thickness_source
            != "matched_terminal_and_transverse_cad_wall_assemblies"
            or height_source
            != "matched_terminal_and_transverse_cad_wall_assemblies"):
        _contract_error(
            "terminal_open_connection_dimension_source_invalid",
            "terminal open connection dimensions must come from the matched terminal and transverse walls",
        )
    if representation == "window_frame_host_extension" and (
            thickness_source != "window_frame_rail_spacing_and_source_wall_assembly"
            or height_source != "matched_source_wall_assembly"):
        _contract_error(
            "window_frame_host_dimension_source_invalid",
            "window frame host dimensions must come from the matched frame and source wall",
        )
    if (representation == "frame_geometry_opening_host"
            and thickness_source not in {
                "cad_window_frame_rail_spacing",
                "cad_sparse_frame_supported_wall_face_span",
                "cad_root_frame_supported_wall_face_span",
            }):
        _contract_error(
            "frame_geometry_opening_host_dimension_source_invalid",
            "frame-geometry opening host thickness must come from measured frame or supported wall faces",
        )
    if (representation == "door_swing_geometry_opening_host"
            and thickness_source not in {
                "cad_door_jamb_global_wall_cross_sections",
                "cad_door_terminal_wall_support_thickness",
                "cad_door_source_face_pair_jamb_thickness",
                "cad_door_leaf_hinge_wall_face_span",
                "cad_arc_projected_wall_face_pair_thickness",
            }):
        _contract_error(
            "door_swing_geometry_opening_host_dimension_source_invalid",
            "door-swing opening host thickness must come from both measured wall jambs",
        )
    if (representation == "repeated_window_frame_opening_host" and (
            thickness_source != "matched_repeated_window_wall_assembly"
            or height_source != "matched_reference_window_wall_assembly")):
        _contract_error(
            "repeated_window_frame_opening_host_dimension_source_invalid",
            "repeated window host dimensions must come from its unique measured reference wall",
        )
    if (representation == "collinear_face_continuation"
            and thickness_source != "matched_staggered_cad_wall_faces"):
        _contract_error(
            "collinear_face_continuation_dimension_source_invalid",
            "staggered wall continuation thickness must come from its matched CAD faces",
        )
    result.update(
        id=wall_id, source_representation=representation, review_status=review_status,
        source_entity_handles=handles, footprint_polygon=footprint,
        footprint_area_m2=area, centerline=centerline, thickness_m=thickness,
        height_m=height, thickness_source=thickness_source, height_source=height_source,
    )
    return canonicalize(result)


class WallAssembly(dict):
    """Validated dictionary representation of WallAssembly v1."""

    def __init__(self, value: Mapping[str, Any] | None = None, **kwargs: Any):
        super().__init__(validate_wall_assembly({**dict(value or {}), **kwargs}))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WallAssembly":
        return cls(value)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))


def _validate_mesh_parts(
    parts: Any, field: str, vertex_count: int, *, indices_required: bool = True,
) -> list[dict[str, Any]]:
    if not isinstance(parts, list):
        _contract_error("manifest_parts_invalid", f"{field} must be an array", field=field)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(parts):
        if not isinstance(raw, Mapping):
            _contract_error("manifest_part_invalid", f"{field}[{index}] must be an object", field=field)
        part = copy.deepcopy(dict(raw))
        part_id = str(part.get("id") or "").strip()
        if not part_id or part_id in seen:
            _contract_error("manifest_part_id_invalid", f"{field} ids must be non-empty and unique", field=field, id=part_id)
        seen.add(part_id)
        indices = part.get("indices")
        if indices is None and not indices_required:
            result.append(canonicalize(part))
            continue
        if not isinstance(indices, list) or len(indices) == 0 or len(indices) % 3:
            _contract_error("manifest_indices_invalid", f"{field}[{index}].indices must contain triangles", field=field)
        normalized_indices: list[int] = []
        for item in indices:
            if isinstance(item, bool) or int(item) != item or not 0 <= int(item) < vertex_count:
                _contract_error("manifest_vertex_reference_invalid", "triangle index is outside vertices", field=field, index=item)
            normalized_indices.append(int(item))
        part["id"] = part_id
        part["indices"] = normalized_indices
        result.append(canonicalize(part))
    return sorted(result, key=lambda part: str(part["id"]))


def validate_geometry_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate GeometryManifest v1 and verify/recompute its stable hash."""
    if not isinstance(value, Mapping):
        _contract_error("manifest_invalid", "geometry manifest must be an object")
    result = copy.deepcopy(dict(value))
    result["version"] = int(result.get("version", GEOMETRY_CONTRACT_VERSION))
    if result["version"] != GEOMETRY_CONTRACT_VERSION:
        _contract_error("manifest_version_unsupported", "unsupported geometry manifest version")
    for field in ("project_id", "model_facts_hash", "registration_hash", "geometry_kernel_version"):
        if not str(result.get(field) or "").strip():
            _contract_error("manifest_field_missing", f"{field} is required", field=field)
    result["model_revision"] = int(result.get("model_revision", 0))
    if result["model_revision"] < 1:
        _contract_error("manifest_revision_invalid", "model_revision must be positive")
    if str(result.get("units") or "meter").lower() not in {"meter", "meters", "m"}:
        _contract_error("manifest_units_invalid", "geometry manifest units must be meters")
    result["units"] = "meter"
    vertices = result.get("vertices")
    if not isinstance(vertices, list):
        _contract_error("manifest_vertices_invalid", "vertices must be an array")
    normalized_vertices = []
    for index, vertex in enumerate(vertices):
        if not isinstance(vertex, Sequence) or isinstance(vertex, (str, bytes)) or len(vertex) != 3:
            _contract_error("manifest_vertex_invalid", "each manifest vertex must be xyz", index=index)
        normalized_vertices.append([_number(item, f"vertices[{index}]") for item in vertex])
    result["vertices"] = normalized_vertices
    result["wall_parts"] = _validate_mesh_parts(result.get("wall_parts", []), "wall_parts", len(vertices))
    result["floor_parts"] = _validate_mesh_parts(result.get("floor_parts", []), "floor_parts", len(vertices))
    result["ceiling_parts"] = _validate_mesh_parts(
        result.get("ceiling_parts", []), "ceiling_parts", len(vertices))
    result["object_parts"] = _validate_mesh_parts(
        result.get("object_parts", []), "object_parts", len(vertices))
    result["opening_voids"] = _validate_mesh_parts(
        result.get("opening_voids", []), "opening_voids", len(vertices), indices_required=False,
    )
    expected_parts = sorted([
        *result["wall_parts"], *result["floor_parts"],
        *result["ceiling_parts"], *result["object_parts"],
    ], key=lambda part: str(part["id"]))
    expected_part_ids = [str(part["id"]) for part in expected_parts]
    if len(expected_part_ids) != len(set(expected_part_ids)):
        _contract_error("manifest_part_id_collision", "part ids must be unique across all geometry categories")
    if "parts" in result:
        result["parts"] = _validate_mesh_parts(result.get("parts"), "parts", len(vertices))
        if canonicalize(result["parts"]) != canonicalize(expected_parts):
            _contract_error(
                "manifest_parts_index_mismatch",
                "compatibility parts index must exactly match categorized geometry parts",
            )
    wall_targets = {
        str(value) for part in result["wall_parts"]
        for value in (part.get("entity_id"), part.get("wall_assembly_id"), part.get("wall_id"))
        if str(value or "")
    }
    strict_opening_contract = str(result.get("opening_contract") or "") == "owned-dimensions-v1"
    for index, opening in enumerate(result["opening_voids"]):
        opening_id = str(opening.get("opening_id") or opening.get("id") or "").strip()
        target = str(
            opening.get("wall_assembly_id") or opening.get("wall_id")
            or opening.get("host_ifc_id") or ""
        ).strip()
        if not opening_id:
            _contract_error("manifest_opening_id_missing", "opening void needs a stable opening id", index=index)
        if not target:
            _contract_error("manifest_opening_owner_missing", "opening void must identify its host wall", opening_id=opening_id)
        if target not in wall_targets:
            _contract_error(
                "manifest_opening_orphan", "opening void host is absent from manifest wall parts",
                opening_id=opening_id, target=target,
            )
        if strict_opening_contract and ("width_m" not in opening or "height_m" not in opening):
            _contract_error(
                "manifest_opening_dimensions_missing", "strict opening void needs width_m and height_m",
                opening_id=opening_id,
            )
        width = _number(opening.get("width_m"), f"opening_voids[{index}].width_m")
        height = _number(opening.get("height_m", 2.1), f"opening_voids[{index}].height_m")
        sill = _number(opening.get("sill_height_m", 0), f"opening_voids[{index}].sill_height_m")
        if width <= 0 or height <= 0 or sill < 0:
            _contract_error(
                "manifest_opening_dimensions_invalid",
                "opening width/height must be positive and sill height non-negative",
                opening_id=opening_id, width_m=width, height_m=height, sill_height_m=sill,
            )
        opening["opening_id"] = opening_id
        opening["width_m"] = width
        opening["height_m"] = height
        opening["sill_height_m"] = sill
        if "offset_m" in opening:
            offset = _number(opening.get("offset_m"), f"opening_voids[{index}].offset_m")
            if offset < 0:
                _contract_error("manifest_opening_interval_invalid", "opening offset must be non-negative", opening_id=opening_id)
            wall_length = opening.get("wall_length_m")
            if wall_length is not None and offset + width > _number(
                    wall_length, f"opening_voids[{index}].wall_length_m") + 1e-8:
                _contract_error(
                    "manifest_opening_interval_invalid", "opening extends beyond its host wall",
                    opening_id=opening_id, offset_m=offset, width_m=width, wall_length_m=wall_length,
                )
        wall_height = opening.get("wall_height_m")
        if wall_height is not None and sill + height > _number(
                wall_height, f"opening_voids[{index}].wall_height_m") + 1e-8:
            _contract_error(
                "manifest_opening_vertical_invalid", "opening extends above its host wall",
                opening_id=opening_id, sill_height_m=sill, height_m=height, wall_height_m=wall_height,
            )
    expected_hash = canonical_hash(result, exclude_fields=MANIFEST_HASH_FIELDS)
    supplied_hash = str(result.get("manifest_hash") or "")
    if supplied_hash and supplied_hash != expected_hash:
        _contract_error(
            "manifest_hash_mismatch", "manifest_hash does not cover the current geometry",
            expected=expected_hash, actual=supplied_hash,
        )
    result["manifest_hash"] = expected_hash
    return canonicalize(result)


def build_geometry_manifest(**fields: Any) -> dict[str, Any]:
    """Build and hash a GeometryManifest v1."""
    return validate_geometry_manifest({"version": GEOMETRY_CONTRACT_VERSION, **fields})


class GeometryManifest(dict):
    """Validated dictionary representation of GeometryManifest v1."""

    def __init__(self, value: Mapping[str, Any] | None = None, **kwargs: Any):
        super().__init__(validate_geometry_manifest({**dict(value or {}), **kwargs}))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GeometryManifest":
        return cls(value)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))


def _issue(
    severity: str, code: str, message: str, *, observed: Any = None,
    limit: Any = None, entity_ids: Sequence[str] = (), **details: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {"severity": severity, "code": code, "message": message}
    if observed is not None:
        result["observed"] = observed
    if limit is not None:
        result["limit"] = limit
    if entity_ids:
        result["entity_ids"] = [str(item) for item in entity_ids]
    result.update(details)
    return result


def _metric(metrics: Mapping[str, Any], names: Sequence[str]) -> tuple[bool, Any, str]:
    for name in names:
        if name in metrics:
            return True, metrics[name], name
    return False, None, names[0]


def _require_metric(
    metrics: Mapping[str, Any], names: Sequence[str], issues: list[dict[str, Any]],
) -> tuple[bool, float]:
    found, value, name = _metric(metrics, names)
    if not found:
        issues.append(_issue("hard", "metric_missing", f"required geometry metric is missing: {name}", metric=name))
        return False, 0.0
    try:
        return True, _number(value, name)
    except GeometryContractError:
        issues.append(_issue("hard", "metric_invalid", f"geometry metric is not finite: {name}", metric=name))
        return False, 0.0


def _at_least(
    metrics: Mapping[str, Any], names: Sequence[str], limit: float, code: str,
    issues: list[dict[str, Any]], message: str,
) -> None:
    found, value = _require_metric(metrics, names, issues)
    if found and value < limit:
        issues.append(_issue("hard", code, message, observed=value, limit={"minimum": limit}))


def _at_most(
    metrics: Mapping[str, Any], names: Sequence[str], limit: float, code: str,
    issues: list[dict[str, Any]], message: str,
) -> None:
    found, value = _require_metric(metrics, names, issues)
    if found and value > limit:
        issues.append(_issue("hard", code, message, observed=value, limit={"maximum": limit}))


def evaluate_geometry_metrics(
    *, source_type: str, input_grade: str, metrics: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Apply CAD/raster and model-to-manifest phase-one hard gates."""
    if not isinstance(metrics, Mapping):
        return [_issue("fatal", "metrics_invalid", "geometry metrics must be an object")]
    source_key = "raster" if input_grade.startswith("raster_") else "cad"
    source_metrics = metrics.get(source_key) if isinstance(metrics.get(source_key), Mapping) else metrics
    manifest_metrics = metrics.get("manifest") if isinstance(metrics.get("manifest"), Mapping) else metrics
    issues: list[dict[str, Any]] = []

    if source_key == "cad":
        _at_least(source_metrics, ("provenance_coverage",), 1.0, "cad_provenance_incomplete", issues,
                  "all authoritative CAD geometry must retain provenance")
        _at_least(source_metrics, ("wall_assembly_coverage",), 1.0, "cad_wall_assembly_incomplete", issues,
                  "every formal CAD wall needs a WallAssembly")
        _at_most(source_metrics, ("boundary_p95_m", "wall_boundary_p95_m"), 0.05,
                 "cad_boundary_p95_exceeded", issues, "CAD/model boundary P95 exceeds 0.05m")
        _at_most(source_metrics, ("boundary_max_m", "wall_boundary_max_m"), 0.10,
                 "cad_boundary_max_exceeded", issues, "CAD/model boundary maximum exceeds 0.10m")
        _at_most(source_metrics, ("max_room_area_relative_error", "room_area_relative_error_max"), 0.01,
                 "cad_room_area_error", issues, "room area relative error exceeds 1%")
        _at_least(source_metrics, ("room_coverage",), 0.98, "cad_room_coverage_low", issues,
                  "eligible CAD room coverage is below 98%")
        _at_most(source_metrics, ("room_overlap_area_m2",), 1e-6, "cad_room_overlap", issues,
                 "CAD-derived rooms overlap")
        _at_most(source_metrics, ("outer_max_gap_m",), 0.02, "cad_outer_gap", issues,
                 "outer shell has an unexpected gap above 0.02m")
        found, opening_count, _ = _metric(source_metrics, ("opening_eligible_count",))
        eligible_openings = None
        if found:
            try:
                eligible_openings = _number(opening_count, "opening_eligible_count")
            except GeometryContractError:
                issues.append(_issue("hard", "metric_invalid", "geometry metric is not finite: opening_eligible_count"))
        if not found or eligible_openings is None or eligible_openings > 0:
            _at_most(source_metrics, ("opening_center_width_p95_m",), 0.05,
                     "cad_opening_error", issues, "opening center/width P95 exceeds 0.05m")
        for names, code, message in (
            (("orphan_opening_count",), "cad_orphan_opening", "orphan openings are not allowed"),
            (("outside_opening_count",), "cad_outside_opening", "openings outside walls are not allowed"),
            (("overlapping_opening_count",), "cad_overlapping_opening", "overlapping openings are not allowed"),
            (("unresolved_wall_count",), "cad_wall_unresolved", "unresolved CAD walls are not production ready"),
        ):
            _at_most(source_metrics, names, 0.0, code, issues, message)
        if "unresolved_opening_count" in source_metrics:
            _at_most(source_metrics, ("unresolved_opening_count",), 0.0,
                     "cad_opening_unresolved", issues,
                     "unresolved CAD openings are not production ready")
    else:
        if input_grade == "raster_draft":
            issues.append(_issue("review", "raster_not_human_locked", "raster draft needs a real scale and human lock"))
        elif input_grade != "raster_human_locked":
            issues.append(_issue("fatal", "raster_input_grade_invalid", "raster acceptance has an invalid input grade"))
        _at_least(source_metrics, ("scale_anchor_count",), 1.0, "raster_scale_missing", issues,
                  "a formal raster needs at least one real dimension")
        _at_most(source_metrics, ("scale_disagreement", "scale_disagreement_ratio"), 0.02,
                 "raster_scale_disagreement", issues, "raster scale anchors disagree by more than 2%")
        _at_most(source_metrics, ("registration_roundtrip_px",), 0.25,
                 "raster_roundtrip_error", issues, "raster registration roundtrip exceeds 0.25px")
        _at_most(source_metrics, ("wall_centerline_p95_m",), 0.10,
                 "raster_wall_alignment", issues, "raster wall centerline P95 exceeds 0.10m")
        _at_least(source_metrics, ("room_iou",), 0.95, "raster_room_iou", issues,
                  "raster room IoU is below 0.95")
        _at_least(source_metrics, ("opening_precision",), 1.0, "raster_opening_precision", issues,
                  "all confirmed raster openings must be correct")
        _at_least(source_metrics, ("opening_recall",), 1.0, "raster_opening_recall", issues,
                  "all confirmed raster openings must be represented")
        _at_least(source_metrics, ("human_review_completion",), 1.0, "raster_review_incomplete", issues,
                  "raster human review must be complete")
        _at_most(source_metrics, ("unresolved_review_count",), 0.0, "raster_review_unresolved", issues,
                 "unresolved raster review items remain")

    _at_least(manifest_metrics, ("floor_footprint_iou",), 0.999, "manifest_floor_mismatch", issues,
              "manifest floor footprint differs from the canonical model")
    found_abs, absolute_difference, _ = _metric(manifest_metrics, ("wall_footprint_symmetric_difference_m2",))
    found_ratio, relative_difference, _ = _metric(manifest_metrics, ("wall_footprint_symmetric_difference_ratio",))
    if not found_abs or not found_ratio:
        issues.append(_issue("hard", "metric_missing", "manifest wall symmetric-difference metrics are required"))
    else:
        try:
            absolute = _number(absolute_difference, "wall_footprint_symmetric_difference_m2")
            relative = _number(relative_difference, "wall_footprint_symmetric_difference_ratio")
            if absolute > 1e-4 and relative > 0.001:
                issues.append(_issue(
                    "hard", "manifest_wall_mismatch", "manifest wall footprint exceeds both allowed differences",
                    observed={"area_m2": absolute, "ratio": relative},
                    limit={"area_m2": 1e-4, "ratio": 0.001, "policy": "either"},
                ))
        except GeometryContractError:
            issues.append(_issue("hard", "metric_invalid", "manifest wall symmetric-difference metrics must be finite"))
    _at_most(manifest_metrics, ("opening_interval_error_m",), 1e-6, "manifest_opening_mismatch", issues,
             "manifest opening interval differs from the canonical model")
    _at_least(manifest_metrics, ("projection_iou", "model_to_manifest_projection_iou"), 0.995,
              "manifest_projection_mismatch", issues, "2D model and 3D top projection IoU is below 0.995")
    _at_most(manifest_metrics, ("orphan_manifest_opening_count",), 0.0,
             "manifest_orphan_opening", issues, "manifest contains an opening outside its wall")
    return issues


def _validate_issue(raw: Mapping[str, Any], index: int) -> dict[str, Any]:
    issue = copy.deepcopy(dict(raw))
    severity = str(issue.get("severity") or "").strip()
    code = str(issue.get("code") or "").strip()
    message = str(issue.get("message") or "").strip()
    if severity not in ISSUE_SEVERITIES or not code or not message:
        _contract_error("acceptance_issue_invalid", "acceptance issue needs severity, code and message", index=index)
    issue.update(severity=severity, code=code, message=message)
    return canonicalize(issue)


def _status_from_issues(issues: Sequence[Mapping[str, Any]]) -> str:
    severities = {str(issue.get("severity") or "") for issue in issues}
    if severities & {"fatal", "hard"}:
        return "blocked"
    if "review" in severities:
        return "needs_human_review"
    return "passed"


def validate_geometry_acceptance_report(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate report integrity; it does not compare the report with current state."""
    if not isinstance(value, Mapping):
        _contract_error("acceptance_report_invalid", "geometry acceptance report must be an object")
    result = copy.deepcopy(dict(value))
    result["report_version"] = int(result.get("report_version", GEOMETRY_CONTRACT_VERSION))
    if result["report_version"] != GEOMETRY_CONTRACT_VERSION:
        _contract_error("acceptance_report_version_unsupported", "unsupported acceptance report version")
    for field in ("project_id", "source_type", "input_grade", "source_hash", "model_facts_hash",
                  "registration_hash", "geometry_kernel_version", "manifest_hash"):
        if not str(result.get(field) or "").strip():
            _contract_error("acceptance_report_field_missing", f"{field} is required", field=field)
    if str(result.get("input_grade")) not in INPUT_GRADES:
        _contract_error("acceptance_input_grade_invalid", "unknown acceptance input grade")
    result["model_revision"] = int(result.get("model_revision", 0))
    if result["model_revision"] < 1:
        _contract_error("acceptance_revision_invalid", "acceptance report needs a positive model revision")
    raw_issues = result.get("issues") or []
    if not isinstance(raw_issues, list):
        _contract_error("acceptance_issues_invalid", "issues must be an array")
    result["issues"] = [_validate_issue(issue, index) for index, issue in enumerate(raw_issues)]
    supplied_status = str(result.get("status") or "")
    expected_status = _status_from_issues(result["issues"])
    if supplied_status not in REPORT_STATUSES:
        _contract_error("acceptance_status_invalid", "unknown acceptance report status", status=supplied_status)
    if supplied_status != "stale" and supplied_status != expected_status:
        _contract_error(
            "acceptance_status_mismatch", "report status does not match its issues",
            expected=expected_status, actual=supplied_status,
        )
    expected_hash = canonical_hash(result, exclude_fields=REPORT_HASH_FIELDS)
    supplied_hash = str(result.get("report_hash") or "")
    if supplied_hash and supplied_hash != expected_hash:
        _contract_error(
            "acceptance_report_hash_mismatch", "report_hash does not cover the current report",
            expected=expected_hash, actual=supplied_hash,
        )
    result["report_hash"] = expected_hash
    return canonicalize(result)


def build_geometry_acceptance_report(
    *, project_id: str, source_type: str, input_grade: str, source_hash: str,
    model_revision: int, model_facts_hash: str, registration_hash: str,
    geometry_kernel_version: str, manifest_hash: str, metrics: Mapping[str, Any],
    cad_facts_hash: str = "", human_review: Mapping[str, Any] | None = None,
    artifacts: Mapping[str, Any] | None = None,
    issues: Sequence[Mapping[str, Any]] = (), report_id: str = "",
    created_at: Any = None,
) -> dict[str, Any]:
    """Evaluate metrics and build an integrity-protected acceptance report."""
    evaluated = evaluate_geometry_metrics(
        source_type=source_type, input_grade=input_grade, metrics=metrics,
    )
    review = copy.deepcopy(dict(human_review or {}))
    if review.get("required") and not review.get("completed"):
        evaluated.append(_issue("review", "human_review_incomplete", "required human review is incomplete"))
    if review.get("engineering_assumptions_required") and not review.get("assumptions_confirmed"):
        evaluated.append(_issue("review", "engineering_assumptions_unconfirmed", "vertical engineering assumptions are unconfirmed"))
    evaluated.extend(_validate_issue(issue, index) for index, issue in enumerate(issues))
    payload: dict[str, Any] = {
        "report_version": GEOMETRY_CONTRACT_VERSION,
        "project_id": str(project_id), "source_type": str(source_type),
        "input_grade": str(input_grade), "source_hash": str(source_hash),
        "model_revision": int(model_revision), "model_facts_hash": str(model_facts_hash),
        "registration_hash": str(registration_hash), "cad_facts_hash": str(cad_facts_hash),
        "geometry_kernel_version": str(geometry_kernel_version), "manifest_hash": str(manifest_hash),
        "metrics": copy.deepcopy(dict(metrics)), "issues": evaluated,
        "human_review": review, "artifacts": copy.deepcopy(dict(artifacts or {})),
        "status": _status_from_issues(evaluated),
    }
    if created_at is not None:
        payload["created_at"] = created_at
    if not report_id:
        report_id = "gar_" + canonical_hash(payload)[:20]
    payload["report_id"] = report_id
    payload["report_hash"] = canonical_hash(payload, exclude_fields=REPORT_HASH_FIELDS)
    return validate_geometry_acceptance_report(payload)


class GeometryAcceptanceReport(dict):
    """Validated dictionary representation of GeometryAcceptanceReport v1."""

    def __init__(self, value: Mapping[str, Any] | None = None, **kwargs: Any):
        super().__init__(validate_geometry_acceptance_report({**dict(value or {}), **kwargs}))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GeometryAcceptanceReport":
        return cls(value)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self))


STALE_FACT_FIELDS = (
    "source_hash", "model_revision", "model_facts_hash", "registration_hash",
    "cad_facts_hash", "geometry_kernel_version", "manifest_hash",
)


def acceptance_stale_reasons(
    report: Mapping[str, Any], current_facts: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return every authority/hash mismatch that makes a report stale."""
    reasons = []
    for field in STALE_FACT_FIELDS:
        expected = report.get(field, "")
        actual = current_facts.get(field, "")
        # cad_facts_hash is intentionally empty for raster sources.
        if field == "cad_facts_hash" and not expected and not actual:
            continue
        if canonicalize(expected) != canonicalize(actual):
            reasons.append({"field": field, "expected": expected, "actual": actual})
    return reasons


def refresh_acceptance_staleness(
    report: Mapping[str, Any], current_facts: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a report copy marked stale when current authority differs."""
    normalized = validate_geometry_acceptance_report(report)
    reasons = acceptance_stale_reasons(normalized, current_facts)
    if not reasons:
        return normalized
    normalized["status"] = "stale"
    normalized["stale_reasons"] = reasons
    normalized["report_hash"] = canonical_hash(normalized, exclude_fields=REPORT_HASH_FIELDS)
    return validate_geometry_acceptance_report(normalized)


def report_hash_matches(report: Mapping[str, Any]) -> bool:
    supplied = str(report.get("report_hash") or "")
    return bool(supplied) and supplied == canonical_hash(report, exclude_fields=REPORT_HASH_FIELDS)


def migrate_legacy_project_geometry(project: Mapping[str, Any]) -> dict[str, Any]:
    """Create an in-memory v3-compatible project without blessing legacy verification.

    The old ``verified`` flag is retained for history/UI compatibility, but a
    new production gate sees ``legacy_unproven`` until a v1 acceptance report
    is generated.  Existing model ``schema_version`` is not overwritten because
    older render/edit code still owns that schema.
    """
    migrated = copy.deepcopy(dict(project or {}))
    model = copy.deepcopy(dict(migrated.get("model") or {}))
    already_current = int(migrated.get("geometry_schema_version", 0) or 0) >= GEOMETRY_MODEL_VERSION
    has_acceptance = isinstance(migrated.get("geometry_acceptance"), Mapping)
    migrated["geometry_schema_version"] = GEOMETRY_MODEL_VERSION
    model["geometry_schema_version"] = GEOMETRY_MODEL_VERSION
    if not already_current or not has_acceptance:
        migrated["legacy_verified"] = bool(migrated.get("verified"))
        migrated["input_grade"] = "legacy_unproven"
        model["input_grade"] = "legacy_unproven"
        migrated["geometry_acceptance_summary"] = {
            "status": "legacy_unproven",
            "reason": "legacy_verified_does_not_prove_source_model_manifest_correspondence",
        }
    else:
        grade = str(migrated.get("input_grade") or model.get("input_grade") or "legacy_unproven")
        migrated["input_grade"] = grade
        model["input_grade"] = grade
    migrated["model"] = model
    return migrated


def _current_facts_from_project(
    project: Mapping[str, Any], manifest: Mapping[str, Any] | None,
) -> dict[str, Any]:
    model = project.get("model") if isinstance(project.get("model"), Mapping) else {}
    registration = project.get("source_registration")
    if not isinstance(registration, Mapping):
        registration = model.get("source_registration") if isinstance(model.get("source_registration"), Mapping) else {}
    manifest = manifest or {}
    has_plan_geometry = any(key in model for key in ("wall_assemblies", "walls", "rooms", "physical_spaces", "openings"))
    if has_plan_geometry:
        try:
            current_model_hash = geometry_facts_hash(model)
        except GeometryContractError:
            current_model_hash = "invalid_geometry_facts"
    else:
        current_model_hash = str(model.get("model_facts_hash") or project.get("model_facts_hash") or "")
    return {
        "source_hash": str(registration.get("source_hash") or project.get("source_hash") or ""),
        "model_revision": int(project.get("revision", 0) or 0),
        "model_facts_hash": current_model_hash,
        "registration_hash": str(registration.get("registration_hash") or project.get("registration_hash") or ""),
        "cad_facts_hash": str(model.get("cad_facts_hash") or (project.get("cad_import") or {}).get("cad_facts_hash") or ""),
        "geometry_kernel_version": str(manifest.get("geometry_kernel_version") or project.get("geometry_kernel_version") or ""),
        "manifest_hash": str(manifest.get("manifest_hash") or project.get("manifest_hash") or ""),
    }


def production_readiness(
    project: Mapping[str, Any], report: Mapping[str, Any] | None = None,
    manifest: Mapping[str, Any] | None = None, *, current_facts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure, fail-closed production gate for camera/pano/paid/Image2 routes."""
    reasons: list[dict[str, Any]] = []
    model = project.get("model") if isinstance(project.get("model"), Mapping) else {}
    grade = str(project.get("input_grade") or model.get("input_grade") or "legacy_unproven")
    if grade in {"legacy_unproven", "raster_draft"}:
        reasons.append(_issue("hard", "input_not_locked", "project input is not authoritative/locked", observed=grade))
    registration = project.get("source_registration")
    if not isinstance(registration, Mapping):
        registration = model.get("source_registration") if isinstance(model.get("source_registration"), Mapping) else None
    if not registration:
        reasons.append(_issue("fatal", "source_registration_missing", "source registration is required"))
    else:
        try:
            validated_registration = validate_source_registration(registration)
            if grade != validated_registration["input_grade"]:
                reasons.append(_issue("hard", "input_grade_mismatch", "project and registration input grades differ"))
            if str(validated_registration.get("source_type") or "") == "cad":
                coordinate_version = int(model.get("coordinate_contract_version", 0) or 0)
                coordinate_system = str(model.get("coordinate_system") or "")
                if (int(validated_registration.get("version", 0) or 0) < SOURCE_REGISTRATION_VERSION
                        or coordinate_version < 2
                        or coordinate_system != "right-handed-y-up-x-east-z-south-v2"):
                    reasons.append(_issue(
                        "hard", "cad_coordinate_contract_legacy_invalid",
                        "legacy/reflected CAD coordinates cannot enter production; reparse with sky-down coordinate contract v2",
                        registration_version=int(validated_registration.get("version", 0) or 0),
                        coordinate_contract_version=coordinate_version,
                        coordinate_system=coordinate_system,
                    ))
        except GeometryContractError as error:
            reasons.append(_issue("fatal", error.code, error.message, **error.details))

    if manifest is None:
        reasons.append(_issue("fatal", "geometry_manifest_missing", "a locked server geometry manifest is required"))
    else:
        try:
            manifest = validate_geometry_manifest(manifest)
        except GeometryContractError as error:
            reasons.append(_issue("fatal", error.code, error.message, **error.details))
    if manifest:
        has_plan_geometry = any(
            key in model for key in ("wall_assemblies", "walls", "rooms", "physical_spaces", "openings")
        )
        if has_plan_geometry:
            try:
                current_geometry_hash = geometry_facts_hash(model)
            except GeometryContractError as error:
                current_geometry_hash = "invalid_geometry_facts"
                reasons.append(_issue("fatal", error.code, error.message, **error.details))
        else:
            current_geometry_hash = str(
                model.get("model_facts_hash") or project.get("model_facts_hash") or "")
        if str(manifest.get("model_facts_hash") or "") != current_geometry_hash:
            reasons.append(_issue(
                "hard", "manifest_model_facts_mismatch",
                "geometry manifest was not compiled from the current normalized plan facts",
                expected=current_geometry_hash, actual=str(manifest.get("model_facts_hash") or ""),
            ))
        strict_entities = grade in {"vector_authoritative", "raster_human_locked"}
        assemblies = [row for row in model.get("wall_assemblies") or [] if isinstance(row, Mapping)]
        if strict_entities and has_plan_geometry:
            unresolved_walls = {
                str(row.get("id") or "") for row in assemblies
                if normalize_review_status(row.get("review_status")) == "pending"
            }
            declared_assembly_ids = {str(row.get("id") or "") for row in assemblies}
            unresolved_walls |= {
                str(row.get("id") or "") for row in model.get("walls") or []
                if isinstance(row, Mapping)
                and str(row.get("wall_assembly_id") or "") not in declared_assembly_ids
                and normalize_review_status(row.get("review_status")) == "pending"
            }
            unresolved_openings = {
                str(row.get("id") or "") for row in model.get("openings") or []
                if isinstance(row, Mapping)
                and normalize_review_status(row.get("review_status")) == "pending"
            }
            if unresolved_walls:
                reasons.append(_issue(
                    "review", "current_unresolved_wall_review",
                    "current model still contains wall bodies awaiting review",
                    entity_ids=sorted(unresolved_walls),
                ))
            if unresolved_openings:
                reasons.append(_issue(
                    "review", "current_unresolved_opening_review",
                    "current model still contains openings awaiting review",
                    entity_ids=sorted(unresolved_openings),
                ))
            confirmed_wall_ids = {
                str(row.get("id") or "") for row in assemblies if geometry_entity_confirmed(row)
            }
            confirmed_wall_ids |= {
                str(row.get("id") or "") for row in model.get("walls") or []
                if isinstance(row, Mapping)
                and str(row.get("wall_assembly_id") or "") not in declared_assembly_ids
                and geometry_entity_confirmed(row)
            }
            manifest_wall_ids = {
                str(part.get("wall_assembly_id") or part.get("entity_id") or "")
                for part in manifest.get("wall_parts") or [] if isinstance(part, Mapping)
            }
            unexpected_walls = sorted(manifest_wall_ids - confirmed_wall_ids)
            if unexpected_walls:
                reasons.append(_issue(
                    "hard", "manifest_contains_unconfirmed_wall",
                    "manifest contains wall bodies which were not accepted/confirmed",
                    entity_ids=unexpected_walls,
                ))
            confirmed_opening_ids = {
                str(row.get("id") or "") for row in model.get("openings") or []
                if isinstance(row, Mapping) and geometry_entity_confirmed(row)
            }
            manifest_opening_ids = {
                str(row.get("opening_id") or row.get("id") or "")
                for row in manifest.get("opening_voids") or [] if isinstance(row, Mapping)
            }
            if manifest_opening_ids != confirmed_opening_ids:
                reasons.append(_issue(
                    "hard", "manifest_opening_set_mismatch",
                    "manifest opening voids must exactly match confirmed model openings",
                    missing=sorted(confirmed_opening_ids - manifest_opening_ids),
                    unexpected=sorted(manifest_opening_ids - confirmed_opening_ids),
                ))

    if report is None:
        reasons.append(_issue("fatal", "geometry_acceptance_missing", "a geometry acceptance report is required"))
        normalized_report: dict[str, Any] = {}
    else:
        try:
            normalized_report = validate_geometry_acceptance_report(report)
        except GeometryContractError as error:
            reasons.append(_issue("fatal", error.code, error.message, **error.details))
            normalized_report = {}
    facts = dict(current_facts or _current_facts_from_project(project, manifest))
    if normalized_report:
        stale = acceptance_stale_reasons(normalized_report, facts)
        if stale:
            reasons.append(_issue("hard", "geometry_acceptance_stale", "acceptance facts no longer match the project", mismatches=stale))
        if normalized_report.get("status") != "passed":
            reasons.append(_issue(
                "hard", "geometry_acceptance_not_passed", "geometry acceptance report is not passed",
                observed=normalized_report.get("status"),
            ))
        if str(normalized_report.get("input_grade")) != grade:
            reasons.append(_issue("hard", "acceptance_input_grade_mismatch", "report and project input grades differ"))
        review = normalized_report.get("human_review") or {}
        if review.get("engineering_assumptions_required") and not review.get("assumptions_confirmed"):
            reasons.append(_issue("review", "engineering_assumptions_unconfirmed", "engineering assumptions need confirmation"))
    blocking = [reason for reason in reasons if reason.get("severity") in BLOCKING_SEVERITIES]
    return {
        "ready": not blocking,
        "code": "ready" if not blocking else str(blocking[0].get("code") or "not_ready"),
        "input_grade": grade,
        "report_status": normalized_report.get("status") if normalized_report else "missing",
        "reasons": reasons,
        "current_facts": facts,
    }


def assert_production_ready(
    project: Mapping[str, Any], report: Mapping[str, Any] | None = None,
    manifest: Mapping[str, Any] | None = None, *, current_facts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return readiness or raise ``production_not_ready`` with all reasons."""
    readiness = production_readiness(
        project, report=report, manifest=manifest, current_facts=current_facts,
    )
    if not readiness["ready"]:
        _contract_error(
            "production_not_ready", "whole-home geometry is not production ready",
            gate_code=readiness["code"], reasons=readiness["reasons"],
        )
    return readiness


# Short aliases make route integration readable and keep the long names public.
validate_manifest = validate_geometry_manifest
validate_acceptance_report = validate_geometry_acceptance_report
legacy_unproven_migration = migrate_legacy_project_geometry


__all__ = [
    "GEOMETRY_CONTRACT_VERSION", "GEOMETRY_MODEL_VERSION", "SOURCE_REGISTRATION_VERSION", "INPUT_GRADES",
    "WALL_REPRESENTATIONS", "REPORT_STATUSES", "ISSUE_SEVERITIES",
    "GeometryContractError", "SourceRegistration", "WallAssembly",
    "GeometryManifest", "GeometryAcceptanceReport", "canonicalize",
    "canonical_json", "canonical_hash", "normalize_review_status",
    "geometry_entity_confirmed", "geometry_facts_payload", "geometry_facts_hash",
    "invert_matrix3",
    "validate_source_registration", "validate_wall_assembly",
    "validate_geometry_manifest", "validate_manifest", "build_geometry_manifest",
    "evaluate_geometry_metrics", "validate_geometry_acceptance_report",
    "validate_acceptance_report", "build_geometry_acceptance_report",
    "acceptance_stale_reasons", "refresh_acceptance_staleness",
    "report_hash_matches", "migrate_legacy_project_geometry",
    "legacy_unproven_migration", "production_readiness", "assert_production_ready",
]
