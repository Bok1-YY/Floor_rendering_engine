"""Bind same-metric-window Gemini reviews without promoting source semantics."""
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
from tools.goal_loop_v2.build_opening_gap_registered_composites import validate as validate_registered
from tools.goal_loop_v2.build_opening_xy_clean_evidence import EXPECTED_INCLUDED
from tools.goal_loop_v2.fal_opening_gap_composite_review import parse as parse_v2
from tools.goal_loop_v2.fal_opening_gap_registered_review import parse as parse_registered

REGISTERED = ROOT / "reports/opening_gap_registered_composites_20260903/registered-composites.json"
BASE = Path(r"C:/Users/1_1/Desktop/goal_loop_v2_1308_registered_gap_gemini_20260903")
HISTORICAL_OP001 = Path(r"C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_gap_composite_gemini_20260903/OP001/result.json")
OUT = ROOT / "reports/registered_gap_review_bundle_20260903"
FAIL_CLOSED = ("xy_experiment_confirmation", "cut_confirmation", "pair_confirmation", "adjacency_confirmation", "semantic_promotion", "build_authorized")


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _expected_bindings(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"role": role, "filename": Path(row[role]["path"]).name, "bytes": row[role]["bytes"], "sha256": row[role]["sha256"]}
        for role in ("composite", "registered_source", "model_closeup")
    ]


def _validate_result(path: Path, value: Mapping[str, Any], registered: Mapping[str, Any], row: Mapping[str, Any], registered_file_sha256: str) -> dict[str, Any]:
    result = deepcopy(dict(value))
    opening_id = row["opening_id"]
    if result.get("schema") != "fal-opening-gap-registered-review-v1" or result.get("opening_id") != opening_id or result.get("model") != "google/gemini-2.5-flash":
        raise ValueError("registered review identity/model drift")
    if result.get("http_status") != 200 or result.get("usable_advisory") is not True or result.get("validation_error") is not None or result.get("transport_error") is not None:
        raise ValueError("registered review transport/usable drift")
    if result.get("evidence_file_sha256") != registered_file_sha256 or result.get("evidence_candidate_hash") != registered["candidate_hash"]:
        raise ValueError("registered review evidence binding drift")
    if result.get("variant_hash") != row["variant_hash"] or result.get("metric_window") != row["metric_window"] or result.get("image_bindings") != _expected_bindings(row):
        raise ValueError("registered review variant/window/image drift")
    parsed = parse_registered(result["raw_response"]["choices"][0]["message"]["content"], opening_id)
    if parsed != result.get("parsed") or _canonical_hash(result["raw_response"]) != result.get("raw_response_sha256"):
        raise ValueError("registered review raw/parsed/hash drift")
    if any(result.get(key) is not False for key in FAIL_CLOSED) or result.get("score_effect") != "none":
        raise ValueError("registered review was promoted")
    cost = (result.get("usage") or {}).get("cost")
    if not isinstance(cost, (int, float)) or cost < 0:
        raise ValueError("registered review cost missing")
    accepted = (
        parsed["source_segment_visible"] == "yes"
        and parsed["model_gap_centered_on_source_segment"] == "yes"
        and parsed["model_gap_width_matches_source_xy"] == "yes"
        and parsed["junction_or_neighbor_obstruction"] == "no"
        and parsed["xy_variant_visually_valid"] == "yes"
        and parsed["recommendation"] == "accept_xy_variant"
    )
    return {"opening_id": opening_id, "result_file_sha256": _file_hash(path), "parsed": parsed, "accepted_for_isolated_xy_variant": accepted, "cost_usd": float(cost), "canonical_result": result}


