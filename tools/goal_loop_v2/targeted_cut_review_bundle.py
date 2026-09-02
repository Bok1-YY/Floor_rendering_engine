"""Bind OP004/OP009 targeted Gemini geometry reviews to corrected evidence."""
from __future__ import annotations
from copy import deepcopy
import hashlib,json
from pathlib import Path
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.fal_targeted_cut_review import parse
def _hash(v):return hashlib.sha256(canonical_json(v)).hexdigest()
def build_targeted_cut_review_bundle(document,manifest_path:Path,result_dir:Path,*,_skip_validate=False):
    doc=validate_v21_document(document);manifest=json.loads(manifest_path.read_text(encoding='utf-8'));rows=[]
    for oid in ('OP004','OP009'):
        geometry=next(x for x in manifest['openings'] if x['opening_id']==oid);p=result_dir/oid/'result.json';raw=p.read_bytes();result=json.loads(raw.decode('utf-8'));parsed=parse(json.dumps(result['parsed'],separators=(',',':')),oid);expected=[{'filename':Path(geometry['artifacts'][role]['path']).name,'bytes':Path(geometry['artifacts'][role]['path']).stat().st_size,'sha256':geometry['artifacts'][role]['sha256']} for role in ('full','crop')]
        if result.get('image_bindings')!=expected or not result.get('geometry_advisory_complete'):raise ValueError(f'{oid} targeted result binding/incomplete')
        agree=parsed['segment_on_visible_opening']=='yes' and parsed['host_alignment']=='agree' and parsed['opposite_face_geometry']=='agree'
        rows.append({'opening_id':oid,'geometry_packet_hash':manifest['candidate_hash'],'merged_group':deepcopy(geometry['merged_group']),'jamb_support':deepcopy(geometry['jamb_support']),'result_file_sha256':hashlib.sha256(raw).hexdigest(),'result_canonical_sha256':_hash(result),'parsed':parsed,'image_bindings':expected,'visual_geometry_agreement':agree,'cost_usd':float(result['usage']['cost']),'cut_confirmation':False,'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False})
    result={'schema':'targeted-cut-review-bundle-v1','source_structure_hash':doc['structure_hash'],'targeted_evidence_file_sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest(),'targeted_evidence_candidate_hash':manifest['candidate_hash'],'reviews':rows,'covered_opening_ids':['OP004','OP009'],'all_visual_geometry_agree':all(r['visual_geometry_agreement'] for r in rows),'total_cost_usd':round(sum(r['cost_usd'] for r in rows),10),'decision':'source_correction_candidate_ready_for_independent_review','cut_confirmation':False,'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
    return result if _skip_validate else validate_targeted_cut_review_bundle(doc,result)
def validate_targeted_cut_review_bundle(document,candidate):
    doc=validate_v21_document(document)
    if candidate.get('schema')!='targeted-cut-review-bundle-v1' or candidate.get('source_structure_hash')!=doc['structure_hash'] or candidate.get('covered_opening_ids')!=['OP004','OP009']:raise ValueError('targeted bundle source/coverage drift')
    for key in ('cut_confirmation','pair_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'):
        if candidate.get(key) is not False:raise ValueError('targeted bundle was promoted')
    if candidate.get('candidate_hash')!=_hash({k:v for k,v in candidate.items() if k!='candidate_hash'}):raise ValueError('targeted bundle hash drift')
    return deepcopy(dict(candidate))
__all__=['build_targeted_cut_review_bundle','validate_targeted_cut_review_bundle']
