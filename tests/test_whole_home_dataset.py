from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from Floor_engine_server import whole_home_dataset as dataset


CATALOG_ROOT = Path(__file__).parent / "datasets" / "whole_home_geometry"


def _catalog():
    return dataset.load_catalog(CATALOG_ROOT)


def test_committed_catalog_audits_and_contains_progressive_sources():
    catalog = _catalog()
    report = dataset.audit_catalog(catalog)
    assert report["ok"], report["errors"]
    assert report["case_count"] >= 9
    cases = {case["case_id"]: case for case in catalog.manifest["cases"]}
    assert cases["ifcbench_fzk_house"]["difficulty_level"] == "L1"
    assert cases["ifcbench_city_house_munich"]["difficulty_level"] == "L2"
    assert cases["ifcbench_duplex"]["difficulty_level"] == "L2"
    assert cases["ifcbench_fantasy_residential_building_1"]["difficulty_level"] == "L3"
    assert cases["structured3d_complex_residential"]["difficulty_level"] == "L4"
    assert cases["zind_extreme_multi_floor_residence"]["difficulty_level"] == "L5"


def test_l1_is_real_non_toy_and_score_is_recomputed():
    catalog = _catalog()
    case = next(item for item in catalog.manifest["cases"] if item["case_id"] == "ifcbench_fzk_house")
    score, level, groups = dataset.score_difficulty(case["difficulty_features"], catalog.difficulty_rules)
    assert (score, level) == (16, "L1")
    assert case["difficulty_features"]["room_count"] >= 4
    assert case["difficulty_features"]["opening_count"] >= 5
    assert case["difficulty_features"]["has_stairs"] is True
    assert groups["geometry_topology"] > 0


def test_select_cases_supports_csv_filters_and_rejects_unknown_values():
    catalog = _catalog()
    chosen = dataset.select_cases(catalog, levels=["L1,L2"], splits=["validation"])
    assert [case["case_id"] for case in chosen] == ["ifcbench_duplex"]
    with pytest.raises(dataset.DatasetError, match="unknown filter"):
        dataset.select_cases(catalog, levels=["L0"])


@pytest.mark.parametrize(
    "url",
    [
        "http://huggingface.co/datasets/sylvainHellin/ifc-bench/a",
        "https://example.com/free-cad/model.dwg",
        "https://user:password@huggingface.co/datasets/sylvainHellin/ifc-bench/a",
        "https://github.com/unknown/reuploaded-cad",
    ],
)
def test_official_url_allowlist_rejects_untrusted_or_unsafe_sources(url):
    with pytest.raises(dataset.DatasetError):
        dataset.validate_official_url(url)


def test_restricted_sources_are_skipped_without_network_or_agreement(tmp_path, monkeypatch):
    catalog = _catalog()
    restricted = dataset.select_cases(catalog, case_ids=["zind_extreme_multi_floor_residence"])
    monkeypatch.setattr(dataset, "_download_one", lambda *args, **kwargs: pytest.fail("network called"))
    result = dataset.download_cases(catalog, restricted, data_root=tmp_path)
    assert result["downloaded"] == []
    assert result["skipped"][0]["case_id"] == "zind_extreme_multi_floor_residence"
    assert "approval" in result["skipped"][0]["reason"].lower()


def test_download_uses_pinned_lock_values_and_gitignored_root(tmp_path, monkeypatch):
    catalog = _catalog()
    cases = dataset.select_cases(catalog, case_ids=["ifcbench_fzk_house"])
    calls = []

    def fake_download(url, destination, *, expected_sha256, expected_size, retries=4):
        calls.append((url, destination, expected_sha256, expected_size))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"mocked only; integrity implementation is tested separately")

    monkeypatch.setattr(dataset, "_download_one", fake_download)
    result = dataset.download_cases(catalog, cases, data_root=tmp_path)
    assert len(calls) == 2
    assert calls[0][0].startswith("https://huggingface.co/datasets/sylvainHellin/ifc-bench/resolve/")
    assert len(calls[0][2]) == 64
    assert calls[0][3] == 2570803
    assert all(str(tmp_path) in str(call[1]) for call in calls)
    assert len(result["downloaded"]) == 2


def _minimal_ifc_catalog(payload: bytes) -> dataset.Catalog:
    digest = hashlib.sha256(payload).hexdigest()
    manifest = {
        "cases": [{
            "case_id": "local_ifc", "building_group": "local:ifc", "source_id": "unused",
            "license_id": "MIT", "truth_kind": "truth_derived_pair", "split": "development",
            "difficulty_score": 0, "difficulty_level": "L1", "difficulty_features": {},
            "artifacts": [{"lock_id": "local_ifc", "media_type": "application/x-step"}],
        }]
    }
    lock = {"artifacts": {"local_ifc": {
        "case_id": "local_ifc", "relative_path": "local_ifc/arc.ifc",
        "url": "https://huggingface.co/datasets/sylvainHellin/ifc-bench/resolve/deadbeef/arc.ifc",
        "sha256": digest, "size_bytes": len(payload),
    }}}
    return dataset.Catalog(
        manifest=manifest, lock=lock, licenses={"licenses": {}},
        difficulty_rules=_catalog().difficulty_rules, splits={"levels": {}},
    )


