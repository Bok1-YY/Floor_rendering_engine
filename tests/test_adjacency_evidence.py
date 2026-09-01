import copy
import pytest
from tools.goal_loop_v2.adjacency_evidence import build_adjacency_evidence_candidate, validate_adjacency_evidence_candidate

def _fixture():
    doc = {"structure_hash": "x", "spaces": [{"id": "A"}, {"id": "B"}]}
    opening = {"schema": "opening-wall-space-evidence-candidate-v1", "source_structure_hash": "x", "openings": [{"opening_id": "OP1", "side_a_space_id": "A", "side_b_space_id": "B"}]}
    return doc, opening

def test_candidate_is_fail_closed_and_complete(monkeypatch):
    import tools.goal_loop_v2.adjacency_evidence as mod
    monkeypatch.setattr(mod, "validate_v21_document", lambda d: d)
    doc, opening = _fixture(); c = build_adjacency_evidence_candidate(doc, opening)
    assert c["status"] == "pending_independent_review"
    assert c["semantic_promotion"] is False and c["build_authorized"] is False
    assert c["edges"][0]["traversability_status"] == "unresolved"
    assert c["roots"][0]["status"] == "unresolved"
    validate_adjacency_evidence_candidate(doc, opening, c)

def test_validator_rejects_path_or_edge_confirmation(monkeypatch):
    import tools.goal_loop_v2.adjacency_evidence as mod
    monkeypatch.setattr(mod, "validate_v21_document", lambda d: d)
    doc, opening = _fixture(); c = build_adjacency_evidence_candidate(doc, opening)
    bad = copy.deepcopy(c); bad["edges"][0]["path_trace"] = [["ROOT-EXTERIOR", "A"]]
    bad["candidate_hash"] = "0" * 64
    with pytest.raises(ValueError): validate_adjacency_evidence_candidate(doc, opening, bad)
