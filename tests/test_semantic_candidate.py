import pytest
from tools.goal_loop_v2.semantic_candidate import build_semantic_candidate, validate_semantic_candidate

def _doc():
    import json
    p = "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
    return json.load(open(p, encoding="utf-8"))

def _evidence(doc):
    return {"schema":"opening-wall-space-evidence-candidate-v1", "candidate_hash":"e" * 64}

def test_all_openings_fail_closed():
    doc = _doc(); ev = _evidence(doc)
    ge = {o["id"]: {"geometry_finding":"candidate_geometry"} for o in doc["opening_contract"]["openings"]}
    c = build_semantic_candidate(doc, ev, ge, gemini_advisories={"OP002":{"observed_type":"door", "confidence":0.7}})
    assert len(c["openings"]) == len(doc["opening_contract"]["openings"])
    assert c["semantic_promotion"] is False and c["build_authorized"] is False

def test_rejects_missing_opening():
    doc = _doc(); ev = _evidence(doc); ge = {o["id"]: {} for o in doc["opening_contract"]["openings"]}
    ge.pop(next(iter(ge)))
    with pytest.raises(ValueError, match="cover exactly"):
        build_semantic_candidate(doc, ev, ge)
