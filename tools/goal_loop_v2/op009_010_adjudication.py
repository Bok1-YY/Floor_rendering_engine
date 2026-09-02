"""Build fail-closed, policy-separated OP009/OP010 adjudication candidates."""
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
from tools.goal_loop_v2.jamb_policy import minimum_jamb_support_m
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids, validate_target_aware_wall_solids

SCHEMA = "op009-op010-adjudication-candidate-v1"
CONFIG = {"OP009": "ATOM-WB005-01", "OP010": "ATOM-WB003-03"}
COMMON_BLOCKERS = [
    "SOURCE_HOST_MISSING",
    "SOURCE_EFFECTIVE_VOID_MISSING",
    "SOURCE_JAMB_MISSING",
    "SOURCE_SIDE_SPACES_MISSING",
    "SOURCE_PHYSICAL_WALL_BREAK_MISSING",
    "SOURCE_ADJACENCY_MISSING",
    "SOURCE_STATUS_CANDIDATE",
    "Z_DIMENSIONS_ASSUMED_RESEARCH_ONLY",
    "GEMINI_REVIEW_MISSING",
    "HUMAN_REVIEW_PENDING",
]
SPECIFIC_BLOCKERS = {
    "OP009": ["LEFT_SIDE_SINGLE_CANDIDATE", "RIGHT_SIDE_CLOSE_RANKING"],
    "OP010": ["RIGHT_SIDE_SINGLE_CANDIDATE", "EXTERIOR_BOUNDARY_INTERSECTION_UNCONFIRMED"],
}


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
    evidence_dir = evidence_file.resolve().parent
    declared = Path(str(artifact["path"]))
    actual = next((p for p in (evidence_dir / declared.name, declared) if p.is_file()), None)
    if actual is None:
        raise ValueError(f"OP009/010 evidence artifact missing: {declared.name}")
    raw = actual.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != artifact.get("sha256"):
        raise ValueError(f"OP009/010 evidence artifact hash drift: {declared.name}")
    return {"filename": actual.name, "sha256": digest, "bytes": len(raw)}


def _host_support(segment: list[list[float]], host: list[list[float]], policy_minimum: float) -> dict[str, Any]:
    h0, h1 = host
    dx, dy = h1[0] - h0[0], h1[1] - h0[1]
    length = math.hypot(dx, dy)
    denominator = length * length
    parameters = [((p[0] - h0[0]) * dx + (p[1] - h0[1]) * dy) / denominator for p in segment]
    low, high = min(parameters), max(parameters)
    before = max(0.0, low * length)
    after = max(0.0, (1.0 - high) * length)
    minimum = min(before, after)
    return {
        "host_parameters": [round(v, 9) for v in parameters],
        "endpoint_supported": [0.0 <= v <= 1.0 for v in parameters],
        "geometric_jamb_before_m": round(before, 9),
        "geometric_jamb_after_m": round(after, 9),
        "minimum_geometric_jamb_m": round(minimum, 9),
        "candidate_policy_minimum_m": policy_minimum,
        "candidate_policy_sufficient": minimum >= policy_minimum,
        "policy_source": "opening_contract.minimum_jamb_support_m",
        "source_jamb_confirmation": False,
    }


