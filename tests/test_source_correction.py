from __future__ import annotations

from copy import deepcopy
import hashlib

import pytest

from tools.fastloop_research.contract import canonical_json
from tools.goal_loop_v2.source_correction import apply_source_corrections
from tests.test_research_structure_v21 import v21_fixture_with_gap


def _hash(value): return hashlib.sha256(canonical_json(value)).hexdigest()


def _rehost_inputs():
    document = v21_fixture_with_gap()
    document["unresolved_issues"].append({"id":"ISSUE-OLD-HOST","severity":"hard","category":"opening_contract_capability","entity_refs":["OPEN-ENTRY"],"status":"open","message":"old host","blocks_reference_freeze":True,"blocks_build":True,"evidence_refs":["VIEW-EVIDENCE"]})
    from tools.fastloop_research.v21_contract import compute_v21_structure_hash
    document["structure_hash"]=compute_v21_structure_hash(document)
    opening = next(row for row in document["opening_contract"]["openings"] if row["id"] == "OPEN-ENTRY")
    former = {"source_observation":deepcopy(opening["source_observation"]),"host": deepcopy(opening["host"]), "effective_void": deepcopy(opening["effective_void"]), "jamb_before": deepcopy(opening["jamb_before"]), "jamb_after": deepcopy(opening["jamb_after"])}
    artifact_hash="e"*64
    history = {"id": "HISTORY-REHOST", "origin":"external_artifact","captured_from_structure_hash":None,"captured_from_artifact_sha256":artifact_hash,"captured_payload_sha256": _hash(former), "former_source_observation":former["source_observation"],"former_host": former["host"], "former_effective_void": former["effective_void"], "former_jamb_before": former["jamb_before"], "former_jamb_after": former["jamb_after"], "status": "rejected_by_source_evidence", "reason_code": "wrong_host", "evidence_refs": ["VIEW-EVIDENCE"]}
    template = next(row for row in document["opening_contract"]["openings"] if row["id"] == "PORTAL-GAP")
    new_source={"kind":"door","nominal_segment_m":[[4,0],[5,0]],"nominal_width_m":1.0,"anchor_id":None,"evidence_refs":["VIEW-EVIDENCE"],"status":"candidate"}
    replacement_issue={"id":"ISSUE-NEW-GAP","severity":"hard","category":"opening_contract_capability","entity_refs":["OPEN-ENTRY"],"status":"open","message":"gap pending independent review","blocks_reference_freeze":True,"blocks_build":True,"evidence_refs":["VIEW-EVIDENCE"]}
    payload = {"opening_id": "OPEN-ENTRY", "source_observation_sha256": _hash(opening["source_observation"]),"approved_artifact_sha256":artifact_hash,"new_source_observation":new_source,"issue_update":{"supersede_issue_id":"ISSUE-OLD-HOST","superseded_message":"old host rejected by source evidence","replacement_issue":replacement_issue}, "superseded_interpretation": history, "build_kind": "door", "host": deepcopy(template["host"]), "effective_void": deepcopy(template["effective_void"]), "jamb_before": deepcopy(template["jamb_before"]), "jamb_after": deepcopy(template["jamb_after"]), "swing_direction": "not_shown"}
    evidence = {"schema": "independent-source-evidence-v1", "finding": "gap", "approved_external_artifacts": {"old_proposal": artifact_hash}}
    operation = {"id": "OP-REHOST", "operation": "rehost_opening_as_gap_portal", "prior_payload_sha256": _hash(opening), "payload": payload, "evidence_refs": ["VIEW-EVIDENCE"]}
    manifest = {"schema": "source-correction-manifest-v1", "source_document_hash": document["structure_hash"], "source_evidence_hash": _hash(evidence), "authority": "independent_source_reviewer", "verdict": "accepted_source_evidence_pending_application_review", "application_authorized": False, "attempt": 1, "max_attempts": 1, "operations": [operation]}
    return document, evidence, manifest


def test_rehost_is_atomic_preserves_nominal_history_and_invalidates_adjacency():
    document, evidence, manifest = _rehost_inputs()
    nominal = deepcopy(next(row for row in document["opening_contract"]["openings"] if row["id"] == "OPEN-ENTRY")["source_observation"])
    result, report = apply_source_corrections(document,evidence,manifest)
    opening = next(row for row in result["opening_contract"]["openings"] if row["id"] == "OPEN-ENTRY")
    assert opening["source_observation"] != nominal
    assert opening["superseded_interpretations"][-1]["former_source_observation"] == nominal
    assert opening["superseded_interpretations"][-1]["status"] == "rejected_by_source_evidence"
    assert opening["host"]["mode"] == "preexisting_gap"
    assert result["adjacency_truth"]["status"] == "unresolved"
    assert report["ready"] is False


def test_stale_hash_silent_history_deletion_and_nominal_mutation_fail_closed():
    document,evidence,manifest=_rehost_inputs()
    stale=deepcopy(manifest); stale["source_document_hash"]="f"*64
    with pytest.raises(ValueError,match="stale"): apply_source_corrections(document,evidence,stale)
    missing=deepcopy(manifest); missing["operations"][0]["payload"]["superseded_interpretation"]["former_host"]=None
    with pytest.raises(ValueError,match="payload/source mismatch|complete rejected external"): apply_source_corrections(document,evidence,missing)
    null_all=deepcopy(manifest); history=null_all["operations"][0]["payload"]["superseded_interpretation"]
    for key in ("former_source_observation","former_host","former_effective_void","former_jamb_before","former_jamb_after"): history[key]=None
    history["captured_payload_sha256"]=_hash({"source_observation":None,"host":None,"effective_void":None,"jamb_before":None,"jamb_after":None})
    with pytest.raises(ValueError,match="payload/source mismatch|complete rejected external"): apply_source_corrections(document,evidence,null_all)
    fake=deepcopy(manifest); h=fake["operations"][0]["payload"]["superseded_interpretation"]; h["captured_from_artifact_sha256"]="d"*64; fake["operations"][0]["payload"]["approved_artifact_sha256"]="d"*64
    with pytest.raises(ValueError,match="complete rejected external"): apply_source_corrections(document,evidence,fake)
    active=deepcopy(manifest); history=active["operations"][0]["payload"]["superseded_interpretation"]
    history.update(origin="active_document",captured_from_structure_hash="f"*64,captured_from_artifact_sha256=None)
    with pytest.raises(ValueError,match="active-document history"):apply_source_corrections(document,evidence,active)
    ordinary=deepcopy(manifest); ordinary["operations"][0]["operation"]="clip_effective_void"; ordinary["operations"][0]["payload"]["new_source_observation"]={"kind":"door"}
    with pytest.raises(ValueError,match="invalid clip-effective payload"):apply_source_corrections(document,evidence,ordinary)
    attacked=deepcopy(document); next(row for row in attacked["opening_contract"]["openings"] if row["id"]=="OPEN-ENTRY")["source_observation"]["nominal_width_m"] += .1
    from tools.fastloop_research.v21_contract import compute_v21_structure_hash
    attacked["structure_hash"]=compute_v21_structure_hash(attacked)
    forged=deepcopy(manifest); forged["source_document_hash"]=attacked["structure_hash"]; forged["operations"][0]["prior_payload_sha256"]=_hash(next(row for row in attacked["opening_contract"]["openings"] if row["id"]=="OPEN-ENTRY"))
    with pytest.raises(ValueError,match="source hash mismatch"): apply_source_corrections(attacked,evidence,forged)
