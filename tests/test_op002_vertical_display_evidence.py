from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.goal_loop_v2 import build_op002_vertical_display_evidence as target


def test_evidence_binds_intact_display_and_composite(tmp_path: Path) -> None:
    result = target.build(out_dir=tmp_path)
    assert result["schema"] == "op002-layer3b-display-evidence-v1"
    assert result["image_bindings"]["composite"]["size"] == [5000, 1390]
    assert result["image_bindings"]["top"]["size"] == [1200, 1200]
    assert result["image_bindings"]["front_closeup"]["size"] == [1200, 1200]
    contract = result["display_contract"]
    assert contract["wall_count"] == 35
    assert contract["guide_count"] == 2
    assert contract["opening_cuts"] == 0
    assert contract["opening_elements"] == 0
    assert contract["sill_level_m"]["value"] is None
    assert contract["door_leaf_created"] is False
    assert contract["ifc_opening_created"] is False
    assert result["visual_review_scope"] == "display_clarity_and_nonsemantic_separation_only"
    assert result["semantic_promotion"] is False
    assert result["build_authorized"] is False
    assert result["ready"] is False
    assert target.validate(result, out_dir=tmp_path) == result


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["display_contract"].__setitem__("opening_cuts", 1),
        lambda value: value["display_contract"]["sill_level_m"].__setitem__("value", 0.0),
        lambda value: value["display_contract"].__setitem__("door_leaf_created", True),
        lambda value: value.__setitem__("source_vertical_confirmation", True),
        lambda value: value.__setitem__("semantic_promotion", True),
        lambda value: value.__setitem__("build_authorized", True),
    ],
)
def test_rehashed_evidence_tampering_is_rejected(tmp_path: Path, mutator) -> None:
    canonical = target.build(out_dir=tmp_path)
    candidate = deepcopy(canonical)
    mutator(candidate)
    candidate["candidate_hash"] = target._candidate_hash(
        {key: item for key, item in candidate.items() if key != "candidate_hash"}
    )
    with pytest.raises(ValueError):
        target.validate(candidate, out_dir=tmp_path)


def test_cli_rebuild_is_path_independent(tmp_path: Path) -> None:
    first = tmp_path / "first"
    canonical = target.build(out_dir=first)
    second = tmp_path / "second"
    completed = subprocess.run(
        [
            sys.executable,
            str(target.ROOT / "tools/goal_loop_v2/build_op002_vertical_display_evidence.py"),
            "--out",
            str(second),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stdout + completed.stderr
    generated = json.loads((second / "evidence.json").read_text(encoding="utf-8"))
    assert generated == canonical
    assert completed.stdout.strip() == canonical["candidate_hash"]
    assert (second / "REPORT.md").is_file()
