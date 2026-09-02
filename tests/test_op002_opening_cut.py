from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.goal_loop_v2.op002_opening_cut import build_op002_opening_cut_candidate, validate_op002_opening_cut_candidate
from tools.goal_loop_v2.jamb_policy import minimum_jamb_support_m

ROOT = Path(__file__).resolve().parents[1]


def _document():
    path = ROOT / "data" / "goal_loop_v2" / "references" / "1308" / "reference-coordinate-authorized-v21.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_op002_cut_candidate_is_fail_closed_and_bounded():
    document = _document()
    candidate = build_op002_opening_cut_candidate(document)
    assert candidate["opening_id"] == "OP002"
    assert candidate["host_atom_id"] == "ATOM-WB006-02"
    assert all(value > minimum_jamb_support_m(document) for value in candidate["jamb_support_m"])
    assert candidate["cut_confirmation"] is False
    assert candidate["build_authorized"] is False
    assert len(candidate["sensitivity"]) == 5
    assert candidate["side_membership_after_cut"]["same_face"] is False
    outward = next(row for row in candidate["sensitivity"] if row["scenario"] == "thickness_outward_1mm")
    assert outward["side_membership"]["same_face"] is True
    assert outward["topology"]["face_candidate_count"] != candidate["after_topology"]["face_candidate_count"]


def test_op002_cut_rejects_promotion_and_wrong_id():
    document = _document()
    candidate = build_op002_opening_cut_candidate(document)
    promoted = deepcopy(candidate)
    promoted["cut_confirmation"] = True
    with pytest.raises(ValueError, match="promoted"):
        validate_op002_opening_cut_candidate(document, promoted)
    wrong = deepcopy(candidate)
    wrong["opening_id"] = "OP003"
    with pytest.raises(ValueError, match="allowlist"):
        validate_op002_opening_cut_candidate(document, wrong)