def build_op009_010_adjudication(
    document: Mapping[str, Any],
    evidence_file: str | Path,
    side_candidate: Mapping[str, Any],
    wall_candidate: Mapping[str, Any],
    *,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    doc = validate_v21_document(document)
    evidence_path = Path(evidence_file)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    side = validate_opening_side_space_candidate(doc, dict(side_candidate))
    wall = validate_target_aware_wall_solids(doc, dict(wall_candidate))
    if evidence.get("schema") != "op009-op010-geometry-evidence-v1":
        raise ValueError("OP009/010 evidence schema drift")
    if evidence.get("source_structure_hash") != doc["structure_hash"]:
        raise ValueError("OP009/010 evidence source drift")

    openings = []
    for opening_id, host_id in CONFIG.items():
        source = next(x for x in doc["opening_contract"]["openings"] if x["id"] == opening_id)
        evidence_row = next(x for x in evidence["openings"] if x["opening_id"] == opening_id)
        side_row = next(x for x in side["openings"] if x["opening_id"] == opening_id)
        source_atom = next(x for x in doc["wall_graph"]["atoms"] if x["id"] == host_id)
        host = next(x for x in evidence_row["host_wall_candidates"] if x["atom_id"] == host_id)
        if host["segment_m"] != source_atom["centerline_m"]:
            raise ValueError(f"{opening_id} evidence host differs from source atom")
        support = _host_support(evidence_row["source_segment_m"], host["segment_m"], minimum_jamb_support_m(doc))
        blockers = COMMON_BLOCKERS + SPECIFIC_BLOCKERS[opening_id] + ["SIDE_SPACE_PAIR_UNCONFIRMED"]
        openings.append(
            {
                "opening_id": opening_id,
                "policy_key": f"{opening_id.lower()}-independent-policy-v1",
                "source_status": source["status"],
                "source_kind_advisory": source["source_observation"]["kind"],
                "source_build_disposition": source["build_disposition"],
                "source_traversable": source["traversable"],
                "registration": deepcopy(evidence_row["registration"]),
                "source_segment_m": deepcopy(evidence_row["source_segment_m"]),
                "segment_frame": deepcopy(side_row["segment_frame"]),
                "host_candidate": {
                    "atom_id": host_id,
                    "branch_id": source_atom["branch_id"],
                    "segment_m": deepcopy(host["segment_m"]),
                    "endpoint_distance_sum_m": host["endpoint_distance_sum_m"],
                    "thickness_m": source_atom["thickness_m"],
                    "height_m": source_atom["height_m"],
                    "status": source_atom["status"],
                    "assumption_ids": deepcopy(source_atom["assumption_ids"]),
                },
                "host_support_candidate": support,
                "side_space_rankings": deepcopy(side_row["sides"]),
                "selected_space_pair": None,
                "artifact_bindings": {
                    role: _artifact_binding(evidence_path, evidence_row["artifacts"][role])
                    for role in ("crop", "full")
                },
                "blockers": blockers,
                "decision": "unresolved_candidate",
                "host_confirmation": False,
                "void_confirmation": False,
                "jamb_confirmation": False,
                "side_space_confirmation": False,
                "cut_confirmation": False,
                "adjacency_confirmation": False,
                "semantic_promotion": False,
                "build_authorized": False,
            }
        )

    result = {
        "schema": SCHEMA,
        "source_structure_hash": doc["structure_hash"],
        "evidence_binding": _file_binding(evidence_path),
        "opening_side_candidate_hash": side["candidate_hash"],
        "target_aware_wall_candidate_hash": wall["candidate_hash"],
        "openings": openings,
        "policy_separation": {
            "different_opening_ids": openings[0]["opening_id"] != openings[1]["opening_id"],
            "different_host_atoms": openings[0]["host_candidate"]["atom_id"] != openings[1]["host_candidate"]["atom_id"],
            "shared_orientation_only": openings[0]["segment_frame"]["tangent_unit"] == openings[1]["segment_frame"]["tangent_unit"],
            "shared_cut_or_adjacency_policy": False,
            "independent_policy_keys": [x["policy_key"] for x in openings],
        },
        "status": "pending_composite_review",
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _hash({k: v for k, v in result.items() if k != "candidate_hash"})
    if _skip_validate:
        return result
    return validate_op009_010_adjudication(doc, evidence_path, side, wall, result)


def validate_op009_010_adjudication(document, evidence_file, side_candidate, wall_candidate, candidate):
    doc = validate_v21_document(document)
    if not isinstance(candidate, Mapping) or candidate.get("schema") != SCHEMA:
        raise ValueError("OP009/010 packet schema drift")
    for key in ("semantic_promotion", "build_authorized", "ready"):
        if candidate.get(key) is not False:
            raise ValueError("OP009/010 packet was promoted")
    for row in candidate.get("openings", []):
        for key in (
            "host_confirmation",
            "void_confirmation",
            "jamb_confirmation",
            "side_space_confirmation",
            "cut_confirmation",
            "adjacency_confirmation",
            "semantic_promotion",
            "build_authorized",
        ):
            if row.get(key) is not False:
                raise ValueError("OP009/010 opening was promoted")
        if row.get("selected_space_pair") is not None:
            raise ValueError("OP009/010 unconfirmed space pair was selected")
    expected = build_op009_010_adjudication(doc, evidence_file, side_candidate, wall_candidate, _skip_validate=True)
    if dict(candidate) != expected:
        raise ValueError("OP009/010 evidence or policy drift")
    return deepcopy(dict(candidate))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=REPO_ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json")
    parser.add_argument("--evidence", type=Path, default=REPO_ROOT / "reports/op009_op010_geometry_evidence_20260901/op009-op010-evidence.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    document = json.loads(args.source.read_text(encoding="utf-8"))
    packet = build_op009_010_adjudication(
        document,
        args.evidence,
        build_opening_side_space_candidate(document),
        build_target_aware_wall_solids(document),
    )
    payload = canonical_json(packet) + b"\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload)
    else:
        sys.stdout.buffer.write(payload)
    return 0


__all__ = ["build_op009_010_adjudication", "validate_op009_010_adjudication"]

if __name__ == "__main__":
    raise SystemExit(main())
