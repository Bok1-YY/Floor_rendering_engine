from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from shapely.geometry import Polygon

from tools.goal_loop_v2.build_exterior_root_search_candidate import (
    BATCH_FAIL_CLOSED,
    EXPECTED_SOURCE_OPENING_IDS,
    NEAR_MISS_THRESHOLD_M,
    ROW_FAIL_CLOSED,
    TOPOLOGY_TOLERANCE_M,
    _candidate_hash,
    build,
    classify_segment_against_outer_boundary,
    validate,
)
from tools.goal_loop_v2.demoted_portal_deny import PORTAL_ID

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/goal_loop_v2/build_exterior_root_search_candidate.py"
CANDIDATE = ROOT / "reports/exterior_root_search_candidate_20260903/candidate.json"


def _row(candidate, opening_id):
    return next(row for row in candidate["openings"] if row["opening_id"] == opening_id)


def _rehash_row_and_candidate(candidate, row):
    row["candidate_hash"] = _candidate_hash(
        {key: value for key, value in row.items() if key != "candidate_hash"}
    )
    candidate["candidate_hash"] = _candidate_hash(
        {key: value for key, value in candidate.items() if key != "candidate_hash"}
    )


def test_current_source_has_complete_scan_and_no_root_crossing():
    candidate = build()
    assert candidate["opening_ids"] == list(EXPECTED_SOURCE_OPENING_IDS)
    assert candidate["opening_count"] == 12
    assert candidate["actual_crossing_ids"] == []
    assert candidate["boundary_touch_ids"] == []
    assert candidate["near_miss_ids"] == ["OP003"]
    assert candidate["hard_denied_ids"] == [PORTAL_ID]
    assert candidate["root_status"] == "needs_human_or_source_policy"
    assert candidate["root_opening_id"] is None
    assert candidate["next_policy_gate"]["required"] is True
    assert candidate["next_policy_gate"]["automatic_model_authority"] is False
    assert [option["risk_class"] for option in candidate["next_policy_gate"]["allowed_options"]] == [
        "evidence_required",
        "human_policy_required",
        "human_policy_and_hypothesis_only",
    ]
    assert candidate["next_policy_gate"]["allowed_options"][2]["result_authority"] == "hypothesis_only_no_graph_edge"
    for key in BATCH_FAIL_CLOSED:
        assert candidate[key] is False
    assert candidate["score_effect"] == "none"


def test_every_row_is_hash_bound_and_fail_closed():
    candidate = build()
    assert len({row["opening_id"] for row in candidate["openings"]}) == 12
    for row in candidate["openings"]:
        assert row["candidate_hash"] == _candidate_hash(
            {key: value for key, value in row.items() if key != "candidate_hash"}
        )
        assert row["any_actual_crossing"] is False
        assert row["segment_scans"]
        for key in ROW_FAIL_CLOSED:
            assert row[key] is False
        assert row["score_effect"] == "none"
        assert row["human_readable_boundary"]["geometric_contact_is_not_root_confirmation"] is True
        assert row["human_readable_boundary"]["near_miss_is_not_boundary_contact"] is True


def test_nominal_and_effective_segments_are_both_scanned_where_present():
    candidate = build()
    assert [scan["source"] for scan in _row(candidate, "OP001")["segment_scans"]] == [
        "source_observation.nominal_segment_m",
        "effective_void.segment_m",
    ]
    assert [scan["source"] for scan in _row(candidate, "OP002")["segment_scans"]] == [
        "source_observation.nominal_segment_m",
        "effective_void.segment_m",
    ]
    for opening_id in set(EXPECTED_SOURCE_OPENING_IDS) - {"OP001", "OP002"}:
        assert [scan["source"] for scan in _row(candidate, opening_id)["segment_scans"]] == [
            "source_observation.nominal_segment_m"
        ]


def test_op001_is_internal_and_unit_root_remains_a_hypothesis():
    candidate = build()
    row = _row(candidate, "OP001")
    summary = candidate["special_summaries"]["OP001"]
    assert row["primary_classification"] == "internal"
    assert row["primary_minimum_boundary_distance_m"] == 3.432346
    assert row["source_record_state"]["source_observation_kind"] == "entrance_symbol"
    assert row["source_record_state"]["record_fields_are_root_confirmations"] is False
    assert summary["building_exterior_root_confirmation"] is False
    assert summary["unit_root_candidate"] == "hypothesis"
    assert summary["unit_root_confirmation"] is False
    assert summary["entry_label_is_source_pixel_context_only"] is True


def test_op003_is_a_near_miss_not_contact_or_crossing():
    candidate = build()
    row = _row(candidate, "OP003")
    geometry = row["segment_scans"][0]["geometry"]
    assert row["primary_classification"] == "near-miss"
    assert row["primary_minimum_boundary_distance_m"] == 0.014637
    assert geometry["intersects_boundary"] is False
    assert geometry["boundary_contact_without_crossing"] is False
    assert geometry["actual_crossing"] is False
    assert geometry["nearest_boundary_edge_ids"] == ["OUTER-EDGE-008"]
    assert candidate["special_summaries"]["OP003"]["near_miss_is_not_crossing"] is True


def test_balcony_facing_candidates_do_not_become_roots():
    candidate = build()
    for opening_id in ("OP009", "OP010"):
        row = _row(candidate, opening_id)
        assert row["primary_classification"] == "internal"
        assert row["root_candidate"] is False
        assert row["root_confirmation"] is False
        assert candidate["special_summaries"][opening_id]["balcony_facing_is_not_exterior_root"] is True


