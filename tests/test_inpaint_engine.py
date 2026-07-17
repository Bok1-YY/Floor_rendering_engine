# -*- coding: utf-8 -*-
"""生成式修补引擎解析（resolve_inpaint_engine / get_inpaint_models）单测。

只测配置→分派的纯逻辑：默认值、非法值回落、comfyui 优先级、usage 标签一致性。
不发任何网络请求。
"""
import io
import json

import pytest
from fastapi import HTTPException
from PIL import Image

from Floor_engine_server import server_state, image_ops
from Floor_engine_server import api as api_mod
from Floor_engine_server import config as config_mod
from Floor_engine_server import server_api
from Floor_engine_server.task_registry import TaskRegistry


def _patch_config(monkeypatch, cfg: dict):
    """api 与 config 各自 import 了 load_config，两处都要 patch。"""
    monkeypatch.setattr(config_mod, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(api_mod, "load_config", lambda: dict(cfg))


def test_default_models(monkeypatch):
    """老配置（无新键）：remove 默认专职 eraser、add 默认 FLUX Fill。"""
    _patch_config(monkeypatch, {})
    assert config_mod.get_inpaint_models() == {"remove": "bria-eraser", "add": "flux-fill"}


@pytest.mark.parametrize("bad", ["", "nonexistent-model", 123, None])
def test_invalid_model_falls_back(monkeypatch, bad):
    _patch_config(monkeypatch, {"inpaint_remove_model": bad, "inpaint_add_model": bad})
    models = config_mod.get_inpaint_models()
    assert models["remove"] == config_mod.DEFAULT_INPAINT_REMOVE_MODEL
    assert models["add"] == config_mod.DEFAULT_INPAINT_ADD_MODEL


@pytest.mark.parametrize("key,label", [
    ("bria-eraser", "BriaEraser"),
    ("finegrain-eraser", "FinegrainEraser"),
    ("lama", "LaMa"),
    ("flux-fill", "FluxFill"),
])
def test_resolve_fal_remove_models(monkeypatch, key, label):
    _patch_config(monkeypatch, {"inpaint_provider": "fal", "inpaint_remove_model": key})
    assert api_mod.resolve_inpaint_engine("remove") == ("fal", key, label)


def test_resolve_add_always_flux_fill(monkeypatch):
    """add 模式不受 remove 模型配置影响。"""
    _patch_config(monkeypatch, {"inpaint_provider": "fal", "inpaint_remove_model": "bria-eraser"})
    assert api_mod.resolve_inpaint_engine("add") == ("fal", "flux-fill", "FluxFill")


def test_comfyui_provider_overrides_models(monkeypatch):
    """provider=comfyui 时模型键不生效，remove/add 都走本地实例。"""
    _patch_config(monkeypatch, {"inpaint_provider": "comfyui", "inpaint_remove_model": "bria-eraser"})
    assert api_mod.resolve_inpaint_engine("remove") == ("comfyui", "comfyui", "ComfyUI")
    assert api_mod.resolve_inpaint_engine("add") == ("comfyui", "comfyui", "ComfyUI")


def test_remove_model_table_covers_all_choices():
    """INPAINT_REMOVE_MODELS 的每个键都必须有明确的分派归宿（eraser 表 / 专属分支 / flux 退路），
    否则分派会静默退回 flux-fill（配置项形同虚设）。"""
    dedicated = {"flux-fill", "qwen-inpaint", "gemini-mark"}
    for key in config_mod.INPAINT_REMOVE_MODELS:
        assert key in api_mod._FAL_ERASER_MODELS or key in dedicated


def test_resolve_gemini_mark_remove(monkeypatch):
    """gemini-mark 记账走 google 线路（用 gemini_api_key）。"""
    _patch_config(monkeypatch, {"inpaint_provider": "fal", "inpaint_remove_model": "gemini-mark"})
    assert api_mod.resolve_inpaint_engine("remove") == ("google", "gemini-mark", "GeminiMark")


def test_qwen_only_available_for_add(monkeypatch):
    """qwen-inpaint 做移除已实测证伪（原图内容被原样重绘），只允许配在添加模式；
    remove 配置里写它必须回落默认 eraser。"""
    _patch_config(monkeypatch, {"inpaint_provider": "fal",
                                "inpaint_remove_model": "qwen-inpaint",
                                "inpaint_add_model": "qwen-inpaint"})
    assert api_mod.resolve_inpaint_engine("remove")[1] == config_mod.DEFAULT_INPAINT_REMOVE_MODEL
    assert api_mod.resolve_inpaint_engine("add") == ("fal", "qwen-inpaint", "QwenInpaint")


def test_qwen_payload_shape(monkeypatch):
    """Qwen inpaint：prompt 必填注入移除指令、mask 二值、输出取 images[]（复数）。"""
    from PIL import Image

    captured = {}

    def fake_queue(api_key, endpoint, payload, *, on_stage=None, should_cancel=None):
        captured["endpoint"] = endpoint
        captured["payload"] = payload
        return None, "stop-here"

    monkeypatch.setattr(api_mod, "_call_fal_queue_json", fake_queue)
    api_mod.call_fal_qwen_inpaint("k", Image.new("RGB", (32, 32)), Image.new("L", (32, 32), 255),
                                  "顺便把地毯也去掉", mode="remove", seed=7)
    assert captured["endpoint"] == api_mod.QWEN_INPAINT_ENDPOINT
    p = captured["payload"]
    assert "Remove the masked objects" in p["prompt"] and "顺便把地毯也去掉" in p["prompt"]
    assert p["seed"] == 7 and "image_url" in p and "mask_url" in p


def test_gemini_mark_builds_marked_image(monkeypatch):
    """标记法：发给 Gemini 的源图在 mask 区域必须带红色标记，指令含移除阴影措辞。"""
    import base64, io
    from PIL import Image

    captured = {}

    def fake_edit(api_key, model_id, instruction, source_b64, image_size, ar, preserve,
                  on_stage=None, should_cancel=None):
        captured["instruction"] = instruction
        captured["b64"] = source_b64
        captured["image_size"] = image_size
        return None, "stop-here"

    monkeypatch.setattr(api_mod, "call_gemini_edit", fake_edit)
    img = Image.new("RGB", (100, 100), (0, 128, 0))
    mask = Image.new("L", (100, 100), 0)
    for x in range(40, 60):
        for y in range(40, 60):
            mask.putpixel((x, y), 255)
    api_mod.call_gemini_mark_inpaint("k", img, mask, "", mode="remove")
    assert "shadows and reflections" in captured["instruction"]
    assert "red marking" in captured["instruction"]
    assert captured["image_size"] == "2K"
    marked = Image.open(io.BytesIO(base64.b64decode(captured["b64"])))
    in_r, in_g, _ = marked.getpixel((50, 50))
    out_r, out_g, _ = marked.getpixel((10, 10))
    assert in_r > out_r + 60, "mask 区应明显偏红"
    assert abs(out_g - 128) < 20, "mask 外应保持原色"


def test_crop_context_and_stitch():
    """裁剪回贴：bbox 外扩、坐标正确、贴回后选区外像素不变。"""
    from PIL import Image

    img = Image.new("RGB", (2000, 1500), (50, 60, 70))
    mask = Image.new("L", (2000, 1500), 0)
    for x in range(900, 1100):
        for y in range(700, 800):
            mask.putpixel((x, y), 255)
    crop_img, crop_mask, box = api_mod._crop_inpaint_context(img, mask)
    l, t, r, b = box
    assert l < 900 and t < 700 and r > 1100 and b > 800, "上下文窗口必须大于 bbox"
    assert crop_img.size == (r - l, b - t), "小于 max_side 时不应缩放"
    assert crop_mask.size == crop_img.size
    # stitch：引擎输出染成红色 → 贴回后 box 内为红、box 外保持原色
    result_crop = Image.new("RGB", crop_img.size, (200, 0, 0))
    full = api_mod._stitch_inpaint_result(img, result_crop, box)
    assert full.size == img.size
    assert full.getpixel((5, 5)) == (50, 60, 70)
    assert full.getpixel((1000, 750)) == (200, 0, 0)


def test_crop_context_downscales_when_large():
    """裁剪区长边超过 2048 时等比缩小到工作分辨率。"""
    from PIL import Image

    img = Image.new("RGB", (4096, 3072))
    mask = Image.new("L", (4096, 3072), 0)
    for x in range(0, 4000, 4):
        for y in range(0, 3000, 4):
            mask.putpixel((x, y), 255)   # 巨大 mask → 裁剪区≈整图
    crop_img, crop_mask, box = api_mod._crop_inpaint_context(img, mask)
    assert max(crop_img.size) <= api_mod._INPAINT_CROP_MAX_SIDE
    assert crop_mask.size == crop_img.size


def test_add_crop_keeps_more_scene_context_than_remove():
    img = Image.new("RGB", (3000, 2000))
    mask = _rect_mask(size=img.size, box=(1450, 950, 1549, 1049))

    _, _, remove_box = api_mod._crop_inpaint_context(img, mask, mode="remove")
    _, _, add_box = api_mod._crop_inpaint_context(img, mask, mode="add")

    assert add_box[2] - add_box[0] > remove_box[2] - remove_box[0]
    assert add_box[3] - add_box[1] > remove_box[3] - remove_box[1]


def test_eraser_binarizes_mask(monkeypatch):
    """call_fal_mask_eraser 发出的 mask 必须是二值 PNG（BRIA 硬性要求 255/0）。"""
    from PIL import Image
    import base64, io

    captured = {}

    def fake_queue(api_key, endpoint, payload, *, on_stage=None, should_cancel=None):
        captured["endpoint"] = endpoint
        captured["payload"] = payload
        return None, "stop-here"   # 不继续走网络

    monkeypatch.setattr(api_mod, "_call_fal_queue_json", fake_queue)
    img = Image.new("RGB", (64, 64), (100, 100, 100))
    feathered = Image.new("L", (64, 64), 0)
    for x in range(64):
        for y in range(64):
            feathered.putpixel((x, y), min(255, x * 4))   # 渐变灰 mask 模拟羽化
    api_mod.call_fal_mask_eraser("k", img, feathered, model_key="bria-eraser")

    assert captured["endpoint"] == api_mod.BRIA_ERASER_ENDPOINT
    assert captured["payload"].get("mask_type") == "manual"
    mask_uri = captured["payload"]["mask_url"]
    raw = base64.b64decode(mask_uri.split(",", 1)[1])
    sent = Image.open(io.BytesIO(raw)).convert("L")
    values = set(sent.get_flattened_data())
    assert values <= {0, 255}, f"mask 必须二值，实际出现灰度值: {sorted(values)[:10]}"


def test_lama_uses_mask_image_url_field(monkeypatch):
    """LaMa 的 mask 字段名与 BRIA 不同（mask_image_url）。"""
    from PIL import Image

    captured = {}

    def fake_queue(api_key, endpoint, payload, *, on_stage=None, should_cancel=None):
        captured["endpoint"] = endpoint
        captured["payload"] = payload
        return None, "stop-here"

    monkeypatch.setattr(api_mod, "_call_fal_queue_json", fake_queue)
    api_mod.call_fal_mask_eraser("k", Image.new("RGB", (8, 8)), Image.new("L", (8, 8), 255),
                                 model_key="lama")
    assert captured["endpoint"] == api_mod.LAMA_ENDPOINT
    assert "mask_image_url" in captured["payload"]
    assert "mask_url" not in captured["payload"]


def test_inpaint_candidate_png_is_pixel_exact(tmp_path):
    """候选必须无损落盘，避免 JPEG 候选 + 最终图的二次有损编码。"""
    src = Image.new("RGB", (17, 13))
    for x in range(src.width):
        for y in range(src.height):
            src.putpixel((x, y), ((x * 17) % 256, (y * 23) % 256, ((x + y) * 29) % 256))
    path = tmp_path / "candidate.png"

    image_ops.save_inpaint_candidate_png(src, str(path))
    saved = Image.open(path)
    saved.load()

    assert saved.format == "PNG"
    assert list(saved.get_flattened_data()) == list(src.get_flattened_data())


def test_inpaint_queue_rejects_when_active_limit_reached():
    """达到运行中上限后背压判断必须拒绝新付费任务。"""
    entries = {
        f"busy-{i}": {"status": "running", "ts": i, "candidates": []}
        for i in range(server_state.MAX_ACTIVE_INPAINTS)
    }
    assert server_state.inpaint_queue_is_full(entries) is True


def test_inpaint_trim_frees_slot_at_exact_capacity():
    reg = TaskRegistry("inpaints", max_entries=server_state.MAX_INPAINTS,
                       is_terminal=server_state.inpaint_is_terminal,
                       on_evict=server_state.delete_inpaint_files)
    reg.replace((f"done-{i}", {"status": "cancelled", "ts": i, "candidates": []})
                for i in range(server_state.MAX_INPAINTS))
    reg.trim(reserve=1)
    assert len(reg) == server_state.MAX_INPAINTS - 1
    assert reg.get("done-0") is None


def _rect_mask(size=(400, 300), box=(150, 100, 249, 199)):
    mask = Image.new("L", size, 0)
    for x in range(box[0], box[2] + 1):
        for y in range(box[1], box[3] + 1):
            mask.putpixel((x, y), 255)
    return mask


def test_add_mask_default_is_strict_and_binary():
    raw = _rect_mask()
    engine, blend = image_ops.prepare_inpaint_masks(raw, raw.size, 0, 0.01, "add")

    assert set(engine.get_flattened_data()) <= {0, 255}
    assert engine.getbbox() == raw.getbbox()
    assert blend.getbbox() == raw.getbbox(), "添加模式的羽化不得扩到用户选区外"


def test_remove_mask_auto_expands_beyond_brush():
    raw = _rect_mask()
    engine, blend = image_ops.prepare_inpaint_masks(raw, raw.size, 0, 0.01, "remove")
    rl, rt, rr, rb = raw.getbbox()
    el, et, er, eb = engine.getbbox()

    assert el < rl and et < rt and er > rr and eb > rb
    assert blend.getbbox() != raw.getbbox()


def test_add_composite_keeps_every_pixel_outside_brush():
    original = Image.new("RGB", (400, 300), (20, 40, 60))
    generated = Image.new("RGB", original.size, (240, 20, 10))
    raw = _rect_mask(size=original.size)
    _, blend = image_ops.prepare_inpaint_masks(raw, original.size, 0, 0.01, "add")

    result = api_mod._composite_inpaint_result(original, generated, blend)
    assert result.getpixel((149, 150)) == original.getpixel((149, 150))
    assert result.getpixel((250, 150)) == original.getpixel((250, 150))
    assert result.getpixel((200, 150)) != original.getpixel((200, 150))


def test_pure_eraser_forces_single_candidate(monkeypatch):
    _patch_config(monkeypatch, {
        "inpaint_provider": "fal",
        "inpaint_remove_model": "bria-eraser",
        "inpaint_add_model": "flux-fill",
    })
    count, notice = api_mod.effective_inpaint_candidate_count("remove", 3)
    assert count == 1
    assert "避免重复计费" in notice
    assert api_mod.effective_inpaint_candidate_count("add", 3) == (3, "")
    # 排队任务使用提交时的引擎快照；后续设置变化不能把已收口的 Eraser 重新放大到 3 次。
    _patch_config(monkeypatch, {"inpaint_provider": "fal", "inpaint_remove_model": "flux-fill"})
    assert api_mod.effective_inpaint_candidate_count(
        "remove", 3, resolved_engine=("fal", "bria-eraser", "BriaEraser"),
    )[0] == 1


def test_mode_specific_prompt_compiler_preserves_user_request(monkeypatch):
    _patch_config(monkeypatch, {"inpaint_remove_prompt": "continue the oak floor"})
    remove = api_mod._instruction_inpaint_prompt("remove", "")
    add = api_mod._instruction_inpaint_prompt("add", "一盆龟背竹")

    assert "shadow and reflection" in remove
    assert "continue the oak floor" in remove
    assert "一盆龟背竹" in add
    assert "only inside the masked area" in add
    assert "perspective" in add and "contact shadow" in add


def test_qwen_add_payload_wraps_user_prompt(monkeypatch):
    captured = {}

    def fake_queue(api_key, endpoint, payload, *, on_stage=None, should_cancel=None):
        captured.update(payload)
        return None, "stop-here"

    monkeypatch.setattr(api_mod, "_call_fal_queue_json", fake_queue)
    api_mod.call_fal_qwen_inpaint(
        "k", Image.new("RGB", (16, 16)), Image.new("L", (16, 16), 255),
        "一盆龟背竹", mode="add",
    )

    assert "一盆龟背竹" in captured["prompt"]
    assert "only inside the masked area" in captured["prompt"]
    assert "contact shadow" in captured["prompt"]


def test_inpaint_source_applies_exif_orientation():
    raw = Image.new("RGB", (40, 20), (100, 120, 140))
    exif = raw.getexif()
    exif[274] = 6  # 90° clockwise
    buf = io.BytesIO()
    raw.save(buf, format="JPEG", exif=exif)
    opened = Image.open(io.BytesIO(buf.getvalue()))

    normalized = image_ops.normalize_inpaint_source(opened)
    assert normalized.size == (20, 40)
    assert normalized.mode == "RGB"


def test_inpaint_mask_decoder_rejects_non_png():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="JPEG")
    import base64
    with pytest.raises(HTTPException) as exc:
        image_ops.decode_inpaint_mask(base64.b64encode(buf.getvalue()).decode())
    assert exc.value.status_code == 400
    assert "PNG" in exc.value.detail


def test_default_comfy_workflow_does_not_expand_mask_twice():
    with open(api_mod._COMFY_DEFAULT_WORKFLOW, encoding="utf-8") as file:
        workflow = json.load(file)
    assert workflow["7"]["inputs"]["expand"] == 0
    assert workflow["8"]["inputs"]["grow_mask_by"] == 8
