"""Source-only opening→wall→space evidence for the V2.1 gate.

This is deliberately an evidence *candidate*: it records measurable links but
never promotes opening semantics, adjacency, or build readiness.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib, math
import json
from pathlib import Path
from typing import Any, Mapping
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document

def _hash(v: Any) -> str: return hashlib.sha256(canonical_json(v)).hexdigest()
def _dist(p,a,b):
    x,y=float(p[0]),float(p[1]); ax,ay=float(a[0]),float(a[1]); bx,by=float(b[0]),float(b[1])
    dx,dy=bx-ax,by-ay; t=max(0,min(1,((x-ax)*dx+(y-ay)*dy)/(dx*dx+dy*dy or 1)))
    return math.hypot(x-(ax+t*dx),y-(ay+t*dy))
def _mid(seg): return [(float(seg[0][0])+float(seg[1][0]))/2,(float(seg[0][1])+float(seg[1][1]))/2]

def _file_binding(role, path):
    p=Path(path).expanduser().resolve(); raw=p.read_bytes()
    if p.suffix.lower()==".json": canonical=_hash(json.loads(raw.decode("utf-8"))); media="application/json"
    else: canonical=hashlib.sha256(raw.replace(b"\r\n",b"\n").replace(b"\r",b"\n")).hexdigest(); media="application/octet-stream"
    return {"role":role,"path":str(p),"file_sha256":hashlib.sha256(raw).hexdigest(),"canonical_sha256":canonical,"media_type":media}

def build_opening_evidence_candidate(document: Mapping[str,Any], source_document_file=None, evidence_files=None) -> dict:
    doc=validate_v21_document(document)
    atoms=doc["wall_graph"]["atoms"]
    spaces=[{"id":s["id"],"name":s.get("name"),"status":"source_observed_space_candidate"} for s in doc["spaces"]]
    records=[]
    for opening in doc["opening_contract"]["openings"]:
        obs=opening["source_observation"]; seg=obs.get("nominal_segment_m")
        links=[]
        if isinstance(seg,list) and len(seg)==2:
            midpoint=_mid(seg)
            ranked=sorted(((_dist(midpoint,a["centerline_m"][0],a["centerline_m"][1]),a["id"]) for a in atoms),key=lambda x:(x[0],x[1]))
            links=[{"atom_id":aid,"midpoint_distance_m":round(distance,6),"segment_distance_m":round(min(_dist(seg[0], next(a for a in atoms if a["id"]==aid)["centerline_m"][0], next(a for a in atoms if a["id"]==aid)["centerline_m"][1]),_dist(seg[1], next(a for a in atoms if a["id"]==aid)["centerline_m"][0], next(a for a in atoms if a["id"]==aid)["centerline_m"][1])),6),"relation":"geometric_wall_candidate"} for distance,aid in ranked[:3]]
        records.append({"opening_id":opening["id"],"source_segment_m":deepcopy(seg),"source_kind":obs.get("kind"),"source_status":obs.get("status"),"wall_links":links,"side_a_space_id":opening.get("side_a_space_id"),"side_b_space_id":opening.get("side_b_space_id"),"space_relation_status":"candidate_only","semantic_promotion":False,"build_authorized":False})
    source_binding=None if source_document_file is None else _file_binding("source_document",source_document_file)
    evidence_bindings=[] if evidence_files is None else [_file_binding(role,path) for role,path in sorted(evidence_files.items())]
    result={"schema":"opening-wall-space-evidence-candidate-v1","source_structure_hash":doc["structure_hash"],"source_document":source_binding,"evidence_bindings":evidence_bindings,"openings":records,"spaces":spaces,"limitations":{"opening_semantics":False,"wall_ownership":False,"room_adjacency":False,"z_height":False,"build":False},"status":"pending_independent_review","build_authorized":False,"ready":False,"candidate_hash":"0"*64}
    result["candidate_hash"]=_hash({k:v for k,v in result.items() if k!="candidate_hash"})
    validate_opening_evidence_candidate(doc,result)
    return result

def validate_opening_evidence_candidate(document: Mapping[str,Any], candidate: Mapping[str,Any]) -> dict:
    doc=validate_v21_document(document)
    required={"schema","source_structure_hash","source_document","evidence_bindings","openings","spaces","limitations","status","build_authorized","ready","candidate_hash"}
    if not isinstance(candidate,Mapping) or set(candidate)!=required: raise ValueError("opening evidence candidate keys invalid")
    if candidate["schema"]!="opening-wall-space-evidence-candidate-v1" or candidate["source_structure_hash"]!=doc["structure_hash"] or candidate["status"]!="pending_independent_review" or candidate["build_authorized"] is not False or candidate["ready"] is not False: raise ValueError("opening evidence candidate is not source-only")
    if candidate["limitations"]!={"opening_semantics":False,"wall_ownership":False,"room_adjacency":False,"z_height":False,"build":False}: raise ValueError("opening evidence limitations leak claims")
    if candidate["source_document"] is not None and (set(candidate["source_document"])!={"role","path","file_sha256","canonical_sha256","media_type"} or candidate["source_document"]["role"]!="source_document"): raise ValueError("source provenance binding invalid")
    if {s.get("id") for s in candidate["spaces"]}!={s["id"] for s in doc["spaces"]}: raise ValueError("space evidence coverage mismatch")
    if len(candidate["openings"])!=len(doc["opening_contract"]["openings"]): raise ValueError("opening evidence coverage mismatch")
    source_openings={o["id"]:o for o in doc["opening_contract"]["openings"]}; atom_ids={a["id"] for a in doc["wall_graph"]["atoms"]}
    if {r.get("opening_id") for r in candidate["openings"]}!=set(source_openings): raise ValueError("opening evidence IDs mismatch")
    for row in candidate["openings"]:
        if row.get("semantic_promotion") is not False or row.get("build_authorized") is not False or row.get("space_relation_status")!="candidate_only": raise ValueError("opening evidence promoted semantics")
        source=source_openings[row["opening_id"]]; obs=source["source_observation"]
        if row.get("source_segment_m")!=obs.get("nominal_segment_m") or row.get("source_kind")!=obs.get("kind") or row.get("source_status")!=obs.get("status"): raise ValueError("opening source observation drift")
        if any(set(link)!={"atom_id","midpoint_distance_m","segment_distance_m","relation"} or link["atom_id"] not in atom_ids or link["relation"]!="geometric_wall_candidate" for link in row.get("wall_links",[])): raise ValueError("wall link provenance invalid")
    expected=_hash({k:v for k,v in candidate.items() if k!="candidate_hash"})
    if candidate["candidate_hash"]!=expected: raise ValueError("opening evidence hash drift")
    return deepcopy(dict(candidate))

__all__=["build_opening_evidence_candidate","validate_opening_evidence_candidate"]
