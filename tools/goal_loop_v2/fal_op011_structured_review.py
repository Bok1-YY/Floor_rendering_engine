"""Fail-closed OP011 structured-output client (network execution is caller-controlled)."""
import argparse,base64,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.goal_loop_v2.fal_op011_glazed_review import MODEL,parse,prompt
ENDPOINT='https://fal.run/openrouter/router/openai/v1/chat/completions'
def sha(b):return hashlib.sha256(b).hexdigest()
def execute(config_path,evidence_path,output_path,model=MODEL):
 import requests
 cfg=json.loads(Path(config_path).read_text());ev=json.loads(Path(evidence_path).read_text());images=[];bindings=[]
 for role in ('full','crop'):
  a=ev['artifact_bindings'][role];p=Path(a['path']);raw=p.read_bytes();digest=sha(raw)
  if digest!=a['sha256']:raise ValueError('OP011 scope artifact drift')
  images.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(raw).decode()}});bindings.append({'role':role,'filename':p.name,'bytes':len(raw),'sha256':digest})
 yesno={'type':'string','enum':['yes','no','unclear']}; schema={'type':'object','additionalProperties':False,'properties':{'opening_id':{'type':'string','const':'OP011'},'visible_wall_break':yesno,'swing_arc_visible':yesno,'sliding_track_visible':yesno,'subtype':{'type':['string','null'],'enum':['hinged_access_door','sliding_access_door','window','fixed_glazing','wall_gap','unknown',None]},'traversable_visual_cue':yesno,'confidence':{'type':['string','null'],'enum':['high','medium','low',None]}},'required':['opening_id','visible_wall_break','swing_arc_visible','sliding_track_visible','subtype','traversable_visual_cue','confidence']}
 body={'model':str(model or MODEL).strip() or MODEL,'messages':[{'role':'system','content':'Return only the requested JSON object.'},{'role':'user','content':[{'type':'text','text':prompt()},*images]}],'response_format':{'type':'json_schema','json_schema':{'name':'op011_glazed_review','strict':True,'schema':schema}},'temperature':0,'max_tokens':256}
 contract={'model':body['model'],'routing_control':'fal_managed_no_per_request_provider_selection','messages':[{'role':m['role'],'text':m['content'] if isinstance(m['content'],str) else [{'type':z['type'],'sha256':bindings[i]['sha256']} for i,z in enumerate(m['content'][1:])]} for m in body['messages']],'response_format':body['response_format'],'temperature':body['temperature'],'max_tokens':body['max_tokens']}
 request_hash=sha(json.dumps(contract,sort_keys=True,separators=(',',':')).encode());proxy=str(cfg.get('fal_queue_proxy') or cfg.get('proxy') or '').strip();kwargs={'headers':{'Authorization':f"Key {cfg.get('fal_api_key','')}"},'timeout':180,'verify':bool(cfg.get('tls_verify',True))};
 if proxy: kwargs['proxies']={'http':proxy,'https':proxy}
 try:r=requests.post(ENDPOINT,json=body,**kwargs)
 except (requests.ConnectionError,requests.Timeout) as exc:
  result={'schema':'fal-op011-structured-review-v1','opening_id':'OP011','endpoint':ENDPOINT,'model':body['model'],'routing_control':'fal_managed_no_per_request_provider_selection','request_contract_sha256':request_hash,'payload_schema_sha256':sha(json.dumps(body['response_format'],sort_keys=True,separators=(',',':')).encode()),'prompt_sha256':sha(prompt().encode()),'system_sha256':sha(body['messages'][0]['content'].encode()),'evidence_file_sha256':sha(Path(evidence_path).read_bytes()),'evidence_candidate_hash':ev['candidate_hash'],'image_bindings':bindings,'http_status':None,'parsed':None,'validation_error':None,'usable_advisory':False,'usage':None,'raw_response':None,'raw_response_sha256':None,'transport_error':f'{type(exc).__name__}: {exc}','subtype_confirmation':False,'traversability_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False};Path(output_path).parent.mkdir(parents=True,exist_ok=True);Path(output_path).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
 try: raw=r.json()
 except ValueError: raw={'non_json_sha256':sha(r.content)}
 content=raw.get('choices',[{}])[0].get('message',{}).get('content') if isinstance(raw,dict) else None;parsed=None;err=None
 if r.status_code!=200: err=f'fal HTTP {r.status_code}'
 if err is None:
  try: parsed=parse(content)
  except (ValueError,json.JSONDecodeError) as e: err=str(e)
 result={'schema':'fal-op011-structured-review-v1','opening_id':'OP011','endpoint':ENDPOINT,'model':body['model'],'routing_control':'fal_managed_no_per_request_provider_selection','request_contract_sha256':request_hash,'payload_schema_sha256':sha(json.dumps(body['response_format'],sort_keys=True,separators=(',',':')).encode()),'prompt_sha256':sha(prompt().encode()),'system_sha256':sha(body['messages'][0]['content'].encode()),'evidence_file_sha256':sha(Path(evidence_path).read_bytes()),'evidence_candidate_hash':ev['candidate_hash'],'image_bindings':bindings,'http_status':r.status_code,'parsed':parsed,'validation_error':err,'usable_advisory':parsed is not None and err is None,'usage':raw.get('usage') if isinstance(raw,dict) else None,'raw_response':raw,'raw_response_sha256':sha(json.dumps(raw,sort_keys=True,separators=(',',':')).encode()),'subtype_confirmation':False,'traversability_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False};Path(output_path).parent.mkdir(parents=True,exist_ok=True);Path(output_path).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--config',required=True,type=Path);p.add_argument('--evidence',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--model',default=MODEL);a=p.parse_args(argv);r=execute(a.config,a.evidence,a.output,a.model);print(json.dumps({'opening_id':r['opening_id'],'model':r['model'],'usable_advisory':r['usable_advisory']}));return 0 if r['usable_advisory'] else 2
if __name__=='__main__':raise SystemExit(main())
