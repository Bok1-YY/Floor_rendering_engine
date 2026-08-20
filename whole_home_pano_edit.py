# -*- coding: utf-8 -*-
"""整张 ERP 编辑的 prompt 契约与环形移位修缝(文档 §7.5)。

原则:
  * Image 1(RGB clay ERP)是唯一编辑画布;depth/normal/edge/semantic/subject-ID 只作几何权威
    参考,逐项编号,不让模型猜谁是权威;
  * 首尾缝修补只允许一次受控环形移位 + 中央窄带 mask;mask 是提示性指导,
    修补前后必须由球面 gate 复核 mask 外结构,失败即回退原候选。
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

import numpy as np
from PIL import Image
import cv2

ERPA_IMAGE_ROLES = (
    (1, 'rgb', '完整 RGB clay equirectangular 全景:唯一编辑画布,360°×180°,2:1。'
          '它固定每个相机射线、裁剪、墙、天花、地板、开口、物体轮廓与遮挡。'),
    (2, 'depth', '同球面 metric depth 全景(整组固定 near/far):固定深度顺序。'),
    (3, 'normal', '同球面 world-space normal 全景(XYZ→RGB):固定表面朝向。'),
    (4, 'edge', '同球面 edge 全景:固定建筑与物体结构边界。'),
    (5, 'semantic', '同球面 semantic 全景:固定类别角色,永远不要复制其假色。'),
    (6, 'subject_id', '同球面 subject-ID 全景:固定每个开口/物体实例身份,永远不要复制其假色。'),
)


def build_erp_edit_prompt(
        manifest: dict, style_description: str = '', *,
        generation_targets: Optional[Iterable[str]] = None,
        consistency_contract: str = '', appearance_reference_count: int = 0) -> str:
    """§7.5 提示词骨架:几何权威、允许/禁止项、全景契约。"""
    pano_id = str(manifest.get('pano_id') or '')
    width = int(manifest.get('erp_width') or 0)
    height = int(manifest.get('erp_height') or 0)
    targets = sorted({str(value).strip() for value in generation_targets or []
                      if str(value).strip()})
    lines = [
        f'Edit Image 1 only. It is a complete monoscopic equirectangular panorama '
        f'({pano_id}): 360 degrees horizontally, 180 degrees vertically, aspect ratio 2:1 '
        f'({width}x{height}). The left and right edges are adjacent and must join continuously.',
        '',
        'Geometry authority:',
        '- Image 1 fixes every camera ray, crop, wall, ceiling, floor, opening, and every existing '
        'object silhouette/occlusion.',
        '- Image 2 fixes metric depth order.',
        '- Image 3 fixes world-space surface orientation.',
        '- Image 4 fixes architectural and object edge boundaries.',
        '- Image 5 fixes semantic role identity; never copy its false colors.',
        '- Image 6 fixes exact opening/object instance identity; never copy its false colors.',
        '- The supplied edit mask is white in appearance-editable regions and black on locked '
        'architectural edge bands. Black pixels must remain exactly unchanged.',
    ]
    if appearance_reference_count:
        first = 7
        last = first + int(appearance_reference_count) - 1
        label = f'Image {first}' if first == last else f'Images {first}-{last}'
        lines.append(
            f'- {label} are accepted panoramas from this exact home. Use them only as appearance '
            'references for material, palette, furniture family, and lighting. Never import their '
            'geometry, camera, object position, or room layout into Image 1.')
    if style_description:
        lines.append(f'- Text-only approved style/material direction: {style_description}. '
                     'It affects appearance only and cannot override Images 1-6.')
    if consistency_contract:
        lines.append(f'- Whole-home appearance contract shared by every hotspot: {consistency_contract}')
    lines += [
        '',
        'Allowed change: convert the approved clay scene into a photorealistic interior using '
        'realistic finishes, materials, existing-furniture appearance, and physically coherent lighting.',
    ]
    if targets:
        lines += [
            f'Controlled furnishing exception: Image 1 is known to omit only these movable '
            f'room-program roles: {", ".join(targets)}.',
            '- Add exactly one coherent instance for each listed missing role, and no other new role. '
            'Keep it on the visible room floor, at plausible scale, outside door/window openings and '
            'circulation paths; never use it to hide or reinterpret architecture.',
            '- Existing objects remain fixed in footprint, count, orientation, silhouette family, and '
            'occlusion. The exception applies only to the explicitly listed missing roles.',
        ]
    else:
        lines.append('Object rule: do not add, delete, duplicate, or move any object.')
    lines += [
        'Forbidden: move the viewpoint; change projection; add/delete/move walls, doors, windows or '
        'columns; reveal hidden rooms; change floor elevation; bend straight architectural edges; '
        'turn a window into a door or a door into a window.',
        '',
        'Panorama contract:',
        '- Preserve exact 2:1 equirectangular layout.',
        '- Make longitude -180 and +180 continuous in geometry, texture, lighting and floor seams.',
        '- Preserve a level horizon and coherent ceiling/floor at both poles.',
        '- Return one image only, with no frame, labels, grid, split panels or text.',
    ]
    return '\n'.join(lines)


def build_flux_canny_erp_prompt(
        manifest: dict, style_description: str = '', *,
        generation_targets: Optional[Iterable[str]] = None,
        consistency_contract: str = '', gutter_px: int = 64,
        core_width: int = 1408) -> str:
    """Prompt for the two-input FLUX Canny panorama path.

    Unlike GPT Image edit, this endpoint receives only a clay RGB initialization
    and a canonical white-on-black Canny control.  The prompt therefore never
    claims that depth/semantic images were provided.  Geometry is enforced by
    the control image and then independently measured by the P0 gate.
    """
    pano_id = str(manifest.get('pano_id') or '')
    targets = sorted({str(value).strip() for value in generation_targets or []
                      if str(value).strip()})
    gutter_deg = 360.0 * max(0, int(gutter_px)) / max(1, int(core_width))
    lines = [
        f'Create one photorealistic monoscopic interior panorama for {pano_id}. The RGB input is '
        'the exact clay initialization and the Canny input is the authoritative architectural and '
        'object silhouette map. Follow every Canny line exactly: keep its position, curvature, '
        'length, junction, opening boundary and occlusion.',
        f'The provider canvas contains circular wrap context gutters of {gutter_px}px '
        f'({gutter_deg:.1f} degrees) on both sides. The left gutter duplicates the far-right '
        'longitude and the right gutter duplicates the far-left longitude. Render them as the '
        'same continuous scene so the central crop has a seamless -180/+180 join.',
        'Change appearance only: replace clay colors with realistic surface materials, existing '
        'furniture finishes, physically coherent neutral daylight and soft residential lighting.',
        'Never move the camera. Never add, remove, widen, narrow, bend or reinterpret a wall, '
        'column, floor, ceiling, door, window, opening or existing object. Never turn a wall end '
        'into a timber screen, cabinet, door or decorative panel. Never reveal a room that is '
        'occluded in the RGB/Canny inputs.',
    ]
    if targets:
        lines += [
            f'The only permitted new movable program roles are: {", ".join(targets)}.',
            'Add at most one plausible instance of each listed role, entirely on visible floor and '
            'away from openings/circulation. Add no other furniture or decoration category.',
        ]
    else:
        lines.append('Do not add, delete, duplicate or move any furniture or decoration.')
    if style_description:
        lines.append(f'Appearance direction only: {style_description}.')
    if consistency_contract:
        lines.append(f'Whole-home appearance contract: {consistency_contract}')
    lines += [
        'Preserve a level horizon and coherent ceiling/floor poles. Preserve the equirectangular '
        'projection; straight 3D architecture may appear curved only by normal ERP projection.',
        'No text, people, labels, borders, grids, split panels, lens flare, fisheye photo frame or '
        'floor-plan overlay. Return one image only on the full supplied padded canvas.',
    ]
    return '\n'.join(lines)


def prepare_flux_canny_inputs(
        rgb_erp: Image.Image, edge_erp: Image.Image, *,
        core_width: int = 1408, core_height: int = 704,
        gutter_px: int = 64) -> tuple[Image.Image, Image.Image]:
    """Create same-size RGB and canonical Canny inputs with circular gutters.

    The renderer's edge channel is black-on-white.  FLUX Canny convention is a
    real Canny map (white edges on black), so we derive it deterministically
    rather than inverting renderer pixels and accidentally treating blank sky
    as a control region.
    """
    width = int(core_width)
    height = int(core_height)
    gutter = int(gutter_px)
    if width < 512 or height < 256 or width != height * 2:
        raise ValueError('flux_canny_core_must_be_2_1')
    if gutter < 0 or gutter >= width // 4:
        raise ValueError('flux_canny_gutter_invalid')
    resampling = getattr(Image, 'Resampling', Image)
    rgb_core = rgb_erp.convert('RGB').resize((width, height), resampling.LANCZOS)
    edge_core = edge_erp.convert('RGB').resize((width, height), resampling.LANCZOS)
    gray = cv2.cvtColor(np.asarray(edge_core, dtype=np.uint8), cv2.COLOR_RGB2GRAY)
    canny = cv2.Canny(gray, 30, 90)
    # One-pixel dilation survives provider preprocessing without swallowing
    # narrow door/window gaps.  No semantic masks or model output are used.
    canny = cv2.dilate(canny, np.ones((3, 3), dtype=np.uint8), iterations=1)
    control_core = Image.fromarray(np.repeat(canny[..., None], 3, axis=2), 'RGB')
    if gutter:
        rgb_array = np.asarray(rgb_core)
        control_array = np.asarray(control_core)
        rgb_array = np.concatenate((rgb_array[:, -gutter:], rgb_array,
                                    rgb_array[:, :gutter]), axis=1)
        control_array = np.concatenate((control_array[:, -gutter:], control_array,
                                        control_array[:, :gutter]), axis=1)
        rgb_core = Image.fromarray(rgb_array, 'RGB')
        control_core = Image.fromarray(control_array, 'RGB')
    return rgb_core, control_core


def finalize_flux_canny_output(
        provider_image: Image.Image, *, target_width: int, target_height: int,
        core_width: int = 1408, core_height: int = 704,
        gutter_px: int = 64) -> Image.Image:
    """Validate provider size, remove circular gutters and upscale deterministically."""
    expected = (int(core_width) + 2 * int(gutter_px), int(core_height))
    if provider_image.size != expected:
        raise ValueError(
            f'flux_canny_provider_size_mismatch:{provider_image.size[0]}x'
            f'{provider_image.size[1]}!={expected[0]}x{expected[1]}')
    gutter = int(gutter_px)
    core = provider_image.convert('RGB').crop(
        (gutter, 0, gutter + int(core_width), int(core_height)))
    target = (int(target_width), int(target_height))
    if target[0] != target[1] * 2:
        raise ValueError('flux_canny_target_must_be_2_1')
    resampling = getattr(Image, 'Resampling', Image)
    return core.resize(target, resampling.LANCZOS)


def build_structure_holdout_mask(edge_erp: Image.Image, *, protection_deg: float = .5) -> Image.Image:
    """Return GPT edit mask with narrow architectural edge bands locked.

    White pixels may be edited; black pixels are immutable.  The dilation width
    is angular rather than resolution-specific.  ERP is 2:1, so horizontal and
    vertical degrees-per-pixel are equal.
    """
    edge_rgb = np.asarray(edge_erp.convert('RGB'), dtype=np.uint8)
    gray = cv2.cvtColor(edge_rgb, cv2.COLOR_RGB2GRAY)
    # Renderer edge channels may use either bright-on-dark or dark-on-bright.
    # Canny on the channel itself is invariant to that convention.
    binary = cv2.Canny(gray, 30, 90)
    width = int(edge_rgb.shape[1])
    radius = max(1, int(math.ceil(float(protection_deg) * width / 360.0)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    protected = cv2.dilate(binary, kernel) > 0
    mask = np.full(edge_rgb.shape[:2], 255, dtype=np.uint8)
    mask[protected] = 0
    # Preserve the exact wrap samples and pole rows; material synthesis may
    # approach them but cannot repaint the mathematical boundaries themselves.
    mask[:, :radius] = 0
    mask[:, -radius:] = 0
    mask[:radius, :] = 0
    mask[-radius:, :] = 0
    return Image.fromarray(np.repeat(mask[..., None], 3, axis=2), 'RGB')


def circular_shift_erp(image: Image.Image, shift_x: Optional[int] = None) -> Image.Image:
    """ERP 水平环形移位(默认 W/2):原左右接缝移动到图像中央(文档 §7.5-1)。"""
    width, height = image.size
    offset = width // 2 if shift_x is None else int(shift_x) % width
    array = np.asarray(image)
    return Image.fromarray(np.roll(array, offset, axis=1))


def build_seam_repair_mask(erp_width: int, erp_height: int, band_deg: float = 12.0) -> Image.Image:
    """中央窄带 mask(按角度定义,不按固定像素,文档 §7.5-3)。

    白色=允许编辑的窄带;其余全黑。band_deg 为半带宽(总带宽 2×band_deg)。
    """
    half_band_rad = math.radians(max(1.0, min(30.0, float(band_deg))))
    half_width = math.ceil(erp_width * (half_band_rad / math.pi))
    mask = np.zeros((erp_height, erp_width, 3), dtype=np.uint8)
    center = erp_width // 2
    left = max(0, center - half_width)
    right = min(erp_width, center + half_width)
    mask[:, left:right] = 255
    return Image.fromarray(mask, 'RGB')


def build_seam_repair_prompt() -> str:
    """环形修缝专用 prompt:只衔接窄带两侧纹理,禁止改变结构(文档 §7.5-3/4)。"""
    return (
        'This image is a horizontally shifted equirectangular panorama. The original left/right '
        'seam now runs vertically near the image center. Edit ONLY inside the provided mask band: '
        'blend the two sides of the seam so texture, lighting and floor seams continue smoothly '
        'across it. Do not move or repaint walls, openings, objects, shadows or depth outside the '
        'band; do not add or remove any element; keep the horizon level and the 2:1 layout. '
        'Return one image only, with no frame, labels or text.'
    )
