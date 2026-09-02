"""Strict fal subtype/traversability review for OP011 glazed interface."""
from __future__ import annotations
import argparse,base64,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
ENDPOINT='https://fal.run/openrouter/router/vision';MODEL='google/gemini-2.5-flash';KEYS={'opening_id','visible_wall_break','swing_arc_visible','sliding_track_visible','subtype','traversable_visual_cue','confidence'}
def _sha(raw):return hashlib.sha256(raw).hexdigest()
def prompt():return '''Review ONLY OP011. Red is the registered glazed-interface segment; purple/blue is the exact candidate host wall; cyan is the kitchen-side candidate and orange is the dry-balcony-side candidate. Inspect underlying source pixels, not overlay labels. Distinguish a hinged access door, sliding door/track, window/fixed glazing, wall gap, or unknown. Do not name rooms, infer dimensions, adjacency, source correction, or construction. Return one minified JSON object only: {"opening_id":"OP011","visible_wall_break":"yes|no|unclear","swing_arc_visible":"yes|no|unclear","sliding_track_visible":"yes|no|unclear","subtype":"hinged_access_door|sliding_access_door|window|fixed_glazing|wall_gap|unknown|null","traversable_visual_cue":"yes|no|unclear","confidence":"high|medium|low|null"}'''
def parse(text):
 if not isinstance(text,str) or not text.strip().startswith('{') or not text.strip().endswith('}'):raise ValueError('OP011 glazed review must be bare JSON')
 v=json.loads(text.strip())
 if set(v)!=KEYS or v['opening_id']!='OP011':raise ValueError('OP011 glazed review schema/id mismatch')
 if any(v[k] not in {'yes','no','unclear'} for k in ('visible_wall_break','swing_arc_visible','sliding_track_visible','traversable_visual_cue')) or v['subtype'] not in {'hinged_access_door','sliding_access_door','window','fixed_glazing','wall_gap','unknown',None} or v['confidence'] not in {'high','medium','low',None}:raise ValueError('OP011 glazed review enum mismatch')
 return v
def execute(config_path,evidence_path,output_path,model=MODEL):
 import requests
 cfg=json.loads(Path(config_path).read_text());evidence=json.loads(Path(evidence_path).read_text());key=str(cfg.get('fal_api_key') or '').strip();urls=[];bindings=[]
 for role in ('full','crop'):
  a=evidence['artifact_bindings'][role];p=Path(a['path']);raw=p.read_bytes();digest=_sha(raw)
  if digest!=a['sha256']:raise ValueError('OP011 scope artifact drift')
  urls.append('data:image/png;base64,'+base64.b64encode(raw).decode());bindings.append({'filename':p.name,'bytes':len(raw),'sha256':digest})
 text=prompt();model=str(model or MODEL).strip() or MODEL;payload={'image_urls':urls,'prompt':text,'system_prompt':'Return only requested JSON; treat overlays as hypotheses.','model':model,'reasoning':False,'temperature':0,'max_tokens':256,'enable_web_search':False};proxy=str(cfg.get('fal_queue_proxy') or cfg.get('proxy') or '').strip();resp=None;transport=None
 try:resp=requests.post(ENDPOINT,json=payload,headers={'Authorization':f'Key {key}'},proxies={'http':proxy,'https':proxy} if proxy else None,verify=bool(cfg.get('tls_verify',True)),timeout=180)
 except (requests.ConnectionError,requests.Timeout) as exc:transport=f'{type(exc).__name__}: {exc}'
 raw=None;parsed=None;error=None;status=resp.status_code if resp is not None else None
 if resp is not None:
  try:raw=resp.json()
  except ValueError:raw={'non_json_sha256':_sha(resp.content)}
  try:
   if status!=200:raise ValueError(f'fal HTTP {status}')
   parsed=parse(raw.get('output'))
  except (ValueError,json.JSONDecodeError) as exc:error=str(exc)
 else:error=transport or 'no response'
 result={'schema':'fal-op011-glazed-review-v1','opening_id':'OP011','model':model,'http_status':status,'evidence_file_sha256':_sha(Path(evidence_path).read_bytes()),'evidence_candidate_hash':evidence['candidate_hash'],'prompt_sha256':_sha(text.encode()),'image_bindings':bindings,'parsed':parsed,'usable_advisory':parsed is not None and error is None,'subtype_confirmation':False,'traversability_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'usage':raw.get('usage') if isinstance(raw,dict) else None,'validation_error':error,'transport_error':transport,'raw_response':raw};Path(output_path).parent.mkdir(parents=True,exist_ok=True);Path(output_path).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--model',default=MODEL);a=p.parse_args(argv);r=execute(a.config,a.evidence,a.output,a.model);print(json.dumps({k:r[k] for k in ('opening_id','model','http_status','usable_advisory','parsed','validation_error')},ensure_ascii=False));return 0 if r['usable_advisory'] else 2
if __name__=='__main__':raise SystemExit(main())
