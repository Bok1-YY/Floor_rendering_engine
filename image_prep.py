# -*- coding: utf-8 -*-
"""提示词管线的图像预处理 —— 小样 PNG 规范化(ICC→sRGB)与地板色调分析。

纯 PIL/numpy,无 FastAPI/网络依赖;供 prompts(小样落盘)与识色端点消费。
从 prompt_data / prompts 迁入,函数体逐字未动。
"""
import os

from PIL import Image

from .prompt_data import FLOOR_TONES


def _compute_tone_confidence(med_h, med_s, med_v, directionality, is_wood, tone_key):
    scores = []
    scores.append(min(100, int(directionality * 550)) if is_wood else min(100, int((0.22 - min(directionality, 0.22)) * 550)))
    if med_v > 85: scores.append(min(100, int(70 + (med_v - 85) * 4)))
    elif med_v > 62: scores.append(85 if 68 < med_v < 83 else 70)
    elif med_v > 42: scores.append(85 if 48 < med_v < 60 else 68)
    else: scores.append(85 if med_v < 36 else 68)
    if "灰米" in tone_key: scores.append(90 if 9 <= med_s <= 21 else 62)
    elif "暖色" in tone_key or "奶油" in tone_key: scores.append(88 if med_s > 26 else 68)
    elif "冷色" in tone_key or "冷灰" in tone_key: scores.append(85 if (med_h > 155 or med_s < 12) else 68)
    elif "近白" in tone_key or "漂白" in tone_key: scores.append(90 if med_s < 12 else 65)
    else: scores.append(78)
    return int(sum(scores) / len(scores))

def analyze_floor_tone(image_path):
    """分析地板素材图，返回最匹配的 FLOOR_TONES 档位和分析 HTML。"""
    if not image_path: return FLOOR_TONES[0], ""
    try:
        import numpy as np
        img = Image.open(image_path).convert('RGB')
        w, h = img.size
        cx, cy = int(w * 0.2), int(h * 0.2)
        img_small = img.crop((cx, cy, w - cx, h - cy)).resize((160, 160), Image.Resampling.LANCZOS)
        arr = np.array(img_small, dtype=np.float32) / 255.0
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        cmax = np.maximum(np.maximum(r, g), b)
        delta = cmax - np.minimum(np.minimum(r, g), b)
        eps = 1e-7
        v_arr = cmax
        s_arr = np.where(cmax < 0.01, 0.0, delta / (cmax + eps))
        h_arr = np.zeros_like(r)
        m_r = (cmax == r) & (delta > eps); m_g = (cmax == g) & (delta > eps); m_b = (cmax == b) & (delta > eps)
        h_arr[m_r] = (60 * ((g[m_r] - b[m_r]) / (delta[m_r] + eps))) % 360
        h_arr[m_g] = 60 * ((b[m_g] - r[m_g]) / (delta[m_g] + eps)) + 120
        h_arr[m_b] = 60 * ((r[m_b] - g[m_b]) / (delta[m_b] + eps)) + 240
        sat_mask = s_arr > 0.06
        med_h = float(np.median(h_arr[sat_mask])) if sat_mask.sum() > 40 else 32.0
        med_s = float(np.median(s_arr)) * 100
        med_v = float(np.median(v_arr)) * 100
        gray = 0.299 * r + 0.587 * g + 0.114 * b
        p = np.pad(gray, 1, mode='edge')
        sx = (-p[:-2,:-2] - 2*p[1:-1,:-2] - p[2:,:-2] + p[:-2,2:] + 2*p[1:-1,2:] + p[2:,2:])
        sy = (-p[:-2,:-2] - 2*p[:-2,1:-1] - p[:-2,2:] + p[2:,:-2] + 2*p[2:,1:-1] + p[2:,2:])
        mag = np.sqrt(sx**2 + sy**2).flatten()
        ang = (np.degrees(np.arctan2(sy, sx)) % 180).flatten()
        hist, _ = np.histogram(ang, bins=18, range=(0, 180), weights=mag)
        directionality = float(hist.max() / mag.sum()) if mag.sum() > 0 else 0.0
        IS_WOOD = directionality > 0.12
        NEAR_WHITE = med_v > 84 and med_s < 18
        LIGHT = 62 <= med_v <= 85; MID_DEEP = 42 <= med_v < 62; DEEP = med_v < 42
        if med_v > 85 and not NEAR_WHITE: LIGHT = True
        WARM = 12 <= med_h <= 55 and med_s >= 22
        GREIGE = 12 <= med_h <= 55 and 8 <= med_s < 22
        COOL = (med_h > 155 or med_h < 12) or (med_s >= 12 and 110 <= med_h <= 265)
        NEUTRAL = med_s < 8
        if IS_WOOD:
            if NEAR_WHITE or (NEUTRAL and med_v > 82): tone_key = "木纹·近白"
            elif LIGHT and WARM: tone_key = "木纹·暖色浅调"
            elif LIGHT and (GREIGE or NEUTRAL): tone_key = "木纹·中性灰米"
            elif LIGHT and COOL: tone_key = "木纹·冷色浅调"
            elif MID_DEEP and WARM: tone_key = "木纹·暖色中深调"
            elif MID_DEEP and (COOL or GREIGE or NEUTRAL): tone_key = "木纹·冷色浅调"
            elif DEEP and WARM: tone_key = "木纹·暖色深调"
            else: tone_key = "木纹·冷色深调"
        else:
            if NEAR_WHITE or (NEUTRAL and med_v > 82): tone_key = "石纹·近白"
            elif (LIGHT or MID_DEEP) and WARM: tone_key = "石纹·奶油/洞石"
            elif (LIGHT or MID_DEEP) and (GREIGE or NEUTRAL): tone_key = "石纹·暖灰/沙灰"
            elif (LIGHT or MID_DEEP) and COOL: tone_key = "石纹·冷灰/水泥灰"
            else: tone_key = "石纹·深灰/炭灰"
        matched = next((t for t in FLOOR_TONES if tone_key.replace("·近白", "") in t or tone_key in t), FLOOR_TONES[0])
        conf = _compute_tone_confidence(med_h, med_s, med_v, directionality, IS_WOOD, tone_key)
        mat_icon = "🪵 木纹" if IS_WOOD else "🪨 石纹"
        info_html = (f'<div style="margin-top:4px;padding:6px 10px;background:#1a1a12;border-left:3px solid #a0826d;'
                     f'border-radius:0 4px 4px 0;font-size:0.8em;color:#c8a87a;line-height:1.5;">'
                     f'🔍 自动识别：<b>{matched}</b> &nbsp;·&nbsp; 置信度 <b>{conf}%</b><br>'
                     f'{mat_icon} &nbsp; H {med_h:.0f}° · S {med_s:.0f}% · V {med_v:.0f}% · 方向性 {directionality:.2f}<br>'
                     f'<span style="color:#7a6040;font-size:0.88em;">💡 如识别有误可在下方手动修改</span></div>')
        return matched, info_html
    except Exception as e:
        return FLOOR_TONES[0], f'<span style="color:#c0392b;font-size:0.8em;">⚠️ 色调分析失败：{e}</span>'


