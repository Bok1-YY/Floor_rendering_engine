"""Bind cross-provider clean-crop XY reviews without promoting source semantics."""
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
from tools.goal_loop_v2.build_opening_xy_clean_evidence import EXPECTED_INCLUDED, validate as validate_evidence
from tools.goal_loop_v2.fal_opening_xy_clean_review import FIELDS, parse

EVIDENCE = ROOT / "reports/opening_xy_clean_evidence_20260902/evidence.json"
GEMINI_BASE = Path(r"C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_opening_xy_clean_20260902")
OPENAI_BASE = Path(r"C:/Users/1_1/Desktop/goal_loop_v2_1308_openai_opening_xy_clean_20260902")
OUT = ROOT / "reports/opening_xy_review_bundle_20260902"
CORE_FIELDS = ("segment_on_visible_opening", "continuous_wall_across_segment", "xy_gap_plausible")
EXPECTED_CORE = {"segment_on_visible_opening": "yes", "continuous_wall_across_segment": "no", "xy_gap_plausible": "yes"}
FAIL_CLOSED = ("cut_confirmation", "pair_confirmation", "adjacency_confirmation", "semantic_promotion", "build_authorized")


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _expected_image_bindings(evidence_row: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "role": role,
            "filename": Path(evidence_row["artifacts"][role]["path"]).name,
            "bytes": evidence_row["artifacts"][role]["bytes"],
            "sha256": evidence_row["artifacts"][role]["sha256"],
        }
        for role in ("locator", "raw_crop")
    ]


def _validate_selected_result(
    path: Path,
    result: Mapping[str, Any],
    evidence: Mapping[str, Any],
    evidence_row: Mapping[str, Any],
    expected_model: str,
    evidence_file_sha256: str,
) -> dict[str, Any]:
    value = deepcopy(dict(result))
    opening_id = evidence_row["opening_id"]
    if value.get("schema") != "fal-opening-xy-clean-review-v2" or value.get("opening_id") != opening_id:
        raise ValueError("XY selected result schema/id drift")
    if value.get("model") != expected_model or value.get("http_status") != 200 or value.get("usable_advisory") is not True:
        raise ValueError("XY selected result model/transport/usable drift")
    if value.get("validation_error") is not None or value.get("transport_error") is not None:
        raise ValueError("XY selected result contains an error")
    if value.get("evidence_file_sha256") != evidence_file_sha256 or value.get("evidence_candidate_hash") != evidence["candidate_hash"]:
        raise ValueError("XY selected result evidence binding drift")
    if value.get("image_bindings") != _expected_image_bindings(evidence_row) or value.get("local_segment_px") != evidence_row["local_segment_px"]:
        raise ValueError("XY selected result image/coordinate binding drift")
    parsed = value.get("parsed")
    if not isinstance(parsed, dict) or set(parsed) != set(FIELDS):
        raise ValueError("XY selected parsed schema drift")
    reparsed = parse(value["raw_response"]["choices"][0]["message"]["content"], opening_id)
    if reparsed != parsed:
        raise ValueError("XY selected raw/parsed mismatch")
    if _canonical_hash(value["raw_response"]) != value.get("raw_response_sha256"):
        raise ValueError("XY selected raw response hash drift")
    if any(value.get(key) is not False for key in FAIL_CLOSED) or value.get("score_effect") != "none":
        raise ValueError("XY selected result was promoted")
    cost = (value.get("usage") or {}).get("cost")
    if not isinstance(cost, (int, float)) or cost < 0:
        raise ValueError("XY selected result cost missing")
    return {
        "model": value["model"],
        "result_file_sha256": _file_hash(path),
        "raw_response_sha256": value["raw_response_sha256"],
        "request_contract_sha256": value["request_contract_sha256"],
        "parsed": parsed,
        "cost_usd": float(cost),
        "canonical_result": value,
    }


