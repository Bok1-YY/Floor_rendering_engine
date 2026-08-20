from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from Floor_engine_server import records, routes_library, server_helpers
from Floor_engine_server.server_schemas import GeometryAuditReviewRequest
from Floor_engine_server.whole_home_geometry_history import (
    archive_geometry_gold_history,
    build_geometry_audit_payload,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def gold_case(tmp_path: Path) -> Path:
    raw = tmp_path / "raw" / "ifcbench_fzk_house"
    gold = tmp_path / "prepared" / "ifcbench_fzk_house" / "same_source_gold_v1"
    raw.mkdir(parents=True)
    gold.mkdir(parents=True)
    source = raw / "arc.ifc"
    source.write_bytes(b"IFC SOURCE")
    (raw / "snapshot.png").write_bytes(b"PNG SNAPSHOT")
    contents = {
        "input_dimensioned.png": b"PNG PLAN",
        "input_double_line.dxf": b"DXF PLAN",
        "truth_geometry.json": b"{}\n",
        "truth_geometry_manifest.json": b"{\"manifest\":true}\n",
        "truth_gray_model.obj": b"o model\n",
        "truth_gray_preview.png": b"PNG GRAY",
    }
    locks = {}
    for name, content in contents.items():
        path = gold / name
        path.write_bytes(content)
        locks[name] = {"sha256": _sha(path), "size_bytes": len(content)}
    _write_json(gold / "case_manifest.json", {
        "case_id": "ifcbench_fzk_house",
        "source_path": str(source),
        "source_sha256": _sha(source),
        "derivation_version": "ifc-same-source-gold-v1",
        "ifcopenshell_version": "0.8.5",
        "storey": {"ifc_id": 1, "name": "Ground", "elevation_m": 0.0},
        "counts": {"walls": 9, "spaces": 6, "openings": 14},
        "coordinate_contract": {"ifc": "metres-z-up", "engine_manifest": "metres-y-up"},
        "artifacts": locks,
    })
    _write_json(gold / "gold_result.json", {
        "case_id": "ifcbench_fzk_house", "status": "passed",
        "gold_runner_version": "ifc-same-source-gold-v1",
        "source_sha256": _sha(source),
        "derived_dxf_sha256": locks["input_double_line.dxf"]["sha256"],
        "production_parse": {"hard_errors": [], "warning_count": 0, "model_facts_hash": "a" * 64},
        "metrics": {
            "wall_footprint_iou": 1.0, "wall_boundary_p95_m": 0.0,
            "room_footprint_iou": 1.0, "opening_precision": 1.0,
            "opening_recall": 1.0, "opening_center_p95_m": 0.15,
            "opening_width_p95_m": 0.0, "wall_assembly_coverage": 1.0,
        },
        "thresholds": {
            "wall_footprint_iou_min": 0.98, "wall_boundary_p95_m_max": 0.05,
            "room_footprint_iou_min": 0.95, "opening_precision_min": 0.9,
            "opening_recall_min": 0.9, "opening_center_p95_m_max": 0.2,
            "opening_width_p95_m_max": 0.05, "wall_assembly_coverage_min": 1.0,
        },
        "issues": [],
    })
    _write_json(gold / "raster_gold_result.json", {
        "case_id": "ifcbench_fzk_house", "status": "passed",
        "gold_runner_version": "ifc-same-source-gold-v1",
        "source_sha256": locks["input_dimensioned.png"]["sha256"],
        "registration_hash": "b" * 64, "evidence_hash": "c" * 64,
        "metrics": {
            "scale_anchor_count": 2, "scale_disagreement": 0.0,
            "registration_roundtrip_px": 0.0, "wall_centerline_p95_m": 0.0,
            "wall_ink_support_ratio": 1.0, "room_iou": 0.99,
            "opening_precision": 1.0, "opening_recall": 1.0,
        },
        "issues": [],
    })
    return gold


def test_geometry_audit_payload_verifies_both_channels_and_artifacts(gold_case: Path):
    audit = build_geometry_audit_payload(gold_case, level="L1", case_title="FZK House")
    assert audit["status"] == "passed"
    assert audit["integrity"] == {"status": "passed", "checked_count": 11, "failures": []}
    assert len(audit["channels"][0]["metrics"]) == 8
    assert len(audit["channels"][1]["metrics"]) == 8
    assert all(metric["status"] == "passed"
               for channel in audit["channels"] for metric in channel["metrics"])
    assert len(audit["audit_hash"]) == 64


def test_geometry_audit_detects_tampered_derived_artifact(gold_case: Path):
    (gold_case / "input_double_line.dxf").write_bytes(b"tampered and resized")
    audit = build_geometry_audit_payload(gold_case, level="L1")
    assert audit["status"] == "failed"
    assert audit["integrity"]["status"] == "failed"
    assert audit["integrity"]["failures"] == [{
        "artifact_id": "input_double_line.dxf",
        "problems": ["sha256_mismatch", "size_mismatch"],
    }]


def test_archive_is_idempotent_and_record_api_supports_review_and_download(
    gold_case: Path, tmp_path: Path, monkeypatch,
):
    output = tmp_path / "output_files"
    output.mkdir(exist_ok=True)
    monkeypatch.setattr(records, "MAIN_OUTPUT_DIR", str(output))
    monkeypatch.setattr(server_helpers, "MAIN_OUTPUT_DIR", str(output))
    first = archive_geometry_gold_history(
        gold_case, level="L1", case_title="FZK House", output_root=output)
    second = archive_geometry_gold_history(
        gold_case, level="L1", case_title="FZK House", output_root=output)
    assert first["created"] is True
    assert second["created"] is False
    assert first["record_id"] == second["record_id"]
    saved = records.load_records_file(first["json_path"])
    assert len(saved) == 1
    assert saved[0]["immutable_audit"] is True
    assert len(saved[0]["results"]) == 3

    monkeypatch.setattr(routes_library, "scan_json_files", lambda: [first["json_path"]])
    index = routes_library.list_geometry_audits(limit=20)
    assert len(index) == 1
    assert index[0]["file"]["json_path"] == first["json_path"]
    assert index[0]["entry"]["id"] == first["record_id"]
    assert index[0]["entry"]["results"][0]["result_url"]
    assert all("source_path" not in artifact
               for artifact in index[0]["entry"]["geometry_audit"]["artifacts"])

    metric_ids = [
        metric["metric_id"]
        for channel in saved[0]["geometry_audit"]["channels"]
        for metric in channel["metrics"]
    ]
    response = routes_library.review_geometry_audit(GeometryAuditReviewRequest(
        json_path=first["json_path"], record_id=first["record_id"],
        checked_metric_ids=metric_ids[:2], reviewer="Boki", note="已核对前两项",
    ))
    assert response["checked_count"] == 2
    assert response["metric_count"] == 16
    assert response["complete"] is False
    reloaded = records.load_records_file(first["json_path"])[0]
    assert reloaded["geometry_audit"]["review"]["reviewer"] == "Boki"
    assert reloaded["geometry_audit"]["audit_hash"] == saved[0]["geometry_audit"]["audit_hash"]

    download = routes_library.geometry_audit_artifact(
        first["json_path"], first["record_id"], "input_double_line.dxf")
    assert Path(download.path).read_bytes() == b"DXF PLAN"

    with pytest.raises(HTTPException) as exc:
        routes_library.review_geometry_audit(GeometryAuditReviewRequest(
            json_path=first["json_path"], record_id=first["record_id"],
            checked_metric_ids=["cad.not-a-real-metric"],
        ))
    assert exc.value.status_code == 400


def test_geometry_audit_download_refuses_copied_file_tampering(
    gold_case: Path, tmp_path: Path, monkeypatch,
):
    output = tmp_path / "output_files"
    output.mkdir(exist_ok=True)
    monkeypatch.setattr(records, "MAIN_OUTPUT_DIR", str(output))
    monkeypatch.setattr(server_helpers, "MAIN_OUTPUT_DIR", str(output))
    archived = archive_geometry_gold_history(gold_case, level="L1", output_root=output)
    record = records.load_records_file(archived["json_path"])[0]
    artifact = next(row for row in record["geometry_audit"]["artifacts"]
                    if row["artifact_id"] == "input_double_line.dxf")
    (output / artifact["relative_path"]).write_bytes(b"changed after archive")
    with pytest.raises(HTTPException) as exc:
        routes_library.geometry_audit_artifact(
            archived["json_path"], archived["record_id"], "input_double_line.dxf")
    assert exc.value.status_code == 409
