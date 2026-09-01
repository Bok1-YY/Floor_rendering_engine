from __future__ import annotations
from copy import deepcopy
import hashlib
import pytest
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import compute_v21_structure_hash
from tools.goal_loop_v2.source_correction import apply_source_corrections
from tests.test_research_structure_v21 import v21_fixture_with_gap
from tests.test_research_structure_v2 import v2_fixture

H=lambda value:hashlib.sha256(canonical_json(value)).hexdigest()

def _history(opening,artifact="e"*64):
    former={"source_observation":deepcopy(opening["source_observation"]),"host":deepcopy(opening["host"]),"effective_void":deepcopy(opening["effective_void"]),"jamb_before":deepcopy(opening["jamb_before"]),"jamb_after":deepcopy(opening["jamb_after"])}
    return {"id":"HISTORY-"+opening["id"],"origin":"external_artifact","captured_from_structure_hash":None,"captured_from_artifact_sha256":artifact,"captured_payload_sha256":H(former),"former_source_observation":former["source_observation"],"former_host":former["host"],"former_effective_void":former["effective_void"],"former_jamb_before":former["jamb_before"],"former_jamb_after":former["jamb_after"],"status":"rejected_by_source_evidence","reason_code":"duplicate_or_wrong_axis","evidence_refs":["VIEW-EVIDENCE"]}

def _inputs():
    document=v21_fixture_with_gap();primary=next(row for row in document["opening_contract"]["openings"] if row["id"]=="OPEN-ENTRY");duplicate=next(row for row in document["opening_contract"]["openings"] if row["id"]=="PORTAL-GAP")
    replacement=deepcopy(primary);replacement["source_observation"]={**deepcopy(primary["source_observation"]),"nominal_segment_m":[[2.1,0],[3.1,0]],"nominal_width_m":1.0,"status":"candidate"};replacement["effective_void"]={**deepcopy(primary["effective_void"]),"segment_m":[[2.1,0],[3.1,0]],"width_m":1.0,"status":"candidate"};replacement["status"]="candidate"
    branch=next(row for row in document["wall_graph"]["branches"] if row["id"]=="BRANCH-SOUTH")
    payload={"primary_opening_id":"OPEN-ENTRY","new_primary_opening":replacement,"primary_history":_history(primary),"expected_demotions":["PORTAL-GAP"],"demotions":[{"opening_id":"PORTAL-GAP","history":_history(duplicate),"evidence_observation":{**deepcopy(duplicate["source_observation"]),"kind":"unknown","status":"candidate"},"requires_restored_wall_overlap":True}],"wall_restore":{"branch_id":"BRANCH-SOUTH","atom_id":"WALL-SOUTH","node_id":"NODE-1","endpoint_index":1,"prior_branch_sha256":H(branch),"old_point_m":[4,0],"new_point_m":[5,0]},"approved_artifact_sha256":"e"*64,"source_evidence_refs":["VIEW-EVIDENCE"]}
    evidence={"approved_external_artifacts":{"audit":"e"*64},"deduplication_contract":{"primary_opening_id":"OPEN-ENTRY","expected_demotions":["PORTAL-GAP"],"protected_opening_ids":["OP011"]}};operation={"id":"DEDUP","operation":"deduplicate_rehost_opening","prior_payload_sha256":H(primary),"payload":payload,"evidence_refs":["VIEW-EVIDENCE"]};manifest={"schema":"source-correction-manifest-v1","source_document_hash":document["structure_hash"],"source_evidence_hash":H(evidence),"authority":"independent_source_reviewer","verdict":"accepted_source_evidence_pending_application_review","application_authorized":False,"attempt":1,"max_attempts":1,"operations":[operation]}
    return document,evidence,manifest

def test_atomic_dedup_leaves_one_real_opening_restores_wall_and_resets_adjacency():
    document,evidence,manifest=_inputs();result,report=apply_source_corrections(document,evidence,manifest);openings={row["id"]:row for row in result["opening_contract"]["openings"]}
    assert openings["OPEN-ENTRY"]["build_disposition"]=="cut" and openings["OPEN-ENTRY"]["status"]=="candidate"
    assert openings["PORTAL-GAP"]["build_disposition"]=="evidence_only" and openings["PORTAL-GAP"]["host"] is None
    assert openings["OPEN-ENTRY"]["superseded_interpretations"][-1]["former_source_observation"]==document["opening_contract"]["openings"][0]["source_observation"]
    assert next(row for row in result["wall_graph"]["branches"] if row["id"]=="BRANCH-SOUTH")["centerline_m"][1]==[5,0]
    assert result["adjacency_truth"]["status"]=="unresolved" and result["adjacency_truth"]["entrance_opening_id"] is None
    assert report["ready"] is False

def test_wrong_axis_partial_wall_restore_and_incomplete_history_fail_closed():
    document,evidence,manifest=_inputs();wrong=deepcopy(manifest);replacement=wrong["operations"][0]["payload"]["new_primary_opening"];replacement["effective_void"]["segment_m"]=[[2,0],[2,1]];replacement["source_observation"]["nominal_segment_m"]=[[2,0],[2,1]]
    with pytest.raises(ValueError,match="wrong wall-cut axis"):apply_source_corrections(document,evidence,wrong)
    partial=deepcopy(manifest);partial["operations"][0]["payload"]["wall_restore"]["new_point_m"]=[4.5,0]
    with pytest.raises(ValueError,match="not covered by restored continuous wall"):apply_source_corrections(document,evidence,partial)
    missing=deepcopy(manifest);missing["operations"][0]["payload"]["demotions"][0]["history"]["former_jamb_after"]=None
    with pytest.raises(ValueError,match="complete approved external rejected history"):apply_source_corrections(document,evidence,missing)

def test_stale_hash_and_v2_input_fail_without_mutating_v2():
    document,evidence,manifest=_inputs();stale=deepcopy(manifest);stale["source_document_hash"]="f"*64
    with pytest.raises(ValueError,match="stale"):apply_source_corrections(document,evidence,stale)
    legacy=v2_fixture();before=deepcopy(legacy)
    with pytest.raises(Exception):apply_source_corrections(legacy,evidence,manifest)
    assert legacy==before

@pytest.mark.parametrize("attack",["omit","substitute","include_op011","duplicate","axis_mismatch"])
def test_expected_demotion_set_and_primary_axis_are_fail_closed(attack):
    document,evidence,manifest=_inputs();payload=manifest["operations"][0]["payload"]
    if attack=="omit":payload["demotions"]=[]
    if attack=="substitute":payload["demotions"][0]["opening_id"]="OPEN-ENTRY"
    if attack=="include_op011":payload["expected_demotions"].append("OP011");payload["demotions"].append({**deepcopy(payload["demotions"][0]),"opening_id":"OP011"})
    if attack=="duplicate":payload["expected_demotions"].append("PORTAL-GAP")
    if attack=="axis_mismatch":payload["new_primary_opening"]["source_observation"]["nominal_segment_m"]=[[2,0],[2,1]]
    with pytest.raises(ValueError):apply_source_corrections(document,evidence,manifest)
