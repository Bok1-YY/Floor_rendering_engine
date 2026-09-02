"""Strict OP011 review client for uncontaminated evidence."""
import argparse,base64,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.goal_loop_v2.fal_op011_glazed_review import MODEL,parse
ENDPOINT='https://fal.run/openrouter/router/openai/v1/chat/completions'
def sha(b):return hashlib.sha256(b).hexdigest()
def review_prompt(local):
 return 'You receive two images. Image 1 is a full-plan locator whose rectangle stays outside the target. Image 2 is a byte-exact, unmodified crop of the canonical source. In Image 2, review only the source-native black/gray pixels along the vertical OP011 segment at local crop coordinates '+json.dumps(local)+'. Ignore the locator rectangle, room names, dimensions, hatch patterns and inferred room function. A sliding track is visible only if source-native linework shows a distinct track or paired guide connected to a door panel; a wall break is visible only if the wall boundary has a material gap with endpoints. Do not infer rooms, dimensions, adjacency, source correction or construction. Return one minified JSON object only: {"opening_id":"OP011","visible_wall_break":"yes|no|unclear","swing_arc_visible":"yes|no|unclear","sliding_track_visible":"yes|no|unclear","subtype":"hinged_access_door|sliding_access_door|window|fixed_glazing|wall_gap|unknown|null","traversable_visual_cue":"yes|no|unclear","confidence":"high|medium|low|null"}'
def execute(config_path,evidence_path,output_path,model=MODEL):
 import requests
 cfg=json.loads(Path(config_path).read_text());ev=json.loads(Path(evidence_path).read_text());
 if ev.get('source_pixels_untouched') is not True:raise ValueError('uncontaminated evidence gate failed')
 images=[];bindings=[]
 for role in ('locator','raw_crop'):
  a=ev['artifacts'][role];p=Path(a['path']);raw=p.read_bytes();digest=sha(raw)
  if digest!=a['sha256']:raise ValueError('OP011 uncontaminated artifact drift')
  images.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(raw).decode()}});bindings.append({'role':role,'filename':p.name,'bytes':len(raw),'sha256':digest})
 local=[[round(p[0]-ev['crop_box_px'][0],6),round(p[1]-ev['crop_box_px'][1],6)] for p in ev['registered_segment_px']]
 text=review_prompt(local)
 schema={'type':'object','additionalProperties':False,'properties':{'opening_id':{'type':'string','const':'OP011'},'visible_wall_break':{'type':'string','enum':['yes','no','unclear']},'swing_arc_visible':{'type':'string','enum':['yes','no','unclear']},'sliding_track_visible':{'type':'string','enum':['yes','no','unclear']},'subtype':{'type':['string','null'],'enum':['hinged_access_door','sliding_access_door','window','fixed_glazing','wall_gap','unknown',None]},'traversable_visual_cue':{'type':'string','enum':['yes','no','unclear']},'confidence':{'type':['string','null'],'enum':['high','medium','low',None]}},'required':['opening_id','visible_wall_break','swing_arc_visible','sliding_track_visible','subtype','traversable_visual_cue','confidence']}
 body={'model':str(model or MODEL).strip() or MODEL,'messages':[{'role':'system','content':'Return only the requested JSON object.'},{'role':'user','content':[{'type':'text','text':text},*images]}],'response_format':{'type':'json_schema','json_schema':{'name':'op011_uncontaminated_review','strict':True,'schema':schema}},'temperature':0,'max_tokens':256}
 contract={'model':body['model'],'image_order':['locator','raw_crop'],'local_target_coordinates':local,'messages':[{'role':m['role'],'text':m['content'] if isinstance(m['content'],str) else [{'type':z['type'],'sha256':bindings[i]['sha256']} for i,z in enumerate(m['content'][1:])]} for m in body['messages']],'response_format':body['response_format'],'temperature':0,'max_tokens':256};reqhash=sha(json.dumps(contract,sort_keys=True,separators=(',',':')).encode());kwargs={'headers':{'Authorization':f"Key {cfg.get('fal_api_key','')}"},'timeout':180,'verify':bool(cfg.get('tls_verify',True))};proxy=str(cfg.get('fal_queue_proxy') or cfg.get('proxy') or '').strip();
 if proxy:kwargs['proxies']={'http':proxy,'https':proxy}
 transport=None
 try:r=requests.post(ENDPOINT,json=body,**kwargs)
 except (requests.ConnectionError,requests.Timeout) as exc:transport=f'{type(exc).__name__}: {exc}';r=None
 raw=None;err=transport;parsed=None
 if r is not None:
  try:raw=r.json()
  except ValueError:raw={'non_json_sha256':sha(r.content)}
  if r.status_code!=200:err=f'fal HTTP {r.status_code}'
  elif isinstance(raw,dict):
   try:parsed=parse(raw.get('choices',[{}])[0].get('message',{}).get('content'))
   except (ValueError,json.JSONDecodeError) as exc:err=str(exc)
 result={'schema':'fal-op011-uncontaminated-review-v1','opening_id':'OP011','endpoint':ENDPOINT,'model':body['model'],'request_contract_sha256':reqhash,'local_target_coordinates':local,'evidence_file_sha256':sha(Path(evidence_path).read_bytes()),'evidence_candidate_hash':ev['candidate_hash'],'image_bindings':bindings,'http_status':None if r is None else r.status_code,'parsed':parsed,'validation_error':err,'transport_error':transport,'usable_advisory':parsed is not None and err is None,'raw_response':raw,'raw_response_sha256':None if raw is None else sha(json.dumps(raw,sort_keys=True,separators=(',',':')).encode()),'subtype_confirmation':False,'traversability_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False};Path(output_path).parent.mkdir(parents=True,exist_ok=True);Path(output_path).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--config',required=True,type=Path);p.add_argument('--evidence',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--model',default=MODEL);a=p.parse_args(argv);r=execute(a.config,a.evidence,a.output,a.model);print(json.dumps({'opening_id':r['opening_id'],'model':r['model'],'usable_advisory':r['usable_advisory']}));return 0 if r['usable_advisory'] else 2
if __name__=='__main__':raise SystemExit(main())
