"""Run one strict two-image opening review through fal OpenRouter Vision."""
from __future__ import annotations
import argparse,base64,hashlib,json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))
from tools.goal_loop_v2.gemini_single_opening_review import build_prompt,parse_review_text

ENDPOINT='https://fal.run/openrouter/router/vision';MODEL='google/gemini-2.5-flash';SCHEMA='fal-openrouter-single-opening-review-result-v1'
SYSTEM_PROMPT="Return only the requested JSON object. Do not use markdown, prose, room names, entrance claims, or unsupported dimensions."
def _sha(raw:bytes)->str:return hashlib.sha256(raw).hexdigest()
def _image(path:Path):
    raw=path.read_bytes();return f'data:image/png;base64,{base64.b64encode(raw).decode("ascii")}',{'filename':path.name,'bytes':len(raw),'sha256':_sha(raw)}
def build_request(opening_id:str,full_path:Path,crop_path:Path,model=MODEL):
    full,fb=_image(full_path);crop,cb=_image(crop_path);prompt=build_prompt(opening_id)
    return {'image_urls':[full,crop],'prompt':prompt,'system_prompt':SYSTEM_PROMPT,'model':model,'reasoning':False,'temperature':0,'max_tokens':512,'enable_web_search':False},prompt,[fb,cb]
def execute_review(config_path:Path,opening_id:str,full_path:Path,crop_path:Path,output_path:Path,model=MODEL):
    import requests
    config=json.loads(config_path.read_text(encoding='utf-8'));key=str(config.get('fal_api_key') or '').strip()
    if not key:raise ValueError('fal API key is not configured')
    payload,prompt,bindings=build_request(opening_id,full_path,crop_path,model);proxy=str(config.get('fal_queue_proxy') or config.get('proxy') or '').strip();proxies={'http':proxy,'https':proxy} if proxy else None;response=None;transport_error=None
    try:response=requests.post(ENDPOINT,json=payload,headers={'Authorization':f'Key {key}','Content-Type':'application/json'},proxies=proxies,verify=bool(config.get('tls_verify',True)),timeout=180)
    except (requests.ConnectionError,requests.Timeout) as exc:transport_error=f'{type(exc).__name__}: {exc}'
    raw=None;parsed=None;error=None;status=response.status_code if response is not None else None
    if response is not None:
        try:raw=response.json()
        except ValueError:raw={'non_json_body_sha256':_sha(response.content),'body_preview':response.text[:500]}
        try:
            if status!=200:raise ValueError(f'fal OpenRouter HTTP status {status}')
            if not isinstance(raw,dict) or not isinstance(raw.get('output'),str):raise ValueError('fal OpenRouter output missing')
            parsed=parse_review_text(raw['output'],opening_id)
        except (ValueError,json.JSONDecodeError) as exc:error=str(exc)
    else:error=transport_error or 'fal OpenRouter request failed without response'
    result={'schema':SCHEMA,'provider':'fal-openrouter-vision','model':model,'opening_id':opening_id,'endpoint':ENDPOINT,'http_status':status,'attempts':1,'prompt_sha256':_sha(prompt.encode('utf-8')),'system_prompt_sha256':_sha(SYSTEM_PROMPT.encode('utf-8')),'image_bindings':bindings,'parsed':parsed,'validation_error':error,'transport_error':transport_error,'usage':raw.get('usage') if isinstance(raw,dict) else None,'usable_advisory':parsed is not None and error is None,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'raw_response':raw}
    output_path.parent.mkdir(parents=True,exist_ok=True);output_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--opening',required=True);p.add_argument('--full',type=Path,required=True);p.add_argument('--crop',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--model',default=MODEL);a=p.parse_args(argv);r=execute_review(a.config,a.opening,a.full,a.crop,a.output,a.model);print(json.dumps({k:r[k] for k in ('opening_id','http_status','model','usable_advisory','validation_error')},ensure_ascii=False));return 0 if r['usable_advisory'] else 2
if __name__=='__main__':raise SystemExit(main())
