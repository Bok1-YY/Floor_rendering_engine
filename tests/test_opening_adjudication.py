import pytest
from tools.goal_loop_v2.opening_adjudication import build_adjudication_candidate, validate_adjudication_candidate

def _doc():
    import json
    from pathlib import Path
    return json.loads(Path("data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json").read_text(encoding="utf-8"))

def _ev(doc):
    from tools.goal_loop_v2.opening_evidence import build_opening_evidence_candidate
    return build_opening_evidence_candidate(doc)

def test_adjudication_is_bounded_and_covers_targets():
    doc=_doc(); ev=_ev(doc)
    geom={x:{"geometry_finding":"exact_host" if x=="OP002" else "unresolved_geometry"} for x in ("OP001","OP002","OP007","OP008")}
    out=build_adjudication_candidate(doc,ev,geom,gemini_observations={"OP002":{"observed_type":"hinged_door","confidence":0.6,"notes":"advisory"}})
    assert out["status"]=="pending_independent_review"
    assert out["semantic_promotion"] is False and out["build_authorized"] is False
    assert [x["opening_id"] for x in out["targets"]]==["OP001","OP002","OP007","OP008"]

def test_adjudication_rejects_semantic_geometry_claim():
    doc=_doc(); ev=_ev(doc); geom={x:{"geometry_finding":"unresolved_geometry"} for x in ("OP001","OP002","OP007","OP008")}
    out=build_adjudication_candidate(doc,ev,geom)
    out["targets"][0]["geometry"]["geometry_finding"]="confirmed_door"
    out["candidate_hash"]="0"*64
    with pytest.raises(ValueError): validate_adjudication_candidate(doc,ev,out)