def _validate_failed_op004(path: Path, value: Mapping[str, Any], evidence: Mapping[str, Any], evidence_row: Mapping[str, Any], evidence_file_sha256: str) -> dict[str, Any]:
    result = deepcopy(dict(value))
    if result.get("schema") != "fal-opening-xy-clean-review-v2" or result.get("opening_id") != "OP004" or result.get("model") != "openai/gpt-4o-mini":
        raise ValueError("OP004 failed attempt identity drift")
    if result.get("http_status") is not None or result.get("usable_advisory") is not False or result.get("parsed") is not None:
        raise ValueError("OP004 failed attempt was made usable")
    if "SSLError" not in str(result.get("transport_error")) or result.get("raw_response") is not None or result.get("usage") is not None:
        raise ValueError("OP004 failed attempt transport/raw drift")
    if result.get("evidence_file_sha256") != evidence_file_sha256 or result.get("evidence_candidate_hash") != evidence["candidate_hash"]:
        raise ValueError("OP004 failed attempt evidence binding drift")
    if result.get("image_bindings") != _expected_image_bindings(evidence_row) or result.get("local_segment_px") != evidence_row["local_segment_px"]:
        raise ValueError("OP004 failed attempt image binding drift")
    if any(result.get(key) is not False for key in FAIL_CLOSED) or result.get("score_effect") != "none":
        raise ValueError("OP004 failed attempt was promoted")
    return {"opening_id": "OP004", "attempt_kind": "transport_failure_preserved", "result_file_sha256": _file_hash(path), "canonical_result": result}


