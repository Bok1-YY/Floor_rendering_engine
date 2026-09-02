import hashlib,subprocess,sys
from pathlib import Path
from tools.goal_loop_v2.build_partition_targeted_adjudication import build
ROOT=Path(__file__).resolve().parents[1]
def test_exact_partition_targeted_geometry_artifacts_and_jamb_gate():
 d=build();assert d['opening_ids']==['OP002','OP006','OP007','OP010'];assert [x['jamb_classification'] for x in d['openings']]==['sufficient','insufficient','insufficient','sufficient'];assert all(x['sensitivity_stable'] for x in d['openings']);assert all(not d[k] for k in ('pair_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'))
 for row in d['openings']:
  assert row['public_cell']['polygon_hash']!=row['non_public_face']['polygon_hash'];assert len(row['non_public_face']['space_ids'])==1;assert len(row['host_face_segments_m'])==2;assert row['registration']['max_endpoint_error_px']<=1
  for artifact in row['artifact_bindings'].values():
   p=Path(artifact['path']);assert p.is_file() and p.stat().st_size==artifact['bytes'];assert hashlib.sha256(p.read_bytes()).hexdigest()==artifact['sha256']
def test_direct_script_runs_outside_repository(tmp_path):
 script=ROOT/'tools/goal_loop_v2/build_partition_targeted_adjudication.py';result=subprocess.run([sys.executable,str(script)],cwd=tmp_path,text=True,capture_output=True,check=False);assert result.returncode==0,result.stderr
