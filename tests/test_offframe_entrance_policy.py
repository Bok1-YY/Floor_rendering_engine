from tools.goal_loop_v2.offframe_entrance_policy import build_policy, validate_policy
from tools.goal_loop_v2.source_contract_report import _score_source_contract
import json
from pathlib import Path
from tests.test_research_structure_v21 import v21_fixture_with_gap

def test_offframe_is_research_only_and_does_not_mutate_source(tmp_path):
    doc = v21_fixture_with_gap(); src = tmp_path / "source.json"; src.write_text("{}", encoding="utf-8")
    before = dict(doc); c = build_policy(doc, src)
    assert validate_policy(c, doc)["policy"]["bim_ifc_authorized"] is False
    assert c["research_use"]["allowed"] is True
    assert c["frame_scope"]["entrance_opening_id"] is None
    assert doc == before

def test_policy_rejects_geometry_or_authorization_tampering(tmp_path):
    doc = v21_fixture_with_gap(); src = tmp_path / "source.json"; src.write_text("{}", encoding="utf-8")
    c = build_policy(doc, src); c["frame_scope"]["entrance_geometry"] = [[1, 2], [3, 4]]
    try: validate_policy(c, doc)
    except ValueError: pass
    else: raise AssertionError("invented off-frame geometry accepted")

def test_research_only_policy_never_raises_score_or_build_readiness(tmp_path):
    doc = v21_fixture_with_gap(); src = tmp_path / "source.json"
    src.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    policy = build_policy(doc, src)
    contract = json.loads((Path(__file__).parents[1] / "docs" / "goal_loop_v2" / "goal-contract.json").read_text(encoding="utf-8"))
    contract["samples"] = ["fixture-v2"]
    base_report, base_detail = _score_source_contract(doc, contract, {"ready": False}, {"lineage_type": "test"})
    policy_report, policy_detail = _score_source_contract(doc, contract, {"ready": False}, {"lineage_type": "test"}, None, policy)
    assert policy_report == base_report
    assert policy_detail["weighted_score"] == base_detail["weighted_score"]
    assert policy_detail["offframe_policy"]["research_only"] is True
    assert policy_detail["offframe_policy"]["build_authorized"] is False
