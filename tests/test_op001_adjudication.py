from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

from tools.goal_loop_v2.opening_side_candidates import build_opening_side_space_candidate
from tools.goal_loop_v2.op001_adjudication import build_op001_adjudication, validate_op001_adjudication
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
EVIDENCE = ROOT / "reports/op001_entrance_evidence_20260901/op001-evidence.json"


def _inputs():
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    return document, build_opening_side_space_candidate(document), build_target_aware_wall_solids(document)


def test_symbol_and_source_cut_claim_do_not_create_exterior_root():
    document, side, wall = _inputs()
    packet = build_op001_adjudication(document, EVIDENCE, side, wall)
    assert packet["source_snapshot"]["kind"] == "entrance_symbol"
    assert packet["source_snapshot"]["effective_void"]["status"] == "confirmed"
    assert packet["distance_only_host_evidence"]["closed_wall_break_proven"] is False
    assert packet["outer_boundary_relation"]["intersects_outer_boundary"] is False
    assert packet["outer_boundary_relation"]["endpoints_inside_confirmed_footprint"] == [True, True]
    assert packet["selected_space_pair"] is None
    assert packet["entrance_confirmation"] is False
    assert packet["exterior_root_confirmation"] is False
    assert packet["build_authorized"] is False


def test_forged_pair_root_cut_or_build_is_rejected():
    document, side, wall = _inputs()
    packet = build_op001_adjudication(document, EVIDENCE, side, wall)
    attacks = [
        ("selected_space_pair", ["common_core_circulation", "lobby"], "space pair"),
        ("exterior_root_confirmation", True, "promoted"),
        ("cut_confirmation", True, "promoted"),
        ("build_authorized", True, "promoted"),
    ]
    for key, value, message in attacks:
        forged = deepcopy(packet)
        forged[key] = value
        with pytest.raises(ValueError, match=message):
            validate_op001_adjudication(document, EVIDENCE, side, wall, forged)


def test_overlay_tampering_fails_closed(tmp_path):
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    copied = tmp_path / EVIDENCE.name
    copied.write_text(json.dumps(evidence), encoding="utf-8")
    for artifact in evidence["artifacts"].values():
        source = Path(artifact["path"])
        shutil.copyfile(source, tmp_path / source.name)
    target = tmp_path / "op001-crop-overlay.png"
    target.write_bytes(target.read_bytes() + b"tamper")
    document, side, wall = _inputs()
    with pytest.raises(ValueError, match="artifact hash drift"):
        build_op001_adjudication(document, copied, side, wall)


def test_direct_entrypoint_works_outside_repository(tmp_path):
    output = tmp_path / "packet.json"
    script = ROOT / "tools/goal_loop_v2/op001_adjudication.py"
    result = subprocess.run([sys.executable, str(script), "--output", str(output)], cwd=tmp_path, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["exterior_root_confirmation"] is False
