"""Build a source-bound, fail-closed opening-edge admissibility register.

The register compares pair candidates from distinct evidence layers without
collapsing them into adjacency truth. No row is admitted to the graph.
"""
from __future__ import annotations

import argparse
from collections import Counter
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
from tools.goal_loop_v2.build_layer3a_subtype_register import validate as validate_layer3a
from tools.goal_loop_v2.build_partition_resolved_cut_pair import validate as validate_partition_pairs
from tools.goal_loop_v2.build_source_connectivity_defect_candidate import validate as validate_connectivity
from tools.goal_loop_v2.build_source_root_policy_candidate import validate as validate_root_policy
from tools.goal_loop_v2.candidate_opening_cut_impact import validate_candidate_opening_cut_impact
from tools.goal_loop_v2.correction_candidate_registry import validate_registry
from tools.goal_loop_v2.demoted_portal_deny import PORTAL_ID, build_demoted_portal_deny
from tools.goal_loop_v2.op001_unit_scope_candidate import validate_op001_unit_scope_candidate
from tools.goal_loop_v2.unit_scope_reachability_v3 import validate_unit_scope_reachability_v3

SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
LAYER3A = ROOT / "reports/layer3a_subtype_register_20260903/register.json"
CUT_MATRIX = ROOT / "reports/candidate_opening_cut_impact_20260902/candidate-opening-cut-impact.json"
PARTITION_PAIRS = ROOT / "reports/partition_resolved_cut_pair_20260902/partition-resolved-cut-pair.json"
CORRECTION_REGISTRY = ROOT / "reports/correction_candidate_registry_20260902/correction-candidate-registry.json"
UNIT_REACHABILITY = ROOT / "reports/unit_scope_reachability_v3_20260902/unit-scope-reachability-v3.json"
OP001_SCOPE = ROOT / "reports/op001_unit_scope_candidate_20260902/op001-unit-scope-candidate.json"
SOURCE_CONNECTIVITY = ROOT / "reports/source_connectivity_defect_candidate_20260902/candidate.json"
ROOT_POLICY = ROOT / "reports/source_root_policy_candidate_20260903/candidate.json"
LIVE_GATES = ROOT / "reports/live_gate_recompute_20260902/live-gate-recompute.json"
OUT = ROOT / "reports/source_bound_edge_admissibility_register_20260903"

ROW_IDS = tuple([f"OP{index:03d}" for index in range(1, 12)] + [PORTAL_ID, "OP012"])
LAYER3A_IDS = ("OP001", "OP002", "OP003", "OP004", "OP006", "OP007", "OP008", "OP009", "OP010")
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
ROW_FAIL_CLOSED = (
    "pair_confirmation",
    "host_confirmation",
    "effective_void_confirmation",
    "jamb_confirmation",
    "traversability_confirmation",
    "adjacency_confirmation",
    "root_confirmation",
    "edge_confirmation",
    "graph_edge_admitted",
    "source_application_authorized",
    "source_correction_authorized",
    "semantic_promotion",
    "build_authorized",
    "ready",
)
BATCH_FAIL_CLOSED = (
    "any_pair_confirmed",
    "any_edge_admitted",
    "root_confirmation",
    "traversability_confirmation",
    "pair_confirmation",
    "adjacency_confirmation",
    "reachability_confirmation",
    "source_application_authorized",
    "source_correction_authorized",
    "semantic_promotion",
    "build_authorized",
    "ready",
)

HUMAN_STATUS = {
    "OP001": "内部单位范围候选；ENTRY 不是楼栋外部入口。",
    "OP002": "二维开口候选较强，但两侧空间证据互相冲突，尚未加入连接图。",
    "OP003": "距西外边界 0.014637 m 的内部 near-miss；不是边界接触、穿越或 root。",
    "OP004": "两侧候选指向二号卧室与北侧卫生间，但两者仍处于未接入区域。",
    "OP005": "当前源记录为 unknown/evidence-only，不能建立候选边。",
    "OP006": "卧室三与走廊是候选两侧；交叉墙节点和开口证据仍未闭环。",
    "OP007": "lobby 与 WC 是候选两侧；公共区标签不能直接变成房间连接。",
    "OP008": "bath 与 lobby 只是一个候选；bath/WC 侧仍有明显歧义。",
    "OP009": "后阳台界面两侧候选一致，但 glazed 类型和可开启/通行仍未确认。",
    "OP010": "厨房与前阳台是候选两侧；宽玻璃界面不等于楼栋外部 root。",
    "OP011": "厨房到 dry_balcony（候选阳台）可能跨出当前候选连通区，但类型审查冲突，不能入图。",
    PORTAL_ID: "历史降级 portal，当前硬拒绝，不进入 opening edge。",
    "OP012": "历史恢复假设已隔离，不属于当前 source opening，也不进入连接图。",
}

