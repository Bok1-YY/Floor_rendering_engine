from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2.build_source_bound_edge_admissibility_register import (
    BATCH_FAIL_CLOSED,
    BLOCKER_COUNTS,
    HARD_FAILURES,
    ROW_FAIL_CLOSED,
    ROW_IDS,
    _candidate_hash,
    build,
    validate,
)
from tools.goal_loop_v2.demoted_portal_deny import PORTAL_ID

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/goal_loop_v2/build_source_bound_edge_admissibility_register.py"
CANDIDATE = ROOT / "reports/source_bound_edge_admissibility_register_20260903/register.json"
REGISTRY = ROOT / "reports/correction_candidate_registry_20260902/correction-candidate-registry.json"


def _row(candidate, opening_id):
    return next(row for row in candidate["rows"] if row["opening_id"] == opening_id)


def _rehash_row_and_register(candidate, row):
    row["candidate_hash"] = _candidate_hash(
        {key: value for key, value in row.items() if key != "candidate_hash"}
    )
    candidate["candidate_hash"] = _candidate_hash(
        {key: value for key, value in candidate.items() if key != "candidate_hash"}
    )


def test_register_covers_all_source_rows_plus_op012_history():
    candidate = build()
    assert candidate["row_ids"] == list(ROW_IDS)
    assert candidate["row_count"] == 13
    assert len(candidate["rows"]) == 13
    assert candidate["conflicting_pair_ids"] == ["OP002"]
    assert candidate["ambiguous_pair_ids"] == ["OP008"]
    assert candidate["missing_pair_ids"] == ["OP005"]
    assert candidate["hard_denied_ids"] == [PORTAL_ID]
    assert candidate["quarantined_history_ids"] == ["OP012"]
    assert candidate["next_target"]["opening_id"] == "OP002"
    assert candidate["current_pointer_update_authorized"] is False


def test_all_rows_and_batch_are_fail_closed():
    candidate = build()
    for key in BATCH_FAIL_CLOSED:
        assert candidate[key] is False
    assert candidate["score_effect"] == "none"
    for row in candidate["rows"]:
        for key in ROW_FAIL_CLOSED:
            assert row[key] is False
        assert row["score_effect"] == "none"
        assert row["selected_pair"] is None
        assert row["source_record_state"]["record_fields_are_edge_confirmations"] is False
        assert row["human_readable_boundary"]["named_sides_are_not_a_confirmed_pair"] is True
        assert row["human_readable_boundary"]["cuttable_is_not_wall_break_authority"] is True


def test_op002_exposes_real_semantic_space_conflict_across_layers():
    row = _row(build(), "OP002")
    assert row["pair_status"] == "conflicting_candidates"
    assert row["pair_candidate_sets"] == [
        ["bedroom_01", "bedroom_corridor"],
        ["bedroom_01", "common_core_circulation"],
    ]
    sources = {item["evidence_source"]: item for item in row["pair_evidence"]}
    assert sources["source_opening_record_sides"]["unordered_pair"] == ["bedroom_01", "bedroom_corridor"]
    assert sources["source_adjacency_candidate"]["unordered_pair"] == [
        "bedroom_01",
        "common_core_circulation",
    ]
    assert sources["correction_registry_candidate"]["unordered_pair"] == [
        "bedroom_01",
        "bedroom_corridor",
    ]
    assert row["priority"] == {"rank": 1, "label_zh": "最高（下一条）"}
    assert "PAIR_SOURCE_CONFLICT" in row["remaining_blockers"]


def test_op001_is_unit_scope_only_and_not_a_building_root():
    row = _row(build(), "OP001")
    assert row["pair_status"] == "unique_candidate_pair_unconfirmed"
    assert row["pair_candidate_sets"] == [["common_core_circulation", "lobby"]]
    assert row["root_relevance"] == "unit_scope_hypothesis_only"
    assert row["root_confirmation"] is False
    assert "ENTRY 不是楼栋外部入口" in row["human_readable_status_zh"]


def test_op003_pair_is_internal_near_miss_not_root():
    row = _row(build(), "OP003")
    assert row["pair_candidate_sets"] == [["bedroom_01", "west_toilet"]]
    assert row["root_relevance"] == "near_miss_not_root"
    assert "0.014637 m" in row["human_readable_status_zh"]
    assert "RETURN_WALL_ENDPOINT_UNRESOLVED" in row["remaining_blockers"]


def test_op004_pair_agrees_but_is_internal_to_unreachable_component():
    row = _row(build(), "OP004")
    assert row["pair_status"] == "unique_candidate_pair_unconfirmed"
    assert row["pair_candidate_sets"] == [["bedroom_02", "north_toilet"]]
    assert row["internal_to_candidate_unreachable_component"] is True
    assert row["crosses_candidate_reachable_boundary"] is False
    membership = row["candidate_graph_membership"][0]["space_membership"]
    assert membership == {
        "bedroom_02": "candidate_unreachable",
        "north_toilet": "candidate_unreachable",
    }


def test_op008_preserves_bath_wc_ambiguity_and_selects_nothing():
    row = _row(build(), "OP008")
    assert row["pair_status"] == "ambiguous_candidates"
    assert row["selected_pair"] is None
    assert row["pair_ambiguity_evidence"] == [
        {
            "evidence_source": "partition_resolved_candidate",
            "public_space_id": "lobby",
            "other_side_space_options": ["bath", "wc"],
            "classification": "group_ambiguous",
            "confirmation": False,
        }
    ]
    assert row["pair_candidate_sets"] == [["bath", "lobby"]]
    assert "PAIR_AMBIGUOUS_BATH_OR_WC" in row["remaining_blockers"]


