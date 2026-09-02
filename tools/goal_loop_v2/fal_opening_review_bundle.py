"""Bind the complete OP001-OP011 fal/OpenRouter Gemini review set."""
from __future__ import annotations
from copy import deepcopy
import hashlib,json
from pathlib import Path
from typing import Any,Mapping

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.gemini_single_opening_review import parse_review_text

SCHEMA='fal-openrouter-opening-review-bundle-v1';MODEL='google/gemini-2.5-flash'
IMAGE_PATHS={
'OP001':('reports/op001_entrance_evidence_20260901/op001-full-overlay.png','reports/op001_entrance_evidence_20260901/op001-crop-overlay.png'),
'OP002':('reports/op002_vertical_evidence_20260901/op002-vertical-full-overlay.png','reports/op002_vertical_evidence_20260901/op002-vertical-crop-overlay.png'),
'OP003':('reports/op003_op004_geometry_evidence_20260901/OP003-full-overlay.png','reports/op003_op004_geometry_evidence_20260901/OP003-crop-overlay.png'),
'OP004':('reports/op003_op004_geometry_evidence_20260901/OP004-full-overlay.png','reports/op003_op004_geometry_evidence_20260901/OP004-crop-overlay.png'),
'OP005':('reports/op005_op006_geometry_evidence_20260901/OP005-full-overlay.png','reports/op005_op006_geometry_evidence_20260901/OP005-crop-overlay.png'),
'OP006':('reports/op005_op006_geometry_evidence_20260901/OP006-full-overlay.png','reports/op005_op006_geometry_evidence_20260901/OP006-crop-overlay.png'),
'OP007':('reports/op007_op008_geometry_evidence_20260902/OP007-full-overlay.png','reports/op007_op008_geometry_evidence_20260902/OP007-crop-overlay.png'),
'OP008':('reports/op007_op008_geometry_evidence_20260902/OP008-full-overlay.png','reports/op007_op008_geometry_evidence_20260902/OP008-crop-overlay.png'),
'OP009':('reports/op009_op010_geometry_evidence_20260901/OP009-full-overlay.png','reports/op009_op010_geometry_evidence_20260901/OP009-crop-overlay.png'),
'OP010':('reports/op009_op010_geometry_evidence_20260901/OP010-full-overlay.png','reports/op009_op010_geometry_evidence_20260901/OP010-crop-overlay.png'),
'OP011':('reports/op011_geometry_evidence_20260902/OP011-full-overlay.png','reports/op011_geometry_evidence_20260902/OP011-crop-overlay.png')}
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def _file_hash(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def _result_path(base:Path,oid:str):return base/oid/('retry-result.json' if oid in {'OP005','OP010'} else 'result.json')
def build_fal_opening_review_bundle(document:Mapping[str,Any],repo_root:Path,result_dir:Path,*,_skip_validate=False):
    doc=validate_v21_document(document);rows=[]
    for oid in sorted(IMAGE_PATHS):
        p=_result_path(result_dir,oid);raw=p.read_bytes();result=json.loads(raw.decode('utf-8'))
        if result.get('schema')!='fal-openrouter-single-opening-review-result-v1' or result.get('opening_id')!=oid or result.get('model')!=MODEL or result.get('usable_advisory') is not True:raise ValueError(f'{oid} fal review is not usable')
        if result.get('semantic_promotion') is not False or result.get('score_effect')!='none' or result.get('build_authorized') is not False:raise ValueError(f'{oid} fal review was promoted')
        parsed=result.get('parsed');parse_review_text(json.dumps(parsed,separators=(',',':')),oid)
        expected=[]
        for rel in IMAGE_PATHS[oid]:
            image=repo_root/rel;expected.append({'filename':image.name,'bytes':image.stat().st_size,'sha256':_file_hash(image)})
        if result.get('image_bindings')!=expected:raise ValueError(f'{oid} fal review image binding drift')
        rows.append({'opening_id':oid,'result_file_sha256':hashlib.sha256(raw).hexdigest(),'result_canonical_sha256':_hash(result),'parsed':deepcopy(parsed),'image_bindings':expected,'usage':deepcopy(result.get('usage')),'cost_usd':float((result.get('usage') or {}).get('cost') or 0),'advisory_complete':True,'semantic_promotion':False,'score_effect':'none','build_authorized':False})
    result={'schema':SCHEMA,'source_structure_hash':doc['structure_hash'],'provider':'fal-openrouter-vision','model':MODEL,'reviews':rows,'covered_opening_ids':[r['opening_id'] for r in rows],'total_cost_usd':round(sum(r['cost_usd'] for r in rows),10),'all_reviews_usable':True,'advisory_only':True,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64}
    result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
    return result if _skip_validate else validate_fal_opening_review_bundle(doc,repo_root,result)
def validate_fal_opening_review_bundle(document,repo_root,candidate):
    doc=validate_v21_document(document)
    if candidate.get('schema')!=SCHEMA or candidate.get('covered_opening_ids')!=sorted(IMAGE_PATHS):raise ValueError('fal review bundle coverage drift')
    for key in ('semantic_promotion','build_authorized','ready'):
        if candidate.get(key) is not False:raise ValueError('fal review bundle was promoted')
    for row in candidate.get('reviews',[]):
        parse_review_text(json.dumps(row.get('parsed'),separators=(',',':')),row.get('opening_id'))
        expected=[]
        for rel in IMAGE_PATHS[row['opening_id']]:
            image=Path(repo_root)/rel;expected.append({'filename':image.name,'bytes':image.stat().st_size,'sha256':_file_hash(image)})
        if row.get('image_bindings')!=expected:raise ValueError('fal review bundle image drift')
        for key in ('semantic_promotion','build_authorized'):
            if row.get(key) is not False:raise ValueError('fal review row was promoted')
    if candidate.get('candidate_hash')!=_hash({k:v for k,v in candidate.items() if k!='candidate_hash'}):raise ValueError('fal review bundle hash drift')
    if candidate.get('source_structure_hash')!=doc['structure_hash'] or candidate.get('score_effect')!='none':raise ValueError('fal review bundle source/score drift')
    return deepcopy(dict(candidate))
__all__=['build_fal_opening_review_bundle','validate_fal_opening_review_bundle']