def test_checksum_verification_and_ifc_inventory_preparation(tmp_path):
    payload = b"ISO-10303-21;\nDATA;\n#1= IFCSPACE('a');\n#2= IFCWALLSTANDARDCASE('b');\n#3= IFCDOOR('c');\nENDSEC;\nEND-ISO-10303-21;\n"
    catalog = _minimal_ifc_catalog(payload)
    source = tmp_path / "raw" / "local_ifc" / "arc.ifc"
    source.parent.mkdir(parents=True)
    source.write_bytes(payload)

    verified = dataset.verify_checksums(catalog, catalog.manifest["cases"], data_root=tmp_path, require_installed=True)
    assert verified["ok"] is True
    prepared = dataset.prepare_cases(catalog, catalog.manifest["cases"], data_root=tmp_path)
    assert prepared["prepared"] == ["local_ifc"]
    inventory = json.loads((tmp_path / "prepared" / "local_ifc" / "inventory.json").read_text("utf-8"))
    counts = inventory["sources"][0]["ifc_entity_counts"]
    assert counts["IFCSPACE"] == 1
    assert counts["IFCWALL_TOTAL"] == 1
    assert counts["IFCDOOR"] == 1

    source.write_bytes(payload + b"corrupt")
    corrupt = dataset.verify_checksums(catalog, catalog.manifest["cases"], data_root=tmp_path)
    assert corrupt["ok"] is False
    assert corrupt["corrupt"][0]["artifact"].startswith("local_ifc:")


def test_audit_detects_building_leakage_and_untrusted_artifact_url():
    original = _catalog()
    manifest = copy.deepcopy(original.manifest)
    duplicate = copy.deepcopy(manifest["cases"][0])
    duplicate["case_id"] = "leaked_variant"
    duplicate["split"] = "validation"
    duplicate["artifacts"] = []
    manifest["cases"].append(duplicate)
    splits = copy.deepcopy(original.splits)
    splits["levels"]["L1"]["validation"].append("leaked_variant")
    broken = dataset.Catalog(
        manifest=manifest, lock=original.lock, licenses=original.licenses,
        difficulty_rules=original.difficulty_rules, splits=splits,
    )
    report = dataset.audit_catalog(broken)
    assert report["ok"] is False
    assert any("building leakage" in error for error in report["errors"])

    lock = copy.deepcopy(original.lock)
    lock["artifacts"]["ifcbench_fzk_house_arc_ifc"]["url"] = "https://example.com/reupload.ifc"
    broken_url = dataset.Catalog(
        manifest=original.manifest, lock=lock, licenses=original.licenses,
        difficulty_rules=original.difficulty_rules, splits=original.splits,
    )
    report = dataset.audit_catalog(broken_url)
    assert any("official allowlist" in error for error in report["errors"])


