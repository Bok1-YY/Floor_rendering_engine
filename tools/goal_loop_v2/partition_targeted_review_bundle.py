"""Bind OP002/OP010 partition-targeted fal review outcomes."""
from __future__ import annotations
from copy import deepcopy
import hashlib,json
from pathlib import Path
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.fal_targeted_cut_review import parse
def _hash(v):return hashlib.sha256(canonical_json(v)).hexdigest()
def build_partition_targeted_review_bundle(document,manifest_path:Path,result_dir:Path,*,_skip_validate=False):
 doc=validate_v21_document(document);manifest_bytes=manifest_path.read_bytes();manifest_hash=hashlib.sha256(manifest_bytes).hexdigest();manifest=json.loads(manifest_bytes.decode());rows=[]
 for oid in ('OP002',):
  geometry=next(x for x in manifest['openings'] if x['opening_id']==oid);p=result_dir/oid/'result.json';raw=p.read_bytes();result=json.loads(raw.decode());usable=result.get('geometry_advisory_complete') is True;parsed=None
  if usable:parsed=parse(json.dumps(result['parsed'],separators=(',',':')),oid)
  expected=[{'filename':Path(geometry['artifact_bindings'][role]['path']).name,'bytes':geometry['artifact_bindings'][role]['bytes'],'sha256':geometry['artifact_bindings'][role]['sha256']} for role in ('full','crop')]
  if result.get('manifest_file_sha256')!=manifest_hash or result.get('image_bindings')!=expected:raise ValueError(f'{oid} partition targeted image drift')
  agree=bool(parsed and parsed['segment_on_visible_opening']=='yes' and parsed['host_alignment']=='agree' and parsed['opposite_face_geometry']=='agree')
  rows.append({'opening_id':oid,'result_file_sha256':hashlib.sha256(raw).hexdigest(),'result_canonical_sha256':_hash(result),'usable_advisory':usable,'parsed':deepcopy(parsed),'visual_geometry_agreement':agree,'directed_side_assignment':deepcopy(geometry['directed_side_assignment']),'jamb_support':deepcopy(geometry['jamb_support']),'image_bindings':expected,'cost_usd':float((result.get('usage') or {}).get('cost') or 0),'decision':'correction_review_candidate' if agree else 'review_protocol_failed','cut_confirmation':False,'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False})
 result={'schema':'partition-targeted-review-bundle-v2','source_structure_hash':doc['structure_hash'],'evidence_file_sha256':manifest_hash,'evidence_candidate_hash':manifest['candidate_hash'],'covered_opening_ids':['OP002'],'excluded_opening_reviews':[{'opening_id':'OP010','reason':'prior flash and pro reviews violated bare-JSON protocol; unchanged visual method not repeated after policy-only migration'}],'reviews':rows,'usable_count':sum(r['usable_advisory'] for r in rows),'total_cost_usd':round(sum(r['cost_usd'] for r in rows),10),'correction_review_candidate_ids':[r['opening_id'] for r in rows if r['visual_geometry_agreement']],'cut_confirmation':False,'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
 return result if _skip_validate else validate_partition_targeted_review_bundle(doc,result)
def validate_partition_targeted_review_bundle(document,candidate):
 doc=validate_v21_document(document)
 if candidate.get('schema')!='partition-targeted-review-bundle-v2' or candidate.get('source_structure_hash')!=doc['structure_hash'] or candidate.get('covered_opening_ids')!=['OP002']:raise ValueError('partition targeted bundle source/coverage drift')
 for key in ('cut_confirmation','pair_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'):
  if candidate.get(key) is not False:raise ValueError('partition targeted bundle was promoted')
 if candidate.get('candidate_hash')!=_hash({k:v for k,v in candidate.items() if k!='candidate_hash'}):raise ValueError('partition targeted bundle hash drift')
 return deepcopy(dict(candidate))
__all__=['build_partition_targeted_review_bundle','validate_partition_targeted_review_bundle']
