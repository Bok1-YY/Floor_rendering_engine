"""Extend verified unit reachability v1 with OP003 return-wall Tier C."""
from __future__ import annotations
from copy import deepcopy
import hashlib,json,sys
from pathlib import Path
from typing import Any,Mapping
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.unit_scope_reachability_candidate import build_unit_scope_reachability_candidate,validate_unit_scope_reachability_candidate,_reach

SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';BASE=ROOT/'reports/unit_scope_reachability_candidate_20260902/unit-scope-reachability-candidate.json';OP3=ROOT/'reports/op003_return_wall_candidate_20260902/op003-return-wall-candidate.json'
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def _file_hash(p)->str:return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def build_unit_scope_reachability_v2(document:Mapping[str,Any],*,_skip_validate=False):
 doc=validate_v21_document(document);base=json.loads(BASE.read_text());validate_unit_scope_reachability_candidate(doc,base);op3=json.loads(OP3.read_text());tier_c_edges=deepcopy(base['tiers'][-1]['edges']);tier_c_edges.append({'id':'RETURN-PROTOCOL-FAILED-OP003','space_a_id':op3['directed_side_assignment']['side_a'],'space_b_id':op3['directed_side_assignment']['side_b'],'kind':'return_wall_candidate_targeted_review_protocol_failed','opening_id':'OP003','confirmation':False});reachable=_reach(base['root_hypothesis']['space_id'],tier_c_edges);scope=base['scope_space_ids'];result={'schema':'unit-scope-reachability-candidate-v2','source_structure_hash':doc['structure_hash'],'base_candidate_hash':base['candidate_hash'],'base_file_sha256':_file_hash(BASE),'root_hypothesis':deepcopy(base['root_hypothesis']),'scope_space_ids':deepcopy(scope),'excluded_nontraversable_space_ids':deepcopy(base['excluded_nontraversable_space_ids']),'tiers':deepcopy(base['tiers'])+[{'tier':'C','description':'Tier B plus OP003 return-wall targeted-review-protocol-failed candidate','edges':tier_c_edges,'reachable_space_ids':reachable,'unreachable_scope_space_ids':sorted(set(scope)-set(reachable))}],'evidence_chain':{'op003_return_candidate_hash':op3['candidate_hash'],'op003_return_file_sha256':_file_hash(OP3)},'root_confirmation':False,'reachability_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
 return result if _skip_validate else validate_unit_scope_reachability_v2(doc,result)
def validate_unit_scope_reachability_v2(document,candidate):
 doc=validate_v21_document(document)
 if candidate.get('schema')!='unit-scope-reachability-candidate-v2' or candidate.get('source_structure_hash')!=doc['structure_hash'] or [x.get('tier') for x in candidate.get('tiers',[])]!=['A','B','C']:raise ValueError('unit reachability v2 schema/tier drift')
 for key in ('root_confirmation','reachability_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'):
  if candidate.get(key) is not False:raise ValueError('unit reachability v2 was promoted')
 if any(edge.get('confirmation') is not False for tier in candidate['tiers'] for edge in tier['edges']):raise ValueError('unit reachability v2 edge was promoted')
 if candidate!=build_unit_scope_reachability_v2(doc,_skip_validate=True):raise ValueError('unit reachability v2 evidence/graph drift')
 return deepcopy(dict(candidate))
def main():
 doc=json.loads(SOURCE.read_text());result=build_unit_scope_reachability_v2(doc);out=ROOT/'reports/unit_scope_reachability_v2_20260902';out.mkdir(parents=True,exist_ok=True);(out/'unit-scope-reachability-v2.json').write_text(json.dumps(result,indent=2)+'\n');(out/'REPORT.md').write_text(f"# Unit-scope reachability v2\n\nTier C adds the unconfirmed OP003 return-wall edge. Remaining islands: {', '.join(result['tiers'][-1]['unreachable_scope_space_ids'])}. All roots/edges/reachability remain unconfirmed.\n");print(result['candidate_hash'])
if __name__=='__main__':main()
__all__=['build_unit_scope_reachability_v2','validate_unit_scope_reachability_v2']
