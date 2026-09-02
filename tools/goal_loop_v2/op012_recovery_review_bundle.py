"""Bind conflicting main/independent and fal OP012 recovery reviews."""
from __future__ import annotations
from copy import deepcopy
import hashlib,json
from pathlib import Path
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.fal_op012_recovery_review import parse

ROOT=Path(__file__).resolve().parents[2];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';EVIDENCE=ROOT/'reports/op012_recovery_evidence_20260902/op012-recovery-evidence.json';RESULT=Path(r'C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_op012_recovery_20260902/result.json')
def _hash(v):return hashlib.sha256(canonical_json(v)).hexdigest()
def _file_hash(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def build_op012_recovery_review_bundle(document,result_file=RESULT,*,_skip_validate=False):
 doc=validate_v21_document(document);evidence=json.loads(EVIDENCE.read_text());raw=Path(result_file).read_bytes();result=json.loads(raw.decode());parsed=parse(json.dumps(result['parsed'],separators=(',',':')));expected=[{'filename':Path(evidence['artifact_bindings'][role]['path']).name,'bytes':evidence['artifact_bindings'][role]['bytes'],'sha256':evidence['artifact_bindings'][role]['sha256']} for role in ('full','crop')]
 if result.get('evidence_file_sha256')!=_file_hash(EVIDENCE) or result.get('evidence_candidate_hash')!=evidence['candidate_hash'] or result.get('image_bindings')!=expected or result.get('usable_advisory') is not True:raise ValueError('OP012 fal review binding/incomplete')
 fal_supports_recovery=all(parsed[k]=='yes' for k in ('nominal_on_visible_door','effective_matches_wall_break','swing_aligns_segment','distinct_from_neighbor_openings')) and parsed['visual_kind']=='door';bundle={'schema':'op012-recovery-review-conflict-v1','source_structure_hash':doc['structure_hash'],'opening_id':'OP012','evidence_candidate_hash':evidence['candidate_hash'],'evidence_file_sha256':_file_hash(EVIDENCE),'fal_result_file_sha256':hashlib.sha256(raw).hexdigest(),'fal_result_canonical_sha256':_hash(result),'fal_parsed':parsed,'fal_supports_recovery':fal_supports_recovery,'fal_cost_usd':float(result['usage']['cost']),'main_visual_disposition':'reject_recovery_continuous_wall_between_neighbor_doors','independent_geometry_disposition':'reject_recovery_quarantine','review_conflict':True,'decision':'quarantined_review_conflict','human_adjudication_required':True,'recovery_confirmation':False,'host_confirmation':False,'effective_void_confirmation':False,'pair_confirmation':False,'cut_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};bundle['candidate_hash']=_hash({k:v for k,v in bundle.items() if k!='candidate_hash'})
 return bundle if _skip_validate else validate_op012_recovery_review_bundle(doc,bundle,result_file)
def validate_op012_recovery_review_bundle(document,candidate,result_file=RESULT):
 doc=validate_v21_document(document)
 if candidate.get('schema')!='op012-recovery-review-conflict-v1' or candidate.get('opening_id')!='OP012' or candidate.get('review_conflict') is not True:raise ValueError('OP012 review conflict schema/state drift')
 for key in ('recovery_confirmation','host_confirmation','effective_void_confirmation','pair_confirmation','cut_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'):
  if candidate.get(key) is not False:raise ValueError('OP012 conflict bundle was promoted')
 if candidate!=build_op012_recovery_review_bundle(doc,result_file,_skip_validate=True):raise ValueError('OP012 review conflict evidence drift')
 return deepcopy(dict(candidate))
__all__=['build_op012_recovery_review_bundle','validate_op012_recovery_review_bundle']
