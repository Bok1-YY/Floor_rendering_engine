from copy import deepcopy
import hashlib,json,subprocess,sys
from pathlib import Path
import pytest
from tools.goal_loop_v2.op012_recovery_evidence import build_op012_recovery_evidence,validate_op012_recovery_evidence
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'
def test_op012_is_quarantined_new_id_and_preserves_active_op005():
 d=json.loads(SOURCE.read_text());c=build_op012_recovery_evidence(d);assert c['opening_id']=='OP012' and c['active_op005_preserved']['mutated'] is False;assert c['registration']['max_endpoint_error_px']<=1;assert c['host_hypothesis']['effective_owner_atom_id']=='ATOM-WB007-01';assert c['host_hypothesis']['nominal_continuation_atom_id']=='ATOM-WB007-02';assert c['host_hypothesis']['junction_id']=='J-007';assert c['jamb_hypothesis']['governing_minimum_m']==pytest.approx(.05);assert c['jamb_hypothesis']['scalar_supports_sufficient'];assert c['recovery_confirmation'] is False
 for artifact in c['artifact_bindings'].values():
  p=Path(artifact['path']);assert p.is_file() and p.stat().st_size==artifact['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==artifact['sha256']
def test_history_host_op005_mutation_or_promotion_is_rejected():
 import tools.goal_loop_v2.op012_recovery_evidence as module
 d=json.loads(SOURCE.read_text());c=build_op012_recovery_evidence(d)
 for mutate,message in [(lambda x:x['historical_provenance'].__setitem__('rejected_payload_sha256','0'*64),'history/source drift'),(lambda x:x['active_op005_preserved'].__setitem__('mutated',True),'OP005 was mutated'),(lambda x:x.__setitem__('recovery_confirmation',True),'promoted')]:
  f=deepcopy(c);mutate(f);f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
  with pytest.raises(ValueError,match=message):validate_op012_recovery_evidence(d,f)
def test_direct_script_runs_outside_repository(tmp_path):
 script=ROOT/'tools/goal_loop_v2/op012_recovery_evidence.py';result=subprocess.run([sys.executable,str(script)],cwd=tmp_path,text=True,capture_output=True,check=False);assert result.returncode==0,result.stderr
