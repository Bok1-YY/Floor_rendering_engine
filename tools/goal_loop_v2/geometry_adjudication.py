"""Fail-closed geometry adjudication for v2 reference evidence.

The module resolves only geometric evidence issues. It never changes opening
semantics, adjacency, entity confirmation status, or build readiness.
"""

from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.v2_contract import (
    assess_v2_build_readiness,
    compute_v2_structure_hash,
    validate_v2_document,
)
from tools.fastloop_research.contract import canonical_json


TOLERANCE_M = 0.001
MINIMUM_SUPPORT_M = 0.05


def _point(value: Sequence[float]) -> tuple[float, float]:
    return float(value[0]), float(value[1])


def _distance_to_line(point, first, second) -> float:
    px, py = _point(point)
    ax, ay = _point(first)
    bx, by = _point(second)
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length <= 1e-12:
        return math.inf
    return abs((px - ax) * (-dy / length) + (py - ay) * (dx / length))


def _segment_intersection(first, second) -> tuple[float, float] | None:
    a, b = map(_point, first)
    c, d = map(_point, second)
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    denominator = rx * sy - ry * sx
    if abs(denominator) <= 1e-12:
        return None
    qx, qy = c[0] - a[0], c[1] - a[1]
    t = (qx * sy - qy * sx) / denominator
    u = (qx * ry - qy * rx) / denominator
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return a[0] + t * rx, a[1] + t * ry
    return None


def _wall_index(proposal: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(row["id"]): row for row in proposal["wall_graph"]["walls"]}


def proposal_content_hash(proposal: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(proposal)).hexdigest()


def _line_wall_solid_interval(point, direction, wall: Mapping[str, Any]) -> tuple[float, float] | None:
    """Return the parameter interval where a unit line crosses wall footprint."""

    a, b = map(_point, wall["proposed_centerline_m"])
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    tx, ty = dx / length, dy / length
    nx, ny = -ty, tx
    px, py = _point(point)
    ux, uy = direction
    coordinates = [((px - a[0]) * tx + (py - a[1]) * ty, ux * tx + uy * ty, 0.0, length), ((px - a[0]) * nx + (py - a[1]) * ny, ux * nx + uy * ny, -float(wall.get("nominal_thickness_m") or 0) * 0.5, float(wall.get("nominal_thickness_m") or 0) * 0.5)]
    lower, upper = -math.inf, math.inf
    for origin, slope, minimum, maximum in coordinates:
        if abs(slope) <= 1e-12:
            if origin < minimum - TOLERANCE_M or origin > maximum + TOLERANCE_M:
                return None
            continue
        first, second = (minimum - origin) / slope, (maximum - origin) / slope
        lower, upper = max(lower, min(first, second)), min(upper, max(first, second))
        if upper < lower - 1e-12:
            return None
    return lower, upper


def _support_is_cut(proposal: Mapping[str, Any], current_opening_id: str, supporting_wall_id: str, junction_point, sill: float, head: float) -> list[str]:
    cutters: list[str] = []
    for other in proposal.get("openings") or []:
        if other.get("id") == current_opening_id or other.get("owning_wall_id_candidate") != supporting_wall_id:
            continue
        void = other.get("effective_void")
        if not isinstance(void, Mapping) or len(void.get("segment_m") or []) != 2:
            continue
        other_sill = float(other.get("sill_m") or 0.0)
        other_head = other_sill + float(other.get("height_m") or 0.0)
        if min(head, other_head) <= max(sill, other_sill) + 1e-9:
            continue
        if _distance_point_to_segment(junction_point, *void["segment_m"]) <= MINIMUM_SUPPORT_M + TOLERANCE_M:
            cutters.append(str(other["id"]))
    return cutters


def _distance_point_to_segment(point, first, second) -> float:
    p, a, b = _point(point), _point(first), _point(second)
    dx, dy = b[0] - a[0], b[1] - a[1]
    denominator = dx * dx + dy * dy
    if denominator <= 1e-18:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / denominator))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def assess_wall_face_terminations(proposal: Mapping[str, Any]) -> dict[str, Any]:
    diagnostics = list((proposal.get("wall_graph") or {}).get("endpoint_diagnostics") or [])
    unresolved: list[dict[str, Any]] = []
    passed = 0
    for row in diagnostics:
        if row.get("junction_ids"):
            passed += 1
            continue
        candidates = list(row.get("wall_face_support_candidates") or [])
        valid = [candidate for candidate in candidates if candidate.get("continuous_at_1mm") is True and float(candidate.get("wall_face_distance_m", math.inf)) <= TOLERANCE_M]
        if row.get("status") == "wall_face_termination_candidate" and len(valid) == 1:
            passed += 1
        else:
            unresolved.append({
                "wall_id": row.get("wall_id"), "endpoint_index": row.get("endpoint_index"),
                "status": row.get("status"),
                "candidate_count": len(candidates),
                "best_face_distance_m": min((float(item.get("wall_face_distance_m", math.inf)) for item in candidates), default=None),
            })
    return {
        "check": "wall_face_termination_completeness",
        "outcome": "resolved" if diagnostics and not unresolved else "unresolved",
        "endpoint_count": len(diagnostics), "mechanically_supported_count": passed,
        "unresolved": unresolved,
        "tolerance_m": TOLERANCE_M,
    }


