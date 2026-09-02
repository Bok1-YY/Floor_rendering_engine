from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools.goal_loop_v2.face_abutment_targets import build_face_abutment_targets, validate_face_abutment_targets

ROOT=Path(__file__).resolve().parents[1]
def _doc(): return json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text(encoding='utf-8'))

def test_1308_face_abutment_inventory_and_bath_target():
    candidate=build_face_abutment_targets(_doc())
    assert candidate['coverage']=={'face_abutment_endpoint_count':40,'status_counts':{'ambiguous_tied_targets':3,'resolved_unique_candidate':27,'unresolved_no_forward_target':10}}
    bath=next(row for row in candidate['records'] if row['atom_id']=='ATOM-WB018-01' and row['endpoint_index']==1)
    assert bath['target_atom_id']=='ATOM-WB017-02'
    assert bath['face_gap_m']==pytest.approx(1.4318435725968592e-7)
    assert candidate['target_confirmation'] is False and candidate['build_authorized'] is False

def test_face_abutment_targets_reject_rehashed_target_and_promotion():
    import tools.goal_loop_v2.face_abutment_targets as module
    document=_doc();candidate=build_face_abutment_targets(document)
    forged=deepcopy(candidate);forged['records'][0]['target_atom_id']='FORGED';forged['candidate_hash']=module._hash({k:v for k,v in forged.items() if k!='candidate_hash'})
    with pytest.raises(ValueError,match='geometry/ranking drift'):validate_face_abutment_targets(document,forged)
    promoted=deepcopy(candidate);promoted['target_confirmation']=True
    with pytest.raises(ValueError,match='promoted'):validate_face_abutment_targets(document,promoted)