def _private_cad_project(tmp_path):
    raw = tmp_path / "private_address_and_owner.dwg"
    raw.write_bytes(b"AC1032 PRIVATE ORIGINAL CAD")
    converted = tmp_path / "private_named_acadsharp_output.dxf"
    converted.write_bytes(b"0\nSECTION\n2\nENTITIES\n0\nEOF\n")
    report = {
        "schema_version": 1,
        "source_path": str(converted),
        "source_sha256": dataset.sha256_file(converted),
        "insunits": 4,
        "unit_scale_to_m": 0.001,
        "structural_entity_count": 2,
        "ignored_nonstructural_count": 91,
        "selected_candidate_id": "private-candidate-id",
        "candidate_plans": [
            {"candidate_id": "private-other"},
            {"candidate_id": "private-candidate-id"},
        ],
        "texts": [{"text": "PRIVATE STREET ADDRESS", "layer": "OWNER-NOTES"}],
        "raw_faces": [{
            "face_id": "private-face-handle",
            "polygon": [{"x": 1002.0, "z": -498.0}, {"x": 1005.0, "z": -498.0},
                        {"x": 1005.0, "z": -495.0}, {"x": 1002.0, "z": -495.0}],
            "interior_rings": [], "area_m2": 9.0, "manual_eligible": True,
            "anchors": [{"text": "PRIVATE ROOM NAME", "cad_provenance": {"layer": "PRIVATE"}}],
        }],
    }
    report_path = tmp_path / "parse_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    draft = {
        "wall_height_m": 2.8,
        "walls": [{
            "id": "private-wall-handle", "start": {"x": 1002.0, "z": -498.0},
            "end": {"x": 1005.0, "z": -498.0}, "thickness_m": 0.2,
            "height_m": 2.8, "boundary_kind": "centerline",
            "cad_provenance": {
                "source_kind": "INSERT", "handle": "SECRET-HANDLE", "layer": "PRIVATE-LAYER",
                "block": "PRIVATE-BLOCK", "insert_chain": [{"block": "PRIVATE-NESTED"}],
            },
        }],
        "wall_assemblies": [{
            "id": "private-assembly", "source_representation": "closed_footprint",
            "review_status": "needs_review", "height_m": 2.8,
            "footprint_polygon": [[1002.0, -498.0], [1005.0, -498.0],
                                  [1005.0, -497.8], [1002.0, -497.8]],
            "cad_provenance": {"source_kind": "LWPOLYLINE", "layer": "PRIVATE-LAYER"},
        }],
        "openings": [{
            "id": "private-opening", "wall_id": "private-wall-handle", "kind": "door",
            "offset_m": 1.0, "width_m": 0.9, "height_m": 2.1, "sill_height_m": 0,
            "cad_provenance": {"source_kind": "INSERT", "block": "PRIVATE-DOOR"},
        }],
        "physical_spaces": [{"id": "private-space", "label": "PRIVATE ROOM"}],
        "semantic_zones": [],
        "space_confirmation": {"status": "needs_review", "reason_codes": ["private-code"]},
    }
    draft_path = tmp_path / "space_draft.json"
    draft_path.write_text(json.dumps(draft), encoding="utf-8")
    project = {
        "project_id": "PRIVATE-PROJECT-ID",
        "cad_path": str(raw),
        "parse_report": {
            "schema_version": 1, "storage": "external_json_v1",
            "report_path": str(report_path), "report_sha256": dataset.sha256_file(report_path),
        },
        "cad_space_draft_pointer": {
            "storage": "external_json_v1", "path": str(draft_path),
            "sha256": dataset.sha256_file(draft_path),
        },
    }
    project_path = tmp_path / "project.json"
    project_path.write_text(json.dumps(project), encoding="utf-8")
    return project_path, raw, converted


