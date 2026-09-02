from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import shutil
import pytest
from tools.goal_loop_v2.opening_side_candidates import build_opening_side_space_candidate
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids
from tools.goal_loop_v2.op007_008_adjudication import build_op007_008_adjudication,validate_op007_008_adjudication
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';EVIDENCE=ROOT/'reports/op007_op008_geometry_evidence_20260902/op007-op008-evidence.json'
def _inputs():
    d=json.loads(SOURCE.read_text(encoding='utf-8'));return d,build_opening_side_space_candidate(d),build_target_aware_wall_solids(d)
def test_packets_remain_distinct_with_source_derived_jamb_policy():
    d,s,w=_inputs();c=build_op007_008_adjudication(d,EVIDENCE,s,w);rows={r['opening_id']:r for r in c['openings']}
    assert c['distinctness']['different_host_atoms'] and c['distinctness']['orthogonal_directions']
    assert rows['OP007']['pair_candidate']==['wc','kitchen'] and rows['OP008']['pair_candidate']==['bath','kitchen']
    assert rows['OP007']['jamb_support']['minimum_jamb_m']==pytest.approx(0.06545)
    assert rows['OP008']['jamb_support']['minimum_jamb_m']==pytest.approx(0.035139857,abs=1e-6)
    assert rows['OP007']['jamb_support']['required_minimum_jamb_m']==pytest.approx(0.05)
    assert rows['OP007']['jamb_support']['jamb_sufficient'] is True
    assert rows['OP008']['jamb_support']['jamb_sufficient'] is False
    assert 'JAMB_INSUFFICIENT_AT_ENDPOINT' not in rows['OP007']['blockers']
    assert 'JAMB_INSUFFICIENT_AT_ENDPOINT' in rows['OP008']['blockers']
    assert c['semantic_promotion'] is False and c['build_authorized'] is False
def test_packets_reject_rehashed_merge_and_promotion():
    import tools.goal_loop_v2.op007_008_adjudication as module
    d,s,w=_inputs();c=build_op007_008_adjudication(d,EVIDENCE,s,w);f=deepcopy(c);f['openings'][1]['host_candidate']['atom_id']='ATOM-WB019-01';f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
    with pytest.raises(ValueError,match='evidence drift'):validate_op007_008_adjudication(d,EVIDENCE,s,w,f)
    p=deepcopy(c);p['semantic_promotion']=True
    with pytest.raises(ValueError,match='promoted'):validate_op007_008_adjudication(d,EVIDENCE,s,w,p)

def test_direct_script_entrypoint_works_outside_repository(tmp_path):
    output=tmp_path/'packet.json'
    script=ROOT/'tools/goal_loop_v2/op007_008_adjudication.py'
    result=subprocess.run([sys.executable,str(script),'--output',str(output)],cwd=tmp_path,text=True,capture_output=True,check=False)
    assert result.returncode==0,result.stderr
    packet=json.loads(output.read_text(encoding='utf-8'))
    assert packet['schema']=='op007-op008-adjudication-candidate-v1'
    assert packet['build_authorized'] is False

def test_overlay_artifact_bytes_are_reopened_and_verified(tmp_path):
    evidence=json.loads(EVIDENCE.read_text(encoding='utf-8'))
    copied=tmp_path/EVIDENCE.name
    copied.write_text(json.dumps(evidence),encoding='utf-8')
    for row in evidence['openings']:
        for artifact in row['artifacts'].values():
            source=Path(artifact['path'])
            shutil.copyfile(source,tmp_path/source.name)
    target=tmp_path/'OP008-crop-overlay.png'
    target.write_bytes(target.read_bytes()+b'tamper')
    d,s,w=_inputs()
    with pytest.raises(ValueError,match='artifact hash drift'):
        build_op007_008_adjudication(d,copied,s,w)
