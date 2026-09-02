from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools.goal_loop_v2.fal_opening_review_bundle import build_fal_opening_review_bundle,validate_fal_opening_review_bundle
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';RESULTS=Path(r'C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_openrouter_20260902')
def test_live_fal_bundle_covers_all_openings_and_cost_is_bounded():
    if not RESULTS.is_dir():pytest.skip('live fal result directory unavailable')
    d=json.loads(SOURCE.read_text(encoding='utf-8'));b=build_fal_opening_review_bundle(d,ROOT,RESULTS);assert b['covered_opening_ids']==[f'OP{i:03d}' for i in range(1,12)];assert b['total_cost_usd']==pytest.approx(.0037274);assert b['all_reviews_usable'] and b['build_authorized'] is False
def test_bundle_rejects_rehashed_promotion_or_image_drift():
    if not RESULTS.is_dir():pytest.skip('live fal result directory unavailable')
    import tools.goal_loop_v2.fal_opening_review_bundle as module
    d=json.loads(SOURCE.read_text(encoding='utf-8'));b=build_fal_opening_review_bundle(d,ROOT,RESULTS)
    for mutate,message in [(lambda x:x.__setitem__('build_authorized',True),'promoted'),(lambda x:x['reviews'][0]['image_bindings'][0].__setitem__('sha256','0'*64),'image drift')]:
        f=deepcopy(b);mutate(f);f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
        with pytest.raises(ValueError,match=message):validate_fal_opening_review_bundle(d,ROOT,f)
