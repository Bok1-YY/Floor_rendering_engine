"""Derive a fail-closed source-connectivity ambiguity candidate from validated evidence."""
from __future__ import annotations

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
from tools.goal_loop_v2.build_op011_overlay_contamination_bundle import validate as validate_contamination
from tools.goal_loop_v2.correction_candidate_registry import validate_registry
from tools.goal_loop_v2.op011_host_scope_candidate import validate_op011_host_scope_candidate
from tools.goal_loop_v2.op012_neighbor_forensics import validate_op012_neighbor_forensics
from tools.goal_loop_v2.op012_recovery_review_bundle import validate_op012_recovery_review_bundle
from tools.goal_loop_v2.unit_scope_reachability_v3 import validate_unit_scope_reachability_v3

SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
V3 = ROOT / "reports/unit_scope_reachability_v3_20260902/unit-scope-reachability-v3.json"
REGISTRY = ROOT / "reports/correction_candidate_registry_20260902/correction-candidate-registry.json"
OP011_HOST = ROOT / "reports/op011_host_scope_candidate_20260902/op011-host-scope-candidate.json"
OP011_CONTAMINATION = ROOT / "reports/op011_overlay_contamination_20260902/bundle.json"
OP012_FORENSICS = ROOT / "reports/op012_neighbor_forensics_20260902/op012-neighbor-forensics.json"
OP012_REVIEW = ROOT / "reports/op012_recovery_review_20260902/op012-recovery-review-conflict.json"
OP012_RESULT = Path(r"C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_op012_recovery_20260902/result.json")
CONTAMINATION_INPUTS = (
    Path(r"C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_op011_structured_gemini_20260902/result-v2.json"),
    Path(r"C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_op011_structured_openai_20260902/result-v2.json"),
    Path(r"C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_op011_clean_gemini_20260902/result.json"),
    Path(r"C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_op011_clean_openai_20260902/result.json"),
)
OUT = ROOT / "reports/source_connectivity_defect_candidate_20260902"


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _binding(path: Path, candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "file_sha256": _file_hash(path),
        "candidate_hash": candidate.get("candidate_hash"),
    }


def _components(nodes: list[str], internal_edges: list[dict[str, Any]]) -> list[list[str]]:
    parent = {node: node for node in nodes}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for edge in internal_edges:
        union(*edge["relation"])
    groups: dict[str, list[str]] = {}
    for node in nodes:
        groups.setdefault(find(node), []).append(node)
    return sorted((sorted(group) for group in groups.values()), key=lambda group: group[0])


