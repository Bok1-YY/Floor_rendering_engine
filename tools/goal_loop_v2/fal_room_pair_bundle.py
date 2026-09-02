"""Bind and audit the neutral-label fal room-pair review trial."""
from __future__ import annotations
from copy import deepcopy
import hashlib,json
from pathlib import Path
from typing import Any,Mapping
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.fal_room_pair_review import parse_result

SCHEMA='fal-room-pair-advisory-bundle-v1'
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def build_fal_room_pair_bundle(document:Mapping[str,Any],manifest_path:Path,result_dir:Path,*,_skip_validate=False):
    doc=validate_v21_document(document);manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('source_structure_hash')!=doc['structure_hash']:raise ValueError('room-pair manifest source drift')
    rows=[]
    for m in manifest['openings']:
        oid=m['opening_id'];p=result_dir/oid/'result.json';raw=p.read_bytes();result=json.loads(raw.decode('utf-8'));mapping={x['label']:x['space_id'] for x in m['anchor_map']};a=sorted(k for k in mapping if k.startswith('A'));b=sorted(k for k in mapping if k.startswith('B'));usable=result.get('usable_advisory') is True;parsed=result.get('parsed')
        if usable:parse_result(json.dumps(parsed,separators=(',',':')),oid,a,b)
        expected=[{'filename':Path(m['artifacts'][role]['path']).name,'bytes':Path(m['artifacts'][role]['path']).stat().st_size,'sha256':m['artifacts'][role]['sha256']} for role in ('full','crop')]
        if result.get('image_bindings')!=expected:raise ValueError(f'{oid} room-pair image binding drift')
        pair=result.get('advisory_pair_candidate') if usable else None;labels=[parsed['side_a_label'],parsed['side_b_label']] if usable else []
        agreement='unusable' if not usable else ('both_top_ranked' if labels==['A1','B1'] else ('partial_top_ranked' if 'A1' in labels or 'B1' in labels else 'no_top_ranked'))
        rows.append({'opening_id':oid,'result_file_sha256':hashlib.sha256(raw).hexdigest(),'result_canonical_sha256':_hash(result),'usable_advisory':usable,'parsed':deepcopy(parsed),'advisory_pair_candidate':deepcopy(pair),'top_rank_agreement':agreement,'cost_usd':float((result.get('usage') or {}).get('cost') or 0),'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False})
    counts={name:sum(r['top_rank_agreement']==name for r in rows) for name in ('both_top_ranked','partial_top_ranked','no_top_ranked','unusable')}
    result={'schema':SCHEMA,'source_structure_hash':doc['structure_hash'],'manifest_file_sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest(),'reviews':rows,'covered_opening_ids':[r['opening_id'] for r in rows],'usable_count':sum(r['usable_advisory'] for r in rows),'agreement_counts':counts,'total_cost_usd':round(sum(r['cost_usd'] for r in rows),10),'method_disposition':'insufficient_for_pair_confirmation','pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
    return result if _skip_validate else validate_fal_room_pair_bundle(doc,result)
def validate_fal_room_pair_bundle(document,candidate):
    doc=validate_v21_document(document)
    if candidate.get('schema')!=SCHEMA or candidate.get('source_structure_hash')!=doc['structure_hash'] or candidate.get('covered_opening_ids')!=[f'OP{i:03d}' for i in range(1,12)]:raise ValueError('room-pair bundle source/coverage drift')
    for key in ('pair_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'):
        if candidate.get(key) is not False:raise ValueError('room-pair bundle was promoted')
    if candidate.get('method_disposition')!='insufficient_for_pair_confirmation' or candidate.get('candidate_hash')!=_hash({k:v for k,v in candidate.items() if k!='candidate_hash'}):raise ValueError('room-pair bundle disposition/hash drift')
    return deepcopy(dict(candidate))
__all__=['build_fal_room_pair_bundle','validate_fal_room_pair_bundle']
