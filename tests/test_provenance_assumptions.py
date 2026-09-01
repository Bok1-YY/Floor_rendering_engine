from tools.goal_loop_v2.provenance_assumptions import build_assumption_registry, validate_assumption_registry
from tests.test_research_structure_v21 import v21_fixture_with_gap

def test_registry_is_explicit_and_non_authorizing():
    doc = v21_fixture_with_gap()
    reg = build_assumption_registry(doc)
    assert validate_assumption_registry(reg, doc)["registry_hash"] == reg["registry_hash"]
    assert reg["policy"]["build_authorized"] is False
    assert any(x["id"] == "ASSUME-OPENING-HEIGHTS-RESEARCH" for x in reg["assumptions"])
    assert any(x["id"] == "UNRESOLVED-OP011" for x in reg["unresolved"])

def test_registry_rejects_hash_drift():
    doc = v21_fixture_with_gap(); reg = build_assumption_registry(doc)
    reg["unresolved"][0]["message"] = "tampered"
    try: validate_assumption_registry(reg, doc)
    except ValueError: pass
    else: raise AssertionError("hash drift accepted")
