"""Human annotation protocol for a possible off-frame entrance.

This is an evidence *candidate* only.  A click is not a source fact and never
authorizes semantic promotion or Blender/IFC construction.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "human-entry-annotation-candidate-v1"
_FALSE_POLICY = {
    "semantic_promotion": False,
    "adjacency_confirmation": False,
    "entrance_confirmation": False,
    "build_authorized": False,
    "bim_ifc_authorized": False,
    "ready": False,
}


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canon(value).encode("utf-8")).hexdigest()


def _bind(path: str | Path) -> dict[str, str]:
    p = Path(path).resolve()
    raw = p.read_bytes()
    return {"path": str(p), "sha256": hashlib.sha256(raw).hexdigest(),
            "file_type": p.suffix.lstrip(".") or "unknown"}


def build_annotation(document: Mapping[str, Any], source_document: str | Path,
                     *, image_file: str | Path, clicks: Sequence[Mapping[str, Any]],
                     annotator_note: str = "") -> dict[str, Any]:
    """Build a human input sidecar without mutating ``document``.

    Each click must be in the canonical image frame and contain ``x``, ``y``.
    Optional ``role`` is limited to ``entrance_point``, ``inside_side`` or
    ``outside_side``.  The protocol records intent, not geometry ownership.
    """
    if not clicks:
        raise ValueError("at least one image click is required")
    normalized = []
    allowed = {"entrance_point", "inside_side", "outside_side"}
    for click in clicks:
        if not isinstance(click, Mapping) or not isinstance(click.get("x"), (int, float)) or not isinstance(click.get("y"), (int, float)):
            raise ValueError("each click requires numeric x and y")
        if click.get("role", "entrance_point") not in allowed:
            raise ValueError("unknown human click role")
        normalized.append({"x": click["x"], "y": click["y"],
                           "role": click.get("role", "entrance_point"),
                           "note": str(click.get("note", ""))})
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "sample_id": document.get("sample_id", "1308"),
        "source": {"structure_hash": document.get("structure_hash"),
                   "document": _bind(source_document), "image": _bind(image_file)},
        "annotation_frame": {"space": "canonical_px", "coordinates_are_source_only": True,
                              "clicks": normalized},
        "annotator_note": annotator_note,
        "required_followup": ["crop_and_overlay", "registration_check",
                               "host_wall_check", "interior_space_check",
                               "independent_review"],
        "status": "human_candidate_pending_review",
        "policy": dict(_FALSE_POLICY),
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _hash({k: v for k, v in result.items() if k != "candidate_hash"})
    return result


def validate_annotation(candidate: Mapping[str, Any], document: Mapping[str, Any]) -> dict[str, Any]:
    if candidate.get("schema") != SCHEMA or candidate.get("sample_id") != document.get("sample_id"):
        raise ValueError("human annotation schema/sample mismatch")
    if candidate.get("source", {}).get("structure_hash") != document.get("structure_hash"):
        raise ValueError("human annotation source mismatch")
    if candidate.get("status") != "human_candidate_pending_review":
        raise ValueError("human annotation prematurely promoted")
    if candidate.get("policy") != _FALSE_POLICY:
        raise ValueError("human annotation contains unsafe authorization")
    frame = candidate.get("annotation_frame", {})
    if frame.get("space") != "canonical_px" or frame.get("coordinates_are_source_only") is not True:
        raise ValueError("human annotation must use canonical source pixels")
    clicks = frame.get("clicks")
    if not isinstance(clicks, list) or not clicks:
        raise ValueError("human annotation has no clicks")
    expected = _hash({k: v for k, v in candidate.items() if k != "candidate_hash"})
    if candidate.get("candidate_hash") != expected:
        raise ValueError("human annotation hash mismatch")
    return dict(candidate)


__all__ = ["SCHEMA", "build_annotation", "validate_annotation"]
