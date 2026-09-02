"""Advisory neutral-label room-side review through fal OpenRouter Vision."""
from __future__ import annotations
import argparse,base64,hashlib,json
from pathlib import Path
import sys
from typing import Any,Mapping

REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))
ENDPOINT='https://fal.run/openrouter/router/vision';MODEL='google/gemini-2.5-flash';SCHEMA='fal-room-pair-advisory-result-v1'
KEYS={'opening_id','review_status','side_a_label','side_b_label','confidence'}
def _sha(raw:bytes)->str:return hashlib.sha256(raw).hexdigest()
def build_prompt(opening_id,a_labels,b_labels):
    return f'''Review ONLY {opening_id}. The red segment is the registered opening. Arrow A and markers {','.join(a_labels)} are on side A; arrow B and markers {','.join(b_labels)} are on side B. For each side, select the one marker whose point lies inside the immediate bounded region touching the red segment on that side. If boundaries are unclear or no marker is visibly in that region, return "unknown". Do not infer door type, room names, dimensions, traversal, adjacency, or an entrance. Return one minified JSON object only: {{"opening_id":"{opening_id}","review_status":"agree|conflict|indeterminate","side_a_label":"{'|'.join(a_labels)}|unknown|null","side_b_label":"{'|'.join(b_labels)}|unknown|null","confidence":"high|medium|low|null"}}'''
def parse_result(text,opening_id,a_labels,b_labels):
    if not isinstance(text,str) or not text.strip().startswith('{') or not text.strip().endswith('}'):raise ValueError('room-pair review must be bare JSON')
    value=json.loads(text.strip())
    if not isinstance(value,dict) or set(value)!=KEYS:raise ValueError('room-pair review schema mismatch')
    if value['opening_id']!=opening_id:raise ValueError('room-pair opening mismatch')
    if value['review_status'] not in {'agree','conflict','indeterminate'} or value['confidence'] not in {'high','medium','low',None}:raise ValueError('room-pair enum mismatch')
    if value['side_a_label'] not in {*a_labels,'unknown',None} or value['side_b_label'] not in {*b_labels,'unknown',None}:raise ValueError('room-pair label mismatch')
    return value
def _data(path):
    raw=path.read_bytes();return 'data:image/png;base64,'+base64.b64encode(raw).decode('ascii'),{'filename':path.name,'bytes':len(raw),'sha256':_sha(raw)}
def execute(config_path:Path,manifest_path:Path,opening_id:str,output_path:Path):
    import requests
    config=json.loads(config_path.read_text(encoding='utf-8'));key=str(config.get('fal_api_key') or '').strip()
    if not key:raise ValueError('fal API key missing')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'));row=next(x for x in manifest['openings'] if x['opening_id']==opening_id);a=[x['label'] for x in row['anchor_map'] if x['label'].startswith('A')];b=[x['label'] for x in row['anchor_map'] if x['label'].startswith('B')];mapping={x['label']:x['space_id'] for x in row['anchor_map']};full=Path(row['artifacts']['full']['path']);crop=Path(row['artifacts']['crop']['path']);fu,fb=_data(full);cu,cb=_data(crop);prompt=build_prompt(opening_id,a,b);payload={'image_urls':[fu,cu],'prompt':prompt,'system_prompt':'Return only the requested JSON. Never name rooms or assert adjacency.','model':MODEL,'reasoning':False,'temperature':0,'max_tokens':256,'enable_web_search':False};proxy=str(config.get('fal_queue_proxy') or config.get('proxy') or '').strip();proxies={'http':proxy,'https':proxy} if proxy else None;response=None;transport=None
    try:response=requests.post(ENDPOINT,json=payload,headers={'Authorization':f'Key {key}','Content-Type':'application/json'},proxies=proxies,verify=bool(config.get('tls_verify',True)),timeout=180)
    except (requests.ConnectionError,requests.Timeout) as exc:transport=f'{type(exc).__name__}: {exc}'
    raw=None;parsed=None;error=None;status=response.status_code if response is not None else None
    if response is not None:
        try:raw=response.json()
        except ValueError:raw={'non_json_sha256':_sha(response.content)}
        try:
            if status!=200:raise ValueError(f'fal HTTP status {status}')
            parsed=parse_result(raw.get('output'),opening_id,a,b)
        except (ValueError,json.JSONDecodeError) as exc:error=str(exc)
    else:error=transport or 'no response'
    pair=None
    if parsed and parsed['side_a_label'] in mapping and parsed['side_b_label'] in mapping:pair=[mapping[parsed['side_a_label']],mapping[parsed['side_b_label']]]
    result={'schema':SCHEMA,'opening_id':opening_id,'provider':'fal-openrouter-vision','model':MODEL,'http_status':status,'attempts':1,'manifest_file_sha256':_sha(manifest_path.read_bytes()),'prompt_sha256':_sha(prompt.encode()),'image_bindings':[fb,cb],'neutral_label_mapping':mapping,'parsed':parsed,'advisory_pair_candidate':pair,'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'usage':raw.get('usage') if isinstance(raw,dict) else None,'validation_error':error,'transport_error':transport,'usable_advisory':parsed is not None and error is None,'raw_response':raw};output_path.parent.mkdir(parents=True,exist_ok=True);output_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--opening',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args(argv);r=execute(a.config,a.manifest,a.opening,a.output);print(json.dumps({k:r[k] for k in ('opening_id','http_status','usable_advisory','advisory_pair_candidate','validation_error')},ensure_ascii=False));return 0 if r['usable_advisory'] else 2
if __name__=='__main__':raise SystemExit(main())
