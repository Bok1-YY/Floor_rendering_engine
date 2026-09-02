"""Research-only unit-scope reachability graph with confidence tiers."""
from __future__ import annotations
from copy import deepcopy
import hashlib,json
from pathlib import Path
import sys
from typing import Any,Mapping
from shapely.geometry import Polygon
from shapely.ops import unary_union
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document

SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';UNIT=ROOT/'reports/op001_unit_scope_candidate_20260902/op001-unit-scope-candidate.json';PART=ROOT/'reports/semantic_public_partition_20260902/semantic-public-partition.json';PAIR=ROOT/'reports/partition_resolved_cut_pair_20260902/partition-resolved-cut-pair.json';REGISTRY=ROOT/'reports/correction_candidate_registry_20260902/correction-candidate-registry.json';OP6=ROOT/'reports/op006_junction_endpoint_candidate_20260902/op006-junction-endpoint-candidate.json'
PUBLIC={'bedroom_corridor','kitchen','living_hall','lobby'};EXCLUDED={'lift_shaft'}
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def _file_hash(p)->str:return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def _geom(cell):return unary_union([Polygon(x['exterior'],x.get('holes',[])) for x in cell['polygons']])
def _reach(root,edges):
 graph={root:set()}
 for edge in edges:
  a,b=edge['space_a_id'],edge['space_b_id'];graph.setdefault(a,set()).add(b);graph.setdefault(b,set()).add(a)
 seen={root};queue=[root]
 while queue:
  current=queue.pop(0)
  for neighbor in graph.get(current,set()):
   if neighbor not in seen:seen.add(neighbor);queue.append(neighbor)
 return sorted(seen)
def build_unit_scope_reachability_candidate(document:Mapping[str,Any],*,_skip_validate=False):
 doc=validate_v21_document(document);unit=json.loads(UNIT.read_text());partition=json.loads(PART.read_text());pairs=json.loads(PAIR.read_text());registry=json.loads(REGISTRY.read_text());op6=json.loads(OP6.read_text());cells={x['space_id']:_geom(x) for x in partition['cells']};semantic_edges=[]
 for i,a in enumerate(sorted(PUBLIC)):
  for b in sorted(PUBLIC)[i+1:]:
   length=cells[a].boundary.intersection(cells[b].boundary).length
   if length>1e-6:semantic_edges.append({'id':f'VEIRTUAL-{a}-{b}','space_a_id':a,'space_b_id':b,'kind':'semantic_open_plan_boundary','shared_boundary_m':round(length,9),'confirmation':False})
 tier_a=[{'id':'UNIT-ROOT-OP001','space_a_id':'common_core_circulation','space_b_id':'lobby','kind':'unit_scope_entrance_hypothesis','opening_id':'OP001','confirmation':False}]
 for edge in semantic_edges:tier_a.append(edge)
 for row in registry['candidates']:tier_a.append({'id':f"REGISTRY-{row['opening_id']}",'space_a_id':row['directed_side_assignment']['side_a'],'space_b_id':row['directed_side_assignment']['side_b'],'kind':'verified_2d_correction_candidate','opening_id':row['opening_id'],'confirmation':False})
 pair_by={x['opening_id']:x for x in pairs['openings']};tier_b=deepcopy(tier_a)
 for oid in ('OP006','OP007','OP010'):
  row=pair_by[oid];assignment=row['directed_side_assignment'];tier_b.append({'id':f'PROTOCOL-FAILED-{oid}','space_a_id':assignment['side_a'],'space_b_id':assignment['side_b'],'kind':'geometry_candidate_targeted_review_protocol_failed','opening_id':oid,'confirmation':False})
 root='common_core_circulation';scope_spaces=sorted(x['id'] for x in doc['spaces'] if x['id'] not in EXCLUDED);reach_a=_reach(root,tier_a);reach_b=_reach(root,tier_b);result={'schema':'unit-scope-reachability-candidate-v1','source_structure_hash':doc['structure_hash'],'root_hypothesis':{'space_id':root,'scope':'external_to_private_unit_candidate','building_exterior_root_confirmation':False,'unit_root_confirmation':False},'scope_space_ids':scope_spaces,'excluded_nontraversable_space_ids':sorted(EXCLUDED),'semantic_open_plan_edges':semantic_edges,'tiers':[{'tier':'A','description':'unit-root + correction registry + semantic open-plan cells','edges':tier_a,'reachable_space_ids':reach_a,'unreachable_scope_space_ids':sorted(set(scope_spaces)-set(reach_a))},{'tier':'B','description':'Tier A plus OP006/OP007/OP010 targeted-review-protocol-failed geometry candidates','edges':tier_b,'reachable_space_ids':reach_b,'unreachable_scope_space_ids':sorted(set(scope_spaces)-set(reach_b))}],'evidence_chain':{'source_document_sha256':_file_hash(SOURCE),'unit_scope_candidate_hash':unit['candidate_hash'],'semantic_partition_hash':partition['candidate_hash'],'pair_candidate_hash':pairs['candidate_hash'],'correction_registry_hash':registry['candidate_hash'],'op006_endpoint_candidate_hash':op6['candidate_hash']},'root_confirmation':False,'reachability_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
 return result if _skip_validate else validate_unit_scope_reachability_candidate(doc,result)
def validate_unit_scope_reachability_candidate(document,candidate):
 doc=validate_v21_document(document)
 if candidate.get('schema')!='unit-scope-reachability-candidate-v1' or candidate.get('source_structure_hash')!=doc['structure_hash']:raise ValueError('unit reachability source/schema drift')
 for key in ('root_confirmation','reachability_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'):
  if candidate.get(key) is not False:raise ValueError('unit reachability candidate was promoted')
 if any(edge.get('confirmation') is not False for tier in candidate.get('tiers',[]) for edge in tier.get('edges',[])):raise ValueError('unit reachability edge was promoted')
 if candidate!=build_unit_scope_reachability_candidate(doc,_skip_validate=True):raise ValueError('unit reachability evidence/graph drift')
 return deepcopy(dict(candidate))
def main():
 doc=json.loads(SOURCE.read_text());result=build_unit_scope_reachability_candidate(doc);out=ROOT/'reports/unit_scope_reachability_candidate_20260902';out.mkdir(parents=True,exist_ok=True);(out/'unit-scope-reachability-candidate.json').write_text(json.dumps(result,indent=2)+'\n');a,b=result['tiers'];(out/'REPORT.md').write_text(f"# Unit-scope reachability candidate\n\nTier A unreachable: {', '.join(a['unreachable_scope_space_ids'])}.\n\nTier B unreachable: {', '.join(b['unreachable_scope_space_ids'])}.\n\nAll roots/edges/reachability remain unconfirmed; source score/build are unchanged.\n");print(result['candidate_hash'])
if __name__=='__main__':main()
__all__=['build_unit_scope_reachability_candidate','validate_unit_scope_reachability_candidate']
