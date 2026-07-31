# -*- coding: utf-8 -*-
"""Gemini 文本导演规划器。

只负责把现有场景参数压缩成一段可执行的电影真实感指令；不处理地板材质，
不参与图片调用，也不改变不支持的工作流。网络失败时由调用方使用本地兜底。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import requests

from .config import load_config, logger, short_text


CINEMATIC_FALLBACK_DIRECTION = (
    "Treat this as a restrained frame from a real lived-in moment, not an advertisement. "
    "Place the camera at a physically plausible observer position. If people or animals are "
    "already requested, keep them in an unfinished, naturally balanced action rather than a pose; "
    "do not introduce them otherwise. Use one motivated practical "
    "light source, believable contact shadows, ordinary material imperfections, soft highlight "
    "roll-off and selective optical softness. Avoid glossy CGI surfaces, HDR clarity, artificial "
    "rim light, excessive bokeh, teal-orange grading and staged commercial expressions. Keep a "
    "large, unobstructed floor area visible as the product anchor."
)

_SUPPORTED_MARKERS = ("纯效果图", "宠物友好", "参照模式", "Omakase")
_TIMEOUT = (10, 45)

_SYSTEM_PROMPT = """你是一名住宅商业摄影的现场导演。请把输入的室内场景参数转译成一张真实、可拍、
带有电影叙事感的英文镜头指令。重点不是添加滤镜，而是决定摄影机为什么在这里、人物或宠物正在做什么、
观众的视线怎样移动、光从哪里来，以及哪些普通细节让画面不像广告或 CGI。

产品约束拥有最高优先级：
1. 地板必须保持大面积可见并作为商品视觉锚点，人物、宠物和家具不能遮住主要地面。
2. 不描述或改写地板的颜色、纹理、尺寸、拼缝、光泽与铺装方式；这些由下游系统控制。
3. 严格遵守输入中的禁止项，不能擅自增加被禁止的人物、宠物或道具。
4. 只设计一个镜头，不生成三联、片名、海报或文字。
5. 不使用导演名、电影名、演员名、品牌名或现成 IP。
6. 避免广告摆拍、夸张表情、漂浮姿势、棚拍美光、塑料皮肤/毛发、全画面锐利、HDR、青橙滤镜、
无来源轮廓光、过度景深虚化、烟雾粒子和概念渲染感。
7. final_direction 必须是精简、直接可交给图像模型执行的英文段落，不解释思考过程。

