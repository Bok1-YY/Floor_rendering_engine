import json
import pytest
from tools.goal_loop_v2.fal_targeted_cut_review import parse,prompt
def test_prompt_treats_overlay_as_hypothesis_and_forbids_room_claims():
    p=prompt('OP004');assert 'underlying floor-plan pixels' in p and 'Do not name rooms' in p and 'S-P/F-A' in p and 'F-N/F-B' in p
def test_strict_targeted_parser():
    v={'opening_id':'OP004','segment_on_visible_opening':'yes','host_alignment':'agree','opposite_face_geometry':'agree','visual_kind':'door','confidence':'high'};assert parse(json.dumps(v),'OP004')==v
    v['segment_on_visible_opening']='confirmed'
    with pytest.raises(ValueError,match='enum mismatch'):parse(json.dumps(v),'OP004')
