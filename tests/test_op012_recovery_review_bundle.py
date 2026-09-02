from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools.goal_loop_v2.op012_recovery_review_bundle import RESULT,build_op012_recovery_review_bundle,validate_op012_recovery_review_bundle
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'
def test_live_op012_review_conflict_stays_quarantined():
 if not RESULT.is_file():pytest.skip('live OP012 fal result unavailable')
 d=json.loads(SOURCE.read_text());b=build_op012_recovery_review_bundle(d);assert b['fal_supports_recovery'] is True and b['review_conflict'] is True;assert b['decision']=='quarantined_review_conflict' and b['human_adjudication_required'];assert b['recovery_confirmation'] is False and b['build_authorized'] is False;assert b['fal_cost_usd']==pytest.approx(.0003687)
def test_rehashed_conflict_resolution_or_promotion_is_rejected():
 if not RESULT.is_file():pytest.skip('live OP012 fal result unavailable')
 import tools.goal_loop_v2.op012_recovery_review_bundle as module
 d=json.loads(SOURCE.read_text());b=build_op012_recovery_review_bundle(d)
 for mutate,message in [(lambda x:x.__setitem__('review_conflict',False),'schema/state drift'),(lambda x:x.__setitem__('recovery_confirmation',True),'promoted')]:
  f=deepcopy(b);mutate(f);f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
  with pytest.raises(ValueError,match=message):validate_op012_recovery_review_bundle(d,f)
