import json
from pathlib import Path

P = Path(__file__).parents[1] / "reports/op005_op006_geometry_evidence_20260901/op005-op006-evidence.json"

def test_op005_op006_registration_and_fail_closed_flags():
    d = json.loads(P.read_text(encoding="utf-8"))
    assert {x["opening_id"] for x in d["openings"]} == {"OP005", "OP006"}
    assert all(x["registration"]["max_endpoint_error_px"] <= 1.0 for x in d["openings"])
    assert d["semantic_promotion"] is False
    assert d["build_authorized"] is False
    assert d["ready"] is False

def test_op005_has_no_current_host_candidate_but_op006_does():
    d = {x["opening_id"]: x for x in json.loads(P.read_text(encoding="utf-8"))["openings"]}
    assert d["OP005"]["host_wall_candidates"] == []
    assert d["OP006"]["host_wall_candidates"][0]["atom_id"] == "ATOM-WB007-02"
