"""Generic Gemini clarity review for an intact-wall vertical-guide display."""
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

from tools.goal_loop_v2.build_opening_vertical_display_evidence import default_inputs, validate as validate_evidence
from tools.goal_loop_v2.fal_op011_glazed_review import MODEL

ENDPOINT = "https://fal.run/openrouter/router/openai/v1/chat/completions"
FIELDS = (
    "opening_id",
    "intact_wall_baseline_visible",
    "blue_xy_locator_visible",
    "orange_unbound_head_guide_visible",
    "guides_visually_distinct_from_wall",
    "floor_to_head_opening_cut_visible",
    "door_leaf_threshold_or_sill_geometry_visible",
    "labels_state_unbound_head_and_unknown_sill",
    "display_misleading_as_confirmed_opening",
    "recommendation",
    "confidence",
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_hash(value: Any) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8"))


def parse(text: str, opening_id: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip().startswith("{") or not text.strip().endswith("}"):
        raise ValueError("vertical display review must be bare JSON")
    value = json.loads(text.strip())
    if set(value) != set(FIELDS) or value.get("opening_id") != opening_id:
        raise ValueError("vertical display review schema/id mismatch")
    if any(value.get(key) not in {"yes", "no", "unclear"} for key in FIELDS[1:9]):
        raise ValueError("vertical display review enum mismatch")
    if value.get("recommendation") not in {"accept_research_display", "reject_research_display", "unclear"}:
        raise ValueError("vertical display review recommendation mismatch")
    if value.get("confidence") not in {"high", "medium", "low", None}:
        raise ValueError("vertical display review confidence mismatch")
    return value


def execute(
    config_path: str | Path,
    evidence_path: str | Path,
    output_path: str | Path,
    opening_id: str,
    model: str = MODEL,
) -> dict[str, Any]:
    import requests

    evidence_path = Path(evidence_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    validate_evidence(evidence, opening_id, out_dir=evidence_path.parent, **default_inputs(opening_id))
    image_specs = [
        ("labeled_composite", evidence["image_bindings"]["composite"], False),
        ("front_closeup", evidence["image_bindings"]["front_closeup"], True),
        ("top", evidence["image_bindings"]["top"], True),
        ("northeast", evidence["image_bindings"]["northeast"], True),
    ]
    bindings, images = [], []
    for role, artifact, root_relative in image_specs:
        path = ROOT / artifact["path"] if root_relative else evidence_path.parent / artifact["relative_path"]
        raw = path.read_bytes()
        if len(raw) != artifact["bytes"] or _sha(raw) != artifact["sha256"]:
            raise ValueError("vertical display review image binding drift")
        bindings.append({"role": role, "filename": path.name, "bytes": len(raw), "sha256": _sha(raw)})
        images.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(raw).decode("ascii")}})
    prompt = (
        f"Review ONLY {opening_id} research-display clarity. Image 1 is a labeled composite; images 2-4 are clean "
        "front, top, and northeast renders. The gray 35-wall baseline must remain intact. Blue is a nonsemantic XY "
        "locator above the wall. Orange is a 2.1 m UNBOUND research-default guide, explicitly NOT opening geometry. "
        "Wall height 2.8 m is also an unverified research assumption; sill is unknown. Judge whether these roles are "
        "visible and distinct, whether any actual floor-to-head cut/door leaf/threshold/sill appears, and whether the "
        "display misleadingly looks confirmed. Do NOT decide dimensions, subtype, effective void, room pair, "
        "traversability, adjacency, root, source correction, score, BIM or construction validity. opening_id must be "
        f"exactly {opening_id}. Return only strict JSON."
    )
    system = "Return only the requested JSON. This is display-safety review, not architectural-fact review."
    categorical = {"type": "string", "enum": ["yes", "no", "unclear"]}
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "opening_id": {"type": "string", "enum": [opening_id]},
            **{key: categorical for key in FIELDS[1:9]},
            "recommendation": {"type": "string", "enum": ["accept_research_display", "reject_research_display", "unclear"]},
            "confidence": {"type": ["string", "null"], "enum": ["high", "medium", "low", None]},
        },
        "required": list(FIELDS),
    }
    selected_model = str(model or MODEL).strip() or MODEL
    response_format = {"type": "json_schema", "json_schema": {"name": f"{opening_id.lower()}_vertical_display_review", "strict": True, "schema": schema}}
    body = {
        "model": selected_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": [{"type": "text", "text": prompt}, *images]}],
        "response_format": response_format,
        "temperature": 0,
        "max_tokens": 512,
    }
    contract = {
        "opening_id": opening_id,
        "model": selected_model,
        "prompt_sha256": _sha(prompt.encode("utf-8")),
        "system_sha256": _sha(system.encode("utf-8")),
        "image_bindings": bindings,
        "response_format": response_format,
        "temperature": 0,
        "max_tokens": 512,
    }
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    proxy = str(config.get("fal_queue_proxy") or config.get("proxy") or "").strip()
    kwargs = {"headers": {"Authorization": f"Key {config.get('fal_api_key', '')}"}, "timeout": 240, "verify": bool(config.get("tls_verify", True))}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    response, transport_error = None, None
    try:
        response = requests.post(ENDPOINT, json=body, **kwargs)
    except (requests.ConnectionError, requests.Timeout) as exc:
        transport_error = f"{type(exc).__name__}: {exc}"
    raw_response, non_json = None, False
    if response is not None:
        try:
            raw_response = response.json()
        except ValueError:
            non_json = True
            raw_response = {"non_json_sha256": _sha(response.content)}
    status = response.status_code if response is not None else None
    error, parsed = transport_error, None
    if error is None and status != 200:
        error = f"fal HTTP {status}" + ("; non-JSON provider response" if non_json else "")
    elif error is None and non_json:
        error = "non-JSON provider response"
    elif error is None:
        try:
            parsed = parse(raw_response["choices"][0]["message"]["content"], opening_id)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            error = str(exc)
    result = {
        "schema": "fal-opening-vertical-display-review-v1",
        "opening_id": opening_id,
        "endpoint": ENDPOINT,
        "model": selected_model,
        "request_contract_sha256": _canonical_hash(contract),
        "prompt_sha256": contract["prompt_sha256"],
        "system_sha256": contract["system_sha256"],
        "evidence_file_sha256": _sha(evidence_path.read_bytes()),
        "evidence_candidate_hash": evidence["candidate_hash"],
        "display_plan_candidate_hash": evidence["display_plan_candidate_hash"],
        "display_manifest_file_sha256": evidence["display_manifest_file_sha256"],
        "image_bindings": bindings,
        "http_status": status,
        "parsed": parsed,
        "validation_error": error,
        "transport_error": transport_error,
        "usable_advisory": parsed is not None and error is None,
        "usage": raw_response.get("usage") if isinstance(raw_response, dict) else None,
        "raw_response": raw_response,
        "raw_response_sha256": _canonical_hash(raw_response) if raw_response is not None else None,
        "display_clarity_advisory_only": True,
        **{key: False for key in (
            "source_vertical_confirmation", "source_subtype_confirmation", "effective_void_confirmation",
            "traversability_confirmation", "pair_confirmation", "adjacency_confirmation", "root_confirmation",
            "source_correction_authorized", "semantic_promotion", "build_authorized", "ready"
        )},
        "score_effect": "none",
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
    print(json.dumps({"opening_id": result["opening_id"], "http_status": result["http_status"], "usable_advisory": result["usable_advisory"], "validation_error": result["validation_error"]}))
    return 0 if result["usable_advisory"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["FIELDS", "parse", "execute"]
