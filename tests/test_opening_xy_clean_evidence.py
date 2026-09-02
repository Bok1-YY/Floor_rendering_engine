from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image
import pytest

from tools.fastloop_research.contract import canonical_json
import tools.goal_loop_v2.build_opening_xy_clean_evidence as module


ROOT = Path(__file__).resolve().parents[1]


def _rehash(candidate):
    candidate["candidate_hash"] = hashlib.sha256(canonical_json({key: value for key, value in candidate.items() if key != "candidate_hash"})).hexdigest()


def test_exact_coverage_pixels_clearance_and_rebuild(tmp_path):
    out = tmp_path / "evidence"
    result = module.build(out_dir=out)
    assert tuple(result["opening_ids"]) == module.EXPECTED_INCLUDED
    assert [row["opening_id"] for row in result["exclusions"]] == ["OP005", "OP011", "PORTAL-WB011-WB006-01", "OP012"]
    source = Image.open(module.RAW_IMAGE).convert("RGB")
    for row in result["openings"]:
        crop = Image.open(row["artifacts"]["raw_crop"]["path"]).convert("RGB")
        expected = source.crop(tuple(row["crop_box_px"]))
        assert (crop.size, crop.tobytes()) == (expected.size, expected.tobytes())
        assert row["locator_geometry"]["minimum_clearance_px"] >= 30
        assert row["registration"]["max_endpoint_error_px"] <= 1
    assert module.validate(result, out_dir=out) == result


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["opening_ids"].append("OP011"),
        lambda value: value["exclusions"].pop(),
        lambda value: value.__setitem__("build_authorized", True),
        lambda value: value["openings"][0].__setitem__("host_atom_id", "FORGED"),
        lambda value: value["openings"][0]["locator_geometry"].__setitem__("minimum_clearance_px", 999),
    ],
)
def test_rehashed_injection_geometry_and_promotion_attacks_fail(tmp_path, mutate):
    out = tmp_path / "evidence"
    forged = deepcopy(module.build(out_dir=out))
    mutate(forged)
    _rehash(forged)
    with pytest.raises(ValueError):
        module.validate(forged, out_dir=out)


def test_rehashed_cut_matrix_drift_fails_upstream_validation(tmp_path):
    matrix = json.loads(module.CUT_MATRIX.read_text(encoding="utf-8"))
    matrix["openings"][0]["cuttable"] = False
    matrix["candidate_hash"] = "0" * 64
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(matrix), encoding="utf-8")
    with pytest.raises(ValueError):
        module.build(cut_matrix_path=path, out_dir=tmp_path / "out")


def test_cli_runs_from_temporary_cwd(tmp_path):
    script = ROOT / "tools/goal_loop_v2/build_opening_xy_clean_evidence.py"
    output = tmp_path / "cli-output"
    completed = subprocess.run([sys.executable, str(script), "--out", str(output)], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    assert (output / "evidence.json").is_file()
    assert json.loads((output / "evidence.json").read_text(encoding="utf-8"))["candidate_hash"] in completed.stdout
