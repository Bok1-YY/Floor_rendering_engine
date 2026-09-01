from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.goal_loop_v2.endpoint_policy_inventory import build_endpoint_policy_inventory, validate_endpoint_policy_inventory
from tools.fastloop_research.v21_contract import compute_v21_structure_hash

ROOT = Path(__file__).resolve().parents[1]


def test_1308_endpoint_inventory_is_complete_and_fail_closed():
    source = ROOT / "data" / "goal_loop_v2" / "references" / "1308" / "reference-coordinate-authorized-v21.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    candidate = build_endpoint_policy_inventory(document)
    assert candidate["coverage"] == {
        "atom_count": 35,
        "junction_count": 49,
        "endpoint_count": 70,
        "policy_counts": {
            "face_abutment_candidate": 40,
            "free_end": 2,
            "multiway_junction_candidate": 28,
        },
    }
    assert candidate["policy_confirmation"] is False
    assert candidate["build_authorized"] is False
    assert all(row["metadata_geometry_valid"] for row in candidate["records"])
    assert all(row["validation_errors"] == [] for row in candidate["records"])


def test_endpoint_inventory_rejects_promotion_and_hash_drift():
    source = ROOT / "data" / "goal_loop_v2" / "references" / "1308" / "reference-coordinate-authorized-v21.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    candidate = build_endpoint_policy_inventory(document)
    promoted = deepcopy(candidate)
    promoted["policy_confirmation"] = True
    with pytest.raises(ValueError, match="promoted"):
        validate_endpoint_policy_inventory(document, promoted)
    forged = deepcopy(candidate)
    forged["records"][0]["node_id"] = "MISSING"
    with pytest.raises(ValueError):
        validate_endpoint_policy_inventory(document, forged)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("policy_candidate", "free_end"),
        ("point_m", [999.0, 999.0]),
        ("incident_atom_ids", ["ATOM-WB001-01", "FORGED"]),
    ],
)
def test_rehashed_endpoint_record_forgery_is_rejected(field, value):
    import tools.goal_loop_v2.endpoint_policy_inventory as module

    source = ROOT / "data" / "goal_loop_v2" / "references" / "1308" / "reference-coordinate-authorized-v21.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    candidate = build_endpoint_policy_inventory(document)
    forged = deepcopy(candidate)
    target = next(row for row in forged["records"] if row["policy_candidate"] != "free_end")
    target[field] = value
    forged["candidate_hash"] = module._hash({key: val for key, val in forged.items() if key != "candidate_hash"})
    with pytest.raises(ValueError, match="record drift"):
        validate_endpoint_policy_inventory(document, forged)


def test_rehashed_source_junction_geometry_injection_fails_closed():
    source = ROOT / "data" / "goal_loop_v2" / "references" / "1308" / "reference-coordinate-authorized-v21.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    node = next(row for row in document["wall_graph"]["junctions"] if row["solid_union_policy"] == "face_abutment")
    node["axis_point_m"] = [999.0, 999.0]
    node["incidents"][0]["attachment"] = "forged"
    document["structure_hash"] = compute_v21_structure_hash(document)
    candidate = build_endpoint_policy_inventory(document)
    affected = [row for row in candidate["records"] if row["node_id"] == node["id"]]
    assert affected and all(row["policy_candidate"] == "unresolved" for row in affected)
    assert all(row["metadata_geometry_valid"] is False for row in affected)
    assert all("node_axis_point_mismatch" in row["validation_errors"] for row in affected)
    assert all("incident_role_attachment_mismatch" in row["validation_errors"] for row in affected)
