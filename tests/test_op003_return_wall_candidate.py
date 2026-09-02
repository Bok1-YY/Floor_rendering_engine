from copy import deepcopy
import hashlib,json,subprocess,sys
from pathlib import Path
import pytest
from tools.goal_loop_v2.op003_return_wall_candidate import build_op003_return_wall_candidate,validate_op003_return_wall_candidate
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'
def test_op003_clips_nominal_start_to_return_face_and_stays_candidate():
 d=json.loads(SOURCE.read_text());c=build_op003_return_wall_candidate(d);assert c['host_atom_id']=='ATOM-WB010-01' and c['return_atom_id']=='ATOM-WB004-01';assert c['return_contact_point_m'][0]==pytest.approx(.096341);assert c['return_clip_distance_m']==pytest.approx(.081704);assert c['effective_width_candidate_m']==pytest.approx(.840419);assert c['jamb_support']['governing_minimum_m']==pytest.approx(.05) and c['jamb_support']['scalar_supports_sufficient'];assert c['directed_side_assignment']['side_a']=='bedroom_01' and c['directed_side_assignment']['side_b']=='west_toilet';assert c['advisory_evidence']['room_pair_conflicts_with_geometry'];assert c['return_face_confirmation'] is False and c['build_authorized'] is False
 for artifact in c['artifact_bindings'].values():
  p=Path(artifact['path']);assert p.is_file() and p.stat().st_size==artifact['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==artifact['sha256']
def test_wrong_return_face_effective_pair_or_promotion_is_rejected():
 import tools.goal_loop_v2.op003_return_wall_candidate as module
 d=json.loads(SOURCE.read_text());c=build_op003_return_wall_candidate(d)
 for mutate,message in [(lambda x:x.__setitem__('return_atom_id','FORGED'),'geometry drift'),(lambda x:x['directed_side_assignment'].__setitem__('side_a','west_toilet'),'geometry drift'),(lambda x:x.__setitem__('return_face_confirmation',True),'promoted')]:
  f=deepcopy(c);mutate(f);f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
  with pytest.raises(ValueError,match=message):validate_op003_return_wall_candidate(d,f)
def test_direct_script_runs_outside_repository(tmp_path):
 script=ROOT/'tools/goal_loop_v2/op003_return_wall_candidate.py';result=subprocess.run([sys.executable,str(script)],cwd=tmp_path,text=True,capture_output=True,check=False);assert result.returncode==0,result.stderr
