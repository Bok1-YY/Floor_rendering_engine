import json
from pathlib import Path
from tools.goal_loop_v2.build_partition_resolved_cut_pair import build
ROOT=Path(__file__).resolve().parents[1]
def test_partition_pair_coverage_direction_and_ambiguity():
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text());r=build(d);assert [x['opening_id'] for x in r['openings']]==['OP002','OP006','OP007','OP008','OP010']; assert [x['classification'] for x in r['openings']]==['unique_pair_candidate','unique_pair_candidate','unique_pair_candidate','group_ambiguous','unique_pair_candidate']; assert all(x['sensitivity_stable'] for x in r['openings']);assert all(not r[k] for k in ('pair_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'))
