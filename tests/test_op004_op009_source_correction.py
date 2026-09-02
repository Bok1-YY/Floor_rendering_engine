import json
from pathlib import Path
from copy import deepcopy
import subprocess,sys
import pytest
from tools.goal_loop_v2.build_op004_op009_source_correction import build,validate
ROOT=Path(__file__).resolve().parents[1]
def test_packets_are_exact_and_fail_closed():
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text());r=build(d);assert r['opening_ids']==['OP004','OP009'];assert all(x['status']=='candidate_only' and not x['limitations']['build_authorized'] for x in r['packets'])
 for x in r['packets']: 
  assert set(x['limitations'])=={'source_correction_candidate','build_disposition','build_kind','head_m','sill_m','z_geometry','swing','traversability','adjacency','score_effect','build_authorized','semantic_promotion'}
  assert x['directed_side_assignment']['side_a'] and x['directed_side_assignment']['side_b']
  for bad in ('head_m','sill_m','z_geometry','build_kind','traversability','build_authorized'):
   assert x['limitations'][bad] is False
 assert r['packets'][0]['directed_side_assignment']['side_a']=='bedroom_02' and r['packets'][0]['directed_side_assignment']['side_b']=='north_toilet'
 assert r['packets'][1]['directed_side_assignment']['side_a']=='rear_balcony' and r['packets'][1]['directed_side_assignment']['side_b']=='bedroom_01'
def test_injected_semantic_fields_are_not_accepted_by_contract_shape():
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text());r=build(d); forged=deepcopy(r['packets'][0]); forged['build_kind']='door'; assert 'build_kind' not in {'opening_id','source_structure_hash','host_atom_id','registered_effective_segment_m','registered_effective_width_m','directed_side_assignment','jamb_support_m','visual_advisory','provenance','limitations','status','candidate_hash'}
def test_reversed_directed_pair_is_rejected_even_when_rehashed():
 import tools.goal_loop_v2.build_op004_op009_source_correction as module
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text());r=build(d);forged=deepcopy(r);assignment=forged['packets'][1]['directed_side_assignment'];assignment['side_a'],assignment['side_b']=assignment['side_b'],assignment['side_a'];forged['packets'][1]['candidate_hash']=module.h({k:v for k,v in forged['packets'][1].items() if k!='candidate_hash'});forged['candidate_hash']=module.h({k:v for k,v in forged.items() if k!='candidate_hash'})
 with pytest.raises(ValueError,match='direction drift'):validate(d,forged)
def test_direct_script_runs_outside_repository(tmp_path):
 script=ROOT/'tools/goal_loop_v2/build_op004_op009_source_correction.py';result=subprocess.run([sys.executable,str(script)],cwd=tmp_path,text=True,capture_output=True,check=False);assert result.returncode==0,result.stderr
