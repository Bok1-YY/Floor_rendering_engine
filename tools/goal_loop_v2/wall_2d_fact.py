"""Independent, source-only wall 2D traceability facts for V2.1 documents."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path
from typing import Any, Mapping

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import assess_v21_build_readiness, validate_v21_document


ROLES={"audit","report","branches","atoms","junctions"}


def _hash(value:Any)->str:return hashlib.sha256(canonical_json(value)).hexdigest()
def _file_hash(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()


def _binding(role:str,path:str|Path)->dict:
    evidence_path=Path(path).expanduser().resolve();payload=evidence_path.read_bytes();suffix=evidence_path.suffix.lower()
    if suffix==".json":value=json.loads(payload.decode("utf-8"));media="application/json";canonical=_hash(value)
    elif suffix==".md":text=payload.decode("utf-8").replace("\r\n","\n").replace("\r","\n");media="text/markdown";canonical=hashlib.sha256(text.encode("utf-8")).hexdigest()
    else:raise ValueError("wall 2D evidence must be JSON or Markdown")
    return {"role":role,"media_type":media,"file_sha256":hashlib.sha256(payload).hexdigest(),"canonical_sha256":canonical}


def _same_points(left,right,tolerance=1e-6):
    return isinstance(left,list) and isinstance(right,list) and len(left)==len(right) and all(isinstance(a,list) and isinstance(b,list) and len(a)==len(b)==2 and max(abs(float(a[index])-float(b[index])) for index in range(2))<=tolerance for a,b in zip(left,right))


def _angle(segment):
    first,second=segment;return round(math.degrees(math.atan2(float(second[1])-float(first[1]),float(second[0])-float(first[0])))%180.0,9)


def wall_2d_snapshots(document:Mapping[str,Any])->dict:
    doc=validate_v21_document(document);graph=doc["wall_graph"]
    branches=[{"id":row["id"],"centerline_m":deepcopy(row["centerline_m"]),"angle_degrees":_angle(row["centerline_m"])} for row in sorted(graph["branches"],key=lambda item:item["id"])]
    atoms=[{"id":row["id"],"branch_id":row["branch_id"],"centerline_m":deepcopy(row["centerline_m"]),"angle_degrees":_angle(row["centerline_m"]),"thickness_m":row["thickness_m"],"start_node_id":row["start_node_id"],"end_node_id":row["end_node_id"]} for row in sorted(graph["atoms"],key=lambda item:item["id"])]
    junctions=[{"id":row["id"],"axis_point_m":deepcopy(row["axis_point_m"]),"incident_count":len(row["incidents"])} for row in sorted(graph["junctions"],key=lambda item:item["id"])]
    return {"branches":branches,"atoms":atoms,"junctions":junctions}


def _segments_intersect(first,second,tolerance=1e-6):
    a,b=first;c,d=second
    def cross(p,q,r):return (float(q[0])-float(p[0]))*(float(r[1])-float(p[1]))-(float(q[1])-float(p[1]))*(float(r[0])-float(p[0]))
    def on_segment(p,q,r):return min(float(p[0]),float(r[0]))-tolerance<=float(q[0])<=max(float(p[0]),float(r[0]))+tolerance and min(float(p[1]),float(r[1]))-tolerance<=float(q[1])<=max(float(p[1]),float(r[1]))+tolerance
    values=(cross(a,b,c),cross(a,b,d),cross(c,d,a),cross(c,d,b))
    if values[0]*values[1]<-tolerance and values[2]*values[3]<-tolerance:return True
    return (abs(values[0])<=tolerance and on_segment(a,c,b)) or (abs(values[1])<=tolerance and on_segment(a,d,b)) or (abs(values[2])<=tolerance and on_segment(c,a,d)) or (abs(values[3])<=tolerance and on_segment(c,b,d))


def _coverage(document,intersection_pairs):
    snapshots=wall_2d_snapshots(document);branch_ids=[row["id"] for row in snapshots["branches"]];atom_ids=[row["id"] for row in snapshots["atoms"]];junction_ids=[row["id"] for row in snapshots["junctions"]]
    pairs=sorted([{"branch_a":min(row["branch_a"],row["branch_b"]),"branch_b":max(row["branch_a"],row["branch_b"])} for row in intersection_pairs],key=lambda row:(row["branch_a"],row["branch_b"]))
    if len({(row["branch_a"],row["branch_b"]) for row in pairs})!=len(pairs) or any(row["branch_a"]==row["branch_b"] or row["branch_a"] not in branch_ids or row["branch_b"] not in branch_ids for row in pairs):raise ValueError("wall 2D intersections have invalid branch identities")
    branches={row["id"]:row["centerline_m"] for row in snapshots["branches"]}
    if any(not _segments_intersect(branches[row["branch_a"]],branches[row["branch_b"]]) for row in pairs):raise ValueError("wall 2D intersection evidence does not intersect")
    actual={(min(first,second),max(first,second)) for first,second in combinations(branch_ids,2) if _segments_intersect(branches[first],branches[second])}
    if {(row["branch_a"],row["branch_b"]) for row in pairs}!=actual:raise ValueError("wall 2D intersection evidence is incomplete or extra")
    return {"branch_ids":branch_ids,"atom_ids":atom_ids,"junction_ids":junction_ids,"intersection_pairs":pairs,"branch_trace_hash":_hash(snapshots["branches"]),"atom_trace_hash":_hash(snapshots["atoms"]),"junction_trace_hash":_hash(snapshots["junctions"]),"intersection_hash":_hash(pairs)}


def _actual_inputs(document,source_document_file,evidence_files):
    doc=validate_v21_document(document);source_path=Path(source_document_file).expanduser().resolve()
    if set(evidence_files)!=ROLES:raise ValueError("wall 2D evidence roles must be exact")
    source_value=json.loads(source_path.read_text(encoding="utf-8"))
    if canonical_json(source_value)!=canonical_json(doc):raise ValueError("wall 2D source file differs from document")
    paths={role:Path(path).expanduser().resolve() for role,path in evidence_files.items()};bindings=sorted([_binding(role,path) for role,path in paths.items()],key=lambda row:row["role"])
    audit=json.loads(paths["audit"].read_text(encoding="utf-8"));branches=json.loads(paths["branches"].read_text(encoding="utf-8"));atoms=json.loads(paths["atoms"].read_text(encoding="utf-8"));junctions=json.loads(paths["junctions"].read_text(encoding="utf-8"))
    if not isinstance(audit,Mapping) or not isinstance(audit.get("schema"),str) or not audit["schema"].endswith("wall-graph-audit-v1") or audit.get("input_json_sha256")!=_file_hash(source_path):raise ValueError("wall 2D audit source identity mismatch")
    if audit.get("branches")!=branches or audit.get("atoms")!=atoms or audit.get("junctions")!=junctions:raise ValueError("wall 2D split evidence differs from audit report")
    snapshots=wall_2d_snapshots(doc);branch_source={row["id"]:row for row in snapshots["branches"]};atom_source={row["id"]:row for row in snapshots["atoms"]};junction_source={row["id"]:row for row in snapshots["junctions"]}
    if {row.get("id") for row in branches}!=set(branch_source) or {row.get("id") for row in atoms}!=set(atom_source) or {row.get("id") for row in junctions}!=set(junction_source):raise ValueError("wall 2D audit entity coverage is partial or extra")
    for row in branches:
        source=branch_source[row["id"]]
        if row.get("status")!="source_confirmed_geometry" or not _same_points(row.get("centerline_m"),source["centerline_m"]):raise ValueError("wall 2D branch trace differs from source")
    for row in atoms:
        source=atom_source[row["id"]]
        if row.get("status")!="source_confirmed_geometry" or row.get("branch_id")!=source["branch_id"] or row.get("start_node_id")!=source["start_node_id"] or row.get("end_node_id")!=source["end_node_id"] or not math.isclose(float(row.get("thickness_m")),float(source["thickness_m"]),abs_tol=1e-6) or not _same_points(row.get("centerline_m"),source["centerline_m"]):raise ValueError("wall 2D atom trace/thickness/topology differs from source")
    for row in junctions:
        source=junction_source[row["id"]]
        if row.get("status")!="source_confirmed_geometry" or int(row.get("incident_count",-1))!=source["incident_count"] or not _same_points([row.get("axis_point_m")],[source["axis_point_m"]]):raise ValueError("wall 2D junction trace differs from source")
    intersections=audit.get("topology_intersections")
    if not isinstance(intersections,list) or any(row.get("status")!="source_confirmed_intersection" for row in intersections):raise ValueError("wall 2D intersection evidence incomplete")
    coverage=_coverage(doc,intersections);summary=audit.get("summary")
    expected_counts={"branches_total":len(coverage["branch_ids"]),"branches_confirmed":len(coverage["branch_ids"]),"atoms_total":len(coverage["atom_ids"]),"atoms_confirmed":len(coverage["atom_ids"]),"junctions_total":len(coverage["junction_ids"]),"junctions_confirmed":len(coverage["junction_ids"])}
    if not isinstance(summary,Mapping) or any(summary.get(key)!=value for key,value in expected_counts.items()):raise ValueError("wall 2D audit summary is incomplete")
    return doc,{"path":str(source_path),"file_sha256":_file_hash(source_path),"structure_hash":doc["structure_hash"],"canonical_sha256":_hash(doc)},bindings,coverage


def compute_candidate_hash(candidate):
    payload=deepcopy(dict(candidate));payload.pop("candidate_hash",None);return _hash(payload)


def validate_wall_2d_candidate(document,candidate):
    doc=validate_v21_document(document);keys={"schema","source_document","evidence_bindings","fact","status","build_authorized","ready","candidate_hash"}
    if not isinstance(candidate,Mapping) or set(candidate)!=keys or candidate.get("schema")!="wall-2d-geometry-fact-candidate-v1" or candidate.get("status")!="pending_independent_review" or candidate.get("build_authorized") is not False or candidate.get("ready") is not False:raise ValueError("invalid pending wall 2D fact candidate")
    source=candidate["source_document"]
    if not isinstance(source,Mapping) or set(source)!={"path","file_sha256","structure_hash","canonical_sha256"} or source["structure_hash"]!=doc["structure_hash"] or source["canonical_sha256"]!=_hash(doc) or any(not isinstance(source[field],str) or len(source[field])!=64 or any(ch not in "0123456789abcdef" for ch in source[field]) for field in ("file_sha256","structure_hash","canonical_sha256")):raise ValueError("wall 2D candidate has stale source identity")
    bindings=candidate["evidence_bindings"]
    if not isinstance(bindings,list) or {row.get("role") for row in bindings if isinstance(row,Mapping)}!=ROLES or len(bindings)!=len(ROLES) or any(set(row)!={"role","media_type","file_sha256","canonical_sha256"} or row["media_type"] not in {"application/json","text/markdown"} or any(not isinstance(row[field],str) or len(row[field])!=64 or any(ch not in "0123456789abcdef" for ch in row[field]) for field in ("file_sha256","canonical_sha256")) for row in bindings):raise ValueError("wall 2D candidate evidence bindings invalid")
    fact=candidate["fact"];fact_keys={"kind","coverage","limitations"}
    if not isinstance(fact,Mapping) or set(fact)!=fact_keys or fact["kind"]!="wall_2d_geometry_fact" or fact["limitations"]!={"z_height":False,"solid_continuity":False,"jambs":False,"semantics":False,"adjacency":False,"build":False}:raise ValueError("wall 2D fact leaks unsupported claims")
    coverage=fact["coverage"];coverage_keys={"branch_ids","atom_ids","junction_ids","intersection_pairs","branch_trace_hash","atom_trace_hash","junction_trace_hash","intersection_hash"}
    if not isinstance(coverage,Mapping) or set(coverage)!=coverage_keys or coverage!=_coverage(doc,coverage["intersection_pairs"]):raise ValueError("wall 2D fact coverage is partial, stale, or altered")
    if candidate["candidate_hash"]!=compute_candidate_hash(candidate):raise ValueError("wall 2D candidate hash drift")
    return deepcopy(dict(candidate))


def build_wall_2d_candidate(document,source_document_file,evidence_files):
    doc,source,bindings,coverage=_actual_inputs(document,source_document_file,evidence_files)
    candidate={"schema":"wall-2d-geometry-fact-candidate-v1","source_document":source,"evidence_bindings":bindings,"fact":{"kind":"wall_2d_geometry_fact","coverage":coverage,"limitations":{"z_height":False,"solid_continuity":False,"jambs":False,"semantics":False,"adjacency":False,"build":False}},"status":"pending_independent_review","build_authorized":False,"ready":False,"candidate_hash":"0"*64}
    candidate["candidate_hash"]=compute_candidate_hash(candidate);validate_wall_2d_candidate(doc,candidate);return candidate


def validate_authorized_wall_2d_fact(document,fact):
    doc=validate_v21_document(document);keys={"schema","authority","verdict","candidate","candidate_hash","status","document_mutated","build_authorized","ready"}
    if not isinstance(fact,Mapping) or set(fact)!=keys or fact.get("schema")!="authorized-wall-2d-geometry-fact-v1" or fact.get("authority")!="independent_reference_reviewer" or fact.get("verdict")!="authorize_exact_wall_2d_geometry_fact" or fact.get("status")!="independently_authorized" or fact.get("document_mutated") is not False or fact.get("build_authorized") is not False or fact.get("ready") is not False:raise ValueError("invalid authorized wall 2D fact")
    candidate=validate_wall_2d_candidate(doc,fact["candidate"])
    if fact["candidate_hash"]!=candidate["candidate_hash"]:raise ValueError("authorized wall 2D candidate hash mismatch")
    return deepcopy(dict(fact))


def apply_authorized_wall_2d_fact(document,source_document_file,evidence_files,wrapper):
    doc=validate_v21_document(document);before=canonical_json(doc)
    if not isinstance(wrapper,Mapping) or wrapper.get("schema") not in {"wall-2d-geometry-fact-authorization-v1","wall-2d-geometry-fact-verdict-v1"} or wrapper.get("authority")!="independent_reference_reviewer" or wrapper.get("verdict")!="authorize_exact_wall_2d_geometry_fact" or wrapper.get("build_authorized") is not False:raise ValueError("invalid independently authorized wall 2D wrapper")
    recomputed=build_wall_2d_candidate(doc,source_document_file,evidence_files)
    candidate=wrapper.get("candidate",recomputed)
    if wrapper.get("candidate") is not None and canonical_json(recomputed)!=canonical_json(candidate):raise ValueError("wall 2D wrapper differs from actual evidence recomputation")
    if wrapper.get("candidate_hash")!=recomputed["candidate_hash"]:raise ValueError("authorized wall 2D candidate hash mismatch")
    if assess_v21_build_readiness(doc)["ready"] is not False:raise ValueError("wall 2D fact cannot be applied to a build-ready document")
    fact={"schema":"authorized-wall-2d-geometry-fact-v1","authority":wrapper["authority"],"verdict":wrapper["verdict"],"candidate":candidate,"candidate_hash":candidate["candidate_hash"],"status":"independently_authorized","document_mutated":False,"build_authorized":False,"ready":False}
    validate_authorized_wall_2d_fact(doc,fact)
    if canonical_json(doc)!=before:raise ValueError("wall 2D fact mutated source document")
    report={"schema":"wall-2d-geometry-fact-application-v1","source_structure_hash":doc["structure_hash"],"candidate_hash":candidate["candidate_hash"],"document_mutated":False,"build_authorized":False,"ready":False}
    return fact,report


__all__=["wall_2d_snapshots","build_wall_2d_candidate","validate_wall_2d_candidate","apply_authorized_wall_2d_fact","validate_authorized_wall_2d_fact"]
