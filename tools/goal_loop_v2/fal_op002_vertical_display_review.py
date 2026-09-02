"""Run Gemini display-clarity review for the OP002 Layer3B research artifact."""
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

from tools.goal_loop_v2.build_op002_vertical_display_evidence import validate as validate_evidence
from tools.goal_loop_v2.fal_op011_glazed_review import MODEL

ENDPOINT = "https://fal.run/openrouter/router/openai/v1/chat/completions"
FIELDS = (
    "opening_id",
    "intact_wall_baseline_visible",
    "blue_xy_locator_visible",
    "orange_head_assumption_guide_visible",
    "guides_visually_distinct_from_wall",
    "floor_to_head_opening_cut_visible",
    "door_leaf_threshold_or_sill_geometry_visible",
    "display_labels_state_assumptions_and_unknown_sill",
    "display_misleading_as_confirmed_opening",
    "recommendation",
    "confidence",
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_hash(value: Any) -> str:
    return _sha(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    )


def parse(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip().startswith("{") or not text.strip().endswith("}"):
        raise ValueError("OP002 Layer3B display review must be bare JSON")
    value = json.loads(text.strip())
    if set(value) != set(FIELDS) or value.get("opening_id") != "OP002":
        raise ValueError("OP002 Layer3B display review schema/id mismatch")
    if any(value.get(key) not in {"yes", "no", "unclear"} for key in FIELDS[1:9]):
        raise ValueError("OP002 Layer3B display review enum mismatch")
    if value.get("recommendation") not in {
        "accept_layer3b_research_display",
        "reject_layer3b_research_display",
        "unclear",
    }:
        raise ValueError("OP002 Layer3B display recommendation mismatch")
    if value.get("confidence") not in {"high", "medium", "low", None}:
        raise ValueError("OP002 Layer3B display confidence mismatch")
    return value


def execute(
    config_path: str | Path,
    evidence_path: str | Path,
    output_path: str | Path,
    model: str = MODEL,
) -> dict[str, Any]:
    import requests

    evidence_path = Path(evidence_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    validate_evidence(evidence, out_dir=evidence_path.parent)
    specifications = [
        ("labeled_composite", evidence["image_bindings"]["composite"], False),
        ("front_closeup", evidence["image_bindings"]["front_closeup"], True),
        ("top", evidence["image_bindings"]["top"], True),
        ("northeast", evidence["image_bindings"]["northeast"], True),
    ]
    bindings = []
    images = []
    for role, artifact, root_relative in specifications:
        path = ROOT / artifact["path"] if root_relative else evidence_path.parent / artifact["relative_path"]
        raw = path.read_bytes()
        if len(raw) != artifact["bytes"] or _sha(raw) != artifact["sha256"]:
            raise ValueError("OP002 Layer3B display image binding drift")
        bindings.append(
            {
                "role": role,
                "filename": path.name,
                "bytes": len(raw),
                "sha256": _sha(raw),
            }
        )
        images.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
                },
            }
        )
    prompt = (
        "Review ONLY the clarity and non-misleading presentation of the OP002 Layer3B vertical research display. "
        "Image 1 is a labeled four-pane composite; images 2-4 are clean Blender front, top, and northeast renders. "
        "The gray wall baseline must remain intact. The blue line is a nonsemantic XY locator above the wall top. "
        "The orange line is a nonsemantic head-assumption guide shown at the display-only 2.1 m research assumption. "
        "Wall height 2.8 m and head 2.1 m are unverified research assumptions; sill is unknown/not authorized. "
        "Judge only whether these roles are visually clear, whether any actual floor-to-head wall cut, door leaf, "
        "threshold, or sill geometry appears, and whether the labeled display is misleading as a confirmed opening. "
        "Do NOT decide whether 2.1 m or 2.8 m is architecturally correct. Do NOT infer source vertical truth, effective "
        "void, traversability, adjacency, source correction, score, BIM validity, or construction authorization. "
        "Return only strict JSON."
        " The opening_id value must be exactly OP002 with no suffix, prefix, space, or Layer3B text."
    )
    system_prompt = (
        "Return only the requested JSON object. This is a presentation-safety review of a research display, not an "
        "architectural-fact review."
    )
    categorical = {"type": "string", "enum": ["yes", "no", "unclear"]}
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "opening_id": {"type": "string", "enum": ["OP002"]},
            **{key: categorical for key in FIELDS[1:9]},
            "recommendation": {
                "type": "string",
                "enum": [
                    "accept_layer3b_research_display",
                    "reject_layer3b_research_display",
                    "unclear",
                ],
            },
            "confidence": {
                "type": ["string", "null"],
                "enum": ["high", "medium", "low", None],
            },
        },
        "required": list(FIELDS),
    }
    selected_model = str(model or MODEL).strip() or MODEL
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "op002_layer3b_display_review",
            "strict": True,
            "schema": schema,
        },
    }
    body = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [{"type": "text", "text": prompt}, *images]},
        ],
        "response_format": response_format,
        "temperature": 0,
        "max_tokens": 512,
    }
    contract = {
        "model": selected_model,
        "prompt_sha256": _sha(prompt.encode("utf-8")),
        "system_sha256": _sha(system_prompt.encode("utf-8")),
        "image_bindings": bindings,
        "response_format": response_format,
        "temperature": 0,
        "max_tokens": 512,
    }
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    proxy = str(config.get("fal_queue_proxy") or config.get("proxy") or "").strip()
    kwargs = {
        "headers": {"Authorization": f"Key {config.get('fal_api_key', '')}"},
        "timeout": 240,
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
    status = response.status_code if response is not None else None
    validation_error = transport_error
    parsed = None
    if validation_error is None and status != 200:
        validation_error = f"fal HTTP {status}" + (
            "; non-JSON provider response" if non_json else ""
        )
    elif validation_error is None and non_json:
        validation_error = "non-JSON provider response"
    elif validation_error is None:
        try:
            parsed = parse(raw_response["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            validation_error = str(exc)
    result = {
        "schema": "fal-op002-layer3b-display-review-v1",
        "opening_id": "OP002",
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
        "validation_error": validation_error,
        "transport_error": transport_error,
        "usable_advisory": parsed is not None and validation_error is None,
        "usage": raw_response.get("usage") if isinstance(raw_response, dict) else None,
        "raw_response": raw_response,
        "raw_response_sha256": _canonical_hash(raw_response) if raw_response is not None else None,
        "display_clarity_advisory_only": True,
        "source_vertical_confirmation": False,
        "source_subtype_confirmation": False,
        "effective_void_confirmation": False,
        "traversability_confirmation": False,
        "adjacency_confirmation": False,
        "source_correction_authorized": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args(argv)
    result = execute(args.config, args.evidence, args.output, args.model)
    print(
        json.dumps(
            {
                "opening_id": result["opening_id"],
                "http_status": result["http_status"],
                "usable_advisory": result["usable_advisory"],
                "validation_error": result["validation_error"],
            }
        )
    )
    return 0 if result["usable_advisory"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["FIELDS", "parse", "execute"]
