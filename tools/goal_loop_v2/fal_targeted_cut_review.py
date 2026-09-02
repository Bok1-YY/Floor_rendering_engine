"""Strict advisory visual review of targeted cut geometry overlays."""
from __future__ import annotations
import argparse,base64,hashlib,json
from pathlib import Path
import sys
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))
ENDPOINT='https://fal.run/openrouter/router/vision';MODEL='google/gemini-2.5-flash';KEYS={'opening_id','segment_on_visible_opening','host_alignment','opposite_face_geometry','visual_kind','confidence'}
def _sha(raw):return hashlib.sha256(raw).hexdigest()
def artifact_map(row):
    value=row.get('artifacts') or row.get('artifact_bindings')
    if not isinstance(value,dict) or set(value)!={'full','crop'}:raise ValueError('targeted manifest artifact schema mismatch')
    return value
def prompt(oid):return f'''Review ONLY {oid}. The red segment is a candidate opening void; purple is the candidate host centerline; blue lines are candidate host wall faces; cyan S-P/F-A and orange F-N/F-B are distinct candidate regions on opposite sides. Judge the underlying floor-plan pixels, not the overlay labels. Return yes only if the visible drawing supports the red segment as an opening/gap on that host and the cyan/orange regions are visibly on opposite sides. Do not name rooms, infer dimensions, traversal, adjacency, or entrance status. Return one minified JSON object only: {{"opening_id":"{oid}","segment_on_visible_opening":"yes|no|unclear","host_alignment":"agree|conflict|unclear","opposite_face_geometry":"agree|conflict|unclear","visual_kind":"door|window|glazed_interface|wall_gap|unknown|null","confidence":"high|medium|low|null"}}'''
def parse(text,oid):
    if not isinstance(text,str) or not text.strip().startswith('{') or not text.strip().endswith('}'):raise ValueError('targeted review must be bare JSON')
    v=json.loads(text.strip())
    if set(v)!=KEYS or v['opening_id']!=oid:raise ValueError('targeted review schema/id mismatch')
    if v['segment_on_visible_opening'] not in {'yes','no','unclear'} or v['host_alignment'] not in {'agree','conflict','unclear'} or v['opposite_face_geometry'] not in {'agree','conflict','unclear'} or v['visual_kind'] not in {'door','window','glazed_interface','wall_gap','unknown',None} or v['confidence'] not in {'high','medium','low',None}:raise ValueError('targeted review enum mismatch')
    return v
def execute(config_path,manifest_path,oid,output_path,model=MODEL,reasoning=False):
    import requests
    cfg=json.loads(Path(config_path).read_text(encoding='utf-8'));key=str(cfg.get('fal_api_key') or '').strip();manifest=json.loads(Path(manifest_path).read_text(encoding='utf-8'));row=next(x for x in manifest['openings'] if x['opening_id']==oid);artifacts=artifact_map(row);bindings=[];urls=[]
    for role in ('full','crop'):
        p=Path(artifacts[role]['path']);raw=p.read_bytes();digest=_sha(raw)
        if digest!=artifacts[role].get('sha256'):raise ValueError(f'{oid} targeted manifest artifact hash drift: {role}')
        urls.append('data:image/png;base64,'+base64.b64encode(raw).decode());bindings.append({'filename':p.name,'bytes':len(raw),'sha256':digest})
    text=prompt(oid);payload={'image_urls':urls,'prompt':text,'system_prompt':'Return only the requested JSON. Treat overlays as hypotheses, not facts.','model':model,'reasoning':bool(reasoning),'temperature':0,'max_tokens':256,'enable_web_search':False};proxy=str(cfg.get('fal_queue_proxy') or cfg.get('proxy') or '').strip();resp=None;transport=None
    try:resp=requests.post(ENDPOINT,json=payload,headers={'Authorization':f'Key {key}'},proxies={'http':proxy,'https':proxy} if proxy else None,verify=bool(cfg.get('tls_verify',True)),timeout=180)
    except (requests.ConnectionError,requests.Timeout) as exc:transport=f'{type(exc).__name__}: {exc}'
    raw=None;parsed=None;error=None;status=resp.status_code if resp is not None else None
    if resp is not None:
        try:raw=resp.json()
        except ValueError:raw={'non_json_sha256':_sha(resp.content)}
        try:
            if status!=200:raise ValueError(f'fal HTTP {status}')
            parsed=parse(raw.get('output'),oid)
        except (ValueError,json.JSONDecodeError) as exc:error=str(exc)
    else:error=transport or 'no response'
    result={'schema':'fal-targeted-cut-review-v1','opening_id':oid,'provider':'fal-openrouter-vision','model':model,'reasoning_requested':bool(reasoning),'http_status':status,'prompt_sha256':_sha(text.encode()),'manifest_file_sha256':_sha(Path(manifest_path).read_bytes()),'image_bindings':bindings,'parsed':parsed,'geometry_advisory_complete':parsed is not None and error is None,'cut_confirmation':False,'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'usage':raw.get('usage') if isinstance(raw,dict) else None,'validation_error':error,'transport_error':transport,'raw_response':raw};Path(output_path).parent.mkdir(parents=True,exist_ok=True);Path(output_path).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--opening',choices=['OP002','OP004','OP006','OP007','OP009','OP010'],required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--model',default=MODEL);p.add_argument('--reasoning',action='store_true');a=p.parse_args(argv);r=execute(a.config,a.manifest,a.opening,a.output,a.model,a.reasoning);print(json.dumps({k:r[k] for k in ('opening_id','model','reasoning_requested','http_status','geometry_advisory_complete','parsed','validation_error')},ensure_ascii=False));return 0 if r['geometry_advisory_complete'] else 2
if __name__=='__main__':raise SystemExit(main())
