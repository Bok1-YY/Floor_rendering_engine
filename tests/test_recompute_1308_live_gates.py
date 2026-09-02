import pathlib
import pytest
from tools.goal_loop_v2.recompute_1308_live_gates import DEFAULT_OP002,recompute
def test_live_gate_recompute_covers_all_openings_and_stays_closed():
    if not DEFAULT_OP002.is_file():pytest.skip('external source-bound OP002 Gemini result is unavailable')
    result=recompute(DEFAULT_OP002)
    assert result['source_score']==65
    assert result['hard_failures']==['S06_OPENINGS','S07_SPACES_ADJACENCY_REACHABILITY','S08_PROVENANCE_UNRESOLVED']
    assert result['blocker_counts']['S06_OPENINGS']==33 and result['blocker_counts']['S07_SPACES_ADJACENCY_REACHABILITY']==37 and result['blocker_counts']['S08_PROVENANCE_UNRESOLVED']==2
    assert result['covered_opening_ids']==[f'OP{i:03d}' for i in range(1,12)]
    assert result['all_packets_fail_closed'] and result['demoted_portal_score_effect']=='none'
    assert result['build_authorized'] is False and result['ready'] is False
