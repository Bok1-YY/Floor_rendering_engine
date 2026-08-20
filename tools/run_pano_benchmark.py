#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""已停用的定点球面全景批量付费基准入口。

旧实现会绕过逐 capture 的动态短语确认并执行多轮 provider 调用。入口保留用于给
已有自动化返回明确错误，但在迁移为两阶段 preview/commit 协议前不会执行或计费。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('FLOOR_WHOLE_HOME_MANUAL_SAFE', '0')

# 与 serve.py / tests/conftest.py 相同:以顶层包名注册仓库根目录。
_PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PACKAGE_NAME = 'Floor_engine_server'
if _PACKAGE_NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _PACKAGE_NAME, os.path.join(_PKG_DIR, '__init__.py'),
        submodule_search_locations=[_PKG_DIR])
    _package = importlib.util.module_from_spec(_spec)
    sys.modules[_PACKAGE_NAME] = _package
    _spec.loader.exec_module(_package)

from Floor_engine_server import whole_home_pano_gate as gate  # noqa: E402
from Floor_engine_server import whole_home_pano_edit as edit  # noqa: E402
from Floor_engine_server.api import call_gpt_image_edit  # noqa: E402
from Floor_engine_server.config import GPT_IMAGE_2_MODEL  # noqa: E402
from Floor_engine_server.whole_home_engine import load_project, save_pano_image_file  # noqa: E402


def _resolve_capture(project: dict, pano_id: str) -> dict:
    capture = next((row for row in project.get('pano_captures') or []
                    if str(row.get('pano_id') or '') == pano_id), None)
    if not capture:
        raise SystemExit(f'项目里没有全景 capture: {pano_id}')
    return capture


def run_benchmark(project_id: str, pano_id: str, rounds: int, out_path: str) -> dict:
    raise SystemExit(
        '该旧批量付费基准已安全停用：请在整屋工作台逐 capture 创建免费预览、'
        '逐字确认唯一一次 edit，并仅在 P0 门禁只失败 wrap_seam/cube_edges 时确认 repair。'
    )
    project = load_project(project_id)
    if not project:
        raise SystemExit(f'项目不存在: {project_id}')
    capture = _resolve_capture(project, pano_id)
    manifest = capture.get('manifest') or {}
    channels = manifest.get('channels') or {}
    reference_rgb = channels.get('rgb_erp') or ''
    if not reference_rgb or not os.path.isfile(reference_rgb):
        raise SystemExit('参考 ERP 不存在,请先保存全景 capture')
    model = project.get('model') or {}
    records = []
    for index in range(1, rounds + 1):
        row = {'round': index, 'first_pass': False, 'repair_pass': False,
               'gate_failures': [], 'calls': 0, 'seconds': 0}
        started = time.time()
        prompt = edit.build_erp_edit_prompt(manifest)
        channel_paths = [reference_rgb] + [channels.get(f'{kind}_erp') for kind in
                                           ('depth', 'normal', 'semantic')
                                           if channels.get(f'{kind}_erp')]
        image, error = call_gpt_image_edit('', prompt, [p for p in channel_paths if os.path.isfile(p)],
                                           model_id=GPT_IMAGE_2_MODEL)
        row['calls'] += 1
        if image is None:
            row['error'] = error or 'edit failed'
            records.append(row)
            continue
        edited_path = save_pano_image_file(project_id, pano_id, f'bench_{index}_edited', image)
        result = gate.gate_pano_erp(edited_path, channels, manifest, model)
        row['gate_failures'] = result.get('failures') or []
        row['first_pass'] = bool(result.get('gate_pass'))
        if not result.get('gate_pass') and 'wrap_seam' in (result.get('failures') or []):
            shifted = edit.circular_shift_erp(image, manifest.get('erp_width', 0) // 2)
            mask = edit.build_seam_repair_mask(manifest.get('erp_width', 0),
                                               manifest.get('erp_height', 0))
            shifted_path = save_pano_image_file(project_id, pano_id, f'bench_{index}_shifted', shifted)
            mask_path = save_pano_image_file(project_id, pano_id, f'bench_{index}_mask', mask)
            repaired, repair_error = call_gpt_image_edit(
                '', edit.build_seam_repair_prompt(), [shifted_path], mask_image_path=mask_path,
                model_id=GPT_IMAGE_2_MODEL)
            row['calls'] += 1
            if repaired is not None:
                restored = edit.circular_shift_erp(repaired, -(manifest.get('erp_width', 0) // 2))
                repaired_path = save_pano_image_file(project_id, pano_id, f'bench_{index}_repaired', restored)
                repair_result = gate.gate_pano_erp(repaired_path, channels, manifest, model)
                row['repair_pass'] = bool(repair_result.get('gate_pass'))
                row['gate_failures'] = repair_result.get('failures') or []
            else:
                row['error'] = f'repair failed: {repair_error}'
        row['seconds'] = round(time.time() - started, 1)
        records.append(row)
        print(f"round {index}: first_pass={row['first_pass']} repair_pass={row['repair_pass']} "
              f"failures={row['gate_failures']} calls={row['calls']} s={row['seconds']}", flush=True)
    summary = {
        'project_id': project_id, 'pano_id': pano_id, 'model_id': GPT_IMAGE_2_MODEL,
        'rounds': rounds,
        'first_round_hard_pass_rate': round(sum(r['first_pass'] for r in records) / max(1, rounds), 3),
        'repair_pass_rate': round(sum(r['repair_pass'] for r in records) / max(1, rounds), 3),
        'seam_pole_failure_rate': round(
            sum(1 for r in records if {'wrap_seam', 'poles'} & set(r['gate_failures'])) / max(1, rounds), 3),
        'opening_object_failure_rate': round(
            sum(1 for r in records if {'opening_identity', 'structure_views'} & set(r['gate_failures'])) / max(1, rounds), 3),
        'average_calls': round(sum(r['calls'] for r in records) / max(1, rounds), 2),
        'average_seconds': round(sum(r['seconds'] for r in records) / max(1, rounds), 1),
        'stop_rule': ('stop_generic_api_as_final → 转 P2 共享 3D 资产'
                      if sum(r['first_pass'] or r['repair_pass'] for r in records) / max(1, rounds) < 0.7
                      else 'continue'),
        'records': records,
        'generated_at': time.time(),
    }
    with open(out_path, 'w', encoding='utf-8') as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    print(f'基准已写入 {out_path}')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='定点球面全景 12 热点基准(文档 §12)')
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--pano-id', required=True)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--out', default='data/pano_benchmark.json')
    args = parser.parse_args()
    run_benchmark(args.project_id, args.pano_id, args.rounds, args.out)
