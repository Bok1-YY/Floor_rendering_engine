import json,hashlib
from pathlib import Path
import pytest
from tools.goal_loop_v2.fal_op011_uncontaminated_review import execute,review_prompt
def inputs(tmp_path,untouched=True):
 files={}
 for role in ('locator','raw_crop'):
  p=tmp_path/(role+'.png');p.write_bytes(role.encode());files[role]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
 e=tmp_path/'e.json';e.write_text(json.dumps({'candidate_hash':'a'*64,'source_pixels_untouched':untouched,'crop_box_px':[10,20,100,120],'registered_segment_px':[[20,40],[30,50]],'artifacts':files}));c=tmp_path/'c.json';c.write_text(json.dumps({'fal_api_key':'SECRET','proxy':'http://p','tls_verify':False}));return c,e,tmp_path/'nested'/'o.json'
class R:
 status_code=200
 def json(self):return {'choices':[{'message':{'content':'{"opening_id":"OP011","visible_wall_break":"no","swing_arc_visible":"no","sliding_track_visible":"no","subtype":"fixed_glazing","traversable_visual_cue":"no","confidence":"medium"}'}}]}
def test_prompt_is_source_native_and_has_no_legacy_overlay_cues():
 p=review_prompt([[120.5,120.0],[120.5,229.0]])
 assert 'byte-exact, unmodified crop' in p and 'source-native' in p
 assert all(term not in p for term in ('Red is','purple','cyan','orange','KITCHEN-CAND','DRY-CAND'))
def test_success_order_coords_and_fail_closed(tmp_path,monkeypatch):
 c,e,o=inputs(tmp_path);seen={}
 def post(*a,**k):seen.update(k);return R()
 monkeypatch.setattr('requests.post',post);r=execute(c,e,o,'openai/gpt-4o-mini');assert [x['role'] for x in r['image_bindings']]==['locator','raw_crop'];assert r['local_target_coordinates']==[[10,20],[20,30]];assert seen['json']['response_format']['json_schema']['strict'] is True;assert r['parsed'] and r['semantic_promotion'] is False;assert 'SECRET' not in o.read_text()
def test_gate_and_fenced_and_drift(tmp_path,monkeypatch):
 c,e,o=inputs(tmp_path,False)
 with pytest.raises(ValueError,match='gate'):execute(c,e,o)
 c,e,o=inputs(tmp_path);monkeypatch.setattr('requests.post',lambda *a,**k:type('X',(),{'status_code':200,'json':lambda s:{'choices':[{'message':{'content':'```json {} ```'}}]}})());assert execute(c,e,o)['usable_advisory'] is False
