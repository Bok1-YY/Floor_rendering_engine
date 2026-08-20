from __future__ import annotations

import json
from pathlib import Path

import pytest

from Floor_engine_server import records, routes_library, server_helpers
from Floor_engine_server.whole_home_dataset import sha256_file
from Floor_engine_server.whole_home_geometry import build_geometry_manifest
from Floor_engine_server.whole_home_dwg_audit import (
    DwgAuditError,
    DwgGeometryTruthV1,
    DwgLiveGeometryAuditV1,
    EVIDENCE_ROLES,
    VISUAL_CHECK_IDS,
    archive_dwg_live_geometry_audit,
    render_software_orthographic_evidence,
    seed_pending_truths,
    main as dwg_audit_main,
)


def _truth(source: Path, *, status: str = "verified") -> dict:
    geometry = {
        "walls": [{
            "wall_id": "wall-1",
            "polygon_m": [[0, 0], [4, 0], [4, 0.2], [0, 0.2]],
            "source_handles": ["A1", "A2"],
        }],
        "rooms": [{
            "room_id": "room-1",
            "label": "bedroom",
            "polygon_m": [[0.2, 0.2], [4, 0.2], [4, 3], [0.2, 3]],
        }],
        "openings": [{
            "opening_id": "door-1", "type": "door",
            "center_m": [1.0, 0.1], "width_m": 0.9, "wall_id": "wall-1",
        }],
        "forbidden_entity_handles": ["BED-1", "TABLE-1"],
    }
    if status == "pending":
        geometry = {"walls": [], "rooms": [], "openings": [], "forbidden_entity_handles": []}
    return {
        "schema_name": "DwgGeometryTruthV1",
        "schema_version": 1,
        "case_id": "01",
        "title": "Two-bedroom baseline",
        "level": "L1",
        "status": status,
        "source": {
            "file_name": source.name,
            "sha256": sha256_file(source),
            "size_bytes": source.stat().st_size,
            "dwg_signature": "AC1027",
            "insunits": 4,
        },
        "coordinate_contract": {
            "schema_version": 2,
            "coordinate_system": "cad-meters-x-east-y-north-v2",
            "topdown_view": "sky-to-ground",
            "screen_mapping": {"cad_x": "+screen_right", "cad_y": "+screen_up"},
        },
        "independence": {
            "method": "manual_vector_annotation",
            "production_parser_derived": False,
            "reviewer": "Boki" if status == "verified" else "",
            "reviewed_at": "2026-08-14 10:00:00" if status == "verified" else "",
            "note": "Reviewed against source CAD entities.",
        },
        "geometry": geometry,
        "reference_evidence": {},
    }


def _run(source: Path, *, status: str = "passed") -> dict:
    camera_contract = {
        "schema_version": 2,
        "contract": "whole_home_sky_down_orthographic_v2",
        "coordinate_system": "right-handed-y-up-x-east-z-south-v2",
        "model_coordinate_contract_version": 2,
        "projection": "orthographic",
        "renderer": "threejs_webgl",
        "webgl_capture": True,
        "view_direction": [0, -1, 0],
        "camera_up": [0, 0, -1],
        "screen_right": [1, 0, 0],
        "cad_axis_mapping": {"cad_x": "+screen_right", "cad_y": "+screen_up"},
        "eye": [2, 10, 1.5], "target": [2, 1.4, 1.5],
        "frustum": {"left": -2.2, "right": 2.2, "top": 1.65, "bottom": -1.65,
                    "near": .01, "far": 20},
        "viewport": [1600, 1600], "padding_per_side": .05,
    }
    return {
        "schema_name": "DwgLiveGeometryAuditV2",
        "schema_version": 2,
        "case_id": "01",
        "level": "L1",
        "status": status,
        "executed_at": "2026-08-14 11:00:00",
        "source_sha256": sha256_file(source),
        "project_id": "project-01",
        "revision": "r1",
        "model_facts_hash": "1" * 64,
        "geometry_manifest_hash": "2" * 64,
        "camera_contract": camera_contract,
        "metrics": [
            {"metric_id": "wall_footprint_iou", "actual": 0.999, "operator": ">=", "threshold": 0.995, "unit": "ratio"},
            {"metric_id": "opening_recall", "actual": 1.0, "operator": ">=", "threshold": 1.0, "unit": "ratio"},
        ],
        "visual_checks": [
            {"check_id": check_id, "status": "passed", "note": "multimodal comparison passed"}
            for check_id in VISUAL_CHECK_IDS
        ],
        "issues": [],
    }


