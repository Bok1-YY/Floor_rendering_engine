from copy import deepcopy
import hashlib,json,subprocess,sys
from pathlib import Path
import pytest
from tools.goal_loop_v2.op001_unit_scope_candidate import build_op001_unit_scope_candidate,validate_op001_unit_scope_candidate
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'
def test_op001_distinguishes_building_and_private_unit_scope():
 d=json.loads(SOURCE.read_text());c=build_op001_unit_scope_candidate(d);assert c['building_scope_fact']['intersects_confirmed_outer_boundary'] is False;assert c['unit_scope_hypothesis']['common_side_space_id']=='common_core_circulation';assert c['unit_scope_hypothesis']['unit_side_space_id']=='lobby';assert c['unit_scope_hypothesis']['unit_root_candidate'] is True;assert c['unit_scope_confirmation'] is False;assert c['scope_geometries']['common']['polygon_hash']!=c['scope_geometries']['unit']['polygon_hash'];assert c['registration']['max_endpoint_error_px']<=1
 for artifact in c['artifact_bindings'].values():
  p=Path(artifact['path']);assert p.is_file() and p.stat().st_size==artifact['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==artifact['sha256']
def test_reversed_scope_forced_root_or_promotion_is_rejected():
 import tools.goal_loop_v2.op001_unit_scope_candidate as module
 d=json.loads(SOURCE.read_text());c=build_op001_unit_scope_candidate(d)
 for mutate,message in [(lambda x:x['unit_scope_hypothesis'].__setitem__('common_side_space_id','lobby'),'geometry drift'),(lambda x:x['building_scope_fact'].__setitem__('building_exterior_root_confirmation',True),'exterior root'),(lambda x:x.__setitem__('unit_scope_confirmation',True),'promoted')]:
  f=deepcopy(c);mutate(f);f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
  with pytest.raises(ValueError,match=message):validate_op001_unit_scope_candidate(d,f)
def test_direct_script_runs_outside_repository(tmp_path):
 script=ROOT/'tools/goal_loop_v2/op001_unit_scope_candidate.py';result=subprocess.run([sys.executable,str(script)],cwd=tmp_path,text=True,capture_output=True,check=False);assert result.returncode==0,result.stderr
