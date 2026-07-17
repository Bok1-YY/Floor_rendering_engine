"""色彩匹配与校色算法 —— 纯 numpy/PIL 图像处理,无网络、无状态。

从 api.py 抽出的本地色彩子系统:LAB 空间 Reinhard 色彩迁移、区域识色诊断、
高级参数绝对校色(色温/曝光/亮度区间等)及其 4K 分片变体。

消费方:server_api(校色三端点)、api.call_gemini_edit(磨缝后全局校色)。
所有函数签名与抽出前逐字一致。
"""
import math

from PIL import Image


def match_color_to_reference(src_img, ref_img, strength=1.0):
    """把 src 的整体色彩统计对齐到 ref（LAB 空间均值/方差迁移，Reinhard 色彩迁移）。

    用于消除 img2img 磨缝带来的全局偏色——内容几乎一致，只把色温/饱和度拉回原图。

    Args:
        src_img: 待校色的 PIL Image
        ref_img: 颜色参考 PIL Image
        strength: 0.0~1.0，迁移强度。1.0=完全迁移(原行为)，0.0=保持原图不变
    """
    import numpy as np
    src = src_img.convert('LAB'); ref = ref_img.convert('LAB')
    # ref 只用于取每通道全局均值/方差(Reinhard 统计量)，不参与逐像素运算。
    # 保持 uint8(4K≈50MB，而非 float32 的 ~192MB)，mean()/std() 以 float64 归约——
    # 统计量精确、与原算法数值等价，却省下一份大数组。
    r = np.asarray(ref, dtype=np.uint8)
    # s 用可写 float32 副本，逐通道原地变换 + 原地 clip，避免再开一份 out(~192MB)。
    # 峰值由 ~576MB(s+r+out 三份 float32) 降到 ~292MB(s float32 + r/out uint8)。
    s = np.array(src, dtype=np.float32)
    for c in range(3):
        s_mean, s_std = s[..., c].mean(), s[..., c].std()
        r_mean, r_std = float(r[..., c].mean()), float(r[..., c].std())
        if s_std < 1e-5:
            s[..., c] += (r_mean - s_mean)
        else:
            s[..., c] = (s[..., c] - s_mean) * (r_std / s_std) + r_mean
    np.clip(s, 0, 255, out=s)
    out = s.astype(np.uint8)
    del s, r
    transferred = Image.fromarray(out, mode='LAB').convert('RGB')

    # 按强度与原图混合（strength=1.0 时等价于原行为）
    if strength < 1.0:
        src_rgb = src_img.convert('RGB')
        return Image.blend(src_rgb, transferred, strength)
    return transferred


_COLOR_ADJUSTMENT_DEFAULTS = {
    'temperature': 0.0,
    'tint': 0.0,
    'exposure': 0.0,
    'contrast': 0.0,
    'highlights': 0.0,
    'shadows': 0.0,
    'whites': 0.0,
    'blacks': 0.0,
    'midtones': 0.0,
    'saturation': 0.0,
}


def _normalize_color_adjustments(adjustments=None):
    """Return a complete, float-only adjustment mapping for the color engine."""
    raw = adjustments or {}
    if hasattr(raw, 'model_dump'):
        raw = raw.model_dump()
    return {key: float(raw.get(key, default)) for key, default in _COLOR_ADJUSTMENT_DEFAULTS.items()}


def _estimate_auto_color_adjustments(src_mean, src_std, ref_mean, ref_std):
    """Approximate Reinhard's LAB transform as familiar editor controls.

    The profile is relative to the unmodified Gemini image. It is used to move
    the advanced sliders to a useful automatic baseline; the exact legacy auto
    render remains available separately.
    """
    import math

    def clamp(value, lo, hi):
        return float(min(max(value, lo), hi))

    src_chroma_std = max(1e-5, (float(src_std[1]) + float(src_std[2])) / 2.0)
    ref_chroma_std = (float(ref_std[1]) + float(ref_std[2])) / 2.0
    luminance_ratio = clamp(float(ref_std[0]) / max(1e-5, float(src_std[0])), 0.7, 1.3)
    chroma_ratio = clamp(ref_chroma_std / src_chroma_std, 0.7, 1.3)
    return {
        'temperature': clamp((float(ref_mean[2]) - float(src_mean[2])) / 0.24, -100, 100),
        'tint': clamp((float(ref_mean[1]) - float(src_mean[1])) / 0.24, -100, 100),
        'exposure': clamp((float(ref_mean[0]) - float(src_mean[0])) / 50.0, -2, 2),
        'contrast': clamp(100.0 * math.log2(luminance_ratio), -100, 100),
        'highlights': 0.0,
        'shadows': 0.0,
        'whites': 0.0,
        'blacks': 0.0,
        'midtones': 0.0,
        'saturation': clamp((chroma_ratio - 1.0) * 100.0, -100, 100),
    }


