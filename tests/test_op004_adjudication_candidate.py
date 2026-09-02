from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools.goal_loop_v2.opening_side_candidates import build_opening_side_space_candidate
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids
from tools.goal_loop_v2.op004_adjudication_candidate import build_op004_geometry_adjudication_candidate,validate_op004_geometry_adjudication_candidate
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';EVIDENCE=ROOT/'reports/op003_op004_geometry_evidence_20260901/op003-op004-evidence.json'
def _inputs():
    d=json.loads(SOURCE.read_text(encoding='utf-8'));return d,build_opening_side_space_candidate(d),build_target_aware_wall_solids(d)
def test_op004_geometry_packet_is_fail_closed_and_well_supported():
    d,s,w=_inputs();c=build_op004_geometry_adjudication_candidate(d,EVIDENCE,s,w)
    assert c['host_candidate']['atom_id']=='ATOM-WB007-01' and c['registration']['passed'] is True
    assert c['room_pair_candidate']==['north_toilet','bedroom_02']
    assert all(row['supported'] for row in c['jamb_support']['endpoint_support'])
    assert c['source_confirmation'] is False and c['build_authorized'] is False
def test_op004_packet_rejects_forged_inputs_and_promotion():
    import tools.goal_loop_v2.op004_adjudication_candidate as module
    d,s,w=_inputs();c=build_op004_geometry_adjudication_candidate(d,EVIDENCE,s,w);f=deepcopy(c);f['room_pair_candidate']=['wc','kitchen'];f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
    with pytest.raises(ValueError,match='geometry evidence drift'):validate_op004_geometry_adjudication_candidate(d,EVIDENCE,s,w,f)
    bad=deepcopy(s);bad['openings'][0]['opening_id']='FORGED';bad['candidate_hash']=module._hash({k:v for k,v in bad.items() if k!='candidate_hash'})
    with pytest.raises(ValueError):build_op004_geometry_adjudication_candidate(d,EVIDENCE,bad,w)
    p=deepcopy(c);p['source_confirmation']=True
    with pytest.raises(ValueError,match='promoted'):validate_op004_geometry_adjudication_candidate(d,EVIDENCE,s,w,p)