def _evidence(tmp_path: Path, *, complete: bool = True) -> dict[str, Path]:
    roles = EVIDENCE_ROLES if complete else ("cad_preview", "failure_screenshot")
    result = {}
    for index, role in enumerate(roles):
        path = tmp_path / f"{role}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([index]) + role.encode("ascii"))
        result[role] = path
    return result


def test_truth_hash_is_stable_and_rejects_production_self_proof(tmp_path: Path):
    source = tmp_path / "case.dwg"
    source.write_bytes(b"AC1027 independent source")
    raw = _truth(source)
    first = DwgGeometryTruthV1.from_mapping(raw, source_path=source)
    reordered = json.loads(json.dumps(raw, sort_keys=True))
    second = DwgGeometryTruthV1.from_mapping(reordered, source_path=source)
    assert first.truth_hash == second.truth_hash
    assert len(first.truth_hash) == 64

    raw["production_parser"] = {"selected_candidate_id": "cad_plan_2"}
    with pytest.raises(DwgAuditError, match="production-derived"):
        DwgGeometryTruthV1.from_mapping(raw)

    wrong = tmp_path / "wrong.dwg"
    wrong.write_bytes(b"different")
    with pytest.raises(DwgAuditError, match="does not match locked"):
        DwgGeometryTruthV1.from_mapping(_truth(source), source_path=wrong)


def test_seed_pending_truths_uses_source_locks_but_discards_parser_claims(tmp_path: Path):
    dataset = tmp_path / "dwg_test"
    (dataset / "previews").mkdir(parents=True)
    source = dataset / "01.dwg"
    preview = dataset / "previews" / "01.png"
    source.write_bytes(b"AC1027 source")
    preview.write_bytes(b"PNG preview")
    manifest = {
        "files": [{
            "id": "01", "filename": source.name, "preview": "previews/01.png",
            "difficulty": "L1", "bytes": source.stat().st_size,
            "sha256": sha256_file(source), "dwg_signature": "AC1027", "insunits": 4,
            "production_parser": {"status": "pass", "walls": 999, "selected_candidate_id": "cad_plan_2"},
        }]
    }
    manifest_path = dataset / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = seed_pending_truths(manifest_path, tmp_path / "truth")
    truth_value = json.loads(Path(result[0]["truth_path"]).read_text(encoding="utf-8"))
    assert result[0]["status"] == "pending"
    assert truth_value["status"] == "pending"
    assert truth_value["geometry"]["walls"] == []
    assert "production_parser" not in truth_value
    assert "selected_candidate_id" not in json.dumps(truth_value)
    assert truth_value["independence"]["production_parser_derived"] is False
    assert truth_value["reference_evidence"]["sha256"] == sha256_file(preview)
    assert seed_pending_truths(manifest_path, tmp_path / "truth")[0]["created"] is False


