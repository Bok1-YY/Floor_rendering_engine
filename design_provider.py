"""Whole-home design: provider."""

import base64
import json
import os
from typing import Callable, Optional

import requests
from PIL import Image

from .api import (
    _call_fal_queue_json,
    _fal_image_from_result,
    _file_to_data_uri,
    call_gemini_generate,
)
from .config import FAL_MODEL_MAP, get_text_models, load_config



from .design_schema import (
    QA_PROMPT_VERSION,
    STRUCTURE_REVIEW_ITEMS,
    _QA_SCHEMA
)

def _image_part(path: str) -> dict:
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    ext = os.path.splitext(path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/webp" if ext == ".webp" else "image/jpeg"
    return {"inlineData": {"mimeType": mime, "data": encoded}}

def call_gemini_json(prompt: str, image_paths: list[str], schema: dict,
                     *, max_output_tokens: int = 7000) -> tuple[Optional[dict], Optional[str]]:
    cfg = load_config()
    key = str(cfg.get("gemini_api_key") or "").strip()
    if not key:
        return None, "未配置 Gemini API Key"
    model = str(cfg.get("design_vision_model") or get_text_models()[0])
    parts = [{"text": prompt}] + [_image_part(path) for path in image_paths]
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "maxOutputTokens": max_output_tokens,
            "temperature": 0.1,
        },
    }
    proxy = str(cfg.get("proxy") or "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    try:
        response = requests.post(url, json=payload, timeout=(30, 240), proxies=proxies,
                                 verify=bool(cfg.get("tls_verify", True)))
    except Exception as exc:
        # The URL contains the API key; exception text may echo that URL.
        return None, f"Gemini 结构化请求失败: {type(exc).__name__}"
    if response.status_code != 200:
        try:
            detail = response.json().get("error", {}).get("message") or response.text[:600]
        except Exception:
            detail = response.text[:600]
        return None, f"Gemini HTTP {response.status_code}: {detail}"
    try:
        raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(raw), None
    except Exception as exc:
        return None, f"Gemini JSON 解析失败: {exc}"

def _fal_endpoint(model_id: str) -> str:
    cfg = load_config()
    custom = cfg.get("fal_model_map") if isinstance(cfg.get("fal_model_map"), dict) else {}
    endpoint = custom.get(model_id) or FAL_MODEL_MAP.get(model_id)
    if not endpoint:
        raise ValueError(f"没有为模型配置 Fal edit endpoint: {model_id}")
    return str(endpoint)

def call_design_image(*, model_id: str, prompt: str, image_paths: list[str],
                      resolution: str, aspect_ratio: str, should_cancel: Callable[[], bool],
                      on_stage: Callable[[str], None], on_submitted: Callable[[dict], None],
                      resume_handle: Optional[dict] = None) -> tuple[Optional[Image.Image], Optional[str], str, str]:
    cfg = load_config()
    provider = str(cfg.get("image_provider") or "google").strip().lower()
    if provider == "fal":
        key = str(cfg.get("fal_api_key") or "").strip()
        if not key:
            return None, "未配置 Fal API Key", "fal", ""
        try:
            endpoint = _fal_endpoint(model_id)
        except ValueError as exc:
            return None, str(exc), "fal", ""
        uris = [_file_to_data_uri(path) for path in image_paths]
        if not all(uris):
            return None, "设计参考图不存在", "fal", endpoint
        payload = {
            "prompt": prompt,
            "image_urls": uris,
            "num_images": 1,
            "output_format": "png",
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "sync_mode": False,
            "limit_generations": True,
        }
        data, error = _call_fal_queue_json(
            key, endpoint, payload, on_stage=on_stage, should_cancel=should_cancel,
            resume_handle=resume_handle, on_submitted=on_submitted,
        )
        if data is None:
            return None, error or "Fal 设计任务失败", "fal", endpoint
        image, decode_error = _fal_image_from_result(
            data, plural=True, direct=False, on_stage=on_stage, should_cancel=should_cancel)
        return image, decode_error, "fal", endpoint
    key = str(cfg.get("gemini_api_key") or "").strip()
    if not key:
        return None, "未配置 Gemini API Key", "google", ""
    image, error = call_gemini_generate(
        key, model_id, prompt, image_paths[0], resolution, aspect_ratio,
        on_stage=on_stage, should_cancel=should_cancel, input_image_paths=image_paths,
    )
    return image, error, "google", ""

def evaluate_structure(project: dict, candidate_path: str, extra_image_paths: Optional[list[str]] = None) -> dict:
    cfg = load_config()
    if not str(cfg.get("gemini_api_key") or "").strip():
        return {
            "version": QA_PROMPT_VERSION,
            "status": "manual_required",
            "hard_fail": False,
            "summary": "没有 Gemini Key；必须人工完成全部结构核对。",
            "checks": [],
            "provider": "human_required",
        }
    anchors = project.get("anchor_set") or {}
    prompt = f"""You are an adversarial architectural QA reviewer.
Image 1 is the authoritative clean floor plan. Image 2 is the human numbered-anchor guide. Image 3 is the generated top view.
When Images 4 and 5 are present, they are the north-east and north-west Blender axonometric views; use them to catch
wall-height, opening, junction and connectivity contradictions that a top view can hide.
Human anchors are hard facts. A missing, moved, renamed, or wrong-role anchored space is a hard failure. Any copied
marker ID, point, direction line, legend, or coordinate in Image 3 is a hard failure.
ANCHORS={json.dumps(anchors.get('anchors') or [], ensure_ascii=False)}
Compare them, using this confirmed summary as supporting evidence only:
{json.dumps(project.get('plan_summary') or {}, ensure_ascii=False)}

Return one check for every ID below:
{', '.join(STRUCTURE_REVIEW_ITEMS)}
Mark fail for any changed orientation/crop, exterior footprint, room count/location, partition/adjacency,
entrance/balcony/major opening, kitchen/bath wet-zone location, added/missing space, non-orthographic view,
or generated labels/dimensions/watermarks. Uncertainty in any architectural check is a hard failure.
A beautiful image with altered structure must fail."""
    structure_source = project.get("generation_path") or project["normalized_path"]
    image_paths = [structure_source]
    if project.get("anchor_overlay_path"):
        image_paths.append(project["anchor_overlay_path"])
    image_paths.append(candidate_path)
    image_paths.extend(path for path in (extra_image_paths or []) if path and os.path.isfile(path))
    payload, error = call_gemini_json(prompt, image_paths, _QA_SCHEMA)
    if not payload:
        return {
            "version": QA_PROMPT_VERSION,
            "status": "manual_required",
            "hard_fail": False,
            "summary": error or "自动 QA 不可用；必须人工核对。",
            "checks": [],
            "provider": "gemini_unavailable",
        }
    checks = list(payload.get("checks") or [])
    by_id = {str(row.get("check_id")): row for row in checks if isinstance(row, dict)}
    normalized_checks = []
    hard_fail = bool(payload.get("hard_fail"))
    for check_id in STRUCTURE_REVIEW_ITEMS:
        row = by_id.get(check_id) or {
            "check_id": check_id, "status": "uncertain", "evidence": "模型漏答",
        }
        if row.get("status") != "pass":
            hard_fail = True
        normalized_checks.append(row)
    return {
        "version": QA_PROMPT_VERSION,
        "status": "failed" if hard_fail else "passed",
        "hard_fail": hard_fail,
        "summary": str(payload.get("summary") or ""),
        "checks": normalized_checks,
        "provider": "gemini",
    }
