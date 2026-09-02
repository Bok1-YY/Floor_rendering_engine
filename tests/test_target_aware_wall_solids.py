from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids,validate_target_aware_wall_solids
ROOT=Path(__file__).resolve().parents[1]
def _doc():return json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text(encoding='utf-8'))

def test_target_aware_wall_restores_bath_face_and_is_fail_closed():
    c=build_target_aware_wall_solids(_doc());t=c['topology']
    assert (t['face_candidate_count'],t['single_anchor_face_count'],t['multi_anchor_face_count'],t['unlabeled_face_count'])==(14,12,1,1)
    assert ['bath'] in c['anchor_groups']
    assert all((row['topology']['face_candidate_count'],row['topology']['single_anchor_face_count'])==(14,12) for row in c['sensitivity'])
    bath=next(row for row in c['extensions'] if row['atom_id']=='ATOM-WB018-01' and row['endpoint_index']==1)
    assert bath['target_atom_id']=='ATOM-WB017-02' and bath['extension_m']==pytest.approx(1.143184357259686e-6)
    assert c['wall_solid_confirmation'] is False and c['build_authorized'] is False

def test_target_aware_wall_rejects_rehashed_geometry_and_promotion():
    import tools.goal_loop_v2.target_aware_wall_solids as module
    d=_doc();c=build_target_aware_wall_solids(d);f=deepcopy(c);f['topology']['face_candidate_count']=99;f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
    with pytest.raises(ValueError,match='geometry/topology drift'):validate_target_aware_wall_solids(d,f)
    p=deepcopy(c);p['wall_solid_confirmation']=True
    with pytest.raises(ValueError,match='promoted'):validate_target_aware_wall_solids(d,p)
