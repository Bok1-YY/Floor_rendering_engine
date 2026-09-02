import json
from pathlib import Path
from tools.goal_loop_v2.build_targeted_cut_adjudication import main
ROOT=Path(__file__).resolve().parents[1]
def test_targeted_triplet_is_stable_and_unpromoted():
 main(); d=json.loads((ROOT/'reports/targeted_cut_adjudication_20260902/targeted-cut-adjudication.json').read_text()); assert [x['opening_id'] for x in d['openings']]==['OP003','OP004','OP009']; assert all(x['sensitivity_stable'] for x in d['openings']); assert all(x['registration']['max_endpoint_error_px']<=1 for x in d['openings']); assert all(not x['cut_confirmation'] and not x['pair_confirmation'] and not x['adjacency_confirmation'] for x in d['openings']); assert all(len(a['sha256'])==64 for x in d['openings'] for a in x['artifacts'].values())
