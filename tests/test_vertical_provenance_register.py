from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2.build_layer3a_subtype_register import EXCLUDED_IDS, OPENING_IDS
from tools.goal_loop_v2.build_vertical_provenance_register import (
    BATCH_FAIL_CLOSED,
    ROW_FAIL_CLOSED,
    _candidate_hash,
    build,
    validate,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/goal_loop_v2/build_vertical_provenance_register.py"
OP002_AUDIT = ROOT / "reports/op002_vertical_provenance_20260903/audit.json"
OP001_ARTIFACT_DIR = ROOT / "reports/op001_unit_scope_candidate_20260902"


def _row(candidate, opening_id):
    return next(row for row in candidate["rows"] if row["opening_id"] == opening_id)


def _rehash_row_and_register(candidate, row):
    row["candidate_hash"] = _candidate_hash(
        {key: value for key, value in row.items() if key != "candidate_hash"}
    )
    candidate["candidate_hash"] = _candidate_hash(
        {key: value for key, value in candidate.items() if key != "candidate_hash"}
    )


def test_register_covers_nine_openings_with_all_gates_closed():
    candidate = build()
    assert candidate["opening_ids"] == list(OPENING_IDS)
    assert candidate["excluded_opening_ids"] == list(EXCLUDED_IDS)
    assert candidate["coverage_count"] == 9
    assert candidate["opening_specific_audit_count"] == 2
    assert candidate["source_confirmed_vertical_count"] == 0
    assert candidate["unknown_sill_treatment_count"] == 9
    assert len(candidate["rows"]) == 9
    for key in BATCH_FAIL_CLOSED:
        assert candidate[key] is False
    assert candidate["score_effect"] == "none"


def test_every_row_has_real_xy_segment_and_no_vertical_authority():
    candidate = build()
    for row in candidate["rows"]:
        assert len(row["xy_binding"]["segment_m"]) == 2
        assert all(len(point) == 2 for point in row["xy_binding"]["segment_m"])
        assert row["xy_binding"]["vertical_authority"] is False
        assert row["xy_binding"]["effective_void_authority"] is False
        assert row["layer3a_binding"]["vertical_authority"] is False
        assert row["vertical_parameters"]["head_m"]["source_explicit"] is False
        assert row["vertical_parameters"]["sill_m"]["treatment"] == "unknown"
        assert row["vertical_parameters"]["sill_m"]["usable_for_reversible_research_display"] is False
        for key in ROW_FAIL_CLOSED:
            assert row[key] is False
        assert row["score_effect"] == "none"


def test_op001_keeps_source_record_and_root_hypothesis_quarantined():
    row = _row(build(), "OP001")
    assert row["source_record_state"]["source_observation_kind"] == "entrance_symbol"
    assert row["source_record_state"]["source_observation_status"] == "confirmed"
    assert row["source_record_state"]["source_host_atom_id"] == "ATOM-WB016-02"
    assert row["source_record_state"]["effective_void_record_status"] == "confirmed"
    assert row["source_record_state"]["record_fields_are_register_confirmations"] is False
    assert row["effective_void_confirmation"] is False
    assert row["vertical_parameters"]["head_m"] == {
        "source_record_value": 2.1,
        "research_default_value": 2.1,
        "provenance_class": "research_assumption_bound_to_candidate_source_record",
        "assumption_id": "ASSUME-Z-RESEARCH",
        "source_explicit": False,
        "human_authorized_default": False,
        "eligible_for_source_promotion": False,
    }
    assert row["vertical_parameters"]["sill_m"]["source_record_value"] == 0.0
    assert row["vertical_parameters"]["sill_m"]["provenance_class"] == "unsupported_candidate_value"
    assert row["unit_root_candidate"] == "hypothesis"
    assert row["op001_unit_scope_binding"]["building_outer_boundary_intersection"] is False
    assert row["building_exterior_root_confirmation"] is False
    assert row["unit_root_confirmation"] is False
    assert row["root_confirmation"] is False
    assert row["human_readable_boundary"]["entry_label_is_source_pixel_context_only"] is True


def test_op002_and_op004_bind_only_their_validated_audits():
    candidate = build()
    op002 = _row(candidate, "OP002")
    op004 = _row(candidate, "OP004")
    assert op002["opening_specific_audit_binding"]["schema"] == "op002-vertical-provenance-audit-v2"
    assert op002["opening_specific_audit_binding"]["candidate_hash"] == "07a585668252edbed1177390052077cd24fdf3a73bc58143ceed30e24814861e"
    assert op002["opening_specific_audit_binding"]["vertical_evidence_supports_height"] is False
    assert op004["opening_specific_audit_binding"]["schema"] == "opening-vertical-provenance-audit-v1"
    assert op004["opening_specific_audit_binding"]["candidate_hash"] == "07ec58494591af7d2b7d3db37d9f66c52116bddc3d7ff5c89bb4e1b62eda783f"
    assert op004["opening_specific_audit_binding"]["vertical_evidence_present"] is False
    for opening_id in set(OPENING_IDS) - {"OP002", "OP004"}:
        assert _row(candidate, opening_id)["opening_specific_audit_binding"] is None


@pytest.mark.parametrize(
    ("opening_id", "path", "value"),
    [
        ("OP001", ("building_exterior_root_confirmation",), True),
        ("OP001", ("unit_root_confirmation",), True),
        ("OP001", ("effective_void_confirmation",), True),
        ("OP001", ("vertical_parameters", "head_m", "source_explicit"), True),
        ("OP001", ("vertical_parameters", "sill_m", "usable_for_reversible_research_display"), True),
        ("OP002", ("source_vertical_confirmation",), True),
        ("OP004", ("vertical_display_policy", "opening_geometry_authorized"), True),
    ],
)
def test_row_semantic_or_vertical_promotions_are_rejected(opening_id, path, value):
    candidate = build()
    row = _row(candidate, opening_id)
    target = row
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    _rehash_row_and_register(candidate, row)
    with pytest.raises(ValueError, match="drift|promoted|quarantine"):
        validate(candidate)


def test_cross_opening_audit_reuse_is_rejected():
    candidate = build()
    op003 = _row(candidate, "OP003")
    op003["opening_specific_audit_binding"] = deepcopy(
        _row(candidate, "OP002")["opening_specific_audit_binding"]
    )
    _rehash_row_and_register(candidate, op003)
    with pytest.raises(ValueError, match="drift|cross-opening"):
        validate(candidate)


def test_empty_xy_segment_is_rejected():
    candidate = build()
    op008 = _row(candidate, "OP008")
    op008["xy_binding"]["segment_m"] = None
    _rehash_row_and_register(candidate, op008)
    with pytest.raises(ValueError, match="drift|authority"):
        validate(candidate)


def test_batch_promotion_is_rejected_even_after_rehash():
    candidate = build()
    candidate["vertical_entry_authorized"] = True
    candidate["candidate_hash"] = _candidate_hash(
        {key: value for key, value in candidate.items() if key != "candidate_hash"}
    )
    with pytest.raises(ValueError, match="drift|promoted"):
        validate(candidate)


def test_tampered_op002_audit_file_is_rejected(tmp_path):
    audit = json.loads(OP002_AUDIT.read_text(encoding="utf-8"))
    audit["vertical_parameters"]["sill_m"]["treatment"] = "accepted"
    audit["candidate_hash"] = _candidate_hash(
        {key: value for key, value in audit.items() if key != "candidate_hash"}
    )
    tampered = tmp_path / "op002-audit.json"
    tampered.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(ValueError, match="OP002 vertical provenance|scope drift|derivation drift"):
        build(op002_audit_path=tampered)


def test_candidate_and_row_hashes_are_reproducible():
    first = build()
    second = build()
    assert first == second
    assert first["candidate_hash"] == _candidate_hash(
        {key: value for key, value in first.items() if key != "candidate_hash"}
    )
    for row in first["rows"]:
        assert row["candidate_hash"] == _candidate_hash(
            {key: value for key, value in row.items() if key != "candidate_hash"}
        )


def test_two_parallel_cli_builds_do_not_race_on_op001_artifacts(tmp_path):
    outputs = [tmp_path / "parallel-a.json", tmp_path / "parallel-b.json"]
    processes = [
        subprocess.Popen(
            [sys.executable, str(SCRIPT), "--output", str(output)],
            cwd=tmp_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for output in outputs
    ]
    completed = [process.communicate(timeout=120) for process in processes]
    for process, (stdout, stderr) in zip(processes, completed):
        assert process.returncode == 0, stderr
        assert stdout.strip()
    candidates = [json.loads(output.read_text(encoding="utf-8")) for output in outputs]
    assert candidates[0] == candidates[1]
    assert list(OP001_ARTIFACT_DIR.glob(".*.tmp")) == []


def test_direct_script_runs_outside_repository(tmp_path):
    output = tmp_path / "register.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written == build()