def assess_crossing_jambs(proposal: Mapping[str, Any]) -> list[dict[str, Any]]:
    walls = _wall_index(proposal)
    junctions = list((proposal.get("wall_graph") or {}).get("junctions") or [])
    results: list[dict[str, Any]] = []
    for opening in proposal.get("openings") or []:
        for side_name in ("jamb_before_support", "jamb_after_support"):
            support = opening.get(side_name) or {}
            if support.get("mode") != "crossing_wall_jamb":
                continue
            owner_id = opening.get("owning_wall_id_candidate")
            support_id = support.get("supporting_wall_id")
            owner, supporting = walls.get(owner_id), walls.get(support_id)
            reasons: list[str] = []
            intersection = _segment_intersection(owner["proposed_centerline_m"], supporting["proposed_centerline_m"]) if owner and supporting else None
            matching = [row for row in junctions if row.get("kind") in {"T", "X"} and {owner_id, support_id} <= set(row.get("incident_wall_ids") or [])]
            if not owner or not supporting:
                reasons.append("unknown owner/support wall")
            if intersection is None or len(matching) != 1 or math.dist(intersection, _point(matching[0]["point_m"])) > TOLERANCE_M:
                reasons.append("missing or inconsistent T/X junction")
            effective = opening.get("effective_void")
            if not isinstance(effective, Mapping) or effective.get("crossing_wall_jamb", {}).get("host_scoped_subtraction") is not True:
                reasons.append("effective void is not host-scoped")
            nested_crossing = effective.get("crossing_wall_jamb") if isinstance(effective, Mapping) else {}
            if nested_crossing.get("supporting_wall_id") != support_id:
                reasons.append("nested crossing support wall differs from jamb support identity")
            face_error = math.inf
            continuous_support_m = 0.0
            selected_endpoint = None
            cutters: list[str] = []
            if supporting and isinstance(effective, Mapping):
                segment = effective.get("segment_m") or []
                if len(segment) == 2:
                    endpoint_index = 0 if side_name == "jamb_before_support" else 1
                    selected_endpoint = _point(segment[endpoint_index])
                    direction_vector = (_point(segment[1])[0] - _point(segment[0])[0], _point(segment[1])[1] - _point(segment[0])[1])
                    direction_length = math.hypot(*direction_vector)
                    direction = (direction_vector[0] / direction_length, direction_vector[1] / direction_length) if direction_length > 1e-12 else (0.0, 0.0)
                    interval = _line_wall_solid_interval(selected_endpoint, direction, supporting) if direction_length > 1e-12 else None
                    if interval is not None:
                        face_error = min(abs(interval[0]), abs(interval[1]))
                        continuous_support_m = max(0.0, interval[1] - interval[0])
                if face_error > TOLERANCE_M:
                    reasons.append(f"declared {side_name} endpoint is not on supporting wall face")
                sill = float(opening.get("sill_m") or 0.0)
                head = sill + float(opening.get("height_m") or 0.0)
                support_base = float(supporting.get("base_m") or 0.0)
                support_top = support_base + float(supporting.get("height_m") or 0.0)
                if support_base > sill + TOLERANCE_M or support_top < head - TOLERANCE_M:
                    reasons.append("supporting wall does not cover opening sill/head")
                if continuous_support_m < MINIMUM_SUPPORT_M - 1e-9:
                    reasons.append("continuous crossing-wall support is below 50mm")
                if intersection is not None:
                    cutters = _support_is_cut(proposal, str(opening.get("id")), str(support_id), intersection, sill, head)
                    if cutters:
                        reasons.append("supporting wall protected region is cut by another opening")
            if len(matching) == 1 and nested_crossing.get("junction_kind") != matching[0].get("kind"):
                reasons.append("declared crossing junction kind differs from actual junction")
            result = {
                "check": "crossing_wall_jamb", "opening_id": opening.get("id"), "side": side_name,
                "outcome": "resolved" if not reasons else "unresolved", "owner_wall_id": owner_id,
                "supporting_wall_id": support_id, "junction_id": matching[0]["id"] if len(matching) == 1 else None,
                "selected_endpoint_m": list(selected_endpoint) if selected_endpoint else None,
                "face_error_m": face_error if math.isfinite(face_error) else None,
                "continuous_support_m": continuous_support_m, "minimum_support_m": MINIMUM_SUPPORT_M,
                "support_cut_by_opening_ids": cutters, "reasons": reasons,
            }
            results.append(result)
    return results


