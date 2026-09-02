from copy import deepcopy
import json
from pathlib import Path
import pytest
from tools.goal_loop_v2.demoted_portal_deny import PORTAL_ID,build_demoted_portal_deny,empty_consumer_manifest,enforce_demoted_portal_deny,validate_demoted_portal_deny
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'
def _inputs():
    d=json.loads(SOURCE.read_text(encoding='utf-8'));return d,build_demoted_portal_deny(d,SOURCE)
def test_current_rejected_portal_is_hash_bound_and_absent_from_mapping():
    d,p=_inputs();assert p['deny_record']['rejected_history_id']=='HISTORY-PORTAL-WB011-WB006-01-REJECTED';assert p['mapping_proof']['denied_id_absent'];assert PORTAL_ID not in p['mapping_proof']['wall_cut_opening_ids'];assert PORTAL_ID not in p['mapping_proof']['gap_portal_ids'];assert enforce_demoted_portal_deny(d,SOURCE,p,empty_consumer_manifest())['passed']
@pytest.mark.parametrize('consumer',['wall_cut_opening_ids','gap_portal_ids','wall_mesh_inputs','blender_input_ids','ifc_building_element_ids','ifc_void_relation_ids','ifc_fill_relation_ids','ifc_spatial_relation_ids','adjacency_opening_ids','reachability_opening_ids','entrance_root_opening_ids'])
def test_every_structural_consumer_rejects_portal_and_case_alias(consumer):
    d,p=_inputs();m=empty_consumer_manifest();m[consumer]=[PORTAL_ID.lower()]
    with pytest.raises(ValueError,match='reached consumer'):enforce_demoted_portal_deny(d,SOURCE,p,m)
def test_rehashed_policy_cannot_change_history_consumer_set_or_promotion():
    import tools.goal_loop_v2.demoted_portal_deny as module
    d,p=_inputs()
    for mutate,message in [(lambda x:x['deny_record'].__setitem__('rejected_payload_sha256','0'*64),'policy drift'),(lambda x:x['deny_record']['forbidden_consumers'].pop(),'consumer set drift'),(lambda x:x['deny_record'].__setitem__('build_authorized',True),'promoted')]:
        f=deepcopy(p);mutate(f);f['candidate_hash']=module._hash({k:v for k,v in f.items() if k!='candidate_hash'})
        with pytest.raises(ValueError,match=message):validate_demoted_portal_deny(d,SOURCE,f)
def test_source_drift_or_fabricated_active_portal_invalidates_policy():
    d,p=_inputs();f=deepcopy(d);portal=next(x for x in f['opening_contract']['openings'] if x['id']==PORTAL_ID);portal['build_disposition']='place_in_preexisting_gap'
    with pytest.raises(ValueError):validate_demoted_portal_deny(f,SOURCE,p)
