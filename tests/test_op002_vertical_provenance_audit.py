from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import build_op002_vertical_provenance_audit as target


def test_vertical_values_are_classified_and_fail_closed() -> None:
    result = target.build()
    assert result["schema"] == "op002-vertical-provenance-audit-v2"
    assert result["opening_candidate_state"] == {
        "opening_status": "candidate",
        "source_observation_status": "candidate",
        "effective_void_status": "candidate",
        "assumption_ids": ["ASSUME-Z-RESEARCH"],
    }
    wall = result["vertical_parameters"]["wall_height_m"]
    head = result["vertical_parameters"]["head_m"]
    sill = result["vertical_parameters"]["sill_m"]
    assert wall["observed_value"] == pytest.approx(2.8)
    assert wall["provenance_class"] == "research_assumption"
    assert wall["source_explicit"] is False
    assert head["observed_value"] == pytest.approx(2.1)
    assert head["provenance_class"] == "research_assumption"
    assert head["assumption_id"] == "ASSUME-Z-RESEARCH"
    assert head["source_explicit"] is False
    assert sill["observed_value"] == pytest.approx(0.0)
    assert sill["provenance_class"] == "unsupported_candidate_value"
    assert sill["assumption_id"] is None
    assert sill["treatment"] == "unknown"
    assert sill["usable_for_reversible_research_display"] is False
    assert result["vertical_evidence"]["explicit_no_height_promotion_disclosure"] is True
    assert result["vertical_evidence"]["source_image_sha256"] == target._file_hash(target.SOURCE_IMAGE)
    expected_pixels = [
        [1101.5000000000002, 911.9999999999998],
        [1101.5000000000002, 1033.9999999999998],
    ]
    for actual, expected in zip(
        result["vertical_evidence"]["source_segment_px"],
        expected_pixels,
    ):
        assert actual == pytest.approx(expected)
    assert result["vertical_evidence_supports_height"] is False
    assert result["visual_subtype_advisory_is_vertical_authority"] is False
    assert result["isolated_blender_research_display"]["policy_status"] == "pending_policy_guardian"
    assert result["isolated_blender_research_display"]["sill_m"] is None
    assert result["isolated_blender_research_display"]["opening_geometry_authorized"] is False
    for key in target.FAIL_CLOSED:
        assert result[key] is False
    assert result["score_effect"] == "none"
    assert target.validate(result) == result


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["vertical_parameters"]["head_m"].__setitem__("observed_value", 2.2),
        lambda value: value["vertical_parameters"]["head_m"].__setitem__("provenance_class", "source_explicit"),
        lambda value: value["vertical_parameters"]["sill_m"].__setitem__("treatment", "accepted"),
        lambda value: value["assumption_registry_entry"]["value"].__setitem__("sill_m", 0.0),
        lambda value: value.__setitem__("visual_subtype_advisory_is_vertical_authority", True),
        lambda value: value.__setitem__("source_vertical_confirmation", True),
        lambda value: value.__setitem__("effective_void_confirmation", True),
        lambda value: value.__setitem__("build_authorized", True),
    ],
)
def test_rehashed_vertical_candidate_tampering_is_rejected(mutator) -> None:
    candidate = deepcopy(target.build())
    mutator(candidate)
    candidate["candidate_hash"] = target._candidate_hash(
        {key: item for key, item in candidate.items() if key != "candidate_hash"}
    )
    with pytest.raises(ValueError):
        target.validate(candidate)


def test_subtype_bundle_upstream_drift_is_rejected(tmp_path: Path) -> None:
    subtype = json.loads(target.SUBTYPE_BUNDLE.read_text(encoding="utf-8"))
    subtype["visual_subtype_candidate"] = "window_or_fixed_glazing"
    subtype["candidate_hash"] = target._candidate_hash(
        {key: item for key, item in subtype.items() if key != "candidate_hash"}
    )
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(subtype), encoding="utf-8")
    with pytest.raises(ValueError):
        target.build(subtype_bundle_path=path)


def test_vertical_evidence_disclosure_drift_is_rejected(tmp_path: Path) -> None:
    evidence = json.loads(target.VERTICAL_EVIDENCE.read_text(encoding="utf-8"))
    evidence["observations"] = [
        item
        for item in evidence["observations"]
        if "does not promote door type" not in item
    ]
    path = tmp_path / "vertical-evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="no-height-promotion"):
        target.build(vertical_evidence_path=path)


@pytest.mark.parametrize("field", ["source_sha256", "source_segment_px"])
def test_vertical_evidence_source_image_and_pixel_drift_is_rejected(
    tmp_path: Path,
    field: str,
) -> None:
    evidence = json.loads(target.VERTICAL_EVIDENCE.read_text(encoding="utf-8"))
    if field == "source_sha256":
        evidence[field] = "0" * 64
    else:
        evidence[field][0][0] += 5.0
    path = tmp_path / "vertical-evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError):
        target.build(vertical_evidence_path=path)


def test_vertical_evidence_artifacts_relocate_with_evidence(tmp_path: Path) -> None:
    relocated = tmp_path / "vertical"
    relocated.mkdir()
    shutil.copy2(target.VERTICAL_EVIDENCE, relocated / target.VERTICAL_EVIDENCE.name)
    evidence = json.loads(target.VERTICAL_EVIDENCE.read_text(encoding="utf-8"))
    for artifact in evidence["artifacts"].values():
        source = Path(artifact["path"])
        shutil.copy2(source, relocated / source.name)
    result = target.build(vertical_evidence_path=relocated / target.VERTICAL_EVIDENCE.name)
    assert result == target.build()


def test_cli_rebuilds_identical_candidate(tmp_path: Path) -> None:
    output = tmp_path / "audit" / "audit.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_op002_vertical_provenance_audit.py"),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stdout + completed.stderr
    generated = json.loads(output.read_text(encoding="utf-8"))
    assert generated == target.build()
    assert completed.stdout.strip() == generated["candidate_hash"]
    assert (output.parent / "REPORT.md").is_file()
    assert (output.parent / "REVIEW_CARD_ZH.md").is_file()
