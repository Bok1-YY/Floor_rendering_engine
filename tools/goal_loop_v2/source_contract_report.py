"""Generate the exact S01-S08 source-contract score layer for V2.1."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document,assess_v21_build_readiness
from tools.goal_loop_v2.reference_confirmation import apply_authorized_verdict,validate_verdict_candidate

CONFIRMED={"confirmed","legacy_confirmed"}
ANCHOR_CONFIRMED={"human_confirmed","source_confirmed","legacy_confirmed"}

def _hash(value):return hashlib.sha256(canonical_json(value)).hexdigest()
def _block(kind,entity_id,reason,evidence=None):return {"entity_type":kind,"entity_id":entity_id,"reason":reason,"evidence_refs":list(evidence or [])}

def generate_source_contract_report(prior_document:Mapping[str,Any],authorized_verdict:Mapping[str,Any],actual_evidence_hashes:list[str],document:Mapping[str,Any],goal_contract:Mapping[str,Any],application:Mapping[str,Any]):
    prior=validate_v21_document(prior_document);candidate=authorized_verdict.get("candidate") if isinstance(authorized_verdict,Mapping) else None
    validate_verdict_candidate(prior,candidate,actual_evidence_hashes)
    recomputed_document,recomputed_application=apply_authorized_verdict(prior,authorized_verdict)
    if canonical_json(recomputed_document)!=canonical_json(document) or canonical_json(recomputed_application)!=canonical_json(application):raise ValueError("current document/application differ from authorized independent recomputation")
    doc=validate_v21_document(document)
    scoring=goal_contract["score_contract"];weights=scoring["source_contract"]["checks"]
    expected=["S01_SOURCE_IDENTITY","S02_ORIENTATION_COORDINATE_CHAIN","S03_SCALE_AND_DIMENSIONS","S04_OUTER_BOUNDARY","S05_WALL_GRAPH","S06_OPENINGS","S07_SPACES_ADJACENCY_REACHABILITY","S08_PROVENANCE_UNRESOLVED"]
    if set(weights)!=set(expected):raise ValueError("goal source score check IDs drift")
    details={check:[] for check in expected}
    # S01: immutable file/pixel/structure identity and confirmation lineage.
    source=doc["source"]
    if doc["source_hash"]!=source["original"]["file_sha256"]:details[expected[0]].append(_block("source","original","source_hash differs from original file hash"))
    if application.get("ready") is not False:details[expected[0]].append(_block("provenance","reference-confirmation","geometry application improperly claimed ready"))
    # S02: canonical orientation, affine registration, and every source anchor.
    matrix=source["canonical"]["raw_to_canonical_3x3"]
    if source["canonical"]["orientation_policy"]=="raw_identity" and matrix!=[[1,0,0],[0,1,0],[0,0,1]]:details[expected[1]].append(_block("source","orientation","raw_identity transform is not identity"))
    for anchor in source["anchors"]:
        if anchor["status"] not in ANCHOR_CONFIRMED:details[expected[1]].append(_block("anchor",anchor["id"],f"anchor status is {anchor['status']}",[anchor.get("evidence_asset_id")]))
    # S03: one authoritative scale and reproducible dimension controls.
    scales=[row for row in source["anchors"] if row["kind"]=="scale"]
    if len(scales)!=1:details[expected[2]].append(_block("scale","scale-authority",f"expected one scale, found {len(scales)}"))
    elif scales[0]["status"] not in ANCHOR_CONFIRMED:details[expected[2]].append(_block("scale",scales[0]["id"],f"scale status is {scales[0]['status']}",[scales[0]["evidence_asset_id"]]))
    registration=source["metric_registration"]
    if len(registration["control_points"])<3 or float(registration["max_residual_m"])>float(registration["tolerance_m"]):details[expected[2]].append(_block("registration","metric","controls/residual fail declared tolerance"))
    # S04: validated polygon must also be confirmed source truth.
    if doc["outer_boundary"]["status"] not in CONFIRMED:details[expected[3]].append(_block("outer_boundary","outer_boundary",f"status is {doc['outer_boundary']['status']}",doc["outer_boundary"]["evidence_refs"]))
    # S05: every graph component confirmed; topology itself already passed V2.1 validation.
    for group,kind in ((doc["wall_graph"]["branches"],"wall_branch"),(doc["wall_graph"]["atoms"],"wall_atom"),(doc["wall_graph"]["junctions"],"wall_junction")):
        for row in group:
            if row["status"] not in CONFIRMED:details[expected[4]].append(_block(kind,row["id"],f"status is {row['status']}",row["evidence_refs"]))
    # S06: all opening source/host/effective/jamb/traversal facts must be final.
    readiness=assess_v21_build_readiness(doc)
    for opening in doc["opening_contract"]["openings"]:
        oid=opening["id"];reasons=[]
        if opening["status"] not in CONFIRMED:reasons.append(f"overall status {opening['status']}")
        if opening["source_observation"]["status"] not in CONFIRMED:reasons.append(f"source status {opening['source_observation']['status']}")
        if opening["build_disposition"]=="exclude_pending_resolution":reasons.append("build disposition pending resolution")
        if opening["build_disposition"] in {"cut","place_in_preexisting_gap"}:
            if opening["effective_void"]["status"] not in CONFIRMED:reasons.append(f"effective status {opening['effective_void']['status']}")
            if opening["jamb_before"]["status"] not in CONFIRMED or opening["jamb_after"]["status"] not in CONFIRMED:reasons.append("jamb geometry not confirmed")
            if opening["build_kind"] in {"door","entrance"} and opening["traversable"] is not True:reasons.append("door/entrance is not traversable")
            if opening["side_a_space_id"] is None or opening["side_b_space_id"] is None or opening["side_a_space_id"]==opening["side_b_space_id"]:reasons.append("side spaces unresolved")
        if opening["build_disposition"]=="place_in_preexisting_gap" and any(t["status"] not in CONFIRMED for t in opening["host"]["gap_terminals"]):reasons.append("gap terminals not confirmed")
        for reason in reasons:details[expected[5]].append(_block("opening",oid,reason,opening["source_observation"]["evidence_refs"]))
    for blocker in readiness["blocker_ids"]:
        if blocker.startswith(("gap_portal_wall_overlap:","gap_portal_jamb_support_insufficient:")):details[expected[5]].append(_block("opening_geometry",blocker,blocker))
    # S07: spaces, exact confirmed adjacency, and exterior reachability.
    spaces={row["id"]:row for row in doc["spaces"]}
    for row in spaces.values():
        if row["status"] not in CONFIRMED:details[expected[6]].append(_block("space",row["id"],f"status is {row['status']}",row["evidence_refs"]))
    adjacency=doc["adjacency_truth"]
    if adjacency["status"] not in CONFIRMED:details[expected[6]].append(_block("adjacency","truth",f"status is {adjacency['status']}"))
    graph={space:set() for space in ["exterior",*spaces]}
    for edge in adjacency["edges"]:
        if edge["status"] not in CONFIRMED:details[expected[6]].append(_block("adjacency_edge",edge["id"],f"status is {edge['status']}",edge["evidence_refs"]))
        else:graph[edge["space_a_id"]].add(edge["space_b_id"]);graph[edge["space_b_id"]].add(edge["space_a_id"])
    seen={"exterior"};queue=["exterior"]
    while queue:
        current=queue.pop(0)
        for neighbor in graph[current]:
            if neighbor not in seen:seen.add(neighbor);queue.append(neighbor)
    for missing in sorted(set(spaces)-seen):details[expected[6]].append(_block("reachability",missing,"space unreachable from exterior through confirmed edges"))
    # S08: no unresolved blockers, disclosed/accepted assumptions, confirmed excluded evidence.
    for issue in doc["unresolved_issues"]:
        if issue["status"]=="open":details[expected[7]].append(_block("unresolved_issue",issue["id"],issue["message"],issue["evidence_refs"]))
    for assumption in doc["assumptions"]["items"]:
        if assumption["status"]!="human_accepted" or not assumption["disclosure"].strip():details[expected[7]].append(_block("assumption",assumption["id"],f"status/policy is {assumption['status']}/{assumption['build_policy']}",assumption["evidence_refs"]))
    for feature in source["excluded_linear_features"]:
        if feature["status"] not in CONFIRMED:details[expected[7]].append(_block("excluded_linear_feature",feature["id"],f"status is {feature['status']}",feature["evidence_refs"]))
    checks=[];score=0
    for check_id in expected:
        status="pass" if not details[check_id] else "fail";weight=int(weights[check_id]);score+=weight if status=="pass" else 0
        checks.append({"id":check_id,"status":status,"evidence":f"mechanical V2.1 source-contract audit; weight={weight}; entity_blockers={len(details[check_id])}"})
    raw_sample=str(doc["project"]["sample_id"]);tokens={token.lower() for token in re.split(r"[^A-Za-z0-9]+",raw_sample) if token};matches=[sample for sample in goal_contract["samples"] if sample.lower()==raw_sample.lower() or sample.lower() in tokens]
    if len(matches)!=1:raise ValueError(f"document sample identity does not uniquely map to goal contract: {raw_sample}")
    identity={"sample_id":matches[0],"source_hash":doc["source_hash"],"reference_hash":doc["structure_hash"],"scoring_version":scoring["scoring_version"]}
    report={"schema":"goal-loop-v2-score-layer-v1","layer":"source_contract",**identity,"checks":checks}
    sidecar={"schema":"goal-loop-v2-source-contract-detail-v1",**identity,"weighted_score":score,"maximum_score":sum(weights.values()),"minimum_required":scoring["source_contract"]["minimum"],"hard_failures":[row["id"] for row in checks if row["status"]=="fail"],"entity_blockers":details,"entity_counts":{"anchors":len(source["anchors"]),"branches":len(doc["wall_graph"]["branches"]),"atoms":len(doc["wall_graph"]["atoms"]),"junctions":len(doc["wall_graph"]["junctions"]),"openings":len(doc["opening_contract"]["openings"]),"spaces":len(doc["spaces"]),"adjacency_edges":len(adjacency["edges"]),"unresolved_issues":len(doc["unresolved_issues"]),"assumptions":len(doc["assumptions"]["items"])}}
    sidecar["provenance_chain"]={"prior_structure_hash":prior["structure_hash"],"authorized_candidate_hash":candidate["candidate_hash"],"actual_evidence_sha256":sorted(actual_evidence_hashes),"application_source_hash":application["source_structure_hash"],"application_result_hash":application["result_structure_hash"],"current_result_hash":doc["structure_hash"],"canonical_recomputation_equal":True}
    return report,sidecar

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("prior-document","authorized-verdict","document","goal-contract","application"):p.add_argument(f"--{name}",required=True,type=Path)
    p.add_argument("--evidence",required=True,type=Path,action="append")
    p.add_argument("--output",required=True,type=Path);p.add_argument("--detail",required=True,type=Path);args=p.parse_args(argv)
    prior=json.loads(args.prior_document.read_text(encoding='utf-8'));authorized=json.loads(args.authorized_verdict.read_text(encoding='utf-8'));document=json.loads(args.document.read_text(encoding='utf-8'));contract=json.loads(args.goal_contract.read_text(encoding='utf-8'));application=json.loads(args.application.read_text(encoding='utf-8'));evidence_hashes=[hashlib.sha256(path.read_bytes()).hexdigest() for path in args.evidence]
    report,detail=generate_source_contract_report(prior,authorized,evidence_hashes,document,contract,application)
    args.output.write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8");args.detail.write_text(json.dumps(detail,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"score":detail["weighted_score"],"hard_failures":detail["hard_failures"],"output":str(args.output),"detail":str(args.detail)},sort_keys=True));return 0

if __name__=="__main__":raise SystemExit(main())
