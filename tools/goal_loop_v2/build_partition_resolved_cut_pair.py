"""Fail-closed partition-resolved cut/pair candidate (no promotion)."""
from copy import deepcopy
from pathlib import Path
import json,hashlib,sys
REPO_ROOT=Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))
from tools.fastloop_research.contract import canonical_json
ROOT=Path(__file__).resolve().parents[2]; SRC=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'; PART=ROOT/'reports/semantic_public_partition_20260902/semantic-public-partition.json'; MATRIX=ROOT/'reports/candidate_opening_cut_impact_20260902/candidate-opening-cut-impact.json'; IDS=('OP002','OP006','OP007','OP008','OP010'); PUBLIC={'bedroom_corridor','kitchen','living_hall','lobby'}
def hs(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def h(v):return hashlib.sha256(canonical_json(v)).hexdigest()
def build(document=None):
 d=document or json.loads(SRC.read_text()); p=json.loads(PART.read_text()); m=json.loads(MATRIX.read_text()); rows=[]
 for x in m['openings']:
  if x['opening_id'] not in IDS:continue
  merged=x['merged_groups'][0]; nonpublic=sorted(set(merged)-PUBLIC); cell=next(c for c in p['opening_side_candidates'] if c['opening_id']==x['opening_id']); stable=[s for s in cell['sides'] if s['stable_public_cell_id']]; public_side=stable[0] if len(stable)==1 else None
  rows.append({'opening_id':x['opening_id'],'host_atom_id':x['host_atom_id'],'merged_group':merged,'public_cell_id':None if public_side is None else public_side['stable_public_cell_id'],'public_side':None if public_side is None else public_side['side'],'public_sign':None if public_side is None else public_side['sign'],'non_public_anchor_ids':nonpublic,'directed_side_assignment':None if len(nonpublic)!=1 or public_side is None else ({'side_a':nonpublic[0],'side_b':public_side['stable_public_cell_id']} if public_side['sign']<0 else {'side_a':public_side['stable_public_cell_id'],'side_b':nonpublic[0]}),'classification':'unique_pair_candidate' if len(nonpublic)==1 and public_side is not None else 'group_ambiguous','sensitivity_stable':len(stable)==1 and all(s['stable_public_cell_id']==public_side['stable_public_cell_id'] for s in cell['sides'] if s['stable_public_cell_id']),'cut_confirmation':False,'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False})
 result={'schema':'partition-resolved-cut-pair-candidate-v1','source_structure_hash':d['structure_hash'],'source_document_sha256':hs(SRC),'target_aware_wall_candidate_hash':m['target_aware_wall_candidate_hash'],'cut_matrix_file_sha256':hs(MATRIX),'cut_matrix_candidate_hash':m['candidate_hash'],'partition_file_sha256':hs(PART),'partition_candidate_hash':p['candidate_hash'],'public_anchor_ids':sorted(PUBLIC),'opening_ids':list(IDS),'openings':rows,'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=h({k:v for k,v in result.items() if k!='candidate_hash'});return result
def validate(document,candidate):
 expected=build(document)
 if candidate.get('candidate_hash')!=h({k:v for k,v in candidate.items() if k!='candidate_hash'}):raise ValueError('partition pair candidate hash drift')
 if candidate!=expected:raise ValueError('partition pair evidence/direction drift')
 return deepcopy(candidate)
def main():
 out=ROOT/'reports/partition_resolved_cut_pair_20260902';out.mkdir(parents=True,exist_ok=True);r=build();(out/'partition-resolved-cut-pair.json').write_text(json.dumps(r,indent=2)+'\n');(out/'REPORT.md').write_text('# Partition-resolved cut/pair candidate\n\nOP002, OP006, OP007 and OP010 yield unique non-public anchor candidates after subtracting the four public anchors. OP008 remains group-ambiguous because two non-public anchors remain. Direction is derived from partition sign/left-right orientation. Candidate-only; no pair, adjacency, score, or build promotion.\n');print(r['candidate_hash'])
if __name__=='__main__':main()
