from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from tests.test_research_structure_v21 import v21_fixture_with_gap
from tools.fastloop_research.v2_contract import V2ContractError
from tools.fastloop_research.v21_contract import compute_v21_structure_hash
from tools.goal_loop_v2.reference_confirmation import apply_authorized_verdict,build_verdict_candidate,compute_candidate_hash
from tools.goal_loop_v2.source_contract_report import generate_source_contract_report

ROOT=Path(__file__).resolve().parents[1]

def _inputs(coordinate_confirmed=True):
    prior=v21_fixture_with_gap()
    if coordinate_confirmed:
        for anchor in prior["source"]["anchors"]:
            if anchor["kind"]!="scale":anchor["coordinate_status"]="source_confirmed_coordinate"
    prior["unresolved_issues"].append({"id":"ISSUE-SCORE","severity":"hard","category":"semantic","entity_refs":[],"status":"open","message":"pending","blocks_reference_freeze":True,"blocks_build":True,"evidence_refs":["VIEW-EVIDENCE"]});prior["structure_hash"]=compute_v21_structure_hash(prior)
    decision={"id":"KEEP","issue_id":"ISSUE-SCORE","evidence_refs":["VIEW-EVIDENCE"],"decision":"keep_unresolved","allowed_entity_ids":["ISSUE-SCORE"],"operations":[]}
    candidate=build_verdict_candidate(prior,["a"*64],[decision]);authorized={"schema":"reference-confirmation-verdict-v1","candidate":candidate,"candidate_hash":candidate["candidate_hash"],"authority":"independent_reference_reviewer","verdict":"authorize_exact_reference_geometry","build_authorized":False}
    document,application=apply_authorized_verdict(prior,authorized)
    contract=json.loads((ROOT/"docs"/"goal_loop_v2"/"goal-contract.json").read_text(encoding="utf-8"));contract["samples"]=["fixture-v2"]
    return prior,authorized,["a"*64],document,contract,application

def test_source_report_has_exact_s01_s08_and_independent_weighted_score():
    report,detail=generate_source_contract_report(*_inputs())
    assert set(report)=={"schema","layer","sample_id","source_hash","reference_hash","scoring_version","checks"}
    assert [row["id"] for row in report["checks"]]==["S01_SOURCE_IDENTITY","S02_ORIENTATION_COORDINATE_CHAIN","S03_SCALE_AND_DIMENSIONS","S04_OUTER_BOUNDARY","S05_WALL_GRAPH","S06_OPENINGS","S07_SPACES_ADJACENCY_REACHABILITY","S08_PROVENANCE_UNRESOLVED"]
    assert {row["id"] for row in report["checks"] if row["status"]=="pass"}=={"S01_SOURCE_IDENTITY","S02_ORIENTATION_COORDINATE_CHAIN","S03_SCALE_AND_DIMENSIONS","S04_OUTER_BOUNDARY"}
    assert detail["weighted_score"]==45 and detail["maximum_score"]==100

def test_candidate_entities_never_count_as_confirmed_passes():
    report,detail=generate_source_contract_report(*_inputs())
    assert next(row for row in report["checks"] if row["id"]=="S05_WALL_GRAPH")["status"]=="fail"
    assert any(item["entity_id"]=="BRANCH-EAST" and "candidate" in item["reason"] for item in detail["entity_blockers"]["S05_WALL_GRAPH"])
    assert next(row for row in report["checks"] if row["id"]=="S06_OPENINGS")["status"]=="fail"

def test_s02_requires_coordinate_confirmation_not_semantic_anchor_status():
    report,detail=generate_source_contract_report(*_inputs(coordinate_confirmed=False))
    assert next(row for row in report["checks"] if row["id"]=="S02_ORIENTATION_COORDINATE_CHAIN")["status"]=="fail"
    assert detail["weighted_score"]==40
    assert any(item["entity_id"]=="ANCHOR-ENTRY" and "coordinate status" in item["reason"] for item in detail["entity_blockers"]["S02_ORIENTATION_COORDINATE_CHAIN"])

def test_stale_hash_missing_graph_id_and_forged_status_chain_fail_closed():
    prior,authorized,evidence,document,contract,application=_inputs();stale=deepcopy(document);stale["outer_boundary"]["status"]="candidate"
    with pytest.raises(ValueError):generate_source_contract_report(prior,authorized,evidence,stale,contract,application)
    missing=deepcopy(document);missing["wall_graph"]["atoms"].pop();missing["structure_hash"]=compute_v21_structure_hash(missing)
    with pytest.raises(ValueError):generate_source_contract_report(prior,authorized,evidence,missing,contract,application)
    forged=deepcopy(document)
    for row in forged["wall_graph"]["branches"]:row["status"]="confirmed"
    forged["structure_hash"]=compute_v21_structure_hash(forged)
    with pytest.raises(ValueError):generate_source_contract_report(prior,authorized,evidence,forged,contract,application)

def test_application_and_candidate_identity_forgery_is_s01_hard_failure():
    prior,authorized,evidence,document,contract,application=_inputs();forged=deepcopy(application);forged["candidate_hash"]="f"*64
    with pytest.raises(ValueError):generate_source_contract_report(prior,authorized,evidence,document,contract,forged)

def test_evil_schema_empty_decisions_fake_source_and_evidence_reject_even_rehashed():
    prior,authorized,evidence,document,contract,application=_inputs()
    for attack in ("schema","empty","source","evidence"):
        wrapper=deepcopy(authorized);candidate=wrapper["candidate"]
        if attack=="schema":candidate["schema"]="evil-schema"
        if attack=="empty":candidate["decisions"]=[]
        if attack=="source":candidate["source_document_content_sha256"]="f"*64
        if attack=="evidence":candidate["evidence_hashes"]=["b"*64]
        candidate["candidate_hash"]=compute_candidate_hash(candidate);wrapper["candidate_hash"]=candidate["candidate_hash"]
        with pytest.raises(ValueError):generate_source_contract_report(prior,wrapper,evidence,document,contract,application)
