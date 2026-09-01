"""Read-only prototype for room-face candidates from the 1308 wall graph.

This script writes no project state.  It compares two deterministic routes:
1. polygonize the noded wall centerlines;
2. subtract buffered wall solids from the confirmed outer boundary.

The result is diagnostic/candidate evidence only.  It does not confirm room
polygons, adjacency, traversability, score, or build authorization.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import networkx as nx
from shapely import get_parts, set_precision
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize_full, unary_union


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"


def _graph_metrics(geometry):
    graph = nx.Graph()
    for part in get_parts(geometry):
        coords = list(part.coords)
        for start, end in zip(coords, coords[1:]):
            graph.add_edge(tuple(start), tuple(end))
    components = nx.number_connected_components(graph) if graph.number_of_nodes() else 0
    return {
        "vertex_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "component_count": components,
        "cycle_rank": graph.number_of_edges() - graph.number_of_nodes() + components,
        "degree_1_count": sum(degree == 1 for _, degree in graph.degree()),
        "degree_3_plus_count": sum(degree >= 3 for _, degree in graph.degree()),
    }


def _centerline_diagnostics(lines):
    unioned = unary_union(lines)
    rows = []
    for grid_m in (0.0, 1e-6, 1e-4, 1e-3, 0.005, 0.01, 0.02, 0.05, 0.1):
        geometry = set_precision(unioned, grid_m) if grid_m else unioned
        polygons, cuts, dangles, invalid = polygonize_full(geometry)
        rows.append(
            {
                "precision_grid_m": grid_m,
                "polygon_count": len(list(get_parts(polygons))),
                "cut_count": len(list(get_parts(cuts))),
                "dangle_count": len(list(get_parts(dangles))),
                "invalid_ring_count": len(list(get_parts(invalid))),
                **_graph_metrics(geometry),
            }
        )
    return rows


def _solid_complement(atoms, spaces, outer, cap_style, thickness_scale):
    solids = unary_union(
        [
            LineString(atom["centerline_m"]).buffer(
                float(atom["thickness_m"]) * thickness_scale / 2.0,
                cap_style=cap_style,
                join_style=2,
            )
            for atom in atoms
        ]
    )
    faces = sorted(
        (part for part in get_parts(outer.difference(solids)) if part.geom_type == "Polygon"),
        key=lambda face: (-face.area, face.bounds),
    )
    groups = []
    for index, face in enumerate(faces):
        anchors = sorted(
            space["id"] for space in spaces if face.contains(Point(space["point_m"]))
        )
        groups.append(
            {
                "candidate_index": index,
                "area_m2": round(face.area, 9),
                "bounds_m": [round(value, 9) for value in face.bounds],
                "space_anchor_ids": anchors,
            }
        )
    return {
        "cap_style": {2: "flat", 3: "square"}[cap_style],
        "thickness_scale": thickness_scale,
        "candidate_face_count": len(faces),
        "anchor_bearing_face_count": sum(bool(group["space_anchor_ids"]) for group in groups),
        "empty_face_count": sum(not group["space_anchor_ids"] for group in groups),
        "multi_anchor_faces": [
            group for group in groups if len(group["space_anchor_ids"]) > 1
        ],
        "faces": groups,
    }


def main():
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    atoms = payload["wall_graph"]["atoms"]
    branches = payload["wall_graph"]["branches"]
    junctions = payload["wall_graph"]["junctions"]
    spaces = payload["spaces"]
    outer = Polygon(payload["outer_boundary"]["polygon_m"])
    lines = [LineString(atom["centerline_m"]) for atom in atoms]

    result = {
        "schema": "room-face-polygonization-research-v1",
        "source": {
            "path": str(SOURCE),
            "file_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            "structure_hash": payload["structure_hash"],
            "branch_count": len(branches),
            "atom_count": len(atoms),
            "junction_count": len(junctions),
            "space_anchor_count": len(spaces),
        },
        "centerline_polygonize": _centerline_diagnostics(lines),
        "solid_complement_nominal": [
            _solid_complement(atoms, spaces, outer, 2, 1.0),
            _solid_complement(atoms, spaces, outer, 3, 1.0),
        ],
        "solid_complement_sensitivity": [
            _solid_complement(atoms, spaces, outer, cap_style, scale)
            for cap_style in (2, 3)
            for scale in (0.75, 0.9, 0.95, 0.99, 1.0, 1.01, 1.05, 1.1, 1.25)
        ],
        "limitations": {
            "room_polygon_confirmation": False,
            "room_adjacency": False,
            "traversability": False,
            "semantic_promotion": False,
            "score_effect": "none",
            "build_authorized": False,
            "ready": False,
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
