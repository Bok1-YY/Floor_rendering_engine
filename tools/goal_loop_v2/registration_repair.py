"""Auditable, fail-closed disposition for a mismatched opening packet.

This module deliberately does not rewrite a source contract.  It records which
evidence packet is stale and which independently authorised geometry remains
the governing candidate until a replacement pixel packet is produced.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def build_registration_repair_manifest(
    *,
    opening_id: str,
    source_document_sha256: str,
    source_structure_hash: str,
    metric_segment_m: Sequence[Sequence[float]],
    pixel_packet_sha256: str,
    pixel_segment: Sequence[Sequence[float]],
    expected_pixel_segment: Sequence[Sequence[float]],
    max_endpoint_error_px: float,
    tolerance_px: float = 1.0,
) -> dict[str, Any]:
    """Create a disposition; reject malformed or deceptively permissive input."""
    if not opening_id or len(metric_segment_m) != 2 or len(pixel_segment) != 2 or len(expected_pixel_segment) != 2:
        raise ValueError("opening and segments must be complete")
    if not source_document_sha256 or not source_structure_hash or not pixel_packet_sha256:
        raise ValueError("provenance hashes are required")
    error = float(max_endpoint_error_px)
    tol = float(tolerance_px)
    if error < 0 or tol <= 0:
        raise ValueError("invalid registration measurements")
    disposition = "pixel_evidence_rejected_stale_or_wrong_frame" if error > tol else "pixel_evidence_consistent_pending_semantic_review"
    return {
        "schema": "registration-repair-disposition-v1",
        "opening_id": opening_id,
        "authority": "independent_registration_reviewer",
        "source_document_sha256": source_document_sha256,
        "source_structure_hash": source_structure_hash,
            "metric_segment_m": [list(p) for p in metric_segment_m],
        "pixel_packet_sha256": pixel_packet_sha256,
        "pixel_segment": [list(p) for p in pixel_segment],
        "expected_pixel_segment": [list(p) for p in expected_pixel_segment],
        "max_endpoint_error_px": error,
        "tolerance_px": tol,
        "disposition": disposition,
        "governing_geometry": "metric_source_contract" if error > tol else "source_registration_contract",
        "source_mutation_authorized": False,
        "semantic_promotion": False,
        "build_authorized": False,
        "ready": False,
    }


def validate_repair_manifest(manifest: Mapping[str, Any]) -> None:
    required = {
        "schema", "opening_id", "authority", "source_document_sha256", "source_structure_hash",
        "metric_segment_m", "pixel_packet_sha256", "pixel_segment", "expected_pixel_segment",
        "max_endpoint_error_px", "tolerance_px", "disposition", "governing_geometry",
        "source_mutation_authorized", "semantic_promotion", "build_authorized", "ready",
    }
    if set(manifest) != required or manifest["schema"] != "registration-repair-disposition-v1":
        raise ValueError("invalid registration repair manifest")
    if manifest["source_mutation_authorized"] or manifest["semantic_promotion"] or manifest["build_authorized"] or manifest["ready"]:
        raise ValueError("repair disposition cannot authorize mutation or build")
    if manifest["max_endpoint_error_px"] <= manifest["tolerance_px"] and manifest["disposition"] != "pixel_evidence_consistent_pending_semantic_review":
        raise ValueError("consistent packet has invalid disposition")
    if manifest["max_endpoint_error_px"] > manifest["tolerance_px"] and manifest["disposition"] != "pixel_evidence_rejected_stale_or_wrong_frame":
        raise ValueError("mismatched packet must be rejected")
