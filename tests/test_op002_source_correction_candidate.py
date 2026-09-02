import json
from copy import deepcopy
from pathlib import Path
import pytest
from tools.goal_loop_v2.build_op002_source_correction_candidate import build,validate
ROOT=Path(__file__).resolve().parents[1]
def test_op002_wrapper_fields_and_hash():
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text()); c=build(d); assert c['opening_id']=='OP002'; assert c['packet']['directed_side_assignment']=={'side_a':'bedroom_corridor','side_b':'bedroom_01'}; assert c['packet']['jamb_support_m']['minimum_jamb_m']>=.12; assert validate(d,c)==c
def test_wrapper_rejects_forbidden_injection_and_promotion():
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text()); c=build(d); bad=deepcopy(c);bad['packet']['build_kind']='door'
 with pytest.raises(ValueError):validate(d,bad)
 bad=deepcopy(c);bad['semantic_promotion']=True
 with pytest.raises(ValueError,match='promoted'):validate(d,bad)
