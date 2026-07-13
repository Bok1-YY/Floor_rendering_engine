# ==========================================
# 自定义配方持久化：用户把当前参数组合存为「我的配方」，可改名/编辑/删除。
# recipes.py 保持「纯数据 + 纯逻辑无 IO」，持久化单独放本模块（headless，勿引 UI）。
# 存储：BASE_DIR/custom_recipes.json（运行期产物，与 engine_config.json 同级，不入库）。
# 写安全：模块级锁 + tmp/os.replace 原子写（仿 records._save_usage_raw）。
# ==========================================
import os
import json
import time
import uuid
import threading
from typing import List, Optional

from .config import BASE_DIR, logger

CUSTOM_RECIPES_FILE = os.path.join(BASE_DIR, 'custom_recipes.json')
_lock = threading.Lock()


def _now() -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _load_raw() -> List[dict]:
    try:
        if os.path.exists(CUSTOM_RECIPES_FILE):
            with open(CUSTOM_RECIPES_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
            if isinstance(d, list):
                return [r for r in d if isinstance(r, dict) and r.get('id')]
    except Exception as ex:
        logger.warning(f"[配方] 读取自定义配方失败(按空处理): {ex}")
    return []


def _save_raw(items: List[dict]) -> None:
    tmp = CUSTOM_RECIPES_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CUSTOM_RECIPES_FILE)


def list_custom_recipes() -> List[dict]:
    """全部自定义配方，新建的在前。"""
    with _lock:
        return _load_raw()


def add_custom_recipe(name: str, params: dict) -> dict:
    """新增配方（params=GenParams 全量快照，调用方负责剔除易变字段）。返回新配方。"""
    rec = {
        'id': uuid.uuid4().hex,
        'name': str(name or '').strip()[:40] or '未命名配方',
        'created_at': _now(),
        'updated_at': _now(),
        'params': params if isinstance(params, dict) else {},
    }
    with _lock:
        items = _load_raw()
        items.insert(0, rec)
        _save_raw(items)
    logger.info(f"[配方] 新增自定义配方 id={rec['id']}, name={rec['name']}")
    return rec


def update_custom_recipe(rid: str, name: Optional[str] = None,
                         params: Optional[dict] = None) -> Optional[dict]:
    """改名/覆盖参数（传 None 的字段不动）。返回更新后的配方；找不到返回 None。"""
    with _lock:
        items = _load_raw()
        for r in items:
            if r.get('id') == rid:
                if name is not None:
                    r['name'] = str(name or '').strip()[:40] or r['name']
                if params is not None and isinstance(params, dict):
                    r['params'] = params
                r['updated_at'] = _now()
                _save_raw(items)
                logger.info(f"[配方] 更新自定义配方 id={rid}")
                return r
    logger.warning(f"[配方] 更新失败，未找到 id={rid}")
    return None


def delete_custom_recipe(rid: str) -> bool:
    """删除配方；找不到返回 False。"""
    with _lock:
        items = _load_raw()
        kept = [r for r in items if r.get('id') != rid]
        if len(kept) == len(items):
            logger.warning(f"[配方] 删除失败，未找到 id={rid}")
            return False
        _save_raw(kept)
    logger.info(f"[配方] 删除自定义配方 id={rid}")
    return True


__all__ = ['list_custom_recipes', 'add_custom_recipe',
           'update_custom_recipe', 'delete_custom_recipe', 'CUSTOM_RECIPES_FILE']
