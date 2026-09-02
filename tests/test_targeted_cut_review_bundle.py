from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools.goal_loop_v2.targeted_cut_review_bundle import build_targeted_cut_review_bundle,validate_targeted_cut_review_bundle
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';MANIFEST=ROOT/'reports/targeted_cut_adjudication_20260902/targeted-cut-adjudication.json';RESULTS=Path(r'C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_targeted_cut_jamb50_20260902')
def test_live_targeted_bundle_agrees_but_stays_unpromoted():
    if not RESULTS.is_dir():pytest.skip('live targeted fal results unavailable')
    d=json.loads(SOURCE.read_text());b=build_targeted_cut_review_bundle(d,MANIFEST,RESULTS);assert b['covered_opening_ids']==['OP004','OP009'];assert b['all_visual_geometry_agree'];assert b['total_cost_usd']==pytest.approx(.0006648);assert b['decision']=='source_correction_candidate_ready_for_independent_review';assert b['build_authorized'] is False
def test_rehashed_promotion_is_rejected():
    if not RESULTS.is_dir():pytest.skip('live targeted fal results unavailable')
    import tools.goal_loop_v2.targeted_cut_review_bundle as module
    d=json.loads(SOURCE.read_text());b=build_targeted_cut_review_bundle(d,MANIFEST,RESULTS);f=deepcopy(b);f['cut_confirmation']=True;f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
    with pytest.raises(ValueError,match='promoted'):validate_targeted_cut_review_bundle(d,f)
