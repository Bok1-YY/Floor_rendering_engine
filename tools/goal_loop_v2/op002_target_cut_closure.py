"""Fail-closed OP002 cut/closure candidate on target-aware walls."""
from __future__ import annotations
from copy import deepcopy
import hashlib, math
from typing import Any, Mapping
from shapely.geometry import LineString

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.junction_wall_solids import _topology
from tools.goal_loop_v2.op002_opening_cut import _cut_polygon, _surface_geometry
from tools.goal_loop_v2.op002_cut_closure import _groups
from tools.goal_loop_v2.op002_topology_tolerance import build_op002_topology_tolerance
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids

SCHEMA='op002-target-aware-cut-closure-candidate-v1';OPENING_ID='OP002';CLEARANCE=1e-6;HALF_WIDTH=1e-6
HALF_WIDTHS=[1e-9,1e-8,1e-7,1e-6,1e-5,5e-5];ENDPOINT_DELTAS=[-0.0293,-0.001,-1e-6,0.0,1e-6,0.001,0.0293]
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()

def _closure(physical,segment,half_width,delta):
    p0,p1=[tuple(map(float,p)) for p in segment];dx,dy=p1[0]-p0[0],p1[1]-p0[1];length=math.hypot(dx,dy);tx,ty=dx/length,dy/length
    line=[[p0[0]-tx*delta,p0[1]-ty*delta],[p1[0]+tx*delta,p1[1]+ty*delta]]
    barrier=LineString(line).buffer(half_width,cap_style=2,join_style=2)
    return barrier,physical.union(barrier)

def build_op002_target_cut_closure(document:Mapping[str,Any],*,_skip_validate=False):
    doc=validate_v21_document(document);wall=build_target_aware_wall_solids(doc);tolerance=build_op002_topology_tolerance(doc)
    opening=next(x for x in doc['opening_contract']['openings'] if x['id']==OPENING_ID);host=next(x for x in doc['wall_graph']['atoms'] if x['id']==opening['host']['owning_wall_atom_id']);segment=deepcopy(opening['effective_void']['segment_m'])
    base=_surface_geometry(wall['wall_union']['solid_m']);cut=_cut_polygon(segment,host['thickness_m'],0,CLEARANCE);physical=base.difference(cut);barrier,closed=_closure(physical,segment,HALF_WIDTH,0)
    result={'schema':SCHEMA,'source_structure_hash':doc['structure_hash'],'opening_id':OPENING_ID,'target_wall_candidate_hash':wall['candidate_hash'],'topology_tolerance_candidate_hash':tolerance['candidate_hash'],'segment_m':segment,'physical_clearance_m':CLEARANCE,'closure_half_width_m':HALF_WIDTH,'physical_cut_geometry_hash':_hash(list(cut.exterior.coords)),'closure_geometry_hash':_hash(list(barrier.exterior.coords)),'baseline_topology':wall['topology'],'baseline_anchor_groups':wall['anchor_groups'],'physical_topology':_topology(doc,physical,9,.05),'physical_anchor_groups':_groups(doc,physical)['anchor_groups'],'closure_topology':_topology(doc,closed,9,.05),'closure_anchor_groups':_groups(doc,closed)['anchor_groups'],'sensitivity':{'half_width_m':[],'endpoint_delta_m':[]},'status':'pending_independent_review','cut_confirmation':False,'room_pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64}
    for width in HALF_WIDTHS:
        _,geometry=_closure(physical,segment,width,0);result['sensitivity']['half_width_m'].append({'half_width_m':width,'topology':_topology(doc,geometry,9,.05),'anchor_groups':_groups(doc,geometry)['anchor_groups']})
    for delta in ENDPOINT_DELTAS:
        _,geometry=_closure(physical,segment,HALF_WIDTH,delta);result['sensitivity']['endpoint_delta_m'].append({'endpoint_delta_m':delta,'topology':_topology(doc,geometry,9,.05),'anchor_groups':_groups(doc,geometry)['anchor_groups']})
    result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
    return result if _skip_validate else validate_op002_target_cut_closure(doc,result)

def validate_op002_target_cut_closure(document,candidate):
    doc=validate_v21_document(document)
    if candidate.get('schema')!=SCHEMA or candidate.get('opening_id')!=OPENING_ID:raise ValueError('target-aware OP002 schema/allowlist violation')
    for key in ('cut_confirmation','room_pair_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'):
        if candidate.get(key) is not False:raise ValueError('target-aware OP002 candidate was promoted')
    if dict(candidate)!=build_op002_target_cut_closure(doc,_skip_validate=True):raise ValueError('target-aware OP002 geometry/topology drift')
    return deepcopy(dict(candidate))

__all__=['build_op002_target_cut_closure','validate_op002_target_cut_closure']
