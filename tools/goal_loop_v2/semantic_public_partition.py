"""Research-only nearest-anchor partition of the public free-space face."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from shapely.geometry import Point, Polygon, MultiPoint
from shapely.ops import voronoi_diagram, unary_union
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids
from tools.goal_loop_v2.candidate_opening_cut_impact import build_candidate_opening_cut_impact
from tools.goal_loop_v2.junction_wall_solids import _polygon_parts

MATRIX_FILE=ROOT/'reports/candidate_opening_cut_impact_20260902/candidate-opening-cut-impact.json'
EPSILONS=(0.001,0.01,0.05,0.1)
PUBLIC={'bedroom_corridor','kitchen','living_hall','lobby'}
def _hash(v): return hashlib.sha256(canonical_json(v)).hexdigest()
def _bind(p):
 p=Path(p).resolve(); raw=p.read_bytes(); return {'path':str(p),'file_sha256':hashlib.sha256(raw).hexdigest(),'canonical_sha256':_hash(json.loads(raw.decode()))}
def _surface(w): return unary_union([Polygon(p['exterior'],p.get('holes',[])) for p in w['wall_union']['solid_m']['polygons']])
def _cells(face, anchors):
    pts=MultiPoint([Point(a['point_m']) for a in anchors]); vd=voronoi_diagram(pts,envelope=face.envelope,edges=False)
    out=[]
    for a in anchors:
        p=Point(a['point_m']); cell=next((g for g in vd.geoms if g.covers(p)),None)
        out.append({'space_id':a['id'],'polygon':cell.intersection(face) if cell else Polygon()})
    return out
def _canon(g):
 parts=sorted(_polygon_parts(g),key=lambda p:(round(p.bounds[0],9),round(p.bounds[1],9),round(p.area,9)))
 return [{'exterior':[list(x) for x in p.exterior.coords],'holes':[[list(x) for x in ring.coords] for ring in p.interiors]} for p in parts]
def build_semantic_public_partition(document, cut_matrix_file=MATRIX_FILE, *, _skip_validate=False):
 doc=validate_v21_document(document); wall=build_target_aware_wall_solids(doc); matrix=build_candidate_opening_cut_impact(doc)
 groups=wall['anchor_groups']; face_ids=next((g for g in groups if set(g)>=PUBLIC),None)
 if face_ids is None: raise ValueError('public multi-anchor face missing')
 outer=Polygon(doc['outer_boundary']['polygon_m']); base=_surface(wall); face=outer.difference(base.intersection(outer)); face=next(g for g in (face.geoms if hasattr(face,'geoms') else [face]) if set(s['id'] for s in doc['spaces'] if g.covers(Point(s['point_m'])))>=PUBLIC)
 anchors=[s for s in doc['spaces'] if s['id'] in PUBLIC]; cells=_cells(face,anchors)
 cell_rows=[{'space_id':c['space_id'],'polygons':_canon(c['polygon']),'polygon_hash':_hash(_canon(c['polygon'])),'area_m2':round(c['polygon'].area,9),'anchor_covered':c['polygon'].covers(Point(next(a['point_m'] for a in anchors if a['id']==c['space_id'])))} for c in cells]
 overlap=max((cells[i]['polygon'].intersection(cells[j]['polygon']).area for i in range(len(cells)) for j in range(i+1,len(cells))),default=0);union_error=face.symmetric_difference(unary_union([c['polygon'] for c in cells])).area
 samples=[]
 for row in matrix['openings']:
  if row['classification']=='not_cuttable' or not any(PUBLIC.issubset(set(g)) for g in row['merged_groups']): continue
  seg=row['segment_m']; x0,y0=seg[0]; x1,y1=seg[1]; dx,dy=x1-x0,y1-y0; import math; L=math.hypot(dx,dy); nx,ny=-dy/L,dx/L;atom=next(a for a in doc['wall_graph']['atoms'] if a['id']==row['host_atom_id']);half=atom['thickness_m']/2
  sides=[]
  for sign in (-1,1):
   vals=[]
   for eps in EPSILONS:
    p=Point((x0+x1)/2+sign*nx*(half+eps),(y0+y1)/2+sign*ny*(half+eps)); hits=[c['space_id'] for c in cells if c['polygon'].covers(p)]; vals.append({'epsilon_m':eps,'offset_from_centerline_m':round(half+eps,9),'point_m':[p.x,p.y],'inside_public_face':face.covers(p),'cell_ids':hits,'tie':len(hits)>1})
   nonempty=[tuple(v['cell_ids']) for v in vals if v['cell_ids']];sides.append({'sign':sign,'side':'right' if sign==-1 else 'left','samples':vals,'stable_public_cell_id':nonempty[0][0] if nonempty and len(set(nonempty))==1 and len(nonempty[0])==1 else None,'boundary_or_tie_instability':len(set(nonempty))>1 or any(v['tie'] for v in vals)})
  samples.append({'opening_id':row['opening_id'],'host_atom_id':row['host_atom_id'],'host_half_thickness_m':half,'sides':sides})
 result={'schema':'semantic-public-partition-candidate-v2','source_structure_hash':doc['structure_hash'],'wall_candidate_hash':wall['candidate_hash'],'cut_matrix_hash':matrix['candidate_hash'],'public_face_space_ids':sorted(PUBLIC),'public_face_hash':_hash(_canon(face)),'public_face_area_m2':round(face.area,9),'cells':cell_rows,'partition_diagnostics':{'maximum_pairwise_overlap_m2':round(overlap,12),'union_symmetric_difference_m2':round(union_error,12),'all_anchors_covered':all(c['anchor_covered'] for c in cell_rows)},'opening_side_candidates':samples,'epsilon_sensitivity_m':list(EPSILONS),'research_only':True,'room_polygon_confirmation':False,'pair_confirmation':False,'adjacency_confirmation':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64}
 if cut_matrix_file: result['cut_matrix_binding']=_bind(cut_matrix_file)
 else: result['cut_matrix_binding']=None
 result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'}); return result if _skip_validate else validate_semantic_public_partition(doc,result,cut_matrix_file)
def validate_semantic_public_partition(document,candidate,cut_matrix_file=MATRIX_FILE):
 doc=validate_v21_document(document)
 if candidate.get('schema')!='semantic-public-partition-candidate-v2':raise ValueError('semantic partition schema drift')
 for key in ('room_polygon_confirmation','pair_confirmation','adjacency_confirmation','build_authorized','ready'):
  if candidate.get(key) is not False:raise ValueError('semantic partition was promoted')
 if candidate!=build_semantic_public_partition(doc,cut_matrix_file,_skip_validate=True):raise ValueError('semantic partition geometry/evidence drift')
 return json.loads(json.dumps(candidate))
def main():
 doc=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text());result=build_semantic_public_partition(doc);out=ROOT/'reports/semantic_public_partition_20260902';out.mkdir(parents=True,exist_ok=True);(out/'semantic-public-partition.json').write_text(json.dumps(result,indent=2)+'\n');print(result['candidate_hash'])
if __name__=='__main__':main()
__all__=['build_semantic_public_partition','validate_semantic_public_partition']
