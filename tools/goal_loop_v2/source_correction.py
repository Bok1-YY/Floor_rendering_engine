"""Atomic, hash-bound source corrections for research bundle v2.1 candidates."""

from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import compute_v21_structure_hash, validate_v21_document, assess_v21_build_readiness, v21_mapping_metadata, _segment_atom_overlap_length


ALLOWED = {"rehost_opening_as_gap_portal", "add_gap_portal_opening", "exclude_linear_feature", "clip_effective_void", "classify_endpoint", "deduplicate_rehost_opening"}


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def validate_source_correction_manifest(document, evidence, manifest):
    doc = validate_v21_document(document)
    keys = {"schema", "source_document_hash", "source_evidence_hash", "authority", "verdict", "application_authorized", "attempt", "max_attempts", "operations"}
    if not isinstance(manifest, Mapping) or set(manifest) != keys or manifest.get("schema") != "source-correction-manifest-v1": raise ValueError("invalid source correction manifest")
    if manifest["source_document_hash"] != doc["structure_hash"] or manifest["source_evidence_hash"] != _hash(evidence): raise ValueError("source correction manifest has stale document/evidence hash")
    if manifest["authority"] != "independent_source_reviewer" or manifest["verdict"] != "accepted_source_evidence_pending_application_review" or manifest["application_authorized"] is not False: raise ValueError("source correction manifest is not pending non-build authority")
    if manifest["attempt"] != 1 or manifest["max_attempts"] != 1: raise ValueError("only source correction attempt 1 is allowed")
    if not isinstance(manifest["operations"], list) or len({row.get("id") for row in manifest["operations"] if isinstance(row, Mapping)}) != len(manifest["operations"]): raise ValueError("source correction operation IDs must be unique")
    for row in manifest["operations"]:
        if not isinstance(row, Mapping) or set(row) != {"id", "operation", "prior_payload_sha256", "payload", "evidence_refs"} or row["operation"] not in ALLOWED: raise ValueError("invalid source correction operation")
        if not isinstance(row["evidence_refs"], list) or not row["evidence_refs"]: raise ValueError("source correction requires evidence refs")
    return doc


