from copy import deepcopy
import json,subprocess,sys
from pathlib import Path
import pytest
from tools.goal_loop_v2.unit_scope_reachability_candidate import build_unit_scope_reachability_candidate,validate_unit_scope_reachability_candidate
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'
def test_unit_scope_tiers_are_candidate_only_and_expose_unreachable_spaces():
 d=json.loads(SOURCE.read_text());c=build_unit_scope_reachability_candidate(d);a,b=c['tiers'];assert c['root_hypothesis']['space_id']=='common_core_circulation' and c['root_hypothesis']['building_exterior_root_confirmation'] is False;assert 'lift_shaft' not in c['scope_space_ids'] and c['excluded_nontraversable_space_ids']==['lift_shaft'];assert set(a['reachable_space_ids'])<set(b['reachable_space_ids']);assert {'bedroom_02','north_toilet','west_toilet','bath','dry_balcony'}.issubset(set(b['unreachable_scope_space_ids']));assert c['root_confirmation'] is False and c['build_authorized'] is False;assert all(edge['confirmation'] is False for tier in c['tiers'] for edge in tier['edges'])
def test_forced_root_edge_or_reachability_is_rejected_when_rehashed():
 import tools.goal_loop_v2.unit_scope_reachability_candidate as module
 d=json.loads(SOURCE.read_text());c=build_unit_scope_reachability_candidate(d)
 for mutate,message in [(lambda x:x.__setitem__('root_confirmation',True),'promoted'),(lambda x:x['tiers'][0]['edges'][0].__setitem__('confirmation',True),'edge was promoted'),(lambda x:x['tiers'][0]['reachable_space_ids'].append('FORGED'),'graph drift')]:
  f=deepcopy(c);mutate(f);f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
  with pytest.raises(ValueError,match=message):validate_unit_scope_reachability_candidate(d,f)
def test_direct_script_runs_outside_repository(tmp_path):
 script=ROOT/'tools/goal_loop_v2/unit_scope_reachability_candidate.py';result=subprocess.run([sys.executable,str(script)],cwd=tmp_path,text=True,capture_output=True,check=False);assert result.returncode==0,result.stderr
