from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2.build_source_root_policy_candidate import (
    BLOCKER_COUNTS,
    FAIL_CLOSED,
    HARD_FAILURES,
    POLICY_MODES,
    _candidate_hash,
    build,
    validate,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/goal_loop_v2/build_source_root_policy_candidate.py"
EXTERIOR_ROOT = ROOT / "reports/exterior_root_search_candidate_20260903/candidate.json"
CANDIDATE = ROOT / "reports/source_root_policy_candidate_20260903/candidate.json"


def _rehash(candidate):
    candidate["candidate_hash"] = _candidate_hash(
        {key: value for key, value in candidate.items() if key != "candidate_hash"}
    )


def test_recommendation_selection_confirmation_and_edge_are_separate():
    candidate = build()
    assert candidate["recommended_policy_mode"] == "unit_scope_root"
    assert candidate["selected_policy_mode"] is None
    assert candidate["human_decision"]["status"] == "pending"
    assert candidate["human_decision"]["policy_mode"] is None
    assert candidate["selection_contract"] == {
        "allowed_modes": list(POLICY_MODES),
        "mutually_exclusive": True,
        "default_selected_mode": None,
        "recommendation_is_selection": False,
        "human_selection_creates_confirmation": False,
        "human_selection_creates_graph_edge": False,
        "candidate_submission_button_zh": "提交 root 策略候选",
    }
    for key in FAIL_CLOSED:
        assert candidate[key] is False
    assert candidate["score_effect"] == "none"


def test_all_three_modes_are_unselected_and_fail_closed():
    candidate = build()
    assert tuple(candidate["modes"]) == POLICY_MODES
    for mode in candidate["modes"].values():
        assert mode["policy_selected"] is False
        assert mode["human_authorized"] is False
        for key in FAIL_CLOSED:
            assert mode[key] is False
        assert mode["score_effect"] == "none"


def test_in_frame_exterior_mode_is_disabled_without_crossing():
    mode = build()["modes"]["in_frame_exterior_root"]
    assert mode["status"] == "disabled_no_source_crossing"
    assert mode["selectable_for_candidate_submission"] is False
    assert mode["current_evidence"]["actual_crossing_ids"] == []
    assert mode["current_evidence"]["boundary_touch_ids"] == []
    assert mode["current_evidence"]["near_miss_id"] == "OP003"
    assert mode["current_evidence"]["near_miss_distance_m"] == 0.014637
    assert mode["current_evidence"]["near_miss_is_crossing"] is False
    assert mode["risk_class"] == "evidence_required"


def test_unit_scope_mode_is_only_a_human_policy_candidate():
    mode = build()["modes"]["unit_scope_root"]
    evidence = mode["current_evidence"]
    assert mode["status"] == "recommended_available_for_candidate_submission"
    assert mode["selectable_for_candidate_submission"] is True
    assert mode["risk_class"] == "human_policy_required"
    assert mode["result_authority"] == "unit_scope_policy_candidate_only_not_building_exterior"
    assert evidence["opening_id"] == "OP001"
    assert evidence["source_kind"] == "entrance_symbol"
    assert evidence["host_atom_id"] == "ATOM-WB016-02"
    assert evidence["source_traversable_value"] is False
    assert evidence["common_side_space_id"] == "common_core_circulation"
    assert evidence["private_unit_side_space_id"] == "lobby"
    assert evidence["building_outer_boundary_intersection"] is False
    assert evidence["unit_root_candidate"] == "hypothesis"
    assert evidence["entry_label_is_source_pixel_context_only"] is True
    assert evidence["record_fields_are_policy_confirmations"] is False


def test_off_frame_mode_is_hypothesis_only_without_opening_or_edge():
    mode = build()["modes"]["off_frame_root_hypothesis"]
    assert mode["status"] == "available_for_hypothesis_submission"
    assert mode["selectable_for_candidate_submission"] is True
    assert mode["risk_class"] == "human_policy_and_hypothesis_only"
    assert mode["current_evidence"] == {
        "opening_id": None,
        "source_support": "not_observed_in_current_frame",
        "source_fact": False,
        "hypothesis_only": True,
    }
    assert mode["result_authority"] == "hypothesis_only_no_opening_no_graph_edge"
    assert mode["graph_edge_admitted"] is False


def test_ui_copy_uses_candidate_language_and_disables_unsafe_action():
    ui = build()["ui_spec_zh"]
    assert ui["title"] == "1308 root（范围起点假设）策略选择"
    assert "不是入口确认" in ui["notice"]
    assert ui["default_prompt"] == "请选择一种策略（默认不选）"
    assert "未确认" in ui["recommendation_banner"]
    assert ui["options"]["in_frame_exterior_root"]["disabled"] is True
    assert ui["options"]["unit_scope_root"]["label"] == "提交单位范围 root 假设"
    assert ui["options"]["unit_scope_root"]["helper_text"] == "提交后仍为待审核候选，不会改变模型或评分。"
    assert ui["options"]["off_frame_root_hypothesis"]["rationale_required"] is True
    assert ui["options"]["off_frame_root_hypothesis"]["rationale_placeholder"] == "请说明为什么当前图幅不足以观察外部 root。"
    assert ui["primary_button"] == "提交 root 策略候选"
    assert ui["primary_button_disabled_until_human_selection"] is True
    assert ui["success_title"] == "Root 策略候选已记录（不是入口确认）"
    assert ui["success_statement"] == "本次没有确认任何入口。"
    assert "确认入口" in ui["forbidden_ambiguous_labels"]


def test_current_score_and_all_hard_failures_are_unchanged():
    impact = build()["score_and_gate_impact"]
    assert impact["source_score_before"] == 65
    assert impact["source_score_after"] == 65
    assert impact["hard_failures_before"] == list(HARD_FAILURES)
    assert impact["hard_failures_after"] == list(HARD_FAILURES)
    assert impact["blocker_counts_before"] == BLOCKER_COUNTS
    assert impact["blocker_counts_after"] == BLOCKER_COUNTS
    assert impact["blocker_count_delta"] == {key: 0 for key in BLOCKER_COUNTS}
    assert all(
        impact[key] is False
        for key in (
            "policy_packet_creation_changes_score",
            "policy_recommendation_changes_score",
            "human_unit_scope_selection_changes_score",
            "off_frame_hypothesis_changes_score",
        )
    )


def test_all_inputs_are_file_and_candidate_hash_bound():
    bindings = build()["input_bindings"]
    assert bindings["exterior_root_search"]["candidate_hash"] == "dfc5ff48f69a2e6fa539a96c4cb968ee2f54750bfcac6688f03b3f90bca3bc8f"
    assert len(bindings["exterior_root_search"]["file_sha256"]) == 64
    assert len(bindings["op001_unit_scope"]["candidate_hash"]) == 64
    assert len(bindings["op001_unit_scope"]["file_sha256"]) == 64
    assert bindings["unit_scope_reachability_v3"]["candidate_hash"] == "338422f1e49b58f985d280e2f50c541b79ec53a7a38b5737391bfb0718b77966"
    assert bindings["unit_scope_reachability_v3"]["tier_d_unreachable_scope_space_ids"] == [
        "bedroom_02",
        "dry_balcony",
        "north_toilet",
    ]
    assert bindings["unit_scope_reachability_v3"]["reachability_confirmation"] is False
    assert bindings["live_gates"]["source_score"] == 65


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("building_exterior_root_confirmation", True),
        ("unit_root_confirmation", True),
        ("root_confirmation", True),
        ("traversability_confirmation", True),
        ("adjacency_confirmation", True),
        ("reachability_confirmation", True),
        ("graph_edge_admitted", True),
        ("source_application_authorized", True),
        ("build_authorized", True),
    ],
)
def test_rehashed_authority_promotion_is_rejected(field, value):
    candidate = build()
    candidate[field] = value
    _rehash(candidate)
    with pytest.raises(ValueError, match="drift|promoted"):
        validate(candidate)


