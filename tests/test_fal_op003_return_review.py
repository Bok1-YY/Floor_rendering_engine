import json
import pytest
from tools.goal_loop_v2.fal_op003_return_review import parse,prompt
def test_return_prompt_is_source_local_and_nonsemantic():
 p=prompt();assert 'magenta correctly starts at the visible return face' in p and 'Do not name rooms' in p
def test_return_parser_is_strict():
 v={'opening_id':'OP003','nominal_on_visible_door':'yes','effective_starts_at_return_face':'agree','return_wall_alignment':'agree','opposite_face_geometry':'agree','visual_kind':'door','confidence':'high'};assert parse(json.dumps(v))==v;v['return_wall_alignment']='yes'
 with pytest.raises(ValueError,match='enum mismatch'):parse(json.dumps(v))
