"""Run and strictly validate one advisory Gemini opening review."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

MODEL = "gemini-3.6-flash"
RESULT_SCHEMA = "gemini-single-opening-review-result-v1"
EXPECTED_KEYS = {"opening_id", "review_status", "visual_kind", "swing", "side_a", "side_b", "confidence"}
ENUMS = {
    "review_status": {"agree", "conflict", "indeterminate"},
    "visual_kind": {"door", "window", "portal", "glazed_interface", "wall_gap", "unknown", None},
    "swing": {"in", "out", "left", "right", "none", "unknown", None},
    "side_a": {"known", "unknown", None},
    "side_b": {"known", "unknown", None},
    "confidence": {"high", "medium", "low", None},
}


def build_prompt(opening_id: str) -> str:
    return f'''You are a visual reviewer, not a CAD author. Review ONLY opening {opening_id}.
The images are canonical and north-up; do not rotate, mirror, or invent geometry.
The blue segment and wall atom are supplied facts, not claims to accept.
Use pixels visible in the images only. If a field is not directly visible, return null.
If any image and supplied fact conflict, set review_status to "conflict".
Return ONE minified JSON object, no markdown and no explanation:
{{"opening_id":"{opening_id}","review_status":"agree|conflict|indeterminate","visual_kind":"door|window|portal|glazed_interface|wall_gap|unknown|null","swing":"in|out|left|right|none|unknown|null","side_a":"known|unknown|null","side_b":"known|unknown|null","confidence":"high|medium|low|null"}}
Rules: never name a room; never assert an entrance; never estimate width/height;
never convert a dashed swing arc into a confirmed door; never use labels outside
the crop; one opening only.'''


def parse_review_text(text: str, opening_id: str) -> dict[str, Any]:
    if not isinstance(text, str):
        raise ValueError("Gemini review text is missing")
    stripped = text.strip()
    if stripped.startswith("```") or not (stripped.startswith("{") and stripped.endswith("}")):
        raise ValueError("Gemini review must be one bare JSON object")
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict) or set(parsed) != EXPECTED_KEYS:
        raise ValueError("Gemini review schema mismatch")
    if parsed["opening_id"] != opening_id:
        raise ValueError("Gemini review opening mismatch")
    for key, allowed in ENUMS.items():
        if parsed[key] not in allowed:
            raise ValueError(f"Gemini review enum mismatch: {key}")
    return parsed


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _image_part(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = path.read_bytes()
    return (
        {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(raw).decode("ascii")}},
        {"role": path.stem, "filename": path.name, "bytes": len(raw), "sha256": _sha(raw)},
    )


def _response_text(response: Mapping[str, Any]) -> str:
    candidates = response.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini response has no candidate")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text_parts = [part.get("text", "") for part in parts if isinstance(part, Mapping) and "text" in part]
    if not text_parts:
        raise ValueError("Gemini response has no text")
    return "".join(text_parts)


def execute_review(config_path: Path, opening_id: str, full_path: Path, crop_path: Path, output_path: Path, model: str = MODEL) -> dict[str, Any]:
    import requests

    config = json.loads(config_path.read_text(encoding="utf-8"))
    api_key = config.get("gemini_api_key")
    if not api_key:
        raise ValueError("Gemini API key is not configured")
    proxy = str(config.get("proxy") or "").strip()
    prompt = build_prompt(opening_id)
    full_part, full_binding = _image_part(full_path)
    crop_part, crop_binding = _image_part(crop_path)
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}, full_part, crop_part]}],
        "generationConfig": {"maxOutputTokens": 4096, "responseMimeType": "application/json"},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    verify_tls = bool(config.get("tls_verify", True))
    transient_status = {429, 500, 502, 503, 504}
    response = None
    transport_error = None
    attempts = 0
    for attempts in (1, 2):
        try:
            response = requests.post(url, json=body, proxies=proxies, verify=verify_tls, timeout=120)
            if response.status_code not in transient_status or attempts == 2:
                break
        except (requests.ConnectionError, requests.Timeout) as exc:
            transport_error = f"{type(exc).__name__}: {exc}"
            if attempts == 2:
                break

    raw_response: Any = None
    parsed = None
    validation_error = None
    finish_reason = None
    http_status = response.status_code if response is not None else None
    if response is not None:
        try:
            raw_response = response.json()
        except ValueError:
            raw_response = {"non_json_body_sha256": _sha(response.content), "body_preview": response.text[:500]}
        try:
            if response.status_code != 200:
                raise ValueError(f"Gemini HTTP status {response.status_code}")
            candidate = (raw_response.get("candidates") or [None])[0]
            finish_reason = candidate.get("finishReason") if isinstance(candidate, Mapping) else None
            if finish_reason != "STOP":
                raise ValueError(f"Gemini response incomplete: {finish_reason}")
            parsed = parse_review_text(_response_text(raw_response), opening_id)
        except (ValueError, json.JSONDecodeError) as exc:
            validation_error = str(exc)
    else:
        validation_error = transport_error or "Gemini request failed without a response"

    usable = parsed is not None and validation_error is None
    result = {
        "schema": RESULT_SCHEMA,
        "opening_id": opening_id,
        "model": model,
        "proxy_configured": bool(proxy),
        "attempts": attempts,
        "http_status": http_status,
        "finish_reason": finish_reason,
        "prompt_sha256": _sha(prompt.encode("utf-8")),
        "image_bindings": [full_binding, crop_binding],
        "parsed": parsed,
        "validation_error": validation_error,
        "transport_error": transport_error,
        "usage_metadata": raw_response.get("usageMetadata") if isinstance(raw_response, Mapping) else None,
        "usable_advisory": usable,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "raw_response": raw_response,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--opening", required=True)
    parser.add_argument("--full", type=Path, required=True)
    parser.add_argument("--crop", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args(argv)
    result = execute_review(args.config, args.opening, args.full, args.crop, args.output, args.model)
    print(json.dumps({k: result[k] for k in ("opening_id", "http_status", "finish_reason", "attempts", "usable_advisory", "validation_error")}, ensure_ascii=False))
    return 0 if result["usable_advisory"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
