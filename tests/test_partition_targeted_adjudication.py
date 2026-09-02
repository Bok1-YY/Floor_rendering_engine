import json
from pathlib import Path
from tools.goal_loop_v2.build_partition_targeted_adjudication import main
ROOT=Path(__file__).resolve().parents[1]
def test_exact_partition_targeted_coverage_and_jamb_gate():
 main();d=json.loads((ROOT/'reports/partition_targeted_adjudication_20260902/partition-targeted-adjudication.json').read_text());assert d['opening_ids']==['OP002','OP006','OP007','OP010'];assert [x['jamb_classification'] for x in d['openings']]==['sufficient','insufficient','insufficient','sufficient'];assert all(x['sensitivity_stable'] for x in d['openings']);assert all(not d[k] for k in ('pair_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'))
