"""Two-phase, exact-hash v2.1 reference-geometry confirmation."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import compute_v21_structure_hash,validate_v21_document,assess_v21_build_readiness

ALLOWED={"confirm_endpoint_classification","confirm_gap_geometry","confirm_wall_cut_geometry","confirm_excluded_feature","resolve_issue","supersede_issue"}

def _hash(value):return hashlib.sha256(canonical_json(value)).hexdigest()

def compute_candidate_hash(candidate):
    payload=deepcopy(dict(candidate));payload.pop("candidate_hash",None);return _hash(payload)

def build_verdict_candidate(document:Mapping[str,Any],evidence_hashes:list[str],decisions:list[Mapping[str,Any]]):
    doc=validate_v21_document(document)
    if not evidence_hashes or len(set(evidence_hashes))!=len(evidence_hashes) or any(not isinstance(h,str) or len(h)!=64 for h in evidence_hashes):raise ValueError("candidate requires unique evidence hashes")
    candidate={"schema":"reference-confirmation-verdict-candidate-v1","source_document_hash":doc["structure_hash"],"source_document_content_sha256":_hash(doc),"evidence_hashes":sorted(evidence_hashes),"status":"pending_independent_review","build_authorized":False,"decisions":deepcopy(decisions),"candidate_hash":"0"*64}
    _validate_candidate(doc,candidate,verify_hash=False)
    candidate["candidate_hash"]=compute_candidate_hash(candidate)
    return candidate

def _validate_candidate(doc,candidate,verify_hash=True):
    keys={"schema","source_document_hash","source_document_content_sha256","evidence_hashes","status","build_authorized","decisions","candidate_hash"}
    if not isinstance(candidate,Mapping) or set(candidate)!=keys or candidate["schema"]!="reference-confirmation-verdict-candidate-v1" or candidate["status"]!="pending_independent_review" or candidate["build_authorized"] is not False:raise ValueError("invalid pending reference-confirmation candidate")
    if candidate["source_document_hash"]!=doc["structure_hash"] or candidate["source_document_content_sha256"]!=_hash(doc):raise ValueError("candidate has stale source document hash")
    if verify_hash and candidate["candidate_hash"]!=compute_candidate_hash(candidate):raise ValueError("candidate hash drift")
    if not isinstance(candidate["evidence_hashes"],list) or not candidate["evidence_hashes"] or len(set(candidate["evidence_hashes"]))!=len(candidate["evidence_hashes"]) or any(not isinstance(value,str) or len(value)!=64 or any(ch not in "0123456789abcdef" for ch in value) for value in candidate["evidence_hashes"]):raise ValueError("candidate evidence hashes invalid")
    if not isinstance(candidate["decisions"],list) or not candidate["decisions"]:raise ValueError("candidate decisions required")
    issues={row["id"] for row in doc["unresolved_issues"]}
    decision_ids=set()
    for decision in candidate["decisions"]:
        if not isinstance(decision,Mapping) or set(decision)!={"id","issue_id","evidence_refs","decision","allowed_entity_ids","operations"}:raise ValueError("invalid embedded decision")
        if decision["id"] in decision_ids or decision["issue_id"] not in issues or not decision["evidence_refs"] or not decision["allowed_entity_ids"] or len(set(decision["allowed_entity_ids"]))!=len(decision["allowed_entity_ids"]):raise ValueError("decision identity/issue/evidence invalid")
        decision_ids.add(decision["id"])
        if decision["decision"]=="keep_unresolved" and decision["operations"]:raise ValueError("keep-unresolved decision cannot authorize operations")
        if decision["decision"] not in {"confirm_geometry","resolve_source_issue","supersede_source_issue","keep_unresolved"}:raise ValueError("unsupported decision")
        for operation in decision["operations"]:_validate_operation(operation,set(decision["allowed_entity_ids"]),decision["issue_id"])

def validate_verdict_candidate(source_document,candidate,actual_evidence_hashes=None):
    doc=validate_v21_document(source_document);_validate_candidate(doc,candidate,verify_hash=True)
    if actual_evidence_hashes is not None and sorted(candidate["evidence_hashes"])!=sorted(actual_evidence_hashes):raise ValueError("candidate evidence hashes differ from actual approved evidence bytes")
    return deepcopy(dict(candidate))

def _validate_operation(op,allowed,issue_id):
    if not isinstance(op,Mapping) or op.get("operation") not in ALLOWED:raise ValueError("unsupported confirmation operation")
    schemas={
      "confirm_endpoint_classification":{"operation","branch_id","atom_id","node_id","endpoint_index","promotion_scope","status"},
      "confirm_gap_geometry":{"operation","opening_id","status"},
      "confirm_wall_cut_geometry":{"operation","opening_id","status"},
      "confirm_excluded_feature":{"operation","feature_id","status"},
      "resolve_issue":{"operation","issue_id","status"},
      "supersede_issue":{"operation","issue_id","status"},
    }
    if set(op)!=schemas[op["operation"]] or op["status"] not in {"confirmed","resolved","superseded"}:raise ValueError("operation exact schema/status mismatch")
    if op["operation"]=="confirm_endpoint_classification" and op["promotion_scope"]!="node_status_only":raise ValueError("endpoint classification may confirm node status only")
    targets={value for key,value in op.items() if key.endswith("_id") and key not in {"issue_id"}}
    if not targets<=allowed:raise ValueError("operation targets entity outside decision allowlist")
    if op["operation"] in {"resolve_issue","supersede_issue"} and op["issue_id"]!=issue_id:raise ValueError("issue operation differs from containing decision")

def apply_authorized_verdict(document,authorized):
    doc=validate_v21_document(document)
    keys={"schema","candidate","candidate_hash","authority","verdict","build_authorized"}
    if not isinstance(authorized,Mapping) or set(authorized)!=keys or authorized["schema"]!="reference-confirmation-verdict-v1" or authorized["authority"]!="independent_reference_reviewer" or authorized["verdict"]!="authorize_exact_reference_geometry" or authorized["build_authorized"] is not False:raise ValueError("invalid independently authorized verdict")
    candidate=authorized["candidate"]
    _validate_candidate(doc,candidate,verify_hash=True)
    if authorized["candidate_hash"]!=candidate["candidate_hash"]:raise ValueError("authorized candidate hash mismatch")
    result=deepcopy(doc);branches={r["id"]:r for r in result["wall_graph"]["branches"]};atoms={r["id"]:r for r in result["wall_graph"]["atoms"]};nodes={r["id"]:r for r in result["wall_graph"]["junctions"]};openings={r["id"]:r for r in result["opening_contract"]["openings"]};features={r["id"]:r for r in result["source"]["excluded_linear_features"]};issues={r["id"]:r for r in result["unresolved_issues"]}
    def semantic_fingerprint(row):
        host=deepcopy(row["host"])
        if host:
            for terminal in host.get("gap_terminals",[]):terminal.pop("status",None)
        return {"build_disposition":row["build_disposition"],"build_kind":row["build_kind"],"host":host,"swing_direction":row["swing_direction"],"traversable":row["traversable"],"side_a_space_id":row["side_a_space_id"],"side_b_space_id":row["side_b_space_id"],"status":row["status"]}
    adjacency_before=deepcopy(result["adjacency_truth"]);semantic_before={oid:semantic_fingerprint(row) for oid,row in openings.items()};branch_status_before={key:row["status"] for key,row in branches.items()};atom_status_before={key:row["status"] for key,row in atoms.items()}
    promotions=[]
    for decision in candidate["decisions"]:
      for op in decision["operations"]:
        kind=op["operation"]
        if kind=="confirm_endpoint_classification":
            branch,atom,node=branches[op["branch_id"]],atoms[op["atom_id"]],nodes[op["node_id"]]
            if atom["branch_id"]!=branch["id"] or atom["start_node_id" if op["endpoint_index"]==0 else "end_node_id"]!=node["id"]:raise ValueError("endpoint confirmation topology mismatch")
            node["status"]="confirmed";promotions.append(node["id"])
        elif kind=="confirm_gap_geometry":
            opening=openings[op["opening_id"]]
            if opening["build_disposition"]!="place_in_preexisting_gap":raise ValueError("gap confirmation targets non-gap opening")
            opening["source_observation"]["status"]=opening["effective_void"]["status"]="confirmed"
            for terminal in opening["host"]["gap_terminals"]:terminal["status"]="confirmed"
            opening["jamb_before"]["status"]=opening["jamb_after"]["status"]="confirmed";promotions.append(opening["id"]+":geometry")
        elif kind=="confirm_wall_cut_geometry":
            opening=openings[op["opening_id"]]
            if opening["build_disposition"]!="cut":raise ValueError("wall-cut confirmation targets non-cut opening")
            opening["source_observation"]["status"]=opening["effective_void"]["status"]="confirmed";opening["jamb_before"]["status"]=opening["jamb_after"]["status"]="confirmed";promotions.append(opening["id"]+":geometry")
        elif kind=="confirm_excluded_feature":
            feature=features[op["feature_id"]];feature["status"]="confirmed"
            for attachment in feature["attachments"]:attachment["status"]="confirmed"
            promotions.append(feature["id"])
        elif kind=="resolve_issue":issues[op["issue_id"]].update(status="resolved",blocks_reference_freeze=False,blocks_build=False);promotions.append(op["issue_id"])
        elif kind=="supersede_issue":issues[op["issue_id"]].update(status="superseded",blocks_reference_freeze=False,blocks_build=False);promotions.append(op["issue_id"])
    if result["adjacency_truth"]!=adjacency_before:raise ValueError("reference geometry verdict changed adjacency")
    if {key:row["status"] for key,row in branches.items()}!=branch_status_before or {key:row["status"] for key,row in atoms.items()}!=atom_status_before:raise ValueError("endpoint classification changed branch/atom status")
    for oid,before in semantic_before.items():
        after=semantic_fingerprint(openings[oid])
        if after!=before:raise ValueError("reference geometry verdict changed opening semantic/build fields")
    result["structure_hash"]=compute_v21_structure_hash(result);result=validate_v21_document(result);readiness=assess_v21_build_readiness(result)
    if readiness["ready"]:raise ValueError("reference geometry confirmation must remain not build-ready")
    return result,{"schema":"reference-confirmation-application-v1","candidate_hash":candidate["candidate_hash"],"source_structure_hash":doc["structure_hash"],"result_structure_hash":result["structure_hash"],"promotion_ids":sorted(set(promotions)),"ready":False,"remaining_blockers":readiness["blocker_ids"]}

__all__=["build_verdict_candidate","compute_candidate_hash","validate_verdict_candidate","apply_authorized_verdict"]
