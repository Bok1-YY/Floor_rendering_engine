"""Consolidate independently verified non-applying 2D correction candidates."""
from __future__ import annotations
from copy import deepcopy
import hashlib,json
from pathlib import Path
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.build_op002_source_correction_candidate import build as build_op002,validate as validate_op002
from tools.goal_loop_v2.build_op004_op009_source_correction import build as build_op004_009,validate as validate_op004_009

ROOT=Path(__file__).resolve().parents[2];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';OP2=ROOT/'reports/op002_source_correction_candidate_20260902/op002-source-correction-candidate.json';OP49=ROOT/'reports/op004_op009_source_correction_20260902/source-correction-candidates.json'
def _hash(v):return hashlib.sha256(canonical_json(v)).hexdigest()
def _file_hash(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def build_registry(document,*,_skip_validate=False):
 doc=validate_v21_document(document);op2=json.loads(OP2.read_text());op49=json.loads(OP49.read_text());validate_op002(doc,op2);validate_op004_009(doc,op49);rows=[]
 p=op2['packet'];rows.append({'opening_id':'OP002','source_candidate_file_sha256':_file_hash(OP2),'source_candidate_hash':op2['candidate_hash'],'packet_hash':p['candidate_hash'],'host_atom_id':p['host']['atom_id'],'segment_m':p['registered_effective_segment_m'],'width_m':p['registered_effective_width_m'],'directed_side_assignment':deepcopy(p['directed_side_assignment']),'minimum_jamb_m':p['jamb_support_m']['minimum_jamb_m'],'visual_advisory':'door','remaining_blockers':['SOURCE_APPLICATION_REVIEW','VERTICAL_POLICY_PENDING','TRAVERSABILITY_PENDING','ADJACENCY_PENDING','HUMAN_ACCEPTANCE_PENDING'],'application_authorized':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False})
 for p in op49['packets']:
  rows.append({'opening_id':p['opening_id'],'source_candidate_file_sha256':_file_hash(OP49),'source_candidate_hash':op49['candidate_hash'],'packet_hash':p['candidate_hash'],'host_atom_id':p['host_atom_id'],'segment_m':p['registered_effective_segment_m'],'width_m':p['registered_effective_width_m'],'directed_side_assignment':deepcopy(p['directed_side_assignment']),'minimum_jamb_m':p['jamb_support_m']['minimum'],'visual_advisory':p['visual_advisory']['visual_kind'],'remaining_blockers':['SOURCE_APPLICATION_REVIEW','VERTICAL_POLICY_PENDING','TRAVERSABILITY_PENDING','ADJACENCY_PENDING','HUMAN_ACCEPTANCE_PENDING']+(['TYPE_CONFLICT_PENDING'] if p['opening_id']=='OP009' else []),'application_authorized':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False})
 result={'schema':'non-applying-2d-correction-candidate-registry-v1','source_structure_hash':doc['structure_hash'],'source_document_sha256':_file_hash(SOURCE),'opening_ids':[r['opening_id'] for r in rows],'candidates':rows,'human_review_pending':True,'application_authorized':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
 return result if _skip_validate else validate_registry(doc,result)
def validate_registry(document,candidate):
 doc=validate_v21_document(document)
 if candidate.get('schema')!='non-applying-2d-correction-candidate-registry-v1' or candidate.get('opening_ids')!=['OP002','OP004','OP009']:raise ValueError('correction registry source/coverage drift')
 for key in ('application_authorized','semantic_promotion','build_authorized','ready'):
  if candidate.get(key) is not False:raise ValueError('correction registry was promoted')
 if candidate!=build_registry(doc,_skip_validate=True):raise ValueError('correction registry evidence/direction drift')
 return deepcopy(dict(candidate))
__all__=['build_registry','validate_registry']
