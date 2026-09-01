"""Two-phase, exact-hash v2.1 reference-geometry confirmation."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import compute_v21_structure_hash,validate_v21_document,assess_v21_build_readiness

ALLOWED={"confirm_endpoint_classification","confirm_gap_geometry","confirm_wall_cut_geometry","confirm_excluded_feature","confirm_source_anchor","confirm_source_coordinate","resolve_issue","supersede_issue"}
SOURCE_ANCHOR_ISSUES={"S02_ORIENTATION_COORDINATE_CHAIN","S03_SCALE_AND_DIMENSIONS"}

def _hash(value):return hashlib.sha256(canonical_json(value)).hexdigest()

def compute_candidate_hash(candidate):
    payload=deepcopy(dict(candidate));payload.pop("candidate_hash",None);return _hash(payload)

def compute_evidence_binding(path:str|Path):
    evidence_path=Path(path).expanduser().resolve();payload=evidence_path.read_bytes();file_hash=hashlib.sha256(payload).hexdigest()
    if evidence_path.suffix.lower()==".json":
        try:value=json.loads(payload.decode("utf-8"))
        except (UnicodeError,json.JSONDecodeError) as exc:raise ValueError("coordinate evidence JSON is invalid") from exc
        media_type="application/json";canonical_hash=_hash(value)
    elif evidence_path.suffix.lower()==".md":
        try:text=payload.decode("utf-8")
        except UnicodeError as exc:raise ValueError("coordinate evidence report is not UTF-8") from exc
        media_type="text/markdown";canonical_hash=hashlib.sha256(text.replace("\r\n","\n").replace("\r","\n").encode("utf-8")).hexdigest()
    else:raise ValueError("coordinate evidence must be JSON or Markdown")
    return {"media_type":media_type,"file_sha256":file_hash,"canonical_sha256":canonical_hash}

def compute_coordinate_anchor_geometry_hash(document:Mapping[str,Any],anchor_ids:Sequence[str]):
    anchors={row["id"]:row for row in document["source"]["anchors"]}
    if len(anchor_ids)!=len(set(anchor_ids)) or any(anchor_id not in anchors for anchor_id in anchor_ids):raise ValueError("coordinate geometry hash requires unique existing anchors")
    snapshot=[{"id":anchor_id,"kind":anchors[anchor_id]["kind"],"points_px":deepcopy(anchors[anchor_id]["geometry"]["points_px"])} for anchor_id in sorted(anchor_ids)]
    return _hash(snapshot)

def _validate_coordinate_audit(document,candidate,actual_evidence_files):
    json_paths=[Path(path).expanduser().resolve() for path in actual_evidence_files if Path(path).suffix.lower()==".json"]
    if len(json_paths)!=1:raise ValueError("coordinate fact requires exactly one audit JSON")
    try:audit=json.loads(json_paths[0].read_text(encoding="utf-8"))
    except (OSError,UnicodeError,json.JSONDecodeError) as exc:raise ValueError("coordinate audit JSON cannot be read") from exc
    document_sample=str(document["project"]["sample_id"]);audit_sample=str(audit.get("sample_id")) if isinstance(audit,Mapping) else "";sample_tokens={token.lower() for token in re.split(r"[^A-Za-z0-9]+",document_sample) if token}
    if not isinstance(audit,Mapping) or audit.get("schema")!="goal-loop-v2-coordinate-anchor-audit-v1" or not (audit_sample.lower()==document_sample.lower() or audit_sample.lower() in sample_tokens):raise ValueError("coordinate audit identity/schema mismatch")
    rows=audit.get("anchors")
    if not isinstance(rows,list):raise ValueError("coordinate audit lacks anchor rows")
    audit_rows={row.get("id"):row for row in rows if isinstance(row,Mapping) and isinstance(row.get("id"),str)}
    if len(audit_rows)!=len(rows):raise ValueError("coordinate audit anchor IDs must be unique")
    operations=[op for decision in candidate["decisions"] for op in decision["operations"] if op["operation"]=="confirm_source_coordinate"]
    if len(operations)!=1:raise ValueError("coordinate candidate must contain exactly one atomic coordinate operation")
    operation=operations[0];target_ids=operation["anchor_ids"]
    audited_non_scale={anchor_id for anchor_id,row in audit_rows.items() if row.get("kind")!="scale"}
    if audited_non_scale!=set(target_ids):raise ValueError("coordinate audit target IDs differ from candidate")
    anchors={row["id"]:row for row in document["source"]["anchors"]}
    scale_ids={anchor_id for anchor_id,row in anchors.items() if row["kind"]=="scale"}
    if set(audit.get("excluded_from_scope",[]))!=scale_ids:raise ValueError("coordinate audit scale exclusion differs from source")
    def same_points(left,right):
        return isinstance(left,list) and isinstance(right,list) and len(left)==len(right) and all(isinstance(a,list) and isinstance(b,list) and len(a)==len(b)==2 and all(not isinstance(x,bool) and isinstance(x,(int,float)) and math.isfinite(float(x)) for x in (*a,*b)) and max(abs(float(a[i])-float(b[i])) for i in range(2))<=1e-9 for a,b in zip(left,right))
    for anchor_id in target_ids:
        audited=audit_rows[anchor_id];source=anchors[anchor_id]
        if audited.get("bounds")!="pass" or audited.get("coordinate_status")!="source_confirmed_coordinate" or audited.get("semantic_status")!=source["status"] or audited.get("kind")!=source["kind"] or not same_points(audited.get("points_px"),source["geometry"]["points_px"]):raise ValueError("coordinate audit geometry differs from source anchor")
    if operation["coordinate_geometry_sha256"]!=compute_coordinate_anchor_geometry_hash(document,target_ids):raise ValueError("coordinate operation geometry hash differs from source anchors")

def build_verdict_candidate(document:Mapping[str,Any],evidence_hashes:list[str],decisions:list[Mapping[str,Any]],*,evidence_bindings:list[Mapping[str,Any]]|None=None):
    doc=validate_v21_document(document)
    if not evidence_hashes or len(set(evidence_hashes))!=len(evidence_hashes) or any(not isinstance(h,str) or len(h)!=64 for h in evidence_hashes):raise ValueError("candidate requires unique evidence hashes")
    candidate={"schema":"reference-confirmation-verdict-candidate-v1","source_document_hash":doc["structure_hash"],"source_document_content_sha256":_hash(doc),"evidence_hashes":sorted(evidence_hashes),"status":"pending_independent_review","build_authorized":False,"decisions":deepcopy(decisions),"candidate_hash":"0"*64}
    if evidence_bindings is not None:candidate["evidence_bindings"]=deepcopy(evidence_bindings)
    _validate_candidate(doc,candidate,verify_hash=False)
    candidate["candidate_hash"]=compute_candidate_hash(candidate)
    return candidate

def _validate_candidate(doc,candidate,verify_hash=True):
    keys={"schema","source_document_hash","source_document_content_sha256","evidence_hashes","status","build_authorized","decisions","candidate_hash"}
    if not isinstance(candidate,Mapping) or frozenset(candidate) not in {frozenset(keys),frozenset(keys|{"evidence_bindings"})} or candidate["schema"]!="reference-confirmation-verdict-candidate-v1" or candidate["status"]!="pending_independent_review" or candidate["build_authorized"] is not False:raise ValueError("invalid pending reference-confirmation candidate")
    if candidate["source_document_hash"]!=doc["structure_hash"] or candidate["source_document_content_sha256"]!=_hash(doc):raise ValueError("candidate has stale source document hash")
    if verify_hash and candidate["candidate_hash"]!=compute_candidate_hash(candidate):raise ValueError("candidate hash drift")
    if not isinstance(candidate["evidence_hashes"],list) or not candidate["evidence_hashes"] or len(set(candidate["evidence_hashes"]))!=len(candidate["evidence_hashes"]) or any(not isinstance(value,str) or len(value)!=64 or any(ch not in "0123456789abcdef" for ch in value) for value in candidate["evidence_hashes"]):raise ValueError("candidate evidence hashes invalid")
    if not isinstance(candidate["decisions"],list) or not candidate["decisions"]:raise ValueError("candidate decisions required")
    issues={row["id"] for row in doc["unresolved_issues"]}
    anchors={row["id"]:row for row in doc["source"]["anchors"]}
    decision_ids=set();anchor_targets=set();coordinate_targets=set();has_coordinate_decision=False
    for decision in candidate["decisions"]:
        if not isinstance(decision,Mapping) or set(decision)!={"id","issue_id","evidence_refs","decision","allowed_entity_ids","operations"}:raise ValueError("invalid embedded decision")
        anchor_decision=decision["decision"]=="confirm_source_fact"
        coordinate_decision=decision["decision"]=="confirm_coordinate_fact";has_coordinate_decision=has_coordinate_decision or coordinate_decision
        issue_valid=decision["issue_id"] in issues or (anchor_decision and decision["issue_id"] in SOURCE_ANCHOR_ISSUES) or (coordinate_decision and decision["issue_id"]=="S02_ORIENTATION_COORDINATE_CHAIN")
        if decision["id"] in decision_ids or not issue_valid or not decision["evidence_refs"] or not decision["allowed_entity_ids"] or len(set(decision["allowed_entity_ids"]))!=len(decision["allowed_entity_ids"]):raise ValueError("decision identity/issue/evidence invalid")
        decision_ids.add(decision["id"])
        if decision["decision"]=="keep_unresolved" and decision["operations"]:raise ValueError("keep-unresolved decision cannot authorize operations")
        if decision["decision"] not in {"confirm_geometry","confirm_source_fact","confirm_coordinate_fact","resolve_source_issue","supersede_source_issue","keep_unresolved"}:raise ValueError("unsupported decision")
        if anchor_decision and (not decision["operations"] or any(op.get("operation")!="confirm_source_anchor" for op in decision["operations"] if isinstance(op,Mapping))):raise ValueError("source-fact decision may contain source-anchor operations only")
        if coordinate_decision and (not decision["operations"] or any(op.get("operation")!="confirm_source_coordinate" for op in decision["operations"] if isinstance(op,Mapping))):raise ValueError("coordinate-fact decision may contain coordinate operations only")
        decision_anchor_targets=set();decision_coordinate_targets=set()
        for operation in decision["operations"]:
            _validate_operation(operation,set(decision["allowed_entity_ids"]),decision["issue_id"])
            if operation["operation"]=="confirm_source_anchor":
                if not anchor_decision:raise ValueError("source-anchor operation requires source-fact decision")
                anchor=anchors.get(operation["anchor_id"])
                if anchor is None or anchor["status"]!="source_candidate":raise ValueError("source-anchor target must be an existing source candidate")
                expected_issue="S03_SCALE_AND_DIMENSIONS" if anchor["kind"]=="scale" else "S02_ORIENTATION_COORDINATE_CHAIN"
                if decision["issue_id"]!=expected_issue:raise ValueError("source-anchor decision is bound to the wrong source score issue")
                if anchor["id"] in anchor_targets:raise ValueError("source anchor may be promoted at most once per candidate")
                anchor_targets.add(anchor["id"]);decision_anchor_targets.add(anchor["id"])
            elif operation["operation"]=="confirm_source_coordinate":
                if not coordinate_decision:raise ValueError("coordinate operation requires coordinate-fact decision")
                ids=operation["anchor_ids"]
                expected={anchor_id for anchor_id,anchor in anchors.items() if anchor["kind"]!="scale" and "coordinate_status" not in anchor}
                if len(ids)!=len(set(ids)) or set(ids)!=expected or any(anchors[anchor_id]["kind"]=="scale" for anchor_id in ids):raise ValueError("coordinate operation must target the exact unconfirmed non-scale anchor set")
                if operation["coordinate_geometry_sha256"]!=compute_coordinate_anchor_geometry_hash(doc,ids):raise ValueError("coordinate operation geometry hash differs from source anchors")
                if set(ids)&coordinate_targets:raise ValueError("coordinate anchor may be promoted at most once per candidate")
                coordinate_targets.update(ids);decision_coordinate_targets.update(ids)
        if anchor_decision and set(decision["allowed_entity_ids"])!=decision_anchor_targets:raise ValueError("source-fact decision allowlist must exactly equal anchor targets")
        if coordinate_decision and set(decision["allowed_entity_ids"])!=decision_coordinate_targets:raise ValueError("coordinate-fact decision allowlist must exactly equal coordinate targets")
    bindings=candidate.get("evidence_bindings")
    if has_coordinate_decision:
        if not isinstance(bindings,list) or len(bindings)!=2:raise ValueError("coordinate candidate requires exact JSON and Markdown evidence bindings")
        seen_files=set();media=set()
        for binding in bindings:
            if not isinstance(binding,Mapping) or set(binding)!={"media_type","file_sha256","canonical_sha256"} or binding["media_type"] not in {"application/json","text/markdown"}:raise ValueError("coordinate evidence binding schema invalid")
            for field in ("file_sha256","canonical_sha256"):
                if not isinstance(binding[field],str) or len(binding[field])!=64 or any(ch not in "0123456789abcdef" for ch in binding[field]):raise ValueError("coordinate evidence binding hash invalid")
            if binding["file_sha256"] in seen_files:raise ValueError("coordinate evidence bindings must be unique")
            seen_files.add(binding["file_sha256"]);media.add(binding["media_type"])
        if media!={"application/json","text/markdown"} or seen_files!=set(candidate["evidence_hashes"]):raise ValueError("coordinate evidence bindings differ from candidate evidence hashes")
    elif bindings is not None:raise ValueError("non-coordinate candidate cannot carry coordinate evidence bindings")

def validate_verdict_candidate(source_document,candidate,actual_evidence_hashes=None,actual_evidence_bindings=None):
    doc=validate_v21_document(source_document);_validate_candidate(doc,candidate,verify_hash=True)
    if actual_evidence_hashes is not None and sorted(candidate["evidence_hashes"])!=sorted(actual_evidence_hashes):raise ValueError("candidate evidence hashes differ from actual approved evidence bytes")
    if actual_evidence_bindings is not None and canonical_json(sorted(candidate.get("evidence_bindings",[]),key=lambda row:row["file_sha256"]))!=canonical_json(sorted(actual_evidence_bindings,key=lambda row:row["file_sha256"])):raise ValueError("candidate evidence bindings differ from actual evidence canonical hashes")
    return deepcopy(dict(candidate))

def validate_coordinate_evidence(source_document,candidate,actual_evidence_files:Sequence[str|Path]):
    doc=validate_v21_document(source_document);actual_bindings=[compute_evidence_binding(path) for path in actual_evidence_files]
    validate_verdict_candidate(doc,candidate,[row["file_sha256"] for row in actual_bindings],actual_bindings);_validate_coordinate_audit(doc,candidate,actual_evidence_files)
    return deepcopy(dict(candidate))

def _validate_operation(op,allowed,issue_id):
    if not isinstance(op,Mapping) or op.get("operation") not in ALLOWED:raise ValueError("unsupported confirmation operation")
    schemas={
      "confirm_endpoint_classification":{"operation","branch_id","atom_id","node_id","endpoint_index","promotion_scope","status"},
      "confirm_gap_geometry":{"operation","opening_id","status"},
      "confirm_wall_cut_geometry":{"operation","opening_id","status"},
      "confirm_excluded_feature":{"operation","feature_id","status"},
      "confirm_source_anchor":{"operation","anchor_id","status"},
      "confirm_source_coordinate":{"operation","anchor_ids","coordinate_geometry_sha256","status"},
      "resolve_issue":{"operation","issue_id","status"},
      "supersede_issue":{"operation","issue_id","status"},
    }
    valid_statuses={"source_confirmed","human_confirmed"} if op["operation"]=="confirm_source_anchor" else ({"source_confirmed_coordinate"} if op["operation"]=="confirm_source_coordinate" else {"confirmed","resolved","superseded"})
    if set(op)!=schemas[op["operation"]] or op["status"] not in valid_statuses:raise ValueError("operation exact schema/status mismatch")
    if op["operation"]=="confirm_source_coordinate" and (not isinstance(op["anchor_ids"],list) or not op["anchor_ids"] or any(not isinstance(value,str) for value in op["anchor_ids"])):raise ValueError("coordinate operation anchor IDs invalid")
    if op["operation"]=="confirm_source_coordinate" and (not isinstance(op["coordinate_geometry_sha256"],str) or len(op["coordinate_geometry_sha256"])!=64 or any(ch not in "0123456789abcdef" for ch in op["coordinate_geometry_sha256"])):raise ValueError("coordinate operation geometry hash invalid")
    if op["operation"]=="confirm_endpoint_classification" and op["promotion_scope"]!="node_status_only":raise ValueError("endpoint classification may confirm node status only")
    targets={value for key,value in op.items() if key.endswith("_id") and key not in {"issue_id"}}
    if not targets<=allowed:raise ValueError("operation targets entity outside decision allowlist")
    if op["operation"] in {"resolve_issue","supersede_issue"} and op["issue_id"]!=issue_id:raise ValueError("issue operation differs from containing decision")

def apply_authorized_verdict(document,authorized,actual_evidence_files:Sequence[str|Path]|None=None):
    doc=validate_v21_document(document)
    keys={"schema","candidate","candidate_hash","authority","verdict","build_authorized"}
    allowed_verdicts={"authorize_exact_reference_geometry","authorize_exact_source_fact","authorize_exact_coordinate_fact"}
    if not isinstance(authorized,Mapping) or set(authorized)!=keys or authorized["schema"]!="reference-confirmation-verdict-v1" or authorized["authority"]!="independent_reference_reviewer" or authorized["verdict"] not in allowed_verdicts or authorized["build_authorized"] is not False:raise ValueError("invalid independently authorized verdict")
    candidate=authorized["candidate"]
    _validate_candidate(doc,candidate,verify_hash=True)
    if authorized["candidate_hash"]!=candidate["candidate_hash"]:raise ValueError("authorized candidate hash mismatch")
    decisions=candidate["decisions"];operations=[op for decision in decisions for op in decision["operations"]]
    if authorized["verdict"]=="authorize_exact_source_fact":
        if not operations or any(decision["decision"]!="confirm_source_fact" for decision in decisions) or any(op["operation"]!="confirm_source_anchor" or op["status"]!="source_confirmed" for op in operations):raise ValueError("source-fact wrapper may authorize source-confirmed anchor operations only")
    elif authorized["verdict"]=="authorize_exact_coordinate_fact":
        if not operations or any(decision["decision"]!="confirm_coordinate_fact" for decision in decisions) or any(op["operation"]!="confirm_source_coordinate" or op["status"]!="source_confirmed_coordinate" for op in operations):raise ValueError("coordinate-fact wrapper may authorize coordinate operations only")
        if actual_evidence_files is None:raise ValueError("coordinate-fact wrapper requires actual evidence files")
        validate_coordinate_evidence(doc,candidate,actual_evidence_files)
    elif any(decision["decision"] in {"confirm_source_fact","confirm_coordinate_fact"} for decision in decisions) or any(op["operation"] in {"confirm_source_anchor","confirm_source_coordinate"} for op in operations):raise ValueError("reference-geometry wrapper cannot authorize source facts")
    result=deepcopy(doc);branches={r["id"]:r for r in result["wall_graph"]["branches"]};atoms={r["id"]:r for r in result["wall_graph"]["atoms"]};nodes={r["id"]:r for r in result["wall_graph"]["junctions"]};openings={r["id"]:r for r in result["opening_contract"]["openings"]};features={r["id"]:r for r in result["source"]["excluded_linear_features"]};anchors={r["id"]:r for r in result["source"]["anchors"]};issues={r["id"]:r for r in result["unresolved_issues"]}
    def semantic_fingerprint(row):
        host=deepcopy(row["host"])
        if host:
            for terminal in host.get("gap_terminals",[]):terminal.pop("status",None)
        return {"build_disposition":row["build_disposition"],"build_kind":row["build_kind"],"host":host,"swing_direction":row["swing_direction"],"traversable":row["traversable"],"side_a_space_id":row["side_a_space_id"],"side_b_space_id":row["side_b_space_id"],"status":row["status"]}
    adjacency_before=deepcopy(result["adjacency_truth"]);semantic_before={oid:semantic_fingerprint(row) for oid,row in openings.items()};branch_status_before={key:row["status"] for key,row in branches.items()};atom_status_before={key:row["status"] for key,row in atoms.items()};anchors_before=deepcopy(result["source"]["anchors"]);anchor_status_before={key:row["status"] for key,row in anchors.items()};promoted_anchor_ids=set();promoted_coordinate_ids=set()
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
        elif kind=="confirm_source_anchor":
            anchor=anchors[op["anchor_id"]];anchor["status"]=op["status"];promoted_anchor_ids.add(anchor["id"]);promotions.append(anchor["id"])
        elif kind=="confirm_source_coordinate":
            for anchor_id in op["anchor_ids"]:
                anchors[anchor_id]["coordinate_status"]=op["status"];promoted_coordinate_ids.add(anchor_id);promotions.append(anchor_id+":coordinate")
        elif kind=="resolve_issue":issues[op["issue_id"]].update(status="resolved",blocks_reference_freeze=False,blocks_build=False);promotions.append(op["issue_id"])
        elif kind=="supersede_issue":issues[op["issue_id"]].update(status="superseded",blocks_reference_freeze=False,blocks_build=False);promotions.append(op["issue_id"])
    if result["adjacency_truth"]!=adjacency_before:raise ValueError("reference geometry verdict changed adjacency")
    if {key:row["status"] for key,row in branches.items()}!=branch_status_before or {key:row["status"] for key,row in atoms.items()}!=atom_status_before:raise ValueError("endpoint classification changed branch/atom status")
    for oid,before in semantic_before.items():
        after=semantic_fingerprint(openings[oid])
        if after!=before:raise ValueError("reference geometry verdict changed opening semantic/build fields")
    anchors_probe=deepcopy(result["source"]["anchors"])
    for row in anchors_probe:
        if row["id"] in promoted_anchor_ids:row["status"]=anchor_status_before[row["id"]]
        if row["id"] in promoted_coordinate_ids:
            before=next(item for item in anchors_before if item["id"]==row["id"])
            if "coordinate_status" in before:row["coordinate_status"]=before["coordinate_status"]
            else:row.pop("coordinate_status",None)
    if anchors_probe!=anchors_before:raise ValueError("source-anchor confirmation changed non-status anchor facts")
    result["structure_hash"]=compute_v21_structure_hash(result);result=validate_v21_document(result);readiness=assess_v21_build_readiness(result)
    if readiness["ready"]:raise ValueError("reference geometry confirmation must remain not build-ready")
    return result,{"schema":"reference-confirmation-application-v1","candidate_hash":candidate["candidate_hash"],"source_structure_hash":doc["structure_hash"],"result_structure_hash":result["structure_hash"],"promotion_ids":sorted(set(promotions)),"ready":False,"remaining_blockers":readiness["blocker_ids"]}

__all__=["build_verdict_candidate","compute_candidate_hash","compute_evidence_binding","compute_coordinate_anchor_geometry_hash","validate_verdict_candidate","validate_coordinate_evidence","apply_authorized_verdict"]
