from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.goal_loop_v2.op002_cut_closure import build_op002_cut_closure_candidate, validate_op002_cut_closure_candidate

ROOT = Path(__file__).resolve().parents[1]


def _document():
    path = ROOT / "data" / "goal_loop_v2" / "references" / "1308" / "reference-coordinate-authorized-v21.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _group_containing(groups, space_id):
    return next(group for group in groups if space_id in group)


def test_cut_and_topology_closure_remain_separate_and_fail_closed():
    candidate = build_op002_cut_closure_candidate(_document())
    physical = _group_containing(candidate["physical_membership"]["anchor_groups"], "bedroom_01")
    closed = _group_containing(candidate["closure_anchor_groups"], "bedroom_01")
    assert "bedroom_corridor" in physical
    assert closed == ["bedroom_01"]
    main = _group_containing(candidate["closure_anchor_groups"], "bedroom_corridor")
    assert main == ["bath", "bedroom_corridor", "kitchen", "living_hall", "lobby"]
    assert candidate["physical_topology"]["face_candidate_count"] == 11
    assert candidate["closure_topology"]["face_candidate_count"] == 12
    assert candidate["cut_confirmation"] is False
    assert candidate["build_authorized"] is False


def test_closure_width_is_stable_but_material_shortening_fails():
    candidate = build_op002_cut_closure_candidate(_document())
    assert all(row["topology"]["face_candidate_count"] == 12 for row in candidate["sensitivity"]["half_width_m"])
    by_delta = {row["endpoint_delta_m"]: row for row in candidate["sensitivity"]["endpoint_delta_m"]}
    assert "bedroom_corridor" in _group_containing(by_delta[-0.001]["closure_membership"]["anchor_groups"], "bedroom_01")
    assert _group_containing(by_delta[0.0]["closure_membership"]["anchor_groups"], "bedroom_01") == ["bedroom_01"]
    assert _group_containing(by_delta[0.001]["closure_membership"]["anchor_groups"], "bedroom_01") == ["bedroom_01"]


def test_cut_closure_rejects_promotion_wrong_id_and_rehashed_topology():
    import tools.goal_loop_v2.op002_cut_closure as module

    document = _document()
    candidate = build_op002_cut_closure_candidate(document)
    promoted = deepcopy(candidate)
    promoted["cut_confirmation"] = True
    with pytest.raises(ValueError, match="promoted"):
        validate_op002_cut_closure_candidate(document, promoted)
    wrong = deepcopy(candidate)
    wrong["opening_id"] = "OP003"
    with pytest.raises(ValueError, match="allowlist"):
        validate_op002_cut_closure_candidate(document, wrong)
    forged = deepcopy(candidate)
    forged["closure_topology"]["face_candidate_count"] = 999
    forged["candidate_hash"] = module._hash({key: value for key, value in forged.items() if key != "candidate_hash"})
    with pytest.raises(ValueError, match="geometry/topology drift"):
        validate_op002_cut_closure_candidate(document, forged)
