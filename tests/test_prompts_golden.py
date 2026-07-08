"""提示词黄金回归测试。

prompts.save_task_files_html() 是这工具的核心壁垒——4 套工作流的英文 prompt 组装逻辑。
本测试用固定参数跑它，把返回的两段 prompt（[2]=combined、[7]=pro）与提交在 tests/golden/
下的基准文本做字符串相等比对，作为改 prompts.py 时的回归安全网。

纯本地：不联网、不调 API、不写真实 output_files/（见 conftest.py 的隔离夹具）。

用法：
  pytest Floor_engine_server/tests/ -q            # 跑回归比对
  UPDATE_GOLDEN=1 pytest Floor_engine_server/tests/   # 有意改了 prompt 后，刷新基准（务必人工核对 diff！）
"""
import difflib
import os

import pytest

from Floor_engine_server.models import TaskParams, task_params_to_kwargs
from Floor_engine_server.prompts import save_task_files_html

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")

# 5 套工作流 + 2 个无缝拼法专项，每套一组固定参数（mode 字符串取自 server_api._WORKFLOW_MODES；用子串匹配分支）。
# 公共参数尽量用 TaskParams 默认值（都是真实 UI 取值），只覆盖区分各工作流所必需的字段。
CASES = [
    ("pure", "纯效果图 (生成全新空间)", {}),
    ("replace", "地板替换 (保持原图换地板)", {}),
    ("pet", "宠物友好 (动物独处/主宠互动)", {
        "pet_type": "金毛犬", "pet_action": "趴卧休息", "pet_focus": "宠物为主角",
    }),
    ("ref", "参照模式 (风格参照图生新图)", {
        # 固定一段风格描述，绕开参照模式 Step-1 的在线风格分析（那步才调 API）。
        "style_analysis_text": (
            "A bright Scandinavian living room with warm light oak flooring, large "
            "windows, white walls, and minimal natural-wood furniture."
        ),
    }),
    ("omakase", "Omakase (AI 代笔场景)", {
        # 固定英文散文（不经翻译，离线确定）——锁住"场景层散文 + 地板技术层焊接"的组装结构。
        "scene_override": (
            "A cozy Dutch family living room on a rainy afternoon, a mother and a small child "
            "playing with wooden blocks on the floor, soft window light, lived-in everyday details."
        ),
    }),
    # 无缝拼法专项：收尾覆盖段(SEAMLESS_NEGATIVE)的 motif 按拼法切换，曾有方格拼误落 chevron 的 bug。
    ("seamless_square", "纯效果图 (生成全新空间)", {"floor_size": "正方形拼：609 x 609 mm"}),
    ("seamless_hb", "纯效果图 (生成全新空间)", {"floor_size": "常规人字拼：125 x 625 mm"}),
    # 墙板模式三子行为：参数化模板（消费 style/lighting/angle/room + panel_size），走独立墙板管线。
    ("panel_redesign", "墙板模式 (护墙板/木饰面：再设计/替换/原创)", {
        "panel_submode": "再设计", "panel_size": "标准竖板 约200mm宽",
        "lighting": "🌤️ 漫反射柔光 (Diffused Light) - 光线均匀柔和，无直射高光，完美还原地板色彩。",
        "style_analysis_text": (
            "A warm minimalist wall-panel entry with vertical walnut wainscoting, slim brass "
            "trim, matte stone floor, off-white walls, soft even daylight."
        ),
    }),
    ("panel_replace", "墙板模式 (护墙板/木饰面：再设计/替换/原创)", {"panel_submode": "替换"}),
    ("panel_pure", "墙板模式 (护墙板/木饰面：再设计/替换/原创)", {
        "panel_submode": "纯原创", "panel_size": "细木格栅/线条板",
        "angle": "85mm lens (Telephoto)",
        "lighting": "🌅 黄金时刻逆光 (Golden Hour) - 傍晚低角度暖光，拉长阴影。",
    }),
]


def _build_params(mode: str, image_path: str, extra: dict) -> TaskParams:
    return TaskParams(
        workflow_mode=mode,
        model_choice="Pro",
        image_path=image_path,
        style_type="原木风",
        last_image_path="",
        **extra,
    )