PRIORITY = {
    "OP002": {"rank": 1, "label_zh": "最高（下一条）"},
    "OP008": {"rank": 2, "label_zh": "最高"},
    "OP011": {"rank": 3, "label_zh": "最高"},
    "OP001": {"rank": 4, "label_zh": "高"},
    "OP003": {"rank": 5, "label_zh": "高"},
    "OP004": {"rank": 6, "label_zh": "高"},
    "OP006": {"rank": 7, "label_zh": "高"},
    "OP009": {"rank": 8, "label_zh": "高"},
    "OP010": {"rank": 9, "label_zh": "高"},
    "OP007": {"rank": 10, "label_zh": "中高"},
    "OP005": {"rank": None, "label_zh": "当前排除"},
    PORTAL_ID: {"rank": None, "label_zh": "硬拒绝"},
    "OP012": {"rank": None, "label_zh": "已隔离"},
}


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _without_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "candidate_hash"}


def _pair_key(side_a: str, side_b: str) -> list[str]:
    return sorted((side_a, side_b))


def _pair_evidence(source: str, side_a: str, side_b: str, authority: str, **extra: Any) -> dict[str, Any]:
    return {
        "evidence_source": source,
        "space_a_id": side_a,
        "space_b_id": side_b,
        "unordered_pair": _pair_key(side_a, side_b),
        "authority": authority,
        "confirmation": False,
        **extra,
    }


def _assert_fail_closed(value: Mapping[str, Any], keys: tuple[str, ...], *, context: str) -> None:
    for key in keys:
        if value.get(key) is not False:
            raise ValueError(f"{context} promoted or omitted {key}")
    if value.get("score_effect") != "none":
        raise ValueError(f"{context} score drift")


def _validate_live_gates(value: Mapping[str, Any], structure_hash: str) -> None:
    if (
        value.get("schema") != "goal-loop-v2-1308-live-gate-recompute-v1"
        or value.get("source_structure_hash") != structure_hash
        or value.get("source_score") != 65
        or tuple(value.get("hard_failures", [])) != HARD_FAILURES
        or value.get("blocker_counts") != BLOCKER_COUNTS
        or value.get("all_packets_fail_closed") is not True
        or value.get("build_authorized") is not False
        or value.get("ready") is not False
    ):
        raise ValueError("edge register live-gate baseline drift")


def _source_state(opening: Mapping[str, Any]) -> dict[str, Any]:
    host = opening.get("host") if isinstance(opening.get("host"), Mapping) else None
    effective = opening.get("effective_void") if isinstance(opening.get("effective_void"), Mapping) else None
    before = opening.get("jamb_before") if isinstance(opening.get("jamb_before"), Mapping) else None
    after = opening.get("jamb_after") if isinstance(opening.get("jamb_after"), Mapping) else None
    return {
        "opening_status": opening["status"],
        "build_disposition": opening["build_disposition"],
        "build_kind": opening["build_kind"],
        "source_observation_kind": opening["source_observation"]["kind"],
        "source_observation_status": opening["source_observation"]["status"],
        "source_segment_m": deepcopy(opening["source_observation"]["nominal_segment_m"]),
        "source_host_atom_id": host.get("owning_wall_atom_id") if host else None,
        "effective_void_record_status": effective.get("status") if effective else None,
        "effective_void_record_segment_m": deepcopy(effective.get("segment_m")) if effective else None,
        "jamb_before_record_status": before.get("status") if before else None,
        "jamb_after_record_status": after.get("status") if after else None,
        "source_side_a_space_id": opening.get("side_a_space_id"),
        "source_side_b_space_id": opening.get("side_b_space_id"),
        "source_traversable_value": opening["traversable"],
        "record_fields_are_edge_confirmations": False,
    }


