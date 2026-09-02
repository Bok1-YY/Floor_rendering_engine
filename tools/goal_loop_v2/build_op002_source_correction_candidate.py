"""Fail-closed, non-applying OP002 2D source-correction wrapper."""
import json, hashlib
from copy import deepcopy
from pathlib import Path
from tools.fastloop_research.contract import canonical_json
ROOT=Path(__file__).resolve().parents[2]; SRC=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'; EVID=ROOT/'reports/partition_targeted_adjudication_20260902/partition-targeted-adjudication.json'; REVIEW=ROOT/'reports/partition_targeted_review_bundle_20260902/partition-targeted-review-bundle.json'; OUT=ROOT/'reports/op002_source_correction_candidate_20260902'
def _sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def _hash(v): return hashlib.sha256(canonical_json(v)).hexdigest()
def build(document=None, *, _skip_validate=False):
 d=document or json.loads(SRC.read_text()); e=json.loads(EVID.read_text()); r=json.loads(REVIEW.read_text()); x=next(v for v in e['openings'] if v['opening_id']=='OP002'); rv=next(v for v in r['reviews'] if v['opening_id']=='OP002'); o=next(v for v in d['opening_contract']['openings'] if v['id']=='OP002')
 p={'opening_id':'OP002','host':{'atom_id':x['host_atom_id'],'thickness_m':x['host_thickness_m']},'registered_effective_segment_m':x['registered_segment_m'],'registered_effective_width_m':o['effective_void']['width_m'],'directed_side_assignment':x['directed_side_assignment'],'jamb_support_m':x['jamb_support'],'advisory':{'visual_door_agreement':True,'direct_gemini_agreement':True,'confidence':'high'},'evidence_chain':{'source_document_sha256':_sha(SRC),'targeted_evidence_candidate_hash':e['candidate_hash'],'targeted_evidence_file_sha256':_sha(EVID),'review_bundle_candidate_hash':r['candidate_hash'],'review_bundle_file_sha256':_sha(REVIEW),'review_result_file_sha256':rv['result_file_sha256'],'review_result_canonical_sha256':rv['result_canonical_sha256']},'forbidden_fields':{'build_disposition':True,'build_kind':True,'head_m':True,'sill_m':True,'z_geometry':True,'swing':True,'traversability':True,'adjacency':True,'score_effect':True,'build_authorized':True,'application_authorized':True,'pair_confirmation':True,'semantic_promotion':True},'status':'candidate_only','candidate_hash':'0'*64}; p['candidate_hash']=_hash({k:v for k,v in p.items() if k!='candidate_hash'})
 out={'schema':'op002-source-correction-candidate-v1','source_structure_hash':d['structure_hash'],'opening_id':'OP002','packet':p,'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64}; out['candidate_hash']=_hash({k:v for k,v in out.items() if k!='candidate_hash'}); return out
def validate(document,candidate):
 if candidate.get('schema')!='op002-source-correction-candidate-v1' or candidate.get('opening_id')!='OP002': raise ValueError('OP002 schema/allowlist violation')
 if any(candidate.get(k) is not False for k in ('pair_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready')): raise ValueError('candidate was promoted')
 expected=build(document); 
 if dict(candidate)!=expected: raise ValueError('OP002 source-correction evidence drift')
 return deepcopy(candidate)
def main():
 OUT.mkdir(parents=True,exist_ok=True); r=build(); (OUT/'op002-source-correction-candidate.json').write_text(json.dumps(r,indent=2)+'\n'); (OUT/'REVIEW_CARD.md').write_text('# OP002 2D source-correction candidate\n\nThis non-applying wrapper proposes only the exact 2D host atom, registered effective segment/width, signed-normal directed sides, and measured jamb supports. Visual door agreement and prior direct Gemini agreement are advisory. Build disposition, type, height, sill, Z, swing, traversability, adjacency, score and build authorization are forbidden and remain unpromoted.\n'); print(r['candidate_hash'])
if __name__=='__main__': main()
