from __future__ import annotations

import re

from ..config import DEFAULT_IMAGE_PROVIDER, load_config, logger
from .types import coerce_provider_outcome


def is_safe_failover_error(error) -> bool:
    if str(getattr(error, 'retry_safety', '')) == 'ambiguous':
        return False
    text = str(error or '')
    if 'location is not supported' in text:
        return True
    if str(getattr(error, 'retry_safety', '')) == 'safe':
        return True
    return bool(re.search(r'HTTP (408|425|429|500|502|503|504)', text))


def dispatch_image_generate(api_key: str, model_id: str, prompt_text: str, image_path: str,
                            image_size: str = '4K', aspect_ratio: str = '4:3',
                            room_image_path=None, style_ref_image_path=None, on_stage=None,
                            should_cancel=None, bevel_ref_image_path=None,
                            input_image_paths=None, cinematic_mode: bool = False, cfg: dict | None = None):
    # Lazy import keeps api.py as a compatibility facade without an import cycle.
    from ..api import call_fal_generate, call_gemini_generate

    cfg = dict(cfg) if cfg is not None else load_config()
    provider = str(cfg.get('image_provider') or DEFAULT_IMAGE_PROVIDER).strip().lower()
    if provider == 'fal':
        fal_key = str(cfg.get('fal_api_key') or '').strip()
        if not fal_key:
            return coerce_provider_outcome(
                (None, '未配置 Fal API Key(请在 API 设置里填写 Fal Key)', 'fal'),
                provider='fal', model_id=model_id)
        image, error = call_fal_generate(
            fal_key, model_id, prompt_text, image_path, image_size, aspect_ratio,
            room_image_path, style_ref_image_path, on_stage, should_cancel,
            bevel_ref_image_path=bevel_ref_image_path, input_image_paths=input_image_paths)
        return coerce_provider_outcome((image, error, 'fal'), provider='fal', model_id=model_id)

    image, error = call_gemini_generate(
        api_key, model_id, prompt_text, image_path, image_size, aspect_ratio,
        room_image_path, style_ref_image_path, on_stage, should_cancel,
        bevel_ref_image_path=bevel_ref_image_path, input_image_paths=input_image_paths,
        cinematic_mode=cinematic_mode)
    if image is not None:
        return coerce_provider_outcome((image, error, 'google'), provider='google', model_id=model_id)

    fal_key = str(cfg.get('fal_api_key') or '').strip()
    cancelled = bool(should_cancel and should_cancel())
    if bool(cfg.get('auto_failover', False)) and fal_key and not cancelled and is_safe_failover_error(error):
        logger.warning('[生图调度] Google 明确安全失败，自动转 Fal 备用线路 model=%s', model_id)
        if on_stage:
            try:
                on_stage('🔁 直连明确失败，转 Fal 备用线路…')
            except Exception:
                pass
        fallback_image, fallback_error = call_fal_generate(
            fal_key, model_id, prompt_text, image_path, image_size, aspect_ratio,
            room_image_path, style_ref_image_path, on_stage, should_cancel,
            bevel_ref_image_path=bevel_ref_image_path, input_image_paths=input_image_paths)
        if fallback_image is not None:
            return coerce_provider_outcome(
                (fallback_image, fallback_error, 'fal'), provider='fal', model_id=model_id)
        return coerce_provider_outcome(
            (None, f'直连失败({error})；Fal 备用也失败({fallback_error})', 'google'),
            provider='google', model_id=model_id)
    return coerce_provider_outcome((None, error, 'google'), provider='google', model_id=model_id)


__all__ = ['dispatch_image_generate', 'is_safe_failover_error']
