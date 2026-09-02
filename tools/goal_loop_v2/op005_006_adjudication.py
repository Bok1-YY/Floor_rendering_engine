"""Build fail-closed, policy-separated OP005/OP006 candidates."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib,json,math
from pathlib import Path
import sys
from typing import Any,Mapping

REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.opening_side_candidates import build_opening_side_space_candidate,validate_opening_side_space_candidate
from tools.goal_loop_v2.jamb_policy import minimum_jamb_support_m
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids,validate_target_aware_wall_solids

SCHEMA='op005-op006-adjudication-candidate-v1';HOSTS={'OP005':None,'OP006':'ATOM-WB007-02'}
COMMON=['SOURCE_HOST_MISSING','SOURCE_EFFECTIVE_VOID_MISSING','SOURCE_JAMB_MISSING','SOURCE_SIDE_SPACES_MISSING','SOURCE_PHYSICAL_WALL_BREAK_MISSING','SOURCE_ADJACENCY_MISSING','TRAVERSABILITY_UNCONFIRMED','Z_DIMENSIONS_ASSUMED_RESEARCH_ONLY','GEMINI_REVIEW_MISSING','HUMAN_REVIEW_PENDING']
SPECIFIC={'OP005':['SOURCE_KIND_UNKNOWN','EVIDENCE_HOST_CANDIDATE_EMPTY','SOURCE_ANCHOR_MISSING','LEFT_SIDE_CLOSE_RANKING'],'OP006':['SOURCE_BUILD_DISPOSITION_EXCLUDED','HOST_ONLY_GEOMETRIC_CANDIDATE','SPACE_PAIR_UNCONFIRMED']}
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def _binding(path):
    p=Path(path).resolve();raw=p.read_bytes();return {'path':str(p),'file_sha256':hashlib.sha256(raw).hexdigest(),'canonical_sha256':_hash(json.loads(raw.decode('utf-8')))}
def _artifact(evidence_file,artifact):
    base=Path(evidence_file).resolve().parent;declared=Path(artifact['path']);actual=next((p for p in (base/declared.name,declared) if p.is_file()),None)
    if actual is None:raise ValueError(f'OP005/006 artifact missing: {declared.name}')
    raw=actual.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    if digest!=artifact.get('sha256'):raise ValueError(f'OP005/006 artifact hash drift: {declared.name}')
    return {'filename':actual.name,'bytes':len(raw),'sha256':digest}
def _support(segment,host,minimum):
    h0,h1=host;dx,dy=h1[0]-h0[0],h1[1]-h0[1];length=math.hypot(dx,dy);den=length*length;values=[((p[0]-h0[0])*dx+(p[1]-h0[1])*dy)/den for p in segment];lo,hi=min(values),max(values);before=max(0,lo*length);after=max(0,(1-hi)*length);measured=min(before,after)
    return {'host_parameters':[round(v,9) for v in values],'endpoint_supported':[0<=v<=1 for v in values],'geometric_jamb_before_m':round(before,9),'geometric_jamb_after_m':round(after,9),'minimum_geometric_jamb_m':round(measured,9),'candidate_policy_minimum_m':minimum,'candidate_policy_sufficient':measured>=minimum,'policy_source':'opening_contract.minimum_jamb_support_m','source_jamb_confirmation':False}
def build_op005_006_adjudication(document:Mapping[str,Any],evidence_file,side_candidate,wall_candidate,*,_skip_validate=False):
    doc=validate_v21_document(document);path=Path(evidence_file);ev=json.loads(path.read_text(encoding='utf-8'));side=validate_opening_side_space_candidate(doc,dict(side_candidate));wall=validate_target_aware_wall_solids(doc,dict(wall_candidate))
    if ev.get('schema')!='op005-op006-geometry-evidence-v1':raise ValueError('OP005/006 evidence schema drift')
    if ev.get('source_structure_hash')!=doc['structure_hash']:raise ValueError('OP005/006 evidence source drift')
    rows=[]
    for oid in ('OP005','OP006'):
        source=next(x for x in doc['opening_contract']['openings'] if x['id']==oid);e=next(x for x in ev['openings'] if x['opening_id']==oid);s=next(x for x in side['openings'] if x['opening_id']==oid);host_id=HOSTS[oid]
        if oid=='OP005':
            if e['host_wall_candidates']:raise ValueError('OP005 unexpectedly acquired an evidence host')
            host=None;support=None
        else:
            atom=next(x for x in doc['wall_graph']['atoms'] if x['id']==host_id);h=next(x for x in e['host_wall_candidates'] if x['atom_id']==host_id)
            if h['segment_m']!=atom['centerline_m']:raise ValueError('OP006 host differs from source atom')
            host={'atom_id':host_id,'branch_id':atom['branch_id'],'segment_m':deepcopy(h['segment_m']),'endpoint_distance_sum_m':h['endpoint_distance_sum_m'],'thickness_m':atom['thickness_m'],'height_m':atom['height_m'],'status':atom['status'],'assumption_ids':deepcopy(atom['assumption_ids'])};support=_support(e['source_segment_m'],h['segment_m'],minimum_jamb_support_m(doc))
        blockers=COMMON+SPECIFIC[oid]
        if support is not None and not support['candidate_policy_sufficient']:blockers=blockers+['GEOMETRIC_JAMB_INSUFFICIENT']
        rows.append({'opening_id':oid,'policy_key':f'{oid.lower()}-independent-policy-v1','source_status':source['status'],'source_observation_status':source['source_observation']['status'],'source_kind':source['source_observation']['kind'],'source_build_disposition':source['build_disposition'],'source_anchor_id':source['source_observation']['anchor_id'],'registration':deepcopy(e['registration']),'source_segment_m':deepcopy(e['source_segment_m']),'segment_frame':deepcopy(s['segment_frame']),'host_candidate':host,'host_support_candidate':support,'side_space_rankings':deepcopy(s['sides']),'selected_space_pair':None,'artifact_bindings':{role:_artifact(path,e['artifacts'][role]) for role in ('crop','full')},'blockers':blockers,'decision':'unresolved_candidate','host_confirmation':False,'void_confirmation':False,'jamb_confirmation':False,'side_space_confirmation':False,'cut_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'build_authorized':False})
    a,b=rows[0]['segment_frame']['tangent_unit'],rows[1]['segment_frame']['tangent_unit'];dot=a[0]*b[0]+a[1]*b[1]
    result={'schema':SCHEMA,'source_structure_hash':doc['structure_hash'],'evidence_binding':_binding(path),'opening_side_candidate_hash':side['candidate_hash'],'target_aware_wall_candidate_hash':wall['candidate_hash'],'openings':rows,'policy_separation':{'different_opening_ids':True,'orthogonal_directions':abs(dot)<1e-9,'direction_dot':round(dot,9),'different_host_policies':True,'shared_cut_or_adjacency_policy':False,'independent_policy_keys':[r['policy_key'] for r in rows]},'status':'pending_composite_review','semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64}
    result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
    return result if _skip_validate else validate_op005_006_adjudication(doc,path,side,wall,result)
def validate_op005_006_adjudication(document,evidence_file,side_candidate,wall_candidate,candidate):
    doc=validate_v21_document(document)
    if candidate.get('schema')!=SCHEMA:raise ValueError('OP005/006 schema drift')
    for key in ('semantic_promotion','build_authorized','ready'):
        if candidate.get(key) is not False:raise ValueError('OP005/006 packet was promoted')
    rows=candidate.get('openings',[])
    if [r.get('opening_id') for r in rows]!=['OP005','OP006']:raise ValueError('OP005/006 opening identity drift')
    for r in rows:
        if r.get('selected_space_pair') is not None:raise ValueError('OP005/006 unconfirmed pair selected')
        for key in ('host_confirmation','void_confirmation','jamb_confirmation','side_space_confirmation','cut_confirmation','adjacency_confirmation','semantic_promotion','build_authorized'):
            if r.get(key) is not False:raise ValueError('OP005/006 opening was promoted')
    if rows[0].get('host_candidate') is not None or rows[0].get('host_support_candidate') is not None:raise ValueError('OP005 host was fabricated')
    if dict(candidate)!=build_op005_006_adjudication(doc,evidence_file,side_candidate,wall_candidate,_skip_validate=True):raise ValueError('OP005/006 evidence or policy drift')
    return deepcopy(dict(candidate))
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,default=REPO_ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json');p.add_argument('--evidence',type=Path,default=REPO_ROOT/'reports/op005_op006_geometry_evidence_20260901/op005-op006-evidence.json');p.add_argument('--output',type=Path);a=p.parse_args(argv);doc=json.loads(a.source.read_text(encoding='utf-8'));packet=build_op005_006_adjudication(doc,a.evidence,build_opening_side_space_candidate(doc),build_target_aware_wall_solids(doc));raw=canonical_json(packet)+b'\n'
    if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(raw)
    else:sys.stdout.buffer.write(raw)
    return 0
__all__=['build_op005_006_adjudication','validate_op005_006_adjudication']
if __name__=='__main__':raise SystemExit(main())
