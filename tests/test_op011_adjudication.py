from copy import deepcopy
import json
from pathlib import Path
import shutil,subprocess,sys
import pytest
from tools.goal_loop_v2.opening_side_candidates import build_opening_side_space_candidate
from tools.goal_loop_v2.op011_adjudication import build_op011_adjudication,validate_op011_adjudication
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';EVIDENCE=ROOT/'reports/op011_geometry_evidence_20260902/op011-evidence.json'
def _inputs():
    d=json.loads(SOURCE.read_text(encoding='utf-8'));return d,build_opening_side_space_candidate(d),build_target_aware_wall_solids(d)
def test_glazed_interface_remains_coordinate_fact_without_subtype_or_connection():
    d,s,w=_inputs();p=build_op011_adjudication(d,EVIDENCE,s,w)
    assert p['source_snapshot']['kind']=='glazed_interface' and p['source_snapshot']['status']=='unresolved'
    assert p['registration']['max_endpoint_error_px']==0
    assert p['host_candidate'] is None and p['semantic_subtype'] is None and p['selected_space_pair'] is None
    assert p['side_space_rankings'][0]['ambiguity']['ambiguity_class']=='close_ranking'
    assert p['traversability_confirmation'] is False and p['build_authorized'] is False
def test_forged_subtype_pair_cut_or_build_is_rejected():
    d,s,w=_inputs();p=build_op011_adjudication(d,EVIDENCE,s,w)
    for key,value,message in [('semantic_subtype','door','fabricated'),('selected_space_pair',['bath','wc'],'pair selected'),('cut_confirmation',True,'promoted'),('build_authorized',True,'promoted')]:
        f=deepcopy(p);f[key]=value
        with pytest.raises(ValueError,match=message):validate_op011_adjudication(d,EVIDENCE,s,w,f)
def test_overlay_tamper_fails_closed(tmp_path):
    e=json.loads(EVIDENCE.read_text(encoding='utf-8'));copy=tmp_path/EVIDENCE.name;copy.write_text(json.dumps(e),encoding='utf-8')
    for a in e['artifacts'].values():src=Path(a['path']);shutil.copyfile(src,tmp_path/src.name)
    t=tmp_path/'OP011-full-overlay.png';t.write_bytes(t.read_bytes()+b'tamper');d,s,w=_inputs()
    with pytest.raises(ValueError,match='artifact hash drift'):build_op011_adjudication(d,copy,s,w)
def test_direct_entrypoint_outside_repo(tmp_path):
    out=tmp_path/'packet.json';script=ROOT/'tools/goal_loop_v2/op011_adjudication.py';r=subprocess.run([sys.executable,str(script),'--output',str(out)],cwd=tmp_path,text=True,capture_output=True,check=False);assert r.returncode==0,r.stderr;assert json.loads(out.read_text(encoding='utf-8'))['semantic_subtype'] is None
