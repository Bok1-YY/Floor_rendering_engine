import json
from pathlib import Path
import subprocess,sys
from copy import deepcopy
import pytest
from tools.goal_loop_v2.semantic_public_partition import build_semantic_public_partition,validate_semantic_public_partition

ROOT=Path(__file__).resolve().parents[1]
def test_public_partition_is_candidate_only_and_complete():
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text())
 r=build_semantic_public_partition(d)
 assert {x['space_id'] for x in r['cells']}=={'bedroom_corridor','kitchen','living_hall','lobby'}
 assert [x['opening_id'] for x in r['opening_side_candidates']]==['OP002','OP006','OP007','OP008','OP010']
 assert all(c['polygons'] and c['area_m2']>0 and c['anchor_covered'] for c in r['cells'])
 assert r['partition_diagnostics']['maximum_pairwise_overlap_m2']<=1e-9
 assert r['partition_diagnostics']['union_symmetric_difference_m2']<=1e-9
 assert r['partition_diagnostics']['all_anchors_covered']
 for row in r['opening_side_candidates']:
  assert any(side['stable_public_cell_id'] for side in row['sides'])
  for side in row['sides']:
   assert all(sample['offset_from_centerline_m']>.06 for sample in side['samples'])
 assert not r['pair_confirmation'] and not r['adjacency_confirmation'] and not r['build_authorized']
 assert r['score_effect']=='none'
def test_partition_rejects_rehashed_cell_or_promotion_drift():
 import tools.goal_loop_v2.semantic_public_partition as module
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text());r=build_semantic_public_partition(d);f=deepcopy(r);f['cells'][0]['space_id']='FORGED';f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
 with pytest.raises(ValueError,match='geometry/evidence drift'):validate_semantic_public_partition(d,f)
def test_direct_partition_script_runs_outside_repository(tmp_path):
 script=ROOT/'tools/goal_loop_v2/semantic_public_partition.py';result=subprocess.run([sys.executable,str(script)],cwd=tmp_path,text=True,capture_output=True,check=False);assert result.returncode==0,result.stderr
