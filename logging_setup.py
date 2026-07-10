# ==========================================
# 日志初始化
# ==========================================
"""Centralized logging setup for floor_engine.

Computes BASE_DIR independently to avoid circular imports with config.py.
"""

import os
import logging
import sys

# Keep this independent from config.py to avoid a circular import, but use the same frozen-aware rules.
_IS_FROZEN = getattr(sys, 'frozen', False) or ('__compiled__' in globals())
if _IS_FROZEN:
    _exe = (os.environ.get('NUITKA_ONEFILE_BINARY')
            or os.environ.get('NUITKA_ORIGINAL_ARGV0')
            or sys.argv[0]
            or sys.executable)
    _BASE_DIR = os.path.dirname(os.path.abspath(_exe))
else:
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
