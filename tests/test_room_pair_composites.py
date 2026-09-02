import json
from pathlib import Path
from tools.goal_loop_v2.build_room_pair_composites import main
ROOT=Path(__file__).resolve().parents[1]
def test_all_eleven_composites_are_registered_and_unselected():
 main(); p=ROOT/'reports/room_pair_composites_20260902/room-pair-composites.json'; d=json.loads(p.read_text())
 assert [x['opening_id'] for x in d['openings']]==[f'OP{i:03d}' for i in range(1,12)]
 assert all(x['registration']['max_endpoint_error_px']<=1 for x in d['openings'])
 assert all(not x['pair_selected'] and not x['semantic_promotion'] and not x['adjacency_confirmation'] for x in d['openings'])
 assert all(Path(a['path']).exists() and len(a['sha256'])==64 for x in d['openings'] for a in x['artifacts'].values())
 assert d['semantic_promotion'] is False and d['adjacency_confirmation'] is False and d['build_authorized'] is False
