import json,hashlib
from pathlib import Path
import pytest
import subprocess,sys
from tools.goal_loop_v2.fal_op011_structured_review import execute
def test_structured_success_and_bindings(tmp_path,monkeypatch):
 files={}
 for role in ('full','crop'):
  p=tmp_path/(role+'.png');p.write_bytes((role+'bytes').encode());files[role]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
 e=tmp_path/'e.json';e.write_text(json.dumps({'candidate_hash':'a'*64,'artifact_bindings':files}));c=tmp_path/'c.json';c.write_text(json.dumps({'fal_api_key':'SECRET'}));o=tmp_path/'o.json'
 class R:
  status_code=200
  def json(self):return {'choices':[{'message':{'content':'{"opening_id":"OP011","visible_wall_break":"no","swing_arc_visible":"no","sliding_track_visible":"no","subtype":"fixed_glazing","traversable_visual_cue":"no","confidence":"medium"}'}}],'usage':{'total_tokens':1}}
 seen={}
 def post(*a,**k):
  seen['body']=k['json'];return R()
 monkeypatch.setattr('requests.post',post)
 r=execute(c,e,o,'openai/gpt-4o-mini');assert r['model']=='openai/gpt-4o-mini' and seen['body']['response_format']['json_schema']['strict'] is True;assert r['parsed']['opening_id']=='OP011';assert 'SECRET' not in o.read_text()
 assert seen['body']['response_format']['json_schema']['schema']['properties']['opening_id']=={'type':'string','const':'OP011'}
 assert r['routing_control']=='fal_managed_no_per_request_provider_selection'
def test_fenced_content_rejected_and_hash_drift_fails(tmp_path,monkeypatch):
 p=tmp_path/'x.png';p.write_bytes(b'x');e=tmp_path/'e.json';e.write_text(json.dumps({'candidate_hash':'b'*64,'artifact_bindings':{k:{'path':str(p),'sha256':hashlib.sha256(b'x').hexdigest()} for k in ('full','crop')}}));c=tmp_path/'c.json';c.write_text('{}');o=tmp_path/'o.json'
 class R:
  status_code=200
  def json(self):return {'choices':[{'message':{'content':'```json {} ```'}}]}
 monkeypatch.setattr('requests.post',lambda *a,**k:R());assert execute(c,e,o)['usable_advisory'] is False;p.write_bytes(b'changed')
 with pytest.raises(ValueError,match='drift'):execute(c,e,o)

def test_proxy_tls_non200_raw_hash_and_parent(tmp_path,monkeypatch):
 p=tmp_path/'x.png';p.write_bytes(b'x');e=tmp_path/'e.json';e.write_text(json.dumps({'candidate_hash':'b'*64,'artifact_bindings':{k:{'path':str(p),'sha256':hashlib.sha256(b'x').hexdigest()} for k in ('full','crop')}}));c=tmp_path/'c.json';c.write_text(json.dumps({'fal_api_key':'SECRET','fal_queue_proxy':'http://proxy','tls_verify':False}));o=tmp_path/'nested'/'result.json';seen={}
 class R:
  status_code=500;content=b'not-json'
  def json(self):raise ValueError('bad json')
 def post(*a,**k):seen.update(k);return R()
 monkeypatch.setattr('requests.post',post);r=execute(c,e,o);assert r['validation_error']=='fal HTTP 500';assert 'non_json_sha256' in r['raw_response'];assert len(r['raw_response_sha256'])==64;assert seen['proxies']=={'http':'http://proxy','https':'http://proxy'} and seen['verify'] is False;assert o.exists() and 'SECRET' not in o.read_text()

def test_direct_script_help_resolves_repo_imports_from_temp_cwd(tmp_path):
 script=Path(__file__).resolve().parents[1]/'tools/goal_loop_v2/fal_op011_structured_review.py';r=subprocess.run([sys.executable,str(script),'--help'],cwd=tmp_path,capture_output=True,text=True);assert r.returncode==0 and '--model' in r.stdout

def test_transport_error_writes_fail_closed_result(tmp_path,monkeypatch):
 p=tmp_path/'x.png';p.write_bytes(b'x');e=tmp_path/'e.json';e.write_text(json.dumps({'candidate_hash':'b'*64,'artifact_bindings':{k:{'path':str(p),'sha256':hashlib.sha256(b'x').hexdigest()} for k in ('full','crop')}}));c=tmp_path/'c.json';c.write_text('{}');o=tmp_path/'nested'/'o.json'
 import requests
 monkeypatch.setattr('requests.post',lambda *a,**k:(_ for _ in ()).throw(requests.ConnectionError('offline')));r=execute(c,e,o);assert r['usable_advisory'] is False and r['parsed'] is None and 'ConnectionError' in r['transport_error'] and o.exists()