def test_rehashed_selected_mode_while_human_is_pending_is_rejected():
    candidate = build()
    candidate["selected_policy_mode"] = "unit_scope_root"
    candidate["modes"]["unit_scope_root"]["policy_selected"] = True
    _rehash(candidate)
    with pytest.raises(ValueError, match="drift|selected"):
        validate(candidate)


def test_unit_scope_mode_cannot_become_building_exterior():
    candidate = build()
    mode = candidate["modes"]["unit_scope_root"]
    mode["building_exterior_root_confirmation"] = True
    _rehash(candidate)
    with pytest.raises(ValueError, match="drift|promoted|boundary"):
        validate(candidate)


def test_off_frame_mode_cannot_create_an_opening_or_graph_edge():
    candidate = build()
    mode = candidate["modes"]["off_frame_root_hypothesis"]
    mode["current_evidence"]["opening_id"] = "OP012"
    mode["graph_edge_admitted"] = True
    _rehash(candidate)
    with pytest.raises(ValueError, match="drift|promoted|boundary"):
        validate(candidate)


def test_entry_label_cannot_enable_traversability():
    candidate = build()
    mode = candidate["modes"]["unit_scope_root"]
    assert mode["current_evidence"]["entry_label_is_source_pixel_context_only"] is True
    mode["traversability_confirmation"] = True
    _rehash(candidate)
    with pytest.raises(ValueError, match="drift|promoted"):
        validate(candidate)


def test_tampered_exterior_root_input_is_rejected(tmp_path):
    exterior = json.loads(EXTERIOR_ROOT.read_text(encoding="utf-8"))
    exterior["actual_crossing_ids"] = ["OP003"]
    exterior["any_geometric_boundary_crossing_candidate"] = True
    exterior["candidate_hash"] = _candidate_hash(
        {key: value for key, value in exterior.items() if key != "candidate_hash"}
    )
    tampered = tmp_path / "exterior-root.json"
    tampered.write_text(json.dumps(exterior), encoding="utf-8")
    with pytest.raises(ValueError, match="exterior root search|drift|promoted"):
        build(exterior_root_path=tampered)


def test_rehashed_score_or_blocker_reduction_is_rejected():
    candidate = build()
    impact = candidate["score_and_gate_impact"]
    impact["source_score_after"] = 75
    impact["blocker_counts_after"]["S07_SPACES_ADJACENCY_REACHABILITY"] = 0
    impact["blocker_count_delta"]["S07_SPACES_ADJACENCY_REACHABILITY"] = -37
    _rehash(candidate)
    with pytest.raises(ValueError, match="drift|score"):
        validate(candidate)


def test_candidate_and_output_are_reproducible():
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