def _golden_path(case_id: str, kind: str) -> str:
    return os.path.join(GOLDEN_DIR, f"{case_id}_{kind}.txt")


def _check_against_golden(case_id: str, kind: str, actual: str) -> None:
    path = _golden_path(case_id, kind)
    if os.environ.get("UPDATE_GOLDEN"):
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(actual)
        return
    assert os.path.exists(path), (
        f"缺少基准文件 {path}；首次请先跑  UPDATE_GOLDEN=1 pytest  生成。"
    )
    with open(path, "r", encoding="utf-8") as f:
        expected = f.read()
    if actual != expected:
        diff = "\n".join(difflib.unified_diff(
            expected.splitlines(), actual.splitlines(),
            fromfile=f"{case_id}_{kind}.txt (基准)", tofile=f"{case_id}_{kind} (本次)",
            lineterm="",
        ))
        pytest.fail(
            f"[{case_id}/{kind}] prompt 与基准不一致。\n"
            f"若为有意改动，确认下面 diff 符合预期后跑 UPDATE_GOLDEN=1 pytest 刷新基准：\n{diff}"
        )


@pytest.mark.parametrize("case_id, mode, extra", CASES, ids=[c[0] for c in CASES])
def test_prompt_golden(case_id, mode, extra, swatch_image):
    params = _build_params(mode, swatch_image, extra)
    result = save_task_files_html(**task_params_to_kwargs(params))
    # 契约：返回 8 元组，[2]=combined prompt、[7]=Pro 版 prompt（见 prompts.py 末尾 return）。
    assert isinstance(result, tuple) and len(result) == 8, f"返回值结构变了: {result!r}"
    combined, pro = result[2], result[7]
    assert combined, f"[{case_id}] combined prompt 为空，可能参数没跑进生成分支"
    _check_against_golden(case_id, "combined", combined)
    _check_against_golden(case_id, "pro", pro)


def test_prompt_assembly_is_deterministic(swatch_image):
    """同参数连跑两次，两段 prompt 必须逐字相同——守住'快照可比对'的前提。"""
    params = _build_params("纯效果图 (生成全新空间)", swatch_image, {})
    r1 = save_task_files_html(**task_params_to_kwargs(params))
    r2 = save_task_files_html(**task_params_to_kwargs(params))
    assert r1[2] == r2[2], "combined prompt 两次不一致——prompt 组装里混入了非确定性"
    assert r1[7] == r2[7], "Pro prompt 两次不一致——prompt 组装里混入了非确定性"


def test_seamless_square_has_no_chevron(swatch_image):
    """语义锁：无缝+正方形拼的 B2/Pro 提示词绝不能出现 chevron（人字纹）——
    收尾覆盖段(READ THIS LAST)曾硬编码 chevron，以最高优先级压过正文的方格描述。"""
    params = _build_params("纯效果图 (生成全新空间)", swatch_image,
                           {"floor_size": "正方形拼：609 x 609 mm"})
    result = save_task_files_html(**task_params_to_kwargs(params))
    assert "chevron" not in result[2].lower(), "B2/combined 提示词混入 chevron"
    assert "chevron" not in result[7].lower(), "Pro 提示词混入 chevron"


def test_error_paths_return_8_tuple():
    """契约锁：错误路径与成功路径同为 8 元组——曾返回 7 元组导致调用方解包崩溃、真实报错被吞。"""
    r = save_task_files_html(**task_params_to_kwargs(
        TaskParams(workflow_mode="纯效果图 (生成全新空间)", model_choice="Pro", image_path="")))
    assert isinstance(r, tuple) and len(r) == 8, f"空路径返回结构不对: {r!r}"
    r2 = save_task_files_html(**task_params_to_kwargs(
        TaskParams(workflow_mode="纯效果图 (生成全新空间)", model_choice="Pro",
                   image_path=os.path.join(os.path.dirname(__file__), "conftest.py"),  # 非图片文件 → 处理失败分支
                   last_image_path="")))
    assert isinstance(r2, tuple) and len(r2) == 8, f"坏图返回结构不对: {r2!r}"
    assert r2[0] is None and "失败" in r2[1]
