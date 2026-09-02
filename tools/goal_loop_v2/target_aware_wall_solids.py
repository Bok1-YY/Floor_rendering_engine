"""Fail-closed target-aware wall-solid candidate."""
from __future__ import annotations
from copy import deepcopy
import hashlib
from typing import Any, Mapping

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.endpoint_policy_inventory import build_endpoint_policy_inventory
from tools.goal_loop_v2.face_abutment_targets import build_face_abutment_targets
from tools.goal_loop_v2.junction_wall_solids import _atom_geometry, _canonical_surface, _require_shapely, _topology

SCHEMA="target-aware-wall-solid-candidate-v1"
SELECTED_TOLERANCE_M=1e-6
SENSITIVITY_TOLERANCES_M=[0.0,3e-7,1e-6,5e-6,5e-5]

def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()

def _build_geometry(doc,endpoint,target,tolerance):
    atoms={a['id']:a for a in doc['wall_graph']['atoms']};ers={(r['atom_id'],r['endpoint_index']):r for r in endpoint['records']};trs={(r['atom_id'],r['endpoint_index']):r for r in target['records']};geoms=[];extensions=[]
    for aid,a in atoms.items():
        values=[]
        for i in (0,1):
            e=ers[(aid,i)]
            if e['policy_candidate']=='free_end':value=a['thickness_m']/2
            elif e['policy_candidate']=='multiway_junction_candidate':value=0.0
            else:
                t=trs[(aid,i)];value=t['face_gap_m']+tolerance if t['target_status']=='resolved_unique_candidate' else 0.0
            values.append(value);extensions.append({'atom_id':aid,'endpoint_index':i,'extension_m':value,'policy':e['policy_candidate'],'target_status':trs.get((aid,i),{}).get('target_status'),'target_atom_id':trs.get((aid,i),{}).get('target_atom_id'),'confirmation':False})
        geoms.append(_atom_geometry(a,values[0],values[1],Polygon))
    return unary_union(geoms),extensions

def _groups(doc,wall):
    outer=Polygon(doc['outer_boundary']['polygon_m']);free=outer.difference(wall.intersection(outer));faces=[free] if free.geom_type=='Polygon' else [g for g in free.geoms if g.geom_type=='Polygon'];groups=[]
    for face in faces:
        ids=sorted(s['id'] for s in doc['spaces'] if face.covers(Point(s['point_m'])))
        if ids:groups.append(ids)
    return sorted(groups)

def build_target_aware_wall_solids(document:Mapping[str,Any],*,_skip_validate=False):
    doc=validate_v21_document(document);endpoint=build_endpoint_policy_inventory(doc);target=build_face_abutment_targets(doc);_,_,orient,_=_require_shapely();wall,extensions=_build_geometry(doc,endpoint,target,SELECTED_TOLERANCE_M)
    result={'schema':SCHEMA,'source_structure_hash':doc['structure_hash'],'endpoint_inventory_hash':endpoint['candidate_hash'],'face_target_candidate_hash':target['candidate_hash'],'selected_numerical_tolerance_m':SELECTED_TOLERANCE_M,'extensions':extensions,'wall_union':{'solid_m':_canonical_surface(wall,orient,9),'geometry_hash':_hash(_canonical_surface(wall,orient,9)),'area_m2':round(wall.area,9)},'topology':_topology(doc,wall,9,.05),'anchor_groups':_groups(doc,wall),'sensitivity':[],'status':'pending_independent_review','wall_solid_confirmation':False,'room_topology_confirmation':False,'semantic_promotion':False,'build_authorized':False,'ready':False,'candidate_hash':'0'*64}
    for tolerance in SENSITIVITY_TOLERANCES_M:
        geometry,_=_build_geometry(doc,endpoint,target,tolerance);result['sensitivity'].append({'numerical_tolerance_m':tolerance,'topology':_topology(doc,geometry,9,.05),'anchor_groups':_groups(doc,geometry)})
    result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
    return result if _skip_validate else validate_target_aware_wall_solids(doc,result)

def validate_target_aware_wall_solids(document,candidate):
    doc=validate_v21_document(document)
    if candidate.get('schema')!=SCHEMA or candidate.get('source_structure_hash')!=doc['structure_hash']:raise ValueError('target-aware wall source/schema drift')
    for key in ('wall_solid_confirmation','room_topology_confirmation','semantic_promotion','build_authorized','ready'):
        if candidate.get(key) is not False:raise ValueError('target-aware wall candidate was promoted')
    if dict(candidate)!=build_target_aware_wall_solids(doc,_skip_validate=True):raise ValueError('target-aware wall geometry/topology drift')
    return deepcopy(dict(candidate))

__all__=['build_target_aware_wall_solids','validate_target_aware_wall_solids']
