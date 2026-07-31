"""pytest 公共夹具：让 prompt 黄金测试在隔离、离线、确定的环境下跑。

三件事：
  1. 把 Floor_engine_server 的【父目录】塞进 sys.path —— 包内部是相对导入（from .config ...），
     必须以顶层包 `Floor_engine_server` 被导入，故需父目录可见。
  2. 把 records.MAIN_OUTPUT_DIR 重定向到 pytest 临时目录 —— save_task_files_html 会经
     get_json_path() 在 MAIN_OUTPUT_DIR 下写 JSON 记录 + 优化图 png，重定向后绝不污染真实 output_files/。
  3. 强制翻译走【离线 FALLBACK 路径】（prompt_data.TRANSLATOR_AVAILABLE=False）—— 否则
     deep-translator 会联网且返回不稳定，快照会漂。离线路径只查静态 TECH_DICT/FALLBACK_DICT，
     未命中的中文统一返回 "[原文]"，完全确定。
"""
import os
import sys
import importlib.util

import pytest

# ── 1. 让历史包名与 GitHub 默认克隆目录名都可用 ──
_PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FLOOR_DATA_DIR", os.path.join(_PKG_DIR, "data", "pytest"))
_PACKAGE_NAME = "Floor_engine_server"
if _PACKAGE_NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _PACKAGE_NAME,
        os.path.join(_PKG_DIR, "__init__.py"),
        submodule_search_locations=[_PKG_DIR],
    )
    if _spec is None or _spec.loader is None:
        raise RuntimeError("无法从项目目录加载 Floor Engine 测试包")
    _package = importlib.util.module_from_spec(_spec)
    sys.modules[_PACKAGE_NAME] = _package
    _spec.loader.exec_module(_package)


@pytest.fixture(autouse=True)
def isolated_offline_env(monkeypatch, tmp_path):
    """每个测试：输出目录隔离到 tmp + 翻译离线确定。autouse，所有用例自动生效。"""
    out_dir = tmp_path / "output_files"
    out_dir.mkdir(parents=True, exist_ok=True)
    # get_json_path() 读的是 records 模块里的 MAIN_OUTPUT_DIR 这个名字，patch 它即可。
    monkeypatch.setattr("Floor_engine_server.records.MAIN_OUTPUT_DIR", str(out_dir))
    # translate_zh_to_en() 在调用时读 prompt_data 模块全局 TRANSLATOR_AVAILABLE，patch 成 False 强制离线。
    monkeypatch.setattr("Floor_engine_server.prompt_data.TRANSLATOR_AVAILABLE", False)
    return out_dir


@pytest.fixture
def swatch_image(tmp_path):
    """生成一张极小的纯色地板小样图当 image_path。

    prompt 文本不依赖图片像素（色调等都由参数显式传入），故纯色图足够、且让快照稳定。
    """
    from PIL import Image
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    p = src / "测试地板小样.png"
    Image.new("RGB", (64, 64), (180, 150, 110)).save(str(p))
    return str(p)
