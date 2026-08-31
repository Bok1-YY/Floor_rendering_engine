"""Convert a reviewed Goal-Loop proposal into a v2 evidence document.

The adapter is intentionally one-way and non-promoting: every imported wall,
space, opening and adjacency remains candidate/unresolved.  It never emits a
build-ready document or reuses a Blender artifact.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.v2_contract import (  # noqa: E402
    assess_v2_build_readiness,
    compute_v2_structure_hash,
    validate_v2_document,
)
from tools.goal_loop_v2.common import atomic_write_json, read_json  # noqa: E402


def _lerp(a: list[float], b: list[float], t: float) -> list[float]:
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]


def _segment_parameter(point: list[float], start: list[float], end: list[float]) -> tuple[float, float]:
    dx, dy = end[0] - start[0], end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator <= 1e-18:
        return 0.0, math.inf
    t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator
    projection = _lerp(start, end, t)
    return t, math.dist(point, projection)


def _metric(matrix: list[list[float]], point: list[float]) -> list[float]:
    return [
        matrix[0][0] * point[0] + matrix[0][1] * point[1] + matrix[0][2],
        matrix[1][0] * point[0] + matrix[1][1] * point[1] + matrix[1][2],
    ]


def _source_document(proposal: Mapping[str, Any], orientation: Mapping[str, Any]) -> dict[str, Any]:
    original = orientation["original_jpeg"]
    canonical = orientation["canonical_visible"]
    transform = orientation["transforms"]["canonical_pixel_to_metric_3x3"]
    width, height = canonical["size"]
    view_id = "VIEW-CANONICAL"

    anchors: list[dict[str, Any]] = []
    for raw in proposal["source"]["anchors"]:
        points_px = raw.get("points_raw_px")
        if not points_px:
            points_norm = raw.get("points_norm") or []
            points_px = [[point[0] / 1000.0 * width, point[1] / 1000.0 * height] for point in points_norm]
        kind = raw["kind"] if raw["kind"] in {"scale", "space", "entrance", "opening"} else "fixed_feature"
        anchors.append({
            "id": raw["anchor_id"],
            "kind": kind,
            "geometry": {"space": "canonical_px", "primitive": "point" if len(points_px) == 1 else "segment", "points_px": points_px},
            "measured_distance_mm": raw.get("distance_mm") if kind == "scale" else None,
            "status": "source_candidate",
            "evidence_asset_id": view_id,
            "note": str(raw.get("source") or raw.get("status") or "proposal evidence"),
        })

    frame = proposal["source"]["coordinate_system"]["registration_frame_px"]
    controls_px = [[frame["left"], frame["bottom"]], [frame["right"], frame["bottom"]], [frame["left"], frame["top"]]]
    controls = [
        {"id": f"CONTROL-{index+1}", "canonical_px": point, "metric_m": _metric(transform, point), "evidence_refs": [view_id]}
        for index, point in enumerate(controls_px)
    ]
    scale_anchor = next(anchor["id"] for anchor in anchors if anchor["kind"] == "scale")
    return {
        "schema": "source-provenance-v3",
        "original": {"file_sha256": original["sha256"], "pixel_sha256": orientation["decoded_raw_pixels"]["sha256"], "size_px": original["raw_size"], "exif_orientation": original["exif_orientation"]},
        "canonical": {"file_sha256": canonical["file_sha256"], "pixel_sha256": canonical["raw_pixel_sha256"], "size_px": canonical["size"], "orientation_policy": orientation["policy"]["selected"], "raw_to_canonical_3x3": orientation["transforms"]["raw_pixel_to_canonical_pixel_3x3"]},
        "views": [{"id": view_id, "role": "normalized_evidence", "file_sha256": canonical["file_sha256"], "pixel_sha256": canonical["raw_pixel_sha256"], "size_px": canonical["size"], "canonical_to_view_3x3": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}],
        "metric_registration": {"model": "affine-2d", "solver": "exact", "canonical_px_to_metric_3x3": transform, "control_points": controls, "max_residual_m": 0.0, "tolerance_m": 0.001, "scale_anchor_id": scale_anchor},
        "anchors": anchors,
    }


def _wall_document(proposal: Mapping[str, Any]) -> dict[str, Any]:
    source_walls = list(proposal["wall_graph"]["walls"])
    source_junctions = list(proposal["wall_graph"]["junctions"])
    endpoint_diagnostics = {(row["wall_id"], int(row["endpoint_index"])): row for row in proposal["wall_graph"]["endpoint_diagnostics"]}
    branch_by_id: dict[str, dict[str, Any]] = {}
    atom_rows: list[dict[str, Any]] = []
    node_rows: dict[str, dict[str, Any]] = {}
    assumption_id = "ASSUME-Z-RESEARCH"

    for wall in source_walls:
        wall_id = wall["id"]
        start, end = wall["proposed_centerline_m"]
        branch_by_id[wall_id] = {"id": wall_id, "centerline_m": wall["proposed_centerline_m"], "status": "candidate", "evidence_refs": ["VIEW-CANONICAL"]}
        cuts: list[tuple[float, str | None]] = [(0.0, None), (1.0, None)]
        for junction in source_junctions:
            if wall_id not in junction["incident_wall_ids"]:
                continue
            t, distance = _segment_parameter(junction["point_m"], start, end)
            if -1e-6 <= t <= 1 + 1e-6 and distance <= 0.05:
                cuts.append((max(0.0, min(1.0, t)), junction["id"]))
        dedup: list[tuple[float, str | None]] = []
        for t, junction_id in sorted(cuts):
            if dedup and abs(t - dedup[-1][0]) <= 1e-8:
                if junction_id:
                    dedup[-1] = (dedup[-1][0], junction_id)
            else:
                dedup.append((t, junction_id))

        node_ids: list[str] = []
        for index, (t, junction_id) in enumerate(dedup):
            if junction_id:
                node_id = junction_id
            else:
                node_id = f"NODE-{wall_id}-{'START' if index == 0 else 'END'}"
            node_ids.append(node_id)
            if node_id not in node_rows:
                point = _lerp(start, end, t)
                diagnostic = endpoint_diagnostics.get((wall_id, 0 if index == 0 else 1)) if not junction_id else None
                attachment = "free_end"
                termination = "unresolved"
                if diagnostic and diagnostic.get("wall_face_support_candidates"):
                    attachment, termination = "wall_face", "source_termination"
                source_junction = next((row for row in source_junctions if row["id"] == junction_id), None)
                node_rows[node_id] = {
                    "id": node_id,
                    "kind": source_junction["kind"] if source_junction else "endpoint",
                    "axis_point_m": source_junction["point_m"] if source_junction else point,
                    "termination_kind": None if source_junction else termination,
                    "incidents": [],
                    "solid_union_policy": "union" if source_junction else "face_abutment" if attachment == "wall_face" else "cap",
                    "status": "candidate" if source_junction or attachment == "wall_face" else "unresolved",
                    "evidence_refs": ["VIEW-CANONICAL"],
                    "_attachment": attachment,
                }

        for index in range(len(dedup) - 1):
            t0, t1 = dedup[index][0], dedup[index + 1][0]
            atom_id = f"ATOM-{wall_id}-{index+1:02d}"
            atom_rows.append({
                "id": atom_id, "branch_id": wall_id, "branch_interval": [t0, t1],
                "centerline_m": [_lerp(start, end, t0), _lerp(start, end, t1)],
                "thickness_m": float(wall.get("nominal_thickness_m") or wall.get("measured_thickness_m") or 0.12),
                "base_m": 0.0, "height_m": float(wall.get("height_m") or 2.8),
                "left_space_id": None, "right_space_id": None,
                "start_node_id": node_ids[index], "end_node_id": node_ids[index + 1],
                "status": "candidate", "evidence_refs": ["VIEW-CANONICAL"], "assumption_ids": [assumption_id],
            })
            for end_name, node_id in (("start", node_ids[index]), ("end", node_ids[index + 1])):
                node = node_rows[node_id]
                node["incidents"].append({"atom_id": atom_id, "end": end_name, "role": "through" if node["kind"] in {"T", "X", "continuation"} else "terminating", "attachment": "axis" if node["kind"] != "endpoint" else node["_attachment"], "contact_point_m": deepcopy(node["axis_point_m"])})

    for node in node_rows.values():
        node.pop("_attachment", None)
    return {"version": "atomic-wall-junction-graph-v2", "branches": list(branch_by_id.values()), "atoms": atom_rows, "junctions": list(node_rows.values())}


def convert_proposal_to_v2(proposal: Mapping[str, Any], orientation: Mapping[str, Any]) -> dict[str, Any]:
    source = _source_document(proposal, orientation)
    spaces = [{"id": row["id"], "label": row["label"], "point_m": row["representative_point_m"], "status": "candidate", "evidence_refs": ["VIEW-CANONICAL"]} for row in proposal["spaces"]]
    openings = []
    for row in proposal["openings"]:
        anchor_id = f"ANCHOR-{row['id']}"
        source_kind = str(row.get("source_kind") or "unknown")
        if source_kind not in {"entrance_symbol", "door", "window", "glazed_access_door", "glazed_interface", "open_passage", "unknown"}:
            source_kind = "unknown"
        unresolved_observation = not row.get("contract_kind_candidate") and source_kind in {"glazed_interface", "unknown"}
        openings.append({
            "id": row["id"],
            "source_observation": {"kind": "entrance_symbol" if row.get("contract_kind_candidate") == "entrance" else source_kind, "nominal_segment_m": row["source_segment_m"], "nominal_width_m": float(row["nominal_width_m"]), "anchor_id": anchor_id, "evidence_refs": ["VIEW-CANONICAL"], "status": "unresolved" if unresolved_observation else "candidate"},
            "build_disposition": "exclude_pending_resolution", "build_kind": None,
            "owning_wall_atom_id": None, "effective_void": None, "swing_direction": None,
            "traversable": False, "side_a_space_id": None, "side_b_space_id": None,
            "jamb_before": None, "jamb_after": None,
            "status": "unresolved" if unresolved_observation else "candidate",
            "assumption_ids": ["ASSUME-Z-RESEARCH"],
        })

    semantic_edges = []
    for row in proposal["adjacency"]["semantic_edges"]:
        side_a, side_b = row["space_a_candidates"], row["space_b_candidates"]
        if len(side_a) == len(side_b) == 1:
            semantic_edges.append({"id": f"EDGE-{row['opening_id']}", "space_a_id": side_a[0], "space_b_id": side_b[0], "kind": "door", "opening_id": row["opening_id"], "status": "candidate", "evidence_refs": ["VIEW-CANONICAL"]})

    issues = []
    for blocker in proposal["stable_blockers"]:
        blocker_id = blocker["id"]
        issues.append({"id": blocker_id, "severity": "hard", "category": blocker["class"], "entity_refs": [], "status": "open", "message": blocker["message"], "blocks_reference_freeze": True, "blocks_build": True, "evidence_refs": ["VIEW-CANONICAL"]})

    document = {
        "schema": "research-structure-bundle-v2",
        "project": {"project_id": f"REFERENCE-{proposal['proposal_id']}", "revision": 1, "sample_id": f"SAMPLE-{proposal.get('sample_id') or proposal['proposal_id']}"},
        "source_hash": proposal["source"]["source_file_hash"], "structure_hash": "0" * 64,
        "source": source,
        "outer_boundary": {"polygon_m": proposal["outer_boundary_candidate"]["metric_m"], "status": "candidate", "evidence_refs": ["VIEW-CANONICAL"]},
        "spaces": spaces,
        "wall_graph": _wall_document(proposal),
        "opening_contract": {"version": "opening-contract-v2", "minimum_jamb_support_m": 0.05, "openings": openings},
        "adjacency_truth": {"version": "adjacency-truth-v2", "status": "candidate", "entrance_opening_id": proposal["adjacency"].get("entrance_opening_id"), "edges": semantic_edges},
        "assumptions": {"schema": "assumption-registry-v2", "research_only": True, "items": [{"id": "ASSUME-Z-RESEARCH", "category": "z_geometry", "targets": [{"entity_kind": "bundle", "entity_id": None, "field": "default wall/opening heights"}], "value": {"wall_height_m": 2.8, "door_head_m": 2.1}, "unit": "m", "basis": "research_default", "status": "unverified", "build_policy": "allow_research_only", "evidence_refs": [], "disclosure": "The source plan does not certify Z dimensions; these values are reversible research assumptions."}]},
        "unresolved_issues": issues,
    }
    document["structure_hash"] = compute_v2_structure_hash(document)
    validate_v2_document(document)
    readiness = assess_v2_build_readiness(document)
    if readiness["ready"]:
        raise ValueError("proposal adapter must never promote unresolved evidence to build-ready")
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a reviewed proposal into a non-build-ready v2 evidence document.")
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--orientation", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--readiness-output", required=True, type=Path)
    args = parser.parse_args(argv)
    document = convert_proposal_to_v2(read_json(args.proposal), read_json(args.orientation))
    readiness = assess_v2_build_readiness(document)
    atomic_write_json(args.output, document)
    atomic_write_json(args.readiness_output, readiness)
    print(json.dumps({"document": str(args.output.resolve()), "readiness": readiness}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
