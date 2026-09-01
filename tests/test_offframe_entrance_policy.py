from tools.goal_loop_v2.offframe_entrance_policy import build_policy, validate_policy
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
