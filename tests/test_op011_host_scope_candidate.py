from copy import deepcopy
import hashlib,json,subprocess,sys
from pathlib import Path
import pytest
from tools.goal_loop_v2.op011_host_scope_candidate import build_op011_host_scope_candidate,validate_op011_host_scope_candidate
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'
def test_op011_exact_host_scope_and_type_conflict_stay_unconfirmed():
 d=json.loads(SOURCE.read_text());c=build_op011_host_scope_candidate(d);assert c['host_atom_id']=='ATOM-WB022-01';assert c['registration']['max_endpoint_error_px']<=1;assert c['jamb_support']['before_m']==pytest.approx(.072722);assert c['jamb_support']['scalar_supports_sufficient'];assert c['directed_side_assignment']['side_a']=='kitchen' and c['directed_side_assignment']['side_b']=='dry_balcony';assert all(x['stable'] for x in c['side_samples']);assert c['advisory_evidence']['source_vs_vlm_type_conflict'];assert c['traversability_confirmation'] is False and c['build_authorized'] is False
 for artifact in c['artifact_bindings'].values():
  p=Path(artifact['path']);assert p.is_file() and p.stat().st_size==artifact['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==artifact['sha256']
def test_wrong_host_pair_type_or_promotion_is_rejected():
 import tools.goal_loop_v2.op011_host_scope_candidate as module
 d=json.loads(SOURCE.read_text());c=build_op011_host_scope_candidate(d)
 for mutate,message in [(lambda x:x.__setitem__('host_atom_id','FORGED'),'geometry drift'),(lambda x:x['directed_side_assignment'].__setitem__('side_b','wc'),'geometry drift'),(lambda x:x.__setitem__('traversability_confirmation',True),'promoted')]:
  f=deepcopy(c);mutate(f);f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
  with pytest.raises(ValueError,match=message):validate_op011_host_scope_candidate(d,f)
def test_direct_script_runs_outside_repository(tmp_path):
 script=ROOT/'tools/goal_loop_v2/op011_host_scope_candidate.py';result=subprocess.run([sys.executable,str(script)],cwd=tmp_path,text=True,capture_output=True,check=False);assert result.returncode==0,result.stderr
