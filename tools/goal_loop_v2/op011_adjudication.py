"""Fail-closed OP011 glazed-interface adjudication candidate."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib,json
from pathlib import Path
import sys
from typing import Any,Mapping

REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.opening_side_candidates import build_opening_side_space_candidate,validate_opening_side_space_candidate
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids,validate_target_aware_wall_solids

SCHEMA='op011-glazed-interface-adjudication-candidate-v1'
BLOCKERS=['SOURCE_STATUS_UNRESOLVED','SOURCE_HOST_MISSING','SOURCE_EFFECTIVE_VOID_MISSING','SOURCE_JAMB_MISSING','SOURCE_SIDE_SPACES_MISSING','GLAZED_SUBTYPE_UNCONFIRMED','TRAVERSABILITY_UNCONFIRMED','PHYSICAL_WALL_BREAK_UNPROVEN','ADJACENCY_UNPROVEN','LEFT_SIDE_CLOSE_RANKING','Z_DIMENSIONS_ASSUMED_RESEARCH_ONLY','GEMINI_ROUTE_FAILURE','HUMAN_REVIEW_PENDING']
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def _binding(path):
    p=Path(path).resolve();raw=p.read_bytes();return {'path':str(p),'file_sha256':hashlib.sha256(raw).hexdigest(),'canonical_sha256':_hash(json.loads(raw.decode('utf-8')))}
def _artifact(evidence_file,artifact):
    base=Path(evidence_file).resolve().parent;declared=Path(artifact['path']);actual=next((p for p in (base/declared.name,declared) if p.is_file()),None)
    if actual is None:raise ValueError(f'OP011 artifact missing: {declared.name}')
    raw=actual.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    if digest!=artifact.get('sha256'):raise ValueError(f'OP011 artifact hash drift: {declared.name}')
    return {'filename':actual.name,'bytes':len(raw),'sha256':digest}
def build_op011_adjudication(document:Mapping[str,Any],evidence_file,side_candidate,wall_candidate,*,_skip_validate=False):
    doc=validate_v21_document(document);path=Path(evidence_file);ev=json.loads(path.read_text(encoding='utf-8'));side=validate_opening_side_space_candidate(doc,dict(side_candidate));wall=validate_target_aware_wall_solids(doc,dict(wall_candidate));source=next(x for x in doc['opening_contract']['openings'] if x['id']=='OP011');s=next(x for x in side['openings'] if x['opening_id']=='OP011')
    if ev.get('schema')!='op011-geometry-evidence-v1' or ev.get('opening_id')!='OP011':raise ValueError('OP011 evidence schema drift')
    if ev.get('source_structure_hash')!=doc['structure_hash'] or ev.get('source_sha256')!=doc['source']['canonical']['file_sha256']:raise ValueError('OP011 source drift')
    if ev.get('source_segment_m')!=source['source_observation']['nominal_segment_m']:raise ValueError('OP011 segment drift')
    if ev.get('host_wall_candidates')!=[] or any(ev.get(k) is not None for k in ('effective_void','jamb_before','jamb_after','side_a_space_id','side_b_space_id')):raise ValueError('OP011 evidence fabricated structure')
    result={'schema':SCHEMA,'source_structure_hash':doc['structure_hash'],'evidence_binding':_binding(path),'opening_side_candidate_hash':side['candidate_hash'],'target_aware_wall_candidate_hash':wall['candidate_hash'],'opening_id':'OP011','source_snapshot':{'status':source['status'],'observation_status':source['source_observation']['status'],'kind':source['source_observation']['kind'],'build_disposition':source['build_disposition'],'traversable':source['traversable']},'registration':deepcopy(ev['registration']),'source_segment_m':deepcopy(ev['source_segment_m']),'segment_frame':deepcopy(s['segment_frame']),'host_candidate':None,'effective_void_candidate':None,'jamb_before_candidate':None,'jamb_after_candidate':None,'semantic_subtype':None,'side_space_rankings':deepcopy(s['sides']),'selected_space_pair':None,'artifact_bindings':{role:_artifact(path,ev['artifacts'][role]) for role in ('crop','full')},'blockers':deepcopy(BLOCKERS),'decision':'unresolved_glazed_interface_coordinate_fact','host_confirmation':False,'void_confirmation':False,'jamb_confirmation':False,'subtype_confirmation':False,'side_space_confirmation':False,'cut_confirmation':False,'adjacency_confirmation':False,'traversability_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64}
    result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
    return result if _skip_validate else validate_op011_adjudication(doc,path,side,wall,result)
def validate_op011_adjudication(document,evidence_file,side_candidate,wall_candidate,candidate):
    doc=validate_v21_document(document)
    if candidate.get('schema')!=SCHEMA:raise ValueError('OP011 schema drift')
    for key in ('host_confirmation','void_confirmation','jamb_confirmation','subtype_confirmation','side_space_confirmation','cut_confirmation','adjacency_confirmation','traversability_confirmation','semantic_promotion','build_authorized','ready'):
        if candidate.get(key) is not False:raise ValueError(f'OP011 was promoted: {key}')
    if candidate.get('selected_space_pair') is not None:raise ValueError('OP011 unconfirmed pair selected')
    if any(candidate.get(k) is not None for k in ('host_candidate','effective_void_candidate','jamb_before_candidate','jamb_after_candidate','semantic_subtype')):raise ValueError('OP011 structure was fabricated')
    if dict(candidate)!=build_op011_adjudication(doc,evidence_file,side_candidate,wall_candidate,_skip_validate=True):raise ValueError('OP011 evidence or policy drift')
    return deepcopy(dict(candidate))
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,default=REPO_ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json');p.add_argument('--evidence',type=Path,default=REPO_ROOT/'reports/op011_geometry_evidence_20260902/op011-evidence.json');p.add_argument('--output',type=Path);a=p.parse_args(argv);doc=json.loads(a.source.read_text(encoding='utf-8'));packet=build_op011_adjudication(doc,a.evidence,build_opening_side_space_candidate(doc),build_target_aware_wall_solids(doc));raw=canonical_json(packet)+b'\n'
    if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(raw)
    else:sys.stdout.buffer.write(raw)
    return 0
__all__=['build_op011_adjudication','validate_op011_adjudication']
if __name__=='__main__':raise SystemExit(main())