def test_passed_live_audit_requires_verified_truth_all_checks_and_five_images(tmp_path: Path):
    source = tmp_path / "case.dwg"
    source.write_bytes(b"AC1027 independent source")
    verified = DwgGeometryTruthV1.from_mapping(_truth(source))
    audit = DwgLiveGeometryAuditV1.build(verified, _run(source), _evidence(tmp_path))
    assert audit.data["status"] == "passed"
    assert audit.data["integrity"]["checked_count"] == 5
    assert len(audit.data["audit_hash"]) == 64
    assert all(len(row["sha256"]) == 64 for row in audit.data["artifacts"])

    with pytest.raises(DwgAuditError, match="verified independent truth"):
        DwgLiveGeometryAuditV1.build(
            DwgGeometryTruthV1.from_mapping(_truth(source, status="pending")),
            _run(source), _evidence(tmp_path),
        )
    with pytest.raises(DwgAuditError, match="missing evidence"):
        DwgLiveGeometryAuditV1.build(verified, _run(source), _evidence(tmp_path, complete=False))
    missing_check = _run(source)
    missing_check["visual_checks"] = missing_check["visual_checks"][:-1]
    with pytest.raises(DwgAuditError, match="missing visual checks"):
        DwgLiveGeometryAuditV1.build(verified, missing_check, _evidence(tmp_path))

    legacy = _run(source)
    legacy["schema_name"] = "DwgLiveGeometryAuditV1"
    with pytest.raises(DwgAuditError, match="cannot pass"):
        DwgLiveGeometryAuditV1.build(verified, legacy, _evidence(tmp_path))

    reflected = _run(source)
    reflected["camera_contract"]["frustum"]["top"] = -1.65
    reflected["camera_contract"]["frustum"]["bottom"] = 1.65
    with pytest.raises(DwgAuditError, match="reflected or invalid"):
        DwgLiveGeometryAuditV1.build(verified, reflected, _evidence(tmp_path))


def test_software_projection_is_deterministic_and_cannot_masquerade_as_webgl(tmp_path: Path):
    source = tmp_path / "case.dwg"
    source.write_bytes(b"AC1027 independent source")
    truth = DwgGeometryTruthV1.from_mapping(_truth(source))
    manifest = build_geometry_manifest(
        project_id="project-01",
        model_revision=1,
        model_facts_hash="1" * 64,
        registration_hash="4" * 64,
        geometry_kernel_version="test-kernel-v1",
        units="meter",
        vertices=[[0, 0, 0], [4, 0, 0], [4, 0, 0.2], [0, 0, 0.2]],
        wall_parts=[{"id": "wall-1", "wall_id": "wall-1", "indices": [0, 1, 2, 0, 2, 3]}],
        floor_parts=[], ceiling_parts=[], object_parts=[], opening_voids=[],
    )
    projection_contract = {
        "schema_version": 2,
        "source_coordinate_system": "right-handed-y-up-x-east-z-south-v2",
        "target_coordinate_system": "cad-meters-x-east-y-north-v2",
        "scale": 1, "rotation_deg": 0, "translation_m": [0, .2], "flip_y": True,
    }
    first = render_software_orthographic_evidence(
        truth, manifest, tmp_path / "projection-a", canvas_size=512,
        model_to_truth=projection_contract)
    second = render_software_orthographic_evidence(
        truth, manifest, tmp_path / "projection-b", canvas_size=512,
        model_to_truth=projection_contract)
    assert first["renderer"] == "software_projection"
    assert first["webgl_capture"] is False
    assert first["metrics"]["wall_footprint_pixel_iou"] == 1.0
    assert first["report_hash"] == second["report_hash"]
    for artifact_id in ("structure_truth", "software_model_topdown", "software_overlay", "software_diff"):
        assert sha256_file(first["evidence_files"][artifact_id]) == sha256_file(second["evidence_files"][artifact_id])

    failed_run = _run(source, status="failed")
    software_evidence = {
        "cad_preview": tmp_path / "projection-a" / "structure_truth.png",
        **{key: Path(path) for key, path in first["evidence_files"].items()},
    }
    failed_audit = DwgLiveGeometryAuditV1.build(truth, failed_run, software_evidence)
    kinds = {row["artifact_id"]: row["evidence_kind"] for row in failed_audit.data["artifacts"]}
    assert kinds["software_model_topdown"] == "software_projection"
    with pytest.raises(DwgAuditError, match="missing evidence"):
        DwgLiveGeometryAuditV1.build(truth, _run(source), software_evidence)


