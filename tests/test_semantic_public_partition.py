import json
from pathlib import Path
from tools.goal_loop_v2.semantic_public_partition import build_semantic_public_partition

ROOT=Path(__file__).resolve().parents[1]
def test_public_partition_is_candidate_only_and_complete():
 d=json.loads((ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json').read_text())
 r=build_semantic_public_partition(d)
 assert {x['space_id'] for x in r['cells']}=={'bedroom_corridor','kitchen','living_hall','lobby'}
 assert len(r['opening_side_candidates'])>=5
 assert not r['pair_confirmation'] and not r['adjacency_confirmation'] and not r['build_authorized']
 assert r['score_effect']=='none'
