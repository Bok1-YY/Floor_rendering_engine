import json
import pytest
from tools.goal_loop_v2.fal_op012_recovery_review import parse,prompt
def test_op012_prompt_separates_neighbors_and_forbids_recovery_claim():
 p=prompt();assert 'distinct from neighboring doors above/below' in p and 'source recovery' in p
def test_op012_strict_parser():
 v={'opening_id':'OP012','nominal_on_visible_door':'unclear','effective_matches_wall_break':'no','swing_aligns_segment':'no','distinct_from_neighbor_openings':'unclear','visual_kind':'wall','confidence':'medium'};assert parse(json.dumps(v))==v;v['visual_kind']='confirmed_door'
 with pytest.raises(ValueError,match='enum mismatch'):parse(json.dumps(v))
