"""Strict fal visual review of quarantined OP012 recovery evidence."""
from __future__ import annotations
import argparse,base64,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
ENDPOINT='https://fal.run/openrouter/router/vision';MODEL='google/gemini-2.5-flash';KEYS={'opening_id','nominal_on_visible_door','effective_matches_wall_break','swing_aligns_segment','distinct_from_neighbor_openings','visual_kind','confidence'}
def _sha(raw):return hashlib.sha256(raw).hexdigest()
def prompt():return '''Review ONLY the quarantined OP012 hypothesis. Red is the historical nominal vertical segment; magenta is the historical effective segment; purple/blue is the candidate wall; cyan marks J-007. Inspect underlying pixels. Determine whether this exact red/magenta segment is a distinct visible opening/door, whether a swing arc aligns with it, and whether it is distinct from neighboring doors above/below. Do not infer room names, adjacency, dimensions, source recovery, or construction. Return one minified JSON object only: {"opening_id":"OP012","nominal_on_visible_door":"yes|no|unclear","effective_matches_wall_break":"yes|no|unclear","swing_aligns_segment":"yes|no|unclear","distinct_from_neighbor_openings":"yes|no|unclear","visual_kind":"door|wall|annotation|unknown|null","confidence":"high|medium|low|null"}'''
def parse(text):
 if not isinstance(text,str) or not text.strip().startswith('{') or not text.strip().endswith('}'):raise ValueError('OP012 review must be bare JSON')
 v=json.loads(text.strip())
 if set(v)!=KEYS or v['opening_id']!='OP012':raise ValueError('OP012 review schema/id mismatch')
 if any(v[k] not in {'yes','no','unclear'} for k in ('nominal_on_visible_door','effective_matches_wall_break','swing_aligns_segment','distinct_from_neighbor_openings')) or v['visual_kind'] not in {'door','wall','annotation','unknown',None} or v['confidence'] not in {'high','medium','low',None}:raise ValueError('OP012 review enum mismatch')
 return v
def execute(config_path,evidence_path,output_path):
 import requests
 cfg=json.loads(Path(config_path).read_text());evidence=json.loads(Path(evidence_path).read_text());key=str(cfg.get('fal_api_key') or '').strip();urls=[];bindings=[]
 for role in ('full','crop'):
  a=evidence['artifact_bindings'][role];p=Path(a['path']);raw=p.read_bytes();digest=_sha(raw)
  if digest!=a['sha256']:raise ValueError('OP012 evidence artifact drift')
  urls.append('data:image/png;base64,'+base64.b64encode(raw).decode());bindings.append({'filename':p.name,'bytes':len(raw),'sha256':digest})
 text=prompt();payload={'image_urls':urls,'prompt':text,'system_prompt':'Return only the requested JSON. Treat overlays and historical records as hypotheses.','model':MODEL,'reasoning':False,'temperature':0,'max_tokens':256,'enable_web_search':False};proxy=str(cfg.get('fal_queue_proxy') or cfg.get('proxy') or '').strip();resp=None;transport=None
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
 result={'schema':'fal-op012-recovery-review-v1','opening_id':'OP012','model':MODEL,'http_status':status,'evidence_file_sha256':_sha(Path(evidence_path).read_bytes()),'evidence_candidate_hash':evidence['candidate_hash'],'prompt_sha256':_sha(text.encode()),'image_bindings':bindings,'parsed':parsed,'usable_advisory':parsed is not None and error is None,'recovery_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'usage':raw.get('usage') if isinstance(raw,dict) else None,'validation_error':error,'transport_error':transport,'raw_response':raw};Path(output_path).parent.mkdir(parents=True,exist_ok=True);Path(output_path).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args(argv);r=execute(a.config,a.evidence,a.output);print(json.dumps({k:r[k] for k in ('opening_id','http_status','usable_advisory','parsed','validation_error')},ensure_ascii=False));return 0 if r['usable_advisory'] else 2
if __name__=='__main__':raise SystemExit(main())
