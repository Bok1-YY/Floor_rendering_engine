"""Run a strict Gemini review of the full combined registered XY-gap evidence."""
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

from tools.goal_loop_v2.build_combined_gap_registered_evidence import (
    EXPECTED_IDS,
    validate as validate_evidence,
)
from tools.goal_loop_v2.fal_op011_glazed_review import MODEL

ENDPOINT = "https://fal.run/openrouter/router/openai/v1/chat/completions"
TOP_FIELDS = (
    "full_plan_registration_readable",
    "global_wall_alignment_plausible",
    "all_nine_candidate_ids_locatable",
    "unexpected_extra_full_height_gap_visible",
    "combined_xy_visual_result_valid",
    "recommendation",
    "confidence",
    "per_opening",
)
ROW_FIELDS = (
    "opening_id",
    "model_gap_centered_on_visible_source_opening",
    "model_gap_width_matches_source_xy",
    "neighboring_wall_or_junction_obstruction",
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
        raise ValueError("combined registered review must be bare JSON")
    value = json.loads(text.strip())
    if set(value) != set(TOP_FIELDS):
        raise ValueError("combined registered review top-level schema mismatch")
    categorical = (
        "full_plan_registration_readable",
        "global_wall_alignment_plausible",
        "all_nine_candidate_ids_locatable",
        "unexpected_extra_full_height_gap_visible",
        "combined_xy_visual_result_valid",
    )
    if any(value.get(key) not in {"yes", "no", "unclear"} for key in categorical):
        raise ValueError("combined registered review top-level enum mismatch")
    if value.get("recommendation") not in {
        "accept_combined_xy_research",
        "reject_combined_xy_research",
        "unclear",
    }:
        raise ValueError("combined registered review recommendation mismatch")
    if value.get("confidence") not in {"high", "medium", "low", None}:
        raise ValueError("combined registered review confidence mismatch")
    rows = value.get("per_opening")
    if not isinstance(rows, list) or len(rows) != len(EXPECTED_IDS):
        raise ValueError("combined registered review per-opening count mismatch")
    if [row.get("opening_id") for row in rows] != list(EXPECTED_IDS):
        raise ValueError("combined registered review per-opening order/id mismatch")
    for row in rows:
        if set(row) != set(ROW_FIELDS):
            raise ValueError("combined registered review row schema mismatch")
        if any(row.get(key) not in {"yes", "no", "unclear"} for key in ROW_FIELDS[1:]):
            raise ValueError("combined registered review row enum mismatch")
    return value


def _resolve_evidence_image(
    evidence_path: Path,
    artifact: dict[str, Any],
    *,
    root_relative: bool,
) -> Path:
    path = ROOT / artifact["path"] if root_relative else evidence_path.parent / artifact["relative_path"]
    raw = path.read_bytes()
    if len(raw) != artifact["bytes"] or _sha(raw) != artifact["sha256"]:
        raise ValueError("combined registered review image binding drift")
    return path


def execute(
    config_path: str | Path,
    evidence_path: str | Path,
    output_path: str | Path,
    model: str = MODEL,
) -> dict[str, Any]:
    import requests

    evidence_path = Path(evidence_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    validate_evidence(evidence, out_dir=evidence_path.parent, rebuild=False)
    image_specs = [
        ("full_registered_composite", evidence["full_plan"]["composite"], False),
        ("nine_gap_contact_sheet", evidence["contact_sheet"], False),
        ("full_registered_source_clean", evidence["full_plan"]["registered_source"], False),
        ("combined_model_top_clean", evidence["full_plan"]["combined_model_top"], True),
    ]
    bindings = []
    images = []
    for role, artifact, root_relative in image_specs:
        path = _resolve_evidence_image(
            evidence_path,
            artifact,
            root_relative=root_relative,
        )
        raw = path.read_bytes()
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
        "Review only the combined full-height XY-gap research candidate. Image 1 is a full-plan three-pane "
        "registered composite: clean source, clean combined Blender top render, and an ID locator. Image 2 is a "
        "3x3 contact sheet of nine local registered source/model pairs, all rendered from the SAME combined "
        "43-piece Blender wall set. Images 3 and 4 are the clean full-plan source and model panes. Every source/model "
        "pair uses the same metric center, orientation, orthographic scale, and 1200x1200 resolution. Evaluate only: "
        "(a) full-plan registration readability and global wall alignment, (b) whether all nine candidate IDs are "
        "locatable, (c) for each ID whether the combined-model gap is centered on and has the same XY width as the "
        "visible source opening, (d) whether a neighboring wall or junction obstructs that gap, and (e) whether any "
        "unexpected extra full-height gap is visible. A full-height gap is a visualization device only. Do NOT infer "
        "door/window type, head/sill/Z, rooms, room pairs, traversability, adjacency, source correction, BIM truth, "
        "score, or construction validity. Return only strict JSON."
    )
    system_prompt = (
        "Return only the requested JSON object. Treat the registered source, combined model, and locator as separate "
        "evidence roles. Do not convert XY visual agreement into architectural semantics."
    )
    categorical = {"type": "string", "enum": ["yes", "no", "unclear"]}
    row_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "opening_id": {"type": "string", "enum": list(EXPECTED_IDS)},
            "model_gap_centered_on_visible_source_opening": categorical,
            "model_gap_width_matches_source_xy": categorical,
            "neighboring_wall_or_junction_obstruction": categorical,
        },
        "required": list(ROW_FIELDS),
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "full_plan_registration_readable": categorical,
            "global_wall_alignment_plausible": categorical,
            "all_nine_candidate_ids_locatable": categorical,
            "unexpected_extra_full_height_gap_visible": categorical,
            "combined_xy_visual_result_valid": categorical,
            "recommendation": {
                "type": "string",
                "enum": [
                    "accept_combined_xy_research",
                    "reject_combined_xy_research",
                    "unclear",
                ],
            },
            "confidence": {
                "type": ["string", "null"],
                "enum": ["high", "medium", "low", None],
            },
            "per_opening": {
                "type": "array",
                "minItems": len(EXPECTED_IDS),
                "maxItems": len(EXPECTED_IDS),
                "items": row_schema,
            },
        },
        "required": list(TOP_FIELDS),
    }
    selected_model = str(model or MODEL).strip() or MODEL
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "combined_gap_registered_review",
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
        "max_tokens": 1600,
    }
    contract = {
        "model": selected_model,
        "prompt_sha256": _sha(prompt.encode("utf-8")),
        "system_sha256": _sha(system_prompt.encode("utf-8")),
        "image_bindings": bindings,
        "response_format": response_format,
        "temperature": 0,
        "max_tokens": 1600,
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
        "schema": "fal-combined-gap-registered-review-v1",
        "endpoint": ENDPOINT,
        "model": selected_model,
        "request_contract_sha256": _canonical_hash(contract),
        "prompt_sha256": contract["prompt_sha256"],
        "system_sha256": contract["system_sha256"],
        "evidence_file_sha256": _sha(evidence_path.read_bytes()),
        "evidence_candidate_hash": evidence["candidate_hash"],
        "combined_plan_candidate_hash": evidence["plan_candidate_hash"],
        "combined_manifest_file_sha256": evidence["combined_manifest_file_sha256"],
        "image_bindings": bindings,
        "http_status": status,
        "parsed": parsed,
        "validation_error": validation_error,
        "transport_error": transport_error,
        "usable_advisory": parsed is not None and validation_error is None,
        "usage": raw_response.get("usage") if isinstance(raw_response, dict) else None,
        "raw_response": raw_response,
        "raw_response_sha256": _canonical_hash(raw_response) if raw_response is not None else None,
        "source_correction_authorized": False,
        "xy_experiment_confirmation": False,
        "cut_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
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
                "http_status": result["http_status"],
                "usable_advisory": result["usable_advisory"],
                "validation_error": result["validation_error"],
            }
        )
    )
    return 0 if result["usable_advisory"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["TOP_FIELDS", "ROW_FIELDS", "parse", "execute"]