_COLOR_ANALYSIS_ZONE_LABELS = {
    'highlight': '受光区',
    'penumbra': '半阴影区',
    'shadow': '阴影区',
}


def _signed_lab_array(img):
    """Return Pillow LAB as float32 with conventional signed a*/b* channels."""
    import numpy as np

    lab = np.asarray(img.convert('LAB'), dtype=np.float32).copy()
    lab[..., 1] = np.where(lab[..., 1] > 127, lab[..., 1] - 256, lab[..., 1])
    lab[..., 2] = np.where(lab[..., 2] > 127, lab[..., 2] - 256, lab[..., 2])
    return lab


def _representative_color_patch(src_crop, floor_mask, zone_mask):
    """Pick a 4:3 source crop with the highest zone/floor pixel coverage."""
    import numpy as np

    ch, cw = zone_mask.shape
    ys, xs = np.nonzero(zone_mask)
    if not len(xs):
        return None

    patch_w = min(cw, max(48, int(round(cw * 0.25))))
    patch_h = max(36, int(round(patch_w * 0.75)))
    if patch_h > ch:
        patch_h = ch
        patch_w = min(cw, max(1, int(round(patch_h * 4.0 / 3.0))))
    patch_w, patch_h = max(1, patch_w), max(1, patch_h)

    # Integral images make several thousand candidate windows inexpensive.
    zone_integral = np.pad(zone_mask.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    floor_integral = np.pad(floor_mask.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    if len(xs) > 3000:
        sample = np.linspace(0, len(xs) - 1, 3000, dtype=np.int64)
        xs, ys = xs[sample], ys[sample]
    x0 = np.clip(xs - patch_w // 2, 0, max(0, cw - patch_w))
    y0 = np.clip(ys - patch_h // 2, 0, max(0, ch - patch_h))
    x1, y1 = x0 + patch_w, y0 + patch_h

    def sums(integral):
        return integral[y1, x1] - integral[y0, x1] - integral[y1, x0] + integral[y0, x0]

    area = float(patch_w * patch_h)
    score = 0.82 * (sums(zone_integral) / area) + 0.18 * (sums(floor_integral) / area)
    best = int(np.argmax(score))
    return src_crop.crop((int(x0[best]), int(y0[best]), int(x1[best]), int(y1[best])))


def _color_bias_hints(zone_values, ref_values):
    """Return structured, user-facing color-cast hints for one lighting zone."""
    import numpy as np

    source_a, source_b, source_chroma = zone_values
    ref_a, ref_b, ref_chroma = ref_values
    hints = []
    delta_b = float(source_b - ref_b)
    delta_a = float(source_a - ref_a)
    chroma_threshold = max(3.0, abs(float(ref_chroma)) * 0.12)
    delta_chroma = float(source_chroma - ref_chroma)
    if delta_b >= 3.0:
        hints.append({'code': 'warm', 'text': '偏暖，建议降低色温'})
    elif delta_b <= -3.0:
        hints.append({'code': 'cool', 'text': '偏冷，建议提高色温'})
    if delta_a >= 3.0:
        hints.append({'code': 'magenta', 'text': '偏洋红，建议降低色调'})
    elif delta_a <= -3.0:
        hints.append({'code': 'green', 'text': '偏绿，建议提高色调'})
    if delta_chroma >= chroma_threshold:
        hints.append({'code': 'saturated', 'text': '偏饱和，建议降低饱和度'})
    elif delta_chroma <= -chroma_threshold:
        hints.append({'code': 'gray', 'text': '偏灰，建议增加饱和度'})
    if not hints:
        hints.append({'code': 'matched', 'text': '冷暖和饱和度接近小样'})
    return hints


def analyze_color_region(src_img, ref_img, rect):
    """Analyze representative floor patches in highlight/penumbra/shadow zones.

    The caller supplies the user-selected floor rectangle.  Analysis remains
    advisory: returned adjustments are absolute manual controls relative to the
    unmodified source and do not mutate either image.
    """
    import numpy as np

    src = src_img.convert('RGB')
    width, height = src.size
    x, y, w, h = rect
    x0 = max(0, min(width - 1, int(round(x * width))))
    y0 = max(0, min(height - 1, int(round(y * height))))
    x1 = max(x0, min(width, int(round((x + w) * width))))
    y1 = max(y0, min(height, int(round((y + h) * height))))
    defaults = dict(_COLOR_ADJUSTMENT_DEFAULTS)

    def unavailable(message):
        return {
            'status': 'insufficient_region',
            'confidence': 'low',
            'summary': message,
            'recommended_adjustments': defaults,
            'zones': [{
                'zone': key, 'label': label, 'image': None, 'luminance': None,
                'hints': [{'code': 'unavailable', 'text': message}],
            } for key, label in _COLOR_ANALYSIS_ZONE_LABELS.items()],
        }

    if x1 - x0 < max(32, int(0.02 * width)) or y1 - y0 < max(32, int(0.02 * height)):
        return unavailable('框选区域太小，请重新框选更大的纯地板范围')

    crop = src.crop((x0, y0, x1, y1))
    lab = _signed_lab_array(crop)
    flat = lab.reshape(-1, 3)
    center = np.median(flat, axis=0)
    scale = np.maximum(np.median(np.abs(flat - center), axis=0) * 1.4826, (2.0, 1.5, 1.5))
    distance = np.sqrt((((flat - center) / scale) ** 2).mean(axis=1))
    floor_mask = (distance <= 3.2).reshape(lab.shape[:2])
    floor_count = int(floor_mask.sum())
    if floor_count < max(512, int(0.05 * floor_mask.size)):
        return unavailable('未找到足够的地板像素，请避开地毯和家具重新框选')

    floor_lab = lab[floor_mask]
    ref_lab = _signed_lab_array(ref_img.convert('RGB')).reshape(-1, 3)
    ref_a = float(np.median(ref_lab[:, 1]))
    ref_b = float(np.median(ref_lab[:, 2]))
    ref_chroma = float(np.median(np.hypot(ref_lab[:, 1], ref_lab[:, 2])))
    ref_values = (ref_a, ref_b, ref_chroma)
    p10, p33, p67, p90 = np.quantile(floor_lab[:, 0], (0.10, 0.33, 0.67, 0.90))
    low_dynamic = float(p90 - p10) < 12.0

    if low_dynamic:
        masks = {
            'highlight': np.zeros_like(floor_mask),
            'penumbra': floor_mask,
            'shadow': np.zeros_like(floor_mask),
        }
    else:
        masks = {
            'highlight': floor_mask & (lab[..., 0] >= p67),
            'penumbra': floor_mask & (lab[..., 0] > p33) & (lab[..., 0] < p67),
            'shadow': floor_mask & (lab[..., 0] <= p33),
        }

    zones = []
    zone_values = []
    for key, label in _COLOR_ANALYSIS_ZONE_LABELS.items():
        zone_mask = masks[key]
        if not zone_mask.any():
            zones.append({
                'zone': key, 'label': label, 'image': None, 'luminance': None,
                'hints': [{'code': 'unavailable', 'text': f'未检测到明显{label}'}],
            })
            continue
        values = lab[zone_mask]
        median_a = float(np.median(values[:, 1]))
        median_b = float(np.median(values[:, 2]))
        median_chroma = float(np.median(np.hypot(values[:, 1], values[:, 2])))
        zone_values.append((median_a, median_b, median_chroma))
        zones.append({
            'zone': key,
            'label': label,
            'image': _representative_color_patch(crop, floor_mask, zone_mask),
            'luminance': round(float(np.median(values[:, 0])) / 255.0 * 100.0, 1),
            'hints': _color_bias_hints((median_a, median_b, median_chroma), ref_values),
        })

    source_a, source_b, source_chroma = np.mean(np.asarray(zone_values), axis=0)
    delta_a, delta_b = float(source_a - ref_a), float(source_b - ref_b)
    chroma_threshold = max(3.0, abs(ref_chroma) * 0.12)
    delta_chroma = float(source_chroma - ref_chroma)

    def clamp(value, lo=-100.0, hi=100.0):
        return float(min(max(value, lo), hi))

    recommended = dict(defaults)
    recommended['temperature'] = 0.0 if abs(delta_b) < 3.0 else round(clamp(-delta_b / 0.24))
    recommended['tint'] = 0.0 if abs(delta_a) < 3.0 else round(clamp(-delta_a / 0.24))
    if abs(delta_chroma) >= chroma_threshold and source_chroma > 1e-5:
        recommended['saturation'] = round(clamp((ref_chroma / source_chroma - 1.0) * 100.0))

    valid_values = np.asarray(zone_values)
    warm_signs = set(np.sign(valid_values[np.abs(valid_values[:, 1] - ref_b) >= 3.0, 1] - ref_b))
    sat_delta = valid_values[:, 2] - ref_chroma
    sat_signs = set(np.sign(sat_delta[np.abs(sat_delta) >= chroma_threshold]))
    mixed = len(warm_signs) > 1 or len(sat_signs) > 1

    summary_parts = []
    if recommended['temperature'] < 0:
        summary_parts.append('整体偏暖')
    elif recommended['temperature'] > 0:
        summary_parts.append('整体偏冷')
    if recommended['tint'] < 0:
        summary_parts.append('整体偏洋红')
    elif recommended['tint'] > 0:
        summary_parts.append('整体偏绿')
    if recommended['saturation'] < 0:
        summary_parts.append('整体偏饱和')
    elif recommended['saturation'] > 0:
        summary_parts.append('整体偏灰')
    summary = '、'.join(summary_parts) + '；建议使用下方参数。' if summary_parts else \
        '整体冷暖和饱和度接近小样，无需明显调整。'
    if mixed:
        summary = '各光照区偏色方向不一致，可能存在混合光源；' + summary
    elif low_dynamic:
        summary = '光照层次不明显；' + summary

    return {
        'status': 'low_dynamic_range' if low_dynamic else 'ok',
        'confidence': 'low' if (mixed or low_dynamic) else 'high',
        'summary': summary,
        'recommended_adjustments': recommended,
        'zones': zones,
    }


def apply_color_adjustments(img, adjustments=None):
    """Apply deterministic photo-editor controls to an RGB image.

    Temperature/tint operate in LAB chroma, exposure in linear RGB, tonal
    controls in LAB luminance, and saturation around Rec.709 luma. Validation
    of public ranges lives in server_api; this helper remains reusable by tests
    and other local callers.
    """
    import numpy as np

    adj = _normalize_color_adjustments(adjustments)
    out = img.convert('RGB')

    temperature = adj['temperature']
    tint = adj['tint']
    if temperature or tint:
        lab = np.array(out.convert('LAB'), dtype=np.float32)
        # Pillow LAB stores a/b as signed bytes encoded in uint8 (e.g. 226=-30).
        # Decode before shifting, then encode again; direct uint8 arithmetic
        # would wrap cool/green colors across the neutral point.
        for channel, offset in ((2, temperature * 0.24), (1, tint * 0.24)):
            chroma = np.where(lab[..., channel] > 127,
                              lab[..., channel] - 256, lab[..., channel])
            chroma = np.clip(chroma + offset, -128, 127)
            lab[..., channel] = np.where(chroma < 0, chroma + 256, chroma)
        out = Image.fromarray(np.rint(lab).astype(np.uint8), mode='LAB').convert('RGB')
        del lab

    exposure = adj['exposure']
    if exposure:
        srgb = np.asarray(out, dtype=np.float32) / 255.0
        linear = np.where(srgb <= 0.04045, srgb / 12.92,
                          ((srgb + 0.055) / 1.055) ** 2.4)
        linear *= 2.0 ** exposure
        np.clip(linear, 0.0, 1.0, out=linear)
        srgb = np.where(linear <= 0.0031308, linear * 12.92,
                        1.055 * (linear ** (1.0 / 2.4)) - 0.055)
        out = Image.fromarray(np.rint(np.clip(srgb, 0.0, 1.0) * 255.0).astype(np.uint8), mode='RGB')
        del srgb, linear

    tonal_keys = ('contrast', 'highlights', 'shadows', 'whites', 'blacks', 'midtones')
    if any(adj[key] for key in tonal_keys):
        lab = np.array(out.convert('LAB'), dtype=np.float32)
        lum = lab[..., 0] / 255.0

        contrast_factor = 2.0 ** (adj['contrast'] / 100.0)
        lum = 0.5 + (lum - 0.5) * contrast_factor
        np.clip(lum, 0.0, 1.0, out=lum)

        def _smoothstep(edge0, edge1, values):
            t = np.clip((values - edge0) / (edge1 - edge0), 0.0, 1.0)
            return t * t * (3.0 - 2.0 * t)

        black_w = 1.0 - _smoothstep(0.0, 0.25, lum)
        shadow_w = 1.0 - _smoothstep(0.15, 0.55, lum)
        midtone_w = np.clip(1.0 - np.abs(lum - 0.5) / 0.3, 0.0, 1.0)
        highlight_w = _smoothstep(0.45, 0.85, lum)
        white_w = _smoothstep(0.75, 1.0, lum)
        delta = 0.25 * (
            (adj['blacks'] / 100.0) * black_w
            + (adj['shadows'] / 100.0) * shadow_w
            + (adj['midtones'] / 100.0) * midtone_w
            + (adj['highlights'] / 100.0) * highlight_w
            + (adj['whites'] / 100.0) * white_w
        )
        lab[..., 0] = np.clip(lum + delta, 0.0, 1.0) * 255.0
        out = Image.fromarray(np.rint(lab).astype(np.uint8), mode='LAB').convert('RGB')
        del lab, lum, delta, black_w, shadow_w, midtone_w, highlight_w, white_w

    saturation = adj['saturation']
    if saturation:
        rgb = np.asarray(out, dtype=np.float32) / 255.0
        luma = (rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722)[..., None]
        rgb = luma + (1.0 + saturation / 100.0) * (rgb - luma)
        out = Image.fromarray(np.rint(np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8), mode='RGB')
        del rgb, luma

    return out


def match_color_region(src_img, ref_img, rect, strength=1.0, feather=0.05,
                       adjustments=None, adjustment_mode='relative',
                       return_auto_adjustments=False):
    """区域化 Reinhard + 颜色相似度掩膜：只对归一化矩形 rect=(x,y,w,h)∈[0,1] 内
    「像地板」的像素做 LAB 色彩迁移，边缘线性羽化回原图。

    「手动校色」引擎：以地板小样为 ref，把成图中框选的地板区域拉回小样色彩。
    两层保护，防止选区内的非地板物体（绿植/家具/地毯）被统计外推成极端色：
    1. robust 统计——先按全选区估 LAB 均值/方差，剔除离群像素后重估，
       迁移统计只来自「地板本体」像素，不被绿植等离群色污染；
    2. 相似度掩膜——每像素到地板统计的归一化距离 d，d≤D0 全变换、d≥D1 不变换、
       中间线性过渡；离群物体自然豁免。
    feather 按 src 短边比例定义（非固定像素），保证预览(降采样)与全分辨率提交视觉一致。

    Args:
        src_img: 成图 PIL Image（任意模式，内部转 RGB）
        ref_img: 颜色参照 PIL Image（任意模式，内部转 RGB）
        rect: (x, y, w, h) 归一化选区
        strength: 0.0~1.0 迁移强度（折进 mask，0=恒等返回原图副本）
        feather: 羽化宽度 / src 短边，0=硬边
        adjustments: 高级微调参数
        adjustment_mode: relative=自动结果后微调（兼容旧调用）；auto=仅自动；
                         manual=以 Gemini 原图为零点应用绝对参数
        return_auto_adjustments: 同时返回相对原图估算的自动滑杆基准
    Returns:
        RGB PIL Image（始终新对象，不改 src_img）
    """
    import numpy as np
    src = src_img.convert('RGB')
    W, H = src.size
    x, y, w, h = rect
    # 归一化 → 像素坐标，钳到图内
    x0 = max(0, min(W - 1, int(round(x * W))))
    y0 = max(0, min(H - 1, int(round(y * H))))
    x1 = max(x0, min(W, int(round((x + w) * W))))
    y1 = max(y0, min(H, int(round((y + h) * H))))
    # 无效/过小选区或零强度 → 恒等（返回副本，调用方可放心持有）
    if (adjustment_mode != 'manual' and strength <= 0) or \
            (x1 - x0) < max(2, int(0.02 * W)) or (y1 - y0) < max(2, int(0.02 * H)):
        if return_auto_adjustments:
            return src, dict(_COLOR_ADJUSTMENT_DEFAULTS)
        return src

    feather_px = max(0, int(round(feather * min(W, H))))
    # 外扩羽化带得 crop 框（羽化发生在选区外侧，选区内部保持全强度）
    bx0, by0 = max(0, x0 - feather_px), max(0, y0 - feather_px)
    bx1, by1 = min(W, x1 + feather_px), min(H, y1 + feather_px)
    crop = src.crop((bx0, by0, bx1, by1))

    s = np.array(crop.convert('LAB'), dtype=np.float32)      # (ch, cw, 3)
    r = np.asarray(ref_img.convert('RGB').convert('LAB'), dtype=np.uint8)

    # ── robust 地板统计：全选区初估 → 剔离群 → 重估 ──
    _SIGMA_FLOOR = 2.0     # 方差下限：均匀纹理时避免除零/距离爆炸
    flat = s.reshape(-1, 3)
    mu = flat.mean(axis=0)
    sd = np.maximum(flat.std(axis=0), _SIGMA_FLOOR)
    d = np.sqrt((((flat - mu) / sd) ** 2).mean(axis=1))      # 每像素归一化距离
    inlier = d <= 2.5
    if inlier.sum() >= max(64, int(0.05 * flat.shape[0])):   # 剩得太少就退回全量统计
        mu = flat[inlier].mean(axis=0)
        sd = np.maximum(flat[inlier].std(axis=0), _SIGMA_FLOOR)
        d = np.sqrt((((flat - mu) / sd) ** 2).mean(axis=1))

    # ── 相似度权重：d≤D0 全变换，d≥D1 不变换，线性过渡 ──
    _D0, _D1 = 2.0, 3.2
    w_sim = np.clip((_D1 - d) / (_D1 - _D0), 0.0, 1.0).reshape(s.shape[0], s.shape[1])

    # ── Reinhard 迁移：inlier(地板本体)统计 → ref 全图统计（逐通道均值/方差）──
    # 方差比钳制：校色的目标是对齐整体色调(均值)，不是移植小样的纹理对比度。
    # 不钳制时，地板 a/b 通道方差窄、小样方差宽 → 比值放大把细微色彩波动放大成可见斑块。
    _RATIO_MIN, _RATIO_MAX = 0.7, 1.3
    r_flat = r.reshape(-1, 3)
    r_mean_all = r_flat.mean(axis=0)
    r_std_all = r_flat.std(axis=0)
    r_profile_flat = r_flat.astype(np.float32)

    # 高级滑杆以 Gemini 原图为零点，a/b 必须按 Pillow 的有符号 LAB 编码统计。
    profile_src = flat[inlier] if inlier.sum() >= max(64, int(0.05 * flat.shape[0])) else flat
    src_profile_mean = np.array([
        profile_src[:, 0].mean(),
        np.where(profile_src[:, 1] > 127, profile_src[:, 1] - 256, profile_src[:, 1]).mean(),
        np.where(profile_src[:, 2] > 127, profile_src[:, 2] - 256, profile_src[:, 2]).mean(),
    ], dtype=np.float32)
    src_profile_std = np.array([
        profile_src[:, 0].std(),
        np.where(profile_src[:, 1] > 127, profile_src[:, 1] - 256, profile_src[:, 1]).std(),
        np.where(profile_src[:, 2] > 127, profile_src[:, 2] - 256, profile_src[:, 2]).std(),
    ], dtype=np.float32)
    ref_profile_mean = np.array([
        r_profile_flat[:, 0].mean(),
        np.where(r_profile_flat[:, 1] > 127, r_profile_flat[:, 1] - 256, r_profile_flat[:, 1]).mean(),
        np.where(r_profile_flat[:, 2] > 127, r_profile_flat[:, 2] - 256, r_profile_flat[:, 2]).mean(),
    ], dtype=np.float32)
    ref_profile_std = np.array([
        r_profile_flat[:, 0].std(),
        np.where(r_profile_flat[:, 1] > 127, r_profile_flat[:, 1] - 256, r_profile_flat[:, 1]).std(),
        np.where(r_profile_flat[:, 2] > 127, r_profile_flat[:, 2] - 256, r_profile_flat[:, 2]).std(),
    ], dtype=np.float32)
    auto_adjustments = _estimate_auto_color_adjustments(
        src_profile_mean, src_profile_std, ref_profile_mean, ref_profile_std)
    for c in range(3):
        r_mean, r_std = float(r_mean_all[c]), float(r_std_all[c])
        s_mean, s_std = float(mu[c]), float(sd[c])
        ratio = 1.0 if s_std < 1e-5 else min(max(r_std / s_std, _RATIO_MIN), _RATIO_MAX)
        s[..., c] = (s[..., c] - s_mean) * ratio + r_mean
    np.clip(s, 0, 255, out=s)
    auto_transferred = Image.fromarray(s.astype(np.uint8), mode='LAB').convert('RGB')
    del s, r, flat, d

    normalized_adjustments = _normalize_color_adjustments(adjustments)
    if adjustment_mode == 'manual':
        transferred = apply_color_adjustments(crop, normalized_adjustments)
        mask_strength = 1.0
    else:
        transferred = auto_transferred
        if adjustment_mode == 'relative' and any(normalized_adjustments.values()):
            transferred = apply_color_adjustments(transferred, normalized_adjustments)
        mask_strength = float(strength)

    ch, cw = by1 - by0, bx1 - bx0
    # 两条 1-D 线性 ramp（边缘 0→内部 1，跨 feather_px）广播取 min → 羽化 mask，仅 crop 大小
    def _ramp(n, lo_pad, hi_pad):
        ramp = np.ones(n, dtype=np.float32)
        if lo_pad > 0:
            ramp[:lo_pad] = np.linspace(0.0, 1.0, lo_pad, endpoint=False, dtype=np.float32)
        if hi_pad > 0:
            ramp[n - hi_pad:] = np.linspace(0.0, 1.0, hi_pad, endpoint=False, dtype=np.float32)[::-1]
        return ramp

    ry = _ramp(ch, y0 - by0, by1 - y1)
    rx = _ramp(cw, x0 - bx0, bx1 - x1)
    mask_f = np.minimum(ry[:, None], rx[None, :]) * w_sim
    mask = (mask_f * (255.0 * mask_strength)).astype(np.uint8)
    del mask_f, w_sim
    out = src.copy()
    out.paste(transferred, (bx0, by0), Image.fromarray(mask, mode='L'))
    if return_auto_adjustments:
        return out, auto_adjustments
    return out


def _global_color_profile(src_img, ref_img, rect):
    """Estimate a robust floor-to-swatch LAB transform from the analysis rect."""
    import numpy as np

    src = src_img.convert('RGB')
    width, height = src.size
    x, y, w, h = rect
    x0 = max(0, min(width - 1, int(round(x * width))))
    y0 = max(0, min(height - 1, int(round(y * height))))
    x1 = max(x0, min(width, int(round((x + w) * width))))
    y1 = max(y0, min(height, int(round((y + h) * height))))
    if (x1 - x0) < max(2, int(0.02 * width)) or (y1 - y0) < max(2, int(0.02 * height)):
        return None, dict(_COLOR_ADJUSTMENT_DEFAULTS)

    # Statistics do not need every 4K pixel.  Bounding both inputs keeps the
    # selected-floor and reference working sets predictable on customer PCs.
    sample = src.crop((x0, y0, x1, y1))
    sample.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    reference = ref_img.convert('RGB').copy()
    reference.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    source_lab = _signed_lab_array(sample).reshape(-1, 3)
    ref_lab = _signed_lab_array(reference).reshape(-1, 3)

    center = source_lab.mean(axis=0)
    spread = np.maximum(source_lab.std(axis=0), (2.0, 1.5, 1.5))
    distance = np.sqrt((((source_lab - center) / spread) ** 2).mean(axis=1))
    inliers = distance <= 2.5
    if inliers.sum() >= max(64, int(0.05 * len(source_lab))):
        source_floor = source_lab[inliers]
    else:
        source_floor = source_lab

    source_mean = source_floor.mean(axis=0)
    source_std = np.maximum(source_floor.std(axis=0), (2.0, 1.5, 1.5))
    ref_mean = ref_lab.mean(axis=0)
    ref_std = ref_lab.std(axis=0)
    ratio = np.clip(ref_std / source_std, 0.7, 1.3)
    auto_adjustments = _estimate_auto_color_adjustments(
        source_mean, source_std, ref_mean, ref_std)
    return {
        'source_mean': source_mean.astype(np.float32),
        'ratio': ratio.astype(np.float32),
        'ref_mean': ref_mean.astype(np.float32),
    }, auto_adjustments


def apply_color_adjustments_striped(img, adjustments=None, strip_rows=256):
    """Apply pointwise editor controls to the full image with bounded memory."""
    src = img.convert('RGB')
    normalized = _normalize_color_adjustments(adjustments)
    if not any(normalized.values()):
        return src.copy()
    rows = max(1, int(strip_rows))
    if src.height <= rows:
        return apply_color_adjustments(src, normalized)
    out = Image.new('RGB', src.size)
    for y0 in range(0, src.height, rows):
        y1 = min(src.height, y0 + rows)
        out.paste(apply_color_adjustments(src.crop((0, y0, src.width, y1)), normalized), (0, y0))
    return out


def match_color_global(src_img, ref_img, rect, strength=1.0, feather=0.05,
                       adjustments=None, adjustment_mode='auto',
                       return_auto_adjustments=False, strip_rows=256):
    """Use the selected floor only for statistics, then adjust the whole image.

    ``feather`` remains in the signature for wire compatibility but is
    intentionally ignored: auto, manual and relative modes all affect every
    pixel.  Processing is striped because all supported controls and the LAB
    transfer are pointwise operations.
    """
    import numpy as np

    del feather
    src = src_img.convert('RGB')
    normalized = _normalize_color_adjustments(adjustments)

    if adjustment_mode == 'manual' and not return_auto_adjustments:
        return apply_color_adjustments_striped(src, normalized, strip_rows)

    profile, auto_adjustments = _global_color_profile(src, ref_img, rect)
    if adjustment_mode == 'manual':
        out = apply_color_adjustments_striped(src, normalized, strip_rows)
        return (out, auto_adjustments) if return_auto_adjustments else out
    if profile is None or strength <= 0:
        out = src.copy()
        return (out, auto_adjustments) if return_auto_adjustments else out

    strength = float(min(max(strength, 0.0), 1.0))
    rows = max(1, int(strip_rows))
    out = Image.new('RGB', src.size)
    for y0 in range(0, src.height, rows):
        y1 = min(src.height, y0 + rows)
        source_strip = src.crop((0, y0, src.width, y1))
        lab = _signed_lab_array(source_strip)
        lab = (lab - profile['source_mean']) * profile['ratio'] + profile['ref_mean']
        lab[..., 0] = np.clip(lab[..., 0], 0, 255)
        lab[..., 1:] = np.clip(lab[..., 1:], -128, 127)
        encoded = lab.copy()
        encoded[..., 1:] = np.where(encoded[..., 1:] < 0, encoded[..., 1:] + 256, encoded[..., 1:])
        transferred = Image.fromarray(np.rint(encoded).astype(np.uint8), mode='LAB').convert('RGB')
        if adjustment_mode == 'relative' and any(normalized.values()):
            transferred = apply_color_adjustments(transferred, normalized)
        if strength < 1.0:
            transferred = Image.blend(source_strip, transferred, strength)
        out.paste(transferred, (0, y0))

    if return_auto_adjustments:
        return out, auto_adjustments
    return out


__all__ = [
    'match_color_to_reference',
    'analyze_color_region',
    'match_color_region',
    'match_color_global',
    'apply_color_adjustments',
    'apply_color_adjustments_striped',
]
