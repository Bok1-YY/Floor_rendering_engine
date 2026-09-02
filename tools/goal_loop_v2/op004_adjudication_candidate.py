"""Fail-closed OP004 geometry/adjudication candidate.

This packet records the geometric hypothesis only.  It deliberately cannot
authorize a cut, score, build, or promotion; every input file is re-read by
the validator so a forged/rehashed packet is rejected.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib, json, math
from pathlib import Path
from typing import Any, Mapping
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.opening_side_candidates import validate_opening_side_space_candidate
from tools.goal_loop_v2.target_aware_wall_solids import validate_target_aware_wall_solids

SCHEMA = "op004-geometry-adjudication-candidate-v1"
OPENING_ID = "OP004"
HOST_ID = "ATOM-WB007-01"
PAIR = ["north_toilet", "bedroom_02"]
BLOCKERS = ["SOURCE_HOST_MISSING", "SOURCE_EFFECTIVE_VOID_MISSING", "SOURCE_JAMB_MISSING", "SOURCE_SIDE_SPACES_MISSING", "SOURCE_STATUS_CANDIDATE", "GEMINI_ROUTE_FAILURE", "HUMAN_REVIEW_PENDING"]

def _hash(v: Any) -> str: return hashlib.sha256(canonical_json(v)).hexdigest()
def _file_binding(role: str, path: str | Path) -> dict[str, str]:
    p = Path(path).resolve(); raw = p.read_bytes()
    return {"role": role, "path": str(p), "file_sha256": hashlib.sha256(raw).hexdigest(), "canonical_sha256": _hash(json.loads(raw.decode("utf-8"))) if p.suffix.lower()==".json" else hashlib.sha256(raw.replace(b"\r\n",b"\n").replace(b"\r",b"\n")).hexdigest(), "media_type": "application/json" if p.suffix.lower()==".json" else "application/octet-stream"}

def _binding(value: Mapping[str, Any] | str | Path, role: str) -> dict[str, str]:
    if isinstance(value, Mapping): return dict(value)
    return _file_binding(role, value)

def _support(segment, host):
    a,b = segment; h0,h1 = host
    dx,dy=h1[0]-h0[0],h1[1]-h0[1]; den=dx*dx+dy*dy
    def projection(p): return ((p[0]-h0[0])*dx+(p[1]-h0[1])*dy)/den if den else 0.0
    vals=[projection(a),projection(b)]
    return {"host_atom_id":HOST_ID,"endpoint_support": [{"endpoint_index":i,"segment_endpoint_m":list(p),"host_parameter":round(vals[i],9),"supported":0.0<=vals[i]<=1.0} for i,p in enumerate((a,b))],"support_length_m":round(math.dist(a,b),9),"derivation":"segment endpoints projected onto host centerline"}

def build_op004_geometry_adjudication_candidate(document: Mapping[str,Any], evidence_file, opening_side_candidate, target_aware_wall_candidate, *, _skip_validate=False):
    doc=validate_v21_document(document); ev=json.loads(Path(evidence_file).read_text(encoding="utf-8")); row=next((x for x in ev.get("openings",[]) if x.get("opening_id")==OPENING_ID),None)
    if row is None: raise ValueError("OP004 evidence missing")
    host=next((x for x in row.get("host_wall_candidates",[]) if x.get("atom_id")==HOST_ID),None)
    if host is None: raise ValueError("OP004 host candidate missing")
    side = json.loads(Path(opening_side_candidate).read_text(encoding="utf-8")) if isinstance(opening_side_candidate,(str,Path)) else dict(opening_side_candidate)
    wall = json.loads(Path(target_aware_wall_candidate).read_text(encoding="utf-8")) if isinstance(target_aware_wall_candidate,(str,Path)) else dict(target_aware_wall_candidate)
    side = validate_opening_side_space_candidate(doc, side)
    wall = validate_target_aware_wall_solids(doc, wall)
    result={"schema":SCHEMA,"source_structure_hash":doc["structure_hash"],"opening_id":OPENING_ID,"source_evidence_binding":_file_binding("source_evidence",evidence_file),"opening_side_candidate_hash":side.get("candidate_hash"),"target_aware_wall_candidate_hash":wall.get("candidate_hash"),"registration":{"max_endpoint_error_px":row["registration"]["max_endpoint_error_px"],"tolerance_px":1.0,"passed":row["registration"]["max_endpoint_error_px"]<=1.0},"host_candidate":{"atom_id":HOST_ID,"segment_m":deepcopy(host["segment_m"]),"endpoint_distance_sum_m":host["endpoint_distance_sum_m"]},"room_pair_candidate":deepcopy(PAIR),"jamb_support":_support(row["source_segment_m"],host["segment_m"]),"remaining_blockers":deepcopy(BLOCKERS),"decision":"unresolved_candidate","status":"pending_human_review","cut_confirmation":False,"source_confirmation":False,"pair_confirmation":False,"semantic_promotion":False,"score_effect":"none","build_authorized":False,"ready":False,"candidate_hash":"0"*64}
    result["candidate_hash"]=_hash({k:v for k,v in result.items() if k!="candidate_hash"})
    return result if _skip_validate else validate_op004_geometry_adjudication_candidate(doc,evidence_file,opening_side_candidate,target_aware_wall_candidate,result)

def validate_op004_geometry_adjudication_candidate(document,evidence_file,opening_side_candidate,target_aware_wall_candidate,candidate):
    doc=validate_v21_document(document)
    if not isinstance(candidate,Mapping) or candidate.get("schema")!=SCHEMA: raise ValueError("OP004 candidate schema drift")
    for k in ("cut_confirmation","source_confirmation","pair_confirmation","semantic_promotion","build_authorized","ready"):
        if candidate.get(k) is not False: raise ValueError("OP004 candidate was promoted")
    expected=build_op004_geometry_adjudication_candidate(doc,evidence_file,opening_side_candidate,target_aware_wall_candidate,_skip_validate=True)
    if dict(candidate)!=expected: raise ValueError("OP004 geometry evidence drift")
    if candidate["candidate_hash"]!=_hash({k:v for k,v in candidate.items() if k!="candidate_hash"}): raise ValueError("OP004 candidate hash drift")
    return deepcopy(dict(candidate))

__all__=["build_op004_geometry_adjudication_candidate","validate_op004_geometry_adjudication_candidate"]
