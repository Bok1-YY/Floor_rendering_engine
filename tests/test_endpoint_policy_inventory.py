from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.goal_loop_v2.endpoint_policy_inventory import build_endpoint_policy_inventory, validate_endpoint_policy_inventory

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
