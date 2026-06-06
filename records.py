import os
import json
import time
import io
import base64
import base64 as b64mod
import hashlib
import html
from typing import List, Tuple, Optional

from PIL import Image

from .config import (
    BASE_DIR, MAIN_OUTPUT_DIR, CONFIG_FILE,
    logger, _short_text, _load_config, _save_config,
)

def _get_json_path(image_path):
    base_name = os.path.splitext(os.path.basename(image_path))[0]
    target_dir = os.path.join(MAIN_OUTPUT_DIR, base_name)
    os.makedirs(target_dir, exist_ok=True)
    return os.path.join(target_dir, f"{base_name}_记录.json"), base_name, target_dir

def _load_records(json_path):
    try:
        with open(json_path, 'r', encoding='utf-8') as f: return json.load(f)
    except Exception as e:
        if json_path and os.path.exists(json_path):
            logger.error(f"记录读取失败: {json_path} / {e}")
        return []

def _save_records(json_path, records):
    try:
        with open(json_path, 'w', encoding='utf-8') as f: json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"记录保存失败: {json_path} / {e}")
        raise

def _delete_record(json_path, record_id):
    """删除 JSON 文件中指定 ID 的记录"""
    records = _load_records(json_path)
    new_records = [r for r in records if r.get('id') != record_id]
    if len(new_records) == len(records):
        logger.warning(f"[记录] 删除失败，未找到记录 json={json_path}, record={record_id}")
        return False
    _save_records(json_path, new_records)
    logger.info(f"[记录] 已删除记录 json={json_path}, record={record_id}")
    return True

def _delete_result_image(json_path, record_id, result_index):
    """删除记录中指定索引的效果图"""
    records = _load_records(json_path)
    for r in records:
        if r.get('id') == record_id:
            results = r.get('results', [])
            if 0 <= result_index < len(results):
                results.pop(result_index)
                _save_records(json_path, records)
                logger.info(f"[记录] 已删除效果图 json={json_path}, record={record_id}, index={result_index}")
                return True
    logger.warning(f"[记录] 删除效果图失败 json={json_path}, record={record_id}, index={result_index}")
    return False

def _img_to_b64(img_or_path, max_width: Optional[int] = None) -> str:
    try:
        img = Image.open(img_or_path) if isinstance(img_or_path, str) else img_or_path.copy()
        if max_width and img.width > max_width: img = img.resize((max_width, int(img.height * max_width / img.width)), Image.Resampling.LANCZOS)
        if img.mode != 'RGB': img = img.convert('RGB')
        buf = io.BytesIO(); img.save(buf, format='JPEG', quality=85)
        return b64mod.b64encode(buf.getvalue()).decode('utf-8')
    except Exception as e:
        logger.error(f"图片转 base64 失败: {img_or_path} / {e}")
        return ''

def _b64_to_pil(b64_str):
    if not b64_str: return None
    try: return Image.open(io.BytesIO(b64mod.b64decode(b64_str)))
    except Exception as e:
        logger.error(f"base64 转图片失败: {e}")
        return None

def scan_json_files():
    results = []
    if not os.path.exists(MAIN_OUTPUT_DIR): return results
    for root, dirs, files in os.walk(MAIN_OUTPUT_DIR):
        for f in files:
            if f.endswith('_记录.json'): results.append(os.path.join(root, f))
    return sorted(results, reverse=True)

def get_record_labels(json_path):
    records = _load_records(json_path)
    choices = []
    for r in records:
        label = (f"{r.get('timestamp','')} | "
                 f"{r.get('workflow_mode','').split(' ')[0]} | "
                 f"{r.get('room_type','')}")
        choices.append((label, r.get('id', '')))
    return choices