def apply_source_corrections(document, evidence, manifest):
    original = validate_source_correction_manifest(document, evidence, manifest)
    result = deepcopy(original)
    openings = {row["id"]: row for row in result["opening_contract"]["openings"]}
    adjacency_before = deepcopy(result["adjacency_truth"])
    nominal_before = {opening_id: canonical_json(row["source_observation"]) for opening_id, row in openings.items()}
    source_replacements: set[str] = set()
    for operation in manifest["operations"]:
        kind, payload = operation["operation"], operation["payload"]
        if kind == "deduplicate_rehost_opening":
            expected_keys={"primary_opening_id","new_primary_opening","primary_history","expected_demotions","demotions","wall_restore","approved_artifact_sha256","source_evidence_refs"}
            if set(payload)!=expected_keys:raise ValueError("invalid deduplicate/rehost payload")
            primary=openings[payload["primary_opening_id"]]
            dedup_contract=evidence.get("deduplication_contract") if isinstance(evidence,Mapping) else None
            if not isinstance(dedup_contract,Mapping) or set(dedup_contract)!={"primary_opening_id","expected_demotions","protected_opening_ids"}:raise ValueError("source evidence lacks exact deduplication contract")
            expected_demotions=payload["expected_demotions"]
            if not isinstance(expected_demotions,list) or len(expected_demotions)!=len(set(expected_demotions)) or set(expected_demotions)!=set(dedup_contract["expected_demotions"]) or payload["primary_opening_id"]!=dedup_contract["primary_opening_id"] or payload["primary_opening_id"] in expected_demotions or set(expected_demotions)&set(dedup_contract["protected_opening_ids"]):raise ValueError("deduplication expected-demotion identity mismatch")
            if any(opening_id not in openings for opening_id in expected_demotions):raise ValueError("deduplication expected demotion does not exist")
            if operation["prior_payload_sha256"]!=_hash(primary):raise ValueError("deduplicate primary prior hash mismatch")
            approved={value for value in (evidence.get("approved_external_artifacts") or {}).values() if isinstance(value,str)}
            if payload["approved_artifact_sha256"] not in approved:raise ValueError("deduplicate artifact is not approved by evidence")
            def validate_external_history(history,current):
                former={"source_observation":history["former_source_observation"],"host":history["former_host"],"effective_void":history["former_effective_void"],"jamb_before":history["former_jamb_before"],"jamb_after":history["former_jamb_after"]}
                if history["origin"]!="external_artifact" or history["captured_from_structure_hash"] is not None or history["captured_from_artifact_sha256"] not in approved or history["captured_payload_sha256"]!=_hash(former) or history["status"]!="rejected_by_source_evidence" or any(value is None for value in former.values()) or history["former_source_observation"]!=current["source_observation"]:raise ValueError("deduplicate requires complete approved external rejected history")
            validate_external_history(payload["primary_history"],primary)
            replacement=deepcopy(payload["new_primary_opening"])
            if replacement["id"]!=primary["id"] or replacement["status"]!="candidate":raise ValueError("deduplicate replacement primary identity/status invalid")
            owner_id=(replacement.get("host") or {}).get("owning_wall_atom_id");owner_atom=next((row for row in result["wall_graph"]["atoms"] if row["id"]==owner_id),None)
            if replacement.get("build_disposition")!="cut" or owner_atom is None or _segment_atom_overlap_length(replacement["effective_void"]["segment_m"],owner_atom)<float(replacement["effective_void"]["width_m"])-0.001:raise ValueError("deduplicate replacement primary has wrong wall-cut axis/host")
            source_segment=replacement["source_observation"]["nominal_segment_m"];effective_segment=replacement["effective_void"]["segment_m"]
            if max(math.dist(source_segment[index],effective_segment[index]) for index in (0,1))>0.001 or abs(float(replacement["source_observation"]["nominal_width_m"])-float(replacement["effective_void"]["width_m"]))>0.001:raise ValueError("deduplicate replacement source/effective axis mismatch")
            replacement["superseded_interpretations"]=[*deepcopy(primary["superseded_interpretations"]),deepcopy(payload["primary_history"])]
            openings[primary["id"]]=replacement
            seen_demotions=set()
            for demotion in payload["demotions"]:
                if set(demotion)!={"opening_id","history","evidence_observation","requires_restored_wall_overlap"} or demotion["opening_id"]==primary["id"] or demotion["opening_id"] in seen_demotions:raise ValueError("invalid duplicate demotion")
                duplicate=openings[demotion["opening_id"]];validate_external_history(demotion["history"],duplicate);seen_demotions.add(duplicate["id"])
                duplicate["superseded_interpretations"].append(deepcopy(demotion["history"]))
                duplicate.update(source_observation=deepcopy(demotion["evidence_observation"]),build_disposition="evidence_only",build_kind=None,host=None,effective_void=None,swing_direction=None,traversable=False,side_a_space_id=None,side_b_space_id=None,jamb_before=None,jamb_after=None,status="candidate")
            if len(seen_demotions)!=len(payload["demotions"]):raise ValueError("duplicate demotion coverage incomplete")
            if seen_demotions!=set(expected_demotions):raise ValueError("deduplication demotions do not exactly cover expected set")
            restore=payload["wall_restore"]
            if set(restore)!={"branch_id","atom_id","node_id","endpoint_index","prior_branch_sha256","old_point_m","new_point_m"}:raise ValueError("invalid wall restore payload")
            branch=next(row for row in result["wall_graph"]["branches"] if row["id"]==restore["branch_id"])
            atom=next(row for row in result["wall_graph"]["atoms"] if row["id"]==restore["atom_id"])
            node=next(row for row in result["wall_graph"]["junctions"] if row["id"]==restore["node_id"])
            index=int(restore["endpoint_index"])
            if restore["prior_branch_sha256"]!=_hash(branch) or branch["centerline_m"][index]!=restore["old_point_m"] or atom["branch_id"]!=branch["id"] or atom["start_node_id" if index==0 else "end_node_id"]!=node["id"]:raise ValueError("wall restore prior topology/hash mismatch")
            branch["centerline_m"][index]=deepcopy(restore["new_point_m"]);atom["centerline_m"][index]=deepcopy(restore["new_point_m"]);node["axis_point_m"]=deepcopy(restore["new_point_m"])
            incident=next(row for row in node["incidents"] if row["atom_id"]==atom["id"] and row["end"]==("start" if index==0 else "end"));incident["contact_point_m"]=deepcopy(restore["new_point_m"]);node["status"]="candidate";branch["status"]="candidate";atom["status"]="candidate"
            active=[row for row in [openings[primary["id"]],*[openings[item] for item in seen_demotions]] if row["build_disposition"] in {"cut","place_in_preexisting_gap"}]
            if len(active)!=1 or active[0]["id"]!=primary["id"]:raise ValueError("deduplicate did not leave exactly one active opening")
            for demotion in payload["demotions"]:
                if demotion["requires_restored_wall_overlap"]:
                    observation=demotion["history"]["former_source_observation"]
                    if _segment_atom_overlap_length(observation["nominal_segment_m"],atom)<float(observation["nominal_width_m"])-0.001:raise ValueError("rejected portal is not covered by restored continuous wall")
            source_replacements.add(primary["id"]);source_replacements.update(seen_demotions)
        elif kind == "rehost_opening_as_gap_portal":
            if set(payload)!={"opening_id","source_observation_sha256","approved_artifact_sha256","new_source_observation","issue_update","superseded_interpretation","build_kind","host","effective_void","jamb_before","jamb_after","swing_direction"}:raise ValueError("invalid rehost payload")
            opening = openings[payload["opening_id"]]
            if operation["prior_payload_sha256"] != _hash(opening) or payload["source_observation_sha256"] != _hash(opening["source_observation"]): raise ValueError("rehost prior opening/source hash mismatch")
            history = deepcopy(payload["superseded_interpretation"])
            former_payload = {"source_observation": history["former_source_observation"], "host": history["former_host"], "effective_void": history["former_effective_void"], "jamb_before": history["former_jamb_before"], "jamb_after": history["former_jamb_after"]}
            if history["captured_payload_sha256"] != _hash(former_payload) or history["former_source_observation"] != opening["source_observation"]: raise ValueError("rehost history payload/source mismatch")
            if history["origin"] == "active_document":
                active_payload={"source_observation":opening["source_observation"],"host":opening["host"],"effective_void":opening["effective_void"],"jamb_before":opening["jamb_before"],"jamb_after":opening["jamb_after"]}
                if history["captured_from_structure_hash"] != original["structure_hash"] or history["captured_from_artifact_sha256"] is not None or former_payload != active_payload: raise ValueError("active-document history does not equal original active opening")
            elif history["origin"] == "external_artifact":
                approved={value for value in (evidence.get("approved_external_artifacts") or {}).values() if isinstance(value,str)} if isinstance(evidence,Mapping) else set()
                if history["captured_from_structure_hash"] is not None or history["captured_from_artifact_sha256"] != payload["approved_artifact_sha256"] or payload["approved_artifact_sha256"] not in approved or history["status"] != "rejected_by_source_evidence" or any(value is None for value in former_payload.values()): raise ValueError("rehost requires complete rejected external-artifact interpretation")
            else: raise ValueError("unsupported rehost history origin")
            opening["superseded_interpretations"].append(history)
            opening.update(source_observation=deepcopy(payload["new_source_observation"]), build_disposition="place_in_preexisting_gap", build_kind=payload["build_kind"], host=deepcopy(payload["host"]), effective_void=deepcopy(payload["effective_void"]), jamb_before=deepcopy(payload["jamb_before"]), jamb_after=deepcopy(payload["jamb_after"]), swing_direction=payload["swing_direction"], traversable=False, side_a_space_id=None, side_b_space_id=None, status="candidate")
            source_replacements.add(opening["id"])
            issue_update=payload["issue_update"]
            old_issue=next(row for row in result["unresolved_issues"] if row["id"]==issue_update["supersede_issue_id"] and row["status"]=="open")
            old_issue.update(status="superseded",blocks_reference_freeze=False,blocks_build=False,message=issue_update["superseded_message"])
            if any(row["id"]==issue_update["replacement_issue"]["id"] for row in result["unresolved_issues"]):raise ValueError("replacement issue ID already exists")
            result["unresolved_issues"].append(deepcopy(issue_update["replacement_issue"]))
        elif kind == "add_gap_portal_opening":
            if set(payload)!={"opening"}:raise ValueError("invalid add-gap payload")
            if operation["prior_payload_sha256"] != _hash(None) or payload["opening"]["id"] in openings: raise ValueError("new gap portal prior state is not empty")
            opening = deepcopy(payload["opening"]); openings[opening["id"]] = opening; result["opening_contract"]["openings"].append(opening)
        elif kind == "exclude_linear_feature":
            if set(payload)!={"branch_id","feature"}:raise ValueError("invalid exclude-feature payload")
            branch_id, feature = payload["branch_id"], deepcopy(payload["feature"])
            branch = next(row for row in result["wall_graph"]["branches"] if row["id"] == branch_id)
            if operation["prior_payload_sha256"] != _hash(branch): raise ValueError("exclude feature prior branch hash mismatch")
            atom_ids = {row["id"] for row in result["wall_graph"]["atoms"] if row["branch_id"] == branch_id}
            if any((opening.get("host") or {}).get("owning_wall_atom_id") in atom_ids for opening in openings.values()): raise ValueError("cannot exclude branch that actively hosts opening cut")
            result["wall_graph"]["branches"] = [row for row in result["wall_graph"]["branches"] if row["id"] != branch_id]
            result["wall_graph"]["atoms"] = [row for row in result["wall_graph"]["atoms"] if row["id"] not in atom_ids]
            kept_nodes = []
            for node in result["wall_graph"]["junctions"]:
                node["incidents"] = [row for row in node["incidents"] if row["atom_id"] not in atom_ids]
                if node["incidents"]: kept_nodes.append(node)
            result["wall_graph"]["junctions"] = kept_nodes
            result["source"]["excluded_linear_features"].append(feature)
        elif kind == "clip_effective_void":
            if set(payload)!={"opening_id","source_observation_sha256","build_kind","host","effective_void","jamb_before","jamb_after","swing_direction"}:raise ValueError("invalid clip-effective payload")
            opening = openings[payload["opening_id"]]
            if operation["prior_payload_sha256"] != _hash(opening) or payload["source_observation_sha256"] != _hash(opening["source_observation"]): raise ValueError("clip prior/nominal hash mismatch")
            opening.update(build_disposition="cut", build_kind=payload["build_kind"], host=deepcopy(payload["host"]), effective_void=deepcopy(payload["effective_void"]), jamb_before=deepcopy(payload["jamb_before"]), jamb_after=deepcopy(payload["jamb_after"]), swing_direction=payload["swing_direction"], traversable=False, side_a_space_id=None, side_b_space_id=None, status="candidate")
        elif kind == "classify_endpoint":
            if set(payload) != {"branch_id", "endpoint_index", "classification", "canonical_px", "metric_point_m", "evidence_refs"} or payload["classification"] not in {"wall_face_contact", "intentional_opening_termination", "joinery_wall_attachment", "intentional_joinery_free_end"}:
                raise ValueError("invalid endpoint classification payload")
            branch = next(row for row in result["wall_graph"]["branches"] if row["id"] == payload["branch_id"])
            if operation["prior_payload_sha256"] != _hash(branch): raise ValueError("endpoint prior branch hash mismatch")
            endpoint = int(payload["endpoint_index"])
            if payload.get("canonical_px") is not None:
                matrix = result["source"]["metric_registration"]["canonical_px_to_metric_3x3"]
                x, y = payload["canonical_px"]
                derived = [matrix[0][0]*x + matrix[0][1]*y + matrix[0][2], matrix[1][0]*x + matrix[1][1]*y + matrix[1][2]]
                if max(abs(derived[i]-payload["metric_point_m"][i]) for i in range(2)) > 1e-6: raise ValueError("endpoint canonical pixel/metric mismatch")
                branch["centerline_m"][endpoint] = deepcopy(payload["metric_point_m"])
                endpoint_atoms = [atom for atom in result["wall_graph"]["atoms"] if atom["branch_id"] == branch["id"] and math.isclose(float(atom["branch_interval"][endpoint]), float(endpoint), abs_tol=1e-9)]
                if len(endpoint_atoms) != 1: raise ValueError("endpoint classification does not map to exactly one atom")
                atom = endpoint_atoms[0]; atom["centerline_m"][endpoint] = deepcopy(payload["metric_point_m"])
                node_id = atom["start_node_id" if endpoint == 0 else "end_node_id"]
                node = next(row for row in result["wall_graph"]["junctions"] if row["id"] == node_id)
                node["axis_point_m"] = deepcopy(payload["metric_point_m"])
                incident = next(row for row in node["incidents"] if row["atom_id"] == atom["id"] and row["end"] == ("start" if endpoint == 0 else "end"))
                incident["contact_point_m"] = deepcopy(payload["metric_point_m"])
                node["termination_kind"] = "intentional_partition_end" if "intentional" in payload["classification"] else "source_termination"
                node["status"] = "candidate"
            branch["status"] = "candidate"
    result["opening_contract"]["openings"] = list(openings.values())
    for opening_id, before in nominal_before.items():
        if opening_id not in source_replacements and canonical_json(openings[opening_id]["source_observation"]) != before: raise ValueError("source correction mutated nominal opening observation")
    # Every source correction invalidates old semantic/reachability acceptance.
    result["adjacency_truth"]["status"] = "unresolved"
    result["adjacency_truth"]["entrance_opening_id"] = None
    for edge in result["adjacency_truth"]["edges"]: edge["status"] = "candidate"
    if result["adjacency_truth"]["status"]!="unresolved" or result["adjacency_truth"]["entrance_opening_id"] is not None or any(edge["status"]!="candidate" for edge in result["adjacency_truth"]["edges"]): raise ValueError("source correction failed to invalidate adjacency")
    result["structure_hash"] = compute_v21_structure_hash(result)
    result = validate_v21_document(result)
    readiness = assess_v21_build_readiness(result)
    if readiness["ready"]: raise ValueError("pending source correction must not become build-ready")
    return result, {"schema": "source-correction-application-v1", "source_structure_hash": original["structure_hash"], "result_structure_hash": result["structure_hash"], "manifest_hash": _hash(manifest), "operation_ids": [row["id"] for row in manifest["operations"]], "adjacency_invalidated": True, "ready": False, "mapping_metadata": v21_mapping_metadata(result)}

