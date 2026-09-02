"""Source-bound hard deny for the rejected WB011/WB006 duplicate portal."""
from __future__ import annotations
from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Any,Mapping,Sequence

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document,v21_mapping_metadata

SCHEMA='source-exclusion-deny-sidecar-v1';PORTAL_ID='PORTAL-WB011-WB006-01';HISTORY_ID='HISTORY-PORTAL-WB011-WB006-01-REJECTED'
FORBIDDEN=['wall_cut','gap_portal','wall_mesh_input','blender_input','ifc_building_element','ifc_void_relation','ifc_fill_relation','ifc_spatial_relation','adjacency_edge','reachability_graph','entrance_root']
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def _file_hash(path)->str:return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _portal(doc):return next((x for x in doc['opening_contract']['openings'] if x['id']==PORTAL_ID),None)
def _history(portal):return next((x for x in portal.get('superseded_interpretations',[]) if x.get('id')==HISTORY_ID),None)
def build_demoted_portal_deny(document:Mapping[str,Any],source_document_file,*,_skip_validate=False):
    doc=validate_v21_document(document);portal=_portal(doc)
    if portal is None:raise ValueError('denied portal audit record is missing')
    history=_history(portal)
    if history is None:raise ValueError('denied portal rejected history is missing')
    required={'build_disposition':'evidence_only','build_kind':None,'effective_void':None,'host':None,'jamb_before':None,'jamb_after':None,'side_a_space_id':None,'side_b_space_id':None,'traversable':False}
    for key,value in required.items():
        if portal.get(key)!=value:raise ValueError(f'denied portal active source state drift: {key}')
    if portal['source_observation']['kind']!='unknown' or portal['source_observation']['status']!='candidate' or portal['status']!='candidate':raise ValueError('denied portal active observation drift')
    if history.get('status')!='rejected_by_source_evidence' or history.get('reason_code')!='duplicate_or_wrong_axis_rejected_by_independent_arbiter':raise ValueError('denied portal rejection decision drift')
    metadata=v21_mapping_metadata(doc)
    active=set(metadata['wall_cut_opening_ids'])|set(metadata['gap_portal_ids'])|set(metadata['wall_mesh_inputs'])|set(metadata['ifc_relation_expectations'])
    if PORTAL_ID in active:raise ValueError('denied portal already entered build mapping')
    result={'schema':SCHEMA,'sample_id':str(doc['project']['sample_id']).split()[0],'source_document_sha256':_file_hash(source_document_file),'source_structure_hash':doc['structure_hash'],'deny_record':{'id':PORTAL_ID,'canonical_id_casefold':PORTAL_ID.casefold(),'deny_class':'rejected_duplicate_or_wrong_axis_portal','source_record_required':deepcopy(required),'source_segment_m':deepcopy(portal['source_observation']['nominal_segment_m']),'rejected_history_id':HISTORY_ID,'rejected_artifact_sha256':history['captured_from_artifact_sha256'],'rejected_payload_sha256':history['captured_payload_sha256'],'reason_code':history['reason_code'],'forbidden_consumers':deepcopy(FORBIDDEN),'semantic_promotion':False,'adjacency_authorized':False,'score_effect':'none','build_authorized':False,'ready':False},'mapping_proof':{'wall_cut_opening_ids':metadata['wall_cut_opening_ids'],'gap_portal_ids':metadata['gap_portal_ids'],'wall_mesh_inputs':metadata['wall_mesh_inputs'],'ifc_relation_opening_ids':sorted(metadata['ifc_relation_expectations']),'denied_id_absent':True},'candidate_hash':'0'*64}
    result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
    return result if _skip_validate else validate_demoted_portal_deny(doc,source_document_file,result)
def validate_demoted_portal_deny(document,source_document_file,candidate):
    doc=validate_v21_document(document)
    if candidate.get('schema')!=SCHEMA:raise ValueError('denied portal sidecar schema drift')
    deny=candidate.get('deny_record',{})
    if deny.get('id')!=PORTAL_ID or deny.get('canonical_id_casefold')!=PORTAL_ID.casefold():raise ValueError('denied portal identity drift')
    if deny.get('forbidden_consumers')!=FORBIDDEN:raise ValueError('denied portal consumer set drift')
    for key in ('semantic_promotion','adjacency_authorized','build_authorized','ready'):
        if deny.get(key) is not False:raise ValueError('denied portal was promoted')
    expected=build_demoted_portal_deny(doc,source_document_file,_skip_validate=True)
    if dict(candidate)!=expected:raise ValueError('denied portal source/history/policy drift')
    return deepcopy(dict(candidate))
def enforce_demoted_portal_deny(document,source_document_file,candidate,consumer_manifest:Mapping[str,Sequence[str]]):
    policy=validate_demoted_portal_deny(document,source_document_file,candidate);denied=PORTAL_ID.casefold()
    expected={'wall_cut_opening_ids','gap_portal_ids','wall_mesh_inputs','blender_input_ids','ifc_building_element_ids','ifc_void_relation_ids','ifc_fill_relation_ids','ifc_spatial_relation_ids','adjacency_opening_ids','reachability_opening_ids','entrance_root_opening_ids'}
    if set(consumer_manifest)!=expected:raise ValueError('denied portal consumer manifest schema drift')
    for consumer,ids in consumer_manifest.items():
        if any(str(item).casefold()==denied for item in ids):raise ValueError(f'denied portal reached consumer: {consumer}')
    return {'schema':'source-exclusion-enforcement-v1','source_structure_hash':policy['source_structure_hash'],'deny_candidate_hash':policy['candidate_hash'],'denied_id':PORTAL_ID,'consumer_manifest_hash':_hash(consumer_manifest),'passed':True,'semantic_promotion':False,'score_effect':'none','build_authorized':False}
def empty_consumer_manifest():
    metadata_keys=['wall_cut_opening_ids','gap_portal_ids','wall_mesh_inputs'];other=['blender_input_ids','ifc_building_element_ids','ifc_void_relation_ids','ifc_fill_relation_ids','ifc_spatial_relation_ids','adjacency_opening_ids','reachability_opening_ids','entrance_root_opening_ids'];return {key:[] for key in [*metadata_keys,*other]}
__all__=['PORTAL_ID','FORBIDDEN','build_demoted_portal_deny','validate_demoted_portal_deny','enforce_demoted_portal_deny','empty_consumer_manifest']
