"""Fail-closed OP001 entrance-symbol adjudication candidate."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.opening_side_candidates import build_opening_side_space_candidate, validate_opening_side_space_candidate
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids, validate_target_aware_wall_solids

SCHEMA = "op001-entrance-symbol-adjudication-candidate-v1"
BLOCKERS = [
    "INDEPENDENT_HOST_OWNERSHIP_UNPROVEN",
    "INDEPENDENT_CLOSED_WALL_BREAK_UNPROVEN",
    "INDEPENDENT_EFFECTIVE_VOID_UNPROVEN",
    "SOURCE_SIDE_SPACES_MISSING",
    "TRAVERSABILITY_UNCONFIRMED",
    "OUTER_BOUNDARY_INTERSECTION_MISSING",
    "EXTERIOR_SIDE_REGION_MISSING",
    "ENTRANCE_ROOT_UNPROVEN",
    "SOURCE_STATUS_CANDIDATE",
    "Z_DIMENSIONS_ASSUMED_RESEARCH_ONLY",
    "GEMINI_REVIEW_MISSING",
    "HUMAN_REVIEW_PENDING",
]


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _file_binding(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    raw = resolved.read_bytes()
    return {
        "path": str(resolved),
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "canonical_sha256": _hash(json.loads(raw.decode("utf-8"))),
    }


def _artifact_binding(evidence_file: Path, artifact: Mapping[str, Any]) -> dict[str, Any]:
    base = evidence_file.resolve().parent
    declared = Path(str(artifact["path"]))
    actual = next((p for p in (base / declared.name, declared) if p.is_file()), None)
    if actual is None:
        raise ValueError(f"OP001 artifact missing: {declared.name}")
    raw = actual.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != artifact.get("sha256"):
        raise ValueError(f"OP001 artifact hash drift: {declared.name}")
    return {"filename": actual.name, "bytes": len(raw), "sha256": digest}


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segment_intersects(a, b, c, d, tolerance=1e-9):
    values = (_cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b))
    return values[0] * values[1] <= tolerance and values[2] * values[3] <= tolerance


def _point_in_polygon(point, polygon):
    inside = False
    j = len(polygon) - 1
    for i, current in enumerate(polygon):
        previous = polygon[j]
        if ((current[1] > point[1]) != (previous[1] > point[1])) and point[0] < (
            (previous[0] - current[0]) * (point[1] - current[1]) / (previous[1] - current[1]) + current[0]
        ):
            inside = not inside
        j = i
    return inside


def _boundary_relation(segment, polygon):
    edges = list(zip(polygon, polygon[1:] + polygon[:1]))
    intersections = [index for index, (a, b) in enumerate(edges) if _segment_intersects(segment[0], segment[1], a, b)]
    return {
        "outer_boundary_status": "confirmed",
        "intersects_outer_boundary": bool(intersections),
        "intersected_edge_indices": intersections,
        "endpoints_inside_confirmed_footprint": [_point_in_polygon(point, polygon) for point in segment],
        "outside_side_region_confirmed": False,
        "exterior_entrance_root_confirmed": False,
    }


def build_op001_adjudication(document: Mapping[str, Any], evidence_file, side_candidate, wall_candidate, *, _skip_validate=False):
    doc = validate_v21_document(document)
    evidence_path = Path(evidence_file)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    side = validate_opening_side_space_candidate(doc, dict(side_candidate))
    wall = validate_target_aware_wall_solids(doc, dict(wall_candidate))
    source = next(x for x in doc["opening_contract"]["openings"] if x["id"] == "OP001")
    side_row = next(x for x in side["openings"] if x["opening_id"] == "OP001")
    if evidence.get("schema") != "op001-entrance-evidence-v1" or evidence.get("opening_id") != "OP001":
        raise ValueError("OP001 evidence schema drift")
    if evidence.get("source_sha256") != doc["source"]["canonical"]["file_sha256"]:
        raise ValueError("OP001 source image drift")
    if evidence.get("source_segment_m") != source["source_observation"]["nominal_segment_m"]:
        raise ValueError("OP001 source segment drift")
    artifacts = evidence["artifacts"]
    boundary = _boundary_relation(evidence["source_segment_m"], doc["outer_boundary"]["polygon_m"])
    result = {
        "schema": SCHEMA,
        "source_structure_hash": doc["structure_hash"],
        "evidence_binding": _file_binding(evidence_path),
        "opening_side_candidate_hash": side["candidate_hash"],
        "target_aware_wall_candidate_hash": wall["candidate_hash"],
        "opening_id": "OP001",
        "source_snapshot": {
            "status": source["status"],
            "observation_status": source["source_observation"]["status"],
            "kind": source["source_observation"]["kind"],
            "build_kind": source["build_kind"],
            "build_disposition": source["build_disposition"],
            "traversable": source["traversable"],
            "host": deepcopy(source["host"]),
            "effective_void": deepcopy(source["effective_void"]),
            "jamb_before": deepcopy(source["jamb_before"]),
            "jamb_after": deepcopy(source["jamb_after"]),
        },
        "registration": {
            "source_segment_m": deepcopy(evidence["source_segment_m"]),
            "source_segment_px": deepcopy(evidence["source_segment_px"]),
            "length_m": evidence["length_m"],
        },
        "distance_only_host_evidence": {
            "atom_id": evidence["nearest_host_candidate"],
            "midpoint_distance_m": evidence["nearest_host_midpoint_distance_m"],
            "segment_distance_m": evidence["nearest_host_segment_distance_m"],
            "closed_wall_break_proven": evidence["closed_wall_break_proven"],
        },
        "visual_observation": {
            "door_swing_or_jamb_proven": evidence["door_swing_or_jamb_proven"],
            "text": evidence["visual_observation"],
        },
        "side_space_rankings": deepcopy(side_row["sides"]),
        "selected_space_pair": None,
        "outer_boundary_relation": boundary,
        "artifact_bindings": {
            "crop": _artifact_binding(evidence_path, artifacts["op001-crop-overlay"]),
            "full": _artifact_binding(evidence_path, artifacts["op001-full-overlay"]),
        },
        "blockers": deepcopy(BLOCKERS),
        "decision": "unresolved_entrance_symbol_candidate",
        "host_confirmation": False,
        "void_confirmation": False,
        "jamb_confirmation": False,
        "side_space_confirmation": False,
        "cut_confirmation": False,
        "adjacency_confirmation": False,
        "entrance_confirmation": False,
        "exterior_root_confirmation": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _hash({k: v for k, v in result.items() if k != "candidate_hash"})
    return result if _skip_validate else validate_op001_adjudication(doc, evidence_path, side, wall, result)


def validate_op001_adjudication(document, evidence_file, side_candidate, wall_candidate, candidate):
    doc = validate_v21_document(document)
    if not isinstance(candidate, Mapping) or candidate.get("schema") != SCHEMA:
        raise ValueError("OP001 packet schema drift")
    for key in ("host_confirmation", "void_confirmation", "jamb_confirmation", "side_space_confirmation", "cut_confirmation", "adjacency_confirmation", "entrance_confirmation", "exterior_root_confirmation", "semantic_promotion", "build_authorized", "ready"):
        if candidate.get(key) is not False:
            raise ValueError(f"OP001 packet was promoted: {key}")
    if candidate.get("selected_space_pair") is not None:
        raise ValueError("OP001 unconfirmed space pair was selected")
    if (candidate.get("outer_boundary_relation") or {}).get("intersects_outer_boundary") is not False:
        raise ValueError("OP001 exterior boundary relation was promoted")
    expected = build_op001_adjudication(doc, evidence_file, side_candidate, wall_candidate, _skip_validate=True)
    if dict(candidate) != expected:
        raise ValueError("OP001 evidence or policy drift")
    return deepcopy(dict(candidate))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=REPO_ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json")
    parser.add_argument("--evidence", type=Path, default=REPO_ROOT / "reports/op001_entrance_evidence_20260901/op001-evidence.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    document = json.loads(args.source.read_text(encoding="utf-8"))
    packet = build_op001_adjudication(document, args.evidence, build_opening_side_space_candidate(document), build_target_aware_wall_solids(document))
    payload = canonical_json(packet) + b"\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload)
    else:
        sys.stdout.buffer.write(payload)
    return 0


__all__ = ["build_op001_adjudication", "validate_op001_adjudication"]
if __name__ == "__main__":
    raise SystemExit(main())
