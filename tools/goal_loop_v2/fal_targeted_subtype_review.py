"""Gemini subtype review for a tighter source crop with cue-attribution fields."""
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

from tools.goal_loop_v2.build_targeted_subtype_evidence import ALLOWED_IDS, validate as validate_evidence
from tools.goal_loop_v2.fal_clean_subtype_review import VISUAL_KINDS
from tools.goal_loop_v2.fal_op011_glazed_review import MODEL

ENDPOINT = "https://fal.run/openrouter/router/openai/v1/chat/completions"
FIELDS = (
    "opening_id",
    "visual_kind",
    "wall_break_visible",
    "swing_arc_visible",
    "sliding_track_visible",
    "neighboring_opening_cue_visible",
    "target_swing_cue_attributable_to_target",
    "confidence",
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_hash(value: Any) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8"))


def parse(text: str, opening_id: str) -> dict[str, Any]:
    if opening_id not in ALLOWED_IDS:
        raise ValueError("opening is not admitted to targeted subtype review")
    if not isinstance(text, str) or not text.strip().startswith("{") or not text.strip().endswith("}"):
        raise ValueError("targeted subtype review must be bare JSON")
    value = json.loads(text.strip())
    if set(value) != set(FIELDS) or value.get("opening_id") != opening_id:
        raise ValueError("targeted subtype review schema/id mismatch")
    if value.get("visual_kind") not in VISUAL_KINDS:
        raise ValueError("targeted subtype visual-kind mismatch")
    if any(value.get(key) not in {"yes", "no", "unclear"} for key in FIELDS[2:7]):
        raise ValueError("targeted subtype cue enum mismatch")
    if value.get("confidence") not in {"high", "medium", "low", None}:
        raise ValueError("targeted subtype confidence mismatch")
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
    validate_evidence(
        evidence,
        opening_id,
        evidence["targeted_crop_box_px"],
        out_dir=evidence_path.parent,
    )
    specifications = [
        ("targeted_raw_crop", evidence["artifacts"]["targeted_raw_crop"], False),
        ("locator", evidence["artifacts"]["locator"], True),
    ]
    bindings, images = [], []
    for role, artifact, root_relative in specifications:
        path = ROOT / artifact["path"] if root_relative else evidence_path.parent / artifact["relative_path"]
        raw = path.read_bytes()
        if len(raw) != artifact["bytes"] or _sha(raw) != artifact["sha256"]:
            raise ValueError("targeted subtype image binding drift")
        bindings.append(
            {
                "role": role,
                "semantic_authority": role == "targeted_raw_crop",
                "filename": path.name,
                "bytes": len(raw),
                "sha256": _sha(raw),
            }
        )
        images.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(raw).decode("ascii")}})
    prompt = (
        f"Review ONLY {opening_id}. Image 1 is a tighter pixel-exact canonical-source crop and is the ONLY semantic "
        "authority. It was designed to exclude a neighboring opening cue while retaining the target segment and its "
        "immediate swing/leaf/jamb context. Image 2 is navigation-only; colored locator marks are not semantic. "
        "Classify the visible subtype and cues, then explicitly state whether any neighboring opening cue remains "
        "visible and whether the visible swing cue is attributable to the target segment itself. Do not infer rooms, "
        "pair, vertical/head/sill/Z, traversability, adjacency, source correction, score, BIM or construction. "
        f"opening_id must be exactly {opening_id}. Return only strict JSON."
    )
    system = "Return only strict JSON. Use only the tighter raw crop for visual semantics and keep cue attribution explicit."
    categorical = {"type": "string", "enum": ["yes", "no", "unclear"]}
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "opening_id": {"type": "string", "enum": [opening_id]},
            "visual_kind": {"type": "string", "enum": list(VISUAL_KINDS)},
            **{key: categorical for key in FIELDS[2:7]},
            "confidence": {"type": ["string", "null"], "enum": ["high", "medium", "low", None]},
        },
        "required": list(FIELDS),
    }
    selected_model = str(model or MODEL).strip() or MODEL
    response_format = {"type": "json_schema", "json_schema": {"name": f"{opening_id.lower()}_targeted_subtype", "strict": True, "schema": schema}}
    body = {
        "model": selected_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": [{"type": "text", "text": prompt}, *images]}],
        "response_format": response_format,
        "temperature": 0,
        "max_tokens": 384,
    }
    contract = {
        "opening_id": opening_id,
        "model": selected_model,
        "prompt_sha256": _sha(prompt.encode("utf-8")),
        "system_sha256": _sha(system.encode("utf-8")),
        "image_bindings": bindings,
        "targeted_crop_box_px": evidence["targeted_crop_box_px"],
        "response_format": response_format,
        "temperature": 0,
        "max_tokens": 384,
    }
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    proxy = str(config.get("fal_queue_proxy") or config.get("proxy") or "").strip()
    kwargs = {"headers": {"Authorization": f"Key {config.get('fal_api_key', '')}"}, "timeout": 180, "verify": bool(config.get("tls_verify", True))}
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
        "schema": "fal-targeted-clean-subtype-review-v1",
        "opening_id": opening_id,
        "endpoint": ENDPOINT,
        "model": selected_model,
        "request_contract_sha256": _canonical_hash(contract),
        "prompt_sha256": contract["prompt_sha256"],
        "system_sha256": contract["system_sha256"],
        "targeted_evidence_file_sha256": _sha(evidence_path.read_bytes()),
        "targeted_evidence_candidate_hash": evidence["candidate_hash"],
        "base_evidence_candidate_hash": evidence["base_evidence_candidate_hash"],
        "source_structure_hash": evidence["source_structure_hash"],
        "host_atom_id": evidence["host_atom_id"],
        "segment_m": evidence["segment_m"],
        "targeted_crop_box_px": evidence["targeted_crop_box_px"],
        "image_bindings": bindings,
        "http_status": status,
        "parsed": parsed,
        "validation_error": error,
        "transport_error": transport_error,
        "usable_advisory": parsed is not None and error is None,
        "usage": raw_response.get("usage") if isinstance(raw_response, dict) else None,
        "raw_response": raw_response,
        "raw_response_sha256": _canonical_hash(raw_response) if raw_response is not None else None,
        "targeted_cue_attribution_advisory_only": True,
        **{key: False for key in (
            "source_subtype_confirmation", "effective_void_confirmation", "vertical_parameters_reviewed",
            "traversability_confirmation", "pair_confirmation", "adjacency_confirmation",
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
