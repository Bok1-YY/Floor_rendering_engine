"""Inpaint provider implementation; no dependency on the public API facade."""
import base64
import io as _io_mod
from typing import Optional, Tuple

from PIL import Image

import urllib3

# 证书校验由 _verify_arg() 按配置决定（默认 tls_verify=true → 校验；显式设 false 才关闭）。
# 这里一次性静音 InsecureRequestWarning：该告警只在 verify=False 时才会出现，开启校验后自然不触发，
# 故无条件 disable 与「仅未校验时静音」等效，不会掩盖开启校验后的任何告警。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from ..config import GEMINI_MODEL_MAP, logger, load_config, get_inpaint_provider, get_comfyui_settings, get_inpaint_remove_prompt, get_inpaint_models

from .comfyui import call_comfyui_inpaint
from .diagnostics import infer_aspect_ratio_from_b64
from .fal import _call_fal_queue_json, _fal_image_from_result, _pil_to_data_uri
from .gemini import call_gemini_edit
from .shared import _notify_stage

FLUX_FILL_ENDPOINT = "fal-ai/flux-pro/v1/fill"

DEFAULT_INPAINT_REMOVE_PROMPT = (
    "Remove every foreground object inside the mask, including its complete shadow and reflection. "
    "Reconstruct only the background that logically continues from the surrounding unmasked floor, "
    "wall or interior surface. Keep the same material pattern, perspective, lighting and geometry. "
    "Leave the area empty; do not add furniture, decorations, people, text or watermarks."
)

DEFAULT_INPAINT_ADD_SUFFIX = (
    "Place it only inside the masked area and integrate it naturally into the existing interior. "
    "Match the scene's perspective, realistic scale, lighting, color temperature and contact shadow. "
    "Preserve the surrounding architecture, floor material and all unmasked content. "
    "Do not add text or watermarks."
)

_INPAINT_CROP_MAX_SIDE = 2048

def _crop_inpaint_context(image, mask, *, max_side: int = _INPAINT_CROP_MAX_SIDE,
                          mode: str = "remove"):
    """围绕 mask bbox 裁上下文窗口。返回 (crop_img, crop_mask, box)。

    移除上下文外扩 = max(bbox 长边 × 0.75, 256px)；添加为获得全局透视，
    使用 max(bbox 长边, 512px)。
    裁剪区长边 > max_side 时等比缩小（引擎侧工作分辨率），贴回时由
    _stitch_inpaint_result 缩回。mask 全空时退化为整图（调用方已校验非空）。
    """
    w, h = image.size
    bbox = mask.convert("L").point(lambda v: 255 if v >= 8 else 0).getbbox()
    if not bbox:
        return image, mask, (0, 0, w, h)
    bl, bt, br, bb = bbox
    longest = max(br - bl, bb - bt)
    # 添加物体需要看到更多全局透视/尺度；移除则优先保住局部纹理分辨率。
    pad = max(int(longest * (1.0 if mode == "add" else 0.75)), 512 if mode == "add" else 256)
    box = (max(0, bl - pad), max(0, bt - pad), min(w, br + pad), min(h, bb + pad))
    crop_img = image.crop(box)
    crop_mask = mask.crop(box)
    cw, ch = crop_img.size
    if max(cw, ch) > max_side:
        scale = max_side / max(cw, ch)
        work = (max(8, round(cw * scale)), max(8, round(ch * scale)))
        crop_img = crop_img.resize(work, Image.LANCZOS)
        # 发给模型的是二值 engine mask，缩放时不能用 LANCZOS 引入灰边。
        crop_mask = crop_mask.resize(work, Image.NEAREST)
    return crop_img, crop_mask, box

def _stitch_inpaint_result(original, result_crop, box):
    """引擎输出缩回裁剪区像素尺寸后贴回原图副本，返回全尺寸图。"""
    left, top, right, bottom = box
    target = (right - left, bottom - top)
    res = result_crop.convert("RGB")
    if res.size != target:
        res = res.resize(target, Image.LANCZOS)
    out = original.convert("RGB").copy()
    out.paste(res, (left, top))
    return out

BRIA_ERASER_ENDPOINT = "fal-ai/bria/eraser"           # $0.04/次，输出单数 image

FINEGRAIN_ERASER_ENDPOINT = "fal-ai/finegrain-eraser/mask"  # 连阴影/反射一起移除

LAMA_ENDPOINT = "fal-ai/lama"                          # 传统修复，廉价备选

_FAL_ERASER_MODELS = {
    "bria-eraser": (BRIA_ERASER_ENDPOINT, "mask_url", {"mask_type": "manual"}),
    "finegrain-eraser": (FINEGRAIN_ERASER_ENDPOINT, "mask_url", {"mode": "express"}),
    "lama": (LAMA_ENDPOINT, "mask_image_url", {}),
}