def _row_blockers(opening_id: str, pair_status: str) -> list[str]:
    common = ["TRAVERSABILITY_UNCONFIRMED", "ADJACENCY_UNCONFIRMED", "HUMAN_ACCEPTANCE_PENDING"]
    special = {
        "OP001": ["BUILDING_EXTERIOR_ROOT_FALSE", "UNIT_ROOT_POLICY_PENDING"],
        "OP002": ["PAIR_SOURCE_CONFLICT", "SOURCE_EFFECTIVE_VOID_CANDIDATE", "SOURCE_APPLICATION_PENDING"],
        "OP003": ["RETURN_WALL_ENDPOINT_UNRESOLVED", "SOURCE_HOST_EFFECTIVE_VOID_MISSING"],
        "OP004": ["SOURCE_HOST_EFFECTIVE_VOID_MISSING", "UNREACHABLE_COMPONENT_INTERNAL_ONLY"],
        "OP005": ["UNKNOWN_EVIDENCE_ONLY", "PAIR_MISSING", "CURRENT_NORMAL_PATH_EXCLUDED"],
        "OP006": ["J007_CROSSING_CONTEXT", "SOURCE_HOST_EFFECTIVE_VOID_MISSING"],
        "OP007": ["PUBLIC_WC_SEMANTIC_RISK", "SOURCE_HOST_EFFECTIVE_VOID_MISSING"],
        "OP008": ["PAIR_AMBIGUOUS_BATH_OR_WC", "RETURN_WALL_VLM_CONFLICT"],
        "OP009": ["GLAZED_OPERATION_UNCONFIRMED", "SOURCE_HOST_EFFECTIVE_VOID_MISSING"],
        "OP010": ["BROAD_GLAZED_INTERFACE_UNRESOLVED", "SOURCE_HOST_EFFECTIVE_VOID_MISSING"],
        "OP011": ["SOURCE_VLM_TYPE_CONFLICT", "EFFECTIVE_VOID_MISSING", "CANDIDATE_COMPONENT_BOUNDARY_NOT_ADMITTED"],
        PORTAL_ID: ["HARD_DENIED_DUPLICATE_OR_WRONG_AXIS", "ALL_EDGE_CONSUMERS_FORBIDDEN"],
        "OP012": ["NOT_IN_SOURCE_OPENINGS", "RECOVERY_REVIEW_CONFLICT", "QUARANTINED_HISTORY"],
    }
    blockers = list(special[opening_id])
    if pair_status == "missing":
        blockers.append("PAIR_CANDIDATE_MISSING")
    elif pair_status == "conflicting_candidates":
        blockers.append("PAIR_CANDIDATES_DISAGREE")
    elif pair_status == "ambiguous_candidates":
        blockers.append("PAIR_CANDIDATES_NOT_UNIQUE")
    if opening_id not in {PORTAL_ID, "OP012", "OP005"}:
        blockers.extend(common)
    return blockers


