from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools.goal_loop_v2.op002_target_cut_closure import build_op002_target_cut_closure,validate_op002_target_cut_closure
ROOT=Path(__file__).resolve().parents[1]
def _doc():return json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text(encoding='utf-8'))
def _group(groups,space):return next(g for g in groups if space in g)

def test_target_cut_closure_keeps_bath_isolated_and_restores_bedroom():
    c=build_op002_target_cut_closure(_doc())
    assert (c['baseline_topology']['face_candidate_count'],c['physical_topology']['face_candidate_count'],c['closure_topology']['face_candidate_count'])==(14,12,13)
    assert _group(c['baseline_anchor_groups'],'bath')==['bath']
    assert _group(c['physical_anchor_groups'],'bath')==['bath']
    assert _group(c['physical_anchor_groups'],'bedroom_01')==['bedroom_01','bedroom_corridor','kitchen','living_hall','lobby']
    assert _group(c['closure_anchor_groups'],'bedroom_01')==['bedroom_01']
    assert _group(c['closure_anchor_groups'],'bedroom_corridor')==['bedroom_corridor','kitchen','living_hall','lobby']
    assert c['cut_confirmation'] is False and c['build_authorized'] is False

def test_target_cut_closure_sensitivity_and_forgery():
    import tools.goal_loop_v2.op002_target_cut_closure as module
    d=_doc();c=build_op002_target_cut_closure(d)
    assert all(x['topology']['face_candidate_count']==13 for x in c['sensitivity']['half_width_m'])
    by={x['endpoint_delta_m']:x for x in c['sensitivity']['endpoint_delta_m']}
    assert _group(by[-0.001]['anchor_groups'],'bedroom_01')!=['bedroom_01']
    assert _group(by[0.0]['anchor_groups'],'bedroom_01')==['bedroom_01']
    f=deepcopy(c);f['closure_topology']['face_candidate_count']=999;f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
    with pytest.raises(ValueError,match='geometry/topology drift'):validate_op002_target_cut_closure(d,f)
