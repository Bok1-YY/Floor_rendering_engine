from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.goal_loop_v2.junction_wall_solids import (
    build_junction_wall_solid_candidate,
    validate_junction_wall_solid_candidate,
)


ROOT = Path(__file__).resolve().parents[1]


def _source():
    path = ROOT / "data" / "goal_loop_v2" / "references" / "1308" / "reference-coordinate-authorized-v21.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_real_1308_builds_all_policy_solids_and_stays_fail_closed():
    candidate = build_junction_wall_solid_candidate(_source())
    assert candidate["coverage"] == {
        "atom_count": 35,
        "junction_count": 49,
        "endpoint_count": 70,
        "endpoint_policy_counts": {
            "face_abutment_candidate": 40,
            "free_end": 2,
            "multiway_junction_candidate": 28,
        },
        "junction_policy_counts": {
            "face_abutment_candidate": 40,
            "free_end": 2,
            "multiway_junction_candidate": 7,
        },
        "atom_solid_count": 35,
        "junction_solid_count": 49,
    }
    assert {row["policy_candidate"] for row in candidate["junction_solids"]} == {
        "face_abutment_candidate", "free_end", "multiway_junction_candidate"
    }
    assert all(row["inference"]["opening_subtraction"] == "none" for row in candidate["junction_solids"])
    assert candidate["method"]["opening_cut_policy"] == "none"
    assert candidate["provenance"]["document_mutated"] is False
    assert candidate["provenance"]["endpoint_policy_baseline_commit"].startswith("6a4568d")
    assert candidate["provenance"]["scoring_mutated"] is False
    assert candidate["provenance"]["build_mutated"] is False
    assert candidate["wall_solid_confirmation"] is False
    assert candidate["room_topology_confirmation"] is False
    assert candidate["semantic_promotion"] is False
    assert candidate["build_authorized"] is False
    assert candidate["ready"] is False


def test_real_1308_records_v1_topology_comparison_and_fixed_sensitivity():
    candidate = build_junction_wall_solid_candidate(_source())
    comparison = candidate["room_topology"]["comparison"]
    assert candidate["room_topology"]["junction_policy_v2"]["face_candidate_count"] == 13
    assert candidate["room_topology"]["room_polygon_v1"]["face_candidate_count"] == 14
    assert comparison["count_deltas_v2_minus_v1"]["face_candidate_count"] == -1
    assert set(comparison["count_deltas_v2_minus_v1"]) == {
        "raw_face_count", "face_candidate_count", "discarded_sliver_count",
        "single_anchor_face_count", "multi_anchor_face_count", "unlabeled_face_count",
    }
    sensitivity = candidate["sensitivity"]
    assert [(row["scenario"], row["face_abutment_extension_m"]) for row in sensitivity] == [
        ("face_abutment_inward_1mm", -0.001),
        ("endpoint_policy_exact", 0.0),
        ("face_abutment_outward_1mm", 0.001),
    ]
    exact = next(row for row in sensitivity if row["scenario"] == "endpoint_policy_exact")
    outward = next(row for row in sensitivity if row["scenario"] == "face_abutment_outward_1mm")
    assert exact["topology"] == candidate["room_topology"]["junction_policy_v2"]
    assert exact["delta_from_exact"] is None
    assert outward["topology"]["face_candidate_count"] == 14
    assert outward["delta_from_exact"]["topology_counts_equal"] is False


def test_rehashed_geometry_and_provenance_forgery_are_rejected():
    import tools.goal_loop_v2.junction_wall_solids as module

    document = _source()
    candidate = build_junction_wall_solid_candidate(document)
    forged_geometry = deepcopy(candidate)
    forged_geometry["junction_solids"][0]["area_m2"] += 1
    forged_geometry["candidate_hash"] = module._hash({k: v for k, v in forged_geometry.items() if k != "candidate_hash"})
    with pytest.raises(ValueError, match="geometry/topology drift"):
        validate_junction_wall_solid_candidate(document, forged_geometry)

    forged_provenance = deepcopy(candidate)
    forged_provenance["provenance"]["build_mutated"] = True
    forged_provenance["candidate_hash"] = module._hash({k: v for k, v in forged_provenance.items() if k != "candidate_hash"})
    with pytest.raises(ValueError, match="inference/provenance drift"):
        validate_junction_wall_solid_candidate(document, forged_provenance)


def test_unresolved_endpoint_policy_fails_before_geometry():
    from tools.fastloop_research.v21_contract import compute_v21_structure_hash

    document = _source()
    node = next(row for row in document["wall_graph"]["junctions"] if row["solid_union_policy"] == "face_abutment")
    node["axis_point_m"] = [999.0, 999.0]
    document["structure_hash"] = compute_v21_structure_hash(document)
    with pytest.raises(ValueError, match="unresolved or invalid"):
        build_junction_wall_solid_candidate(document)


def test_promotion_is_rejected():
    document = _source()
    candidate = build_junction_wall_solid_candidate(document)
    promoted = deepcopy(candidate)
    promoted["wall_solid_confirmation"] = True
    with pytest.raises(ValueError, match="promoted"):
        validate_junction_wall_solid_candidate(document, promoted)