def test_success_and_failure_archive_into_common_geometry_records(tmp_path: Path, monkeypatch):
    source = tmp_path / "case.dwg"
    source.write_bytes(b"AC1027 independent source")
    truth = DwgGeometryTruthV1.from_mapping(_truth(source))
    output = tmp_path / "output_files"
    output.mkdir(exist_ok=True)
    monkeypatch.setattr(records, "MAIN_OUTPUT_DIR", str(output))
    monkeypatch.setattr(server_helpers, "MAIN_OUTPUT_DIR", str(output))

    passed = archive_dwg_live_geometry_audit(
        truth, _run(source), _evidence(tmp_path), output_root=output, source_path=source,
    )
    repeated = archive_dwg_live_geometry_audit(
        truth, _run(source), _evidence(tmp_path), output_root=output, source_path=source,
    )
    assert passed["created"] is True
    assert repeated["created"] is False
    assert passed["record_id"] == repeated["record_id"]
    assert passed["preview_count"] == 5

    failed_run = _run(source, status="failed")
    failed_run["metrics"][0]["actual"] = 0.4
    failed_run["visual_checks"][0]["status"] = "failed"
    failed_run["issues"] = [{"code": "wrong_candidate", "severity": "hard"}]
    failed = archive_dwg_live_geometry_audit(
        truth, failed_run, _evidence(tmp_path, complete=False), output_root=output,
    )
    assert failed["status"] == "failed"
    assert failed["preview_count"] == 2
    saved = records.load_records_file(passed["json_path"])
    assert len(saved) == 2
    assert all(row["immutable_audit"] is True for row in saved)

    monkeypatch.setattr(routes_library, "scan_json_files", lambda: [passed["json_path"]])
    index = routes_library.list_geometry_audits(limit=20)
    assert {row["entry"]["geometry_audit"]["status"] for row in index} == {"passed", "failed"}
    pass_entry = next(row["entry"] for row in index if row["entry"]["geometry_audit"]["status"] == "passed")
    assert [result["model_label"] for result in pass_entry["results"]] == [
        "CAD 原图", "独立结构真值", "3D 正交俯视", "CAD / 3D 叠加", "几何差分",
    ]
    assert all(result["result_url"] for result in pass_entry["results"])
    assert all("source_path" not in artifact for artifact in pass_entry["geometry_audit"]["artifacts"])
    downloaded = routes_library.geometry_audit_artifact(
        passed["json_path"], passed["record_id"], "model_topdown",
    )
    assert sha256_file(downloaded.path) == next(
        artifact["sha256"]
        for artifact in records.load_records_file(passed["json_path"])[-1]["geometry_audit"]["artifacts"]
        if artifact["artifact_id"] == "model_topdown"
    )


def test_cli_failed_audit_archive_is_a_successful_command(tmp_path: Path):
    source = tmp_path / "case.dwg"
    source.write_bytes(b"AC1027 independent source")
    truth_path = tmp_path / "truth.json"
    truth_path.write_text(json.dumps(_truth(source, status="pending")), encoding="utf-8")
    run = _run(source, status="failed")
    run["visual_checks"] = []
    run["issues"] = [{"code": "opening_mismatch", "severity": "hard"}]
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    failure = tmp_path / "failure.png"
    failure.write_bytes(b"\x89PNG\r\n\x1a\nfailed evidence")

    exit_code = dwg_audit_main([
        "archive", str(truth_path), str(run_path),
        "--source", str(source),
        "--evidence", f"failure_screenshot={failure}",
        "--output-root", str(tmp_path / "output_files"),
    ])

    assert exit_code == 0
    assert (tmp_path / "output_files" / "geometry_audits" / "Plan-to-3D_DWG_01_记录.json").is_file()


def test_baseline_archive_default_matches_source_launcher_data_root(tmp_path: Path):
    from tools.archive_dwg_test_baseline import default_output_root

    repository = tmp_path / "checkout"
    assert default_output_root(environment={}, repository=repository) == (
        repository / "data" / "output_files"
    ).resolve()
    custom_data = tmp_path / "runtime-data"
    assert default_output_root(
        environment={"FLOOR_DATA_DIR": str(custom_data)}, repository=repository,
    ) == (custom_data / "output_files").resolve()