def test_demoted_portal_op012_and_gate_are_not_root_inputs():
    candidate = build()
    portal = _row(candidate, PORTAL_ID)
    assert portal["primary_classification"] == "hard-denied-portal"
    assert portal["hard_denied"] is True
    assert portal["deny_candidate_hash"] == candidate["input_bindings"]["demoted_portal_deny_candidate_hash"]
    assert candidate["excluded_non_source_candidates"]["OP012"] == {
        "reason": "not present in current source opening records",
        "included_in_scan": False,
        "eligible_as_root": False,
    }
    assert candidate["excluded_non_source_candidates"]["GATE"] == {
        "reason": "site/plot label, not an opening record",
        "included_in_scan": False,
        "eligible_as_root": False,
    }


def test_geometry_policy_is_explicit():
    candidate = build()
    assert candidate["geometry_policy"]["engine"] == "shapely"
    assert candidate["geometry_policy"]["topology_tolerance_m"] == TOPOLOGY_TOLERANCE_M
    assert candidate["geometry_policy"]["near_miss_threshold_m"] == NEAR_MISS_THRESHOLD_M
    assert candidate["geometry_policy"]["near_miss_is_root_evidence"] is False
    assert candidate["outer_boundary"]["status"] == "confirmed"
    assert candidate["outer_boundary"]["edge_count"] == 8


def test_synthetic_actual_crossing_is_distinct_from_touch():
    outer = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    crossing = classify_segment_against_outer_boundary([[-1, 5], [11, 5]], outer)
    touch = classify_segment_against_outer_boundary([[0, 5], [5, 5]], outer)
    overlap = classify_segment_against_outer_boundary([[0, 2], [0, 8]], outer)
    assert crossing["classification"] == "actual-crossing"
    assert crossing["actual_crossing"] is True
    assert crossing["intersects_boundary"] is True
    assert crossing["inside_or_boundary_length_m"] == 10.0
    assert crossing["outside_length_m"] == 2.0
    assert touch["classification"] == "boundary-touch"
    assert touch["actual_crossing"] is False
    assert touch["boundary_contact_without_crossing"] is True
    assert overlap["classification"] == "boundary-touch"
    assert overlap["boundary_overlap_length_m"] == 6.0


def test_synthetic_near_miss_internal_and_off_frame_are_distinct():
    outer = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    near = classify_segment_against_outer_boundary([[0.05, 2], [0.05, 8]], outer)
    internal = classify_segment_against_outer_boundary([[2, 2], [8, 2]], outer)
    off_frame = classify_segment_against_outer_boundary([[11, 2], [12, 2]], outer)
    assert near["classification"] == "near-miss"
    assert near["minimum_boundary_distance_m"] == 0.05
    assert near["intersects_boundary"] is False
    assert internal["classification"] == "internal"
    assert off_frame["classification"] == "off-frame-not-observed"


@pytest.mark.parametrize(
    ("opening_id", "field", "value"),
    [
        ("OP001", "root_candidate", True),
        ("OP001", "root_confirmation", True),
        ("OP001", "entrance_confirmation", True),
        ("OP003", "geometric_boundary_crossing_candidate", True),
        ("OP009", "outside_side_confirmation", True),
        (PORTAL_ID, "hard_denied", False),
    ],
)
def test_rehashed_row_promotion_or_portal_release_is_rejected(opening_id, field, value):
    candidate = build()
    row = _row(candidate, opening_id)
    row[field] = value
    _rehash_row_and_candidate(candidate, row)
    with pytest.raises(ValueError, match="drift|promoted|special-case"):
        validate(candidate)


def test_rehashed_near_miss_classification_or_distance_drift_is_rejected():
    candidate = build()
    row = _row(candidate, "OP003")
    row["primary_classification"] = "boundary-touch"
    row["primary_minimum_boundary_distance_m"] = 0.0
    row["segment_scans"][0]["geometry"]["classification"] = "boundary-touch"
    row["segment_scans"][0]["geometry"]["minimum_boundary_distance_m"] = 0.0
    _rehash_row_and_candidate(candidate, row)
    with pytest.raises(ValueError, match="drift|special-case"):
        validate(candidate)


def test_rehashed_batch_root_or_score_promotion_is_rejected():
    for field, value in (("root_confirmation", True), ("root_opening_id", "OP001"), ("score_effect", "increase")):
        candidate = build()
        candidate[field] = value
        candidate["candidate_hash"] = _candidate_hash(
            {key: item for key, item in candidate.items() if key != "candidate_hash"}
        )
        with pytest.raises(ValueError, match="drift|promoted|score"):
            validate(candidate)


def test_degenerate_or_malformed_segments_are_rejected():
    outer = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    with pytest.raises(ValueError, match="two 2D endpoints"):
        classify_segment_against_outer_boundary([[1, 1]], outer)
    with pytest.raises(ValueError, match="degenerate"):
        classify_segment_against_outer_boundary([[1, 1], [1, 1]], outer)


def test_candidate_and_output_hashes_are_reproducible():
    first = build()
    second = build()
    assert first == second
    assert first["candidate_hash"] == _candidate_hash(
        {key: value for key, value in first.items() if key != "candidate_hash"}
    )
    assert validate(json.loads(CANDIDATE.read_text(encoding="utf-8"))) == first


def test_direct_script_runs_outside_repository(tmp_path):
    output = tmp_path / "candidate.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written == build()
