"""Research-only cut impacts using exact registered evidence host candidates."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from typing import Any,Mapping
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.all_opening_cut_impact import DENIED,DELTAS,WIDTHS,_groups,_hash
from tools.goal_loop_v2.junction_wall_solids import _polygon_parts
from tools.goal_loop_v2.op002_opening_cut import _cut_polygon,_surface_geometry
from tools.goal_loop_v2.opening_side_candidates import build_opening_side_space_candidate
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids

ROOT=Path(__file__).resolve().parents[2];REPORTS=ROOT/'reports'
EVIDENCE_FILES={
'OP003':REPORTS/'op003_op004_geometry_evidence_20260901/op003-op004-evidence.json','OP004':REPORTS/'op003_op004_geometry_evidence_20260901/op003-op004-evidence.json',
'OP005':REPORTS/'op005_op006_geometry_evidence_20260901/op005-op006-evidence.json','OP006':REPORTS/'op005_op006_geometry_evidence_20260901/op005-op006-evidence.json',
'OP007':REPORTS/'op007_op008_geometry_evidence_20260902/op007-op008-evidence.json','OP008':REPORTS/'op007_op008_geometry_evidence_20260902/op007-op008-evidence.json',
'OP009':REPORTS/'op009_op010_geometry_evidence_20260901/op009-op010-evidence.json','OP010':REPORTS/'op009_op010_geometry_evidence_20260901/op009-op010-evidence.json',
'OP011':REPORTS/'op011_geometry_evidence_20260902/op011-evidence.json'}
def _evidence_row(oid):
    path=EVIDENCE_FILES[oid];value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('opening_id')==oid:return path,value
    return path,next(x for x in value['openings'] if x['opening_id']==oid)
def _binding(path):
    raw=path.read_bytes();return {'path':str(path.resolve()),'file_sha256':hashlib.sha256(raw).hexdigest(),'canonical_sha256':hashlib.sha256(canonical_json(json.loads(raw.decode('utf-8')))).hexdigest()}
def _classify(before,after):
    merged=[group for group in after if len(group)>1 and group not in before]
    return ('unique_single_to_single_pair' if len(merged)==1 and len(merged[0])==2 else ('group_ambiguous' if merged else 'no_topology_change')),merged
def build_candidate_opening_cut_impact(document:Mapping[str,Any],*,_skip_validate=False):
    doc=validate_v21_document(document);wall=build_target_aware_wall_solids(doc);side=build_opening_side_space_candidate(doc);base=_surface_geometry(wall['wall_union']['solid_m']);before=_groups(doc,base);rows=[];bindings={}
    for opening in doc['opening_contract']['openings']:
        oid=opening['id'];row={'opening_id':oid,'authority':'none','cuttable':False,'classification':'not_cuttable','host_atom_id':None,'segment_m':None,'pre_cut_anchor_groups':before,'post_cut_anchor_groups':before,'merged_groups':[],'sensitivity':[],'endpoint_sensitivity':[],'cut_confirmation':False,'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False}
        if oid==DENIED:rows.append(row);continue
        host=opening.get('host');effective=opening.get('effective_void');atom_id=host.get('owning_wall_atom_id') if host else None;segment=effective.get('segment_m') if effective else None;authority='source_active' if atom_id and segment else None
        if authority is None and oid in EVIDENCE_FILES:
            path,evidence=_evidence_row(oid);bindings[str(path.resolve())]=_binding(path);candidates=evidence.get('host_wall_candidates') or []
            if len(candidates)==1 and float(candidates[0].get('endpoint_distance_sum_m',1))<=1e-5:
                atom_id=candidates[0]['atom_id'];segment=evidence['source_segment_m'];authority='registered_evidence_candidate'
        if authority is None:rows.append(row);continue
        atom=next((x for x in doc['wall_graph']['atoms'] if x['id']==atom_id),None)
        if atom is None:rows.append(row);continue
        row.update(authority=authority,cuttable=True,host_atom_id=atom_id,segment_m=segment)
        after=_groups(doc,base.difference(_cut_polygon(segment,atom['thickness_m'],0,1e-6)));classification,merged=_classify(before,after);row.update(classification=classification,post_cut_anchor_groups=after,merged_groups=merged)
        for width in WIDTHS:
            groups=_groups(doc,base.difference(_cut_polygon(segment,atom['thickness_m'],0,width)));kind,mg=_classify(before,groups);row['sensitivity'].append({'half_width_m':width,'classification':kind,'merged_groups':mg,'anchor_groups':groups})
        for delta in DELTAS:
            groups=_groups(doc,base.difference(_cut_polygon(segment,atom['thickness_m'],delta,1e-6)));kind,mg=_classify(before,groups);row['endpoint_sensitivity'].append({'endpoint_delta_m':delta,'classification':kind,'merged_groups':mg,'anchor_groups':groups})
        rows.append(row)
    result={'schema':'candidate-opening-cut-impact-v1','source_structure_hash':doc['structure_hash'],'target_aware_wall_candidate_hash':wall['candidate_hash'],'opening_side_candidate_hash':side['candidate_hash'],'evidence_bindings':sorted(bindings.values(),key=lambda x:x['path']),'openings':rows,'denied_ids':[DENIED],'research_only':True,'cut_confirmation':False,'pair_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
    return result if _skip_validate else validate_candidate_opening_cut_impact(doc,result)
def validate_candidate_opening_cut_impact(document,candidate):
    doc=validate_v21_document(document)
    if candidate.get('schema')!='candidate-opening-cut-impact-v1' or [x.get('opening_id') for x in candidate.get('openings',[])[:11]]!=[f'OP{i:03d}' for i in range(1,12)]:raise ValueError('candidate cut matrix coverage drift')
    for key in ('cut_confirmation','pair_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'):
        if candidate.get(key) is not False:raise ValueError('candidate cut matrix was promoted')
    if dict(candidate)!=build_candidate_opening_cut_impact(doc,_skip_validate=True):raise ValueError('candidate cut matrix geometry/evidence drift')
    return json.loads(json.dumps(candidate))
__all__=['build_candidate_opening_cut_impact','validate_candidate_opening_cut_impact']
