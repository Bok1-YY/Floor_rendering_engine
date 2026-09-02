from copy import deepcopy
import json,subprocess,sys
from pathlib import Path
import pytest
from tools.goal_loop_v2.op006_junction_endpoint_candidate import build_op006_junction_endpoint_candidate,validate_op006_junction_endpoint_candidate
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'
def test_op006_junction_candidate_is_source_bound_and_fail_closed():
 d=json.loads(SOURCE.read_text());c=build_op006_junction_endpoint_candidate(d);assert c['host_start_node_id']=='J-007';assert c['junction_context']['kind']=='X' and c['junction_context']['status']=='confirmed';assert {x['atom_id'] for x in c['junction_context']['incident_atoms']}=={'ATOM-WB007-01','ATOM-WB007-02','ATOM-WB009-01','ATOM-WB009-02'};assert c['same_wall_support']['upper_to_junction_m']==pytest.approx(.094538);assert c['same_wall_support']['governing_minimum_m']==pytest.approx(.05);assert c['same_wall_support']['sufficient'];assert c['same_wall_support']['crossing_wall_jamb_claim'] is False;assert c['advisory_evidence']['targeted_parsed'] is None;assert c['build_authorized'] is False
def test_wrong_junction_crossing_or_promotion_is_rejected_when_rehashed():
 import tools.goal_loop_v2.op006_junction_endpoint_candidate as module
 d=json.loads(SOURCE.read_text());c=build_op006_junction_endpoint_candidate(d)
 for mutate,message in [(lambda x:x['junction_context'].__setitem__('junction_id','FORGED'),'evidence drift'),(lambda x:x['same_wall_support'].__setitem__('crossing_wall_jamb_claim',True),'crossing jamb'),(lambda x:x.__setitem__('endpoint_ownership_confirmation',True),'promoted')]:
  f=deepcopy(c);mutate(f);f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
  with pytest.raises(ValueError,match=message):validate_op006_junction_endpoint_candidate(d,f)
def test_direct_script_runs_outside_repository(tmp_path):
 script=ROOT/'tools/goal_loop_v2/op006_junction_endpoint_candidate.py';result=subprocess.run([sys.executable,str(script)],cwd=tmp_path,text=True,capture_output=True,check=False);assert result.returncode==0,result.stderr
