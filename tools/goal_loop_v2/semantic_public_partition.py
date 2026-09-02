"""Research-only nearest-anchor partition of the public free-space face."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
from shapely.geometry import Point, Polygon, MultiPoint
from shapely.ops import voronoi_diagram, unary_union
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids
from tools.goal_loop_v2.candidate_opening_cut_impact import build_candidate_opening_cut_impact

ROOT=Path(__file__).resolve().parents[2]
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
def _canon(g): return [list(x) for x in g.exterior.coords] if g.geom_type=='Polygon' else []
def build_semantic_public_partition(document, cut_matrix_file=None):
 doc=validate_v21_document(document); wall=build_target_aware_wall_solids(doc); matrix=build_candidate_opening_cut_impact(doc)
 groups=wall['anchor_groups']; face_ids=next((g for g in groups if set(g)>=PUBLIC),None)
 if face_ids is None: raise ValueError('public multi-anchor face missing')
 outer=Polygon(doc['outer_boundary']['polygon_m']); base=_surface(wall); face=outer.difference(base.intersection(outer)); face=next(g for g in (face.geoms if hasattr(face,'geoms') else [face]) if set(s['id'] for s in doc['spaces'] if g.covers(Point(s['point_m'])))>=PUBLIC)
 anchors=[s for s in doc['spaces'] if s['id'] in PUBLIC]; cells=_cells(face,anchors)
 cell_rows=[{'space_id':c['space_id'],'polygon':_canon(c['polygon']),'polygon_hash':_hash(_canon(c['polygon']))} for c in cells]
 samples=[]
 for row in matrix['openings']:
  if row['classification']=='not_cuttable' or not row['merged_groups'] or not any(set(g)>=PUBLIC for g in row['post_cut_anchor_groups']): continue
  seg=row['segment_m']; x0,y0=seg[0]; x1,y1=seg[1]; dx,dy=x1-x0,y1-y0; import math; L=math.hypot(dx,dy); nx,ny=-dy/L,dx/L
  sides=[]
  for sign in (-1,1):
   vals=[]
   for eps in EPSILONS:
    p=Point((x0+x1)/2+sign*nx*eps,(y0+y1)/2+sign*ny*eps); hits=[c['space_id'] for c in cells if c['polygon'].covers(p)]; vals.append({'epsilon_m':eps,'cell_ids':hits,'tie':len(hits)!=1})
   sides.append({'sign':sign,'samples':vals,'boundary_or_tie_instability':len({tuple(v['cell_ids']) for v in vals})>1 or any(v['tie'] for v in vals)})
  samples.append({'opening_id':row['opening_id'],'sides':sides})
 result={'schema':'semantic-public-partition-candidate-v1','source_structure_hash':doc['structure_hash'],'wall_candidate_hash':wall['candidate_hash'],'cut_matrix_hash':matrix['candidate_hash'],'public_face_space_ids':sorted(PUBLIC),'cells':cell_rows,'opening_side_candidates':samples,'epsilon_sensitivity_m':list(EPSILONS),'research_only':True,'room_polygon_confirmation':False,'pair_confirmation':False,'adjacency_confirmation':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64}
 if cut_matrix_file: result['cut_matrix_binding']=_bind(cut_matrix_file)
 else: result['cut_matrix_binding']=None
 result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'}); return result
__all__=['build_semantic_public_partition']