def test_op009_agrees_on_pair_but_not_operation_or_traversability():
    row = _row(build(), "OP009")
    assert row["pair_candidate_sets"] == [["bedroom_01", "rear_balcony"]]
    assert row["pair_status"] == "unique_candidate_pair_unconfirmed"
    assert row["root_relevance"] == "balcony_interface_not_building_root"
    assert "GLAZED_OPERATION_UNCONFIRMED" in row["remaining_blockers"]
    assert row["traversability_confirmation"] is False


def test_op011_crosses_only_the_candidate_reachability_boundary():
    row = _row(build(), "OP011")
    assert row["pair_candidate_sets"] == [["dry_balcony", "kitchen"]]
    assert row["crosses_candidate_reachable_boundary"] is True
    assert row["source_connectivity_evidence"]["clean_provider_disagreement"] is True
    assert row["graph_edge_admitted"] is False
    assert "类型审查冲突" in row["human_readable_status_zh"]


def test_op005_portal_and_op012_stay_out_of_edge_admission():
    candidate = build()
    op005 = _row(candidate, "OP005")
    portal = _row(candidate, PORTAL_ID)
    op012 = _row(candidate, "OP012")
    assert op005["pair_status"] == "missing"
    assert op005["eligible_for_targeted_room_side_review"] is False
    assert portal["pair_status"] == "hard_denied"
    assert portal["host_status"] == "hard_denied"
    assert portal["graph_edge_admitted"] is False
    assert op012["pair_status"] == "quarantined_history"
    assert op012["registered_segment_m"] is None
    assert op012["source_record_state"]["opening_status"] == "not_in_source_opening_contract"
    assert op012["graph_edge_admitted"] is False


def test_human_table_is_candidate_language_and_points_to_op002():
    table = build()["human_table"]
    assert table["title"] == "Opening 两侧空间核对表（候选，不是房间确认）"
    assert "不是已确认的房间对" in table["notice"]
    op002 = next(row for row in table["rows"] if row["opening_id"] == "OP002")
    assert op002["priority"] == "最高（下一条）"
    assert op002["next_action"] == "生成 OP002 独立两侧空间复核任务"
    assert "确认房间连接" in table["forbidden_current_actions"]
    assert "完成构建" in table["forbidden_current_actions"]


def test_score_and_hard_failures_do_not_change():
    impact = build()["score_and_gate_impact"]
    assert impact["source_score_before"] == 65
    assert impact["source_score_after"] == 65
    assert impact["hard_failures_before"] == list(HARD_FAILURES)
    assert impact["hard_failures_after"] == list(HARD_FAILURES)
    assert impact["blocker_counts_before"] == BLOCKER_COUNTS
    assert impact["blocker_counts_after"] == BLOCKER_COUNTS
    assert impact["blocker_count_delta"] == {key: 0 for key in BLOCKER_COUNTS}


@pytest.mark.parametrize(
    ("opening_id", "field", "value"),
    [
        ("OP001", "root_confirmation", True),
        ("OP002", "pair_confirmation", True),
        ("OP004", "graph_edge_admitted", True),
        ("OP008", "selected_pair", ["bath", "lobby"]),
        ("OP011", "traversability_confirmation", True),
        (PORTAL_ID, "graph_edge_admitted", True),
        ("OP012", "source_application_authorized", True),
    ],
)
def test_rehashed_row_selection_or_promotion_is_rejected(opening_id, field, value):
    candidate = build()
    row = _row(candidate, opening_id)
    row[field] = value
    _rehash_row_and_register(candidate, row)
    with pytest.raises(ValueError, match="drift|promoted|special-case|authority"):
        validate(candidate)


def test_rehashed_op002_pair_conflict_erasure_is_rejected():
    candidate = build()
    row = _row(candidate, "OP002")
    row["pair_evidence"] = [
        item
        for item in row["pair_evidence"]
        if item["evidence_source"] != "source_adjacency_candidate"
    ]
    row["pair_candidate_sets"] = [["bedroom_01", "bedroom_corridor"]]
    row["pair_candidate_count"] = 1
    row["pair_status"] = "unique_candidate_pair_unconfirmed"
    _rehash_row_and_register(candidate, row)
    with pytest.raises(ValueError, match="drift|special-case|status"):
        validate(candidate)


def test_rehashed_score_or_blocker_reduction_is_rejected():
    candidate = build()
    impact = candidate["score_and_gate_impact"]
    impact["source_score_after"] = 75
    impact["blocker_counts_after"]["S07_SPACES_ADJACENCY_REACHABILITY"] = 0
    impact["blocker_count_delta"]["S07_SPACES_ADJACENCY_REACHABILITY"] = -37
    candidate["candidate_hash"] = _candidate_hash(
        {key: value for key, value in candidate.items() if key != "candidate_hash"}
    )
    with pytest.raises(ValueError, match="drift|score"):
        validate(candidate)


def test_tampered_correction_registry_input_is_rejected(tmp_path):
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    op002 = next(row for row in registry["candidates"] if row["opening_id"] == "OP002")
    op002["directed_side_assignment"]["side_a"] = "common_core_circulation"
    registry["candidate_hash"] = _candidate_hash(
        {key: value for key, value in registry.items() if key != "candidate_hash"}
    )
    tampered = tmp_path / "registry.json"
    tampered.write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(ValueError, match="correction registry|evidence|direction|drift"):
        build(correction_registry_path=tampered)


def test_candidate_and_output_are_reproducible():
    first = build()
    second = build()
    assert first == second
    assert first["candidate_hash"] == _candidate_hash(
        {key: value for key, value in first.items() if key != "candidate_hash"}
    )
    assert validate(json.loads(CANDIDATE.read_text(encoding="utf-8"))) == first


def test_direct_script_runs_outside_repository(tmp_path):
    output = tmp_path / "register.json"
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
