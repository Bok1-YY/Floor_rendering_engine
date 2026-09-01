"""Source-only opening→wall→space evidence for the V2.1 gate.

This is deliberately an evidence *candidate*: it records measurable links but
never promotes opening semantics, adjacency, or build readiness.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib, math
from typing import Any, Mapping
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document

def _hash(v: Any) -> str: return hashlib.sha256(canonical_json(v)).hexdigest()
def _dist(p,a,b):
    x,y=float(p[0]),float(p[1]); ax,ay=float(a[0]),float(a[1]); bx,by=float(b[0]),float(b[1])
    dx,dy=bx-ax,by-ay; t=max(0,min(1,((x-ax)*dx+(y-ay)*dy)/(dx*dx+dy*dy or 1)))
    return math.hypot(x-(ax+t*dx),y-(ay+t*dy))
def _mid(seg): return [(float(seg[0][0])+float(seg[1][0]))/2,(float(seg[0][1])+float(seg[1][1]))/2]

def build_opening_evidence_candidate(document: Mapping[str,Any]) -> dict:
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
            links=[{"atom_id":aid,"midpoint_distance_m":round(distance,6),"relation":"geometric_wall_candidate"} for distance,aid in ranked[:3]]
        records.append({"opening_id":opening["id"],"source_segment_m":deepcopy(seg),"source_kind":obs.get("kind"),"source_status":obs.get("status"),"wall_links":links,"side_a_space_id":opening.get("side_a_space_id"),"side_b_space_id":opening.get("side_b_space_id"),"space_relation_status":"candidate_only","semantic_promotion":False,"build_authorized":False})
    result={"schema":"opening-wall-space-evidence-candidate-v1","source_structure_hash":doc["structure_hash"],"openings":records,"spaces":spaces,"limitations":{"opening_semantics":False,"wall_ownership":False,"room_adjacency":False,"z_height":False,"build":False},"status":"pending_independent_review","build_authorized":False,"ready":False,"candidate_hash":"0"*64}
    result["candidate_hash"]=_hash({k:v for k,v in result.items() if k!="candidate_hash"})
    validate_opening_evidence_candidate(doc,result)
    return result

def validate_opening_evidence_candidate(document: Mapping[str,Any], candidate: Mapping[str,Any]) -> dict:
    doc=validate_v21_document(document)
    required={"schema","source_structure_hash","openings","spaces","limitations","status","build_authorized","ready","candidate_hash"}
    if not isinstance(candidate,Mapping) or set(candidate)!=required: raise ValueError("opening evidence candidate keys invalid")
    if candidate["schema"]!="opening-wall-space-evidence-candidate-v1" or candidate["source_structure_hash"]!=doc["structure_hash"] or candidate["status"]!="pending_independent_review" or candidate["build_authorized"] is not False or candidate["ready"] is not False: raise ValueError("opening evidence candidate is not source-only")
    if candidate["limitations"]!={"opening_semantics":False,"wall_ownership":False,"room_adjacency":False,"z_height":False,"build":False}: raise ValueError("opening evidence limitations leak claims")
    if {s.get("id") for s in candidate["spaces"]}!={s["id"] for s in doc["spaces"]}: raise ValueError("space evidence coverage mismatch")
    if len(candidate["openings"])!=len(doc["opening_contract"]["openings"]): raise ValueError("opening evidence coverage mismatch")
    for row in candidate["openings"]:
        if row.get("semantic_promotion") is not False or row.get("build_authorized") is not False or row.get("space_relation_status")!="candidate_only": raise ValueError("opening evidence promoted semantics")
    expected=_hash({k:v for k,v in candidate.items() if k!="candidate_hash"})
    if candidate["candidate_hash"]!=expected: raise ValueError("opening evidence hash drift")
    return deepcopy(dict(candidate))

__all__=["build_opening_evidence_candidate","validate_opening_evidence_candidate"]
