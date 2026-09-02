from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.fastloop_research.contract import canonical_json
import tools.goal_loop_v2.build_source_connectivity_defect_candidate as module


ROOT = Path(__file__).resolve().parents[1]


def test_current_components_edges_and_modes_are_fail_closed():
    result = module.build()
    observed = {tuple(item["space_ids"]): item for item in result["unreachable_components"]}
    assert result["unreachable_space_ids"] == ["bedroom_02", "dry_balcony", "north_toilet"]
    assert observed[("bedroom_02", "north_toilet")]["internal_candidate_opening_ids"] == ["OP004"]
    assert observed[("dry_balcony",)]["boundary_candidate_opening_ids"] == ["OP011"]
    edges = {edge["opening_id"]: edge for edge in result["candidate_edges"]}
    assert edges["OP004"]["internal_to_unreachable"] is True
    assert edges["OP011"]["crosses_reachable_component"] is True
    assert edges["OP011"]["listed_in_candidate_graph"] is False
    assert edges["OP012"]["counted_for_components"] is False
    assert result["modes"]["functional_bim_connectivity"] == "blocked_by_source_ambiguity"
    assert result["build_authorized"] is False and result["score_effect"] == "none"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda item: item.__setitem__("build_authorized", True), "drift"),
        (lambda item: next(e for e in item["candidate_edges"] if e["opening_id"] == "OP011").__setitem__("confirmed_admitted", True), "drift"),
        (lambda item: next(e for e in item["candidate_edges"] if e["opening_id"] == "OP012").__setitem__("counted_for_components", True), "drift"),
        (lambda item: item["unreachable_components"][0].__setitem__("classification", "confirmed_reachable"), "drift"),
    ],
)
def test_rehashed_candidate_attacks_are_rejected(mutate, message):
    forged = deepcopy(module.build())
    mutate(forged)
    forged["candidate_hash"] = hashlib.sha256(
        canonical_json({key: value for key, value in forged.items() if key != "candidate_hash"})
    ).hexdigest()
    with pytest.raises(ValueError, match=message):
        module.validate(forged)


def test_rehashed_upstream_reachability_drift_is_rejected(tmp_path, monkeypatch):
    upstream = json.loads(module.V3.read_text(encoding="utf-8"))
    upstream["tiers"][-1]["unreachable_scope_space_ids"] = ["dry_balcony"]
    upstream["candidate_hash"] = hashlib.sha256(
        canonical_json({key: value for key, value in upstream.items() if key != "candidate_hash"})
    ).hexdigest()
    path = tmp_path / "forged-v3.json"
    path.write_text(json.dumps(upstream), encoding="utf-8")
    monkeypatch.setattr(module, "V3", path)
    with pytest.raises(ValueError):
        module.build()


def test_direct_script_runs_from_temporary_cwd(tmp_path):
    script = ROOT / "tools/goal_loop_v2/build_source_connectivity_defect_candidate.py"
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert module.build()["candidate_hash"] in completed.stdout
