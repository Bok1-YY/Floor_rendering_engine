from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools.goal_loop_v2.jamb_policy import jamb_policy_binding,minimum_jamb_support_m
ROOT=Path(__file__).resolve().parents[1]
def test_policy_comes_from_v21_contract_not_wall_thickness():
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text());assert minimum_jamb_support_m(d)==pytest.approx(.05);assert jamb_policy_binding(d)['wall_thickness_is_not_policy'];assert any(a['thickness_m']==pytest.approx(.12) for a in d['wall_graph']['atoms'])
def test_missing_invalid_policy_fails_closed():
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text())
 for value in (None,0,-1,float('nan'),2):
  f=deepcopy(d)
  if value is None:del f['opening_contract']['minimum_jamb_support_m']
  else:f['opening_contract']['minimum_jamb_support_m']=value
  with pytest.raises(ValueError):minimum_jamb_support_m(f)
def test_policy_consumers_do_not_reintroduce_wall_thickness_as_jamb_minimum():
 consumers=['op003_adjudication_candidate.py','op005_006_adjudication.py','op007_008_adjudication.py','op009_010_adjudication.py','build_targeted_cut_adjudication.py','build_partition_targeted_adjudication.py']
 for name in consumers:
  text=(ROOT/'tools/goal_loop_v2'/name).read_text(encoding='utf-8')
  assert 'JAMB_MIN' not in text
  assert '0.12' not in text and '.12' not in text
