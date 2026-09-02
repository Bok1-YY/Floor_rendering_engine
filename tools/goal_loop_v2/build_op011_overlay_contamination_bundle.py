"""Canonical, fail-closed OP011 provider disagreement bundle."""
from pathlib import Path
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.fastloop_research.contract import canonical_json
HOST=ROOT/'reports/op011_host_scope_candidate_20260902/op011-host-scope-candidate.json'; CLEAN=ROOT/'reports/op011_uncontaminated_evidence_20260902/op011-uncontaminated-evidence.json'; OUT=ROOT/'reports/op011_overlay_contamination_20260902'; ROLES=('contaminated_gemini','contaminated_openai','clean_gemini','clean_openai')
def hs(b):return hashlib.sha256(b).hexdigest()
def canon(v):return hs(canonical_json(v))
def _parse(x):
 if x.get('usable_advisory') is not True or x.get('http_status') != 200: raise ValueError('result not usable')
 p=x.get('parsed');
 if not isinstance(p,dict) or set(p)!= {'opening_id','visible_wall_break','swing_arc_visible','sliding_track_visible','subtype','traversable_visual_cue','confidence'} or p.get('opening_id')!='OP011':raise ValueError('parsed schema drift')
 if any(p[k] not in {'yes','no','unclear'} for k in ('visible_wall_break','swing_arc_visible','sliding_track_visible','traversable_visual_cue')) or p['subtype'] not in {'hinged_access_door','sliding_access_door','window','fixed_glazing','wall_gap','unknown',None}:raise ValueError('parsed enum drift')
 raw=x.get('raw_response');
 if not isinstance(raw,dict) or hs(canonical_json(raw))!=x.get('raw_response_sha256'):raise ValueError('raw response hash drift')
 if json.loads(raw['choices'][0]['message']['content'])!=p:raise ValueError('raw response content mismatch')
 return p
def build(input_paths):
 host=json.loads(HOST.read_text());clean=json.loads(CLEAN.read_text());rows=[]
 for role,path in zip(ROLES,input_paths):
  x=json.loads(Path(path).read_text());p=_parse(x); expected=host if role.startswith('contaminated') else clean; exp={k:expected['artifact_bindings'][k] for k in ('full','crop')} if role.startswith('contaminated') else {'locator':clean['artifacts']['locator'],'raw_crop':clean['artifacts']['raw_crop']}
  if x.get('evidence_candidate_hash')!=expected.get('candidate_hash'):raise ValueError('evidence candidate hash mismatch')
  if x.get('image_bindings')!=[{'role':k,'filename':Path(v['path']).name,'bytes':v.get('bytes',Path(v['path']).stat().st_size),'sha256':v['sha256']} for k,v in exp.items()]:raise ValueError('image binding mismatch')
  rows.append({'role':role,'model':x['model'],'result_file_sha256':hs(Path(path).read_bytes()),'parsed':p,'raw_response_sha256':x['raw_response_sha256'],'evidence_candidate_hash':x['evidence_candidate_hash'],'image_bindings':x['image_bindings']})
 c0,c1,g,o=[r['parsed'] for r in rows]; consensus=c0==c1; transitions={k:[c0[k],c1[k]] for k in ('sliding_track_visible','subtype','traversable_visual_cue')}; disagreement=any(g[k]!=o[k] for k in ('visible_wall_break','subtype','traversable_visual_cue')); demonstrated=consensus and c0['sliding_track_visible']=='yes' and c0['subtype']=='sliding_access_door' and g['sliding_track_visible']=='no' and o['sliding_track_visible']=='no' and (g['subtype']!=c0['subtype'] or o['subtype']!=c0['subtype']) and rows[0]['image_bindings']!=rows[2]['image_bindings'] and disagreement
 return {'schema':'op011-overlay-contamination-bundle-v2','opening_id':'OP011','host_scope_candidate_hash':host['candidate_hash'],'uncontaminated_candidate_hash':clean['candidate_hash'],'inputs':rows,'contaminated_consensus':c0 if consensus else None,'model_transitions':transitions,'clean_gemini':{k:g[k] for k in ('visible_wall_break','subtype','traversable_visual_cue')},'clean_openai':{k:o[k] for k in ('visible_wall_break','subtype','traversable_visual_cue')},'clean_provider_disagreement':disagreement,'overlay_contamination_demonstrated':demonstrated,'decision':'unresolved_source_visual_ambiguity','subtype_confirmation':False,'traversability_confirmation':False,'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64}
def validate(candidate,input_paths):
 expected=build(input_paths); actual=dict(candidate); actual.pop('candidate_hash',None); expected.pop('candidate_hash',None)
 if actual!=expected:raise ValueError('bundle evidence drift')
 if candidate['candidate_hash']!=canon(expected):raise ValueError('bundle hash drift')
 return candidate
def main():
 paths=[Path(r'C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_op011_structured_gemini_20260902/result-v2.json'),Path(r'C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_op011_structured_openai_20260902/result-v2.json'),Path(r'C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_op011_clean_gemini_20260902/result.json'),Path(r'C:/Users/1_1/Desktop/goal_loop_v2_1308_fal_op011_clean_openai_20260902/result.json')];r=build(paths);r['candidate_hash']=canon({k:v for k,v in r.items() if k!='candidate_hash'});OUT.mkdir(parents=True,exist_ok=True);(OUT/'bundle.json').write_text(json.dumps(r,indent=2)+'\n');(OUT/'REPORT.md').write_text('# OP011 overlay contamination bundle v2\n\nAll conclusions derive from validated raw responses. Decision remains unresolved source visual ambiguity; all promotion flags are false.\n');print(r['candidate_hash'])
if __name__=='__main__':main()
