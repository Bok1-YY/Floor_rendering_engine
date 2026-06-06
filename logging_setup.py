# ==========================================
# 日志初始化
# ==========================================
"""Centralized logging setup for floor_engine.

Computes BASE_DIR independently to avoid circular imports with config.py.
"""

import os
import logging

# 项目根目录（floor_engine 的上级目录）
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
