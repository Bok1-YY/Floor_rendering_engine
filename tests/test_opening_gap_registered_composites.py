from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from tools.fastloop_research.contract import canonical_json
import tools.goal_loop_v2.build_opening_gap_registered_composites as module


ROOT = Path(__file__).resolve().parents[1]


def _rehash(value):
    value["candidate_hash"] = hashlib.sha256(canonical_json({key: item for key, item in value.items() if key != "candidate_hash"})).hexdigest()


def test_nine_same_metric_windows_and_source_integrity(tmp_path):
    source_hash = hashlib.sha256(module.SOURCE.read_bytes()).hexdigest()
    raw_hash = hashlib.sha256((module.ROOT / "data/goal_loop_v2/references/1308/canonical-raw-portrait.png").read_bytes()).hexdigest()
    result = module.build(out_dir=tmp_path / "registered")
    assert result["opening_ids"] == ["OP001", "OP002", "OP003", "OP004", "OP006", "OP007", "OP008", "OP009", "OP010"]
    assert all(row["registered_source"]["size"] == row["model_closeup"]["size"] == [1200, 1200] for row in result["rows"])
    assert all(row["metric_window"]["meters_per_pixel"] == pytest.approx(row["metric_window"]["ortho_scale_m"] / 1200) for row in result["rows"])
    assert all(row["center_registration_error_px"] <= 1e-6 for row in result["rows"])
    assert all(row["composite"]["size"] == [2520, 1370] for row in result["rows"])
    assert hashlib.sha256(module.SOURCE.read_bytes()).hexdigest() == source_hash
    assert hashlib.sha256((module.ROOT / "data/goal_loop_v2/references/1308/canonical-raw-portrait.png").read_bytes()).hexdigest() == raw_hash
    assert module.validate(result, out_dir=tmp_path / "registered") == result


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["opening_ids"].append("OP011"),
        lambda value: value["rows"][0]["metric_window"].__setitem__("ortho_scale_m", 99),
        lambda value: value["rows"][0].__setitem__("center_registration_error_px", 2),
        lambda value: value["rows"][0]["model_closeup"].__setitem__("path", value["rows"][1]["model_closeup"]["path"]),
        lambda value: value.__setitem__("build_authorized", True),
    ],
)
def test_rehashed_scale_registration_reuse_and_promotion_attacks_fail(tmp_path, mutate):
    result = deepcopy(module.build(out_dir=tmp_path / "registered"))
    mutate(result)
    _rehash(result)
    with pytest.raises(ValueError):
        module.validate(result, out_dir=tmp_path / "registered")


def test_cli_from_temporary_cwd(tmp_path):
    script = ROOT / "tools/goal_loop_v2/build_opening_gap_registered_composites.py"
    output = tmp_path / "cli-registered"
    completed = subprocess.run([sys.executable, str(script), "--out", str(output)], cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    assert (output / "registered-composites.json").is_file()
