"""Fresh, quarantined evidence for historical vertical OP005 as OP012 candidate."""
from __future__ import annotations
from copy import deepcopy
import hashlib,json,math,sys
from pathlib import Path
from typing import Any,Mapping
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.jamb_policy import minimum_jamb_support_m
from tools.goal_loop_v2.registration import _inverse,_apply,validate_pixel_metric_segment

SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';IMAGE=ROOT/'data/goal_loop_v2/references/1308/canonical-raw-portrait.png';OUT=ROOT/'reports/op012_recovery_evidence_20260902'
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def _file_hash(p)->str:return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def _host_faces(atom):
 a,b=atom['centerline_m'];dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy);nx,ny=-dy/length,dx/length;half=atom['thickness_m']/2;return [[[a[0]+nx*half,a[1]+ny*half],[b[0]+nx*half,b[1]+ny*half]],[[a[0]-nx*half,a[1]-ny*half],[b[0]-nx*half,b[1]-ny*half]]]
def _draw(image,box,inv,nominal,effective,atom,junction):
 dr=ImageDraw.Draw(image);offset=(box[0],box[1]);px=lambda p:(lambda q:(q[0]-offset[0],q[1]-offset[1]))(_apply(inv,p));dr.line([px(p) for p in atom['centerline_m']],fill=(120,20,200),width=5)
 for line in _host_faces(atom):dr.line([px(p) for p in line],fill=(20,100,255),width=5)
 dr.line([px(p) for p in nominal],fill=(255,40,40),width=9);dr.line([px(p) for p in effective],fill=(255,0,255),width=5);j=px(junction['axis_point_m']);r=9;dr.ellipse((j[0]-r,j[1]-r,j[0]+r,j[1]+r),outline=(0,220,220),width=4);dr.text((j[0]+10,j[1]-15),'J-007',fill=(0,220,220),stroke_width=2,stroke_fill=(0,0,0));dr.text(px(nominal[0]),'OP012?',fill=(255,255,0),stroke_width=2,stroke_fill=(0,0,0))