def export_html_from_json(json_path):
    records = _load_records(json_path)
    if not records: return "❌ 没有找到记录"
    html_path = json_path.replace('_记录.json', '_导出.html')
    entries = []
    for r in records:
        sample_tag = (f'<img src="data:image/jpeg;base64,{r["sample_image_b64"]}" '
                      f'style="width:180px;border-radius:6px;margin-bottom:10px;display:block;" />'
                      ) if r.get('sample_image_b64') else ''
        results_html = ''
        for i, res in enumerate(r.get('results', [])):
            cmt = (f'<div style="margin-top:8px;padding:8px 12px;background:#fff8f0;border-left:3px solid #e8874a;border-radius:4px;">'
                   f'<p style="margin:0;font-size:0.82em;color:#7a4010;">💬 备注：{html.escape(res.get("comment",""))}</p></div>'
                   ) if res.get('comment') else ''
            results_html += (f'<div style="margin-top:12px;padding:12px;background:#fdf8f4;border-radius:8px;">'
                             f'<p style="margin:0 0 8px 0;font-size:0.8em;color:#b07040;">📸 效果图 {i+1} — {res.get("result_timestamp","")}</p>'
                             f'<img src="data:image/jpeg;base64,{res.get("result_image_b64","")}" style="max-width:100%;border-radius:6px;" />'
                             f'{cmt}</div>')
        entries.append(f'<div style="padding:20px;border-bottom:3px solid #e8874a;margin-bottom:10px;">'
                       f'<p style="margin:0 0 8px 0;font-size:0.85em;color:#555;"><strong>🕒 {r.get("timestamp","")}</strong>'
                       f' &nbsp;|&nbsp; {r.get("workflow_mode","")} &nbsp;|&nbsp; {r.get("room_type","")}</p>'
                       f'{sample_tag}'
                       f'<pre style="white-space:pre-wrap;font-size:0.82em;background:#f8f8f8;padding:12px;border-radius:6px;">'
                       f'{html.escape(r.get("params_summary","") or r.get("prompt_en",""))}</pre>'
                       f'{results_html}</div>')
    full_html = ('<!DOCTYPE html><html><head><meta charset="utf-8"><title>地板效果图记录</title>'
                 '<style>body{font-family:sans-serif;max-width:960px;margin:0 auto;padding:20px;}</style>'
                 f'</head><body>{"".join(entries)}</body></html>')
    with open(html_path, 'w', encoding='utf-8') as f: f.write(full_html)
    return f"✅ 已导出：{os.path.basename(html_path)}"

def append_result_to_log(img1_path, img2_path, json_path, record_id, comment1="", comment2=""):
    if not img1_path and not img2_path: return "⚠️ 请至少上传一张效果图"
    if not json_path or not record_id: return "⚠️ 请先加载一条记录"
    try:
        records = _load_records(json_path)
        for r in records:
            if r.get('id') == record_id:
                written = []; ts = time.strftime("%Y-%m-%d %H:%M:%S")
                for img_path, comment, label in [(img1_path, comment1, "Banana2"), (img2_path, comment2, "Pro")]:
                    if img_path:
                        r.setdefault('results', []).append({
                            'result_timestamp': ts,
                            'result_image_b64': _img_to_b64(img_path, max_width=1000),
                            'comment': str(comment).strip() if comment else '',
                            'model_label': label
                        })
                        written.append(label)
                _save_records(json_path, records)
                logger.info(
                    f"[记录] 手动追加效果图 json={json_path}, record={record_id}, written={written}, "
                    f"img1={img1_path}, img2={img2_path}"
                )
                return f"✅ 已写入：{' + '.join(written)}"
        logger.error(f"[记录] 手动追加失败，未找到记录 json={json_path}, record={record_id}")
        return "❌ 未找到对应记录"
    except Exception as e:
        logger.exception(f"[记录] 手动追加写入失败 json={json_path}, record={record_id}")
        return f"❌ 写入失败: {e}"

# ── 数据安全层 ────────────────────────────────────────────────
_PROMPT_KEY = b"braag2026floor_engine_v5_xor"

# 密码哈希加载优先级：环境变量 > 配置文件 > 内置默认值（仅开发用）
_DEFAULT_REVEAL_HASH = "455c459b728d459e5acf0373c929afc894ddb049515cd88cb046945e235e279e"

def _load_reveal_hash() -> str:
    """Load the reveal-password hash from env var, config, or built-in default."""
    import os as _os
    env_hash = _os.environ.get('FLOOR_ENGINE_REVEAL_HASH', '').strip()
    if env_hash:
        return env_hash
    cfg = _load_config()
    cfg_hash = cfg.get('reveal_hash', '').strip()
    if cfg_hash:
        return cfg_hash
    return _DEFAULT_REVEAL_HASH

def _obfuscate(text: str) -> str:
    if not text: return ""
    return base64.b64encode(bytes([b ^ _PROMPT_KEY[i % len(_PROMPT_KEY)] for i, b in enumerate(text.encode("utf-8"))])).decode()

