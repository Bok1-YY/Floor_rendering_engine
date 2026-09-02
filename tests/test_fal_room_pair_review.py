import json
import pytest
from tools.goal_loop_v2.fal_room_pair_review import build_prompt,parse_result
def test_prompt_uses_only_neutral_labels_and_forbids_adjacency():
    p=build_prompt('OP003',['A1','A2'],['B1','B2','B3']);assert 'A1,A2' in p and 'B1,B2,B3' in p;assert 'Do not infer door type, room names' in p
def test_parser_rejects_wrong_side_label_or_extra_room_name():
    valid={'opening_id':'OP003','review_status':'agree','side_a_label':'A1','side_b_label':'B2','confidence':'high'};assert parse_result(json.dumps(valid),'OP003',['A1'],['B1','B2'])==valid
    invalid=dict(valid,side_a_label='B1')
    with pytest.raises(ValueError,match='label mismatch'):parse_result(json.dumps(invalid),'OP003',['A1'],['B1','B2'])
    invalid={**valid,'room_name':'forbidden'}
    with pytest.raises(ValueError,match='schema mismatch'):parse_result(json.dumps(invalid),'OP003',['A1'],['B1','B2'])
