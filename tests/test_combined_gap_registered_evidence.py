from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import build_combined_gap_registered_evidence as target


@pytest.fixture(scope="module")
def evidence(tmp_path_factory):
    out = tmp_path_factory.mktemp("combined_registered")
    result = target.build(out_dir=out)
    return out, result


def test_full_and_local_windows_are_registered_to_combined_model(evidence) -> None:
    out, result = evidence
    assert result["schema"] == "combined-gap-registered-evidence-v2"
    assert result["opening_ids"] == [
        "OP001",
        "OP002",
        "OP003",
        "OP004",
        "OP006",
        "OP007",
        "OP008",
        "OP009",
        "OP010",
    ]
    assert result["excluded_opening_ids"] == [
        "OP005",
        "OP011",
        "PORTAL-WB011-WB006-01",
        "OP012",
    ]
    assert len(result["rows"]) == 9
    assert result["full_plan"]["center_registration_error_px"] <= 1e-6
    assert result["full_plan"]["registered_source"]["size"] == [1200, 1200]
    assert result["full_plan"]["combined_model_top"]["size"] == [1200, 1200]
    assert result["full_plan"]["composite"]["size"] == [3760, 1370]
    assert result["contact_sheet"]["size"] == [3000, 1650]
    assert all(row["center_registration_error_px"] <= 1e-6 for row in result["rows"])
    assert all(row["registered_source"]["size"] == [1200, 1200] for row in result["rows"])
    assert all(row["combined_model_closeup"]["size"] == [1200, 1200] for row in result["rows"])
    assert all(row["composite"]["size"] == [2520, 1370] for row in result["rows"])
    assert all(
        "artifacts/goal_loop_v2/1308/research_combined_xy_gap_v001/" in row["combined_model_closeup"]["path"]
        and "opening_xy_variants_v001" not in row["combined_model_closeup"]["path"]
        for row in result["rows"]
    )
    assert result["model_scope"] == "single_combined_43_piece_wall_set"
    assert result["evidence_plan_portable"] is False
    assert result["semantic_promotion"] is False
    assert result["build_authorized"] is False
    assert target.validate(result, out_dir=out, rebuild=False) == result


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.__setitem__("semantic_promotion", True),
        lambda value: value["rows"][0]["metric_window"]["center_m"].__setitem__(0, 999.0),
        lambda value: value["rows"][0]["registered_source"].__setitem__("sha256", "0" * 64),
        lambda value: value["rows"][0]["combined_model_closeup"].__setitem__(
            "path",
            "artifacts/goal_loop_v2/1308/opening_xy_variants_v001/OP001/1308_OP001_gap_variant_closeup_top.png",
        ),
    ],
)
def test_rehashed_evidence_tampering_is_rejected(evidence, mutator) -> None:
    out, canonical = evidence
    candidate = deepcopy(canonical)
    mutator(candidate)
    candidate["candidate_hash"] = target._candidate_hash(
        {key: value for key, value in candidate.items() if key != "candidate_hash"}
    )
    with pytest.raises((ValueError, FileNotFoundError)):
        target.validate(candidate, out_dir=out, rebuild=False)


def test_cli_rebuild_is_path_independent_and_identical(evidence, tmp_path: Path) -> None:
    _, canonical = evidence
    out = tmp_path / "cli"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_combined_gap_registered_evidence.py"),
            "--out",
            str(out),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stdout + completed.stderr
    generated = json.loads((out / "evidence.json").read_text(encoding="utf-8"))
    assert generated == canonical
    assert completed.stdout.strip() == canonical["candidate_hash"]
    assert (out / "REPORT.md").is_file()
