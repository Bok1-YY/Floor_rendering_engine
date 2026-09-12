"""Comfyui provider implementation; no dependency on the public API facade."""
import os
import time
import json
import random
import io as _io_mod
import uuid

from PIL import Image

import requests as _req
import urllib3

# 证书校验由 _verify_arg() 按配置决定（默认 tls_verify=true → 校验；显式设 false 才关闭）。
# 这里一次性静音 InsecureRequestWarning：该告警只在 verify=False 时才会出现，开启校验后自然不触发，
# 故无条件 disable 与「仅未校验时静音」等效，不会掩盖开启校验后的任何告警。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from ..config import logger, short_text

from .shared import _notify_stage, _redact_api_key

_COMFY_PLACEHOLDER_IMAGE = "__INPAINT_IMAGE__"

_COMFY_PLACEHOLDER_MASK = "__INPAINT_MASK__"

_COMFY_PLACEHOLDER_PROMPT = "__INPAINT_PROMPT__"

_COMFY_PLACEHOLDER_NEGATIVE = "__INPAINT_NEGATIVE__"

_COMFY_PLACEHOLDER_SEED = "__INPAINT_SEED__"

_COMFY_DEFAULT_WORKFLOW = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "comfy_workflows", "inpaint_default.json")

def _comfy_fill_workflow(node, replacements: dict):
    """深遍历 workflow JSON，替换占位符。字符串值精确等于占位符时用原始类型替换
    （seed 要求 int），否则做子串替换（prompt 可嵌进模板自带的修饰词里）。"""
    if isinstance(node, dict):
        return {k: _comfy_fill_workflow(v, replacements) for k, v in node.items()}
    if isinstance(node, list):
        return [_comfy_fill_workflow(v, replacements) for v in node]
    if isinstance(node, str):
        if node in replacements:
            return replacements[node]
        out = node
        for key, val in replacements.items():
            if key in out:
                out = out.replace(key, str(val))
        return out
    return node

