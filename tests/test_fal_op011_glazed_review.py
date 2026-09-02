import json
import pytest
from tools.goal_loop_v2.fal_op011_glazed_review import parse,prompt
def test_glazed_prompt_distinguishes_access_and_fixed_subtypes():
 p=prompt();assert 'sliding door/track' in p and 'window/fixed glazing' in p and 'Do not name rooms' in p
def test_glazed_parser_is_strict():
 v={'opening_id':'OP011','visible_wall_break':'yes','swing_arc_visible':'no','sliding_track_visible':'unclear','subtype':'fixed_glazing','traversable_visual_cue':'no','confidence':'medium'};assert parse(json.dumps(v))==v;v['subtype']='door'
 with pytest.raises(ValueError,match='enum mismatch'):parse(json.dumps(v))
