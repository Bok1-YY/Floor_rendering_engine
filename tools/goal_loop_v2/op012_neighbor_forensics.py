"""Pixel-register OP012 against neighboring real OP005/OP006 openings."""
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
from tools.goal_loop_v2.registration import _inverse,_apply,validate_pixel_metric_segment
SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';IMAGE=ROOT/'data/goal_loop_v2/references/1308/canonical-raw-portrait.png';OUT=ROOT/'reports/op012_neighbor_forensics_20260902'
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def _file_hash(p)->str:return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def build_op012_neighbor_forensics(document:Mapping[str,Any],*,_skip_validate=False):
 doc=validate_v21_document(document);openings={x['id']:x for x in doc['opening_contract']['openings']};op5=openings['OP005']['source_observation']['nominal_segment_m'];op6=openings['OP006']['source_observation']['nominal_segment_m'];history=next(x for x in openings['OP005']['superseded_interpretations'] if x['id']=='HISTORY-OP005-REJECTED-HOST');op12=history['former_source_observation']['nominal_segment_m'];matrix=doc['source']['metric_registration']['canonical_px_to_metric_3x3'];inv=_inverse(matrix)
 def register(segment):
  px=[list(_apply(inv,p)) for p in segment];return {'segment_m':deepcopy(segment),'segment_px':px,'registration':validate_pixel_metric_segment(matrix,px,segment,1.0)}
 r5,r6,r12=register(op5),register(op6),register(op12);hinge5=r5['segment_px'][1];p0_6=r6['segment_px'][0];p1_12=r12['segment_px'][1];relationships={'op012_lower_to_op006_start_px':math.dist(p1_12,p0_6),'op012_lower_to_op005_hinge_px':math.dist(p1_12,hinge5),'op012_x_minus_op005_hinge_x_px':p1_12[0]-hinge5[0],'op006_same_x_as_op012_px':abs(p0_6[0]-p1_12[0])}
 im=Image.open(IMAGE).convert('RGB');box=(1050,620,1450,930);crop=im.crop(box);draw=ImageDraw.Draw(crop)
 for reg,label,color,width in ((r5,'OP005',(255,180,0),5),(r12,'OP012?',(255,0,0),5),(r6,'OP006',(0,180,255),5)):
  points=[(p[0]-box[0],p[1]-box[1]) for p in reg['segment_px']];draw.line(points,fill=color,width=width);draw.text((points[0][0]+5,points[0][1]-15),label,fill=color,stroke_width=1,stroke_fill=(255,255,255))
 OUT.mkdir(parents=True,exist_ok=True);ip=OUT/'op012-neighbor-tight.png';crop.save(ip);result={'schema':'op012-neighbor-registration-forensics-v1','source_structure_hash':doc['structure_hash'],'segments':{'active_op005':r5,'historical_op012':r12,'active_op006':r6},'relationships':relationships,'historical_rejection_reason':history['reason_code'],'historical_payload_sha256':history['captured_payload_sha256'],'pixel_interpretation_candidate':{'op005_swing_hinge_matches_active_op005_endpoint':True,'op012_lower_is_adjacent_to_op006_start':True,'op012_distinct_swing_not_established':True,'fal_neighbor_swing_misattribution_possible':True},'artifact_binding':{'path':str(ip.resolve()),'bytes':ip.stat().st_size,'sha256':_file_hash(ip)},'evidence_chain':{'source_document_sha256':_file_hash(SOURCE),'source_image_sha256':_file_hash(IMAGE)},'recovery_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
 return result if _skip_validate else validate_op012_neighbor_forensics(doc,result)
def validate_op012_neighbor_forensics(document,candidate):
 doc=validate_v21_document(document)
 if candidate.get('schema')!='op012-neighbor-registration-forensics-v1':raise ValueError('OP012 neighbor schema drift')
 for key in ('recovery_confirmation','semantic_promotion','build_authorized','ready'):
  if candidate.get(key) is not False:raise ValueError('OP012 neighbor forensics was promoted')
 if candidate!=build_op012_neighbor_forensics(doc,_skip_validate=True):raise ValueError('OP012 neighbor/source registration drift')
 return deepcopy(dict(candidate))
def main():
 doc=json.loads(SOURCE.read_text());result=build_op012_neighbor_forensics(doc);OUT.mkdir(parents=True,exist_ok=True);(OUT/'op012-neighbor-forensics.json').write_text(json.dumps(result,indent=2)+'\n');(OUT/'REPORT.md').write_text(f"# OP012 neighbor registration forensics\n\nOP012 lower endpoint is {result['relationships']['op012_lower_to_op006_start_px']:.3f} px above OP006 start, while it is {result['relationships']['op012_lower_to_op005_hinge_px']:.3f} px from the active horizontal OP005 hinge. The tight crop separates active OP005/OP006 from the historical vertical hypothesis. Recovery remains unconfirmed.\n");print(result['candidate_hash'])
if __name__=='__main__':main()
__all__=['build_op012_neighbor_forensics','validate_op012_neighbor_forensics']
