from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import build_combined_gap_plan as target


def _rehash(candidate: dict) -> dict:
    candidate["candidate_hash"] = target._candidate_hash(
        {key: value for key, value in candidate.items() if key != "candidate_hash"}
    )
    return candidate


def _rehash_plan(plan: dict) -> None:
    plan["variant_hash"] = target._candidate_hash(
        {key: value for key, value in plan.items() if key != "variant_hash"}
    )


def test_combined_plan_counts_bindings_and_fail_closed() -> None:
    result = target.build()
    assert result["schema"] == "combined-gap-plan-v3"
    assert len(result["plans"]) == 9
    assert len(result["variant_artifact_bindings"]) == 9
    assert result["source_wall_atom_count"] == 35
    assert result["untouched_atom_count"] == 26
    assert result["host_piece_count"] == 17
    assert result["expected_wall_piece_count"] == 43
    assert len(set(result["host_atom_ids"])) == 9
    assert result["included_opening_ids"] == [
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
    for key in target.FAIL_CLOSED:
        assert result[key] is False
    assert result["score_effect"] == "none"
    assert result["reproducibility_scope"] == "current_evidence_workspace"
    assert result["portable_bundle"] is False
    assert result["portability_blocker"] == "upstream_manifests_embed_machine_absolute_artifact_paths"
    assert target.validate(result) == result


def test_all_remaining_pieces_are_exact_gap_complements() -> None:
    result = target.build()
    for plan in result["plans"]:
        target._assert_piece_complement(plan)
        gap_lo, gap_hi = sorted(plan["host_parameters"])
        for piece in plan["remaining_host_pieces"]:
            start, end = piece["host_parameter_interval"]
            assert end <= gap_lo + 1e-9 or start >= gap_hi - 1e-9


def test_op003_endpoint_rule_and_op001_projection_are_explicit() -> None:
    result = target.build()
    op003 = result["op003_endpoint_residual_rule"]
    assert op003 == {
        "gap_starts_at_host_parameter_zero": True,
        "zero_length_endpoint_residual_omitted": True,
        "remaining_host_piece_count": 1,
        "short_residual_piece_count_below_0_05m": 0,
    }
    op001 = result["op001_projection_source_distinction"]
    assert op001["projection_mode"] == "orthogonal_projection_within_wall_solid"
    assert op001["segments_are_distinct"] is True
    assert op001["source_gap_segment_m"] != op001["projected_gap_segment_m"]


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["included_opening_ids"].append("OP005"),
        lambda value: value["host_atom_ids"].__setitem__(1, value["host_atom_ids"][0]),
        lambda value: value.__setitem__("host_piece_count", 18),
        lambda value: value["plans"][0].__setitem__("projection_mode", "centerline_collinear"),
        lambda value: value.__setitem__("semantic_promotion", True),
        lambda value: value.__setitem__("build_authorized", True),
    ],
)
def test_rehashed_combined_candidate_tampering_is_rejected(mutator) -> None:
    candidate = deepcopy(target.build())
    mutator(candidate)
    if candidate["plans"][0]["projection_mode"] == "centerline_collinear":
        _rehash_plan(candidate["plans"][0])
    _rehash(candidate)
    with pytest.raises(ValueError):
        target.validate(candidate)


def test_rehashed_nested_piece_overlap_is_rejected() -> None:
    candidate = deepcopy(target.build())
    plan = candidate["plans"][1]
    gap_lo, gap_hi = sorted(plan["host_parameters"])
    plan["remaining_host_pieces"][0]["host_parameter_interval"] = [
        0.0,
        (gap_lo + gap_hi) / 2.0,
    ]
    _rehash_plan(plan)
    _rehash(candidate)
    with pytest.raises(ValueError):
        target.validate(candidate)


def test_rehashed_gap_plan_upstream_drift_is_rejected(tmp_path: Path) -> None:
    gap_plans = json.loads(target.GAP_PLANS.read_text(encoding="utf-8"))
    gap_plans["plans"][0]["host_atom_id"] = gap_plans["plans"][1]["host_atom_id"]
    _rehash_plan(gap_plans["plans"][0])
    gap_plans["candidate_hash"] = target._candidate_hash(
        {key: value for key, value in gap_plans.items() if key != "candidate_hash"}
    )
    path = tmp_path / "plans.json"
    path.write_text(json.dumps(gap_plans), encoding="utf-8")
    with pytest.raises(ValueError):
        target.build(gap_plans_path=path)


def test_rehashed_registered_review_upstream_drift_is_rejected(tmp_path: Path) -> None:
    review = json.loads(target.REGISTERED_REVIEW.read_text(encoding="utf-8"))
    review["accepted_for_isolated_xy_variant"].append("OP005")
    review["candidate_hash"] = target._candidate_hash(
        {key: value for key, value in review.items() if key != "candidate_hash"}
    )
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError):
        target.build(registered_review_path=path)


def test_layer1_manifest_drift_is_rejected(tmp_path: Path) -> None:
    manifest = json.loads(target.LAYER1_MANIFEST.read_text(encoding="utf-8"))
    manifest["wall_object_count"] = 34
    path = tmp_path / "artifact_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        target.build(layer1_manifest_path=path)


def test_isolated_variant_manifest_drift_is_rejected(tmp_path: Path) -> None:
    variants = tmp_path / "variants"
    for opening_id in target.EXPECTED_INCLUDED:
        destination = variants / opening_id
        destination.mkdir(parents=True)
        shutil.copy2(
            target.VARIANTS_BASE / opening_id / "artifact_manifest.json",
            destination / "artifact_manifest.json",
        )
    manifest_path = variants / "OP001" / "artifact_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["wall_piece_count"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        target.build(variants_base=variants)


def test_cli_rebuilds_identical_candidate(tmp_path: Path) -> None:
    output = tmp_path / "combined" / "plan.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_combined_gap_plan.py"),
            "--output",
            str(output),
        ],
        cwd=target.ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stdout + completed.stderr
    generated = json.loads(output.read_text(encoding="utf-8"))
    assert generated == target.build()
    assert completed.stdout.strip() == generated["candidate_hash"]
    assert (output.parent / "REPORT.md").is_file()
    assert (output.parent / "REVIEW_CARD_ZH.md").is_file()
