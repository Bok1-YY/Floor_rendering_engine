from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import build_targeted_subtype_bundle as target


ROOT = Path(__file__).resolve().parents[1]


def args(opening_id: str) -> dict:
    slug = opening_id.lower()
    return {
        "original_bundle_path": ROOT / f"reports/{slug}_clean_subtype_20260903/bundle.json",
        "original_result_path": ROOT / f"reports/{slug}_clean_subtype_20260903/selected-result.json",
        "targeted_evidence_path": ROOT / f"reports/{slug}_targeted_subtype_evidence_20260903/evidence.json",
        "targeted_result_path": ROOT / f"reports/{slug}_targeted_subtype_20260903/targeted-selected-result.json",
    }


@pytest.mark.parametrize("opening_id", ["OP006", "OP008"])
def test_targeted_bundle_resolves_neighbor_cue_with_history(opening_id: str) -> None:
    result = target.build(opening_id, **args(opening_id))
    assert result["original_advisory"]["preserved_as_history"] is True
    assert result["visual_subtype_candidate"] == "door"
    assert result["neighboring_visual_cues_present"] is False
    assert result["target_cue_isolated"] is True
    assert result["subtype_use_status"] == "resolved_after_tighter_crop"
    assert result["accepted_for_downstream_research_with_quarantine"] is True
    assert result["source_subtype_confirmation"] is False
    assert result["vertical_parameters_reviewed"] is False
    assert result["build_authorized"] is False
    assert target.validate(result, opening_id, **args(opening_id)) == result


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["original_advisory"].__setitem__("preserved_as_history", False),
        lambda value: value.__setitem__("neighboring_visual_cues_present", True),
        lambda value: value.__setitem__("target_cue_isolated", False),
        lambda value: value.__setitem__("subtype_use_status", "accepted_without_review"),
        lambda value: value.__setitem__("source_subtype_confirmation", True),
        lambda value: value.__setitem__("build_authorized", True),
    ],
)
def test_rehashed_targeted_bundle_tampering_is_rejected(mutator) -> None:
    candidate = target.build("OP006", **args("OP006"))
    mutator(candidate)
    candidate["candidate_hash"] = target._candidate_hash({key: value for key, value in candidate.items() if key != "candidate_hash"})
    with pytest.raises(ValueError):
        target.validate(candidate, "OP006", **args("OP006"))


def test_cli_rebuilds_identical_bundle(tmp_path: Path) -> None:
    canonical = target.build("OP006", **args("OP006"))
    out = tmp_path / "bundle"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_targeted_subtype_bundle.py"),
            "--opening-id", "OP006",
            "--original-bundle", str(args("OP006")["original_bundle_path"]),
            "--original-result", str(args("OP006")["original_result_path"]),
            "--targeted-evidence", str(args("OP006")["targeted_evidence_path"]),
            "--targeted-result", str(args("OP006")["targeted_result_path"]),
            "--out", str(out),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stdout + completed.stderr
    assert json.loads((out / "bundle.json").read_text(encoding="utf-8")) == canonical