def build_op012_recovery_evidence(document:Mapping[str,Any],*,_skip_validate=False):
 doc=validate_v21_document(document);op5=next(x for x in doc['opening_contract']['openings'] if x['id']=='OP005');history=next(x for x in op5['superseded_interpretations'] if x['id']=='HISTORY-OP005-REJECTED-HOST');legacy=next(x for x in op5['superseded_interpretations'] if x['id']=='HISTORY-OP005-V2');source_obs=history['former_source_observation'];effective=history['former_effective_void'];nominal=source_obs['nominal_segment_m'];void_segment=effective['segment_m'];host=next(x for x in doc['wall_graph']['atoms'] if x['id']=='ATOM-WB007-01');continuation=next(x for x in doc['wall_graph']['atoms'] if x['id']=='ATOM-WB007-02');junction=next(x for x in doc['wall_graph']['junctions'] if x['id']=='J-007');metric=doc['source']['metric_registration']['canonical_px_to_metric_3x3'];inv=_inverse(metric);pixels=[list(_apply(inv,p)) for p in nominal];registration=validate_pixel_metric_segment(metric,pixels,nominal,1.0);source_image=Image.open(IMAGE).convert('RGB');all_points=[*nominal,*void_segment,*host['centerline_m'],*continuation['centerline_m']];all_px=[_apply(inv,p) for p in all_points];pad=140;box=(max(0,int(min(p[0] for p in all_px)-pad)),max(0,int(min(p[1] for p in all_px)-pad)),min(source_image.width,int(max(p[0] for p in all_px)+pad)),min(source_image.height,int(max(p[1] for p in all_px)+pad)));crop=source_image.crop(box);_draw(crop,box,inv,nominal,void_segment,host,junction);full=source_image.copy();_draw(full,(0,0,source_image.width,source_image.height),inv,nominal,void_segment,host,junction);OUT.mkdir(parents=True,exist_ok=True);cp=OUT/'OP012-crop.png';fp=OUT/'OP012-full.png';crop.save(cp);full.save(fp);minimum=minimum_jamb_support_m(doc);cross=history['former_jamb_after'];same=history['former_jamb_before']
 result={'schema':'op012-quarantined-recovery-evidence-v1','source_structure_hash':doc['structure_hash'],'opening_id':'OP012','active_op005_preserved':{'segment_m':deepcopy(op5['source_observation']['nominal_segment_m']),'status':op5['status'],'mutated':False},'historical_provenance':{'rejected_history_id':history['id'],'rejected_reason_code':history['reason_code'],'rejected_artifact_sha256':history['captured_from_artifact_sha256'],'rejected_payload_sha256':history['captured_payload_sha256'],'legacy_history_id':legacy['id'],'legacy_payload_sha256':legacy['captured_payload_sha256']},'nominal_segment_m':deepcopy(nominal),'effective_segment_m':deepcopy(void_segment),'nominal_width_m':source_obs['nominal_width_m'],'effective_width_m':effective['width_m'],'registration':registration,'host_hypothesis':{'effective_owner_atom_id':host['id'],'nominal_continuation_atom_id':continuation['id'],'junction_id':junction['id'],'junction_kind':junction['kind'],'junction_status':junction['status'],'nominal_crosses_junction_candidate':nominal[1][1]<junction['axis_point_m'][1],'effective_ends_before_junction_m':round(void_segment[1][1]-junction['axis_point_m'][1],9),'host_face_segments_m':_host_faces(host),'confirmation':False},'jamb_hypothesis':{'same_wall_support_m':same['effective_support_m'],'crossing_support_m':cross['effective_support_m'],'supporting_cross_atom_ids':deepcopy(cross['supporting_atom_ids']),'governing_minimum_m':minimum,'scalar_supports_sufficient':min(same['effective_support_m'],cross['effective_support_m'])>=minimum,'crossing_wall_jamb_confirmation':False},'artifact_bindings':{'full':{'path':str(fp.resolve()),'bytes':fp.stat().st_size,'sha256':_file_hash(fp)},'crop':{'path':str(cp.resolve()),'bytes':cp.stat().st_size,'sha256':_file_hash(cp)}},'evidence_chain':{'source_document_sha256':_file_hash(SOURCE),'source_image_sha256':_file_hash(IMAGE)},'remaining_blockers':['FRESH_VISUAL_SWING_REVIEW_PENDING','DISTINCT_OPENING_ID_SOURCE_AUTHORITY_PENDING','CLOSED_WALL_BREAK_PENDING','HOST_ACROSS_JUNCTION_PENDING','ROOM_PAIR_PENDING','TRAVERSABILITY_PENDING','HUMAN_ACCEPTANCE_PENDING'],'recovery_confirmation':False,'host_confirmation':False,'effective_void_confirmation':False,'pair_confirmation':False,'cut_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
 return result if _skip_validate else validate_op012_recovery_evidence(doc,result)
def validate_op012_recovery_evidence(document,candidate):
 doc=validate_v21_document(document)
 if candidate.get('schema')!='op012-quarantined-recovery-evidence-v1' or candidate.get('opening_id')!='OP012':raise ValueError('OP012 recovery schema/identity drift')
 for key in ('recovery_confirmation','host_confirmation','effective_void_confirmation','pair_confirmation','cut_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'):
  if candidate.get(key) is not False:raise ValueError('OP012 recovery was promoted')
 if (candidate.get('active_op005_preserved') or {}).get('mutated') is not False:raise ValueError('active OP005 was mutated')
 if candidate!=build_op012_recovery_evidence(doc,_skip_validate=True):raise ValueError('OP012 recovery history/source drift')
 return deepcopy(dict(candidate))
def main():
 doc=json.loads(SOURCE.read_text());result=build_op012_recovery_evidence(doc);OUT.mkdir(parents=True,exist_ok=True);(OUT/'op012-recovery-evidence.json').write_text(json.dumps(result,indent=2)+'\n');(OUT/'REPORT.md').write_text('# OP012 quarantined recovery evidence\n\nFresh overlays preserve active horizontal OP005 while testing the historical vertical segment as new OP012. Red is historical nominal, magenta is historical effective segment, blue/purple is WB007-01 and cyan marks J-007. Recovery, host, cut, pair, adjacency, score and build remain unconfirmed.\n');print(result['candidate_hash'])
if __name__=='__main__':main()
__all__=['build_op012_recovery_evidence','validate_op012_recovery_evidence']