def apply_authorized_source_correction(wrapper):
    wrapper_keys={"schema","authority","verdict","application_authorized","build_authorized","scope","exact_inputs","constraints"}
    if not isinstance(wrapper,Mapping) or set(wrapper)!=wrapper_keys or wrapper.get("schema")!="goal-loop-v2-authorized-source-correction-v1" or wrapper.get("authority")!="independent_reference_reviewer" or wrapper.get("verdict")!="authorize_exact_source_correction" or wrapper.get("application_authorized") is not False or wrapper.get("build_authorized") is not False:raise ValueError("invalid authorized source-correction wrapper")
    exact=wrapper["exact_inputs"]
    if not isinstance(exact,Mapping) or set(exact)!={"source_document","evidence","manifest","result_candidate"}:raise ValueError("authorized wrapper exact_inputs mismatch")
    descriptor_keys={"source_document":{"path","file_sha256","structure_hash"},"evidence":{"path","file_sha256","canonical_sha256"},"manifest":{"path","file_sha256","canonical_sha256"},"result_candidate":{"path","file_sha256","structure_hash"}}
    values={};file_hashes={}
    for name,keys in descriptor_keys.items():
        descriptor=exact[name]
        if not isinstance(descriptor,Mapping) or set(descriptor)!=keys:raise ValueError(f"authorized wrapper {name} descriptor mismatch")
        path=Path(descriptor["path"]).expanduser().resolve()
        if not path.is_file():raise ValueError(f"authorized wrapper {name} path missing")
        payload=path.read_bytes();digest=hashlib.sha256(payload).hexdigest();file_hashes[name]=digest
        if digest!=descriptor["file_sha256"]:raise ValueError(f"authorized wrapper {name} byte hash mismatch")
        try:value=json.loads(payload.decode("utf-8"))
        except (UnicodeError,json.JSONDecodeError) as exc:raise ValueError(f"authorized wrapper {name} JSON invalid") from exc
        if "canonical_sha256" in descriptor and _hash(value)!=descriptor["canonical_sha256"]:raise ValueError(f"authorized wrapper {name} canonical hash mismatch")
        if "structure_hash" in descriptor and (not isinstance(value,Mapping) or value.get("structure_hash")!=descriptor["structure_hash"]):raise ValueError(f"authorized wrapper {name} structure hash mismatch")
        values[name]=value
    recomputed,inner_report=apply_source_corrections(values["source_document"],values["evidence"],values["manifest"])
    if canonical_json(recomputed)!=canonical_json(values["result_candidate"]):raise ValueError("authorized source correction result differs from exact result candidate")
    if inner_report.get("ready") is not False or assess_v21_build_readiness(recomputed)["ready"] is not False:raise ValueError("authorized source correction must remain not build-ready")
    report={"schema":"authorized-source-correction-application-v1","authorized_wrapper_canonical_sha256":_hash(wrapper),"source_structure_hash":values["source_document"]["structure_hash"],"result_structure_hash":recomputed["structure_hash"],"exact_input_file_sha256":file_hashes,"canonical_result_equal":True,"ready":False,"inner_application":inner_report}
    return recomputed,report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("document", "evidence", "manifest"): parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--authorized-wrapper",type=Path)
    parser.add_argument("--output-document", required=True, type=Path); parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.authorized_wrapper:
        wrapper=json.loads(args.authorized_wrapper.read_text(encoding="utf-8"));result,report=apply_authorized_source_correction(wrapper)
    else:
        if not all((args.document,args.evidence,args.manifest)):parser.error("--document, --evidence and --manifest are required without --authorized-wrapper")
        document, evidence, manifest = [json.loads(getattr(args,name).read_text(encoding="utf-8")) for name in ("document","evidence","manifest")];result, report = apply_source_corrections(document,evidence,manifest)
    args.output_document.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    args.report.write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