def _deobfuscate(encoded: str) -> str:
    if not encoded: return ""
    try: return bytes([b ^ _PROMPT_KEY[i % len(_PROMPT_KEY)] for i, b in enumerate(base64.b64decode(encoded))]).decode("utf-8")
    except Exception: return ""

def reveal_prompt_fn(json_path, record_id, input_password):
    if not input_password: return "🔒 请输入密码"
    if hashlib.sha256(input_password.strip().encode('utf-8')).hexdigest() != _load_reveal_hash(): return "❌ 密码错误"
    for r in _load_records(json_path):
        if r.get('id') == record_id:
            pe = r.get('_pe', '')
            return _deobfuscate(pe) if pe else r.get('prompt_en', '无提示词数据')
    return "❌ 未找到记录"

# ── API 生成结果落地 & 记录写入（原在 api.py，归入记录层）──────────────
def _api_write_to_record(pil_img, model_key: str, json_path_val: str, record_id_val: str):
    # 用 is not None 而不是 bool(pil_img)——PIL Image 在某些版本布尔求值会异常
    if pil_img is None or not json_path_val or not record_id_val:
        logger.warning(f"_api_write_to_record 参数不全: img={pil_img is not None}, jpath={bool(json_path_val)}, rid={bool(record_id_val)}")
        return
    try:
        records = _load_records(json_path_val)
        matched = False
        for r in records:
            if r.get('id') == record_id_val:
                r.setdefault('results', []).append({
                    'result_timestamp': time.strftime("%Y-%m-%d %H:%M:%S"),
                    # 存原始分辨率 base64（用户要求存 4K，按需）
                    'result_image_b64': _img_to_b64(pil_img, max_width=None),
                    'comment': f'API 自动生成 ({pil_img.width}×{pil_img.height})',
                    'model_label': model_key
                })
                _save_records(json_path_val, records)
                logger.info(f"✅ 已写入记录 {record_id_val} / {model_key}")
                matched = True
                break
        if not matched:
            logger.warning(f"未找到记录 ID {record_id_val}，文件: {json_path_val}")
    except Exception as e:
        logger.error(f"❌ 写入记录失败 ({model_key}): {e}")

def _save_api_result_jpg(pil_img, model_key: str, png_path_val: str) -> str:
    try:
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe_key = model_key.replace(" ", "_")
        raw_base = os.path.basename(png_path_val or "api")
        orig_name = raw_base.replace('_优化图.png', '').replace('.png', '') or "result"
        fname = f"{orig_name}_{safe_key}_{ts}.jpg"
        fpath = os.path.join(MAIN_OUTPUT_DIR, fname)
        img = pil_img.copy()
        if img.mode != 'RGB': img = img.convert('RGB')
        img.save(fpath, format='JPEG', quality=95)
        return fpath
    except Exception as e:
        logger.error(f"API 结果图片保存失败 ({model_key}): {e}")
        return None

def append_edited_result_to_record(json_path, record_id, source_index, pil_img, edit_prompt, model_label):
    if pil_img is None:
        return "❌ 没有可写入的二次修改图片"
    if not json_path or not record_id:
        return "⚠️ 请先加载一条记录"
    try:
        records = _load_records(json_path)
        for r in records:
            if r.get('id') == record_id:
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                comment = (
                    f"二次修改自第 {int(source_index) + 1} 张图 | {model_label}\n"
                    f"修改建议：{str(edit_prompt).strip()}"
                )
                r.setdefault('results', []).append({
                    'result_timestamp': ts,
                    'result_image_b64': _img_to_b64(pil_img, max_width=None),
                    'comment': comment,
                    'model_label': f"{model_label} Edit",
                    'source_result_index': int(source_index),
                    'edit_prompt': str(edit_prompt).strip(),
                })
                _save_records(json_path, records)
                logger.info(
                    f"[二改记录] 已追加 record={record_id}, source_index={source_index}, "
                    f"model={model_label}, image={pil_img.width}x{pil_img.height}, prompt={_short_text(edit_prompt, 300)}"
                )
                return "✅ 二次修改结果已追加到当前记录"
        logger.error(f"[二改记录] 未找到对应记录 json={json_path}, record={record_id}")
        return "❌ 未找到对应记录"
    except Exception as e:
        logger.exception(f"[二改记录] 写入失败 json={json_path}, record={record_id}")
        return f"❌ 写入失败: {e}"


__all__ = [n for n in dir() if not n.startswith('__')]
