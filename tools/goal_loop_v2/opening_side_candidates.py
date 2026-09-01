"""Deterministic, fail-closed opening side-space ranking.

The directed opening segment defines a left/right normal frame.  Source space
anchor points are ranked independently on each side.  A ranking is evidence
for review only: it never confirms a side, adjacency, traversability, or build.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document


SCHEMA = "opening-side-space-candidate-v1"
SIDE_IDS = ("left_of_directed_segment", "right_of_directed_segment")
LIMITATIONS = {
    "space_boundary_geometry": False,
    "side_space_confirmation": False,
    "room_adjacency": False,
    "traversability": False,
    "semantic_promotion": False,
    "build": False,
}


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _binding(role: str, path: str | Path) -> dict[str, str]:
    p = Path(path).expanduser().resolve()
    raw = p.read_bytes()
    if p.suffix.lower() == ".json":
        canonical = _hash(json.loads(raw.decode("utf-8")))
        media_type = "application/json"
    else:
        canonical = hashlib.sha256(
            raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        ).hexdigest()
        media_type = "application/octet-stream"
    return {
        "role": role,
        "path": str(p),
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "canonical_sha256": canonical,
        "media_type": media_type,
    }


def _round(value: float) -> float:
    rounded = round(float(value), 9)
    return 0.0 if rounded == -0.0 else rounded


def _point(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, list) or len(value) != 2:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        return None
    result = (float(value[0]), float(value[1]))
    return result if all(math.isfinite(v) for v in result) else None


def _ambiguity(candidates: list[dict[str, Any]], epsilon: float) -> dict[str, Any]:
    count = len(candidates)
    top = candidates[0] if count else None
    second = candidates[1] if count > 1 else None
    top_distance = None if top is None else top["distance_to_segment_m"]
    second_distance = None if second is None else second["distance_to_segment_m"]
    gap = None if second is None else _round(second_distance - top_distance)
    margin = None if second is None else _round(
        (second_distance - top_distance) / max(second_distance, epsilon)
    )
    if count == 0:
        ambiguity_class = "no_candidate"
    elif count == 1:
        ambiguity_class = "single_candidate_uncompared"
    elif margin < 0.25:
        ambiguity_class = "close_ranking"
    else:
        ambiguity_class = "separated_ranking"
    return {
        "candidate_count": count,
        "top_space_id": None if top is None else top["space_id"],
        "second_space_id": None if second is None else second["space_id"],
        "top_distance_m": top_distance,
        "second_distance_m": second_distance,
        "top_distance_gap_m": gap,
        "normalized_top_margin": margin,
        "ambiguity_class": ambiguity_class,
        "rank_ambiguous": ambiguity_class != "separated_ranking",
    }


def _rank_opening(
    opening: Mapping[str, Any], spaces: list[Mapping[str, Any]], rank_limit: int,
    side_epsilon_m: float,
) -> tuple[dict[str, Any], list[str]]:
    opening_id = opening["id"]
    segment = (opening.get("source_observation") or {}).get("nominal_segment_m")
    p0 = _point(segment[0]) if isinstance(segment, list) and len(segment) == 2 else None
    p1 = _point(segment[1]) if isinstance(segment, list) and len(segment) == 2 else None
    invalid_space_ids: list[str] = []
    base = {
        "opening_id": opening_id,
        "source_segment_m": deepcopy(segment),
        "segment_status": "missing_or_invalid",
        "segment_frame": None,
        "sides": [],
        "on_axis_space_ids": [],
        "side_space_confirmation": False,
        "semantic_promotion": False,
        "build_authorized": False,
    }
    if p0 is None or p1 is None:
        return base, invalid_space_ids
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy)
    if length <= side_epsilon_m:
        base["segment_status"] = "degenerate"
        return base, invalid_space_ids
    tangent = (dx / length, dy / length)
    left = (-tangent[1], tangent[0])
    right = (-left[0], -left[1])
    midpoint = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
    buckets: dict[str, list[dict[str, Any]]] = {side: [] for side in SIDE_IDS}
    on_axis: list[str] = []
    for space in spaces:
        anchor = _point(space.get("point_m"))
        space_id = space.get("id")
        if anchor is None or not isinstance(space_id, str) or not space_id:
            if isinstance(space_id, str):
                invalid_space_ids.append(space_id)
            continue
        rx, ry = anchor[0] - midpoint[0], anchor[1] - midpoint[1]
        along = rx * tangent[0] + ry * tangent[1]
        signed = rx * left[0] + ry * left[1]
        if abs(signed) <= side_epsilon_m:
            on_axis.append(space_id)
            continue
        side_id = SIDE_IDS[0] if signed > 0 else SIDE_IDS[1]
        overrun = max(0.0, abs(along) - length / 2)
        distance_to_segment = math.hypot(abs(signed), overrun)
        buckets[side_id].append({
            "rank": 0,
            "space_id": space_id,
            "space_label": space.get("label"),
            "space_source_status": space.get("status"),
            "anchor_point_m": [_round(anchor[0]), _round(anchor[1])],
            "directed_signed_normal_offset_m": _round(signed),
            "side_normal_distance_m": _round(abs(signed)),
            "tangent_offset_from_midpoint_m": _round(along),
            "along_segment_overrun_m": _round(overrun),
            "distance_to_segment_m": _round(distance_to_segment),
            "distance_to_midpoint_m": _round(math.hypot(rx, ry)),
            "status": "ranked_candidate_only",
        })
    sides = []
    for side_id, normal in zip(SIDE_IDS, (left, right)):
        ranked = sorted(
            buckets[side_id],
            key=lambda row: (
                row["distance_to_segment_m"], row["side_normal_distance_m"],
                abs(row["tangent_offset_from_midpoint_m"]), row["space_id"],
            ),
        )[:rank_limit]
        for rank, candidate in enumerate(ranked, 1):
            candidate["rank"] = rank
        sides.append({
            "side_id": side_id,
            "normal_unit": [_round(normal[0]), _round(normal[1])],
            "candidates": ranked,
            "ambiguity": _ambiguity(ranked, side_epsilon_m),
            "status": "candidate_only",
        })
    base.update({
        "segment_status": "usable",
        "segment_frame": {
            "midpoint_m": [_round(midpoint[0]), _round(midpoint[1])],
            "length_m": _round(length),
            "tangent_unit": [_round(tangent[0]), _round(tangent[1])],
            "left_normal_unit": [_round(left[0]), _round(left[1])],
            "right_normal_unit": [_round(right[0]), _round(right[1])],
            "direction_contract": "source_segment_p0_to_p1",
        },
        "sides": sides,
        "on_axis_space_ids": sorted(on_axis),
    })
    return base, invalid_space_ids


def _core(doc: Mapping[str, Any], rank_limit: int, side_epsilon_m: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    spaces = list(doc["spaces"])
    rows = []
    invalid_space_ids: set[str] = set()
    for opening in doc["opening_contract"]["openings"]:
        row, invalid = _rank_opening(opening, spaces, rank_limit, side_epsilon_m)
        rows.append(row)
        invalid_space_ids.update(invalid)
    counts = {status: sum(row["segment_status"] == status for row in rows) for status in ("usable", "missing_or_invalid", "degenerate")}
    coverage = {
        "opening_count": len(rows),
        "space_count": len(spaces),
        "usable_opening_count": counts["usable"],
        "missing_or_invalid_opening_count": counts["missing_or_invalid"],
        "degenerate_opening_count": counts["degenerate"],
        "invalid_space_anchor_ids": sorted(invalid_space_ids),
    }
    return rows, coverage


def _validate_parameters(rank_limit: Any, side_epsilon_m: Any) -> tuple[int, float]:
    if isinstance(rank_limit, bool) or not isinstance(rank_limit, int) or not 1 <= rank_limit <= 10:
        raise ValueError("rank_limit must be an integer from 1 to 10")
    if isinstance(side_epsilon_m, bool) or not isinstance(side_epsilon_m, (int, float)) or not 0 < side_epsilon_m <= 0.1:
        raise ValueError("side_epsilon_m must be in (0, 0.1]")
    return rank_limit, float(side_epsilon_m)


def build_opening_side_space_candidate(
    document: Mapping[str, Any], source_document_file: str | Path | None = None,
    evidence_files: Mapping[str, str | Path] | None = None, *, rank_limit: int = 3,
    side_epsilon_m: float = 1.0e-6,
) -> dict[str, Any]:
    doc = validate_v21_document(document)
    rank_limit, side_epsilon_m = _validate_parameters(rank_limit, side_epsilon_m)
    rows, coverage = _core(doc, rank_limit, side_epsilon_m)
    opening_snapshot = [
        {"id": o["id"], "nominal_segment_m": deepcopy((o.get("source_observation") or {}).get("nominal_segment_m"))}
        for o in doc["opening_contract"]["openings"]
    ]
    space_snapshot = [
        {"id": s["id"], "point_m": deepcopy(s.get("point_m")), "status": s.get("status")}
        for s in doc["spaces"]
    ]
    result = {
        "schema": SCHEMA,
        "source_structure_hash": doc["structure_hash"],
        "source_document": None if source_document_file is None else _binding("source_document", source_document_file),
        "evidence_bindings": [] if evidence_files is None else [_binding(role, path) for role, path in sorted(evidence_files.items())],
        "source_snapshot_hashes": {"opening_segments_sha256": _hash(opening_snapshot), "space_anchors_sha256": _hash(space_snapshot)},
        "parameters": {
            "rank_limit": rank_limit,
            "side_epsilon_m": float(side_epsilon_m),
            "side_frame": "left_right_of_directed_source_segment",
            "ranking_metric": "distance_to_segment_then_normal_then_along_then_space_id",
        },
        "coverage": coverage,
        "openings": rows,
        "limitations": deepcopy(LIMITATIONS),
        "status": "pending_independent_review",
        "side_space_confirmation": False,
        "semantic_promotion": False,
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _hash({k: v for k, v in result.items() if k != "candidate_hash"})
    return validate_opening_side_space_candidate(doc, result)


def _validate_binding(binding: Mapping[str, Any], expected_role: str | None = None) -> None:
    required = {"role", "path", "file_sha256", "canonical_sha256", "media_type"}
    if not isinstance(binding, Mapping) or set(binding) != required:
        raise ValueError("provenance binding invalid")
    if expected_role is not None and binding["role"] != expected_role:
        raise ValueError("provenance role mismatch")
    if _binding(binding["role"], binding["path"]) != dict(binding):
        raise ValueError("provenance file drift")


def validate_opening_side_space_candidate(document: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    doc = validate_v21_document(document)
    required = {
        "schema", "source_structure_hash", "source_document", "evidence_bindings",
        "source_snapshot_hashes", "parameters", "coverage", "openings", "limitations",
        "status", "side_space_confirmation", "semantic_promotion", "build_authorized",
        "ready", "candidate_hash",
    }
    if not isinstance(candidate, Mapping) or set(candidate) != required or candidate.get("schema") != SCHEMA:
        raise ValueError("opening side-space candidate keys/schema invalid")
    if candidate["source_structure_hash"] != doc["structure_hash"]:
        raise ValueError("opening side-space source drift")
    if candidate["limitations"] != LIMITATIONS or candidate["status"] != "pending_independent_review":
        raise ValueError("opening side-space limitations/status invalid")
    if any(candidate[key] is not False for key in ("side_space_confirmation", "semantic_promotion", "build_authorized", "ready")):
        raise ValueError("opening side-space candidate was promoted")
    parameters = candidate["parameters"]
    if set(parameters) != {"rank_limit", "side_epsilon_m", "side_frame", "ranking_metric"}:
        raise ValueError("opening side-space parameters invalid")
    if parameters["side_frame"] != "left_right_of_directed_source_segment" or parameters["ranking_metric"] != "distance_to_segment_then_normal_then_along_then_space_id":
        raise ValueError("opening side-space method drift")
    rank_limit, side_epsilon_m = _validate_parameters(parameters["rank_limit"], parameters["side_epsilon_m"])
    if candidate["source_document"] is not None:
        _validate_binding(candidate["source_document"], "source_document")
    roles: set[str] = set()
    for binding in candidate["evidence_bindings"]:
        _validate_binding(binding)
        if binding["role"] in roles:
            raise ValueError("duplicate evidence role")
        roles.add(binding["role"])
    opening_snapshot = [{"id": o["id"], "nominal_segment_m": deepcopy((o.get("source_observation") or {}).get("nominal_segment_m"))} for o in doc["opening_contract"]["openings"]]
    space_snapshot = [{"id": s["id"], "point_m": deepcopy(s.get("point_m")), "status": s.get("status")} for s in doc["spaces"]]
    expected_snapshots = {"opening_segments_sha256": _hash(opening_snapshot), "space_anchors_sha256": _hash(space_snapshot)}
    if candidate["source_snapshot_hashes"] != expected_snapshots:
        raise ValueError("source snapshot drift")
    expected_rows, expected_coverage = _core(doc, rank_limit, side_epsilon_m)
    if candidate["openings"] != expected_rows or candidate["coverage"] != expected_coverage:
        raise ValueError("opening side-space ranking drift")
    if candidate["candidate_hash"] != _hash({k: v for k, v in candidate.items() if k != "candidate_hash"}):
        raise ValueError("opening side-space hash drift")
    return deepcopy(dict(candidate))


__all__ = ["build_opening_side_space_candidate", "validate_opening_side_space_candidate"]
