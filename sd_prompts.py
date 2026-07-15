"""Stable Diffusion 专属提示词编译器。

Gemini 的对抗式提示词是长期实测资产，本模块刻意不读取、更不改写 Gemini 编译结果；
它只消费 TaskParams 的原始语义字段，生成 SD3.5 的正向/负向两段。
"""

from dataclasses import dataclass
import re

from .models import TaskParams
from .prompt_data import PROPERTY_TYPE_DICT, translate_zh_to_en, extract_en, extract_zh


SD_PROMPT_COMPILER_VERSION = "sd35-v1"


@dataclass(frozen=True)
class SDPromptBundle:
    positive: str
    negative: str
    compiler_version: str = SD_PROMPT_COMPILER_VERSION


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" ,.;")


def _english(value: str) -> str:
    value = _clean(value)
    if not value:
        return ""
    explicit = _clean(extract_en(value))
    if explicit and explicit.lower() != value.lower():
        return explicit
    translated = _clean(translate_zh_to_en(extract_zh(value) or value))
    return translated or value


def _join(parts) -> str:
    return ". ".join(_clean(p) for p in parts if _clean(p)) + "."


def _floor_layout(size: str) -> str:
    raw = size or ""
    en = _english(raw)
    if "人字" in raw:
        return f"precise herringbone installation using {en or 'realistically scaled planks'}"
    if "正方形" in raw:
        return f"precise square parquet installation using {en or 'realistically scaled modules'}"
    return f"straight plank installation using {en or 'realistically scaled floorboards'}, staggered end joints"


def _seam_positive(seam: str) -> str:
    raw = seam or ""
    if "无缝" in raw:
        return "near-continuous tightly fitted floor, extremely low-contrast joints, no dark grout lines"
    if "圆弧倒角" in raw:
        return (
            "soft rounded pressed micro-bevel edges, narrow warm low-contrast joints, gentle rounded-edge "
            "highlights, never wide or black gaps"
        )
    return _english(raw) or "physically plausible narrow floorboard joints"


def _seam_negative(seam: str) -> str:
    raw = seam or ""
    if "无缝" in raw:
        return "dark grout, black seam lines, exaggerated joints, wide floor gaps"
    if "圆弧倒角" in raw:
        return "wide gaps, black grooves, sharp drawn seam lines, completely invisible board rhythm"
    return "wide floor gaps, black grout, irregular broken joints"


def compile_sd35_prompt(
    params: TaskParams,
    *,
    positive_addition: str = "",
    negative_addition: str = "",
) -> SDPromptBundle:
    """把纯效果图参数编译成 SD3.5 正负提示词；其他工作流由 API 层拒绝。"""
    room = params.cn_room_type if params.cn_mode else params.room_type
    location = params.cn_city if params.cn_mode else ", ".join(
        x for x in (params.city, params.country) if _clean(x)
    )
    property_type = PROPERTY_TYPE_DICT.get(params.property_type) or _english(params.property_type)

    scene = ", ".join(x for x in (
        property_type,
        _english(room),
        f"located in {_english(location)}" if location else "",
        _english(params.style_type),
        _english(params.market_furniture),
    ) if x)
    camera = ", ".join(x for x in (
        _english(params.angle),
        _english(params.view),
        "balanced eye-level architectural composition",
        "the flooring occupies approximately 40 to 50 percent of the frame and anchors the foreground",
    ) if x)
    lighting = ", ".join(x for x in (
        _english(params.lighting),
        "physically plausible natural light falloff",
        "neutral accurate white balance",
    ) if x)
    floor = ", ".join(x for x in (
        "the uploaded floor swatch is the mandatory material and color reference",
        "reproduce its wood species impression, grain direction, pore detail, hue, lightness and saturation",
        _english(params.floor_tone),
        _floor_layout(params.floor_size),
        _seam_positive(params.seam_type),
        _english(params.glossiness),
        "real-world grain scale with natural non-repeating variation",
        "the same product remains recognizable across the entire visible floor",
    ) if x)

    positive = _join([
        "professional photorealistic interior architectural photography, real camera capture, not a rendering",
        scene,
        camera,
        lighting,
        floor,
        "physically accurate materials, realistic global illumination, crisp material micro-detail, clean spatial geometry",
        _english(params.custom_addition),
        positive_addition,
    ])

    avoids = [_english(x) for x in (params.avoid_items or []) if _clean(x)]
    negative = ", ".join(dict.fromkeys(filter(None, [
        "CGI", "3D render", "archviz", "illustration", "cartoon", "painting",
        "low resolution", "blurry", "soft floor texture", "oversharpened", "compression artifacts",
        "warped room geometry", "tilted walls", "duplicated furniture", "floating objects",
        "plastic wood", "fake glossy floor", "stretched wood grain", "repeating tiled texture",
        "painted-on floor pattern", "wrong plank width", "wrong installation pattern",
        "floor color shift", "orange cast", "magenta artifacts", _seam_negative(params.seam_type),
        *avoids, _clean(negative_addition),
    ])))
    return SDPromptBundle(positive=positive, negative=negative)


__all__ = ["SDPromptBundle", "SD_PROMPT_COMPILER_VERSION", "compile_sd35_prompt"]