QWEN_INPAINT_ENDPOINT = "fal-ai/qwen-image-edit/inpaint"

_INPAINT_USAGE_LABELS = {
    "bria-eraser": "BriaEraser",
    "finegrain-eraser": "FinegrainEraser",
    "lama": "LaMa",
    "flux-fill": "FluxFill",
    "qwen-inpaint": "QwenInpaint",
    "gemini-mark": "GeminiMark",
    "comfyui": "ComfyUI",
}

def resolve_inpaint_engine(mode: str):
    """按 (inpaint_provider, mode) 解析实际引擎。返回 (provider, model_key, usage_label)。
    api 与 server_api 共用，保证调用与记账一致。comfyui@fal 扩展位见 config.get_inpaint_provider。
    gemini-mark 走 Google 直连（用 gemini_api_key），其余云模型走 Fal。"""
    provider = get_inpaint_provider()
    if provider == "comfyui":
        return "comfyui", "comfyui", _INPAINT_USAGE_LABELS["comfyui"]
    model_key = get_inpaint_models()["remove" if mode == "remove" else "add"]
    engine_provider = "google" if model_key == "gemini-mark" else "fal"
    return engine_provider, model_key, _INPAINT_USAGE_LABELS.get(model_key, "FluxFill")

DEFAULT_QWEN_REMOVE_INSTRUCTION = (
    "Remove the masked objects completely, together with their shadows and reflections. "
    "Seamlessly continue the surrounding floor, wall and background textures. "
    "Do not add any new object, person, text or watermark."
)

def call_fal_qwen_inpaint(api_key: str, image, mask, prompt: str, *, mode: str = "remove",
                          seed=None, on_stage=None, should_cancel=None):
    """Qwen-Image-Edit inpaint（指令式，木纹等写实纹理保留好）。返回 (PIL, error, seed)。

    schema 已经 OpenAPI 核实：prompt/image_url/mask_url 必填，输出复数 images[]。
    remove 模式注入移除指令（用户 prompt 作补充说明拼在后面）。
    """
    _notify_stage(on_stage, "🖌️ Qwen 修补中…")
    text = (prompt or "").strip()
    if mode == "remove":
        text = DEFAULT_QWEN_REMOVE_INSTRUCTION + (f" Additional guidance: {text}" if text else "")
    else:
        text = f"Edit only the masked area. Add: {text}. {DEFAULT_INPAINT_ADD_SUFFIX}"
    binary_mask = mask.convert("L").point(lambda v: 255 if v >= 128 else 0)
    payload = {
        "prompt": text,
        "image_url": _pil_to_data_uri(image, fmt="JPEG"),
        "mask_url": _pil_to_data_uri(binary_mask, fmt="PNG"),
        "num_images": 1,
        "output_format": "png",
    }
    if seed is not None:
        payload["seed"] = int(seed)
    data, err = _call_fal_queue_json(api_key, QWEN_INPAINT_ENDPOINT, payload,
                                     on_stage=on_stage, should_cancel=should_cancel)
    if err:
        return None, err, seed
    image_out, decode_err = _fal_image_from_result(data, plural=True, direct=True)
    return image_out, decode_err, data.get("seed", seed) if data else seed

def call_gemini_mark_inpaint(api_key: str, image, mask, prompt: str, *, mode: str = "remove",
                             on_stage=None, should_cancel=None):
    """Gemini『红色标记引导』局部编辑：mask 区域叠半透明红标发给 Nano Banana Pro + 指令。

    红框/红色标记法是 Nano Banana 社区验证的精确局部编辑玩法（Google 官方 Markup 同理）。
    输出是整图重生成——选区外漂移由调度器的羽化合成回贴消除，两者恰好互补。
    复用 call_gemini_edit 的完整重试/取消机制。返回 (PIL, error)。
    """
    _notify_stage(on_stage, "🖌️ Gemini 标记修补中…")
    # 标记图：mask≥128 处叠 α≈0.45 的红色
    overlay = Image.new("RGB", image.size, (255, 40, 40))
    alpha = mask.convert("L").point(lambda v: 115 if v >= 128 else 0)
    marked = Image.composite(overlay, image.convert("RGB"), alpha)
    buf = _io_mod.BytesIO()
    marked.save(buf, format="JPEG", quality=95)
    b64 = base64.b64encode(buf.getvalue()).decode()
    ar = infer_aspect_ratio_from_b64(b64)

    extra = (prompt or "").strip()
    if mode == "remove":
        instruction = (
            "Some areas in this photo are covered with a translucent red marking. "
            "Completely remove the objects under the red marking, together with their shadows and reflections. "
            "Reconstruct the floor, wall and background behind them so the scene looks naturally empty there. "
            + (f"Additional guidance: {extra}. " if extra else "")
            + "Do not add any new objects. The red marking itself must not appear in the output. "
              "Everything outside the red marking must remain unchanged."
        )
    else:
        instruction = (
            f"Replace the area covered by the translucent red marking with: {extra}. "
            "Blend it naturally with the scene's lighting, perspective and scale. "
            "Add realistic contact shadows where appropriate. The red marking itself must not appear in the output. "
            "Everything outside the red marking must remain unchanged."
        )
    model_id = GEMINI_MODEL_MAP.get("Nano Banana Pro") or next(iter(GEMINI_MODEL_MAP.values()))
    # 裁剪窗口 ≤2048，2K 输出足够且比 4K 档便宜一半
    return call_gemini_edit(api_key, model_id, instruction, b64, "2K", ar, True,
                            on_stage, should_cancel)