def test_private_cad_export_is_deterministic_anonymous_and_explicitly_unreviewed(tmp_path):
    project_path, raw, converted = _private_cad_project(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    key = b"test-only-private-hmac-key"
    one = dataset.export_sanitized_cad_regression(
        project_path, first, fixture_id="cad_real_001", commitment_key=key
    )
    two = dataset.export_sanitized_cad_regression(
        project_path, second, fixture_id="cad_real_001", commitment_key=key
    )
    assert one["fixture_hash"] == two["fixture_hash"]
    assert first.read_bytes() == second.read_bytes()
    fixture = json.loads(first.read_text("ascii"))
    assert dataset.validate_cad_regression_fixture(fixture)["ok"] is True
    assert fixture["ground_truth"]["status"] == "annotation_required"
    assert fixture["ground_truth"]["missing_tasks"]
    assert fixture["selection"]["selected_candidate_ordinal"] == 2
    assert fixture["normalized_entities"]["walls"][0]["start"] == [0.0, 0.0]
    assert fixture["normalized_entities"]["openings"][0]["wall_id"] == "wall_000001"
    serialized = first.read_text("ascii").lower()
    for secret in (
        "private", "owner", "street", "secret-handle", "private-layer", "private-block",
        str(raw).lower(), str(converted).lower(), dataset.sha256_file(raw), dataset.sha256_file(converted),
    ):
        assert secret not in serialized
    commitments = fixture["source_commitments"]
    assert all(row["algorithm"] == "hmac-sha256" for row in commitments)


def test_private_cad_export_keeps_rejected_redundant_evidence_terminal(tmp_path):
    project_path, _, _ = _private_cad_project(tmp_path)
    project = json.loads(project_path.read_text("utf-8"))
    draft_path = Path(project["cad_space_draft_pointer"]["path"])
    draft = json.loads(draft_path.read_text("utf-8"))
    draft["wall_assemblies"].append({
        "id": "private-redundant",
        "source_representation": "redundant_evidence",
        "source_centerline": [[0, 0], [2, 0]],
        "review_status": "rejected",
        "height_m": 0,
        "cad_provenance": {"source_kind": "LINE"},
    })
    draft_path.write_text(json.dumps(draft), encoding="utf-8")
    project["cad_space_draft_pointer"]["sha256"] = dataset.sha256_file(draft_path)
    project_path.write_text(json.dumps(project), encoding="utf-8")
    output = tmp_path / "redundant.json"
    dataset.export_sanitized_cad_regression(
        project_path, output, fixture_id="cad_real_audit_001",
        commitment_key=b"test-only-private-hmac-key",
    )
    fixture = json.loads(output.read_text("ascii"))
    redundant = next(
        row for row in fixture["normalized_entities"]["wall_assemblies"]
        if row["representation"] == "redundant_evidence"
    )
    assert redundant["review_state"] == "rejected"
    assert fixture["candidate_review"]["unresolved_wall_assembly_count"] == 1
    assert dataset.validate_cad_regression_fixture(fixture)["ok"] is True


def test_private_cad_fixture_privacy_gate_rejects_names_paths_unicode_and_tampering():
    fixture_path = CATALOG_ROOT / "cad_private_regression" / "contract_example.json"
    fixture = json.loads(fixture_path.read_text("ascii"))
    assert dataset.validate_cad_regression_fixture(fixture)["ok"] is True

    leaked = copy.deepcopy(fixture)
    leaked["normalized_entities"]["walls"][0]["original_layer"] = "业主地址图层"
    report = dataset.validate_cad_regression_fixture(leaked)
    assert report["ok"] is False
    assert any("forbidden private-data field" in error for error in report["errors"])
    assert any("non-ASCII" in error for error in report["errors"])

    path_leak = copy.deepcopy(fixture)
    path_leak["candidate_review"]["state"] = r"C:\Users\Owner\source.dwg"
    report = dataset.validate_cad_regression_fixture(path_leak)
    assert report["ok"] is False
    assert any("path, URL, CAD filename" in error for error in report["errors"])

    tampered = copy.deepcopy(fixture)
    tampered["normalized_entities"]["walls"][0]["end"] = [99.0, 0.0]
    report = dataset.validate_cad_regression_fixture(tampered)
    assert report["ok"] is False
    assert any("entity_hash: mismatch" in error for error in report["errors"])


def test_committed_real_cad_candidate_is_anonymous_complete_stream_but_not_gold():
    root = CATALOG_ROOT / "cad_private_regression"
    manifest = json.loads((root / "fixture_manifest.json").read_text("utf-8"))
    entry = next(row for row in manifest["fixtures"] if row["fixture_id"] == "cad_real_001")
    fixture_path = root / entry["candidate_file"]
    assert dataset.sha256_file(fixture_path) == entry["candidate_file_sha256"]
    payload = fixture_path.read_bytes()
    assert payload.isascii()
    fixture = json.loads(payload)
    report = dataset.validate_cad_regression_fixture(fixture)
    assert report == {
        "ok": True, "errors": [], "fixture_id": "cad_real_001",
        "ground_truth_status": "annotation_required",
    }
    entities = fixture["normalized_entities"]
    assert len(entities["walls"]) == 743
    assert len(entities["wall_assemblies"]) == 451
    assert len(entities["face_candidates"]) == 27
    assert len(entities["openings"]) == 0
    assert fixture["candidate_review"]["unresolved_wall_assembly_count"] == 236
    assert fixture["ground_truth"]["missing_tasks"]
    assert entry["production_gold_eligible"] is False
    assert entry["manual_privacy_review"] == "pending"


def test_reviewed_ground_truth_requires_complete_wall_and_face_decisions():
    fixture = json.loads(
        (CATALOG_ROOT / "cad_private_regression" / "contract_example.json").read_text("ascii")
    )
    fixture["ground_truth"]["status"] = "reviewed"
    fixture["ground_truth"]["missing_tasks"] = []
    fixture["ground_truth"]["review_checks"] = {
        "walls_complete": True, "openings_complete": True,
        "spaces_complete": True, "source_alignment_checked": True,
    }
    fixture["fixture_hash"] = dataset.canonical_json_sha256({
        key: value for key, value in fixture.items() if key != "fixture_hash"
    })
    report = dataset.validate_cad_regression_fixture(fixture)
    assert report["ok"] is False
    assert any("every wall requires a decision" in error for error in report["errors"])
    assert any("every eligible face" in error for error in report["errors"])


def test_private_cad_export_rejects_short_key_and_dxf_hash_disagreement(tmp_path):
    project_path, _raw, converted = _private_cad_project(tmp_path)
    project = json.loads(project_path.read_text("utf-8"))
    with pytest.raises(dataset.DatasetError, match="at least 16 bytes"):
        dataset.build_sanitized_cad_regression_fixture(
            project, fixture_id="cad_real_001", commitment_key=b"short"
        )
    converted.write_bytes(b"tampered converted data")
    with pytest.raises(dataset.DatasetError, match="hash disagrees"):
        dataset.build_sanitized_cad_regression_fixture(
            project, fixture_id="cad_real_001",
            commitment_key=b"test-only-private-hmac-key",
        )
