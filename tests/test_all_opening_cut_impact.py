import json
from pathlib import Path
from tools.goal_loop_v2.all_opening_cut_impact import build_all_opening_cut_impact
ROOT=Path(__file__).resolve().parents[1]
def test_matrix_is_covered_and_fail_closed():
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text()); r=build_all_opening_cut_impact(d)
 assert [x['opening_id'] for x in r['openings']][:11]==[f'OP{i:03d}' for i in range(1,12)]
 assert next(x for x in r['openings'] if x['opening_id']=='OP005')['classification']=='not_cuttable'
 assert next(x for x in r['openings'] if x['opening_id']=='OP011')['classification']=='not_cuttable'
 assert 'PORTAL-WB011-WB006-01' in r['denied_ids']
 assert all(not r[k] for k in ('semantic_promotion','adjacency_confirmation','build_authorized','ready'))
