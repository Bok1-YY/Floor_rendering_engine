"""OP008 bath-to-lobby return-wall and face-abutment research candidate."""
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
from tools.goal_loop_v2.face_abutment_targets import build_face_abutment_targets
from tools.goal_loop_v2.jamb_policy import minimum_jamb_support_m
from tools.goal_loop_v2.junction_wall_solids import _polygon_parts
from tools.goal_loop_v2.op002_opening_cut import _surface_geometry
from tools.goal_loop_v2.registration import _inverse,_apply,validate_pixel_metric_segment
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids

SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';IMAGE=ROOT/'data/goal_loop_v2/references/1308/canonical-raw-portrait.png';PART=ROOT/'reports/semantic_public_partition_20260902/semantic-public-partition.json';TYPE=ROOT/'reports/fal_openrouter_review_bundle_20260902/fal-review-bundle.json';PAIR=ROOT/'reports/fal_room_pair_trial_20260902/fal-room-pair-bundle.json';OUT=ROOT/'reports/op008_return_wall_candidate_20260902';EPS=(.001,.01,.05,.1)
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def _file_hash(p)->str:return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def _faces(atom):
 a,b=atom['centerline_m'];dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy);nx,ny=-dy/length,dx/length;half=atom['thickness_m']/2;return [[[a[0]+nx*half,a[1]+ny*half],[b[0]+nx*half,b[1]+ny*half]],[[a[0]-nx*half,a[1]-ny*half],[b[0]-nx*half,b[1]-ny*half]]]
def _geom(cell):return unary_union([Polygon(x['exterior'],x.get('holes',[])) for x in cell['polygons']])
def _canon(p):return [list(x) for x in p.exterior.coords]
def _draw(image,box,inv,segment,host,return_face,bath,lobby):
 dr=ImageDraw.Draw(image);offset=(box[0],box[1]);px=lambda p:(lambda q:(q[0]-offset[0],q[1]-offset[1]))(_apply(inv,p));dr.line([px(p) for p in host['centerline_m']],fill=(120,20,200),width=5)
 for line in _faces(host):dr.line([px(p) for p in line],fill=(20,100,255),width=5)
 dr.line([px(p) for p in return_face],fill=(0,220,80),width=7)
 for geom,label,color in ((bath,'F-BATH',(255,170,0)),(lobby,'S-LOBBY',(0,220,220))):
  for part in _polygon_parts(geom):
   poly=[px(p) for p in part.exterior.coords];dr.line(poly,fill=color,width=5);dr.text(poly[0],label,fill=color,stroke_width=2,stroke_fill=(0,0,0))
 dr.line([px(p) for p in segment],fill=(255,40,40),width=9)
