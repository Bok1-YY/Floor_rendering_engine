from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools.goal_loop_v2.candidate_opening_cut_impact import build_candidate_opening_cut_impact,validate_candidate_opening_cut_impact
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'
def test_candidate_layer_is_complete_distinct_and_fail_closed():
    d=json.loads(SOURCE.read_text(encoding='utf-8'));r=build_candidate_opening_cut_impact(d);rows={x['opening_id']:x for x in r['openings']}
    assert rows['OP001']['authority']==rows['OP002']['authority']=='source_active'
    assert {oid for oid,row in rows.items() if row['authority']=='registered_evidence_candidate'}=={'OP003','OP004','OP006','OP007','OP008','OP009','OP010'}
    assert rows['OP005']['classification']==rows['OP011']['classification']=='not_cuttable'
    assert rows['PORTAL-WB011-WB006-01']['classification']=='not_cuttable'
    assert all(r[key] is False for key in ('cut_confirmation','pair_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'))
def test_rehashed_promotion_or_geometry_drift_is_rejected():
    import tools.goal_loop_v2.candidate_opening_cut_impact as module
    d=json.loads(SOURCE.read_text(encoding='utf-8'));r=build_candidate_opening_cut_impact(d)
    for mutate,message in [(lambda x:x.__setitem__('build_authorized',True),'promoted'),(lambda x:x['openings'][2].__setitem__('host_atom_id','FORGED'),'geometry/evidence drift')]:
        f=deepcopy(r);mutate(f);f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
        with pytest.raises(ValueError,match=message):validate_candidate_opening_cut_impact(d,f)
