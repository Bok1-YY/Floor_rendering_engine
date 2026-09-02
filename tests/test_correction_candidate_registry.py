from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools.goal_loop_v2.correction_candidate_registry import build_registry,validate_registry
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'
def test_registry_binds_exact_three_nonapplying_2d_candidates():
 d=json.loads(SOURCE.read_text());r=build_registry(d);assert r['opening_ids']==['OP002','OP004','OP009'];assert r['candidates'][2]['directed_side_assignment']['side_a']=='rear_balcony';assert all(c['minimum_jamb_m']>=.12 for c in r['candidates']);assert all(c['application_authorized'] is False for c in r['candidates']);assert r['human_review_pending'] and r['build_authorized'] is False
def test_rehashed_candidate_reversal_or_application_is_rejected():
 import tools.goal_loop_v2.correction_candidate_registry as module
 d=json.loads(SOURCE.read_text());r=build_registry(d)
 for mutate,message in [(lambda x:x.__setitem__('application_authorized',True),'promoted'),(lambda x:x['candidates'][2]['directed_side_assignment'].__setitem__('side_a','bedroom_01'),'direction drift')]:
  f=deepcopy(r);mutate(f);f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
  with pytest.raises(ValueError,match=message):validate_registry(d,f)
