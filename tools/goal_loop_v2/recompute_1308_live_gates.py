"""Recompute current 1308 source gates and bind all fail-closed opening packets."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))
from tools.goal_loop_v2.demoted_portal_deny import build_demoted_portal_deny
from tools.goal_loop_v2.opening_side_candidates import build_opening_side_space_candidate
from tools.goal_loop_v2.op001_adjudication import build_op001_adjudication
from tools.goal_loop_v2.op002_adjudication_packet_v3 import build_op002_adjudication_packet_v3
from tools.goal_loop_v2.op003_adjudication_candidate import build_op003_geometry_adjudication_candidate
from tools.goal_loop_v2.op004_adjudication_candidate import build_op004_geometry_adjudication_candidate
from tools.goal_loop_v2.op005_006_adjudication import build_op005_006_adjudication
from tools.goal_loop_v2.op007_008_adjudication import build_op007_008_adjudication
from tools.goal_loop_v2.op009_010_adjudication import build_op009_010_adjudication
from tools.goal_loop_v2.op011_adjudication import build_op011_adjudication
from tools.goal_loop_v2.source_contract_report import _score_source_contract
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids

REF=REPO_ROOT/'data/goal_loop_v2/references/1308';REPORTS=REPO_ROOT/'reports'
DEFAULT_OP002=Path(r'C:/Users/1_1/Desktop/goal_loop_v2_1308_op002_single_gemini_20260902_7897/result.json')
def _entry(ids,packet):
    return {'opening_ids':ids,'candidate_hash':packet['candidate_hash'],'semantic_promotion':packet.get('semantic_promotion',False),'score_effect':packet.get('score_effect','none'),'build_authorized':packet.get('build_authorized',False),'ready':packet.get('ready',False)}
def recompute(op002_gemini_result:Path):
    source_path=REF/'reference-coordinate-authorized-v21.json';doc=json.loads(source_path.read_text(encoding='utf-8'));contract=json.loads((REPO_ROOT/'docs/goal_loop_v2/goal-contract.json').read_text(encoding='utf-8'));wall_fact=json.loads((REF/'wall-2d-geometry-fact-authorized-v1.json').read_text(encoding='utf-8'));side=build_opening_side_space_candidate(doc);wall=build_target_aware_wall_solids(doc)
    report,detail=_score_source_contract(doc,contract,{'ready':False},{'lineage_type':'live_gate_recompute_20260902'},wall_fact)
    p1=build_op001_adjudication(doc,REPORTS/'op001_entrance_evidence_20260901/op001-evidence.json',side,wall)
    p2=build_op002_adjudication_packet_v3(doc,REPORTS/'op002_vertical_evidence_20260901/op002-vertical-evidence.json',op002_gemini_result)
    p3=build_op003_geometry_adjudication_candidate(doc,REPORTS/'op003_op004_geometry_evidence_20260901/op003-op004-evidence.json',side,wall)
    p4=build_op004_geometry_adjudication_candidate(doc,REPORTS/'op003_op004_geometry_evidence_20260901/op003-op004-evidence.json',side,wall)
    p56=build_op005_006_adjudication(doc,REPORTS/'op005_op006_geometry_evidence_20260901/op005-op006-evidence.json',side,wall)
    p78=build_op007_008_adjudication(doc,REPORTS/'op007_op008_geometry_evidence_20260902/op007-op008-evidence.json',side,wall)
    p910=build_op009_010_adjudication(doc,REPORTS/'op009_op010_geometry_evidence_20260901/op009-op010-evidence.json',side,wall)
    p11=build_op011_adjudication(doc,REPORTS/'op011_geometry_evidence_20260902/op011-evidence.json',side,wall)
    deny=build_demoted_portal_deny(doc,source_path)
    packets=[_entry(['OP001'],p1),_entry(['OP002'],p2),_entry(['OP003'],p3),_entry(['OP004'],p4),_entry(['OP005','OP006'],p56),_entry(['OP007','OP008'],p78),_entry(['OP009','OP010'],p910),_entry(['OP011'],p11)]
    covered=sorted(x for row in packets for x in row['opening_ids'])
    result={'schema':'goal-loop-v2-1308-live-gate-recompute-v1','source_structure_hash':doc['structure_hash'],'source_score':detail['weighted_score'],'hard_failures':detail['hard_failures'],'blocker_counts':{key:len(value) for key,value in detail['entity_blockers'].items()},'opening_packets':packets,'covered_opening_ids':covered,'all_source_opening_ids':sorted(x['id'] for x in doc['opening_contract']['openings'] if x['id'].startswith('OP')),'demoted_portal_deny_hash':deny['candidate_hash'],'demoted_portal_score_effect':deny['deny_record']['score_effect'],'all_packets_fail_closed':all(not row['semantic_promotion'] and row['score_effect']=='none' and not row['build_authorized'] and not row['ready'] for row in packets),'build_authorized':False,'ready':False,'source_report':report,'source_detail':detail}
    if covered!=result['all_source_opening_ids']:raise ValueError('live gate opening coverage drift')
    if result['source_score']!=65 or result['hard_failures']!=['S06_OPENINGS','S07_SPACES_ADJACENCY_REACHABILITY','S08_PROVENANCE_UNRESOLVED']:raise ValueError('live gate baseline drift')
    if not result['all_packets_fail_closed']:raise ValueError('live gate packet promotion detected')
    return result
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--op002-gemini-result',type=Path,default=DEFAULT_OP002);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args(argv);result=recompute(a.op002_gemini_result);a.output_dir.mkdir(parents=True,exist_ok=True);(a.output_dir/'live-gate-recompute.json').write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8');summary=f"# 1308 live gate recompute\n\n- Source score: **{result['source_score']}/100**\n- Hard failures: `{', '.join(result['hard_failures'])}`\n- Blocker counts: `{json.dumps(result['blocker_counts'],sort_keys=True)}`\n- OP001–OP011 packet coverage: complete; all fail closed.\n- Demoted portal deny: `{result['demoted_portal_deny_hash']}`; score effect `none`.\n- Blender/IFC build authorized: **false**.\n";(a.output_dir/'REPORT.md').write_text(summary,encoding='utf-8');print(json.dumps({'score':result['source_score'],'hard_failures':result['hard_failures'],'blocker_counts':result['blocker_counts'],'all_packets_fail_closed':result['all_packets_fail_closed'],'output':str(a.output_dir)},sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
