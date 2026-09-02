from copy import deepcopy
import json
from pathlib import Path
import shutil,subprocess,sys
import pytest
from tools.goal_loop_v2.opening_side_candidates import build_opening_side_space_candidate
from tools.goal_loop_v2.op005_006_adjudication import build_op005_006_adjudication,validate_op005_006_adjudication
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';EVIDENCE=ROOT/'reports/op005_op006_geometry_evidence_20260901/op005-op006-evidence.json'
def _inputs():
    d=json.loads(SOURCE.read_text(encoding='utf-8'));return d,build_opening_side_space_candidate(d),build_target_aware_wall_solids(d)
def test_distinct_policies_preserve_hostless_op005_and_candidate_host_op006():
    d,s,w=_inputs();p=build_op005_006_adjudication(d,EVIDENCE,s,w);rows={r['opening_id']:r for r in p['openings']}
    assert p['policy_separation']['orthogonal_directions'] and p['policy_separation']['different_host_policies']
    assert rows['OP005']['source_kind']=='unknown' and rows['OP005']['host_candidate'] is None
    assert rows['OP006']['source_kind']=='door' and rows['OP006']['source_observation_status']=='confirmed'
    assert rows['OP006']['host_candidate']['atom_id']=='ATOM-WB007-02'
    assert rows['OP006']['host_support_candidate']['minimum_geometric_jamb_m']==pytest.approx(0.094538)
    assert 'GEOMETRIC_JAMB_INSUFFICIENT' in rows['OP006']['blockers']
    assert rows['OP005']['side_space_rankings'][0]['ambiguity']['ambiguity_class']=='close_ranking'
    assert all(r['selected_space_pair'] is None for r in rows.values())
def test_fabricated_host_pair_or_promotion_is_rejected():
    d,s,w=_inputs();p=build_op005_006_adjudication(d,EVIDENCE,s,w);f=deepcopy(p);f['openings'][0]['host_candidate']={'atom_id':'ATOM-WB009-01'}
    with pytest.raises(ValueError,match='host was fabricated'):validate_op005_006_adjudication(d,EVIDENCE,s,w,f)
    f=deepcopy(p);f['openings'][1]['selected_space_pair']=['bedroom_03','bedroom_corridor']
    with pytest.raises(ValueError,match='pair selected'):validate_op005_006_adjudication(d,EVIDENCE,s,w,f)
    f=deepcopy(p);f['build_authorized']=True
    with pytest.raises(ValueError,match='promoted'):validate_op005_006_adjudication(d,EVIDENCE,s,w,f)
def test_overlay_tamper_fails_closed(tmp_path):
    e=json.loads(EVIDENCE.read_text(encoding='utf-8'));copy=tmp_path/EVIDENCE.name;copy.write_text(json.dumps(e),encoding='utf-8')
    for row in e['openings']:
        for a in row['artifacts'].values():src=Path(a['path']);shutil.copyfile(src,tmp_path/src.name)
    target=tmp_path/'OP006-crop-overlay.png';target.write_bytes(target.read_bytes()+b'tamper');d,s,w=_inputs()
    with pytest.raises(ValueError,match='artifact hash drift'):build_op005_006_adjudication(d,copy,s,w)
def test_direct_entrypoint_outside_repo(tmp_path):
    out=tmp_path/'packet.json';script=ROOT/'tools/goal_loop_v2/op005_006_adjudication.py';r=subprocess.run([sys.executable,str(script),'--output',str(out)],cwd=tmp_path,text=True,capture_output=True,check=False);assert r.returncode==0,r.stderr;assert json.loads(out.read_text(encoding='utf-8'))['build_authorized'] is False
