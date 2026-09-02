"""OP002 adjudication packet rebased onto target-aware wall topology."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.op002_target_cut_closure import build_op002_target_cut_closure

SCHEMA='op002-adjudication-packet-v3'
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def _group(groups,space):return next(group for group in groups if space in group)
def _binding(path):
    p=Path(path).resolve();raw=p.read_bytes();return {'path':str(p),'file_sha256':hashlib.sha256(raw).hexdigest(),'canonical_sha256':_hash(json.loads(raw.decode('utf-8')))}

def build_op002_adjudication_packet_v3(document:Mapping[str,Any],vertical_evidence_file,gemini_result_file,*,_skip_validate=False):
    doc=validate_v21_document(document);topology=build_op002_target_cut_closure(doc);evidence=json.loads(Path(vertical_evidence_file).read_text(encoding='utf-8'));envelope=json.loads(Path(gemini_result_file).read_text(encoding='utf-8'));parsed=envelope.get('parsed_result')
    required={'opening_id','geometry_agreement','observed_kind','pair_agreement','traversable','complete'}
    if envelope.get('http_status')!=200 or envelope.get('failure') is not None or not isinstance(parsed,dict) or set(parsed)!=required or parsed.get('opening_id')!='OP002' or parsed.get('complete') is not True:raise ValueError('OP002 Gemini result invalid')
    expected_images=sorted([evidence['artifacts']['full_overlay']['sha256'],evidence['artifacts']['crop_overlay']['sha256']])
    if sorted(envelope.get('image_sha256',[]))!=expected_images:raise ValueError('OP002 Gemini image provenance mismatch')
    bedroom=_group(topology['closure_anchor_groups'],'bedroom_01');public=_group(topology['closure_anchor_groups'],'bedroom_corridor');bath=_group(topology['closure_anchor_groups'],'bath')
    all_agree=parsed['geometry_agreement']=='agree' and parsed['pair_agreement']=='agree' and parsed['traversable']=='yes'
    blockers=['SOURCE_OPENING_STATUS_CANDIDATE','OTHER_SIDE_FACE_MULTI_ANCHOR','ROOM_POLYGONS_NOT_SOURCE_CONFIRMED','HUMAN_REVIEW_PENDING']
    if not all_agree:blockers.append('GEMINI_REVIEW_CONFLICT_OR_INDETERMINATE')
    result={'schema':SCHEMA,'source_structure_hash':doc['structure_hash'],'opening_id':'OP002','vertical_evidence_binding':_binding(vertical_evidence_file),'gemini_result_binding':_binding(gemini_result_file),'target_cut_closure_hash':topology['candidate_hash'],'gemini_result':deepcopy(parsed),'gemini_review_status':'complete_agree' if all_agree else 'complete_non_agree','review_pair_candidate':['bedroom_01','bedroom_corridor'],'target_aware_geometry':{'bedroom_group':bedroom,'public_group':public,'bath_group':bath,'bedroom_isolated':bedroom==['bedroom_01'],'corridor_on_other_side':'bedroom_corridor' in public,'bath_isolated':bath==['bath'],'other_side_is_multi_anchor':len(public)>1},'remaining_blockers':blockers,'decision':'unresolved_candidate','status':'pending_human_review','pair_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64}
    result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
    return result if _skip_validate else validate_op002_adjudication_packet_v3(doc,vertical_evidence_file,gemini_result_file,result)

def validate_op002_adjudication_packet_v3(document,vertical_evidence_file,gemini_result_file,candidate):
    doc=validate_v21_document(document)
    if candidate.get('schema')!=SCHEMA or candidate.get('opening_id')!='OP002':raise ValueError('OP002 v3 packet schema/target violation')
    for key in ('pair_confirmation','semantic_promotion','build_authorized','ready'):
        if candidate.get(key) is not False:raise ValueError('OP002 v3 packet was promoted')
    if dict(candidate)!=build_op002_adjudication_packet_v3(doc,vertical_evidence_file,gemini_result_file,_skip_validate=True):raise ValueError('OP002 v3 adjudication evidence drift')
    return deepcopy(dict(candidate))

__all__=['build_op002_adjudication_packet_v3','validate_op002_adjudication_packet_v3']
