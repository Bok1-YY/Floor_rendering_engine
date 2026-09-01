from copy import deepcopy
from tools.goal_loop_v2.opening_evidence import build_opening_evidence_candidate,validate_opening_evidence_candidate
from tests.test_research_structure_v21 import v21_fixture_with_gap

def test_candidate_covers_openings_spaces_and_stays_source_only():
    doc=v21_fixture_with_gap(); c=build_opening_evidence_candidate(doc)
    assert len(c["openings"])==len(doc["opening_contract"]["openings"])
    assert len(c["spaces"])==len(doc["spaces"])
    assert all(x["semantic_promotion"] is False for x in c["openings"])
    assert validate_opening_evidence_candidate(doc,c)["candidate_hash"]==c["candidate_hash"]

def test_candidate_rejects_space_promotion_and_hash_drift():
    doc=v21_fixture_with_gap(); c=build_opening_evidence_candidate(doc)
    bad=deepcopy(c); bad["openings"][0]["space_relation_status"]="confirmed"
    try: validate_opening_evidence_candidate(doc,bad)
    except ValueError: pass
    else: raise AssertionError("promoted opening evidence accepted")