def build(
    *,
    evidence_path: Path = EVIDENCE,
    gemini_base: Path = GEMINI_BASE,
    openai_base: Path = OPENAI_BASE,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    evidence_path, gemini_base, openai_base = map(Path, (evidence_path, gemini_base, openai_base))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    validate_evidence(evidence, rebuild=False)
    if tuple(evidence["opening_ids"]) != EXPECTED_INCLUDED:
        raise ValueError("XY review evidence coverage drift")
    evidence_rows = {row["opening_id"]: row for row in evidence["openings"]}
    evidence_file_sha256 = _file_hash(evidence_path)
    rows = []
    total_cost = 0.0
    for opening_id in EXPECTED_INCLUDED:
        gemini_path = gemini_base / opening_id / "result.json"
        openai_path = openai_base / opening_id / ("retry-result.json" if opening_id == "OP004" else "result.json")
        gemini = _validate_selected_result(gemini_path, json.loads(gemini_path.read_text(encoding="utf-8")), evidence, evidence_rows[opening_id], "google/gemini-2.5-flash", evidence_file_sha256)
        openai = _validate_selected_result(openai_path, json.loads(openai_path.read_text(encoding="utf-8")), evidence, evidence_rows[opening_id], "openai/gpt-4o-mini", evidence_file_sha256)
        total_cost += gemini["cost_usd"] + openai["cost_usd"]
        core = {field: gemini["parsed"][field] == openai["parsed"][field] == EXPECTED_CORE[field] for field in CORE_FIELDS}
        disagreements = [field for field in FIELDS[1:7] if gemini["parsed"][field] != openai["parsed"][field]]
        rows.append(
            {
                "opening_id": opening_id,
                "evidence_host_atom_id": evidence_rows[opening_id]["host_atom_id"],
                "attempts": {"gemini": gemini, "openai": openai},
                "core_consensus": core,
                "cue_disagreements": disagreements,
                "included_for_xy_experiment": all(core.values()),
            }
        )

    failed_path = openai_base / "OP004" / "result.json"
    failed = _validate_failed_op004(failed_path, json.loads(failed_path.read_text(encoding="utf-8")), evidence, evidence_rows["OP004"], evidence_file_sha256)
    included = [row["opening_id"] for row in rows if row["included_for_xy_experiment"]]
    result = {
        "schema": "opening-xy-cross-provider-review-bundle-v2",
        "evidence_file_sha256": _file_hash(evidence_path),
        "evidence_candidate_hash": evidence["candidate_hash"],
        "opening_ids": list(EXPECTED_INCLUDED),
        "rows": rows,
        "included_for_xy_experiment": included,
        "cue_disagreements_by_opening": {row["opening_id"]: row["cue_disagreements"] for row in rows if row["cue_disagreements"]},
        "failed_attempts": [failed],
        "selected_review_cost_usd": round(total_cost, 10),
        "xy_experiment_confirmation": False,
        "subtype_confirmation": False,
        "traversability_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _canonical_hash({key: value for key, value in result.items() if key != "candidate_hash"})
    return result if _skip_validate else validate(result, evidence_path=evidence_path, gemini_base=gemini_base, openai_base=openai_base)


def validate(candidate: Mapping[str, Any], *, evidence_path: Path = EVIDENCE, gemini_base: Path = GEMINI_BASE, openai_base: Path = OPENAI_BASE) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(evidence_path=evidence_path, gemini_base=gemini_base, openai_base=openai_base, _skip_validate=True)
    if actual != expected:
        raise ValueError("XY review bundle evidence/derivation drift")
    if actual["included_for_xy_experiment"] != list(EXPECTED_INCLUDED):
        raise ValueError("XY review bundle inclusion coverage drift")
    for key in ("xy_experiment_confirmation", "subtype_confirmation", "traversability_confirmation", "pair_confirmation", "adjacency_confirmation", "semantic_promotion", "build_authorized", "ready"):
        if actual.get(key) is not False:
            raise ValueError("XY review bundle was promoted")
    if actual.get("score_effect") != "none":
        raise ValueError("XY review bundle score drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _canonical_hash(payload):
        raise ValueError("XY review bundle candidate hash drift")
    return actual


def _review_card(result: Mapping[str, Any]) -> str:
    disagreements = result["cue_disagreements_by_opening"]
    return f"""# 九个候选墙洞的双模型复核

## 结论

Gemini 与 OpenAI 都认为 9 个候选线段位于可见开口位置，线段上不是连续实体墙，并且适合进入“只测试 XY 位置”的 Blender 墙洞实验。这里的通过只表示可以做一个全高缺口来观察平面位置，不表示它已经是门、窗、可通行通道或施工洞口。

入选编号：`{', '.join(result['included_for_xy_experiment'])}`。

## 两家模型仍然不同意的地方

`{json.dumps(disagreements, ensure_ascii=False)}`

OP004 的分歧是端点是否足够清晰；OP009 的分歧是看到门扇还是玻璃界面；OP010 的分歧是是否看到门扇。这些差异不会阻止纯 XY 缺口实验，但会继续阻止门窗类型、门头、窗台、开启方向和通行关系。

## 保留的失败

OpenAI 对 OP004 的第一次请求发生 SSL 传输失败，没有收到 HTTP 状态、模型输出或费用；失败结果被原样保留。随后重试成功，且重试结果单独绑定，没有覆盖失败记录。

## 明确不做什么

本复核不会修改 v21 源数据，不会确认房间对、邻接或可达性，不会改变 65 分，不会把 OP005、OP011、拒绝 portal 或 OP012 放回切洞集合，也不会授权正式 Blender/IFC。下一步只能建立独立的研究墙洞分支，并继续与 Layer 1 墙体基线分开保存。
"""


def main() -> int:
    result = build()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "bundle.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "REPORT.md").write_text(
        "# Opening XY cross-provider review bundle v2\n\n"
        f"Both providers reached core XY-gap consensus for {len(result['included_for_xy_experiment'])}/9 candidates. "
        "Cue disagreements remain explicit and all semantic/source/score/build fields remain fail-closed.\n",
        encoding="utf-8",
    )
    (OUT / "REVIEW_CARD_ZH.md").write_text(_review_card(result), encoding="utf-8")
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate"]
