from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools.goal_loop_v2.op002_adjudication_packet_v3 import build_op002_adjudication_packet_v3,validate_op002_adjudication_packet_v3
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';EVIDENCE=ROOT/'reports/op002_vertical_evidence_20260901/op002-vertical-evidence.json'
def _doc():return json.loads(SOURCE.read_text(encoding='utf-8'))
def _result(tmp_path):
    e=json.loads(EVIDENCE.read_text(encoding='utf-8'));v={'http_status':200,'model':'gemini-3.6-flash','image_sha256':[e['artifacts']['full_overlay']['sha256'],e['artifacts']['crop_overlay']['sha256']],'parsed_result':{'opening_id':'OP002','geometry_agreement':'agree','observed_kind':'hinged_door','pair_agreement':'agree','traversable':'yes','complete':True},'failure':None};p=tmp_path/'result.json';p.write_text(json.dumps(v),encoding='utf-8');return p
def test_v3_uses_target_aware_geometry_and_stays_unresolved(tmp_path):
    c=build_op002_adjudication_packet_v3(_doc(),EVIDENCE,_result(tmp_path));g=c['target_aware_geometry']
    assert g['bath_isolated'] is True and g['bedroom_isolated'] is True and g['corridor_on_other_side'] is True
    assert g['public_group']==['bedroom_corridor','kitchen','living_hall','lobby']
    assert c['pair_confirmation'] is False and c['build_authorized'] is False
def test_v3_rejects_rehashed_geometry_and_promotion(tmp_path):
    import tools.goal_loop_v2.op002_adjudication_packet_v3 as module
    d=_doc();r=_result(tmp_path);c=build_op002_adjudication_packet_v3(d,EVIDENCE,r);f=deepcopy(c);f['target_aware_geometry']['bath_isolated']=False;f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
    with pytest.raises(ValueError,match='evidence drift'):validate_op002_adjudication_packet_v3(d,EVIDENCE,r,f)
    p=deepcopy(c);p['pair_confirmation']=True
    with pytest.raises(ValueError,match='promoted'):validate_op002_adjudication_packet_v3(d,EVIDENCE,r,p)
