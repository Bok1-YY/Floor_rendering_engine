from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools.goal_loop_v2.fal_room_pair_bundle import build_fal_room_pair_bundle,validate_fal_room_pair_bundle
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';MANIFEST=ROOT/'reports/room_pair_composites_20260902/room-pair-composites.json';RESULTS=Path(r'C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_room_pair_20260902')
def test_live_room_pair_trial_is_complete_but_non_promotable():
    if not RESULTS.is_dir():pytest.skip('live room-pair results unavailable')
    d=json.loads(SOURCE.read_text(encoding='utf-8'));b=build_fal_room_pair_bundle(d,MANIFEST,RESULTS);assert b['usable_count']==10;assert b['agreement_counts']=={'both_top_ranked':7,'partial_top_ranked':3,'no_top_ranked':0,'unusable':1};assert b['total_cost_usd']==pytest.approx(.0034065);assert b['method_disposition']=='insufficient_for_pair_confirmation' and b['build_authorized'] is False
def test_rehashed_promotion_is_rejected():
    if not RESULTS.is_dir():pytest.skip('live room-pair results unavailable')
    import tools.goal_loop_v2.fal_room_pair_bundle as module
    d=json.loads(SOURCE.read_text(encoding='utf-8'));b=build_fal_room_pair_bundle(d,MANIFEST,RESULTS);f=deepcopy(b);f['pair_confirmation']=True;f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
    with pytest.raises(ValueError,match='promoted'):validate_fal_room_pair_bundle(d,f)
