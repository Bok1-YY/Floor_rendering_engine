"""Quarantine adapter for third-party floor-plan pixel parsers.

The adapter intentionally produces *pixel candidates only*.  It never creates
metric geometry, room topology, semantic confirmations, or build authority.
Third-party output is retained verbatim under ``raw_output`` so a later
reviewer can reproduce the claim without trusting this adapter.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any, Mapping, Sequence

from tools.fastloop_research.contract import canonical_json


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _normalize_items(value: Any, kind: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"parser {kind} output must be a list")
    rows = []
    for index, item in enumerate(value):
        if isinstance(item, Mapping):
            payload = deepcopy(dict(item))
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            payload = {"geometry": deepcopy(list(item))}
        else:
            raise ValueError(f"parser {kind} item {index} is invalid")
        rows.append({"id": f"PARSER-{kind.upper()}-{index:04d}", "kind": kind,
                     "pixel_geometry": payload, "status": "pixel_candidate"})
    return rows


def build_lowtrust_parser_candidate(
    parser_name: str,
    parser_version: str,
    source_image: Mapping[str, Any],
    parser_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap a parser result without letting it cross the source gate.

    ``source_image`` must contain ``path`` and ``file_sha256``; callers should
    compute these from the actual input bytes.  Recognised output keys are
    ``walls``, ``doors``, ``windows`` and ``rooms``. Unknown fields are kept in
    ``raw_output`` but are never interpreted.
    """
    if not parser_name or not parser_version or not isinstance(source_image, Mapping):
        raise ValueError("parser identity and source image binding are required")
    if set(source_image) != {"path", "file_sha256", "width_px", "height_px"}:
        raise ValueError("source image binding keys invalid")
    if len(str(source_image["file_sha256"])) != 64:
        raise ValueError("source image SHA-256 required")
    if not isinstance(parser_output, Mapping):
        raise ValueError("parser output must be an object")
    result = {
        "schema": "lowtrust-floorplan-parser-candidate-v1",
        "parser": {"name": str(parser_name), "version": str(parser_version)},
        "source_image": deepcopy(dict(source_image)),
        "source_image_sha256": str(source_image["file_sha256"]),
        "pixel_candidates": {
            key: _normalize_items(parser_output.get(key), key)
            for key in ("walls", "doors", "windows", "rooms")
        },
        "raw_output": deepcopy(dict(parser_output)),
        "limitations": {
            "metric_scale": False, "wall_atom_ids": False,
            "opening_semantics": False, "room_ids": False,
            "adjacency": False, "provenance_to_source_contract": False,
            "z_height": False, "build": False,
        },
        "status": "quarantined_pixel_candidate",
        "semantic_promotion": False,
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _hash({k: v for k, v in result.items() if k != "candidate_hash"})
    return validate_lowtrust_parser_candidate(result)


def validate_lowtrust_parser_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    required = {"schema", "parser", "source_image", "source_image_sha256",
                "pixel_candidates", "raw_output", "limitations", "status",
                "semantic_promotion", "build_authorized", "ready", "candidate_hash"}
    if not isinstance(candidate, Mapping) or set(candidate) != required:
        raise ValueError("lowtrust candidate keys invalid")
    if candidate["schema"] != "lowtrust-floorplan-parser-candidate-v1":
        raise ValueError("lowtrust schema invalid")
    if candidate["status"] != "quarantined_pixel_candidate":
        raise ValueError("lowtrust status invalid")
    if any(candidate[key] is not False for key in ("semantic_promotion", "build_authorized", "ready")):
        raise ValueError("lowtrust candidate promoted")
    expected_limits = {"metric_scale": False, "wall_atom_ids": False,
                       "opening_semantics": False, "room_ids": False,
                       "adjacency": False, "provenance_to_source_contract": False,
                       "z_height": False, "build": False}
    if candidate["limitations"] != expected_limits:
        raise ValueError("lowtrust limitations leak claims")
    if set(candidate["source_image"]) != {"path", "file_sha256", "width_px", "height_px"}:
        raise ValueError("source image binding invalid")
    if candidate["source_image_sha256"] != candidate["source_image"]["file_sha256"]:
        raise ValueError("source image SHA drift")
    if set(candidate["pixel_candidates"]) != {"walls", "doors", "windows", "rooms"}:
        raise ValueError("pixel candidate kinds invalid")
    for kind, rows in candidate["pixel_candidates"].items():
        if not isinstance(rows, list):
            raise ValueError(f"pixel candidate {kind} is not a list")
        for row in rows:
            if set(row) != {"id", "kind", "pixel_geometry", "status"} or row["kind"] != kind or row["status"] != "pixel_candidate":
                raise ValueError("pixel candidate row promoted or malformed")
    expected = _hash({k: v for k, v in candidate.items() if k != "candidate_hash"})
    if candidate["candidate_hash"] != expected:
        raise ValueError("lowtrust candidate hash drift")
    return deepcopy(dict(candidate))


__all__ = ["build_lowtrust_parser_candidate", "validate_lowtrust_parser_candidate"]
