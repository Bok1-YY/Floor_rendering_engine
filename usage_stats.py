# -*- coding: utf-8 -*-
"""用量统计 —— 「模式 × 模型 × 线路」的成图/失败计数与成本估算汇总。

从 records.py 迁出;函数体逐字未动。落盘 usage_stats.json,原子写,
全程吞异常——绝不能拖垮生图主流程(这里的宽 except 是刻意容错)。
"""
import json
import os
import threading
import time
from typing import Optional

from .config import MAIN_OUTPUT_DIR, logger

# ── 用量统计 ──────────────────────────────────────────────────────
# 累计「不同模式 × 模型(B2/Pro) × 线路(google/fal)」各出了多少张图、失败多少次。
# 不估算金额（计价多变不好控）。落盘 usage_stats.json，原子写，全程吞异常——绝不能拖垮生图主流程。
_USAGE_STATS_FILE = os.path.join(MAIN_OUTPUT_DIR, "usage_stats.json")
_usage_lock = threading.Lock()

def _short_mode_label(mode: str) -> str:
    """长 workflow_mode → 短标签（与记录页 split 显示一致）。"""
    return (mode or "").split("(")[0].split(" ")[0].strip() or "未知"

def _short_model_label(model: str) -> str:
    m = model or ""
    if "VR360" in m or "GPT Image" in m: return "VR360"
    if "Aura" in m: return "AuraSR"
    if "SD35" in m or "SD 3.5" in m: return "SD35"
    if "Lite" in m: return "Lite"
    if "Pro" in m: return "Pro"
    if "B2" in m or " 2" in m or m.endswith("2"): return "B2"
    return m.strip() or "未知"

def _load_usage_raw() -> dict:
    def _normalize_preview_model(data: dict) -> dict:
        """旧版把 NB2 Lite 归并成 B2；按 operation=preview 可无歧义迁回 Lite。"""
        for operations in (data.get('counts') or {}).values():
            if not isinstance(operations, dict):
                continue
            preview = operations.get('preview')
            if not isinstance(preview, dict) or 'B2' not in preview:
                continue
            old = preview.get('B2')
            if not isinstance(old, dict):
                continue
            lite = preview.get('Lite')
            if not isinstance(lite, dict):
                lite = {}
                preview['Lite'] = lite
            preview.pop('B2', None)
            for provider, counts in old.items():
                if not isinstance(counts, dict):
                    continue
                row = lite.setdefault(provider, {'ok': 0, 'fail': 0})
                row['ok'] = int(row.get('ok', 0)) + int(counts.get('ok', 0))
                row['fail'] = int(row.get('fail', 0)) + int(counts.get('fail', 0))
        return data

    try:
        if os.path.exists(_USAGE_STATS_FILE):
            with open(_USAGE_STATS_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                if int(d.get('version', 1) or 1) >= 2:
                    return _normalize_preview_model(d)
                old = d.get('counts') or {}
                return {'version': 2, 'counts': {
                    mode: {'generate': models} for mode, models in old.items()
                    if isinstance(models, dict)
                }}
    except Exception as ex:
        logger.warning(f"[用量] 读取失败(将重置): {ex}")
    return {"version": 2, "counts": {}}

def _save_usage_raw(data: dict) -> None:
    tmp = _USAGE_STATS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, _USAGE_STATS_FILE)

def record_usage(mode: str, model: str, provider: str, ok: bool, operation: str = 'generate') -> None:
    """累加一次出图结果。维度: 模式 × 模型 × 线路(google/fal/local)，各计 ok/fail。
    全程吞异常——统计绝不能影响生图。"""
    try:
        mkey = _short_mode_label(mode)
        mdl = _short_model_label(model)
        prov = (provider or "google").strip().lower()
        if prov == 'comfyui':
            prov = 'local'
        if prov not in ("google", "fal", "local"):
            prov = "unknown"
        with _usage_lock:
            data = _load_usage_raw()
            counts = data.setdefault("counts", {})
            op = (operation or 'generate').strip().lower()
            row = counts.setdefault(mkey, {}).setdefault(op, {}).setdefault(mdl, {}).setdefault(prov, {"ok": 0, "fail": 0})
            row["ok" if ok else "fail"] = int(row.get("ok" if ok else "fail", 0)) + 1
            _save_usage_raw(data)
    except Exception as ex:
        logger.debug(f"[用量] record_usage 忽略: {ex}")

def load_usage_summary(prices: Optional[dict] = None) -> dict:
    """结构化汇总，供 UI 渲染：
    {'rows': [{mode, model, provider, ok, fail, cost?}...(已排序)], 'totals': {'ok','fail','total','cost'}}
    prices: 可选单价表 {'B2': 0.1, 'Pro': 0.5, 'Lite': 0.03, 'B2:fal': 0.12,...}（元/张成功图）。
    行单价先查 '模型:线路' 再回落 '模型'；查无单价的行 cost=None（UI 显示 —），估算口径=只按成功计。"""
    data = _load_usage_raw()
    prices = prices or {}
    rows = []
    tot_ok = tot_fail = 0
    tot_cost = 0.0
    has_cost = False
    unpriced_ok = 0
    for mode, operations in sorted((data.get("counts") or {}).items()):
        if not isinstance(operations, dict):
            continue
        for operation, models in sorted(operations.items()):
            if not isinstance(models, dict):
                continue
            for model, provs in sorted(models.items()):
                if not isinstance(provs, dict):
                    continue
                for prov, c in sorted(provs.items()):
                    if not isinstance(c, dict):
                        continue
                    ok = int(c.get("ok", 0)); fail = int(c.get("fail", 0))
                    tot_ok += ok; tot_fail += fail
                    price = prices.get(f"{model}:{prov}", prices.get(model))
                    # 用量页统计 API 成本；本地 ComfyUI 未配置单价时按 0，而不是误报“未计价”。
                    if price is None and prov == 'local':
                        price = 0.0
                    cost = round(ok * price, 2) if price is not None else None
                    if cost is not None:
                        tot_cost += cost
                        has_cost = True
                    else:
                        unpriced_ok += ok
                    rows.append({"mode": mode, "operation": operation, "model": model,
                                 "provider": prov, "ok": ok, "fail": fail, "cost": cost})
    return {"rows": rows,
            "totals": {"ok": tot_ok, "fail": tot_fail, "total": tot_ok + tot_fail,
                       "cost": round(tot_cost, 2) if has_cost else None,
                       "unpriced_ok": unpriced_ok,
                       "cost_complete": unpriced_ok == 0}}