def convert_to_srgb(img, icc_profile):
    try:
        from PIL import ImageCms; srgb_profile = ImageCms.createProfile('sRGB')
        if icc_profile: return ImageCms.profileToProfile(img, icc_profile, srgb_profile, outputMode='RGB')
        return img
    except Exception: return img
def get_srgb_save_kwargs():
    try:
        from PIL import ImageCms; return {"format": "PNG", "icc_profile": ImageCms.createProfile('sRGB').tobytes()}
    except Exception: return {"format": "PNG"}


def prepare_swatch_png(image_path, png_path, last_image_path):
    """小样 PNG 规范化落盘:换图或 PNG 缺失时做 ICC→sRGB 转换 + 4096 收边并落盘;
    未换图直接秒读已有 PNG。返回 (processed_img, msg_prefix);
    处理失败抛原始异常,错误文案由调用方(save_task_files_html)组装。
    正文逐字迁自 save_task_files_html 的图片预处理段。"""
    if image_path != last_image_path or not os.path.exists(png_path):
        img = Image.open(image_path)
        icc_profile = img.info.get('icc_profile')
        if img.mode in ('RGBA', 'LA'):
            bg = Image.new('RGB', img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3] if img.mode == 'RGBA' else img.split()[1]); img = bg
        elif img.mode == 'P':
            img = img.convert('RGBA')
            bg = Image.new('RGB', img.size, (255, 255, 255)); bg.paste(img, mask=img.split()[3]); img = bg
        elif img.mode not in ('RGB', 'L', 'CMYK'):
            img = img.convert('RGB')
        img = convert_to_srgb(img, icc_profile)
        # 兜底：PNG 不支持 CMYK 等模式，保存前强制转为 RGB（无 ICC 的 CMYK 图会走到这里）
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
        img.save(png_path, **get_srgb_save_kwargs())
        return img, "✅ 新图片已处理并"
    return Image.open(png_path), "⚡ 图片未改变，秒速"