如果场景没有人物或宠物，也要通过可信机位、现实光源、克制综合色、自然不完美和非样板间陈设提高真实感。
只返回符合给定 JSON Schema 的对象。"""

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "situation": {"type": "STRING"},
        "camera_position": {"type": "STRING"},
        "visual_flow": {"type": "STRING"},
        "action": {"type": "STRING"},
        "practical_light": {"type": "STRING"},
        "color_thesis": {"type": "STRING"},
        "realism_constraints": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "minItems": 2,
            "maxItems": 6,
        },
        "final_direction": {"type": "STRING"},
    },
    "required": [
        "situation",
        "camera_position",
        "visual_flow",
        "action",
        "practical_light",
        "color_thesis",
        "realism_constraints",
        "final_direction",
    ],
}


def supports_cinematic(workflow_mode: str) -> bool:
    """电影增强只作用于会生成新场景的 Gemini 工作流。"""
    mode = str(workflow_mode or "")
    return any(marker in mode for marker in _SUPPORTED_MARKERS)


def _value(params: Any, name: str, default: Any = "") -> Any:
    if isinstance(params, dict):
        return params.get(name, default)
    return getattr(params, name, default)


def build_cinematic_context(params: Any, style_analysis_text: str = "") -> dict:
    """只抽取镜头规划需要的场景字段，刻意不向规划器暴露地板物理规格。"""
    workflow_mode = str(_value(params, "workflow_mode"))
    pet_action = str(_value(params, "pet_action"))
    avoid_items = _value(params, "avoid_items", []) or []
    if not isinstance(avoid_items, list):
        avoid_items = [str(avoid_items)]
    # UI 的通用默认禁止项可能同时包含“任何宠物/人物”；显式宠物工作流优先，
    # 避免导演规划器被互相矛盾的上下文要求压掉主体。
    if "宠物友好" in workflow_mode:
        avoid_items = [item for item in avoid_items if "宠物" not in str(item)]
        if pet_action == "主宠互动":
            avoid_items = [item for item in avoid_items if "人物" not in str(item)]
    return {
        "workflow_mode": workflow_mode,
        "property_type": str(_value(params, "property_type")),
        "room_type": str(_value(params, "room_type")),
        "location": " / ".join(filter(None, [
            str(_value(params, "continent")),
            str(_value(params, "country")),
            str(_value(params, "city")),
        ])),
        "style": str(_value(params, "style_type")),
        "lighting_preference": str(_value(params, "lighting")),
        "camera_preference": str(_value(params, "angle")),
        "pet_type": str(_value(params, "pet_type")),
        "pet_action": pet_action,
        "pet_focus": str(_value(params, "pet_focus")),
        "user_direction": str(_value(params, "custom_addition")),
        "omakase_scene": str(_value(params, "scene_override")),
        "reference_style_notes": str(style_analysis_text or ""),
        "avoid_items": [str(item) for item in avoid_items if str(item).strip()],
        "product_constraints": (
            "Keep roughly 40-50% of the image as clearly visible, unobstructed floor. "
            "Do not invent or alter any floor material property."
        ),
    }


def _verify_arg(cfg: dict):
    if not bool(cfg.get("tls_verify", True)):
        return False
    ca = str(cfg.get("tls_ca_bundle") or "").strip()
    if ca and os.path.exists(ca):
        return ca
    return True


def _redact(text: Any) -> str:
    return re.sub(r"([?&]key=)[^&\s)]+", r"\1***", str(text or ""))


def _clean_plan(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    fields = (
        "situation",
        "camera_position",
        "visual_flow",
        "action",
        "practical_light",
        "color_thesis",
        "final_direction",
    )
    clean = {field: str(raw.get(field) or "").strip() for field in fields}
    constraints = raw.get("realism_constraints") or []
    if not isinstance(constraints, list):
        constraints = [constraints]
    clean["realism_constraints"] = [
        str(item).strip() for item in constraints if str(item).strip()
    ][:6]
    direction = clean["final_direction"]
    if len(direction) < 40:
        return None
    clean["final_direction"] = direction[:4000]
    return clean


def plan_cinematic_scene(
    params: Any,
    *,
    api_key: str,
    model: str,
    style_analysis_text: str = "",
) -> tuple[str, dict, Optional[str]]:
    """返回 (最终英文指令, 结构化规划, 错误)。错误时不抛异常，由主流程降级。"""
    if not supports_cinematic(_value(params, "workflow_mode")):
        return "", {}, "当前工作流不支持电影真实感规划"
    if not str(api_key or "").strip():
        return "", {}, "未配置 Gemini API Key"

    cfg = load_config()
    proxy = str(cfg.get("proxy") or "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    context = build_cinematic_context(params, style_analysis_text)
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [{
            "role": "user",
            "parts": [{
                "text": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            }],
        }],
        "generationConfig": {
            "maxOutputTokens": 2200,
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
        },
    }
    headers = {
        "x-goog-api-key": str(api_key).strip(),
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=_TIMEOUT,
            proxies=proxies,
            verify=_verify_arg(cfg),
        )
    except Exception as exc:
        return "", {}, f"电影镜头规划请求异常: {_redact(exc)}"
    if response.status_code != 200:
        return (
            "",
            {},
            f"电影镜头规划 HTTP {response.status_code}: "
            f"{short_text(getattr(response, 'text', ''), 240)}",
        )
    try:
        content = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        plan = _clean_plan(json.loads(content))
    except Exception as exc:
        return "", {}, f"电影镜头规划返回解析失败: {_redact(exc)}"
    if not plan:
        return "", {}, "电影镜头规划未返回可用指令"
    logger.info(
        "[电影真实感] planner_ok model=%s direction_len=%s",
        model,
        len(plan["final_direction"]),
    )
    return plan["final_direction"], plan, None


__all__ = [
    "CINEMATIC_FALLBACK_DIRECTION",
    "build_cinematic_context",
    "plan_cinematic_scene",
    "supports_cinematic",
]