def assess_return_wall_faces(proposal: Mapping[str, Any]) -> list[dict[str, Any]]:
    walls = _wall_index(proposal)
    results: list[dict[str, Any]] = []
    for opening in proposal.get("openings") or []:
        for side_name in ("jamb_before_support", "jamb_after_support"):
            support = opening.get(side_name) or {}
            if support.get("mode") not in {"return_wall_face", "return_or_cross_wall_face_candidate"}:
                continue
            preferred = support.get("preferred_candidate_wall_id")
            candidates = list(support.get("candidates") or [])
            if not candidates and preferred:
                candidates = [{"wall_id": preferred, "wall_face_distance_m": math.inf, "continuous_at_1mm": False}]
            valid = []
            for candidate in candidates:
                wall = walls.get(candidate.get("wall_id"))
                if wall and candidate.get("continuous_at_1mm") is True and float(candidate.get("wall_face_distance_m", math.inf)) <= TOLERANCE_M and float(wall.get("nominal_thickness_m") or 0) >= MINIMUM_SUPPORT_M:
                    valid.append(candidate)
            results.append({
                "check": "return_wall_face", "opening_id": opening.get("id"), "side": side_name,
                "owner_wall_id": opening.get("owning_wall_id_candidate"),
                "supporting_wall_id": valid[0].get("wall_id") if len(valid) == 1 else preferred,
                "outcome": "resolved" if len(valid) == 1 else "unresolved",
                "candidate_count": len(candidates), "valid_candidate_count": len(valid),
                "best_face_distance_m": min((float(item.get("wall_face_distance_m", math.inf)) for item in candidates), default=None),
                "tolerance_m": TOLERANCE_M, "minimum_support_m": MINIMUM_SUPPORT_M,
            })
    return results


