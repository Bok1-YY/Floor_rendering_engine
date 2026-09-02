"""Build a human-readable, non-applying source-root policy candidate.

Recommendation, human selection, root confirmation, and graph admission are
separate gates. This candidate stops before all four authoritative mutations.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.build_exterior_root_search_candidate import (
    validate as validate_exterior_root_search,
)
from tools.goal_loop_v2.op001_unit_scope_candidate import (
    validate_op001_unit_scope_candidate,
)
from tools.goal_loop_v2.unit_scope_reachability_v3 import (
    validate_unit_scope_reachability_v3,
)

SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
EXTERIOR_ROOT = ROOT / "reports/exterior_root_search_candidate_20260903/candidate.json"
OP001_SCOPE = ROOT / "reports/op001_unit_scope_candidate_20260902/op001-unit-scope-candidate.json"
UNIT_REACHABILITY = ROOT / "reports/unit_scope_reachability_v3_20260902/unit-scope-reachability-v3.json"
LIVE_GATES = ROOT / "reports/live_gate_recompute_20260902/live-gate-recompute.json"
OUT = ROOT / "reports/source_root_policy_candidate_20260903"

POLICY_MODES = (
    "in_frame_exterior_root",
    "unit_scope_root",
    "off_frame_root_hypothesis",
)
HARD_FAILURES = (
    "S06_OPENINGS",
    "S07_SPACES_ADJACENCY_REACHABILITY",
    "S08_PROVENANCE_UNRESOLVED",
)
BLOCKER_COUNTS = {
    "S01_SOURCE_IDENTITY": 0,
    "S02_ORIENTATION_COORDINATE_CHAIN": 0,
    "S03_SCALE_AND_DIMENSIONS": 0,
    "S04_OUTER_BOUNDARY": 0,
    "S05_WALL_GRAPH": 0,
    "S06_OPENINGS": 33,
    "S07_SPACES_ADJACENCY_REACHABILITY": 37,
    "S08_PROVENANCE_UNRESOLVED": 2,
}
FAIL_CLOSED = (
    "building_exterior_root_confirmation",
    "unit_root_confirmation",
    "off_frame_root_confirmation",
    "root_confirmation",
    "entrance_confirmation",
    "outside_side_confirmation",
    "traversability_confirmation",
    "pair_confirmation",
    "adjacency_confirmation",
    "reachability_confirmation",
    "graph_edge_admitted",
    "source_application_authorized",
    "source_correction_authorized",
    "semantic_promotion",
    "build_authorized",
    "ready",
)


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _without_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "candidate_hash"}


def _assert_fail_closed(value: Mapping[str, Any], *, context: str) -> None:
    for key in FAIL_CLOSED:
        if value.get(key) is not False:
            raise ValueError(f"{context} promoted or omitted {key}")
    if value.get("score_effect") != "none":
        raise ValueError(f"{context} score drift")


def _mode_common() -> dict[str, Any]:
    return {
        "policy_selected": False,
        "human_authorized": False,
        "building_exterior_root_confirmation": False,
        "unit_root_confirmation": False,
        "off_frame_root_confirmation": False,
        "root_confirmation": False,
        "entrance_confirmation": False,
        "outside_side_confirmation": False,
        "traversability_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
        "reachability_confirmation": False,
        "graph_edge_admitted": False,
        "source_application_authorized": False,
        "source_correction_authorized": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
    }


def _validate_live_gate(live: Mapping[str, Any], source_structure_hash: str) -> None:
    if (
        live.get("schema") != "goal-loop-v2-1308-live-gate-recompute-v1"
        or live.get("source_structure_hash") != source_structure_hash
        or live.get("source_score") != 65
        or tuple(live.get("hard_failures", [])) != HARD_FAILURES
        or live.get("blocker_counts") != BLOCKER_COUNTS
        or live.get("all_packets_fail_closed") is not True
        or live.get("build_authorized") is not False
        or live.get("ready") is not False
    ):
        raise ValueError("root-policy live-gate baseline drift")


def build(
    *,
    source_path: Path = SOURCE,
    exterior_root_path: Path = EXTERIOR_ROOT,
    op001_scope_path: Path = OP001_SCOPE,
    unit_reachability_path: Path = UNIT_REACHABILITY,
    live_gates_path: Path = LIVE_GATES,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    source_path = Path(source_path)
    exterior_root_path = Path(exterior_root_path)
    op001_scope_path = Path(op001_scope_path)
    unit_reachability_path = Path(unit_reachability_path)
    live_gates_path = Path(live_gates_path)

    source = validate_v21_document(_read_json(source_path))
    exterior = validate_exterior_root_search(
        _read_json(exterior_root_path),
        document=source,
        source_path=source_path,
        op001_scope_path=op001_scope_path,
    )
    unit_scope = validate_op001_unit_scope_candidate(source, _read_json(op001_scope_path))
    reachability = validate_unit_scope_reachability_v3(
        source,
        _read_json(unit_reachability_path),
    )
    live_gates = _read_json(live_gates_path)
    _validate_live_gate(live_gates, source["structure_hash"])

    if (
        exterior.get("actual_crossing_ids") != []
        or exterior.get("boundary_touch_ids") != []
        or exterior.get("near_miss_ids") != ["OP003"]
        or exterior.get("root_status") != "needs_human_or_source_policy"
        or exterior.get("root_confirmation") is not False
        or exterior.get("next_policy_gate", {}).get("automatic_model_authority") is not False
    ):
        raise ValueError("root-policy exterior-search input drift")
    if (
        unit_scope.get("building_scope_fact", {}).get("intersects_confirmed_outer_boundary") is not False
        or unit_scope.get("building_scope_fact", {}).get("building_exterior_root_confirmation") is not False
        or unit_scope.get("unit_scope_hypothesis", {}).get("unit_root_candidate") is not True
        or unit_scope.get("unit_scope_confirmation") is not False
        or unit_scope.get("traversability_confirmation") is not False
        or unit_scope.get("adjacency_confirmation") is not False
    ):
        raise ValueError("root-policy OP001 unit-scope input drift")
    if (
        reachability.get("root_hypothesis")
        != {
            "space_id": "common_core_circulation",
            "scope": "external_to_private_unit_candidate",
            "building_exterior_root_confirmation": False,
            "unit_root_confirmation": False,
        }
        or reachability.get("root_confirmation") is not False
        or reachability.get("reachability_confirmation") is not False
        or reachability.get("adjacency_confirmation") is not False
        or reachability["tiers"][-1].get("unreachable_scope_space_ids")
        != ["bedroom_02", "dry_balcony", "north_toilet"]
    ):
        raise ValueError("root-policy unit-reachability input drift")

    unit_hypothesis = unit_scope["unit_scope_hypothesis"]
    op001_source = unit_scope["source_snapshot"]
    modes = {
        "in_frame_exterior_root": {
            "label_zh": "楼栋外部 opening 新证据",
            "status": "disabled_no_source_crossing",
            "selectable_for_candidate_submission": False,
            "risk_class": "evidence_required",
            "current_evidence": {
                "actual_crossing_ids": [],
                "boundary_touch_ids": [],
                "near_miss_id": "OP003",
                "near_miss_distance_m": exterior["special_summaries"]["OP003"]["minimum_boundary_distance_m"],
                "near_miss_is_crossing": False,
            },
            "required_evidence": [
                "source_registered opening segment with actual outer-boundary crossing",
                "bounded outside region and bounded inside space",
                "confirmed host/effective void/jamb",
                "independent traversability proof and path trace",
                "human/source authority and independent verifier",
            ],
            "disabled_reason_zh": "当前没有 opening 与已确认外边界形成真实穿越；OP003 只是 near-miss。",
            "result_authority": "candidate_until_full_geometry_and_traversability_gate",
            **_mode_common(),
        },
        "unit_scope_root": {
            "label_zh": "单位范围 root 假设",
            "status": "recommended_available_for_candidate_submission",
            "selectable_for_candidate_submission": True,
            "risk_class": "human_policy_required",
            "current_evidence": {
                "opening_id": "OP001",
                "source_kind": op001_source["kind"],
                "source_status": op001_source["observation_status"],
                "host_atom_id": op001_source["host"]["owning_wall_atom_id"],
                "effective_void_record_status": op001_source["effective_void"]["status"],
                "source_traversable_value": op001_source["traversable"],
                "common_side_space_id": unit_hypothesis["common_side_space_id"],
                "private_unit_side_space_id": unit_hypothesis["unit_side_space_id"],
                "building_outer_boundary_intersection": False,
                "unit_root_candidate": "hypothesis",
                "entry_label_is_source_pixel_context_only": True,
                "record_fields_are_policy_confirmations": False,
            },
            "required_evidence": [
                "explicit human unit-scope policy selection",
                "bounded common-core and private-unit side acceptance",
                "independent OP001 traversability and barrier-removal proof",
                "independent adjacency/edge gate",
            ],
            "warning_zh": "只表示 common-core 到 private-unit 的范围假设，不是楼栋室外入口，也不确认通行或邻接。",
            "result_authority": "unit_scope_policy_candidate_only_not_building_exterior",
            **_mode_common(),
        },
        "off_frame_root_hypothesis": {
            "label_zh": "图外 root 假设（仅研究）",
            "status": "available_for_hypothesis_submission",
            "selectable_for_candidate_submission": True,
            "risk_class": "human_policy_and_hypothesis_only",
            "current_evidence": {
                "opening_id": None,
                "source_support": "not_observed_in_current_frame",
                "source_fact": False,
                "hypothesis_only": True,
            },
            "required_evidence": [
                "explicit human rationale for incomplete source frame",
                "adjacent plan sheet or source frame extension for later promotion",
                "registered exterior segment and full edge evidence for later promotion",
            ],
            "warning_zh": "只记录图幅可能不完整；不创建 opening、root geometry、graph edge 或 IFC 元素。",
            "result_authority": "hypothesis_only_no_opening_no_graph_edge",
            **_mode_common(),
        },
    }

    result = {
        "schema": "source-root-policy-candidate-v2",
        "source_structure_hash": source["structure_hash"],
        "source_document_sha256": _file_hash(source_path),
        "input_bindings": {
            "exterior_root_search": {
                "file_sha256": _file_hash(exterior_root_path),
                "candidate_hash": exterior["candidate_hash"],
            },
            "op001_unit_scope": {
                "file_sha256": _file_hash(op001_scope_path),
                "candidate_hash": unit_scope["candidate_hash"],
            },
            "unit_scope_reachability_v3": {
                "file_sha256": _file_hash(unit_reachability_path),
                "candidate_hash": reachability["candidate_hash"],
                "tier_d_unreachable_scope_space_ids": deepcopy(
                    reachability["tiers"][-1]["unreachable_scope_space_ids"]
                ),
                "reachability_confirmation": False,
            },
            "live_gates": {
                "file_sha256": _file_hash(live_gates_path),
                "source_score": live_gates["source_score"],
                "hard_failures": deepcopy(live_gates["hard_failures"]),
                "blocker_counts": deepcopy(live_gates["blocker_counts"]),
            },
        },
        "selection_contract": {
            "allowed_modes": list(POLICY_MODES),
            "mutually_exclusive": True,
            "default_selected_mode": None,
            "recommendation_is_selection": False,
            "human_selection_creates_confirmation": False,
            "human_selection_creates_graph_edge": False,
            "candidate_submission_button_zh": "提交 root 策略候选",
        },
        "recommended_policy_mode": "unit_scope_root",
        "recommendation_explanation_zh": (
            "图内没有楼栋外部 opening；OP001 只支持 common-core 与 lobby 的单位范围假设。"
            "此推荐不会自动选择策略，也不会确认外部入口、通行、邻接、评分或构建。"
        ),
        "selected_policy_mode": None,
        "human_decision": {
            "status": "pending",
            "policy_mode": None,
            "operator_id": None,
            "timestamp": None,
            "rationale": None,
            "reason_codes": [],
            "evidence_acknowledgements": [],
        },
        "modes": modes,
        "ui_spec_zh": {
            "title": "1308 root（范围起点假设）策略选择",
            "notice": (
                "当前没有任何 opening 穿过已确认的楼栋外边界。以下是三种待人工选择的解释策略，"
                "不是入口确认，也不授权切墙、邻接、通行、评分或正式建模。"
            ),
            "current_facts": [
                "楼栋外边界已确认，但未发现 opening 穿越它。",
                "OP003 距西侧边界 0.014637 m，是 near-miss，不是接触或穿越。",
                "OP001 位于楼栋内部；ENTRY 文字只是图像上下文。",
                "OP009/OP010 是阳台方向内部接口，不是楼栋外部入口。",
                "GATE 是场地/地块标签，不是 apartment opening。",
            ],
            "default_prompt": "请选择一种策略（默认不选）",
            "recommendation_banner": "系统推荐：单位范围 root 假设（未确认；不等于楼栋外部入口）",
            "options": {
                "in_frame_exterior_root": {
                    "label": "提交楼栋外部 opening 新证据（进入待审）",
                    "disabled": True,
                    "disabled_reason": modes["in_frame_exterior_root"]["disabled_reason_zh"],
                },
                "unit_scope_root": {
                    "label": "提交单位范围 root 假设",
                    "disabled": False,
                    "warning": modes["unit_scope_root"]["warning_zh"],
                    "helper_text": "提交后仍为待审核候选，不会改变模型或评分。",
                },
                "off_frame_root_hypothesis": {
                    "label": "提交图外 root 假设（不生成 opening）",
                    "disabled": False,
                    "warning": modes["off_frame_root_hypothesis"]["warning_zh"],
                    "rationale_required": True,
                    "rationale_placeholder": "请说明为什么当前图幅不足以观察外部 root。",
                },
            },
            "primary_button": "提交 root 策略候选",
            "primary_button_disabled_until_human_selection": True,
            "success_title": "Root 策略候选已记录（不是入口确认）",
            "success_statement": "本次没有确认任何入口。",
            "forbidden_ambiguous_labels": [
                "确认入口",
                "已确认楼栋入口",
                "补充入口",
                "创建入口",
            ],
        },
        "score_and_gate_impact": {
            "source_score_before": 65,
            "source_score_after": 65,
            "hard_failures_before": list(HARD_FAILURES),
            "hard_failures_after": list(HARD_FAILURES),
            "blocker_counts_before": deepcopy(BLOCKER_COUNTS),
            "blocker_counts_after": deepcopy(BLOCKER_COUNTS),
            "blocker_count_delta": {key: 0 for key in BLOCKER_COUNTS},
            "policy_packet_creation_changes_score": False,
            "policy_recommendation_changes_score": False,
            "human_unit_scope_selection_changes_score": False,
            "off_frame_hypothesis_changes_score": False,
        },
        "automatic_model_authority": False,
        "building_exterior_root_confirmation": False,
        "unit_root_confirmation": False,
        "off_frame_root_confirmation": False,
        "root_confirmation": False,
        "entrance_confirmation": False,
        "outside_side_confirmation": False,
        "traversability_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
        "reachability_confirmation": False,
        "graph_edge_admitted": False,
        "source_application_authorized": False,
        "source_correction_authorized": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _candidate_hash(_without_hash(result))
    return result if _skip_validate else validate(
        result,
        source_path=source_path,
        exterior_root_path=exterior_root_path,
        op001_scope_path=op001_scope_path,
        unit_reachability_path=unit_reachability_path,
        live_gates_path=live_gates_path,
    )


def validate(
    candidate: Mapping[str, Any],
    *,
    source_path: Path = SOURCE,
    exterior_root_path: Path = EXTERIOR_ROOT,
    op001_scope_path: Path = OP001_SCOPE,
    unit_reachability_path: Path = UNIT_REACHABILITY,
    live_gates_path: Path = LIVE_GATES,
) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(
        source_path=Path(source_path),
        exterior_root_path=Path(exterior_root_path),
        op001_scope_path=Path(op001_scope_path),
        unit_reachability_path=Path(unit_reachability_path),
        live_gates_path=Path(live_gates_path),
        _skip_validate=True,
    )
    if actual != expected:
        raise ValueError("source-root policy evidence/derivation drift")
    if (
        actual.get("schema") != "source-root-policy-candidate-v2"
        or actual.get("selection_contract", {}).get("allowed_modes") != list(POLICY_MODES)
        or actual.get("selection_contract", {}).get("mutually_exclusive") is not True
        or actual.get("selection_contract", {}).get("recommendation_is_selection") is not False
        or actual.get("selection_contract", {}).get("human_selection_creates_confirmation") is not False
        or actual.get("selection_contract", {}).get("human_selection_creates_graph_edge") is not False
        or actual.get("recommended_policy_mode") != "unit_scope_root"
        or actual.get("selected_policy_mode") is not None
        or actual.get("human_decision", {}).get("status") != "pending"
        or actual.get("human_decision", {}).get("policy_mode") is not None
        or tuple(actual.get("modes", {})) != POLICY_MODES
        or actual.get("automatic_model_authority") is not False
    ):
        raise ValueError("source-root policy selection/recommendation drift")
    _assert_fail_closed(actual, context="source-root policy candidate")
    for mode_id, mode in actual["modes"].items():
        _assert_fail_closed(mode, context=f"source-root policy mode {mode_id}")
        if mode.get("policy_selected") is not False or mode.get("human_authorized") is not False:
            raise ValueError(f"source-root policy mode {mode_id} was selected")
    if (
        actual["modes"]["in_frame_exterior_root"]["selectable_for_candidate_submission"] is not False
        or actual["modes"]["in_frame_exterior_root"]["current_evidence"]["actual_crossing_ids"] != []
        or actual["modes"]["unit_scope_root"]["selectable_for_candidate_submission"] is not True
        or actual["modes"]["unit_scope_root"]["current_evidence"]["building_outer_boundary_intersection"] is not False
        or actual["modes"]["unit_scope_root"]["current_evidence"]["unit_root_candidate"] != "hypothesis"
        or actual["modes"]["unit_scope_root"]["current_evidence"]["record_fields_are_policy_confirmations"] is not False
        or actual["modes"]["off_frame_root_hypothesis"]["selectable_for_candidate_submission"] is not True
        or actual["modes"]["off_frame_root_hypothesis"]["current_evidence"]["opening_id"] is not None
        or actual["modes"]["off_frame_root_hypothesis"]["current_evidence"]["hypothesis_only"] is not True
        or actual["modes"]["off_frame_root_hypothesis"]["graph_edge_admitted"] is not False
    ):
        raise ValueError("source-root policy mode boundary drift")
    ui = actual["ui_spec_zh"]
    if (
        ui.get("primary_button") != "提交 root 策略候选"
        or ui.get("primary_button_disabled_until_human_selection") is not True
        or ui.get("options", {}).get("in_frame_exterior_root", {}).get("disabled") is not True
        or ui.get("options", {}).get("off_frame_root_hypothesis", {}).get("rationale_required") is not True
        or ui.get("options", {}).get("unit_scope_root", {}).get("helper_text")
        != "提交后仍为待审核候选，不会改变模型或评分。"
        or ui.get("options", {}).get("off_frame_root_hypothesis", {}).get("rationale_placeholder")
        != "请说明为什么当前图幅不足以观察外部 root。"
        or ui.get("success_statement") != "本次没有确认任何入口。"
        or "确认入口" not in ui.get("forbidden_ambiguous_labels", [])
    ):
        raise ValueError("source-root policy human UI safety drift")
    impact = actual["score_and_gate_impact"]
    if (
        impact.get("source_score_before") != 65
        or impact.get("source_score_after") != 65
        or impact.get("hard_failures_before") != list(HARD_FAILURES)
        or impact.get("hard_failures_after") != list(HARD_FAILURES)
        or impact.get("blocker_counts_before") != BLOCKER_COUNTS
        or impact.get("blocker_counts_after") != BLOCKER_COUNTS
        or any(value != 0 for value in impact.get("blocker_count_delta", {}).values())
    ):
        raise ValueError("source-root policy score/gate impact drift")
    if actual.get("candidate_hash") != _candidate_hash(_without_hash(actual)):
        raise ValueError("source-root policy candidate hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--exterior-root", type=Path, default=EXTERIOR_ROOT)
    parser.add_argument("--op001-scope", type=Path, default=OP001_SCOPE)
    parser.add_argument("--unit-reachability", type=Path, default=UNIT_REACHABILITY)
    parser.add_argument("--live-gates", type=Path, default=LIVE_GATES)
    parser.add_argument("--output", type=Path, default=OUT / "candidate.json")
    args = parser.parse_args(argv)
    result = build(
        source_path=args.source,
        exterior_root_path=args.exterior_root,
        op001_scope_path=args.op001_scope,
        unit_reachability_path=args.unit_reachability,
        live_gates_path=args.live_gates,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output.parent / "REPORT.md").write_text(
        "# Source root policy candidate v2\n\n"
        "No policy is selected. The system recommends the unit-scope hypothesis because OP001 has source-bound "
        "common-core/lobby evidence while no opening crosses the confirmed building outer boundary. Recommendation "
        "is not selection; human selection is not confirmation; confirmation is not graph-edge admission. In-frame "
        "exterior evidence is currently disabled, unit-scope submission remains building-exterior false, and an "
        "off-frame submission remains hypothesis-only with no opening or graph edge. S06/S07/S08, their blocker "
        "counts, the 65/100 source score, source data, and Blender/IFC authorization are unchanged.\n",
        encoding="utf-8",
    )
    (args.output.parent / "REVIEW_CARD_ZH.md").write_text(
        "# 1308 外部/单位 root 策略候选 v2\n\n"
        "当前默认不选任何策略。系统只推荐“单位范围 root 假设”，原因是 OP001 具备 common-core 到 lobby "
        "的候选证据，而图内没有 opening 穿越楼栋外边界。推荐不等于选择，人工选择不等于确认，确认也不"
        "等于加入 graph edge。楼栋外部选项因缺少真实穿越而禁用；单位范围选项不代表室外入口；图外选项"
        "只能记录假设，不生成 opening 或图边。主按钮应叫“提交 root 策略候选”，不能叫“确认入口”。本"
        "次没有确认任何入口。本候选不改变 S06/S07/S08、blocker 数、65/100 评分、源数据或 Blender/IFC "
        "正式构建权限。\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BLOCKER_COUNTS",
    "FAIL_CLOSED",
    "HARD_FAILURES",
    "POLICY_MODES",
    "_candidate_hash",
    "build",
    "validate",
]
