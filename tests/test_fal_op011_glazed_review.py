import json
from pathlib import Path
import pytest
from tools.goal_loop_v2.fal_op011_glazed_review import parse,prompt,MODEL,execute
def test_default_model_is_preserved_and_cli_model_is_configurable():
 assert MODEL=='google/gemini-2.5-flash'
def test_glazed_prompt_distinguishes_access_and_fixed_subtypes():
 p=prompt();assert 'sliding door/track' in p and 'window/fixed glazing' in p and 'Do not name rooms' in p
def test_glazed_parser_is_strict():
 v={'opening_id':'OP011','visible_wall_break':'yes','swing_arc_visible':'no','sliding_track_visible':'unclear','subtype':'fixed_glazing','traversable_visual_cue':'no','confidence':'medium'};assert parse(json.dumps(v))==v;v['subtype']='door'
 with pytest.raises(ValueError,match='enum mismatch'):parse(json.dumps(v))

def test_execute_propagates_model_and_stays_fail_closed(tmp_path,monkeypatch):
 full=tmp_path/'full.png';crop=tmp_path/'crop.png';full.write_bytes(b'full-png-bytes');crop.write_bytes(b'crop-png-bytes')
 evidence={'candidate_hash':'e'*64,'artifact_bindings':{}}
 for role,path in (('full',full),('crop',crop)):
  import hashlib
  evidence['artifact_bindings'][role]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
 ep=tmp_path/'evidence.json';ep.write_text(json.dumps(evidence),encoding='utf-8')
 secret='super-secret-test-key';cfg=tmp_path/'config.json';cfg.write_text(json.dumps({'fal_api_key':secret}),encoding='utf-8');out=tmp_path/'result.json'
 seen={}
 class Response:
  status_code=200
  def json(self): return {'output':json.dumps({'opening_id':'OP011','visible_wall_break':'no','swing_arc_visible':'no','sliding_track_visible':'no','subtype':'fixed_glazing','traversable_visual_cue':'no','confidence':'medium'})}
 def post(*args,**kwargs): seen['payload']=kwargs['json'];return Response()
 monkeypatch.setattr('requests.post',post)
 result=execute(cfg,ep,out,model='openai/gpt-4o-mini')
 assert seen['payload']['model']=='openai/gpt-4o-mini' and result['model']=='openai/gpt-4o-mini'
 assert result['subtype_confirmation'] is False and result['traversability_confirmation'] is False
 assert result['adjacency_confirmation'] is False and result['semantic_promotion'] is False
 assert result['score_effect']=='none' and result['build_authorized'] is False
 assert secret not in out.read_text(encoding='utf-8')
