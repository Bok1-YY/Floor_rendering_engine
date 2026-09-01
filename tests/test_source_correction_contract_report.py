from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from tests.test_authorized_source_correction import H, _filehash, _wrapper, _write
from tools.fastloop_research.v21_contract import compute_v21_structure_hash
from tools.goal_loop_v2.source_contract_report import (
    generate_source_contract_report_from_source_correction,
)
from tools.goal_loop_v2.source_correction import apply_authorized_source_correction


ROOT = Path(__file__).resolve().parents[1]


def _inputs(tmp_path):
    wrapper, paths, _ = _wrapper(tmp_path)
    source = json.loads(paths["source_document"].read_text(encoding="utf-8"))
    result, application = apply_authorized_source_correction(wrapper)
    contract = json.loads(
        (ROOT / "docs" / "goal_loop_v2" / "goal-contract.json").read_text(encoding="utf-8")
    )
    contract["samples"] = ["fixture-v2"]
    return source, wrapper, [paths["evidence"]], result, contract, application, paths


def _generate(values):
    source, wrapper, evidence, result, contract, application, _ = values
    return generate_source_contract_report_from_source_correction(
        source, wrapper, evidence, result, contract, application
    )


def test_authorized_source_correction_lineage_replays_before_scoring(tmp_path):
    values = _inputs(tmp_path)
    report, detail = _generate(values)
    assert report["reference_hash"] == values[3]["structure_hash"]
    assert report["source_hash"] == values[3]["source_hash"]
    assert next(row for row in report["checks"] if row["id"] == "S01_SOURCE_IDENTITY")["status"] == "pass"
    assert detail["provenance_chain"]["lineage_type"] == "authorized_source_correction"
    assert detail["provenance_chain"]["canonical_recomputation_equal"] is True
    assert detail["provenance_chain"]["current_result_hash"] == values[3]["structure_hash"]


def test_rehashed_wrapper_is_rejected_by_exact_application_replay(tmp_path):
    values = list(_inputs(tmp_path))
    values[1] = deepcopy(values[1])
    values[1]["constraints"].append("forged but canonically rehashed")
    with pytest.raises(ValueError, match="application differs"):
        _generate(values)


def test_tampered_actual_evidence_bytes_are_rejected(tmp_path):
    values = _inputs(tmp_path)
    values[6]["evidence"].write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="byte hash mismatch|evidence files do not exactly match"):
        _generate(values)


def test_jointly_rehashed_evidence_and_manifest_cannot_reuse_old_application(tmp_path):
    values = list(_inputs(tmp_path))
    wrapper, paths = deepcopy(values[1]), values[6]
    evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
    evidence["approved_external_artifacts"]["irrelevant-forgery"] = "9" * 64
    _write(paths["evidence"], evidence)
    wrapper["exact_inputs"]["evidence"].update(
        file_sha256=_filehash(paths["evidence"]), canonical_sha256=H(evidence)
    )
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["source_evidence_hash"] = H(evidence)
    _write(paths["manifest"], manifest)
    wrapper["exact_inputs"]["manifest"].update(
        file_sha256=_filehash(paths["manifest"]), canonical_sha256=H(manifest)
    )
    values[1] = wrapper
    with pytest.raises(ValueError, match="application differs"):
        _generate(values)


def test_jointly_rehashed_manifest_cannot_reuse_old_application(tmp_path):
    values = list(_inputs(tmp_path))
    wrapper, paths = deepcopy(values[1]), values[6]
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["operations"][0]["evidence_refs"].append("FORGED-EVIDENCE-REF")
    _write(paths["manifest"], manifest)
    wrapper["exact_inputs"]["manifest"].update(
        file_sha256=_filehash(paths["manifest"]), canonical_sha256=H(manifest)
    )
    values[1] = wrapper
    with pytest.raises(ValueError, match="application differs"):
        _generate(values)


def test_rehashed_result_candidate_and_current_result_are_rejected(tmp_path):
    values = list(_inputs(tmp_path))
    wrapper, paths = deepcopy(values[1]), values[6]
    forged = deepcopy(values[3])
    forged["outer_boundary"]["status"] = "candidate"
    forged["structure_hash"] = compute_v21_structure_hash(forged)
    _write(paths["result_candidate"], forged)
    wrapper["exact_inputs"]["result_candidate"].update(
        file_sha256=_filehash(paths["result_candidate"]),
        structure_hash=forged["structure_hash"],
    )
    values[1] = wrapper
    values[3] = forged
    with pytest.raises(ValueError, match="differs from exact result candidate"):
        _generate(values)


def test_explicit_source_document_and_actual_evidence_set_are_independent_gates(tmp_path):
    values = list(_inputs(tmp_path))
    forged_source = deepcopy(values[0])
    forged_source["outer_boundary"]["status"] = "candidate"
    forged_source["structure_hash"] = compute_v21_structure_hash(forged_source)
    values[0] = forged_source
    with pytest.raises(ValueError, match="explicit source document differs"):
        _generate(values)

    second = tmp_path / "second"
    second.mkdir()
    values = list(_inputs(second))
    duplicate = tmp_path / "duplicate-evidence.json"
    duplicate.parent.mkdir(parents=True, exist_ok=True)
    duplicate.write_bytes(values[6]["evidence"].read_bytes())
    values[2] = [values[6]["evidence"], duplicate]
    with pytest.raises(ValueError, match="evidence files do not exactly match"):
        _generate(values)
