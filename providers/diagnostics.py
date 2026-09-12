"""Diagnostics provider implementation; no dependency on the public API facade."""


import requests as _req
import urllib3

# 证书校验由 _verify_arg() 按配置决定（默认 tls_verify=true → 校验；显式设 false 才关闭）。
# 这里一次性静音 InsecureRequestWarning：该告警只在 verify=False 时才会出现，开启校验后自然不触发，
# 故无条件 disable 与「仅未校验时静音」等效，不会掩盖开启校验后的任何告警。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from ..config import GEMINI_MODEL_MAP, LITE_PREVIEW_MODEL, short_text, get_omakase_gemini_model
from ..records import b64_to_pil

from .shared import _redact_api_key, _verify_arg

def infer_aspect_ratio_from_b64(b64_str: str) -> str:
    img = b64_to_pil(b64_str)
    if img is None or not img.width or not img.height:
        return "4:3"
    ratio = img.width / img.height
    candidates = [("1:1", 1.0), ("4:3", 4/3), ("3:4", 3/4), ("16:9", 16/9), ("9:16", 9/16)]
    return min(candidates, key=lambda x: abs(x[1] - ratio))[0]

def _probe_gemini_model_endpoint(api_key: str, model_id: str, *,
                                 proxies=None, verify=True) -> str:
    """零生成费用探测 generateContent 路由权限。

    故意发送空 contents：可用端点会在进入生成前返回“contents 未指定”的 400；
    已下线、对新项目关闭或无权限则会先返回 404/403。绝不生成内容或图片。
    """
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_id}:generateContent"
    )
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    response = None
    last_error = None
    for attempt in range(2):
        try:
            response = _req.post(
                url,
                headers=headers,
                json={"contents": []},
                timeout=12,
                proxies=proxies,
                verify=verify,
            )
            break
        except _req.exceptions.SSLError:
            return "❌ 证书校验失败"
        except (_req.exceptions.Timeout, _req.exceptions.ConnectionError) as exc:
            last_error = exc
            if attempt == 0:
                continue
        except Exception as exc:
            return f"❌ 请求异常：{short_text(_redact_api_key(exc), 100)}"
    if response is None:
        if isinstance(last_error, _req.exceptions.Timeout):
            return "❌ 探测超时（已重试）"
        return f"❌ 网络异常（已重试）：{short_text(_redact_api_key(last_error), 100)}"

    try:
        body = response.json()
        message = str((body.get("error", {}) or {}).get("message", ""))
    except Exception:
        message = str(getattr(response, "text", "") or "")
    low = message.lower()
    if response.status_code == 200:
        return "✅ 端点可用（未生成）"
    if response.status_code == 400 and "content" in low and (
        "not specified" in low or "required" in low or "missing" in low
    ):
        return "✅ 端点可用（未生成）"
    if response.status_code == 404:
        return f"❌ 模型不可用 (HTTP 404)：{short_text(message, 100)}"
    if response.status_code in (401, 403):
        return f"❌ Key 无权限 (HTTP {response.status_code})"
    return f"⚠️ HTTP {response.status_code}：{short_text(message, 100)}"

