"""Render partition-targeted, non-promoting adjudication evidence."""
from __future__ import annotations
import hashlib,json,math,sys
from pathlib import Path
from PIL import Image,ImageDraw
from shapely.geometry import Point,Polygon
from shapely.ops import unary_union

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.fastloop_research.contract import canonical_json
from tools.goal_loop_v2.junction_wall_solids import _polygon_parts
from tools.goal_loop_v2.jamb_policy import minimum_jamb_support_m
from tools.goal_loop_v2.op002_opening_cut import _surface_geometry
from tools.goal_loop_v2.registration import _inverse,_apply,validate_pixel_metric_segment
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids

SRC=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';REF=ROOT/'data/goal_loop_v2/references/1308/canonical-raw-portrait.png';PART=ROOT/'reports/semantic_public_partition_20260902/semantic-public-partition.json';MATRIX=ROOT/'reports/candidate_opening_cut_impact_20260902/candidate-opening-cut-impact.json';PAIR=ROOT/'reports/partition_resolved_cut_pair_20260902/partition-resolved-cut-pair.json';OUT=ROOT/'reports/partition_targeted_adjudication_20260902';IDS=('OP002','OP006','OP007','OP010')
def hs(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def h(v):return hashlib.sha256(canonical_json(v)).hexdigest()
def public_geometry(cell):return unary_union([Polygon(poly['exterior'],poly.get('holes',[])) for poly in cell['polygons']])
def host_geometry(atom,segment,policy_minimum):
 a,b=atom['centerline_m'];dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy);nx,ny=-dy/length,dx/length;half=atom['thickness_m']/2;faces=[[[a[0]+nx*half,a[1]+ny*half],[b[0]+nx*half,b[1]+ny*half]],[[a[0]-nx*half,a[1]-ny*half],[b[0]-nx*half,b[1]-ny*half]]];params=[((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(length*length) for p in segment];lo,hi=min(params),max(params);before=max(0,lo*length);after=max(0,(1-hi)*length);measured=min(before,after)
 return faces,{'host_parameters':[round(v,9) for v in params],'jamb_before_m':round(before,9),'jamb_after_m':round(after,9),'minimum_jamb_m':round(measured,9),'threshold_m':policy_minimum,'sufficient':measured>=policy_minimum,'policy_source':'opening_contract.minimum_jamb_support_m'}
def serialized_face(p,space_id):return {'space_ids':[space_id],'polygon_hash':h([list(x) for x in p.exterior.coords]),'polygon':[list(x) for x in p.exterior.coords]}
def draw(image,box,inv,segment,atom,public_geom,nonpublic,minimum):
 dr=ImageDraw.Draw(image);offset=(box[0],box[1]);px=lambda p:(lambda q:(q[0]-offset[0],q[1]-offset[1]))(_apply(inv,p));dr.line([px(p) for p in atom['centerline_m']],fill=(160,40,220),width=5);host_faces,_=host_geometry(atom,segment,minimum)
 for line in host_faces:dr.line([px(p) for p in line],fill=(20,100,255),width=5)
 for part in _polygon_parts(public_geom):
  poly=[px(q) for q in part.exterior.coords];dr.line(poly,fill=(0,220,220),width=5);dr.text(poly[0],'S-P',fill=(0,220,220),stroke_width=2,stroke_fill=(0,0,0))
 poly=[px(q) for q in nonpublic.exterior.coords];dr.line(poly,fill=(255,170,0),width=5);dr.text(poly[0],'F-N',fill=(255,170,0),stroke_width=2,stroke_fill=(0,0,0));dr.line([px(p) for p in segment],fill=(255,40,40),width=8)
def build():
 d=json.loads(SRC.read_text());policy_minimum=minimum_jamb_support_m(d);partition=json.loads(PART.read_text());matrix=json.loads(MATRIX.read_text());pair=json.loads(PAIR.read_text());pair_by={x['opening_id']:x for x in pair['openings']};impact_by={x['opening_id']:x for x in matrix['openings']};atom_by={x['id']:x for x in d['wall_graph']['atoms']};space_by={x['id']:x for x in d['spaces']};wall=build_target_aware_wall_solids(d);base=_surface_geometry(wall['wall_union']['solid_m']);outer=Polygon(d['outer_boundary']['polygon_m']);parts=[p for p in _polygon_parts(outer.difference(base.intersection(outer))) if p.area>=.05];metric=d['source']['metric_registration']['canonical_px_to_metric_3x3'];inv=_inverse(metric);source_image=Image.open(REF).convert('RGB');rows=[];OUT.mkdir(parents=True,exist_ok=True)
 for oid in IDS:
  resolved=pair_by[oid];impact=impact_by[oid];atom=atom_by[impact['host_atom_id']];segment=impact['segment_m'];cell=next(x for x in partition['cells'] if x['space_id']==resolved['public_cell_id']);public_geom=public_geometry(cell);non_ids=resolved['non_public_anchor_ids']
  if len(non_ids)!=1:raise ValueError(f'{oid} targeted non-public side is not singleton')
  non_id=non_ids[0];matches=[p for p in parts if p.covers(Point(space_by[non_id]['point_m']))]
  if len(matches)!=1:raise ValueError(f'{oid} non-public face is not unique')
  nonpublic=matches[0];labels=sorted(x['id'] for x in d['spaces'] if nonpublic.covers(Point(x['point_m'])))
  if labels!=[non_id]:raise ValueError(f'{oid} non-public face is not single-anchor')
  if public_geom.intersection(nonpublic).area>1e-9:raise ValueError(f'{oid} public/non-public evidence overlaps')
  px_segment=[list(_apply(inv,p)) for p in segment];registration=validate_pixel_metric_segment(metric,px_segment,segment,1.0);host_faces,jamb=host_geometry(atom,segment,policy_minimum);all_metric=[*segment,*atom['centerline_m'],*[q for p in _polygon_parts(public_geom) for q in p.exterior.coords],*nonpublic.exterior.coords];all_px=[_apply(inv,p) for p in all_metric];pad=100;box=(max(0,int(min(p[0] for p in all_px)-pad)),max(0,int(min(p[1] for p in all_px)-pad)),min(source_image.width,int(max(p[0] for p in all_px)+pad)),min(source_image.height,int(max(p[1] for p in all_px)+pad)));crop=source_image.crop(box);draw(crop,box,inv,segment,atom,public_geom,nonpublic,policy_minimum);full=source_image.copy();draw(full,(0,0,source_image.width,source_image.height),inv,segment,atom,public_geom,nonpublic,policy_minimum);cp=OUT/f'{oid}-crop.png';fp=OUT/f'{oid}-full.png';crop.save(cp);full.save(fp)
  rows.append({'opening_id':oid,'host_atom_id':atom['id'],'host_centerline_m':atom['centerline_m'],'host_thickness_m':atom['thickness_m'],'host_face_segments_m':host_faces,'registered_segment_m':segment,'registration':registration,'directed_side_assignment':resolved['directed_side_assignment'],'public_cell':{'space_id':cell['space_id'],'polygon_hash':cell['polygon_hash'],'polygons':cell['polygons']},'non_public_face':serialized_face(nonpublic,non_id),'jamb_support':jamb,'jamb_classification':'sufficient' if jamb['sufficient'] else 'insufficient','sensitivity_stable':resolved['sensitivity_stable'],'artifact_bindings':{'full':{'path':str(fp.resolve()),'bytes':fp.stat().st_size,'sha256':hs(fp)},'crop':{'path':str(cp.resolve()),'bytes':cp.stat().st_size,'sha256':hs(cp)}},'cut_confirmation':False,'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False})
 result={'schema':'partition-targeted-adjudication-evidence-v2','source_structure_hash':d['structure_hash'],'source_document_sha256':hs(SRC),'target_aware_wall_candidate_hash':wall['candidate_hash'],'cut_matrix_candidate_hash':matrix['candidate_hash'],'partition_file_sha256':hs(PART),'partition_candidate_hash':partition['candidate_hash'],'pair_candidate_hash':pair['candidate_hash'],'opening_ids':list(IDS),'openings':rows,'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=h({k:v for k,v in result.items() if k!='candidate_hash'});return result
def main():
 result=build();OUT.mkdir(parents=True,exist_ok=True);(OUT/'partition-targeted-adjudication.json').write_text(json.dumps(result,indent=2)+'\n');sufficient=[x['opening_id'] for x in result['openings'] if x['jamb_support']['sufficient']];insufficient=[x['opening_id'] for x in result['openings'] if not x['jamb_support']['sufficient']];(OUT/'REPORT.md').write_text(f"# Partition-targeted adjudication evidence v2\n\nS-P is the research semantic public cell; F-N is the distinct physical non-public pre-cut face. Governing jamb minimum comes from `opening_contract.minimum_jamb_support_m`. Sufficient: {', '.join(sufficient) or 'none'}. Insufficient: {', '.join(insufficient) or 'none'}. Candidate-only; no confirmation or promotion.\n");print(result['candidate_hash'])
if __name__=='__main__':main()
__all__=['build']
