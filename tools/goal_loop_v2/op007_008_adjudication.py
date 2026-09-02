"""Fail-closed distinct OP007/OP008 adjudication candidates."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib,json,math
from pathlib import Path
import sys
from typing import Any,Mapping

# Keep the tool usable both as ``python -m ...`` and as a directly invoked
# repository script.  Pytest happens to add the repository root to sys.path;
# production subprocesses do not.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.opening_side_candidates import build_opening_side_space_candidate,validate_opening_side_space_candidate
from tools.goal_loop_v2.jamb_policy import minimum_jamb_support_m
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids,validate_target_aware_wall_solids

SCHEMA='op007-op008-adjudication-candidate-v1'
CONFIG={'OP007':{'host':'ATOM-WB019-01','pair':['wc','kitchen']},'OP008':{'host':'ATOM-WB018-01','pair':['bath','kitchen']}}
BASE_BLOCKERS=['SOURCE_HOST_MISSING','SOURCE_EFFECTIVE_VOID_MISSING','SOURCE_JAMB_MISSING','SOURCE_SIDE_SPACES_MISSING','SOURCE_STATUS_CANDIDATE','GEMINI_REVIEW_MISSING','HUMAN_REVIEW_PENDING']
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def _binding(path):
    p=Path(path).resolve();raw=p.read_bytes();return {'path':str(p),'file_sha256':hashlib.sha256(raw).hexdigest(),'canonical_sha256':_hash(json.loads(raw.decode('utf-8')))}
def _artifact_binding(evidence_file,artifact):
    evidence_dir=Path(evidence_file).resolve().parent;declared=Path(artifact['path'])
    # Prefer the file beside the evidence manifest.  This keeps a cloned packet
    # verifiable even when the producer recorded an absolute workstation path.
    candidates=(evidence_dir/declared.name,declared)
    actual=next((p for p in candidates if p.is_file()),None)
    if actual is None:raise ValueError(f"OP007/008 evidence artifact missing: {declared.name}")
    raw=actual.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    if digest!=artifact.get('sha256'):raise ValueError(f"OP007/008 evidence artifact hash drift: {declared.name}")
    return {'filename':actual.name,'sha256':digest,'bytes':len(raw)}
def _support(segment,host,minimum):
    a,b=segment;h0,h1=host;dx,dy=h1[0]-h0[0],h1[1]-h0[1];length=math.hypot(dx,dy);den=length*length
    vals=[((p[0]-h0[0])*dx+(p[1]-h0[1])*dy)/den for p in (a,b)];lo,hi=min(vals),max(vals);before=max(0,lo*length);after=max(0,(1-hi)*length)
    return {'host_parameters':[round(v,9) for v in vals],'endpoint_supported':[0<=v<=1 for v in vals],'jamb_before_m':round(before,9),'jamb_after_m':round(after,9),'minimum_jamb_m':round(min(before,after),9),'required_minimum_jamb_m':minimum,'jamb_sufficient':min(before,after)>=minimum,'policy_source':'opening_contract.minimum_jamb_support_m'}
def build_op007_008_adjudication(document:Mapping[str,Any],evidence_file,side_candidate,wall_candidate,*,_skip_validate=False):
    doc=validate_v21_document(document);ev=json.loads(Path(evidence_file).read_text(encoding='utf-8'));side=validate_opening_side_space_candidate(doc,dict(side_candidate));wall=validate_target_aware_wall_solids(doc,dict(wall_candidate));rows=[]
    if ev.get('source_structure_hash')!=doc['structure_hash']:raise ValueError('OP007/008 evidence source drift')
    for oid in ('OP007','OP008'):
        source=next(x for x in doc['opening_contract']['openings'] if x['id']==oid);e=next(x for x in ev['openings'] if x['opening_id']==oid);cfg=CONFIG[oid];host=next(x for x in e['host_wall_candidates'] if x['atom_id']==cfg['host']);jamb=_support(e['source_segment_m'],host['segment_m'],minimum_jamb_support_m(doc));blockers=deepcopy(BASE_BLOCKERS)
        if not jamb['jamb_sufficient']:blockers.append('JAMB_INSUFFICIENT_AT_ENDPOINT')
        rows.append({'opening_id':oid,'source_status':source['status'],'registration':deepcopy(e['registration']),'host_candidate':{'atom_id':cfg['host'],'segment_m':deepcopy(host['segment_m']),'endpoint_distance_sum_m':host['endpoint_distance_sum_m']},'pair_candidate':deepcopy(cfg['pair']),'jamb_support':jamb,'artifact_bindings':{'crop':_artifact_binding(evidence_file,e['artifacts']['crop']),'full':_artifact_binding(evidence_file,e['artifacts']['full'])},'blockers':blockers,'decision':'unresolved_candidate','source_confirmation':False,'pair_confirmation':False,'semantic_promotion':False,'build_authorized':False})
    s0,s1=[r['registration']['expected_pixel_segment'] for r in rows];v0=(s0[1][0]-s0[0][0],s0[1][1]-s0[0][1]);v1=(s1[1][0]-s1[0][0],s1[1][1]-s1[0][1]);dot=v0[0]*v1[0]+v0[1]*v1[1]
    result={'schema':SCHEMA,'source_structure_hash':doc['structure_hash'],'evidence_binding':_binding(evidence_file),'side_candidate_hash':side['candidate_hash'],'target_aware_wall_hash':wall['candidate_hash'],'openings':rows,'distinctness':{'different_opening_ids':True,'different_host_atoms':rows[0]['host_candidate']['atom_id']!=rows[1]['host_candidate']['atom_id'],'pixel_direction_dot':round(dot,9),'orthogonal_directions':abs(dot)<1e-6},'status':'pending_human_review','semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64}
    result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
    return result if _skip_validate else validate_op007_008_adjudication(doc,evidence_file,side_candidate,wall_candidate,result)
def validate_op007_008_adjudication(document,evidence_file,side_candidate,wall_candidate,candidate):
    doc=validate_v21_document(document)
    if candidate.get('schema')!=SCHEMA:raise ValueError('OP007/008 schema drift')
    for key in ('semantic_promotion','build_authorized','ready'):
        if candidate.get(key) is not False:raise ValueError('OP007/008 packet was promoted')
    if dict(candidate)!=build_op007_008_adjudication(doc,evidence_file,side_candidate,wall_candidate,_skip_validate=True):raise ValueError('OP007/008 evidence drift')
    return deepcopy(dict(candidate))

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=REPO_ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json')
    parser.add_argument('--evidence',type=Path,default=REPO_ROOT/'reports/op007_op008_geometry_evidence_20260902/op007-op008-evidence.json')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    document=json.loads(args.source.read_text(encoding='utf-8'))
    packet=build_op007_008_adjudication(document,args.evidence,build_opening_side_space_candidate(document),build_target_aware_wall_solids(document))
    payload=canonical_json(packet)+b'\n'
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_bytes(payload)
    else:
        sys.stdout.buffer.write(payload)
    return 0

__all__=['build_op007_008_adjudication','validate_op007_008_adjudication']

if __name__=='__main__':
    raise SystemExit(main())