def build(
    *,
    source_path: Path = SOURCE,
    layer3a_path: Path = LAYER3A,
    cut_matrix_path: Path = CUT_MATRIX,
    partition_pairs_path: Path = PARTITION_PAIRS,
    correction_registry_path: Path = CORRECTION_REGISTRY,
    unit_reachability_path: Path = UNIT_REACHABILITY,
    op001_scope_path: Path = OP001_SCOPE,
    source_connectivity_path: Path = SOURCE_CONNECTIVITY,
    root_policy_path: Path = ROOT_POLICY,
    live_gates_path: Path = LIVE_GATES,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    paths = {
        "source": Path(source_path),
        "layer3a": Path(layer3a_path),
        "cut_matrix": Path(cut_matrix_path),
        "partition_pairs": Path(partition_pairs_path),
        "correction_registry": Path(correction_registry_path),
        "unit_reachability": Path(unit_reachability_path),
        "op001_scope": Path(op001_scope_path),
        "source_connectivity": Path(source_connectivity_path),
        "root_policy": Path(root_policy_path),
        "live_gates": Path(live_gates_path),
    }
    source = validate_v21_document(_read_json(paths["source"]))
    layer3a = validate_layer3a(_read_json(paths["layer3a"]))
    cut_matrix = validate_candidate_opening_cut_impact(source, _read_json(paths["cut_matrix"]))
    partition = validate_partition_pairs(source, _read_json(paths["partition_pairs"]))
    registry = validate_registry(source, _read_json(paths["correction_registry"]))
    reachability = validate_unit_scope_reachability_v3(source, _read_json(paths["unit_reachability"]))
    op001_scope = validate_op001_unit_scope_candidate(source, _read_json(paths["op001_scope"]))
    connectivity = validate_connectivity(_read_json(paths["source_connectivity"]))
    root_policy = validate_root_policy(_read_json(paths["root_policy"]))
    live_gates = _read_json(paths["live_gates"])
    _validate_live_gates(live_gates, source["structure_hash"])
    deny = build_demoted_portal_deny(source, paths["source"])

    if (
        layer3a.get("opening_ids") != list(LAYER3A_IDS)
        or layer3a.get("all_subtypes_source_confirmed") is not False
        or partition.get("opening_ids") != ["OP002", "OP006", "OP007", "OP008", "OP010"]
        or registry.get("opening_ids") != ["OP002", "OP004", "OP009"]
        or root_policy.get("selected_policy_mode") is not None
        or root_policy.get("root_confirmation") is not False
        or connectivity.get("unreachable_space_ids") != ["bedroom_02", "dry_balcony", "north_toilet"]
    ):
        raise ValueError("edge register upstream coverage/authority drift")

    source_openings = {opening["id"]: opening for opening in source["opening_contract"]["openings"]}
    if tuple(source_openings) != ROW_IDS[:-1]:
        raise ValueError("edge register source opening order/coverage drift")
    layer3a_rows = {row["opening_id"]: row for row in layer3a["rows"]}
    cut_rows = {row["opening_id"]: row for row in cut_matrix["openings"]}
    partition_rows = {row["opening_id"]: row for row in partition["openings"]}
    registry_rows = {row["opening_id"]: row for row in registry["candidates"]}
    tier_d = reachability["tiers"][-1]
    tier_edges = [edge for edge in tier_d["edges"] if edge.get("opening_id")]
    tier_by_opening = {edge["opening_id"]: edge for edge in tier_edges}
    source_adjacency = {edge["opening_id"]: edge for edge in source["adjacency_truth"]["edges"]}
    connectivity_edges = {edge["opening_id"]: edge for edge in connectivity["candidate_edges"]}
    reachable = set(tier_d["reachable_space_ids"])
    unreachable = set(tier_d["unreachable_scope_space_ids"])

    rows: list[dict[str, Any]] = []
    for opening_id in ROW_IDS:
        source_opening = source_openings.get(opening_id)
        pair_evidence: list[dict[str, Any]] = []
        ambiguity_evidence: list[dict[str, Any]] = []

        if source_opening and source_opening.get("side_a_space_id") and source_opening.get("side_b_space_id"):
            pair_evidence.append(
                _pair_evidence(
                    "source_opening_record_sides",
                    source_opening["side_a_space_id"],
                    source_opening["side_b_space_id"],
                    "source_record_candidate_not_edge_confirmation",
                )
            )
        if opening_id in source_adjacency:
            edge = source_adjacency[opening_id]
            pair_evidence.append(
                _pair_evidence(
                    "source_adjacency_candidate",
                    edge["space_a_id"],
                    edge["space_b_id"],
                    "source_adjacency_status_candidate",
                    edge_id=edge["id"],
                    source_edge_status=edge["status"],
                )
            )
        if opening_id == "OP001":
            hypothesis = op001_scope["unit_scope_hypothesis"]
            pair_evidence.append(
                _pair_evidence(
                    "op001_unit_scope_hypothesis",
                    hypothesis["common_side_space_id"],
                    hypothesis["unit_side_space_id"],
                    "unit_scope_hypothesis_not_adjacency",
                )
            )
        if opening_id in registry_rows:
            registry_row = registry_rows[opening_id]
            sides = registry_row["directed_side_assignment"]
            pair_evidence.append(
                _pair_evidence(
                    "correction_registry_candidate",
                    sides["side_a"],
                    sides["side_b"],
                    "non_applying_2d_correction_candidate",
                    packet_hash=registry_row["packet_hash"],
                )
            )
        if opening_id in partition_rows:
            partition_row = partition_rows[opening_id]
            sides = partition_row["directed_side_assignment"]
            if sides:
                pair_evidence.append(
                    _pair_evidence(
                        "partition_resolved_candidate",
                        sides["side_a"],
                        sides["side_b"],
                        "research_partition_candidate",
                        sensitivity_stable=partition_row["sensitivity_stable"],
                    )
                )
            else:
                ambiguity_evidence.append(
                    {
                        "evidence_source": "partition_resolved_candidate",
                        "public_space_id": partition_row["public_cell_id"],
                        "other_side_space_options": deepcopy(partition_row["non_public_anchor_ids"]),
                        "classification": partition_row["classification"],
                        "confirmation": False,
                    }
                )
        if opening_id in tier_by_opening:
            edge = tier_by_opening[opening_id]
            pair_evidence.append(
                _pair_evidence(
                    "unit_scope_reachability_tier_d_research_edge",
                    edge["space_a_id"],
                    edge["space_b_id"],
                    "research_graph_edge_confirmation_false",
                    edge_kind=edge["kind"],
                )
            )
        if opening_id == "OP011":
            edge = connectivity_edges["OP011"]
            pair_evidence.append(
                _pair_evidence(
                    "validated_op011_host_scope_with_review_conflict",
                    edge["relation"][0],
                    edge["relation"][1],
                    edge["authority"],
                    clean_provider_disagreement=edge["clean_provider_disagreement"],
                )
            )

        pair_sets = sorted({tuple(item["unordered_pair"]) for item in pair_evidence})
        if opening_id == PORTAL_ID:
            pair_status = "hard_denied"
        elif opening_id == "OP012":
            pair_status = "quarantined_history"
        elif opening_id == "OP008" and ambiguity_evidence:
            pair_status = "ambiguous_candidates"
        elif len(pair_sets) > 1:
            pair_status = "conflicting_candidates"
        elif len(pair_sets) == 1:
            pair_status = "unique_candidate_pair_unconfirmed"
        else:
            pair_status = "missing"

        cut_row = cut_rows.get(opening_id)
        registry_row = registry_rows.get(opening_id)
        partition_row = partition_rows.get(opening_id)
        connectivity_row = connectivity_edges.get(opening_id)
        if source_opening and source_opening.get("host"):
            host_atom_id = source_opening["host"]["owning_wall_atom_id"]
            host_status = "source_record_present_not_edge_confirmation"
        elif cut_row and cut_row.get("host_atom_id"):
            host_atom_id = cut_row["host_atom_id"]
            host_status = "registered_evidence_candidate"
        elif opening_id == "OP011":
            host_atom_id = "ATOM-WB022-01"
            host_status = "validated_host_scope_candidate"
        else:
            host_atom_id = None
            host_status = "hard_denied" if opening_id == PORTAL_ID else "missing_or_quarantined"

        if source_opening:
            source_state = _source_state(source_opening)
        else:
            source_state = {
                "opening_status": "not_in_source_opening_contract",
                "build_disposition": "quarantined_history",
                "build_kind": None,
                "source_observation_kind": None,
                "source_observation_status": None,
                "source_segment_m": None,
                "source_host_atom_id": None,
                "effective_void_record_status": None,
                "effective_void_record_segment_m": None,
                "jamb_before_record_status": None,
                "jamb_after_record_status": None,
                "source_side_a_space_id": None,
                "source_side_b_space_id": None,
                "source_traversable_value": False,
                "record_fields_are_edge_confirmations": False,
            }

        if opening_id in {"OP001", "OP002"}:
            effective_status = "source_record_present_not_edge_confirmation"
        else:
            effective_status = "missing_or_quarantined"
        if opening_id == "OP001":
            jamb_status = "source_record_confirmed_not_edge_confirmation"
        elif opening_id == "OP002":
            jamb_status = "source_record_candidate"
        elif registry_row:
            jamb_status = "registry_scalar_candidate"
        elif opening_id == "OP011":
            jamb_status = "host_scope_scalar_candidate"
        else:
            jamb_status = "missing_or_unresolved"

        pair_memberships = []
        for pair in pair_sets:
            pair_memberships.append(
                {
                    "unordered_pair": list(pair),
                    "space_membership": {
                        space_id: (
                            "candidate_reachable"
                            if space_id in reachable
                            else "candidate_unreachable"
                            if space_id in unreachable
                            else "outside_unit_scope_or_unknown"
                        )
                        for space_id in pair
                    },
                    "crosses_candidate_reachable_boundary": len(set(pair) & reachable) == 1
                    and len(set(pair) & unreachable) == 1,
                    "internal_to_candidate_unreachable_component": set(pair).issubset(unreachable),
                    "confirmation": False,
                }
            )
        crosses_reachable = any(item["crosses_candidate_reachable_boundary"] for item in pair_memberships)
        internal_unreachable = any(item["internal_to_candidate_unreachable_component"] for item in pair_memberships)

        visual = None
        if opening_id in layer3a_rows:
            visual = {
                "candidate": layer3a_rows[opening_id]["visual_subtype_candidate"],
                "status": "visual_advisory_only",
                "source_subtype_confirmation": False,
            }
        elif opening_id == "OP011":
            visual = {
                "candidate": "glazed_interface_with_provider_conflict",
                "status": "source_vlm_conflict",
                "source_subtype_confirmation": False,
            }

        row = {
            "edge_id": f"EDGE-REGISTER-{opening_id}",
            "opening_id": opening_id,
            "source_record_state": source_state,
            "registered_segment_m": (
                deepcopy(cut_row["segment_m"])
                if cut_row and cut_row.get("segment_m")
                else deepcopy(source_state["source_segment_m"])
            ),
            "host_atom_id": host_atom_id,
            "host_status": host_status,
            "wall_break_status": (
                "research_cut_impact_candidate"
                if cut_row and cut_row.get("cuttable")
                else "missing_or_not_cuttable"
            ),
            "cut_impact_classification": cut_row.get("classification") if cut_row else "not_in_cut_matrix",
            "effective_void_status": effective_status,
            "jamb_status": jamb_status,
            "visual_subtype": visual,
            "pair_evidence": pair_evidence,
            "pair_ambiguity_evidence": ambiguity_evidence,
            "pair_candidate_sets": [list(pair) for pair in pair_sets],
            "pair_candidate_count": len(pair_sets),
            "pair_status": pair_status,
            "selected_pair": None,
            "candidate_graph_membership": pair_memberships,
            "crosses_candidate_reachable_boundary": crosses_reachable,
            "internal_to_candidate_unreachable_component": internal_unreachable,
            "root_relevance": (
                "unit_scope_hypothesis_only"
                if opening_id == "OP001"
                else "near_miss_not_root"
                if opening_id == "OP003"
                else "balcony_interface_not_building_root"
                if opening_id in {"OP009", "OP010"}
                else "hard_denied"
                if opening_id == PORTAL_ID
                else "quarantined_history"
                if opening_id == "OP012"
                else "none"
            ),
            "source_connectivity_evidence": deepcopy(connectivity_row),
            "priority": deepcopy(PRIORITY[opening_id]),
            "human_readable_status_zh": HUMAN_STATUS[opening_id],
            "remaining_blockers": _row_blockers(opening_id, pair_status),
            "eligible_for_targeted_room_side_review": opening_id in LAYER3A_IDS,
            "pair_confirmation": False,
            "host_confirmation": False,
            "effective_void_confirmation": False,
            "jamb_confirmation": False,
            "traversability_confirmation": False,
            "adjacency_confirmation": False,
            "root_confirmation": False,
            "edge_confirmation": False,
            "graph_edge_admitted": False,
            "source_application_authorized": False,
            "source_correction_authorized": False,
            "semantic_promotion": False,
            "score_effect": "none",
            "build_authorized": False,
            "ready": False,
            "human_readable_boundary": {
                "named_sides_are_not_a_confirmed_pair": True,
                "visual_subtype_is_not_edge_confirmation": True,
                "cuttable_is_not_wall_break_authority": True,
                "candidate_reachability_is_not_reachability_confirmation": True,
                "coverage_is_not_completion": True,
            },
        }
        row["candidate_hash"] = _candidate_hash(_without_hash(row))
        rows.append(row)

    status_counts = dict(sorted(Counter(row["pair_status"] for row in rows).items()))
    human_rows = [
        {
            "opening_id": row["opening_id"],
            "pair_display": (
                " / ".join(" ↔ ".join(pair) for pair in row["pair_candidate_sets"])
                if row["pair_candidate_sets"]
                else "不显示两侧空间"
            ),
            "status": row["human_readable_status_zh"],
            "priority": row["priority"]["label_zh"],
            "next_action": (
                "生成 OP002 独立两侧空间复核任务"
                if row["opening_id"] == "OP002"
                else "查看冲突与缺失证据"
                if row["priority"]["rank"] is not None
                else "保持排除/隔离"
            ),
        }
        for row in rows
    ]
    result = {
        "schema": "source-bound-edge-admissibility-register-v1",
        "source_structure_hash": source["structure_hash"],
        "source_document_sha256": _file_hash(paths["source"]),
        "input_bindings": {
            name: {
                "file_sha256": _file_hash(path),
                "candidate_hash": (
                    value.get("candidate_hash") if isinstance(value, Mapping) else None
                ),
            }
            for name, path, value in (
                ("layer3a", paths["layer3a"], layer3a),
                ("cut_matrix", paths["cut_matrix"], cut_matrix),
                ("partition_pairs", paths["partition_pairs"], partition),
                ("correction_registry", paths["correction_registry"], registry),
                ("unit_reachability", paths["unit_reachability"], reachability),
                ("op001_scope", paths["op001_scope"], op001_scope),
                ("source_connectivity", paths["source_connectivity"], connectivity),
                ("root_policy", paths["root_policy"], root_policy),
                ("live_gates", paths["live_gates"], live_gates),
            )
        },
        "demoted_portal_deny_candidate_hash": deny["candidate_hash"],
        "row_ids": list(ROW_IDS),
        "row_count": len(rows),
        "rows": rows,
        "pair_status_counts": status_counts,
        "conflicting_pair_ids": [row["opening_id"] for row in rows if row["pair_status"] == "conflicting_candidates"],
        "ambiguous_pair_ids": [row["opening_id"] for row in rows if row["pair_status"] == "ambiguous_candidates"],
        "missing_pair_ids": [row["opening_id"] for row in rows if row["pair_status"] == "missing"],
        "hard_denied_ids": [row["opening_id"] for row in rows if row["pair_status"] == "hard_denied"],
        "quarantined_history_ids": [row["opening_id"] for row in rows if row["pair_status"] == "quarantined_history"],
        "candidate_reachable_space_ids": sorted(reachable),
        "candidate_unreachable_space_ids": sorted(unreachable),
        "next_target": {
            "opening_id": "OP002",
            "task": "targeted_source_bound_room_side_evidence",
            "reason": "strong geometry chain but source adjacency conflicts with source-record/correction bedroom-corridor side",
            "required_output": "candidate evidence only; no pair, traversal, adjacency, root, score, or build promotion",
        },
        "human_table": {
            "title": "Opening 两侧空间核对表（候选，不是房间确认）",
            "notice": "以下两侧空间均为候选，不是已确认的房间对；候选边不等于真实开口、可通行或已加入邻接图。",
            "columns": ["编号", "两侧空间候选", "当前人话结论", "下一步", "优先级"],
            "rows": human_rows,
            "forbidden_current_actions": [
                "确认房间连接",
                "确认入口",
                "确认可通行",
                "加入邻接图",
                "生成正式墙洞",
                "提升评分",
                "完成构建",
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
        },
        "current_pointer_update_authorized": False,
        "current_pointer_update_next": "only_after_independent_matrix_verification",
        "any_pair_confirmed": False,
        "any_edge_admitted": False,
        "root_confirmation": False,
        "traversability_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
        "reachability_confirmation": False,
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
        source_path=paths["source"],
        layer3a_path=paths["layer3a"],
        cut_matrix_path=paths["cut_matrix"],
        partition_pairs_path=paths["partition_pairs"],
        correction_registry_path=paths["correction_registry"],
        unit_reachability_path=paths["unit_reachability"],
        op001_scope_path=paths["op001_scope"],
        source_connectivity_path=paths["source_connectivity"],
        root_policy_path=paths["root_policy"],
        live_gates_path=paths["live_gates"],
    )


def validate(candidate: Mapping[str, Any], **paths: Path) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(_skip_validate=True, **paths)
    if actual != expected:
        raise ValueError("edge admissibility evidence/derivation drift")
    if (
        actual.get("schema") != "source-bound-edge-admissibility-register-v1"
        or actual.get("row_ids") != list(ROW_IDS)
        or actual.get("row_count") != 13
        or actual.get("conflicting_pair_ids") != ["OP002"]
        or actual.get("ambiguous_pair_ids") != ["OP008"]
        or actual.get("missing_pair_ids") != ["OP005"]
        or actual.get("hard_denied_ids") != [PORTAL_ID]
        or actual.get("quarantined_history_ids") != ["OP012"]
        or actual.get("candidate_unreachable_space_ids") != ["bedroom_02", "dry_balcony", "north_toilet"]
        or actual.get("next_target", {}).get("opening_id") != "OP002"
        or actual.get("current_pointer_update_authorized") is not False
    ):
        raise ValueError("edge admissibility coverage/status drift")
    _assert_fail_closed(actual, BATCH_FAIL_CLOSED, context="edge admissibility register")
    if actual.get("candidate_hash") != _candidate_hash(_without_hash(actual)):
        raise ValueError("edge admissibility register candidate hash drift")
    rows = {row["opening_id"]: row for row in actual["rows"]}
    if len(rows) != 13:
        raise ValueError("edge admissibility duplicate row")
    for row in actual["rows"]:
        _assert_fail_closed(row, ROW_FAIL_CLOSED, context=f"edge row {row['opening_id']}")
        if (
            row.get("candidate_hash") != _candidate_hash(_without_hash(row))
            or row.get("selected_pair") is not None
            or row.get("human_readable_boundary", {}).get("named_sides_are_not_a_confirmed_pair") is not True
            or row.get("human_readable_boundary", {}).get("visual_subtype_is_not_edge_confirmation") is not True
            or row.get("human_readable_boundary", {}).get("cuttable_is_not_wall_break_authority") is not True
            or row.get("human_readable_boundary", {}).get("candidate_reachability_is_not_reachability_confirmation") is not True
        ):
            raise ValueError(f"edge row {row['opening_id']} authority/hash drift")
    if (
        rows["OP002"]["pair_status"] != "conflicting_candidates"
        or rows["OP002"]["pair_candidate_sets"]
        != [["bedroom_01", "bedroom_corridor"], ["bedroom_01", "common_core_circulation"]]
        or rows["OP004"]["pair_candidate_sets"] != [["bedroom_02", "north_toilet"]]
        or rows["OP004"]["internal_to_candidate_unreachable_component"] is not True
        or rows["OP008"]["pair_status"] != "ambiguous_candidates"
        or rows["OP008"]["pair_ambiguity_evidence"][0]["other_side_space_options"] != ["bath", "wc"]
        or rows["OP011"]["crosses_candidate_reachable_boundary"] is not True
        or rows["OP011"]["graph_edge_admitted"] is not False
        or rows[PORTAL_ID]["pair_status"] != "hard_denied"
        or rows["OP012"]["pair_status"] != "quarantined_history"
        or rows["OP012"]["registered_segment_m"] is not None
    ):
        raise ValueError("edge admissibility special-case drift")
    impact = actual["score_and_gate_impact"]
    if (
        impact.get("source_score_before") != 65
        or impact.get("source_score_after") != 65
        or impact.get("hard_failures_before") != list(HARD_FAILURES)
        or impact.get("hard_failures_after") != list(HARD_FAILURES)
        or impact.get("blocker_counts_before") != BLOCKER_COUNTS
        or impact.get("blocker_counts_after") != BLOCKER_COUNTS
        or any(delta != 0 for delta in impact.get("blocker_count_delta", {}).values())
    ):
        raise ValueError("edge admissibility score/gate drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT / "register.json")
    args = parser.parse_args(argv)
    result = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output.parent / "REPORT.md").write_text(
        "# Source-bound edge admissibility register v1\n\n"
        "Thirteen rows compare source, geometry, partition, correction, and research-graph pair candidates without "
        "selecting a pair or admitting an edge. OP002 has a real semantic-space conflict: the source adjacency "
        "candidate uses bedroom_01/common_core_circulation while the source opening record, correction registry, "
        "partition, and Tier-D research edge use bedroom_01/bedroom_corridor. OP008 remains ambiguous between bath "
        "and WC on the non-public side. OP004 is internal to the candidate-unreachable suite; OP011 crosses the "
        "candidate reachable boundary but remains blocked by a source/VLM type conflict. OP005 is missing, the "
        "demoted portal is hard-denied, and OP012 is quarantined history. The next targeted evidence task is OP002 "
        "room-side adjudication. S06/S07/S08, 65/100, source, graph, Blender, and IFC authority are unchanged.\n",
        encoding="utf-8",
    )
    (args.output.parent / "REVIEW_CARD_ZH.md").write_text(
        "# Opening 两侧空间核对表（候选，不是房间确认）\n\n"
        "这张表只回答“有哪些候选、哪里冲突、下一步先看谁”，不回答“房间已经连通”。OP002 是当前"
        "第一优先：旧 source adjacency 写 bedroom_01/common_core_circulation，而 source opening record、"
        "correction、partition 和 Tier-D 候选写 bedroom_01/bedroom_corridor，必须重新核对两侧。OP008 的"
        "非公共侧仍在 bath/WC 之间歧义；OP004 只连接两个尚未接入的空间；OP011 可能连接 kitchen 与 "
        "dry_balcony，但类型审查冲突，不能入图。OP005 当前排除，portal 硬拒绝，OP012 保持历史隔离。"
        "所有两侧名称都是候选，不等于真实门洞、可通行或已加入邻接图；S06/S07/S08、65 分、源文件和"
        "正式 Blender/IFC 权限均不改变。\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BATCH_FAIL_CLOSED",
    "BLOCKER_COUNTS",
    "HARD_FAILURES",
    "ROW_FAIL_CLOSED",
    "ROW_IDS",
    "_candidate_hash",
    "build",
    "validate",
]
