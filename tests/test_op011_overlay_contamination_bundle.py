import json,hashlib
from pathlib import Path
import pytest
from tools.goal_loop_v2.build_op011_overlay_contamination_bundle import build
def make_inputs(tmp_path):
 from tools.goal_loop_v2.build_op011_overlay_contamination_bundle import HOST,CLEAN
 host=json.loads(HOST.read_text());clean=json.loads(CLEAN.read_text());out=[]
 vals=[('gemini','yes','sliding_access_door','yes'),('openai','yes','sliding_access_door','yes'),('gemini','yes','wall_gap','yes'),('openai','no','unknown','no')]
 for i,(model,wall,sub,trav) in enumerate(vals):
  p=tmp_path/f'{i}.json'; parsed={'opening_id':'OP011','visible_wall_break':wall,'swing_arc_visible':'no','sliding_track_visible':'yes' if i<2 else 'no','subtype':sub,'traversable_visual_cue':trav,'confidence':'high'}; raw={'choices':[{'message':{'content':json.dumps(parsed,separators=(',',':'))}}]};
  source=host if i<2 else clean; bindings=[]
  for role in (('full','crop') if i<2 else ('locator','raw_crop')):
   a=source['artifact_bindings'][role] if i<2 else source['artifacts'][role];bindings.append({'role':role,'filename':Path(a['path']).name,'bytes':a.get('bytes',Path(a['path']).stat().st_size),'sha256':a['sha256']})
  p.write_text(json.dumps({'opening_id':'OP011','http_status':200,'usable_advisory':True,'model':model,'raw_response_sha256':hashlib.sha256(__import__('tools.fastloop_research.contract',fromlist=['canonical_json']).canonical_json(raw)).hexdigest(),'evidence_candidate_hash':source['candidate_hash'],'image_bindings':bindings,'parsed':parsed,'raw_response':raw,'subtype_confirmation':False,'traversability_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'build_authorized':False}));out.append(p)
 return out
def test_bundle_encodes_contamination_and_disagreement(tmp_path):
 r=build(make_inputs(tmp_path));assert len(r['inputs'])==4;assert r['contaminated_consensus']['subtype']=='sliding_access_door';assert r['model_transitions']['subtype']==['sliding_access_door','sliding_access_door'];assert r['clean_provider_disagreement'] and r['overlay_contamination_demonstrated'];assert r['decision']=='unresolved_source_visual_ambiguity';assert r['semantic_promotion'] is False and r['build_authorized'] is False
def test_promoted_or_non200_result_rejected(tmp_path):
 paths=make_inputs(tmp_path)
 x=json.loads(paths[0].read_text());x['usable_advisory']=False;paths[0].write_text(json.dumps(x))
 with pytest.raises(ValueError): build(paths)
