"""Fail-closed Gemini visual-subtype advisory for the OP002 clean source crop."""
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
    "visual_kind",
    "wall_break_visible",
    "swing_arc_visible",
    "sliding_track_visible",
    "confidence",
)
VISUAL_KINDS = (
    "door",
    "window_or_fixed_glazing",
    "open_passage",
    "wall_gap",
    "unknown",
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
        raise ValueError("OP002 subtype review must be bare JSON")
    value = json.loads(text.strip())
    if set(value) != set(FIELDS) or value.get("opening_id") != "OP002":
        raise ValueError("OP002 subtype review schema/id mismatch")
    if value.get("visual_kind") not in VISUAL_KINDS:
        raise ValueError("OP002 subtype review visual-kind enum mismatch")
    if any(value.get(key) not in {"yes", "no", "unclear"} for key in FIELDS[2:5]):
        raise ValueError("OP002 subtype review cue enum mismatch")
    if value.get("confidence") not in {"high", "medium", "low", None}:
        raise ValueError("OP002 subtype review confidence mismatch")
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
    validate_evidence(evidence, rebuild=False)
    try:
        row = next(item for item in evidence["openings"] if item["opening_id"] == "OP002")
    except StopIteration as exc:
        raise ValueError("OP002 clean evidence is unavailable") from exc
    if (
        row.get("source_pixels_untouched") is not True
        or row.get("authority") != "source_active"
        or row.get("matrix_cuttable") is not True
    ):
        raise ValueError("OP002 clean evidence authority drift")

    bindings = []
    images = []
    for role in ("raw_crop", "locator"):
        artifact = row["artifacts"][role]
        path = Path(artifact["path"])
        raw = path.read_bytes()
        if len(raw) != artifact["bytes"] or _sha(raw) != artifact["sha256"]:
            raise ValueError("OP002 subtype image artifact drift")
        expected_role = "byte_exact_source_crop" if role == "raw_crop" else "locator_navigation_only"
        if artifact.get("role") != expected_role:
            raise ValueError("OP002 subtype image role drift")
        bindings.append(
            {
                "role": role,
                "semantic_authority": role == "raw_crop",
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
        "Review ONLY OP002 as a visual-subtype candidate. Image 1 is the byte-exact clean raw crop and is the ONLY "
        "semantic image authority. Image 2 is a locator for navigation only; its colored box/line/marks must not be "
        "used as subtype evidence. Classify visual_kind only from source-pixel cues visible in image 1. Report whether "
        "a wall break, door swing arc, or sliding-track cue is visibly present. This is a visual advisory, not source "
        "confirmation. Do NOT infer head, sill, Z, rooms, room pairs, traversability, adjacency, effective void, source "
        "correction, score, BIM truth, build authorization, or construction validity. Return only strict JSON."
    )
    system_prompt = (
        "Return only the requested JSON object. Treat raw_crop as the sole semantic authority and locator as "
        "navigation-only. Never promote a visual subtype into architectural truth."
    )
    categorical = {"type": "string", "enum": ["yes", "no", "unclear"]}
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "opening_id": {"type": "string", "const": "OP002"},
            "visual_kind": {"type": "string", "enum": list(VISUAL_KINDS)},
            "wall_break_visible": categorical,
            "swing_arc_visible": categorical,
            "sliding_track_visible": categorical,
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
            "name": "op002_clean_visual_subtype",
            "strict": True,
            "schema": schema,
        },
    }
    body = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}, *images],
            },
        ],
        "response_format": response_format,
        "temperature": 0,
        "max_tokens": 256,
    }
    contract = {
        "model": selected_model,
        "prompt_sha256": _sha(prompt.encode("utf-8")),
        "system_sha256": _sha(system_prompt.encode("utf-8")),
        "image_bindings": bindings,
        "response_format": response_format,
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
        "schema": "fal-op002-clean-subtype-review-v2",
        "opening_id": "OP002",
        "endpoint": ENDPOINT,
        "model": selected_model,
        "request_contract_sha256": _canonical_hash(contract),
        "prompt_sha256": contract["prompt_sha256"],
        "system_sha256": contract["system_sha256"],
        "evidence_file_sha256": _sha(evidence_path.read_bytes()),
        "evidence_candidate_hash": evidence["candidate_hash"],
        "source_structure_hash": evidence["source_structure_hash"],
        "host_atom_id": row["host_atom_id"],
        "source_segment_m": row["segment_m"],
        "image_bindings": bindings,
        "http_status": status,
        "parsed": parsed,
        "validation_error": validation_error,
        "transport_error": transport_error,
        "usable_advisory": parsed is not None and validation_error is None,
        "usage": raw_response.get("usage") if isinstance(raw_response, dict) else None,
        "raw_response": raw_response,
        "raw_response_sha256": _canonical_hash(raw_response) if raw_response is not None else None,
        "visual_subtype_candidate_only": True,
        "source_subtype_confirmation": False,
        "effective_void_confirmation": False,
        "traversability_confirmation": False,
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
                "model": result["model"],
                "http_status": result["http_status"],
                "usable_advisory": result["usable_advisory"],
                "validation_error": result["validation_error"],
            }
        )
    )
    return 0 if result["usable_advisory"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["FIELDS", "VISUAL_KINDS", "parse", "execute"]