def _validate_historical(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("schema") != "fal-opening-gap-composite-review-v2" or result.get("opening_id") != "OP001" or result.get("model") != "google/gemini-2.5-flash":
        raise ValueError("historical OP001 review identity drift")
    if result.get("http_status") != 200 or result.get("usable_advisory") is not True:
        raise ValueError("historical OP001 review unusable")
    parsed = parse_v2(result["raw_response"]["choices"][0]["message"]["content"], "OP001")
    if parsed != result.get("parsed") or _canonical_hash(result["raw_response"]) != result.get("raw_response_sha256"):
        raise ValueError("historical OP001 raw/parsed/hash drift")
    if any(result.get(key) is not False for key in FAIL_CLOSED) or result.get("score_effect") != "none":
        raise ValueError("historical OP001 review was promoted")
    return {"result_file_sha256": _file_hash(path), "evidence_candidate_hash": result["evidence_candidate_hash"], "parsed": parsed, "canonical_result": result}


def build(*, registered_path: Path = REGISTERED, base: Path = BASE, historical_path: Path = HISTORICAL_OP001, _skip_validate: bool = False) -> dict[str, Any]:
    registered_path, base, historical_path = map(Path, (registered_path, base, historical_path))
    registered = json.loads(registered_path.read_text(encoding="utf-8"))
    validate_registered(registered, rebuild=False)
    if tuple(registered["opening_ids"]) != EXPECTED_INCLUDED:
        raise ValueError("registered review coverage drift")
    registered_rows = {row["opening_id"]: row for row in registered["rows"]}
    registered_file_sha256 = _file_hash(registered_path)
    rows = []
    total_cost = 0.0
    for opening_id in EXPECTED_INCLUDED:
        path = base / opening_id / "result.json"
        row = _validate_result(path, json.loads(path.read_text(encoding="utf-8")), registered, registered_rows[opening_id], registered_file_sha256)
        rows.append(row)
        total_cost += row["cost_usd"]
    historical = _validate_historical(historical_path)
    current_op001 = next(row for row in rows if row["opening_id"] == "OP001")
    scale_effect = (
        historical["parsed"]["recommendation"] == "reject_xy_variant"
        and historical["parsed"]["model_gap_centered_on_source_segment"] == "no"
        and historical["parsed"]["model_gap_width_matches_source_xy"] == "no"
        and current_op001["accepted_for_isolated_xy_variant"]
        and historical["evidence_candidate_hash"] != registered["candidate_hash"]
    )
    accepted = [row["opening_id"] for row in rows if row["accepted_for_isolated_xy_variant"]]
    result = {
            "schema": "registered-gap-review-bundle-v2",
            "registered_composites_file_sha256": _file_hash(registered_path),
            "registered_composites_candidate_hash": registered["candidate_hash"],
            "opening_ids": list(EXPECTED_INCLUDED),
            "rows": rows,
            "accepted_for_isolated_xy_variant": accepted,
            "historical_op001_v2": historical,
            "op001_scale_registration_effect_demonstrated": scale_effect,
            "selected_review_cost_usd": round(total_cost, 10),
            "xy_experiment_confirmation": False,
            "cut_confirmation": False,
            "pair_confirmation": False,
            "adjacency_confirmation": False,
            "semantic_promotion": False,
            "score_effect": "none",
            "build_authorized": False,
            "ready": False,
            "candidate_hash": "0" * 64,
        }
    result["candidate_hash"] = _canonical_hash({key: value for key, value in result.items() if key != "candidate_hash"})
    return result if _skip_validate else validate(result, registered_path=registered_path, base=base, historical_path=historical_path)


def validate(candidate: Mapping[str, Any], *, registered_path: Path = REGISTERED, base: Path = BASE, historical_path: Path = HISTORICAL_OP001) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(registered_path=registered_path, base=base, historical_path=historical_path, _skip_validate=True)
    if actual != expected:
        raise ValueError("registered review bundle evidence/derivation drift")
    if actual.get("accepted_for_isolated_xy_variant") != list(EXPECTED_INCLUDED) or actual.get("op001_scale_registration_effect_demonstrated") is not True:
        raise ValueError("registered review acceptance/scale-effect drift")
    for key in ("xy_experiment_confirmation", "cut_confirmation", "pair_confirmation", "adjacency_confirmation", "semantic_promotion", "build_authorized", "ready"):
        if actual.get(key) is not False:
            raise ValueError("registered review bundle was promoted")
    if actual.get("score_effect") != "none":
        raise ValueError("registered review bundle score drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _canonical_hash(payload):
        raise ValueError("registered review bundle candidate hash drift")
    return actual


def _review_card(result: Mapping[str, Any]) -> str:
    return f"""# 同比例户型缺口审查

## 结论

9 个独立 Blender 缺口在同一个中心、方向、米制比例和 1200×1200 分辨率下与源图重新比较后，Gemini 都认为缺口位置居中、宽度一致，且没有被相邻墙或节点挡住。入选范围：`{', '.join(result['accepted_for_isolated_xy_variant'])}`。

这只证明“单独做这条二维缺口时，模型位置和源图线段对得上”。它不证明这些缺口一定是门或窗，不证明可以通行，也不确认房间对、门头、窗台、开启方向、邻接、施工或 IFC。

## 为什么 OP001 的结论变了

上一版把源图 crop 和 Blender closeup 分别缩放后再比较，OP001 被判为位置和宽度不一致；新版本把两边锁定到完全相同的米制窗口后，OP001 通过。系统把这个变化记录为尺度注册造成的 false negative 修复，而不是把它解释成新的入口授权。

## 下一步边界

这 9 支仍然只能独立保存。尚未允许把它们合并成一个正式户型，也不允许修改 v21、65 分、可达图或构建授权。下一步需要独立 verifier 对 9 份 result 与图像 hash 做最终复核，再决定是否建立“组合 XY 研究模型”；门窗类型和 Z 高度继续留在后续层。
"""


def main() -> int:
    result = build()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "bundle.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "REPORT.md").write_text(
        "# Registered gap review bundle v2\n\nAll nine same-metric-window Gemini reviews accept the isolated XY variants. "
        "OP001's previous reject becomes an accept only after metric registration; no semantic/source/score/build promotion is made.\n",
        encoding="utf-8",
    )
    (OUT / "REVIEW_CARD_ZH.md").write_text(_review_card(result), encoding="utf-8")
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate"]
