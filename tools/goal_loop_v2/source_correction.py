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
from tools.fastloop_research.v21_contract import compute_v21_structure_hash, validate_v21_document, assess_v21_build_readiness, v21_mapping_metadata


ALLOWED = {"rehost_opening_as_gap_portal", "add_gap_portal_opening", "exclude_linear_feature", "clip_effective_void", "classify_endpoint"}


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
        if kind == "rehost_opening_as_gap_portal":
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
    if adjacency_before == result["adjacency_truth"]: raise ValueError("source correction failed to invalidate adjacency")
    result["structure_hash"] = compute_v21_structure_hash(result)
    result = validate_v21_document(result)
    readiness = assess_v21_build_readiness(result)
    if readiness["ready"]: raise ValueError("pending source correction must not become build-ready")
    return result, {"schema": "source-correction-application-v1", "source_structure_hash": original["structure_hash"], "result_structure_hash": result["structure_hash"], "manifest_hash": _hash(manifest), "operation_ids": [row["id"] for row in manifest["operations"]], "adjacency_invalidated": True, "ready": False, "mapping_metadata": v21_mapping_metadata(result)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("document", "evidence", "manifest"): parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--output-document", required=True, type=Path); parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    document, evidence, manifest = [json.loads(getattr(args,name).read_text(encoding="utf-8")) for name in ("document","evidence","manifest")]
    result, report = apply_source_corrections(document,evidence,manifest)
    args.output_document.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    args.report.write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