def call_comfyui_inpaint(base_url: str, image, mask, prompt: str, *, negative_prompt: str = "",
                         seed=None, workflow_path: str = "", timeout: int = 600,
                         on_stage=None, should_cancel=None):
    """经外部 ComfyUI 实例做 inpaint：上传图/mask → 注入 workflow → /prompt → 轮询 /history。

    返回 (PIL, error, seed)。base_url 是可信内网地址(ComfyUI 无鉴权)；
    session.trust_env=False 防内网请求被系统代理劫持（同 Fal 队列的处理）。
    """
    def _stage(txt):
        _notify_stage(on_stage, txt)

    base = (base_url or "").strip().rstrip("/")
    if not base:
        return None, "未配置 ComfyUI 地址(请在设置里填写，如 http://127.0.0.1:8188)", seed
    session = _req.Session()
    session.trust_env = False

    # 1) 读 workflow 模板（自定义路径优先，空/失败给引导性错误）
    wf_path = (workflow_path or "").strip() or _COMFY_DEFAULT_WORKFLOW
    try:
        with open(wf_path, "r", encoding="utf-8") as f:
            template = json.load(f)
    except FileNotFoundError:
        return None, f"ComfyUI workflow 模板不存在: {wf_path}", seed
    except Exception as ex:
        return None, f"ComfyUI workflow 模板解析失败({wf_path}): {ex}", seed

    # 2) 上传 image / mask（uuid 前缀防撞名；overwrite 兜底）
    _stage("📤 上传到 ComfyUI…")
    uploaded = {}
    for tag, pil_img, pil_mode in (("image", image, "RGB"), ("mask", mask, "L")):
        buf = _io_mod.BytesIO()
        pil_img.convert(pil_mode).save(buf, format="PNG")
        name = f"floor_inpaint_{uuid.uuid4().hex[:12]}_{tag}.png"
        try:
            resp = session.post(
                f"{base}/upload/image",
                files={"image": (name, buf.getvalue(), "image/png")},
                data={"type": "input", "overwrite": "true"},
                timeout=(15, 120),
            )
            if resp.status_code != 200:
                return None, f"ComfyUI 上传失败 HTTP {resp.status_code}: {short_text(resp.text, 300)}", seed
            info = resp.json()
            sub = (info.get("subfolder") or "").strip()
            uploaded[tag] = f"{sub}/{info.get('name', name)}" if sub else info.get("name", name)
        except Exception as ex:
            return None, f"ComfyUI 连接失败({base}): {_redact_api_key(ex)}", seed

    # 3) 注入占位符并提交
    the_seed = int(seed) if seed is not None else random.randint(0, 2**31 - 1)
    workflow = _comfy_fill_workflow(template, {
        _COMFY_PLACEHOLDER_IMAGE: uploaded["image"],
        _COMFY_PLACEHOLDER_MASK: uploaded["mask"],
        _COMFY_PLACEHOLDER_PROMPT: prompt,
        _COMFY_PLACEHOLDER_NEGATIVE: negative_prompt or "",
        _COMFY_PLACEHOLDER_SEED: the_seed,
    })
    client_id = uuid.uuid4().hex
    try:
        resp = session.post(f"{base}/prompt", json={"prompt": workflow, "client_id": client_id},
                            timeout=(15, 60))
    except Exception as ex:
        return None, f"ComfyUI 提交失败: {_redact_api_key(ex)}", the_seed
    if resp.status_code != 200:
        try:
            detail = resp.json()
            node_errors = detail.get("node_errors") or {}
            if node_errors:
                first = next(iter(node_errors.values()))
                errs = first.get("errors") or []
                msg = errs[0].get("message") if errs else str(first)
                return None, f"ComfyUI workflow 校验失败: {short_text(msg, 300)}（常见原因：模板里的 checkpoint 在 ComfyUI 里不存在，请改模板或换自定义 workflow）", the_seed
            detail = detail.get("error") or detail
        except Exception:
            detail = resp.text[:300]
        return None, f"ComfyUI 提交 HTTP {resp.status_code}: {short_text(detail, 300)}", the_seed
    prompt_id = str(resp.json().get("prompt_id") or "")
    if not prompt_id:
        return None, "ComfyUI 未返回 prompt_id", the_seed

    # 4) 轮询 /history（节奏与容错对齐 _call_fal_queue_json：1.5s、连续 5 次网络错误才报错）
    deadline = time.time() + max(60, min(3600, int(timeout)))
    poll_errors = 0
    last_stage = ""
    while time.time() < deadline:
        if should_cancel and should_cancel():
            try:
                session.post(f"{base}/interrupt", timeout=(10, 30))
                session.post(f"{base}/queue", json={"delete": [prompt_id]}, timeout=(10, 30))
            except Exception as ex:
                logger.debug(f"[ComfyUI] 取消请求发送失败(尽力而为): {ex}")
            return None, "已取消", the_seed
        try:
            hist = session.get(f"{base}/history/{prompt_id}", timeout=(15, 45))
            hist.raise_for_status()
            entry = (hist.json() or {}).get(prompt_id)
            poll_errors = 0
            if entry:
                status = entry.get("status") or {}
                if str(status.get("status_str") or "").lower() == "error":
                    msgs = status.get("messages") or []
                    detail = next((m[1].get("exception_message") for m in msgs
                                   if isinstance(m, (list, tuple)) and len(m) > 1
                                   and isinstance(m[1], dict) and m[1].get("exception_message")), "")
                    return None, f"ComfyUI 执行失败: {short_text(detail or status, 300)}", the_seed
                for node_output in (entry.get("outputs") or {}).values():
                    images = node_output.get("images") or []
                    if images:
                        img_info = images[0]
                        view = session.get(f"{base}/view", params={
                            "filename": img_info.get("filename", ""),
                            "subfolder": img_info.get("subfolder", ""),
                            "type": img_info.get("type", "output"),
                        }, timeout=(30, 180))
                        view.raise_for_status()
                        out = Image.open(_io_mod.BytesIO(view.content)); out.load()
                        return out, None, the_seed
                return None, "ComfyUI 执行完成但未产出图片(请检查 workflow 是否含 SaveImage 节点)", the_seed
            # 尚未进 history → 区分排队/推理中
            try:
                queue = session.get(f"{base}/queue", timeout=(10, 30)).json()
                running = any(item[1] == prompt_id for item in (queue.get("queue_running") or []) if len(item) > 1)
                stage = "🎨 ComfyUI 推理中…" if running else "⏳ ComfyUI 排队中…"
                if stage != last_stage:
                    _stage(stage); last_stage = stage
            except Exception:
                pass
        except Exception as ex:
            poll_errors += 1
            if poll_errors >= 5:
                return None, f"ComfyUI 状态轮询网络错误: {_redact_api_key(ex)}", the_seed
        time.sleep(1.5)
    return None, "ComfyUI 等待超时；任务可能仍在执行，可稍后重试或调大超时", the_seed

inpaint = call_comfyui_inpaint
