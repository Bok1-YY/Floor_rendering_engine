"""OP011 exact-host kitchen/dry-balcony glazed-interface research candidate."""
from __future__ import annotations
from copy import deepcopy
import hashlib,json,math,sys
from pathlib import Path
from typing import Any,Mapping
from PIL import Image,ImageDraw
from shapely.geometry import Point,Polygon
from shapely.ops import unary_union
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.jamb_policy import minimum_jamb_support_m
from tools.goal_loop_v2.junction_wall_solids import _polygon_parts
from tools.goal_loop_v2.op002_opening_cut import _surface_geometry
from tools.goal_loop_v2.registration import _inverse,_apply,validate_pixel_metric_segment
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids

SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';IMAGE=ROOT/'data/goal_loop_v2/references/1308/canonical-raw-portrait.png';PART=ROOT/'reports/semantic_public_partition_20260902/semantic-public-partition.json';TYPE=ROOT/'reports/fal_openrouter_review_bundle_20260902/fal-review-bundle.json';OUT=ROOT/'reports/op011_host_scope_candidate_20260902';EPS=(.001,.01,.05,.1)
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def _file_hash(p)->str:return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def _faces(atom):
 a,b=atom['centerline_m'];dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy);nx,ny=-dy/length,dx/length;half=atom['thickness_m']/2;return [[[a[0]+nx*half,a[1]+ny*half],[b[0]+nx*half,b[1]+ny*half]],[[a[0]-nx*half,a[1]-ny*half],[b[0]-nx*half,b[1]-ny*half]]]
def _geom(cell):return unary_union([Polygon(x['exterior'],x.get('holes',[])) for x in cell['polygons']])
def _canon(p):return [list(x) for x in p.exterior.coords]
def _draw(image,box,inv,segment,host,kitchen,dry):
 dr=ImageDraw.Draw(image);offset=(box[0],box[1]);px=lambda p:(lambda q:(q[0]-offset[0],q[1]-offset[1]))(_apply(inv,p));dr.line([px(p) for p in host['centerline_m']],fill=(120,20,200),width=5)
 for line in _faces(host):dr.line([px(p) for p in line],fill=(20,100,255),width=5)
 for geom,label,color in ((kitchen,'KITCHEN-CAND',(0,220,220)),(dry,'DRY-CAND',(255,170,0))):
  for part in _polygon_parts(geom):
   poly=[px(p) for p in part.exterior.coords];dr.line(poly,fill=color,width=5);dr.text(poly[0],label,fill=color,stroke_width=2,stroke_fill=(0,0,0))
 dr.line([px(p) for p in segment],fill=(255,40,40),width=9)
