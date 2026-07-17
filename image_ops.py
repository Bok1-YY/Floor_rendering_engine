# -*- coding: utf-8 -*-
"""纯 PIL 图像处理工具 —— mask 解码/羽化、EXIF 归一化、候选落盘。

只依赖 PIL + FastAPI 的 HTTPException(参数校验即时报 4xx);无注册表/网络/编排状态。
供 routes_tools(地板可视化)与 routes_inpaint(生成式修补)消费。
所有函数体与拆分前逐字一致,仅更名转正。
"""
import base64
import io
import os
import tempfile

from fastapi import HTTPException
from PIL import Image, ImageChops, ImageFilter, ImageOps

# ── 地板可视化 mask 上限 ──
FLOOR_PREVIEW_MAX_SIDE = 1280
MAX_FLOOR_MASK_BYTES = 20 * 1024 * 1024
MAX_FLOOR_MASK_PIXELS = 30_000_000


def decode_floor_mask(value: str) -> Image.Image:
    raw_value = (value or '').split(',', 1)[-1]
    try:
        raw = base64.b64decode(raw_value, validate=True)
    except Exception:
        raise HTTPException(400, '地板遮罩不是有效的 base64 PNG')
    if not raw or len(raw) > MAX_FLOOR_MASK_BYTES:
        raise HTTPException(413, '地板遮罩文件过大')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.width * im.height > MAX_FLOOR_MASK_PIXELS:
                raise HTTPException(413, '地板遮罩尺寸过大')
            mask = im.convert('L')
            mask.load()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, '地板遮罩图片无法解码')
    if mask.getbbox() is None:
        raise HTTPException(400, '地板遮罩为空，请先涂抹地面区域')
    return mask


def decode_inpaint_mask(mask_b64: str) -> Image.Image:
    try:
        raw = base64.b64decode(mask_b64, validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if (image.format or '').upper() != 'PNG':
                raise HTTPException(400, '遮罩必须是 PNG 图片')
            if image.width * image.height > 5_000_000:
                raise HTTPException(413, '遮罩像素超过 500 万上限')
            image.load()
            return image.copy()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, '遮罩解码失败（需要 PNG 的 base64）')


def prepare_inpaint_masks(mask: Image.Image, target_size, grow: int, feather: float,
                           mode: str) -> tuple[Image.Image, Image.Image]:
    """返回 (engine_mask, blend_mask)：模型只看二值范围，最终合成单独使用羽化范围。

    remove 自动外扩以覆盖物体边缘/阴影；add 只采用用户显式 grow（前端默认 0）。
    add 羽化限制在有效选区内部，所以默认选区外逐像素不变。"""
    m = mask.convert('L')
    tw, th = target_size
    if m.size != (tw, th):
        mw, mh = m.size
        if abs((mw / mh) - (tw / th)) > 0.01 * (tw / th):
            raise HTTPException(400, '遮罩与原图宽高比不一致，请重新涂抹')
        m = m.resize((tw, th), Image.NEAREST)
    m = m.point(lambda v: 255 if v >= 128 else 0)
    bbox = m.getbbox()
    if not bbox:
        raise HTTPException(400, '遮罩为空：请先在图上涂抹要处理的区域')
    auto_grow = min(64, round(0.08 * max(bbox[2] - bbox[0], bbox[3] - bbox[1]))) if mode == 'remove' else 0
    effective_grow = max(grow, auto_grow)
    engine_mask = m
    if effective_grow > 0:
        engine_mask = m.filter(ImageFilter.GaussianBlur(effective_grow)).point(
            lambda v: 255 if v >= 32 else 0)
    blend_mask = engine_mask
    if feather > 0:
        blurred = engine_mask.filter(ImageFilter.GaussianBlur(max(1.0, feather * min(tw, th))))
        blend_mask = (ImageChops.multiply(engine_mask, blurred) if mode == 'add' else blurred)
    return engine_mask, blend_mask


def prepare_inpaint_mask(mask: Image.Image, target_size, grow: int, feather: float) -> Image.Image:
    """旧内部调用兼容：沿用 remove 的最终合成 mask。"""
    return prepare_inpaint_masks(mask, target_size, grow, feather, 'remove')[1]


def normalize_inpaint_source(image: Image.Image) -> Image.Image:
    """对齐浏览器的 EXIF 方向，并脱离原文件句柄。"""
    normalized = ImageOps.exif_transpose(image).convert('RGB')
    normalized.load()
    return normalized.copy()

def save_inpaint_candidate_png(image: Image.Image, path: str) -> None:
    """原子保存无损候选；避免 JPEG 临时图 + 最终图二次有损编码。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.inpaint_', suffix='.png', dir=os.path.dirname(path))
    os.close(fd)
    try:
        image.convert('RGB').save(tmp, format='PNG', optimize=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

