from copy import deepcopy
import json,subprocess,sys
from pathlib import Path
import pytest
from tools.goal_loop_v2.unit_scope_reachability_v3 import build_unit_scope_reachability_v3,validate_unit_scope_reachability_v3
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'
def test_tier_d_adds_only_bath_and_stays_unconfirmed():
 d=json.loads(SOURCE.read_text());c=build_unit_scope_reachability_v3(d);cc,dd=c['tiers'][-2:];assert set(dd['reachable_space_ids'])-set(cc['reachable_space_ids'])=={'bath'};assert dd['unreachable_scope_space_ids']==['bedroom_02','dry_balcony','north_toilet'];assert dd['edges'][-1]['opening_id']=='OP008' and dd['edges'][-1]['confirmation'] is False;assert c['reachability_confirmation'] is False and c['build_authorized'] is False
def test_forced_tier_d_edge_or_reachability_is_rejected():
 import tools.goal_loop_v2.unit_scope_reachability_v3 as module
 d=json.loads(SOURCE.read_text());c=build_unit_scope_reachability_v3(d)
 for mutate,message in [(lambda x:x['tiers'][-1]['edges'][-1].__setitem__('confirmation',True),'edge was promoted'),(lambda x:x['tiers'][-1]['reachable_space_ids'].append('FORGED'),'graph drift')]:
  f=deepcopy(c);mutate(f);f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
  with pytest.raises(ValueError,match=message):validate_unit_scope_reachability_v3(d,f)
def test_direct_v3_script_runs_outside_repository(tmp_path):
 script=ROOT/'tools/goal_loop_v2/unit_scope_reachability_v3.py';result=subprocess.run([sys.executable,str(script)],cwd=tmp_path,text=True,capture_output=True,check=False);assert result.returncode==0,result.stderr