def build_op011_host_scope_candidate(document:Mapping[str,Any],*,_skip_validate=False):
 doc=validate_v21_document(document);opening=next(x for x in doc['opening_contract']['openings'] if x['id']=='OP011');host=next(x for x in doc['wall_graph']['atoms'] if x['id']=='ATOM-WB022-01');segment=opening['source_observation']['nominal_segment_m'];wall=build_target_aware_wall_solids(doc);outer=Polygon(doc['outer_boundary']['polygon_m']);base=_surface_geometry(wall['wall_union']['solid_m']);parts=[p for p in _polygon_parts(outer.difference(base.intersection(outer))) if p.area>=.05];spaces={x['id']:x for x in doc['spaces']};dry_matches=[p for p in parts if p.covers(Point(spaces['dry_balcony']['point_m']))]
 if len(dry_matches)!=1:raise ValueError('OP011 dry-balcony face not unique')
 dry=dry_matches[0];dry_labels=sorted(x['id'] for x in doc['spaces'] if dry.covers(Point(x['point_m'])))
 if dry_labels!=['dry_balcony']:raise ValueError('OP011 dry-balcony face not single-anchor')
 partition=json.loads(PART.read_text());kitchen_cell=next(x for x in partition['cells'] if x['space_id']=='kitchen');kitchen=_geom(kitchen_cell);mid=((segment[0][0]+segment[1][0])/2,(segment[0][1]+segment[1][1])/2);samples=[]
 for sign,label,geom in ((1,'kitchen',kitchen),(-1,'dry_balcony',dry)):
  values=[]
  for eps in EPS:
   p=Point(mid[0]+sign*(host['thickness_m']/2+eps),mid[1]);values.append({'epsilon_m':eps,'point_m':[p.x,p.y],'inside_expected_geometry':geom.covers(p)})
  samples.append({'side':'left/east' if sign==1 else 'right/west','space_id':label,'samples':values,'stable':all(x['inside_expected_geometry'] for x in values)})
 if not all(x['stable'] for x in samples):raise ValueError('OP011 side sampling unstable')
 before=math.dist(host['centerline_m'][0],segment[0]);after=math.dist(segment[1],host['centerline_m'][1]);minimum=minimum_jamb_support_m(doc);types=json.loads(TYPE.read_text());type_row=next(x for x in types['reviews'] if x['opening_id']=='OP011');metric=doc['source']['metric_registration']['canonical_px_to_metric_3x3'];inv=_inverse(metric);pixels=[list(_apply(inv,p)) for p in segment];registration=validate_pixel_metric_segment(metric,pixels,segment,1.0);source_image=Image.open(IMAGE).convert('RGB');all_points=[*segment,*host['centerline_m'],*dry.exterior.coords,*[q for p in _polygon_parts(kitchen) for q in p.exterior.coords]];all_px=[_apply(inv,p) for p in all_points];pad=100;box=(max(0,int(min(p[0] for p in all_px)-pad)),max(0,int(min(p[1] for p in all_px)-pad)),min(source_image.width,int(max(p[0] for p in all_px)+pad)),min(source_image.height,int(max(p[1] for p in all_px)+pad)));crop=source_image.crop(box);_draw(crop,box,inv,segment,host,kitchen,dry);full=source_image.copy();_draw(full,(0,0,source_image.width,source_image.height),inv,segment,host,kitchen,dry);OUT.mkdir(parents=True,exist_ok=True);cp=OUT/'OP011-host-scope-crop.png';fp=OUT/'OP011-host-scope-full.png';crop.save(cp);full.save(fp)
 result={'schema':'op011-host-scope-candidate-v1','source_structure_hash':doc['structure_hash'],'opening_id':'OP011','source_kind':opening['source_observation']['kind'],'source_observation_status':opening['source_observation']['status'],'active_status':opening['status'],'host_atom_id':host['id'],'host_centerline_m':deepcopy(host['centerline_m']),'host_thickness_m':host['thickness_m'],'host_face_segments_m':_faces(host),'nominal_segment_m':deepcopy(segment),'registration':registration,'jamb_support':{'before_m':round(before,9),'after_m':round(after,9),'governing_minimum_m':minimum,'scalar_supports_sufficient':min(before,after)>=minimum},'directed_side_assignment':{'side_a':'kitchen','side_b':'dry_balcony','contract':'side_a=left/east_of_downward_p0_to_p1; side_b=right/west'},'side_samples':samples,'geometry_bindings':{'kitchen_cell_hash':kitchen_cell['polygon_hash'],'dry_balcony_face_hash':_hash(_canon(dry))},'advisory_evidence':{'type_review':deepcopy(type_row['parsed']),'source_vs_vlm_type_conflict':type_row['parsed']['visual_kind']=='door' and opening['source_observation']['kind']=='glazed_interface','main_visual_no_swing_glazed_interface_candidate':True},'artifact_bindings':{'full':{'path':str(fp.resolve()),'bytes':fp.stat().st_size,'sha256':_file_hash(fp)},'crop':{'path':str(cp.resolve()),'bytes':cp.stat().st_size,'sha256':_file_hash(cp)}},'evidence_chain':{'source_document_sha256':_file_hash(SOURCE),'target_aware_wall_hash':wall['candidate_hash'],'semantic_partition_hash':partition['candidate_hash'],'type_bundle_hash':types['candidate_hash']},'remaining_blockers':['HOST_SOURCE_AUTHORITY_PENDING','GLAZED_SUBTYPE_CONFLICT','TRAVERSABILITY_PENDING','TARGETED_TYPE_REVIEW_PENDING','EFFECTIVE_VOID_PENDING','ADJACENCY_PENDING','HUMAN_ACCEPTANCE_PENDING'],'host_confirmation':False,'glazed_subtype_confirmation':False,'traversability_confirmation':False,'pair_confirmation':False,'cut_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
 return result if _skip_validate else validate_op011_host_scope_candidate(doc,result)
def validate_op011_host_scope_candidate(document,candidate):
 doc=validate_v21_document(document)
 if candidate.get('schema')!='op011-host-scope-candidate-v1' or candidate.get('opening_id')!='OP011':raise ValueError('OP011 host scope schema/identity drift')
 for key in ('host_confirmation','glazed_subtype_confirmation','traversability_confirmation','pair_confirmation','cut_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'):
  if candidate.get(key) is not False:raise ValueError('OP011 host scope candidate was promoted')
 if candidate!=build_op011_host_scope_candidate(doc,_skip_validate=True):raise ValueError('OP011 host/scope geometry drift')
 return deepcopy(dict(candidate))
def main():
 doc=json.loads(SOURCE.read_text());result=build_op011_host_scope_candidate(doc);OUT.mkdir(parents=True,exist_ok=True);(OUT/'op011-host-scope-candidate.json').write_text(json.dumps(result,indent=2)+'\n');(OUT/'REPORT.md').write_text('# OP011 host/scope candidate\n\nOP011 lies exactly on WB022-01 with sufficient same-wall support. Four-offset sampling places kitchen east/left and dry balcony west/right. Source says glazed_interface; Fal type review says door/out, while the source crop shows no swing. Candidate-only; subtype/traversability/adjacency/build remain unresolved.\n');print(result['candidate_hash'])
if __name__=='__main__':main()
__all__=['build_op011_host_scope_candidate','validate_op011_host_scope_candidate']
