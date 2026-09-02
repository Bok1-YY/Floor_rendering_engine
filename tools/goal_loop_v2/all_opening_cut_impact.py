"""Fail-closed prototype measuring candidate opening cut impacts."""
from pathlib import Path
import hashlib,json,math
from shapely.geometry import Point,Polygon
from tools.fastloop_research.contract import canonical_json
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids
from tools.goal_loop_v2.opening_side_candidates import build_opening_side_space_candidate
from tools.goal_loop_v2.op002_opening_cut import _cut_polygon
from tools.goal_loop_v2.op002_opening_cut import _surface_geometry
from tools.goal_loop_v2.junction_wall_solids import _polygon_parts

ROOT=Path(__file__).resolve().parents[2]; DENIED='PORTAL-WB011-WB006-01'; WIDTHS=[1e-9,1e-6,1e-5,5e-5]; DELTAS=[-1e-3,-1e-6,0,1e-6,1e-3]
def _hash(v): return hashlib.sha256(canonical_json(v)).hexdigest()
def _groups(doc,g):
 o=Polygon(doc['outer_boundary']['polygon_m']); f=o.difference(g.intersection(o)); parts=[p for p in _polygon_parts(f) if p.area>=.05]; out=[]
 for p in parts:
  ids=sorted(s['id'] for s in doc['spaces'] if p.covers(Point(s['point_m'])))
  if ids: out.append(ids)
 return sorted(out)
def build_all_opening_cut_impact(document):
 from tools.fastloop_research.v21_contract import validate_v21_document
 doc=validate_v21_document(document); wall=build_target_aware_wall_solids(doc); side=build_opening_side_space_candidate(doc); base=_surface_geometry(wall['wall_union']['solid_m']); before=_groups(doc,base); side_by={r['opening_id']:r for r in side['openings']}; rows=[]
 for o in doc['opening_contract']['openings']:
  oid=o['id']; row={'opening_id':oid,'source_segment_m':(o.get('source_observation') or {}).get('nominal_segment_m'),'classification':'not_cuttable','cuttable':False,'pre_cut_anchor_groups':before,'post_cut_anchor_groups':before,'merged_groups':[],'sensitivity':[],'semantic_promotion':False,'adjacency_confirmation':False,'build_authorized':False}
  host=o.get('host'); seg=(o.get('effective_void') or {}).get('segment_m')
  if oid==DENIED: rows.append(row); continue
  if not host or not seg or host.get('owning_wall_atom_id') is None: rows.append(row); continue
  atom=next((a for a in doc['wall_graph']['atoms'] if a['id']==host['owning_wall_atom_id']),None)
  if atom is None: rows.append(row); continue
  row['cuttable']=True; row['host_atom_id']=atom['id']; row['post_cut_anchor_groups']=_groups(doc,base.difference(_cut_polygon(seg,atom['thickness_m'],0,1e-6))); row['merged_groups']=[g for g in row['post_cut_anchor_groups'] if len(g)>1 and g not in before]; row['classification']='unique_single_to_single_pair' if len(row['merged_groups'])==1 and len(row['merged_groups'][0])==2 else ('group_ambiguous' if row['merged_groups'] else 'no_topology_change')
  for w in WIDTHS:
   gg=base.difference(_cut_polygon(seg,atom['thickness_m'],0,w)); row['sensitivity'].append({'half_width_m':w,'anchor_groups':_groups(doc,gg)})
  row['endpoint_sensitivity']=[{'endpoint_delta_m':d,'anchor_groups':_groups(doc,base.difference(_cut_polygon(seg,atom['thickness_m'],d,1e-6)))} for d in DELTAS]; rows.append(row)
 result={'schema':'all-opening-cut-impact-candidate-v1','source_structure_hash':doc['structure_hash'],'target_aware_wall_candidate_hash':wall['candidate_hash'],'opening_side_candidate_hash':side['candidate_hash'],'openings':rows,'denied_ids':[DENIED],'semantic_promotion':False,'adjacency_confirmation':False,'build_authorized':False,'ready':False,'candidate_hash':'0'*64}; result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'}); return result
if __name__=='__main__':
 p=Path('data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'); out=Path('reports/all_opening_cut_impact_20260902'); out.mkdir(parents=True,exist_ok=True); r=build_all_opening_cut_impact(json.loads(p.read_text())); (out/'all-opening-cut-impact.json').write_text(json.dumps(r,indent=2)+'\n'); print(r['candidate_hash'])