def build_op008_return_wall_candidate(document:Mapping[str,Any],*,_skip_validate=False):
 doc=validate_v21_document(document);opening=next(x for x in doc['opening_contract']['openings'] if x['id']=='OP008');host=next(x for x in doc['wall_graph']['atoms'] if x['id']=='ATOM-WB018-01');return_atom=next(x for x in doc['wall_graph']['atoms'] if x['id']=='ATOM-WB017-02');segment=opening['source_observation']['nominal_segment_m'];wall=build_target_aware_wall_solids(doc);abut=build_face_abutment_targets(doc);target=next(x for x in abut['records'] if x['atom_id']==host['id'] and x['endpoint_index']==1);return_face=min(_faces(return_atom),key=lambda line:min(math.dist(host['centerline_m'][1],p) for p in line));contact=[return_face[0][0],segment[1][1]];face_distance=math.dist(segment[1],contact);before=math.dist(host['centerline_m'][0],segment[0]);minimum=minimum_jamb_support_m(doc);outer=Polygon(doc['outer_boundary']['polygon_m']);base=_surface_geometry(wall['wall_union']['solid_m']);parts=[p for p in _polygon_parts(outer.difference(base.intersection(outer))) if p.area>=.05];space_by={x['id']:x for x in doc['spaces']};bath_matches=[p for p in parts if p.covers(Point(space_by['bath']['point_m']))]
 if len(bath_matches)!=1:raise ValueError('OP008 bath face not unique')
 bath=bath_matches[0];bath_labels=sorted(x['id'] for x in doc['spaces'] if bath.covers(Point(x['point_m'])))
 if bath_labels!=['bath']:raise ValueError('OP008 bath face not single-anchor')
 partition=json.loads(PART.read_text());lobby_cell=next(x for x in partition['cells'] if x['space_id']=='lobby');lobby=_geom(lobby_cell);mid=((segment[0][0]+segment[1][0])/2,(segment[0][1]+segment[1][1])/2);samples=[]
 for sign,label,geom in ((1,'bath',bath),(-1,'lobby',lobby)):
  values=[]
  for eps in EPS:
   p=Point(mid[0],mid[1]+sign*(host['thickness_m']/2+eps));values.append({'epsilon_m':eps,'point_m':[p.x,p.y],'inside_expected_geometry':geom.covers(p)})
  samples.append({'side':'left/north' if sign==1 else 'right/south','space_id':label,'samples':values,'stable':all(x['inside_expected_geometry'] for x in values)})
 if not all(x['stable'] for x in samples):raise ValueError('OP008 side sampling unstable')
 types=json.loads(TYPE.read_text());type_row=next(x for x in types['reviews'] if x['opening_id']=='OP008');pairs=json.loads(PAIR.read_text());pair_row=next(x for x in pairs['reviews'] if x['opening_id']=='OP008');metric=doc['source']['metric_registration']['canonical_px_to_metric_3x3'];inv=_inverse(metric);pixels=[list(_apply(inv,p)) for p in segment];registration=validate_pixel_metric_segment(metric,pixels,segment,1.0);source_image=Image.open(IMAGE).convert('RGB');local_return=[[contact[0],min(return_face[0][1],return_face[1][1],contact[1]-.5)],[contact[0],max(return_face[0][1],return_face[1][1],contact[1]+.5)]] if return_face[0][1]<return_face[1][1] else [[contact[0],contact[1]+.5],[contact[0],contact[1]-.5]];all_points=[*segment,*host['centerline_m'],*local_return,*bath.exterior.coords,*[q for p in _polygon_parts(lobby) for q in p.exterior.coords]];all_px=[_apply(inv,p) for p in all_points];pad=100;box=(max(0,int(min(p[0] for p in all_px)-pad)),max(0,int(min(p[1] for p in all_px)-pad)),min(source_image.width,int(max(p[0] for p in all_px)+pad)),min(source_image.height,int(max(p[1] for p in all_px)+pad)));crop=source_image.crop(box);_draw(crop,box,inv,segment,host,local_return,bath,lobby);full=source_image.copy();_draw(full,(0,0,source_image.width,source_image.height),inv,segment,host,local_return,bath,lobby);OUT.mkdir(parents=True,exist_ok=True);cp=OUT/'OP008-return-crop.png';fp=OUT/'OP008-return-full.png';crop.save(cp);full.save(fp)
 result={'schema':'op008-return-wall-candidate-v1','source_structure_hash':doc['structure_hash'],'opening_id':'OP008','registration':registration,'nominal_segment_m':deepcopy(segment),'host_atom_id':host['id'],'return_atom_id':return_atom['id'],'return_face_m':return_face,'return_contact_point_m':contact,'face_distance_m':round(face_distance,9),'face_abutment_record':deepcopy(target),'jamb_support':{'before':{'mode':'same_wall_solid','effective_support_m':round(before,9),'confirmation':False},'after':{'mode':'return_wall_face_candidate','same_wall_margin_m':round(face_distance,9),'return_effective_support_m':return_atom['thickness_m'],'confirmation':False},'governing_minimum_m':minimum,'scalar_supports_sufficient':min(before,return_atom['thickness_m'])>=minimum},'directed_side_assignment':{'side_a':'bath','side_b':'lobby','contract':'side_a=left/north_of_p0_to_p1; side_b=right/south'},'side_samples':samples,'geometry_bindings':{'bath_face_hash':_hash(_canon(bath)),'lobby_cell_hash':lobby_cell['polygon_hash']},'advisory_evidence':{'type_review':deepcopy(type_row['parsed']),'room_pair_review':deepcopy(pair_row['parsed']),'room_pair_conflicts_with_geometry':pair_row['advisory_pair_candidate']!=['bath','lobby'],'main_visual_bath_to_lobby_door_candidate':True},'artifact_bindings':{'full':{'path':str(fp.resolve()),'bytes':fp.stat().st_size,'sha256':_file_hash(fp)},'crop':{'path':str(cp.resolve()),'bytes':cp.stat().st_size,'sha256':_file_hash(cp)}},'evidence_chain':{'source_document_sha256':_file_hash(SOURCE),'target_aware_wall_hash':wall['candidate_hash'],'face_abutment_hash':abut['candidate_hash'],'semantic_partition_hash':partition['candidate_hash'],'type_bundle_hash':types['candidate_hash'],'room_pair_bundle_hash':pairs['candidate_hash']},'remaining_blockers':['RETURN_FACE_SOURCE_AUTHORITY_PENDING','ROOM_PAIR_VLM_CONFLICT','TARGETED_RETURN_REVIEW_PENDING','EFFECTIVE_VOID_CONFIRMATION_PENDING','VERTICAL_POLICY_PENDING','TRAVERSABILITY_PENDING','ADJACENCY_PENDING','HUMAN_ACCEPTANCE_PENDING'],'return_face_confirmation':False,'pair_confirmation':False,'cut_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
 return result if _skip_validate else validate_op008_return_wall_candidate(doc,result)
def validate_op008_return_wall_candidate(document,candidate):
 doc=validate_v21_document(document)
 if candidate.get('schema')!='op008-return-wall-candidate-v1' or candidate.get('opening_id')!='OP008':raise ValueError('OP008 return schema/identity drift')
 for key in ('return_face_confirmation','pair_confirmation','cut_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'):
  if candidate.get(key) is not False:raise ValueError('OP008 return candidate was promoted')
 if candidate!=build_op008_return_wall_candidate(doc,_skip_validate=True):raise ValueError('OP008 return/source geometry drift')
 return deepcopy(dict(candidate))
def main():
 doc=json.loads(SOURCE.read_text());result=build_op008_return_wall_candidate(doc);OUT.mkdir(parents=True,exist_ok=True);(OUT/'op008-return-wall-candidate.json').write_text(json.dumps(result,indent=2)+'\n');(OUT/'REPORT.md').write_text('# OP008 return-wall candidate\n\nSource/face sampling places bath north of OP008 and lobby south. The 35.14 mm same-wall margin reaches a face-abutted WB017-02 return wall that supplies 120 mm candidate support, above the source 50 mm minimum. Candidate-only; return face, pair, traversal, adjacency and build remain unconfirmed.\n');print(result['candidate_hash'])
if __name__=='__main__':main()
__all__=['build_op008_return_wall_candidate','validate_op008_return_wall_candidate']
