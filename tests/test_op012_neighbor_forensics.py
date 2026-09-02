from copy import deepcopy
import hashlib,json,subprocess,sys
from pathlib import Path
import pytest
from tools.goal_loop_v2.op012_neighbor_forensics import build_op012_neighbor_forensics,validate_op012_neighbor_forensics
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'
def test_neighbor_registration_separates_op005_op012_op006():
 d=json.loads(SOURCE.read_text());c=build_op012_neighbor_forensics(d);r=c['relationships'];assert r['op012_lower_to_op006_start_px']==pytest.approx(6,abs=.01);assert r['op012_lower_to_op005_hinge_px']>50;assert r['op012_x_minus_op005_hinge_x_px']==pytest.approx(52.5,abs=.01);assert r['op006_same_x_as_op012_px']<.01;assert all(x['registration']['max_endpoint_error_px']<=1 for x in c['segments'].values());assert c['recovery_confirmation'] is False
 p=Path(c['artifact_binding']['path']);assert p.is_file() and p.stat().st_size==c['artifact_binding']['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==c['artifact_binding']['sha256']
def test_forged_neighbor_relation_or_recovery_is_rejected():
 import tools.goal_loop_v2.op012_neighbor_forensics as module
 d=json.loads(SOURCE.read_text());c=build_op012_neighbor_forensics(d)
 for mutate,message in [(lambda x:x['relationships'].__setitem__('op012_lower_to_op006_start_px',0),'registration drift'),(lambda x:x.__setitem__('recovery_confirmation',True),'promoted')]:
  f=deepcopy(c);mutate(f);f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
  with pytest.raises(ValueError,match=message):validate_op012_neighbor_forensics(d,f)
def test_direct_neighbor_forensics_runs_outside_repo(tmp_path):
 script=ROOT/'tools/goal_loop_v2/op012_neighbor_forensics.py';result=subprocess.run([sys.executable,str(script)],cwd=tmp_path,text=True,capture_output=True,check=False);assert result.returncode==0,result.stderr
