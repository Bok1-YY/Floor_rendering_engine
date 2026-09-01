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