def build(*, _skip_validate: bool = False) -> dict[str, Any]:
    document = validate_v21_document(_load(SOURCE))
    v3 = validate_unit_scope_reachability_v3(document, _load(V3))
    registry = validate_registry(document, _load(REGISTRY))
    op011_host = validate_op011_host_scope_candidate(document, _load(OP011_HOST))
    op011_contamination = validate_contamination(_load(OP011_CONTAMINATION), CONTAMINATION_INPUTS)
    op012_forensics = validate_op012_neighbor_forensics(document, _load(OP012_FORENSICS))
    op012_review = validate_op012_recovery_review_bundle(document, _load(OP012_REVIEW), OP012_RESULT)

    tier = next(item for item in v3["tiers"] if item["tier"] == "D")
    scope = list(v3["scope_space_ids"])
    reachable = list(tier["reachable_space_ids"])
    unreachable = list(tier["unreachable_scope_space_ids"])
    if len(scope) != len(set(scope)) or len(reachable) != len(set(reachable)) or len(unreachable) != len(set(unreachable)):
        raise ValueError("reachability duplicate space id")
    if set(reachable) & set(unreachable) or set(reachable) | set(unreachable) != set(scope):
        raise ValueError("reachability partition drift")

    graph_openings = {edge.get("opening_id") for edge in tier["edges"] if edge.get("opening_id")}
    confirmed_openings = {
        edge.get("opening_id")
        for edge in tier["edges"]
        if edge.get("opening_id") and edge.get("confirmation") is True
    }

    op004 = next(item for item in registry["candidates"] if item["opening_id"] == "OP004")
    op004_relation = [
        op004["directed_side_assignment"]["side_a"],
        op004["directed_side_assignment"]["side_b"],
    ]
    op004_edge = {
        "opening_id": "OP004",
        "authority": "validated_correction_registry",
        "relation": op004_relation,
        "internal_to_unreachable": all(space in unreachable for space in op004_relation),
        "crosses_reachable_component": len(set(op004_relation) & set(unreachable)) == 1
        and len(set(op004_relation) & set(reachable)) == 1,
        "listed_in_candidate_graph": "OP004" in graph_openings,
        "confirmed_admitted": "OP004" in confirmed_openings,
        "application_authorized": op004["application_authorized"],
        "remaining_blockers": deepcopy(op004["remaining_blockers"]),
        "confirmation": False,
    }

    op011_relation = [
        op011_host["directed_side_assignment"]["side_a"],
        op011_host["directed_side_assignment"]["side_b"],
    ]
    op011_edge = {
        "opening_id": "OP011",
        "authority": "validated_host_scope_with_clean_review_conflict",
        "relation": op011_relation,
        "internal_to_unreachable": all(space in unreachable for space in op011_relation),
        "crosses_reachable_component": len(set(op011_relation) & set(unreachable)) == 1
        and len(set(op011_relation) & set(reachable)) == 1,
        "listed_in_candidate_graph": "OP011" in graph_openings,
        "confirmed_admitted": "OP011" in confirmed_openings,
        "clean_provider_disagreement": op011_contamination["clean_provider_disagreement"],
        "review_decision": op011_contamination["decision"],
        "traversability_confirmation": op011_host["traversability_confirmation"],
        "remaining_blockers": deepcopy(op011_host["remaining_blockers"]),
        "confirmation": False,
    }

    op012_edge = {
        "opening_id": "OP012",
        "authority": "validated_rejected_history_quarantine",
        "listed_in_candidate_graph": "OP012" in graph_openings,
        "confirmed_admitted": "OP012" in confirmed_openings,
        "counted_for_components": False,
        "recovery_confirmation": op012_review["recovery_confirmation"],
        "review_decision": op012_review["decision"],
        "distinct_swing_established": not op012_forensics["pixel_interpretation_candidate"]["op012_distinct_swing_not_established"],
        "confirmation": False,
    }
    edges = [op004_edge, op011_edge, op012_edge]

    internal_edges = [
        edge
        for edge in edges
        if edge.get("relation")
        and edge["internal_to_unreachable"]
        and edge["authority"] != "validated_rejected_history_quarantine"
    ]
    components = []
    for index, spaces in enumerate(_components(sorted(unreachable), internal_edges), start=1):
        internal = [edge["opening_id"] for edge in internal_edges if set(edge["relation"]).issubset(spaces)]
        boundary = [
            edge["opening_id"]
            for edge in edges
            if edge.get("relation")
            and len(set(edge["relation"]) & set(spaces)) == 1
            and len(set(edge["relation"]) & set(reachable)) == 1
        ]
        admitted_boundary = [
            edge["opening_id"]
            for edge in edges
            if edge.get("opening_id") in boundary and edge["confirmed_admitted"]
        ]
        if internal and not boundary:
            classification = "candidate_internal_edge_only_no_root_crossing"
        elif boundary and not admitted_boundary:
            classification = "candidate_boundary_edge_present_but_no_connectivity_edge_admitted"
        else:
            classification = "no_usable_connectivity_candidate"
        components.append(
            {
                "component_id": f"UNREACHABLE-{index:02d}",
                "space_ids": spaces,
                "internal_candidate_opening_ids": sorted(internal),
                "boundary_candidate_opening_ids": sorted(boundary),
                "confirmed_boundary_opening_ids": sorted(admitted_boundary),
                "classification": classification,
                "confirmation": False,
            }
        )

    missing_evidence = []
    if v3["root_confirmation"] is False:
        missing_evidence.append("CONFIRMED_UNIT_ROOT")
    if op004["application_authorized"] is False:
        missing_evidence.append("OP004_SOURCE_APPLICATION_AND_TRAVERSABILITY")
    if op011_contamination["clean_provider_disagreement"]:
        missing_evidence.append("OP011_CLEAN_SOURCE_VISUAL_CONSENSUS")
    if op012_review["recovery_confirmation"] is False:
        missing_evidence.append("OP012_FRESH_SOURCE_RECOVERY_AUTHORITY")
    if unreachable:
        missing_evidence.append("CONFIRMED_CONNECTIVITY_FOR_ALL_SCOPE_SPACES")

    result = {
        "schema": "source-connectivity-defect-candidate-v2",
        "source_structure_hash": document["structure_hash"],
        "bindings": {
            "source": _binding(SOURCE, document),
            "unit_scope_v3": _binding(V3, v3),
            "correction_registry": _binding(REGISTRY, registry),
            "op011_host_scope": _binding(OP011_HOST, op011_host),
            "op011_contamination": _binding(OP011_CONTAMINATION, op011_contamination),
            "op012_forensics": _binding(OP012_FORENSICS, op012_forensics),
            "op012_recovery_review": _binding(OP012_REVIEW, op012_review),
        },
        "scope_space_ids": sorted(scope),
        "reachable_space_ids": sorted(reachable),
        "unreachable_space_ids": sorted(unreachable),
        "candidate_edges": edges,
        "unreachable_components": components,
        "modes": {
            "source_faithful_wall_geometry": "research_candidate_only_not_authorized",
            "functional_bim_connectivity": "blocked_by_source_ambiguity",
            "design_repair_branch": "requires_separate_source_or_human_policy_not_applied",
        },
        "faithful_complete_functional_reconstruction_possible": False,
        "missing_evidence": missing_evidence,
        "source_mutation_authorized": False,
        "root_confirmation": False,
        "reachability_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _candidate_hash({key: value for key, value in result.items() if key != "candidate_hash"})
    return result if _skip_validate else validate(result)


def validate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    expected = build(_skip_validate=True)
    actual = deepcopy(dict(candidate))
    if actual != expected:
        raise ValueError("source connectivity evidence or derivation drift")
    expected_components = {
        ("bedroom_02", "north_toilet"): "candidate_internal_edge_only_no_root_crossing",
        ("dry_balcony",): "candidate_boundary_edge_present_but_no_connectivity_edge_admitted",
    }
    observed = {tuple(item["space_ids"]): item["classification"] for item in actual["unreachable_components"]}
    if observed != expected_components:
        raise ValueError("source connectivity component classification drift")
    op012 = next(edge for edge in actual["candidate_edges"] if edge["opening_id"] == "OP012")
    if op012["counted_for_components"] or op012["confirmed_admitted"] or op012["recovery_confirmation"]:
        raise ValueError("quarantined OP012 entered connectivity")
    for key in (
        "source_mutation_authorized",
        "root_confirmation",
        "reachability_confirmation",
        "pair_confirmation",
        "adjacency_confirmation",
        "semantic_promotion",
        "build_authorized",
        "ready",
    ):
        if actual[key] is not False:
            raise ValueError("source connectivity candidate was promoted")
    if actual["score_effect"] != "none":
        raise ValueError("source connectivity score effect drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual["candidate_hash"] != _candidate_hash(payload):
        raise ValueError("source connectivity candidate hash drift")
    return actual


def _review_card(result: Mapping[str, Any]) -> str:
    suite = next(item for item in result["unreachable_components"] if "bedroom_02" in item["space_ids"])
    dry = next(item for item in result["unreachable_components"] if item["space_ids"] == ["dry_balcony"])
    return f"""# 这张户型图现在卡在哪里

## 一句话结论

目前不是 Blender 不会建模，而是原始户型图里有三处空间没有被可靠地接入室内通行网络：二号卧室、北侧卫生间和生活阳台。墙线可以按图做成灰模，但如果现在把门洞和通行关系也当成正确事实写进 BIM，就会把猜测固化成建筑结构。

## 二号卧室和北侧卫生间

图上能找到 OP004，它把二号卧室与北侧卫生间作为一个候选组合；现有几何计算也能说明两者位于同一处门线两侧。但是这条候选只解决了两个房间彼此之间的关系，没有找到一条已经确认、能把它们接回走廊或客厅的门。历史上的 OP012 曾被怀疑是入口，但重新对齐原图后，它落在相邻两扇门之间的连续墙线上；因此它仍被隔离，不能拿来补门。当前组件状态：`{suite['classification']}`。

## 生活阳台

OP011 的坐标稳定落在厨房与生活阳台之间，墙体位置和两侧空间都比较明确。问题在于原图只显示一段玻璃或边界线，看不出它到底是可推拉的门、固定窗，还是普通墙缝。带彩色标注的图片让 Gemini 和 OpenAI 都误判成“有滑轨的推拉门”；改用完全没有覆盖标记的原始局部图后，Gemini 判断为可通行墙洞，而 OpenAI 判断为没有墙洞。两家高置信结论互相冲突，所以不能自动开门。当前组件状态：`{dry['classification']}`。

## 为什么暂时不能直接生成正式 Blender / IFC

如果只追求“看起来像户型图”的研究灰模，可以把可见墙体照图画出来；但这不等于功能正确的 BIM。正式模型需要知道哪段墙要切洞、洞宽是多少、门能否通行、两个房间是否真的相邻。现在缺的正是这些结构事实。直接继续会产生两种风险：要么把二号卧室做成无法进入的房间，要么凭空增加原图没有的门；生活阳台也可能被错误地做成推拉门。

## 两个安全选择

1. **忠实原图模式**：保留所有可见墙线，把三处空间标成“连通关系未确认”，只生成研究灰模，不宣称是可交付 BIM，也不改变评分。
2. **设计修复模式**：明确授权系统提出最小改图方案，例如为二号卧室套间增加一处走廊入口，并决定 OP011 是推拉门、固定窗还是墙洞。这个分支属于设计修改，不是原图识别，必须单独保存，不能覆盖原图事实。

## 当前系统采取的动作

本候选不会修改原始户型数据，不会把 OP012 重新启用，不会把 OP011 自动变成门，也不会启动正式 Blender/IFC 构建。它只把问题、证据和安全选择整理成可复核状态。待原图补充、人工确认或设计修复策略获得授权后，再进入下一层建模。
"""


def main() -> int:
    result = build()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "candidate.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "REPORT.md").write_text(
        "# Source connectivity ambiguity candidate v2\n\n"
        f"Validated Tier D leaves {', '.join(result['unreachable_space_ids'])} unreachable. "
        "OP004 is internal to one unreachable component; OP011 is an unconfirmed boundary candidate; "
        "OP012 remains quarantined. No source, score, adjacency, or build promotion is made.\n",
        encoding="utf-8",
    )
    (OUT / "REVIEW_CARD_ZH.md").write_text(_review_card(result), encoding="utf-8")
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate"]
