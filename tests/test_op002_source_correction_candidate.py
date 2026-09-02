import json
from copy import deepcopy
from pathlib import Path
import subprocess,sys
import pytest
from tools.goal_loop_v2.build_op002_source_correction_candidate import build,validate
ROOT=Path(__file__).resolve().parents[1]
def test_op002_wrapper_fields_and_hash():
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text()); c=build(d); assert c['opening_id']=='OP002'; assert c['packet']['directed_side_assignment']=={'side_a':'bedroom_corridor','side_b':'bedroom_01'}; assert c['packet']['jamb_support_m']['minimum_jamb_m']>=.12; assert validate(d,c)==c
def test_wrapper_rejects_forbidden_injection_and_promotion():
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text()); c=build(d); bad=deepcopy(c);bad['packet']['build_kind']='door'
 with pytest.raises(ValueError):validate(d,bad)
 bad=deepcopy(c);bad['semantic_promotion']=True
 with pytest.raises(ValueError,match='promoted'):validate(d,bad)
def test_committed_artifact_is_live_and_reversed_pair_is_rejected_when_rehashed():
 import tools.goal_loop_v2.build_op002_source_correction_candidate as module
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text());live=build(d);committed=json.loads((ROOT/'reports/op002_source_correction_candidate_20260902/op002-source-correction-candidate.json').read_text());assert committed==live
 forged=deepcopy(live);a=forged['packet']['directed_side_assignment'];a['side_a'],a['side_b']=a['side_b'],a['side_a'];forged['packet']['candidate_hash']=module._hash({k:v for k,v in forged['packet'].items() if k!='candidate_hash'});forged['candidate_hash']=module._hash({k:v for k,v in forged.items() if k!='candidate_hash'})
 with pytest.raises(ValueError,match='evidence drift'):validate(d,forged)
def test_direct_script_runs_outside_repository(tmp_path):
 script=ROOT/'tools/goal_loop_v2/build_op002_source_correction_candidate.py';result=subprocess.run([sys.executable,str(script)],cwd=tmp_path,text=True,capture_output=True,check=False);assert result.returncode==0,result.stderr
