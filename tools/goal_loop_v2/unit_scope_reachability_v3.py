"""Extend unit reachability v2 with OP008 bath-to-lobby Tier D."""
from __future__ import annotations
from copy import deepcopy
import hashlib,json,sys
from pathlib import Path
from typing import Any,Mapping
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.unit_scope_reachability_candidate import _reach
from tools.goal_loop_v2.unit_scope_reachability_v2 import validate_unit_scope_reachability_v2
SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';BASE=ROOT/'reports/unit_scope_reachability_v2_20260902/unit-scope-reachability-v2.json';OP8=ROOT/'reports/op008_return_wall_candidate_20260902/op008-return-wall-candidate.json'
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def _file_hash(p)->str:return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def build_unit_scope_reachability_v3(document:Mapping[str,Any],*,_skip_validate=False):
 doc=validate_v21_document(document);base=json.loads(BASE.read_text());validate_unit_scope_reachability_v2(doc,base);op8=json.loads(OP8.read_text());edges=deepcopy(base['tiers'][-1]['edges']);edges.append({'id':'RETURN-CONFLICT-OP008','space_a_id':op8['directed_side_assignment']['side_a'],'space_b_id':op8['directed_side_assignment']['side_b'],'kind':'return_wall_candidate_room_pair_vlm_conflict','opening_id':'OP008','confirmation':False});reachable=_reach(base['root_hypothesis']['space_id'],edges);scope=base['scope_space_ids'];result={'schema':'unit-scope-reachability-candidate-v3','source_structure_hash':doc['structure_hash'],'base_v2_candidate_hash':base['candidate_hash'],'base_v2_file_sha256':_file_hash(BASE),'root_hypothesis':deepcopy(base['root_hypothesis']),'scope_space_ids':deepcopy(scope),'excluded_nontraversable_space_ids':deepcopy(base['excluded_nontraversable_space_ids']),'tiers':deepcopy(base['tiers'])+[{'tier':'D','description':'Tier C plus OP008 bath-to-lobby return-wall candidate with room-pair VLM conflict','edges':edges,'reachable_space_ids':reachable,'unreachable_scope_space_ids':sorted(set(scope)-set(reachable))}],'evidence_chain':{'op008_return_candidate_hash':op8['candidate_hash'],'op008_return_file_sha256':_file_hash(OP8)},'root_confirmation':False,'reachability_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
 return result if _skip_validate else validate_unit_scope_reachability_v3(doc,result)
def validate_unit_scope_reachability_v3(document,candidate):
 doc=validate_v21_document(document)
 if candidate.get('schema')!='unit-scope-reachability-candidate-v3' or candidate.get('source_structure_hash')!=doc['structure_hash'] or [x.get('tier') for x in candidate.get('tiers',[])]!=['A','B','C','D']:raise ValueError('unit reachability v3 schema/tier drift')
 for key in ('root_confirmation','reachability_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'):
  if candidate.get(key) is not False:raise ValueError('unit reachability v3 was promoted')
 if any(edge.get('confirmation') is not False for tier in candidate['tiers'] for edge in tier['edges']):raise ValueError('unit reachability v3 edge was promoted')
 if candidate!=build_unit_scope_reachability_v3(doc,_skip_validate=True):raise ValueError('unit reachability v3 evidence/graph drift')
 return deepcopy(dict(candidate))
def main():
 doc=json.loads(SOURCE.read_text());result=build_unit_scope_reachability_v3(doc);out=ROOT/'reports/unit_scope_reachability_v3_20260902';out.mkdir(parents=True,exist_ok=True);(out/'unit-scope-reachability-v3.json').write_text(json.dumps(result,indent=2)+'\n');(out/'REPORT.md').write_text(f"# Unit-scope reachability v3\n\nTier D adds the unconfirmed OP008 bath-to-lobby return candidate. Remaining islands: {', '.join(result['tiers'][-1]['unreachable_scope_space_ids'])}. All roots/edges/reachability remain unconfirmed.\n");print(result['candidate_hash'])
if __name__=='__main__':main()
__all__=['build_unit_scope_reachability_v3','validate_unit_scope_reachability_v3']
