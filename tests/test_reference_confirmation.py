from __future__ import annotations

from copy import deepcopy

import pytest

from tests.test_research_structure_v21 import v21_fixture_with_gap
from tools.fastloop_research.v21_contract import compute_v21_structure_hash
from tools.goal_loop_v2.reference_confirmation import apply_authorized_verdict,build_verdict_candidate


def _document():
    document=v21_fixture_with_gap()
    document["unresolved_issues"].append({"id":"ISSUE-GEOMETRY","severity":"hard","category":"source_geometry","entity_refs":["PORTAL-GAP"],"status":"open","message":"geometry pending","blocks_reference_freeze":True,"blocks_build":True,"evidence_refs":["VIEW-EVIDENCE"]})
    document["structure_hash"]=compute_v21_structure_hash(document);return document


def _candidate(document):
    decisions=[{"id":"DECIDE-GAP","issue_id":"ISSUE-GEOMETRY","evidence_refs":["VIEW-EVIDENCE"],"decision":"confirm_geometry","allowed_entity_ids":["PORTAL-GAP"],"operations":[{"operation":"confirm_gap_geometry","opening_id":"PORTAL-GAP","status":"confirmed"}]}]
    return build_verdict_candidate(document,["a"*64],decisions)


def _authorize(candidate):
    return {"schema":"reference-confirmation-verdict-v1","candidate":candidate,"candidate_hash":candidate["candidate_hash"],"authority":"independent_reference_reviewer","verdict":"authorize_exact_reference_geometry","build_authorized":False}


def test_pending_candidate_cannot_apply_until_exact_independent_authorization():
    document=_document();candidate=_candidate(document)
    with pytest.raises(ValueError):apply_authorized_verdict(document,candidate)
    result,report=apply_authorized_verdict(document,_authorize(candidate))
    portal=next(row for row in result["opening_contract"]["openings"] if row["id"]=="PORTAL-GAP")
    assert portal["source_observation"]["status"]==portal["effective_void"]["status"]=="confirmed"
    assert portal["status"]=="candidate" and portal["traversable"] is True
    assert result["adjacency_truth"]==document["adjacency_truth"]
    assert report["ready"] is False


def test_forged_candidate_stale_hash_and_forged_report_are_rejected():
    document=_document();candidate=_candidate(document)
    forged=_authorize(deepcopy(candidate));forged["candidate"]["decisions"][0]["operations"][0]["opening_id"]="OTHER"
    with pytest.raises(ValueError,match="candidate hash drift"):apply_authorized_verdict(document,forged)
    stale=deepcopy(document);stale["outer_boundary"]["status"]="candidate";stale["structure_hash"]=compute_v21_structure_hash(stale)
    with pytest.raises(ValueError,match="stale"):apply_authorized_verdict(stale,_authorize(candidate))
    extra=_authorize(candidate);extra["report"]={"ready":True}
    with pytest.raises(ValueError,match="invalid independently authorized"):apply_authorized_verdict(document,extra)


def test_semantic_adjacency_build_status_and_unrelated_promotions_are_not_operations():
    document=_document()
    for operation in ({"operation":"set_traversable","opening_id":"PORTAL-GAP","status":"confirmed"},{"operation":"confirm_gap_geometry","opening_id":"UNRELATED","status":"confirmed"}):
        decisions=[{"id":"D","issue_id":"ISSUE-GEOMETRY","evidence_refs":["VIEW-EVIDENCE"],"decision":"confirm_geometry","allowed_entity_ids":["PORTAL-GAP"],"operations":[operation]}]
        with pytest.raises(ValueError):build_verdict_candidate(document,["a"*64],decisions)


def test_keep_unresolved_decision_cannot_hide_operations():
    document=_document();decisions=[{"id":"D","issue_id":"ISSUE-GEOMETRY","evidence_refs":["VIEW-EVIDENCE"],"decision":"keep_unresolved","allowed_entity_ids":["PORTAL-GAP"],"operations":[{"operation":"confirm_gap_geometry","opening_id":"PORTAL-GAP","status":"confirmed"}]}]
    with pytest.raises(ValueError,match="keep-unresolved"):build_verdict_candidate(document,["a"*64],decisions)


