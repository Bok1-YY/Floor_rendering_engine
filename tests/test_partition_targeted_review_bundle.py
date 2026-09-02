from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools.goal_loop_v2.partition_targeted_review_bundle import build_partition_targeted_review_bundle,validate_partition_targeted_review_bundle
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';MANIFEST=ROOT/'reports/partition_targeted_adjudication_20260902/partition-targeted-adjudication.json';RESULTS=Path(r'C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_partition_targeted_20260902')
def test_live_partition_targeted_bundle_keeps_op010_protocol_failure():
 if not RESULTS.is_dir():pytest.skip('live results unavailable')
 d=json.loads(SOURCE.read_text());b=build_partition_targeted_review_bundle(d,MANIFEST,RESULTS);assert b['usable_count']==1 and b['correction_review_candidate_ids']==['OP002'];assert b['total_cost_usd']==pytest.approx(.0006773);assert b['reviews'][1]['decision']=='review_protocol_failed' and b['reviews'][1]['parsed'] is None;assert b['build_authorized'] is False
def test_rehashed_promotion_is_rejected():
 if not RESULTS.is_dir():pytest.skip('live results unavailable')
 import tools.goal_loop_v2.partition_targeted_review_bundle as module
 d=json.loads(SOURCE.read_text());b=build_partition_targeted_review_bundle(d,MANIFEST,RESULTS);f=deepcopy(b);f['pair_confirmation']=True;f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
 with pytest.raises(ValueError,match='promoted'):validate_partition_targeted_review_bundle(d,f)
