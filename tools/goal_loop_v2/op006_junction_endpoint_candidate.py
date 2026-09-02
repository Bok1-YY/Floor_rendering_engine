"""Source-bound OP006 same-wall jamb and J-007 endpoint-ownership candidate."""
from __future__ import annotations
from copy import deepcopy
import hashlib,json,math,sys
from pathlib import Path
from typing import Any,Mapping

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.jamb_policy import minimum_jamb_support_m
from tools.goal_loop_v2.opening_side_candidates import build_opening_side_space_candidate
from tools.goal_loop_v2.op005_006_adjudication import build_op005_006_adjudication
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids

SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';EVIDENCE=ROOT/'reports/op005_op006_geometry_evidence_20260901/op005-op006-evidence.json';PAIR=ROOT/'reports/partition_resolved_cut_pair_20260902/partition-resolved-cut-pair.json';TARGETED=ROOT/'reports/partition_targeted_adjudication_20260902/partition-targeted-adjudication.json';REVIEW=ROOT/'reports/partition_targeted_review_bundle_20260902/partition-targeted-review-bundle.json';TYPE_BUNDLE=ROOT/'reports/fal_openrouter_review_bundle_20260902/fal-review-bundle.json';PAIR_BUNDLE=ROOT/'reports/fal_room_pair_trial_20260902/fal-room-pair-bundle.json'
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def _file_hash(p)->str:return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def build_op006_junction_endpoint_candidate(document:Mapping[str,Any],*,_skip_validate=False):
 doc=validate_v21_document(document);side=build_opening_side_space_candidate(doc);wall=build_target_aware_wall_solids(doc);base_packet=build_op005_006_adjudication(doc,EVIDENCE,side,wall);base_row=next(x for x in base_packet['openings'] if x['opening_id']=='OP006');target=json.loads(TARGETED.read_text());target_row=next(x for x in target['openings'] if x['opening_id']=='OP006');pair=json.loads(PAIR.read_text());pair_row=next(x for x in pair['openings'] if x['opening_id']=='OP006');review=json.loads(REVIEW.read_text());failed=next(x for x in review['failed_reviews'] if x['opening_id']=='OP006');types=json.loads(TYPE_BUNDLE.read_text());type_row=next(x for x in types['reviews'] if x['opening_id']=='OP006');pairs=json.loads(PAIR_BUNDLE.read_text());pair_review=next(x for x in pairs['reviews'] if x['opening_id']=='OP006');host=next(x for x in doc['wall_graph']['atoms'] if x['id']=='ATOM-WB007-02');junction=next(x for x in doc['wall_graph']['junctions'] if x['id']=='J-007');segment=base_row['source_segment_m'];upper=segment[0];lower=segment[1];upper_distance=math.dist(upper,junction['axis_point_m']);lower_support=target_row['jamb_support']['jamb_after_m'];minimum=minimum_jamb_support_m(doc)
 incident=[]
 for source_incident in junction['incidents']:
  atom_id=source_incident['atom_id'];atom=next(x for x in doc['wall_graph']['atoms'] if x['id']==atom_id);endpoint=source_incident['end'];node_key='start_node_id' if endpoint=='start' else 'end_node_id'
  if atom[node_key]!=junction['id'] or math.dist(source_incident['contact_point_m'],junction['axis_point_m'])>1e-9:raise ValueError('J-007 incident atom/contact does not bind junction')
  incident.append({'atom_id':atom_id,'endpoint':endpoint,'branch_id':atom['branch_id'],'thickness_m':atom['thickness_m'],'attachment':source_incident['attachment'],'role':source_incident['role'],'contact_point_m':deepcopy(source_incident['contact_point_m'])})
 result={'schema':'op006-junction-endpoint-candidate-v1','source_structure_hash':doc['structure_hash'],'opening_id':'OP006','base_packet_hash':base_packet['candidate_hash'],'host_atom_id':host['id'],'host_start_node_id':host['start_node_id'],'registered_segment_m':deepcopy(segment),'directed_side_assignment':deepcopy(pair_row['directed_side_assignment']),'same_wall_support':{'upper_to_junction_m':round(upper_distance,9),'lower_to_host_end_m':round(lower_support,9),'minimum_support_m':round(min(upper_distance,lower_support),9),'governing_minimum_m':minimum,'sufficient':min(upper_distance,lower_support)>=minimum,'mode':'same_wall_solid_to_confirmed_junction','crossing_wall_jamb_claim':False},'junction_context':{'junction_id':junction['id'],'kind':junction['kind'],'status':junction['status'],'axis_point_m':deepcopy(junction['axis_point_m']),'solid_union_policy':junction['solid_union_policy'],'incident_atoms':incident},'advisory_evidence':{'type_review':deepcopy(type_row['parsed']),'room_pair_review':deepcopy(pair_review['parsed']),'targeted_review_status':'bare_json_protocol_failed','targeted_result_file_sha256':failed['result_file_sha256'],'targeted_result_canonical_sha256':failed['result_canonical_sha256'],'targeted_parsed':None},'evidence_chain':{'source_document_sha256':_file_hash(SOURCE),'registered_evidence_sha256':_file_hash(EVIDENCE),'target_aware_wall_hash':wall['candidate_hash'],'pair_candidate_hash':pair['candidate_hash'],'targeted_evidence_hash':target['candidate_hash'],'targeted_review_bundle_hash':review['candidate_hash'],'type_bundle_hash':types['candidate_hash'],'room_pair_bundle_hash':pairs['candidate_hash']},'remaining_blockers':['SOURCE_HOST_EFFECTIVE_VOID_APPLICATION_PENDING','TARGETED_REVIEW_PROTOCOL_FAILED','VERTICAL_POLICY_PENDING','TRAVERSABILITY_PENDING','ADJACENCY_PENDING','HUMAN_ACCEPTANCE_PENDING'],'endpoint_ownership_confirmation':False,'effective_void_confirmation':False,'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
 return result if _skip_validate else validate_op006_junction_endpoint_candidate(doc,result)
def validate_op006_junction_endpoint_candidate(document,candidate):
 doc=validate_v21_document(document)
 if candidate.get('schema')!='op006-junction-endpoint-candidate-v1' or candidate.get('opening_id')!='OP006':raise ValueError('OP006 junction candidate schema/identity drift')
 for key in ('endpoint_ownership_confirmation','effective_void_confirmation','pair_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'):
  if candidate.get(key) is not False:raise ValueError('OP006 junction candidate was promoted')
 if (candidate.get('same_wall_support') or {}).get('crossing_wall_jamb_claim') is not False:raise ValueError('OP006 crossing jamb was fabricated')
 if candidate!=build_op006_junction_endpoint_candidate(doc,_skip_validate=True):raise ValueError('OP006 junction/source evidence drift')
 return deepcopy(dict(candidate))
def main():
 doc=json.loads(SOURCE.read_text());result=build_op006_junction_endpoint_candidate(doc);out=ROOT/'reports/op006_junction_endpoint_candidate_20260902';out.mkdir(parents=True,exist_ok=True);(out/'op006-junction-endpoint-candidate.json').write_text(json.dumps(result,indent=2)+'\n');(out/'REPORT.md').write_text('# OP006 J-007 endpoint candidate\n\nOP006 has 94.538 mm same-wall support to confirmed J-007, above the source 50 mm minimum. J-007/X incident atoms provide endpoint-ownership context; this is not a crossing-wall-jamb claim. Targeted fal review failed the bare-JSON protocol, so no source correction, pair, adjacency or build is confirmed.\n');print(result['candidate_hash'])
if __name__=='__main__':main()
__all__=['build_op006_junction_endpoint_candidate','validate_op006_junction_endpoint_candidate']
