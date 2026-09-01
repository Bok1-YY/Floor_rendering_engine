from copy import deepcopy

import pytest

from tools.goal_loop_v2.opening_side_candidates import (
    build_opening_side_space_candidate,
    validate_opening_side_space_candidate,
)


def _document(segment=None):
    return {
        "structure_hash": "source-hash",
        "spaces": [
            {"id": "LEFT-NEAR", "label": "left near", "point_m": [1.0, 1.0], "status": "candidate"},
            {"id": "LEFT-FAR", "label": "left far", "point_m": [6.0, 1.0], "status": "candidate"},
            {"id": "RIGHT-NEAR", "label": "right near", "point_m": [1.0, -1.0], "status": "candidate"},
            {"id": "ON-AXIS", "label": "axis", "point_m": [1.0, 0.0], "status": "candidate"},
        ],
        "opening_contract": {"openings": [{
            "id": "OP1", "source_observation": {"nominal_segment_m": segment or [[0.0, 0.0], [2.0, 0.0]]}
        }]},
    }


@pytest.fixture(autouse=True)
def _minimal_validator(monkeypatch):
    import tools.goal_loop_v2.opening_side_candidates as module
    monkeypatch.setattr(module, "validate_v21_document", lambda value: value)


def test_ranks_anchor_points_on_each_directed_normal_side():
    doc = _document()
    candidate = build_opening_side_space_candidate(doc)
    row = candidate["openings"][0]
    left, right = row["sides"]
    assert row["segment_frame"]["left_normal_unit"] == [0.0, 1.0]
    assert [item["space_id"] for item in left["candidates"]] == ["LEFT-NEAR", "LEFT-FAR"]
    assert [item["rank"] for item in left["candidates"]] == [1, 2]
    assert [item["space_id"] for item in right["candidates"]] == ["RIGHT-NEAR"]
    assert row["on_axis_space_ids"] == ["ON-AXIS"]
    assert left["ambiguity"]["ambiguity_class"] == "separated_ranking"
    assert right["ambiguity"]["ambiguity_class"] == "single_candidate_uncompared"
    assert all(item["status"] == "ranked_candidate_only" for side in row["sides"] for item in side["candidates"])
    assert candidate["side_space_confirmation"] is False
    validate_opening_side_space_candidate(doc, candidate)


def test_reversing_segment_swaps_left_and_right_without_promoting():
    doc = _document([[2.0, 0.0], [0.0, 0.0]])
    row = build_opening_side_space_candidate(doc)["openings"][0]
    assert row["sides"][0]["candidates"][0]["space_id"] == "RIGHT-NEAR"
    assert row["sides"][1]["candidates"][0]["space_id"] == "LEFT-NEAR"
    assert row["side_space_confirmation"] is False
    assert row["semantic_promotion"] is False
    assert row["build_authorized"] is False


def test_validator_recomputes_ranking_and_rejects_forged_confirmation():
    doc = _document()
    candidate = build_opening_side_space_candidate(doc)
    forged = deepcopy(candidate)
    forged["openings"][0]["sides"][0]["candidates"].reverse()
    import tools.goal_loop_v2.opening_side_candidates as module
    forged["candidate_hash"] = module._hash({k: v for k, v in forged.items() if k != "candidate_hash"})
    with pytest.raises(ValueError, match="ranking drift"):
        validate_opening_side_space_candidate(doc, forged)
    promoted = deepcopy(candidate)
    promoted["side_space_confirmation"] = True
    with pytest.raises(ValueError, match="promoted"):
        validate_opening_side_space_candidate(doc, promoted)
    unsafe_parameters = deepcopy(candidate)
    unsafe_parameters["parameters"]["rank_limit"] = 1000
    with pytest.raises(ValueError, match="rank_limit"):
        validate_opening_side_space_candidate(doc, unsafe_parameters)


def test_degenerate_segment_fails_closed_without_rankings():
    doc = _document([[1.0, 1.0], [1.0, 1.0]])
    candidate = build_opening_side_space_candidate(doc)
    row = candidate["openings"][0]
    assert row["segment_status"] == "degenerate"
    assert row["segment_frame"] is None
    assert row["sides"] == []
    assert candidate["coverage"]["degenerate_opening_count"] == 1
    assert candidate["ready"] is False
