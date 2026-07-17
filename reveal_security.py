# -*- coding: utf-8 -*-
"""提示词数据安全层 —— XOR 混淆(非加密!防的是记录文件被随手翻看,不防逆向)
与揭示密码校验。

从 records.py 迁出;函数体逐字未动。密码哈希加载优先级:
环境变量 FLOOR_REVEAL_HASH > 配置文件 > 内置默认值(仅开发用)。
records 单向依赖本模块(persist_jobs/迁移要混淆提示词);本模块不 import records,
记录文件相关的揭示/迁移函数(reveal_prompt_fn 等)因此留在 records。
"""
import base64 as b64mod
import hashlib
import os

from .config import logger, load_config

_PROMPT_KEY = b"braag2026floor_engine_v5_xor"

# 密码哈希加载优先级：环境变量 > 配置文件 > 内置默认值（仅开发用）
_DEFAULT_REVEAL_HASH = "455c459b728d459e5acf0373c929afc894ddb049515cd88cb046945e235e279e"

def load_reveal_hash() -> str:
    """Load the reveal-password hash from env var, config, or built-in default."""
    import os as _os
    env_hash = _os.environ.get('FLOOR_ENGINE_REVEAL_HASH', '').strip()
    if env_hash:
        return env_hash
    cfg = load_config()
    cfg_hash = cfg.get('reveal_hash', '').strip()
    if cfg_hash:
        return cfg_hash
    return _DEFAULT_REVEAL_HASH

def obfuscate_text(text: str) -> str:
    if not text: return ""
    return b64mod.b64encode(bytes([b ^ _PROMPT_KEY[i % len(_PROMPT_KEY)] for i, b in enumerate(text.encode("utf-8"))])).decode()

def deobfuscate_text(encoded: str) -> str:
    if not encoded: return ""
    try: return bytes([b ^ _PROMPT_KEY[i % len(_PROMPT_KEY)] for i, b in enumerate(b64mod.b64decode(encoded))]).decode("utf-8")
    except Exception: return ""