def call_fal_mask_eraser(api_key: str, image, mask, *, model_key: str = "bria-eraser",
                         on_stage=None, should_cancel=None):
    """专职移除模型薄封装（BRIA / Finegrain / LaMa 共用）。返回 (PIL, error)。

    这些模型没有 prompt/seed；mask 语义白=移除区。BRIA 硬性要求二值 mask（255/0），
    这里统一 point 二值化（阈值 128 恰是羽化坡中点，与 grow 语义一致）——
    羽化灰度 mask 只用于本地合成回贴，不发给 eraser。
    """
    endpoint, mask_field, extra = _FAL_ERASER_MODELS.get(model_key, _FAL_ERASER_MODELS["bria-eraser"])
    _notify_stage(on_stage, "🧹 生成式移除中…")
    binary_mask = mask.convert("L").point(lambda v: 255 if v >= 128 else 0)
    payload = {
        "image_url": _pil_to_data_uri(image, fmt="JPEG"),
        mask_field: _pil_to_data_uri(binary_mask, fmt="PNG"),
        **extra,
    }
    data, err = _call_fal_queue_json(api_key, endpoint, payload,
                                     on_stage=on_stage, should_cancel=should_cancel)
    if err:
        return None, err
    return _fal_image_from_result(data, plural=False, direct=True)

def call_fal_inpaint(api_key: str, image, mask, prompt: str, *, seed=None,
                     guidance_scale: float = 3.5, on_stage=None, should_cancel=None):
    """FLUX Fill 真 inpainting：mask 白=重绘区，选区外由调度层合成兜底。返回 (PIL, error, seed)。

    image/mask 均为 PIL 且尺寸一致（FLUX Fill 硬性要求，由调用方 _prepare_inpaint_mask 保证）。
    image 走 JPEG q95 data URI 控制 POST 体积（4K PNG data URI 太大）；mask 黑白 PNG 压缩后极小。
    """
    _notify_stage(on_stage, "🖌️ 生成式修补中…")
    payload = {
        "prompt": prompt,
        "image_url": _pil_to_data_uri(image, fmt="JPEG"),
        "mask_url": _pil_to_data_uri(mask, fmt="PNG"),
        "num_images": 1,
        "output_format": "png",
        "safety_tolerance": "2",
        "guidance_scale": max(1.0, min(10.0, float(guidance_scale))),
    }
    if seed is not None:
        payload["seed"] = int(seed)
    data, err = _call_fal_queue_json(api_key, FLUX_FILL_ENDPOINT, payload,
                                     on_stage=on_stage, should_cancel=should_cancel)
    if err:
        return None, err, seed
    image_out, decode_err = _fal_image_from_result(data, plural=True, direct=True)
    return image_out, decode_err, data.get("seed", seed) if data else seed

def _composite_inpaint_result(original, result, mask):
    """Lightroom 语义兜底：引擎整图 VAE 往返会让选区外像素轻微漂移，
    这里用羽化 mask 把结果贴回原图——选区外严格保持原像素。"""
    try:
        res = result.convert("RGB")
        base_img = original.convert("RGB")
        if res.size != base_img.size:
            res = res.resize(base_img.size, Image.LANCZOS)
        m = mask.convert("L")
        if m.size != base_img.size:
            m = m.resize(base_img.size, Image.LANCZOS)
        return Image.composite(res, base_img, m)
    except Exception:
        logger.exception("[生成式修补] 合成回贴失败，退回引擎原始输出")
        return result

