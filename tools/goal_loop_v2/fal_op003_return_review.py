"""Strict fal visual review of OP003 return-face/effective-void hypothesis."""
from __future__ import annotations
import argparse,base64,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
ENDPOINT='https://fal.run/openrouter/router/vision';MODEL='google/gemini-2.5-flash';KEYS={'opening_id','nominal_on_visible_door','effective_starts_at_return_face','return_wall_alignment','opposite_face_geometry','visual_kind','confidence'}
def _sha(raw):return hashlib.sha256(raw).hexdigest()
def prompt():return '''Review ONLY OP003. Red is the source nominal door segment, magenta is the clipped effective segment, green is the candidate return-wall face, purple/blue is the host wall, and cyan/orange are opposite pre-cut regions. Inspect underlying pixels, not labels. Determine whether the exact red segment is a visible door, magenta correctly starts at the visible return face, host/return geometry aligns with the drawing, and opposite regions lie on the two sides. Do not name rooms, infer dimensions, traversal, adjacency, source correction, or construction. Return one minified JSON object only: {"opening_id":"OP003","nominal_on_visible_door":"yes|no|unclear","effective_starts_at_return_face":"agree|conflict|unclear","return_wall_alignment":"agree|conflict|unclear","opposite_face_geometry":"agree|conflict|unclear","visual_kind":"door|window|wall_gap|unknown|null","confidence":"high|medium|low|null"}'''
def parse(text):
 if not isinstance(text,str) or not text.strip().startswith('{') or not text.strip().endswith('}'):raise ValueError('OP003 return review must be bare JSON')
 v=json.loads(text.strip())
 if set(v)!=KEYS or v['opening_id']!='OP003':raise ValueError('OP003 return review schema/id mismatch')
 if v['nominal_on_visible_door'] not in {'yes','no','unclear'} or any(v[k] not in {'agree','conflict','unclear'} for k in ('effective_starts_at_return_face','return_wall_alignment','opposite_face_geometry')) or v['visual_kind'] not in {'door','window','wall_gap','unknown',None} or v['confidence'] not in {'high','medium','low',None}:raise ValueError('OP003 return review enum mismatch')
 return v
def execute(config_path,evidence_path,output_path):
 import requests
 cfg=json.loads(Path(config_path).read_text());evidence=json.loads(Path(evidence_path).read_text());key=str(cfg.get('fal_api_key') or '').strip();urls=[];bindings=[]
 for role in ('full','crop'):
  a=evidence['artifact_bindings'][role];p=Path(a['path']);raw=p.read_bytes();digest=_sha(raw)
  if digest!=a['sha256']:raise ValueError('OP003 return artifact drift')
  urls.append('data:image/png;base64,'+base64.b64encode(raw).decode());bindings.append({'filename':p.name,'bytes':len(raw),'sha256':digest})
 text=prompt();payload={'image_urls':urls,'prompt':text,'system_prompt':'Return only requested JSON; treat overlays as hypotheses.','model':MODEL,'reasoning':False,'temperature':0,'max_tokens':256,'enable_web_search':False};proxy=str(cfg.get('fal_queue_proxy') or cfg.get('proxy') or '').strip();resp=None;transport=None
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
 result={'schema':'fal-op003-return-review-v1','opening_id':'OP003','model':MODEL,'http_status':status,'evidence_file_sha256':_sha(Path(evidence_path).read_bytes()),'evidence_candidate_hash':evidence['candidate_hash'],'prompt_sha256':_sha(text.encode()),'image_bindings':bindings,'parsed':parsed,'usable_advisory':parsed is not None and error is None,'return_face_confirmation':False,'effective_void_confirmation':False,'pair_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'usage':raw.get('usage') if isinstance(raw,dict) else None,'validation_error':error,'transport_error':transport,'raw_response':raw};Path(output_path).parent.mkdir(parents=True,exist_ok=True);Path(output_path).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args(argv);r=execute(a.config,a.evidence,a.output);print(json.dumps({k:r[k] for k in ('opening_id','http_status','usable_advisory','parsed','validation_error')},ensure_ascii=False));return 0 if r['usable_advisory'] else 2
if __name__=='__main__':raise SystemExit(main())
