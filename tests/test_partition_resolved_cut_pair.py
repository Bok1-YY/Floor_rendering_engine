import json
from pathlib import Path
from copy import deepcopy
import subprocess,sys
import pytest
from tools.goal_loop_v2.build_partition_resolved_cut_pair import build,validate
ROOT=Path(__file__).resolve().parents[1]
def test_partition_pair_coverage_direction_and_ambiguity():
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text());r=build(d);assert [x['opening_id'] for x in r['openings']]==['OP002','OP006','OP007','OP008','OP010']; assert [x['classification'] for x in r['openings']]==['unique_pair_candidate','unique_pair_candidate','unique_pair_candidate','group_ambiguous','unique_pair_candidate']; assert all(x['sensitivity_stable'] for x in r['openings']);assert all(not r[k] for k in ('pair_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'))
 expected={'OP002':{'side_a':'bedroom_corridor','side_b':'bedroom_01'},'OP006':{'side_a':'bedroom_03','side_b':'bedroom_corridor'},'OP007':{'side_a':'lobby','side_b':'wc'},'OP010':{'side_a':'kitchen','side_b':'front_balcony'}}
 for row in r['openings']:
  if row['opening_id']=='OP008':assert row['directed_side_assignment'] is None and row['non_public_anchor_ids']==['bath','wc']
  else:assert row['directed_side_assignment']==expected[row['opening_id']]
def test_rehashed_reversal_forced_ambiguity_and_promotion_are_rejected():
 import tools.goal_loop_v2.build_partition_resolved_cut_pair as module
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text());r=build(d)
 mutations=[lambda x:x['openings'][2].__setitem__('directed_side_assignment',{'side_a':'wc','side_b':'lobby'}),lambda x:x['openings'][3].__setitem__('directed_side_assignment',{'side_a':'lobby','side_b':'bath'}),lambda x:x.__setitem__('pair_confirmation',True)]
 for mutate in mutations:
  f=deepcopy(r);mutate(f);f['candidate_hash']=module.h({k:v for k,v in f.items() if k!='candidate_hash'})
  with pytest.raises(ValueError,match='direction drift'):validate(d,f)
def test_direct_script_runs_outside_repository(tmp_path):
 script=ROOT/'tools/goal_loop_v2/build_partition_resolved_cut_pair.py';result=subprocess.run([sys.executable,str(script)],cwd=tmp_path,text=True,capture_output=True,check=False);assert result.returncode==0,result.stderr
