"""Search source opening segments for an exterior-boundary crossing.

This is a deterministic, fail-closed research candidate. Geometric contact is
reported separately from root, entrance, traversability, and adjacency authority.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.demoted_portal_deny import (
    PORTAL_ID,
    build_demoted_portal_deny,
    validate_demoted_portal_deny,
)
from tools.goal_loop_v2.op001_unit_scope_candidate import (
    validate_op001_unit_scope_candidate,
)

SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
OP001_SCOPE = ROOT / "reports/op001_unit_scope_candidate_20260902/op001-unit-scope-candidate.json"
OUT = ROOT / "reports/exterior_root_search_candidate_20260903"

TOPOLOGY_TOLERANCE_M = 1e-9
NEAR_MISS_THRESHOLD_M = 0.1
EXPECTED_SOURCE_OPENING_IDS = tuple(
    [f"OP{index:03d}" for index in range(1, 12)] + [PORTAL_ID]
)
ROW_FAIL_CLOSED = (
    "geometric_boundary_crossing_candidate",
    "root_candidate",
    "root_confirmation",
    "entrance_confirmation",
    "outside_side_confirmation",
    "traversability_confirmation",
    "pair_confirmation",
    "adjacency_confirmation",
    "source_correction_authorized",
    "semantic_promotion",
    "build_authorized",
    "ready",
)
BATCH_FAIL_CLOSED = (
    "any_geometric_boundary_crossing_candidate",
    "root_confirmation",
    "entrance_confirmation",
    "outside_side_confirmation",
    "traversability_confirmation",
    "pair_confirmation",
    "adjacency_confirmation",
    "reachability_confirmation",
    "source_correction_authorized",
    "semantic_promotion",
    "build_authorized",
    "ready",
)


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any] | Sequence[Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _without_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "candidate_hash"}


def _rounded(value: float, tolerance_m: float) -> float:
    rounded = round(float(value), 9)
    return 0.0 if abs(rounded) <= tolerance_m else rounded


def _point_count(geometry) -> int:
    if geometry.is_empty:
        return 0
    if geometry.geom_type == "Point":
        return 1
    if geometry.geom_type in {"MultiPoint", "GeometryCollection"}:
        return sum(_point_count(part) for part in geometry.geoms)
    return 0


def _outer_edges(outer: Polygon) -> list[tuple[str, LineString]]:
    coordinates = list(outer.exterior.coords)
    return [
        (f"OUTER-EDGE-{index:03d}", LineString([coordinates[index - 1], coordinates[index]]))
        for index in range(1, len(coordinates))
    ]


def classify_segment_against_outer_boundary(
    segment_m: Sequence[Sequence[float]],
    outer: Polygon,
    *,
    topology_tolerance_m: float = TOPOLOGY_TOLERANCE_M,
    near_miss_threshold_m: float = NEAR_MISS_THRESHOLD_M,
) -> dict[str, Any]:
    if len(segment_m) != 2 or any(len(point) != 2 for point in segment_m):
        raise ValueError("root-search segment must contain two 2D endpoints")
    line = LineString(segment_m)
    if line.length <= topology_tolerance_m:
        raise ValueError("root-search segment is degenerate")
    if not outer.is_valid or outer.is_empty or outer.area <= topology_tolerance_m:
        raise ValueError("root-search outer boundary is invalid")

    boundary = outer.boundary
    endpoint_locations: list[str] = []
    for coordinates in segment_m:
        point = Point(coordinates)
        if point.distance(boundary) <= topology_tolerance_m:
            endpoint_locations.append("boundary")
        elif outer.contains(point):
            endpoint_locations.append("inside")
        else:
            endpoint_locations.append("outside")

    intersection = line.intersection(boundary)
    intersection_present = line.intersects(boundary)
    interior_length = line.intersection(outer).length
    exterior_length = line.difference(outer).length
    actual_crossing = (
        intersection_present
        and interior_length > topology_tolerance_m
        and exterior_length > topology_tolerance_m
    )
    boundary_overlap_length = intersection.length
    boundary_contact = intersection_present and not actual_crossing
    distance = line.distance(boundary)

    if actual_crossing:
        classification = "actual-crossing"
    elif boundary_contact or distance <= topology_tolerance_m:
        classification = "boundary-touch"
    elif distance <= near_miss_threshold_m:
        classification = "near-miss"
    elif all(location == "inside" for location in endpoint_locations):
        classification = "internal"
    else:
        classification = "off-frame-not-observed"

    nearest_on_segment, nearest_on_boundary = nearest_points(line, boundary)
    boundary_point = [nearest_on_boundary.x, nearest_on_boundary.y]
    nearest_edge_ids = [
        edge_id
        for edge_id, edge in _outer_edges(outer)
        if Point(boundary_point).distance(edge) <= topology_tolerance_m
    ]
    result = {
        "segment_m": deepcopy([list(point) for point in segment_m]),
        "segment_length_m": _rounded(line.length, topology_tolerance_m),
        "endpoint_locations": endpoint_locations,
        "intersects_boundary": bool(intersection_present),
        "boundary_contact_without_crossing": bool(boundary_contact),
        "actual_crossing": bool(actual_crossing),
        "shapely_crosses_outer_polygon": bool(line.crosses(outer)),
        "intersection_geometry_type": intersection.geom_type,
        "intersection_point_count": _point_count(intersection),
        "boundary_overlap_length_m": _rounded(boundary_overlap_length, topology_tolerance_m),
        "inside_or_boundary_length_m": _rounded(interior_length, topology_tolerance_m),
        "outside_length_m": _rounded(exterior_length, topology_tolerance_m),
        "minimum_boundary_distance_m": _rounded(distance, topology_tolerance_m),
        "nearest_point_on_segment_m": [
            _rounded(nearest_on_segment.x, topology_tolerance_m),
            _rounded(nearest_on_segment.y, topology_tolerance_m),
        ],
        "nearest_point_on_boundary_m": [
            _rounded(nearest_on_boundary.x, topology_tolerance_m),
            _rounded(nearest_on_boundary.y, topology_tolerance_m),
        ],
        "nearest_boundary_edge_ids": nearest_edge_ids,
        "classification": classification,
    }
    return result


def _segment_inputs(opening: Mapping[str, Any]) -> list[dict[str, Any]]:
    observation = opening.get("source_observation")
    if not isinstance(observation, Mapping) or not observation.get("nominal_segment_m"):
        raise ValueError(f"{opening.get('id')} has no source nominal segment")
    inputs = [
        {
            "source": "source_observation.nominal_segment_m",
            "authority": "source_record",
            "segment_m": deepcopy(observation["nominal_segment_m"]),
        }
    ]
    effective_void = opening.get("effective_void")
    if isinstance(effective_void, Mapping) and effective_void.get("segment_m"):
        inputs.append(
            {
                "source": "effective_void.segment_m",
                "authority": "source_record_effective_void",
                "effective_void_record_status": effective_void.get("status"),
                "segment_m": deepcopy(effective_void["segment_m"]),
            }
        )
    return inputs


def _assert_fail_closed(value: Mapping[str, Any], keys: tuple[str, ...], *, context: str) -> None:
    for key in keys:
        if value.get(key) is not False:
            raise ValueError(f"{context} promoted or omitted {key}")
    if value.get("score_effect") != "none":
        raise ValueError(f"{context} score drift")


def build(
    document: Mapping[str, Any] | None = None,
    *,
    source_path: Path = SOURCE,
    op001_scope_path: Path = OP001_SCOPE,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    source_path = Path(source_path)
    op001_scope_path = Path(op001_scope_path)
    document_value = _read_json(source_path) if document is None else deepcopy(dict(document))
    source = validate_v21_document(document_value)
    outer_record = source["outer_boundary"]
    if outer_record.get("status") != "confirmed":
        raise ValueError("root search requires a confirmed source outer boundary")
    outer = Polygon(outer_record["polygon_m"])

    source_openings = source["opening_contract"]["openings"]
    source_ids = tuple(opening["id"] for opening in source_openings)
    if source_ids != EXPECTED_SOURCE_OPENING_IDS:
        raise ValueError("root-search source opening coverage drift")
    if source["adjacency_truth"].get("status") != "unresolved" or source["adjacency_truth"].get("entrance_opening_id") is not None:
        raise ValueError("root-search source graph was already promoted")

    deny = build_demoted_portal_deny(source, source_path)
    validate_demoted_portal_deny(source, source_path, deny)
    unit_scope = validate_op001_unit_scope_candidate(source, _read_json(op001_scope_path))
    if (
        unit_scope.get("building_scope_fact", {}).get("intersects_confirmed_outer_boundary") is not False
        or unit_scope.get("building_scope_fact", {}).get("building_exterior_root_confirmation") is not False
        or unit_scope.get("unit_scope_hypothesis", {}).get("unit_root_candidate") is not True
        or unit_scope.get("unit_scope_confirmation") is not False
    ):
        raise ValueError("OP001 unit-scope/root boundary drift")

    rows: list[dict[str, Any]] = []
    for opening in source_openings:
        opening_id = opening["id"]
        scans = []
        for segment_input in _segment_inputs(opening):
            scan = classify_segment_against_outer_boundary(
                segment_input["segment_m"],
                outer,
            )
            scans.append({**segment_input, "geometry": scan})
        primary = scans[0]["geometry"]
        denied = opening_id == PORTAL_ID
        any_actual_crossing = any(scan["geometry"]["actual_crossing"] for scan in scans)
        classification = "hard-denied-portal" if denied else primary["classification"]
        row = {
            "opening_id": opening_id,
            "source_record_state": {
                "opening_status": opening["status"],
                "build_disposition": opening["build_disposition"],
                "build_kind": opening["build_kind"],
                "source_observation_kind": opening["source_observation"]["kind"],
                "source_observation_status": opening["source_observation"]["status"],
                "record_fields_are_root_confirmations": False,
            },
            "segment_scans": scans,
            "primary_segment_source": scans[0]["source"],
            "primary_classification": classification,
            "primary_minimum_boundary_distance_m": primary["minimum_boundary_distance_m"],
            "any_actual_crossing": any_actual_crossing,
            "hard_denied": denied,
            "deny_candidate_hash": deny["candidate_hash"] if denied else None,
            "geometric_boundary_crossing_candidate": False,
            "root_candidate": False,
            "root_confirmation": False,
            "entrance_confirmation": False,
            "outside_side_confirmation": False,
            "traversability_confirmation": False,
            "pair_confirmation": False,
            "adjacency_confirmation": False,
            "source_correction_authorized": False,
            "semantic_promotion": False,
            "score_effect": "none",
            "build_authorized": False,
            "ready": False,
            "human_readable_boundary": {
                "geometric_contact_is_not_root_confirmation": True,
                "source_label_is_not_root_confirmation": True,
                "near_miss_is_not_boundary_contact": True,
                "root_requires_bounded_outside_inside_and_traversability": True,
            },
        }
        row["candidate_hash"] = _candidate_hash(_without_hash(row))
        rows.append(row)

    rows_by_id = {row["opening_id"]: row for row in rows}
    near_miss_ids = [
        row["opening_id"] for row in rows if row["primary_classification"] == "near-miss"
    ]
    boundary_touch_ids = [
        row["opening_id"] for row in rows if row["primary_classification"] == "boundary-touch"
    ]
    actual_crossing_ids = [
        row["opening_id"] for row in rows if row["any_actual_crossing"] and not row["hard_denied"]
    ]
    result = {
        "schema": "exterior-root-search-candidate-v2",
        "source_structure_hash": source["structure_hash"],
        "source_document_sha256": _file_hash(source_path),
        "outer_boundary": {
            "status": outer_record["status"],
            "polygon_m": deepcopy(outer_record["polygon_m"]),
            "polygon_hash": _candidate_hash(outer_record["polygon_m"]),
            "edge_count": len(_outer_edges(outer)),
        },
        "geometry_policy": {
            "engine": "shapely",
            "topology_tolerance_m": TOPOLOGY_TOLERANCE_M,
            "near_miss_threshold_m": NEAR_MISS_THRESHOLD_M,
            "actual_crossing_definition": "positive inside-or-boundary length and positive outside length with boundary intersection",
            "near_miss_is_root_evidence": False,
        },
        "input_bindings": {
            "demoted_portal_deny_schema": deny["schema"],
            "demoted_portal_deny_candidate_hash": deny["candidate_hash"],
            "op001_unit_scope_file_sha256": _file_hash(op001_scope_path),
            "op001_unit_scope_candidate_hash": unit_scope["candidate_hash"],
        },
        "source_graph_root_state": {
            "adjacency_status": source["adjacency_truth"]["status"],
            "entrance_opening_id": source["adjacency_truth"]["entrance_opening_id"],
        },
        "opening_ids": list(source_ids),
        "opening_count": len(rows),
        "openings": rows,
        "near_miss_ids": near_miss_ids,
        "boundary_touch_ids": boundary_touch_ids,
        "actual_crossing_ids": actual_crossing_ids,
        "hard_denied_ids": [PORTAL_ID],
        "special_summaries": {
            "OP001": {
                "classification": rows_by_id["OP001"]["primary_classification"],
                "minimum_boundary_distance_m": rows_by_id["OP001"]["primary_minimum_boundary_distance_m"],
                "building_exterior_root_confirmation": False,
                "unit_root_candidate": "hypothesis",
                "unit_root_confirmation": False,
                "entry_label_is_source_pixel_context_only": True,
            },
            "OP003": {
                "classification": rows_by_id["OP003"]["primary_classification"],
                "minimum_boundary_distance_m": rows_by_id["OP003"]["primary_minimum_boundary_distance_m"],
                "near_miss_is_not_crossing": True,
            },
            "OP009": {
                "classification": rows_by_id["OP009"]["primary_classification"],
                "minimum_boundary_distance_m": rows_by_id["OP009"]["primary_minimum_boundary_distance_m"],
                "balcony_facing_is_not_exterior_root": True,
            },
            "OP010": {
                "classification": rows_by_id["OP010"]["primary_classification"],
                "minimum_boundary_distance_m": rows_by_id["OP010"]["primary_minimum_boundary_distance_m"],
                "balcony_facing_is_not_exterior_root": True,
            },
        },
        "excluded_non_source_candidates": {
            "OP012": {
                "reason": "not present in current source opening records",
                "included_in_scan": False,
                "eligible_as_root": False,
            },
            "GATE": {
                "reason": "site/plot label, not an opening record",
                "included_in_scan": False,
                "eligible_as_root": False,
            },
        },
        "root_status": "needs_human_or_source_policy",
        "root_opening_id": None,
        "next_policy_gate": {
            "required": True,
            "reason": "no source opening segment crosses the confirmed building outer boundary",
            "allowed_options": [
                {
                    "id": "human_confirmed_in_frame_exterior_segment",
                    "risk_class": "evidence_required",
                    "result_authority": "candidate_until_full_geometry_and_traversability_gate",
                },
                {
                    "id": "human_authorized_unit_scope_root_policy",
                    "risk_class": "human_policy_required",
                    "result_authority": "unit_scope_only_not_building_exterior",
                },
                {
                    "id": "human_authorized_off_frame_root_hypothesis",
                    "risk_class": "human_policy_and_hypothesis_only",
                    "result_authority": "hypothesis_only_no_graph_edge",
                },
            ],
            "automatic_model_authority": False,
            "source_mutation_authorized": False,
            "score_change_authorized": False,
        },
        "any_geometric_boundary_crossing_candidate": False,
        "root_confirmation": False,
        "entrance_confirmation": False,
        "outside_side_confirmation": False,
        "traversability_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
        "reachability_confirmation": False,
        "source_correction_authorized": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _candidate_hash(_without_hash(result))
    return result if _skip_validate else validate(
        result,
        document=source,
        source_path=source_path,
        op001_scope_path=op001_scope_path,
    )


def validate(
    candidate: Mapping[str, Any],
    *,
    document: Mapping[str, Any] | None = None,
    source_path: Path = SOURCE,
    op001_scope_path: Path = OP001_SCOPE,
) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(
        document,
        source_path=Path(source_path),
        op001_scope_path=Path(op001_scope_path),
        _skip_validate=True,
    )
    if actual != expected:
        raise ValueError("exterior root search evidence/derivation drift")
    if (
        actual.get("schema") != "exterior-root-search-candidate-v2"
        or actual.get("opening_ids") != list(EXPECTED_SOURCE_OPENING_IDS)
        or actual.get("opening_count") != 12
        or actual.get("actual_crossing_ids") != []
        or actual.get("boundary_touch_ids") != []
        or actual.get("near_miss_ids") != ["OP003"]
        or actual.get("hard_denied_ids") != [PORTAL_ID]
        or actual.get("root_status") != "needs_human_or_source_policy"
        or actual.get("root_opening_id") is not None
        or actual.get("next_policy_gate", {}).get("required") is not True
        or actual.get("next_policy_gate", {}).get("automatic_model_authority") is not False
    ):
        raise ValueError("exterior root search current-source scope drift")
    _assert_fail_closed(actual, BATCH_FAIL_CLOSED, context="exterior root search")
    if actual.get("candidate_hash") != _candidate_hash(_without_hash(actual)):
        raise ValueError("exterior root search candidate hash drift")

    rows_by_id = {row["opening_id"]: row for row in actual["openings"]}
    if len(rows_by_id) != 12:
        raise ValueError("exterior root search duplicate opening row")
    for row in actual["openings"]:
        _assert_fail_closed(row, ROW_FAIL_CLOSED, context=f"{row['opening_id']} root-search row")
        if (
            row.get("candidate_hash") != _candidate_hash(_without_hash(row))
            or row.get("any_actual_crossing") is not False
            or row.get("human_readable_boundary", {}).get("geometric_contact_is_not_root_confirmation") is not True
            or row.get("human_readable_boundary", {}).get("source_label_is_not_root_confirmation") is not True
            or row.get("human_readable_boundary", {}).get("near_miss_is_not_boundary_contact") is not True
            or row.get("human_readable_boundary", {}).get("root_requires_bounded_outside_inside_and_traversability") is not True
            or not row.get("segment_scans")
        ):
            raise ValueError(f"{row['opening_id']} root-search row authority/hash drift")
    if (
        rows_by_id["OP001"]["primary_classification"] != "internal"
        or rows_by_id["OP001"]["primary_minimum_boundary_distance_m"] != 3.432346
        or rows_by_id["OP003"]["primary_classification"] != "near-miss"
        or rows_by_id["OP003"]["primary_minimum_boundary_distance_m"] != 0.014637
        or rows_by_id["OP009"]["primary_classification"] != "internal"
        or rows_by_id["OP010"]["primary_classification"] != "internal"
        or rows_by_id[PORTAL_ID]["primary_classification"] != "hard-denied-portal"
        or rows_by_id[PORTAL_ID]["hard_denied"] is not True
        or actual["excluded_non_source_candidates"]["OP012"]["included_in_scan"] is not False
        or actual["excluded_non_source_candidates"]["GATE"]["included_in_scan"] is not False
    ):
        raise ValueError("exterior root search special-case drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--op001-scope", type=Path, default=OP001_SCOPE)
    parser.add_argument("--output", type=Path, default=OUT / "candidate.json")
    args = parser.parse_args(argv)
    result = build(source_path=args.source, op001_scope_path=args.op001_scope)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output.parent / "REPORT.md").write_text(
        "# Exterior-root search candidate v2\n\n"
        "All 12 current source opening records were scanned against the confirmed building outer boundary, including "
        "nominal segments and the OP001/OP002 effective-void segment variants. No actual boundary crossing exists. "
        "OP001 is internal and remains only a unit-root hypothesis; OP009/OP010 are internal balcony-facing candidates. "
        "OP003 lies 0.014637 m inside the west outer boundary and is reported as a near-miss, not a touch, crossing, or "
        "root. The 0.1 m near-miss threshold is only a review-priority threshold; exact topology uses 1e-9 m. The "
        "demoted portal is hard-denied, OP012 is absent from source opening records, and GATE is a site/plot label. "
        "The next choices are explicitly risk-classed: in-frame evidence still needs a full geometry/traversability "
        "gate, unit-scope policy is not building-exterior authority, and off-frame policy remains hypothesis-only. "
        "A human/source policy decision is required before any root edge can be confirmed. No model output may supply "
        "that authority, and no source, score, adjacency, reachability, Blender, or IFC state is changed.\n",
        encoding="utf-8",
    )
    (args.output.parent / "REVIEW_CARD_ZH.md").write_text(
        "# 外部入口搜索候选 v2\n\n"
        "已把当前源文件中的 12 条 opening record 全部与已确认的楼栋外边界做精确几何扫描，同时检查 "
        "OP001/OP002 的 nominal 与 effective-void 线段变体。结果没有任何实际穿越外边界的线段。OP001 是"
        "内部界面，只能保留单位入口假设；OP009/OP010 仍是内部阳台方向候选。OP003 距西侧外边界 "
        "0.014637 m，被列为 near-miss 供复核，但它没有接触或穿越边界，也不是 root。0.1 m 只是复核优先级"
        "阈值，精确拓扑容差为 1e-9 m。被降级 portal 继续硬拒绝，OP012 不在当前源 opening 集合，GATE 是"
        "场地/地块标记。下一步必须取得人工或源策略授权；任何 AI 视觉结论都不能自动确认 root。本候选不修改"
        "源文件、评分、邻接、可达性，也不授权 Blender/IFC 正式建模。三个选择已分别标明风险：图内证据仍需"
        "完整几何/通行闸门，单位范围策略不等于楼栋外部权限，画外策略只能保持假设。\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BATCH_FAIL_CLOSED",
    "EXPECTED_SOURCE_OPENING_IDS",
    "NEAR_MISS_THRESHOLD_M",
    "ROW_FAIL_CLOSED",
    "TOPOLOGY_TOLERANCE_M",
    "_candidate_hash",
    "build",
    "classify_segment_against_outer_boundary",
    "validate",
]
