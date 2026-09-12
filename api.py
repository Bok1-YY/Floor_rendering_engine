"""Backward-compatible public provider API. Implementations live in providers/."""
from .providers.dispatch import dispatch_image_generate
from .config import load_config
from typing import Optional, Tuple
from .providers.shared import (
    _IMAGE_MIME,
    _UPLOAD_JPEG_QUALITY_DEFAULT,
    _UPLOAD_MAX_SIDE_DEFAULT,
    _UPLOAD_THRESHOLD_MB_DEFAULT,
    _extract_thought_title,
    _image_thinking_config,
    _notify_stage,
    _read_image_b64,
    _redact_api_key,
    _retry_plan,
    _verify_arg
)
from .providers.gemini import (
    call_gemini_edit,
    call_gemini_generate
)
from .providers.fal import (
    AURA_SR_ENDPOINT,
    SD35_ENDPOINT,
    SD35_IMAGE_ENCODER,
    SD35_IP_ADAPTER_PATH,
    SD35_IP_ADAPTER_WEIGHT,
    _FAL_ASPECT_RATIOS,
    _FAL_MEDIA_MAX_BYTES,
    _FAL_RESOLUTIONS,
    _call_fal_json,
    _call_fal_queue_json,
    _download_fal_media,
    _fal_image_from_result,
    _fal_media_routes,
    _file_to_data_uri,
    _pil_to_data_uri,
    call_fal_aura_upscale,
    call_fal_generate,
    call_fal_sd35_generate,
    sd35_base_size
)
from .providers.inpaint import (
    BRIA_ERASER_ENDPOINT,
    DEFAULT_INPAINT_ADD_SUFFIX,
    DEFAULT_INPAINT_REMOVE_PROMPT,
    DEFAULT_QWEN_REMOVE_INSTRUCTION,
    FINEGRAIN_ERASER_ENDPOINT,
    FLUX_FILL_ENDPOINT,
    LAMA_ENDPOINT,
    QWEN_INPAINT_ENDPOINT,
    _FAL_ERASER_MODELS,
    _INPAINT_CROP_MAX_SIDE,
    _INPAINT_USAGE_LABELS,
    _composite_inpaint_result,
    _crop_inpaint_context,
    _instruction_inpaint_prompt,
    _stitch_inpaint_result,
    call_fal_inpaint,
    call_fal_mask_eraser,
    call_fal_qwen_inpaint,
    call_gemini_mark_inpaint,
    call_image_inpaint,
    effective_inpaint_candidate_count,
    resolve_inpaint_engine
)
from .providers.comfyui import (
    _COMFY_DEFAULT_WORKFLOW,
    _COMFY_PLACEHOLDER_IMAGE,
    _COMFY_PLACEHOLDER_MASK,
    _COMFY_PLACEHOLDER_NEGATIVE,
    _COMFY_PLACEHOLDER_PROMPT,
    _COMFY_PLACEHOLDER_SEED,
    _comfy_fill_workflow,
    call_comfyui_inpaint
)
from .providers.analysis import (
    FLOOR_DESEAM_INSTRUCTION,
    _OMAKASE_RESPONSE_SCHEMA,
    _OMAKASE_SYSTEM_PROMPT,
    _OMAKASE_TIMEOUT,
    _STYLE_ANALYZE_TIMEOUT,
    _STYLE_CACHE_FILE,
    _STYLE_CACHE_VERSION,
    _StyleCache,
    _clean_omakase_options,
    _style_cache,
    _style_cache_get,
    _style_cache_key,
    _style_cache_put,
    analyze_style_image,
    call_deepseek_scenes,
    call_gemini_scenes,
    call_omakase_scenes
)
from .providers.diagnostics import (
    _probe_gemini_model_endpoint,
    infer_aspect_ratio_from_b64,
    test_connection
)

# Historical utility exports retained for callers.
from .providers.shared import _req, time, random, os, json, Image, logger

def call_image_generate(api_key: str, model_id: str, prompt_text: str, image_path: str,
                        image_size: str = "4K", aspect_ratio: str = "4:3",
                        room_image_path: Optional[str] = None,
                        style_ref_image_path: Optional[str] = None,
                        on_stage=None, should_cancel=None,
                        bevel_ref_image_path: Optional[str] = None,
                        input_image_paths: Optional[list[str]] = None,
                        cinematic_mode: bool = False) -> Tuple[Optional[object], Optional[str], str]:
    """生图调度器:按 engine_config.json 的 image_provider 选线路,两条线路同契约。

    返回 (PIL.Image|None, 错误字符串|None, provider)——provider∈{'google','fal'} 是【实际】出图/尝试
    的线路(自动转 Fal 后即 'fal')，供用量统计准确归账，不再靠读配置猜测。

    - 'google'(默认):直连 Google AI Studio,沿用传入的 Gemini api_key。
    - 'fal':走 Fal 路由,改用 config 里的 fal_api_key(忽略传入的 Gemini key)。
    - 自动转线(auto_failover):线路=google 时,若直连因【网络类失败】重试耗尽且本开关开启、
      已配 Fal Key、任务未取消,则自动改走 Fal 再跑一次(用户自己的 key)。内容/请求级错误不转。
    - should_cancel(): 透传给底层,任务取消后立即停止重试,不再产生新的计费请求。
    """
    return dispatch_image_generate(
        api_key, model_id, prompt_text, image_path, image_size, aspect_ratio,
        room_image_path, style_ref_image_path, on_stage, should_cancel,
        bevel_ref_image_path, input_image_paths, cinematic_mode, cfg=load_config())


def __getattr__(name):
    # Preserve old utility/config imports while production uses explicit modules.
    from .providers import shared
    return getattr(shared, name)

__all__ = ['call_gemini_generate', 'call_fal_generate', 'call_image_generate', 'call_gemini_edit', 'analyze_style_image', 'test_connection', 'call_gemini_scenes', 'call_deepseek_scenes', 'call_omakase_scenes', 'FLOOR_DESEAM_INSTRUCTION', 'infer_aspect_ratio_from_b64']