def test_endpoint_excluded_feature_and_issue_promotions_are_decision_bound():
    document=_document()
    feature={"id":"FEATURE-X","classification":"joinery","geometry":{"space":"canonical_px","primitive":"segment","points_px":[[1,1],[2,1]]},"attachments":[{"endpoint_index":0,"relationship":"wall_face_attachment","target_atom_id":"WALL-SOUTH","target_face":"right_side","contact_point_m":[1,-.1],"status":"candidate"},{"endpoint_index":1,"relationship":"intentional_free_end","target_atom_id":None,"target_face":None,"contact_point_m":None,"status":"candidate"}],"build_policy":"exclude_from_full_height_structure","status":"candidate","evidence_refs":["VIEW-EVIDENCE"],"note":"x"}
    document["source"]["excluded_linear_features"].append(feature);document["structure_hash"]=compute_v21_structure_hash(document)
    decisions=[{"id":"D","issue_id":"ISSUE-GEOMETRY","evidence_refs":["VIEW-EVIDENCE"],"decision":"confirm_geometry","allowed_entity_ids":["BRANCH-EAST","WALL-EAST","NODE-EAST-START","FEATURE-X","ISSUE-GEOMETRY"],"operations":[{"operation":"confirm_endpoint_classification","branch_id":"BRANCH-EAST","atom_id":"WALL-EAST","node_id":"NODE-EAST-START","endpoint_index":0,"promotion_scope":"node_status_only","status":"confirmed"},{"operation":"confirm_excluded_feature","feature_id":"FEATURE-X","status":"confirmed"},{"operation":"resolve_issue","issue_id":"ISSUE-GEOMETRY","status":"resolved"}]}]
    branch_before=next(row for row in document["wall_graph"]["branches"] if row["id"]=="BRANCH-EAST")["status"]
    atom_before=next(row for row in document["wall_graph"]["atoms"] if row["id"]=="WALL-EAST")["status"]
    result,report=apply_authorized_verdict(document,_authorize(build_verdict_candidate(document,["a"*64],decisions)))
    assert next(row for row in result["source"]["excluded_linear_features"] if row["id"]=="FEATURE-X")["status"]=="confirmed"
    assert next(row for row in result["unresolved_issues"] if row["id"]=="ISSUE-GEOMETRY")["status"]=="resolved"
    assert "FEATURE-X" in report["promotion_ids"]
    assert next(row for row in result["wall_graph"]["branches"] if row["id"]=="BRANCH-EAST")["status"]==branch_before
    assert next(row for row in result["wall_graph"]["atoms"] if row["id"]=="WALL-EAST")["status"]==atom_before
    assert next(row for row in result["wall_graph"]["junctions"] if row["id"]=="NODE-EAST-START")["status"]=="confirmed"


def test_nine_endpoint_classification_operations_promote_nodes_only():
    document=_document();operation={"operation":"confirm_endpoint_classification","branch_id":"BRANCH-EAST","atom_id":"WALL-EAST","node_id":"NODE-EAST-START","endpoint_index":0,"promotion_scope":"node_status_only","status":"confirmed"}
    decision={"id":"NINE","issue_id":"ISSUE-GEOMETRY","evidence_refs":["VIEW-EVIDENCE"],"decision":"confirm_geometry","allowed_entity_ids":["BRANCH-EAST","WALL-EAST","NODE-EAST-START"],"operations":[deepcopy(operation) for _ in range(9)]}
    branch_before=next(row for row in document["wall_graph"]["branches"] if row["id"]=="BRANCH-EAST")["status"];atom_before=next(row for row in document["wall_graph"]["atoms"] if row["id"]=="WALL-EAST")["status"]
    result,_=apply_authorized_verdict(document,_authorize(build_verdict_candidate(document,["a"*64],[decision])))
    assert next(row for row in result["wall_graph"]["branches"] if row["id"]=="BRANCH-EAST")["status"]==branch_before
    assert next(row for row in result["wall_graph"]["atoms"] if row["id"]=="WALL-EAST")["status"]==atom_before
