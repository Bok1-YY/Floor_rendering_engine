"""Build source-bound, non-promoting 2D correction candidates for OP004/OP009."""
from copy import deepcopy
import json,hashlib,math
from pathlib import Path
import sys
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))
from tools.fastloop_research.contract import canonical_json
ROOT=Path(__file__).resolve().parents[2]; DOC=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'; EVID=ROOT/'reports/targeted_cut_adjudication_20260902/targeted-cut-adjudication.json'; REVIEW=ROOT/'reports/targeted_cut_review_bundle_20260902/targeted-cut-review-bundle.json'; OUT=ROOT/'reports/op004_op009_source_correction_20260902'; IDS=('OP004','OP009')
def hs(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def h(v):return hashlib.sha256(canonical_json(v)).hexdigest()
def directed_assignment(document,segment,pair):
 a,b=segment;dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy);normal=(-dy/length,dx/length);mid=((a[0]+b[0])/2,(a[1]+b[1])/2);spaces={x['id']:x for x in document['spaces']};signed={sid:round((spaces[sid]['point_m'][0]-mid[0])*normal[0]+(spaces[sid]['point_m'][1]-mid[1])*normal[1],9) for sid in pair}
 positive=[sid for sid,value in signed.items() if value>1e-6];negative=[sid for sid,value in signed.items() if value<-1e-6]
 if len(positive)!=1 or len(negative)!=1:raise ValueError('directed side candidates do not lie on opposite sides')
 return {'side_a':positive[0],'side_b':negative[0],'contract':'side_a=left_of_source_p0_to_p1; side_b=right_of_source_p0_to_p1','signed_normal_offsets_m':signed}
def build(document=None):
 d=document or json.loads(DOC.read_text()); ev=json.loads(EVID.read_text()); rv=json.loads(REVIEW.read_text()); by={x['opening_id']:x for x in ev['openings']}; rr={x['opening_id']:x for x in rv['reviews']}; out=[]
 for oid in IDS:
  x=by[oid]; r=rr[oid]; pair=x['merged_group']; out.append({'opening_id':oid,'source_structure_hash':d['structure_hash'],'host_atom_id':x['host_atom_id'],'registered_effective_segment_m':x['segment_m'],'registered_effective_width_m':round(((x['segment_m'][1][0]-x['segment_m'][0][0])**2+(x['segment_m'][1][1]-x['segment_m'][0][1])**2)**.5,9),'directed_side_assignment':directed_assignment(d,x['segment_m'],pair),'jamb_support_m':{'before':r['jamb_support']['jamb_before_m'],'after':r['jamb_support']['jamb_after_m'],'minimum':r['jamb_support']['minimum_jamb_m']},'visual_advisory':{'review_status':'agree','visual_kind':r['parsed']['visual_kind'],'confidence':r['parsed']['confidence'],'geometry_agreement':True,'pair_selection':False},'provenance':{'source_document_sha256':hs(DOC),'targeted_evidence_candidate_hash':rv['targeted_evidence_candidate_hash'],'targeted_evidence_file_sha256':rv['targeted_evidence_file_sha256'],'review_bundle_candidate_hash':rv['candidate_hash'],'review_bundle_file_sha256':hs(REVIEW),'matrix_hash':json.loads((ROOT/'reports/candidate_opening_cut_impact_20260902/candidate-opening-cut-impact.json').read_text())['candidate_hash']},'limitations':{'source_correction_candidate':True,'build_disposition':False,'build_kind':False,'head_m':False,'sill_m':False,'z_geometry':False,'swing':False,'traversability':False,'adjacency':False,'score_effect':False,'build_authorized':False,'semantic_promotion':False},'status':'candidate_only','candidate_hash':'0'*64})
 for x in out:x['candidate_hash']=h({k:v for k,v in x.items() if k!='candidate_hash'})
 result={'schema':'op004-op009-source-correction-candidates-v1','source_structure_hash':d['structure_hash'],'source_document_sha256':hs(DOC),'review_bundle_candidate_hash':rv['candidate_hash'],'opening_ids':list(IDS),'packets':out,'semantic_promotion':False,'adjacency_confirmation':False,'build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=h({k:v for k,v in result.items() if k!='candidate_hash'});return result
def validate(document,candidate):
 expected=build(document)
 if candidate.get('schema')!=expected['schema'] or candidate.get('candidate_hash')!=h({k:v for k,v in candidate.items() if k!='candidate_hash'}):raise ValueError('source correction candidate hash/schema drift')
 if candidate!=expected:raise ValueError('source correction candidate evidence/direction drift')
 return deepcopy(candidate)
def main():
 OUT.mkdir(parents=True,exist_ok=True); r=build();(OUT/'source-correction-candidates.json').write_text(json.dumps(r,indent=2)+'\n');(OUT/'REVIEW_CARD.md').write_text('# OP004 / OP009 source-correction review card\n\nBoth packets are 2D source-correction candidates only. OP004 visual advisory: door; directed side candidate bedroom_02 (left/east) / north_toilet (right/west). OP009 visual advisory: glazed_interface; directed side candidate rear_balcony (left/north) / bedroom_01 (right/south), with the access-door interpretation still conflicting/unresolved. No build disposition, type, height, sill, Z, swing, traversability, adjacency, score, or build authorization is proposed.\n');print(r['candidate_hash'])
if __name__=='__main__':main()