def test_connection(gemini_api_key: str, fal_api_key: str = "", proxy: str = "") -> str:
    """提交前的轻量连通性与模型端点自检（不生图、零生成费用）。

    - Google 直连(公司线，最易被代理重置)：打 ListModels 接口（不挑模型名、不生成），
      验证「线路 + Key」，再用无效空请求探测各生产模型 generateContent 权限。
    - Fal 线路(用户自费，无免费生成 ping)：仅对 fal.run 做可达性探测，不触发任何计费请求。
    """
    proxies = {"http": proxy.strip(), "https": proxy.strip()} if (proxy and proxy.strip()) else None
    _verify = _verify_arg()
    lines = []

    # ── Google 直连：ListModels 探测（不挑模型名、零成本、不生成）──
    # 用「列模型」而非 ping 具体模型：任何模型退役/改名都不会误报 404，
    # 只验证「线路 + Key」。200=好，401/403=Key 问题，超时/重置=线路真不通。
    gk = (gemini_api_key or "").strip()
    if not gk:
        lines.append("Google 直连：⚠️ 未填 Key")
    else:
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        headers = {"x-goog-api-key": gk}
        google_ok = False
        r = None
        list_error = None
        for attempt in range(2):
            try:
                r = _req.get(
                    url,
                    headers=headers,
                    timeout=15,
                    proxies=proxies,
                    verify=_verify,
                )
                break
            except _req.exceptions.SSLError as exc:
                list_error = exc
                break
            except (_req.exceptions.Timeout, _req.exceptions.ConnectionError) as exc:
                list_error = exc
                if attempt == 0:
                    continue
            except Exception as exc:
                list_error = exc
                break

        if r is not None:
            if r.status_code == 200:
                lines.append("Google 直连：✅ 正常")
                google_ok = True
            else:
                # 读真实报错体区分原因：地区封锁 vs Key 无效 vs 其它（400 不能一律算 Key 问题）
                try:
                    msg = ((r.json().get('error', {}) or {}).get('message', '')) or r.text[:200]
                except Exception:
                    msg = r.text[:200]
                low = msg.lower()
                if 'location is not supported' in low:
                    lines.append("Google 直连：❌ 落地地区不支持（geo-block，非 Key 问题；需换海外节点）")
                elif 'api key not valid' in low or 'api_key_invalid' in low or r.status_code in (401, 403):
                    lines.append(f"Google 直连：❌ Key 无效/无权限 (HTTP {r.status_code})")
                else:
                    lines.append(f"Google 直连：⚠️ HTTP {r.status_code}：{short_text(msg, 120)}")
        elif isinstance(list_error, _req.exceptions.SSLError):
            lines.append(
                "Google 直连：❌ 证书校验失败（网络在拦 HTTPS；设 tls_verify=false 或配 CA）："
                f"{short_text(_redact_api_key(list_error), 100)}"
            )
        elif isinstance(list_error, _req.exceptions.Timeout):
            lines.append("Google 直连：❌ 超时（已重试；代理/网络不通）")
        else:
            lines.append(
                "Google 直连：❌ 不通（已重试；"
                f"{short_text(_redact_api_key(list_error), 160)}）"
            )

        # ── 证书校验状态（默认已开启；被显式关闭时探一下本网络能否安全开回）──
        eff = _verify_arg()
        if eff:
            lines.append("证书校验：✅ 已开启" + ("（自定义 CA）" if isinstance(eff, str) else ""))
        else:
            try:
                _req.get(
                    url, headers=headers, timeout=15, proxies=proxies, verify=True
                )
                lines.append("证书校验：⚠️ 当前关闭，但本网络可开启（建议设 tls_verify=true）")
            except _req.exceptions.SSLError:
                lines.append("证书校验：当前关闭（开启会失败：网络在拦 HTTPS，需装 CA）")
            except Exception:
                lines.append("证书校验：当前关闭（暂无法判定能否开启）")

        if google_ok:
            model_checks = (
                ("Omakase/电影规划", get_omakase_gemini_model()),
                ("B2", GEMINI_MODEL_MAP["Nano Banana 2"]),
                ("Pro", GEMINI_MODEL_MAP["Nano Banana Pro"]),
                ("Lite 预览", LITE_PREVIEW_MODEL),
            )
            for label, model_id in model_checks:
                result = _probe_gemini_model_endpoint(
                    gk, model_id, proxies=proxies, verify=_verify
                )
                lines.append(f"{label} [{model_id}]：{result}")

    # ── Fal 线路：可达性探测（任何 HTTP 响应都算可达；不计费）──
    fk = (fal_api_key or "").strip()
    if not fk:
        lines.append("Fal 线路：⚠️ 未配置 Key")
    else:
        try:
            _req.get("https://fal.run", timeout=10, proxies=proxies, verify=_verify)
            lines.append("Fal 线路：✅ 可达（未做计费校验）")
        except _req.exceptions.Timeout:
            lines.append("Fal 线路：❌ 超时（代理/网络不通）")
        except Exception as e:
            lines.append(f"Fal 线路：❌ 不可达（{_redact_api_key(e)}）")

    return "\n".join(lines)

__all__ = ['infer_aspect_ratio_from_b64', 'test_connection']