def effective_inpaint_candidate_count(mode: str, requested: int, *, resolved_engine=None) -> Tuple[int, str]:
    """专职 eraser 无 seed/变体参数，重复调用通常只会重复计费；服务端强制一次。"""
    provider, model_key, _ = resolved_engine or resolve_inpaint_engine(mode)
    count = max(1, min(3, int(requested)))
    if provider == "fal" and model_key in _FAL_ERASER_MODELS and count > 1:
        return 1, "当前专职移除模型不支持可控变体，已只生成 1 张以避免重复计费"
    return count, ""

def _instruction_inpaint_prompt(mode: str, text: str) -> str:
    if mode == "remove":
        guidance = text or get_inpaint_remove_prompt()
        return DEFAULT_INPAINT_REMOVE_PROMPT + (f" Additional guidance: {guidance}" if guidance else "")
    return f"Add the following requested content: {text}. {DEFAULT_INPAINT_ADD_SUFFIX}"

def call_image_inpaint(image, mask, prompt: str, *, blend_mask=None, mode: str = "remove", seed=None,
                       on_stage=None, should_cancel=None,
                       resolved_engine=None) -> Tuple[Optional[object], Optional[str], str, str]:
    """生成式修补调度器：按 (inpaint_provider, mode) 分派。

    返回 (PIL|None, 错误|None, provider, usage_label)——供用量归账（模型标签 + 线路）。
    - comfyui：remove/add 都走本地实例（模型由 workflow 模板自带）
    - fal + remove：专职 eraser（BRIA/Finegrain/LaMa，无 prompt/seed）；配成 flux-fill 时走旧路径
    - fal + add：FLUX Fill（prompt 必填由上游校验）
    不做自动 failover：各引擎出图风格差异大，静默切换会困惑用户。
    成功后用独立 blend mask 合成回原图；add 默认保证涂抹区外像素严格不变，
    remove 则以自动外扩后的有效处理范围为边界。
    """
    # 提交时可传入引擎快照，避免排队期间修改设置导致候选数判断与实际模型不一致。
    provider, model_key, usage_label = resolved_engine or resolve_inpaint_engine(mode)
    text = (prompt or "").strip()
    # Lightroom 式选区级处理：所有引擎都只看围绕选区的上下文窗口（等效原生分辨率）
    crop_img, crop_mask, box = _crop_inpaint_context(image, mask, mode=mode)
    logger.info(f"[生成式修补] start provider={provider} model={model_key} mode={mode} "
                f"size={getattr(image, 'size', '?')} crop={crop_img.size}@{box} prompt_len={len(text)}")
    if provider == "comfyui":
        text = _instruction_inpaint_prompt(mode, text)
        comfy = get_comfyui_settings()
        img, err, _ = call_comfyui_inpaint(
            comfy["base_url"], crop_img, crop_mask, text,
            negative_prompt=comfy["negative_prompt"], seed=seed,
            workflow_path=comfy["workflow_path"], timeout=comfy["timeout"],
            on_stage=on_stage, should_cancel=should_cancel)
    elif model_key == "gemini-mark":
        gemini_key = (load_config().get("gemini_api_key") or "").strip()
        if not gemini_key:
            return None, "未配置 Gemini API Key(Gemini 标记法需要它；或在设置里换一个修补模型)", provider, usage_label
        if mode == "remove" and not text:
            text = get_inpaint_remove_prompt()
        img, err = call_gemini_mark_inpaint(gemini_key, crop_img, crop_mask, text, mode=mode,
                                            on_stage=on_stage, should_cancel=should_cancel)
    else:
        fal_key = (load_config().get("fal_api_key") or "").strip()
        if not fal_key:
            return None, "未配置 Fal API Key(请在设置里填写，或把修补引擎切到 ComfyUI)", provider, usage_label
        if model_key == "qwen-inpaint":
            img, err, _ = call_fal_qwen_inpaint(fal_key, crop_img, crop_mask, text, mode=mode,
                                                seed=seed, on_stage=on_stage, should_cancel=should_cancel)
        elif model_key in _FAL_ERASER_MODELS:
            # 专职移除模型：无 prompt/seed，用户输入的描述在此路径被忽略
            img, err = call_fal_mask_eraser(fal_key, crop_img, crop_mask, model_key=model_key,
                                            on_stage=on_stage, should_cancel=should_cancel)
        else:
            text = _instruction_inpaint_prompt(mode, text)
            img, err, _ = call_fal_inpaint(fal_key, crop_img, crop_mask, text, seed=seed,
                                           on_stage=on_stage, should_cancel=should_cancel)
    if img is not None:
        full = _stitch_inpaint_result(image, img, box)
        img = _composite_inpaint_result(image, full, blend_mask if blend_mask is not None else mask)
    return img, err, provider, usage_label


