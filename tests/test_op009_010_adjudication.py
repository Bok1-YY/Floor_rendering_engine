from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools.goal_loop_v2.opening_side_candidates import build_opening_side_space_candidate
from tools.goal_loop_v2.op009_010_adjudication import build_op009_010_adjudication, validate_op009_010_adjudication
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
EVIDENCE = ROOT / "reports/op009_op010_geometry_evidence_20260901/op009-op010-evidence.json"


def _inputs():
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    return document, build_opening_side_space_candidate(document), build_target_aware_wall_solids(document)


def test_openings_share_container_but_not_policy_or_space_pair():
    document, side, wall = _inputs()
    packet = build_op009_010_adjudication(document, EVIDENCE, side, wall)
    rows = {row["opening_id"]: row for row in packet["openings"]}
    assert packet["policy_separation"]["different_host_atoms"] is True
    assert packet["policy_separation"]["shared_orientation_only"] is True
    assert packet["policy_separation"]["shared_cut_or_adjacency_policy"] is False
    assert rows["OP009"]["host_candidate"]["atom_id"] == "ATOM-WB005-01"
    assert rows["OP010"]["host_candidate"]["atom_id"] == "ATOM-WB003-03"
    assert rows["OP009"]["host_support_candidate"]["minimum_geometric_jamb_m"] == pytest.approx(1.44905)
    assert rows["OP010"]["host_support_candidate"]["minimum_geometric_jamb_m"] == pytest.approx(2.166257)
    assert rows["OP009"]["host_support_candidate"]["candidate_policy_minimum_m"] == pytest.approx(0.05)
    assert all(row["selected_space_pair"] is None for row in rows.values())
    assert rows["OP009"]["side_space_rankings"][1]["ambiguity"]["ambiguity_class"] == "close_ranking"
    assert rows["OP010"]["side_space_rankings"][0]["ambiguity"]["ambiguity_class"] == "separated_ranking"
    assert packet["semantic_promotion"] is False and packet["build_authorized"] is False


def test_forged_pair_and_promotion_are_rejected():
    import tools.goal_loop_v2.op009_010_adjudication as module

    document, side, wall = _inputs()
    packet = build_op009_010_adjudication(document, EVIDENCE, side, wall)
    forged = deepcopy(packet)
    forged["openings"][0]["selected_space_pair"] = ["rear_balcony", "bedroom_01"]
    forged["candidate_hash"] = module._hash({k: v for k, v in forged.items() if k != "candidate_hash"})
    with pytest.raises(ValueError, match="space pair"):
        validate_op009_010_adjudication(document, EVIDENCE, side, wall, forged)
    promoted = deepcopy(packet)
    promoted["build_authorized"] = True
    with pytest.raises(ValueError, match="promoted"):
        validate_op009_010_adjudication(document, EVIDENCE, side, wall, promoted)


def test_overlay_tampering_fails_closed(tmp_path):
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    copied = tmp_path / EVIDENCE.name
    copied.write_text(json.dumps(evidence), encoding="utf-8")
    for row in evidence["openings"]:
        for artifact in row["artifacts"].values():
            source = Path(artifact["path"])
            shutil.copyfile(source, tmp_path / source.name)
    target = tmp_path / "OP009-full-overlay.png"
    target.write_bytes(target.read_bytes() + b"tamper")
    document, side, wall = _inputs()
    with pytest.raises(ValueError, match="artifact hash drift"):
        build_op009_010_adjudication(document, copied, side, wall)


def test_direct_script_entrypoint_works_outside_repository(tmp_path):
    output = tmp_path / "packet.json"
    script = ROOT / "tools/goal_loop_v2/op009_010_adjudication.py"
    result = subprocess.run(
        [sys.executable, str(script), "--output", str(output)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    packet = json.loads(output.read_text(encoding="utf-8"))
    assert packet["schema"] == "op009-op010-adjudication-candidate-v1"
    assert packet["build_authorized"] is False
