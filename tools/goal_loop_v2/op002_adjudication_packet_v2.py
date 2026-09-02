"""OP002 adjudication packet with a bound compact Gemini result."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.op002_adjudication_packet import build_op002_adjudication_packet

SCHEMA = "op002-adjudication-packet-v2"
RESULT_KEYS = {"opening_id", "geometry_agreement", "observed_kind", "pair_agreement", "traversable", "complete"}


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _binding(path: str | Path) -> tuple[dict[str, str], dict[str, Any]]:
    target = Path(path).resolve()
    raw = target.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    return ({"path": str(target), "file_sha256": hashlib.sha256(raw).hexdigest(), "canonical_sha256": _hash(value)}, value)


def build_op002_adjudication_packet_v2(document: Mapping[str, Any], vertical_evidence_file: str | Path, gemini_result_file: str | Path, *, _skip_validate: bool = False) -> dict[str, Any]:
    doc = validate_v21_document(document)
    base = build_op002_adjudication_packet(doc, vertical_evidence_file)
    gemini_binding, envelope = _binding(gemini_result_file)
    parsed = envelope.get("parsed_result")
    if envelope.get("http_status") != 200 or envelope.get("failure") is not None or not isinstance(parsed, dict) or set(parsed) != RESULT_KEYS:
        raise ValueError("Gemini result is incomplete or invalid")
    if parsed["opening_id"] != "OP002" or parsed["complete"] is not True:
        raise ValueError("Gemini result target/completeness mismatch")
    evidence = json.loads(Path(vertical_evidence_file).read_text(encoding="utf-8"))
    expected_images = sorted([evidence["artifacts"]["full_overlay"]["sha256"], evidence["artifacts"]["crop_overlay"]["sha256"]])
    if sorted(envelope.get("image_sha256", [])) != expected_images:
        raise ValueError("Gemini source image provenance mismatch")
    all_agree = parsed["geometry_agreement"] == "agree" and parsed["pair_agreement"] == "agree" and parsed["traversable"] == "yes"
    blockers = [item for item in base["blockers"] if item != "GEMINI_COMPLETE_REVIEW_MISSING"]
    if not all_agree:
        blockers.append("GEMINI_REVIEW_CONFLICT_OR_INDETERMINATE")
    result = {
        "schema": SCHEMA,
        "source_structure_hash": doc["structure_hash"],
        "opening_id": "OP002",
        "base_packet_hash": base["candidate_hash"],
        "gemini_binding": gemini_binding,
        "gemini_result": deepcopy(parsed),
        "gemini_review_status": "complete_agree" if all_agree else "complete_non_agree",
        "remaining_blockers": blockers,
        "decision": "unresolved_candidate",
        "status": "pending_human_review",
        "pair_confirmation": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _hash({key: value for key, value in result.items() if key != "candidate_hash"})
    return result if _skip_validate else validate_op002_adjudication_packet_v2(doc, vertical_evidence_file, gemini_result_file, result)


def validate_op002_adjudication_packet_v2(document: Mapping[str, Any], vertical_evidence_file: str | Path, gemini_result_file: str | Path, candidate: Mapping[str, Any]) -> dict[str, Any]:
    doc = validate_v21_document(document)
    if candidate.get("schema") != SCHEMA or candidate.get("opening_id") != "OP002":
        raise ValueError("OP002 v2 packet schema/target violation")
    for key in ("pair_confirmation", "semantic_promotion", "build_authorized", "ready"):
        if candidate.get(key) is not False:
            raise ValueError("OP002 v2 packet was promoted")
    expected = build_op002_adjudication_packet_v2(doc, vertical_evidence_file, gemini_result_file, _skip_validate=True)
    if dict(candidate) != expected:
        raise ValueError("OP002 v2 adjudication evidence drift")
    return deepcopy(dict(candidate))


__all__ = ["build_op002_adjudication_packet_v2", "validate_op002_adjudication_packet_v2"]
