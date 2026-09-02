"""Fail-closed OP002 physical cut plus topology-only closure candidate."""
from __future__ import annotations
from copy import deepcopy
import argparse, hashlib, json, math
from pathlib import Path
from typing import Any, Mapping

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.junction_wall_solids import build_junction_wall_solid_candidate, _polygon_parts, _topology
from tools.goal_loop_v2.op002_opening_cut import build_op002_opening_cut_candidate, _cut_polygon, _surface_geometry
from tools.goal_loop_v2.op002_topology_tolerance import build_op002_topology_tolerance

SCHEMA = "op002-cut-closure-candidate-v1"
ALLOWED_OPENING_ID = "OP002"
PHYSICAL_CLEARANCE_M = 1e-6
CLOSURE_HALF_WIDTH_M = 1e-6
SENSITIVITY_HALF_WIDTHS_M = [1e-9, 1e-8, 1e-7, 1e-6, 1e-5, 5e-5]
ENDPOINT_DELTAS_M = [-0.0293, -0.001, -0.000001, 0.0, 0.000001, 0.001, 0.0293]

def _hash(v: Any) -> str: return hashlib.sha256(canonical_json(v)).hexdigest()

def _groups(doc, geom):
    outer = Polygon(doc["outer_boundary"]["polygon_m"])
    free = outer.difference(geom.intersection(outer))
    faces = [p for p in _polygon_parts(free) if p.area >= .05]
    groups = [[] for _ in faces]; relations = {}
    for s in sorted(doc["spaces"], key=lambda x: x["id"]):
        p = Point(s["point_m"]); hits = [i for i,f in enumerate(faces) if f.contains(p)]
        if len(hits) == 1: groups[hits[0]].append(s["id"]); relations[s["id"]] = hits[0]
        else: relations[s["id"]] = None
    return {"face_count": len(faces), "anchor_groups": groups, "anchor_membership": relations,
            "single_anchor_face_count": sum(len(g)==1 for g in groups),
            "multi_anchor_face_count": sum(len(g)>1 for g in groups),
            "unlabeled_face_count": sum(not g for g in groups)}

def _closure_geometry(physical_geom, seg, half_width=CLOSURE_HALF_WIDTH_M, closure_endpoint_delta=0.0):
    p0, p1 = [tuple(map(float, point)) for point in seg]
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy)
    tx, ty = dx / length, dy / length
    closure_segment = [
        [p0[0] - tx * closure_endpoint_delta, p0[1] - ty * closure_endpoint_delta],
        [p1[0] + tx * closure_endpoint_delta, p1[1] + ty * closure_endpoint_delta],
    ]
    closure = LineString(closure_segment).buffer(half_width, cap_style=2, join_style=2)
    closure_geom = physical_geom.union(closure)
    return closure, closure_geom

def build_op002_cut_closure_candidate(document: Mapping[str, Any], *, _skip_validate=False):
    doc = validate_v21_document(document)
    wall = build_junction_wall_solid_candidate(doc)
    cut = build_op002_opening_cut_candidate(doc)
    if cut["opening_id"] != ALLOWED_OPENING_ID: raise ValueError("OP002 allowlist violation")
    tolerance = build_op002_topology_tolerance(doc)
    opening = next(x for x in doc["opening_contract"]["openings"] if x["id"] == ALLOWED_OPENING_ID)
    host = next(x for x in doc["wall_graph"]["atoms"] if x["id"] == opening["host"]["owning_wall_atom_id"])
    seg = deepcopy(opening["effective_void"]["segment_m"])
    base = _surface_geometry(wall["wall_union"]["solid_m"])
    cut_shape = _cut_polygon(seg, host["thickness_m"], 0.0, PHYSICAL_CLEARANCE_M)
    physical = base.difference(cut_shape)
    closure, closed = _closure_geometry(physical, seg)
    result = {"schema": SCHEMA, "source_structure_hash": doc["structure_hash"], "opening_id": ALLOWED_OPENING_ID,
      "wall_candidate_hash": wall["candidate_hash"], "opening_cut_candidate_hash": cut["candidate_hash"],
      "topology_tolerance_candidate_hash": tolerance["candidate_hash"],
      "physical_clearance_m": PHYSICAL_CLEARANCE_M, "closure_half_width_m": CLOSURE_HALF_WIDTH_M,
      "segment_m": seg, "physical_cut_geometry_hash": _hash(list(cut_shape.exterior.coords)),
      "closure_geometry_hash": _hash(list(closure.exterior.coords)), "physical_topology": _topology(doc, physical, 9, .05),
      "physical_membership": _groups(doc, physical), "closure_topology": _topology(doc, closed, 9, .05),
      "closure_membership": _groups(doc, closed), "closure_anchor_groups": _groups(doc, closed)["anchor_groups"],
      "sensitivity": {"half_width_m": [], "endpoint_delta_m": []},
      "limitations": {"opening_semantics_confirmed":False,"room_pair_confirmed":False,"adjacency_confirmed":False,"source_geometry_confirmed":False,"score_effect":False,"build":False},
      "status":"pending_independent_review", "cut_confirmation":False,"semantic_promotion":False,"build_authorized":False,"ready":False,"candidate_hash":"0"*64}
    for h in SENSITIVITY_HALF_WIDTHS_M:
        _closure, cg = _closure_geometry(physical, seg, h); result["sensitivity"]["half_width_m"].append({"half_width_m":h,"topology":_topology(doc,cg,9,.05),"membership":_groups(doc,cg)})
    for d in ENDPOINT_DELTAS_M:
        _closure, cg = _closure_geometry(physical, seg, CLOSURE_HALF_WIDTH_M, d); result["sensitivity"]["endpoint_delta_m"].append({"endpoint_delta_m":d,"physical_topology":_topology(doc,physical,9,.05),"closure_topology":_topology(doc,cg,9,.05),"closure_membership":_groups(doc,cg)})
    result["candidate_hash"] = _hash({k:v for k,v in result.items() if k!="candidate_hash"})
    return result if _skip_validate else validate_op002_cut_closure_candidate(doc,result)

def validate_op002_cut_closure_candidate(document, candidate):
    doc=validate_v21_document(document)
    if candidate.get("schema")!=SCHEMA or candidate.get("opening_id")!=ALLOWED_OPENING_ID: raise ValueError("OP002 schema/allowlist violation")
    for k in ("cut_confirmation","semantic_promotion","build_authorized","ready"):
        if candidate.get(k) is not False: raise ValueError("OP002 cut-closure candidate was promoted")
    expected=build_op002_cut_closure_candidate(doc,_skip_validate=True)
    if dict(candidate)!=expected: raise ValueError("OP002 cut-closure geometry/topology drift")
    if candidate["candidate_hash"]!=_hash({k:v for k,v in candidate.items() if k!="candidate_hash"}): raise ValueError("OP002 cut-closure hash drift")
    return deepcopy(dict(candidate))

def _main():
    p=argparse.ArgumentParser(); p.add_argument("--source",required=True,type=Path); p.add_argument("--output",required=True,type=Path); a=p.parse_args()
    c=build_op002_cut_closure_candidate(json.loads(a.source.read_text(encoding="utf-8"))); a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(c,sort_keys=True,indent=2)+"\n",encoding="utf-8"); print(json.dumps({"output":str(a.output.resolve()),"candidate_hash":c["candidate_hash"]}))
    return 0
if __name__=="__main__": raise SystemExit(_main())
