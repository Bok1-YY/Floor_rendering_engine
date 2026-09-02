"""Strict structured review of source-native XY opening evidence."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.goal_loop_v2.build_opening_xy_clean_evidence import validate as validate_evidence
from tools.goal_loop_v2.fal_op011_glazed_review import MODEL

ENDPOINT = "https://fal.run/openrouter/router/openai/v1/chat/completions"
FIELDS = (
    "opening_id",
    "segment_on_visible_opening",
    "visible_opening_endpoints",
    "continuous_wall_across_segment",
    "door_leaf_or_swing_visible",
    "glazed_interface_visible",
    "xy_gap_plausible",
    "confidence",
)
YES_NO_UNCLEAR = {"yes", "no", "unclear"}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_hash(value: Any) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def parse(text: str, opening_id: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip().startswith("{") or not text.strip().endswith("}"):
        raise ValueError("XY response must be bare JSON")
    value = json.loads(text.strip())
    if set(value) != set(FIELDS) or value.get("opening_id") != opening_id:
        raise ValueError("XY schema/id mismatch")
    if any(value[key] not in YES_NO_UNCLEAR for key in FIELDS[1:7]) or value["confidence"] not in {"high", "medium", "low", None}:
        raise ValueError("XY enum mismatch")
    return value


def _prompt(opening_id: str, local_segment: list[list[float]]) -> str:
    return (
        f"Review ONLY {opening_id}. Image 1 is a navigation locator whose yellow rectangle stays away from the target; "
        "Image 2 is a byte-exact raw crop with no synthetic marks. In Image 2 inspect the source-native pixels at local "
        f"segment coordinates {json.dumps(local_segment)}. Report visible XY evidence only. A door leaf/swing or glazed line "
        "is an observed cue, not permission to infer a final door/window type. Do not infer rooms, source corrections, "
        "adjacency, traversability, dimensions, Z geometry, or construction. Return exactly the requested JSON object."
    )


def execute(config_path: str | Path, evidence_path: str | Path, output_path: str | Path, opening_id: str, model: str = MODEL) -> dict[str, Any]:
    import requests

    evidence_path = Path(evidence_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    validate_evidence(evidence, rebuild=False)
    try:
        row = next(item for item in evidence["openings"] if item["opening_id"] == opening_id)
    except StopIteration as exc:
        raise ValueError(f"opening is not in clean evidence: {opening_id}") from exc
    if row.get("source_pixels_untouched") is not True:
        raise ValueError("opening raw source pixels are not untouched")

    bindings = []
    images = []
    for role in ("locator", "raw_crop"):
        artifact = row["artifacts"][role]
        path = Path(artifact["path"])
        raw = path.read_bytes()
        digest = _sha(raw)
        if digest != artifact["sha256"] or len(raw) != artifact["bytes"]:
            raise ValueError("opening XY review artifact drift")
        bindings.append({"role": role, "filename": path.name, "bytes": len(raw), "sha256": digest})
        images.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(raw).decode("ascii")}})

    selected_model = str(model or MODEL).strip() or MODEL
    prompt = _prompt(opening_id, row["local_segment_px"])
    system_prompt = "Return only the requested strict JSON object. Treat navigation marks as non-source metadata."
    categorical = {"type": "string", "enum": ["yes", "no", "unclear"]}
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "opening_id": {"type": "string", "const": opening_id},
            **{key: categorical for key in FIELDS[1:7]},
            "confidence": {"type": ["string", "null"], "enum": ["high", "medium", "low", None]},
        },
        "required": list(FIELDS),
    }
    body = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [{"type": "text", "text": prompt}, *images]},
        ],
        "response_format": {"type": "json_schema", "json_schema": {"name": "opening_xy_clean_review", "strict": True, "schema": schema}},
        "temperature": 0,
        "max_tokens": 256,
    }
    request_contract = {
        "model": selected_model,
        "prompt_sha256": _sha(prompt.encode("utf-8")),
        "system_sha256": _sha(system_prompt.encode("utf-8")),
        "image_bindings": bindings,
        "local_segment_px": row["local_segment_px"],
        "response_format": body["response_format"],
        "temperature": 0,
        "max_tokens": 256,
    }
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    proxy = str(config.get("fal_queue_proxy") or config.get("proxy") or "").strip()
    kwargs = {
        "headers": {"Authorization": f"Key {config.get('fal_api_key', '')}"},
        "timeout": 180,
        "verify": bool(config.get("tls_verify", True)),
    }
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}

    response = None
    transport_error = None
    try:
        response = requests.post(ENDPOINT, json=body, **kwargs)
    except (requests.ConnectionError, requests.Timeout) as exc:
        transport_error = f"{type(exc).__name__}: {exc}"

    raw_response = None
    non_json = False
    if response is not None:
        try:
            raw_response = response.json()
        except ValueError:
            non_json = True
            raw_response = {"non_json_sha256": _sha(response.content)}

    validation_error = transport_error
    parsed = None
    status = response.status_code if response is not None else None
    if validation_error is None and status != 200:
        validation_error = f"fal HTTP {status}" + ("; non-JSON provider response" if non_json else "")
    elif validation_error is None and non_json:
        validation_error = "non-JSON provider response"
    elif validation_error is None:
        try:
            parsed = parse(raw_response["choices"][0]["message"]["content"], opening_id)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            validation_error = str(exc)

    result = {
        "schema": "fal-opening-xy-clean-review-v2",
        "opening_id": opening_id,
        "endpoint": ENDPOINT,
        "model": selected_model,
        "request_contract_sha256": _canonical_hash(request_contract),
        "prompt_sha256": request_contract["prompt_sha256"],
        "system_sha256": request_contract["system_sha256"],
        "evidence_file_sha256": _sha(evidence_path.read_bytes()),
        "evidence_candidate_hash": evidence["candidate_hash"],
        "image_bindings": bindings,
        "local_segment_px": row["local_segment_px"],
        "http_status": status,
        "parsed": parsed,
        "validation_error": validation_error,
        "transport_error": transport_error,
        "usable_advisory": parsed is not None and validation_error is None,
        "usage": raw_response.get("usage") if isinstance(raw_response, dict) else None,
        "raw_response": raw_response,
        "raw_response_sha256": _canonical_hash(raw_response) if raw_response is not None else None,
        "cut_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--opening-id", required=True)
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args(argv)
    result = execute(args.config, args.evidence, args.output, args.opening_id, args.model)
    print(json.dumps({"opening_id": result["opening_id"], "model": result["model"], "http_status": result["http_status"], "usable_advisory": result["usable_advisory"], "validation_error": result["validation_error"]}, ensure_ascii=False))
    return 0 if result["usable_advisory"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["FIELDS", "parse", "execute"]