def adjudicate_geometry(document: Mapping[str, Any], proposal: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_v2_document(document)
    expected_request_keys = {"schema", "source_structure_hash", "proposal_content_hash", "adjudication_authority", "verdict", "build_authorized", "bindings"}
    if not isinstance(request, Mapping) or set(request) != expected_request_keys or request.get("schema") != "geometry-adjudication-request-v1":
        raise ValueError("invalid geometry adjudication request")
    if request["source_structure_hash"] != validated["structure_hash"] or request["proposal_content_hash"] != proposal_content_hash(proposal):
        raise ValueError("geometry adjudication request is not bound to document/proposal")
    if request["adjudication_authority"] != "independent_reference_reviewer" or request["verdict"] != "authorize_geometry_check_only" or request["build_authorized"] is not False:
        raise ValueError("geometry adjudication request lacks non-build independent authority")
    issues = {row["id"]: row for row in validated["unresolved_issues"]}
    bindings = request["bindings"]
    if not isinstance(bindings, list) or len({row.get("issue_id") for row in bindings if isinstance(row, Mapping)}) != len(bindings):
        raise ValueError("geometry adjudication request bindings must be unique")
    endpoint = assess_wall_face_terminations(proposal)
    crossing = assess_crossing_jambs(proposal)
    returns = assess_return_wall_faces(proposal)
    decisions: list[dict[str, Any]] = []
    for binding in bindings:
        expected_binding_keys = {"issue_id", "check", "opening_id", "jamb_side", "owner_wall_id", "supporting_wall_id"}
        if not isinstance(binding, Mapping) or set(binding) != expected_binding_keys:
            raise ValueError("invalid geometry adjudication binding")
        issue = issues.get(binding["issue_id"])
        if not issue or issue["status"] != "open":
            raise ValueError("geometry adjudication binding references unknown/non-open issue")
        compatible_categories = {
            "wall_face_termination_completeness": {"wall_graph"},
            "crossing_wall_jamb": {"opening_contract_capability"},
            "return_wall_face": {"source_ambiguity"},
        }
        if binding["check"] not in compatible_categories or issue["category"] not in compatible_categories[binding["check"]]:
            raise ValueError("geometry adjudication check is incompatible with issue category")
        if binding["check"] == "wall_face_termination_completeness":
            if any(binding[key] is not None for key in ("opening_id", "jamb_side", "owner_wall_id", "supporting_wall_id")):
                raise ValueError("wall-face completeness binding must not carry opening identity")
            evidence = endpoint
        elif binding["check"] == "crossing_wall_jamb":
            matches = [row for row in crossing if row.get("opening_id") == binding["opening_id"] and row.get("side") == binding["jamb_side"] and row.get("owner_wall_id") == binding["owner_wall_id"] and row.get("supporting_wall_id") == binding["supporting_wall_id"]]
            if len(matches) != 1:
                raise ValueError("crossing binding identity does not select exactly one check")
            evidence = matches[0]
        elif binding["check"] == "return_wall_face":
            matches = [row for row in returns if row.get("opening_id") == binding["opening_id"] and row.get("side") == binding["jamb_side"] and row.get("owner_wall_id") == binding["owner_wall_id"] and row.get("supporting_wall_id") == binding["supporting_wall_id"]]
            if len(matches) != 1:
                raise ValueError("return binding identity does not select exactly one check")
            evidence = matches[0]
        else:
            raise ValueError("unsupported geometry adjudication check")
        decisions.append({"issue_id": issue["id"], "outcome": evidence["outcome"], "evidence": evidence})
    return {
        "schema": "research-geometry-adjudication-v1", "source_structure_hash": validated["structure_hash"],
        "request_hash": hashlib.sha256(canonical_json(request)).hexdigest(),
        "decisions": decisions, "wall_face_terminations": endpoint,
        "crossing_jambs": crossing, "return_wall_faces": returns,
    }


def apply_resolved_geometry_decisions(
    document: Mapping[str, Any],
    proposal: Mapping[str, Any],
    request: Mapping[str, Any],
    report: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    original = validate_v2_document(document)
    recomputed = adjudicate_geometry(original, proposal, request)
    core_keys = {"schema", "source_structure_hash", "request_hash", "decisions", "wall_face_terminations", "crossing_jambs", "return_wall_faces"}
    if not isinstance(report, Mapping) or any(report.get(key) != recomputed.get(key) for key in core_keys):
        raise ValueError("geometry adjudication report differs from independent recomputation")
    decision_ids = [row.get("issue_id") for row in report.get("decisions") or [] if isinstance(row, Mapping)]
    if len(decision_ids) != len(set(decision_ids)) or not set(decision_ids) <= {row["issue_id"] for row in request["bindings"]}:
        raise ValueError("geometry adjudication report decisions are duplicate or outside request")
    result = deepcopy(original)
    resolved = {row["issue_id"] for row in report.get("decisions") or [] if row.get("outcome") == "resolved"}
    for issue in result["unresolved_issues"]:
        if issue["id"] in resolved:
            issue.update(status="resolved", blocks_reference_freeze=False, blocks_build=False)
    # Geometry adjudication may close issue records only. It cannot mutate
    # source observations, opening semantics, adjacency or entity statuses.
    if result["opening_contract"] != original["opening_contract"] or result["adjacency_truth"] != original["adjacency_truth"]:
        raise ValueError("geometry adjudication attempted a semantic mutation")
    result["structure_hash"] = compute_v2_structure_hash(result)
    result = validate_v2_document(result)
    readiness = assess_v2_build_readiness(result)
    return result, readiness


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", required=True, type=Path)
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--output-document", required=True, type=Path)
    args = parser.parse_args(argv)
    document = json.loads(args.document.read_text(encoding="utf-8"))
    proposal = json.loads(args.proposal.read_text(encoding="utf-8"))
    request = json.loads(args.request.read_text(encoding="utf-8"))
    report = adjudicate_geometry(document, proposal, request)
    adjudicated, readiness = apply_resolved_geometry_decisions(document, proposal, request, report)
    report["inputs"] = {"document": os.fspath(args.document.resolve()), "document_sha256": _sha256(args.document), "proposal": os.fspath(args.proposal.resolve()), "proposal_sha256": _sha256(args.proposal), "request": os.fspath(args.request.resolve()), "request_sha256": _sha256(args.request)}
    report["adjudicated_structure_hash"] = adjudicated["structure_hash"]
    report["readiness"] = readiness
    args.report.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    args.output_document.write_text(json.dumps(adjudicated, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": os.fspath(args.report), "output_document": os.fspath(args.output_document), "decisions": report["decisions"], "ready": readiness["ready"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
