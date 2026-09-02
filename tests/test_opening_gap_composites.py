from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.fastloop_research.contract import canonical_json
import tools.goal_loop_v2.build_opening_gap_composites as module


ROOT = Path(__file__).resolve().parents[1]


def _rehash(value):
    value["candidate_hash"] = hashlib.sha256(canonical_json({key: item for key, item in value.items() if key != "candidate_hash"})).hexdigest()


def test_nine_unique_variant_composites_and_source_unchanged(tmp_path):
    source_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (module.ROOT / "reports/opening_xy_clean_evidence_20260902").glob("OP*-raw-crop.png")}
    result = module.build(out_dir=tmp_path / "composites")
    assert result["opening_ids"] == ["OP001", "OP002", "OP003", "OP004", "OP006", "OP007", "OP008", "OP009", "OP010"]
    assert len({row["model_closeup"]["path"] for row in result["rows"]}) == 9
    assert all(Path(row["model_closeup"]["path"]).parent.name == row["opening_id"] for row in result["rows"])
    assert all(row["composite"]["size"] == [1600, 900] for row in result["rows"])
    assert all(row["source_crop"]["source_pixels_untouched"] is True for row in result["rows"])
    assert source_hashes == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (module.ROOT / "reports/opening_xy_clean_evidence_20260902").glob("OP*-raw-crop.png")}
    assert module.validate(result, out_dir=tmp_path / "composites") == result


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["opening_ids"].append("OP011"),
        lambda value: value["rows"][0]["model_closeup"].__setitem__("path", value["rows"][1]["model_closeup"]["path"]),
        lambda value: value["rows"][0]["composite"].__setitem__("sha256", "0" * 64),
        lambda value: value.__setitem__("build_authorized", True),
    ],
)
def test_rehashed_injection_reuse_artifact_and_promotion_attacks_fail(tmp_path, mutate):
    result = deepcopy(module.build(out_dir=tmp_path / "composites"))
    mutate(result)
    _rehash(result)
    with pytest.raises(ValueError):
        module.validate(result, out_dir=tmp_path / "composites")


def test_cli_from_temporary_cwd(tmp_path):
    script = ROOT / "tools/goal_loop_v2/build_opening_gap_composites.py"
    output = tmp_path / "cli-composites"
    completed = subprocess.run([sys.executable, str(script), "--out", str(output)], cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    assert (output / "composites.json").is_file()
