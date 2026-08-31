# ==========================================
# 日志初始化
# ==========================================
"""Centralized logging setup for floor_engine.

Computes BASE_DIR independently to avoid circular imports with config.py.
"""

import os
import logging
from .runtime_paths import resolve_data_dir

_BASE_DIR = resolve_data_dir(os.path.dirname(os.path.abspath(__file__)))

os.makedirs(_BASE_DIR, exist_ok=True)

if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler(
                os.path.join(_BASE_DIR, "app_local_save.log"), encoding='utf-8'
            ),
            logging.StreamHandler(),
        ],
    )

logger = logging.getLogger(__name__)
