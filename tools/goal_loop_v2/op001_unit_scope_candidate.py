"""Build a non-applying OP001 common-core to Flat-101 unit-scope candidate."""
from __future__ import annotations
from copy import deepcopy
import hashlib,json,sys
from pathlib import Path
from typing import Any,Mapping
from PIL import Image,ImageDraw
from shapely.geometry import Point,Polygon
from shapely.ops import unary_union

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.junction_wall_solids import _polygon_parts
from tools.goal_loop_v2.op002_opening_cut import _surface_geometry
from tools.goal_loop_v2.registration import _inverse,_apply,validate_pixel_metric_segment
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids

SOURCE=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';IMAGE=ROOT/'data/goal_loop_v2/references/1308/canonical-raw-portrait.png';EVIDENCE=ROOT/'reports/op001_entrance_evidence_20260901/op001-evidence.json';PARTITION=ROOT/'reports/semantic_public_partition_20260902/semantic-public-partition.json';OUT=ROOT/'reports/op001_unit_scope_candidate_20260902'
def _hash(v:Any)->str:return hashlib.sha256(canonical_json(v)).hexdigest()
def _file_hash(p)->str:return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def _canon(g):return [{'exterior':[list(x) for x in p.exterior.coords],'holes':[[list(x) for x in ring.coords] for ring in p.interiors]} for p in sorted(_polygon_parts(g),key=lambda p:(p.bounds[0],p.bounds[1],p.area))]
def _geometry(serialized):return unary_union([Polygon(x['exterior'],x.get('holes',[])) for x in serialized])
def _draw(image,box,inv,segment,common,unit):
 dr=ImageDraw.Draw(image);offset=(box[0],box[1]);px=lambda p:(lambda q:(q[0]-offset[0],q[1]-offset[1]))(_apply(inv,p))
 for geom,label,color in ((common,'COMMON',(0,220,220)),(unit,'UNIT',(255,170,0))):
  for part in _polygon_parts(geom):
   poly=[px(p) for p in part.exterior.coords];dr.line(poly,fill=color,width=6);dr.text(poly[0],label,fill=color,stroke_width=2,stroke_fill=(0,0,0))
 dr.line([px(p) for p in segment],fill=(255,40,40),width=9)
def build_op001_unit_scope_candidate(document:Mapping[str,Any],*,_skip_validate=False):
 doc=validate_v21_document(document);ev=json.loads(EVIDENCE.read_text());partition=json.loads(PARTITION.read_text());opening=next(x for x in doc['opening_contract']['openings'] if x['id']=='OP001');space_by={x['id']:x for x in doc['spaces']};wall=build_target_aware_wall_solids(doc);base=_surface_geometry(wall['wall_union']['solid_m']);outer=Polygon(doc['outer_boundary']['polygon_m']);parts=[p for p in _polygon_parts(outer.difference(base.intersection(outer))) if p.area>=.05];common_matches=[p for p in parts if p.covers(Point(space_by['common_core_circulation']['point_m']))]
 if len(common_matches)!=1:raise ValueError('OP001 common-core physical face is not unique')
 common=common_matches[0];common_labels=sorted(x['id'] for x in doc['spaces'] if common.covers(Point(x['point_m'])))
 if common_labels!=['common_core_circulation']:raise ValueError('OP001 common-core face is not single-anchor')
 lobby_cell=next(x for x in partition['cells'] if x['space_id']=='lobby');unit=_geometry(lobby_cell['polygons'])
 if common.intersection(unit).area>1e-9:raise ValueError('OP001 common/unit scope geometries overlap')
 segment=opening['source_observation']['nominal_segment_m'];metric=doc['source']['metric_registration']['canonical_px_to_metric_3x3'];inv=_inverse(metric);pixels=[list(_apply(inv,p)) for p in segment];registration=validate_pixel_metric_segment(metric,pixels,segment,1.0);source_image=Image.open(IMAGE).convert('RGB');all_points=[*segment,*[q for p in _polygon_parts(common) for q in p.exterior.coords],*[q for p in _polygon_parts(unit) for q in p.exterior.coords]];all_px=[_apply(inv,p) for p in all_points];pad=100;box=(max(0,int(min(p[0] for p in all_px)-pad)),max(0,int(min(p[1] for p in all_px)-pad)),min(source_image.width,int(max(p[0] for p in all_px)+pad)),min(source_image.height,int(max(p[1] for p in all_px)+pad)));crop=source_image.crop(box);_draw(crop,box,inv,segment,common,unit);full=source_image.copy();_draw(full,(0,0,source_image.width,source_image.height),inv,segment,common,unit);OUT.mkdir(parents=True,exist_ok=True);cp=OUT/'OP001-unit-scope-crop.png';fp=OUT/'OP001-unit-scope-full.png';crop.save(cp);full.save(fp)
 result={'schema':'op001-unit-scope-candidate-v1','source_structure_hash':doc['structure_hash'],'opening_id':'OP001','source_snapshot':{'kind':opening['source_observation']['kind'],'observation_status':opening['source_observation']['status'],'active_status':opening['status'],'host':deepcopy(opening['host']),'effective_void':deepcopy(opening['effective_void']),'jamb_before':deepcopy(opening['jamb_before']),'jamb_after':deepcopy(opening['jamb_after']),'traversable':opening['traversable']},'registration':registration,'segment_m':deepcopy(segment),'building_scope_fact':{'intersects_confirmed_outer_boundary':False,'building_exterior_root_confirmation':False},'unit_scope_hypothesis':{'common_side_space_id':'common_core_circulation','common_side_role':'external_to_private_unit_candidate','unit_side_space_id':'lobby','unit_side_role':'flat_101_interior_candidate','entry_label_visible_candidate':True,'flat_101_label_visible_candidate':True,'door_swing_visible_candidate':bool(ev['door_swing_or_jamb_proven']),'unit_root_candidate':True},'scope_geometries':{'common':{'label':'COMMON','space_ids':common_labels,'polygons':_canon(common),'polygon_hash':_hash(_canon(common))},'unit':{'label':'UNIT','space_ids':['lobby'],'polygons':deepcopy(lobby_cell['polygons']),'polygon_hash':lobby_cell['polygon_hash']}},'artifact_bindings':{'full':{'path':str(fp.resolve()),'bytes':fp.stat().st_size,'sha256':_file_hash(fp)},'crop':{'path':str(cp.resolve()),'bytes':cp.stat().st_size,'sha256':_file_hash(cp)}},'evidence_chain':{'source_document_sha256':_file_hash(SOURCE),'source_image_sha256':_file_hash(IMAGE),'op001_evidence_sha256':_file_hash(EVIDENCE),'target_aware_wall_hash':wall['candidate_hash'],'semantic_partition_hash':partition['candidate_hash']},'remaining_blockers':['UNIT_SCOPE_SOURCE_AUTHORITY_PENDING','SIDE_SPACE_CONFIRMATION_PENDING','TRAVERSABILITY_PENDING','ADJACENCY_PENDING','HUMAN_ACCEPTANCE_PENDING'],'unit_scope_confirmation':False,'pair_confirmation':False,'traversability_confirmation':False,'adjacency_confirmation':False,'semantic_promotion':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};result['candidate_hash']=_hash({k:v for k,v in result.items() if k!='candidate_hash'})
 return result if _skip_validate else validate_op001_unit_scope_candidate(doc,result)
def validate_op001_unit_scope_candidate(document,candidate):
 doc=validate_v21_document(document)
 if candidate.get('schema')!='op001-unit-scope-candidate-v1' or candidate.get('opening_id')!='OP001':raise ValueError('OP001 unit scope schema/identity drift')
 for key in ('unit_scope_confirmation','pair_confirmation','traversability_confirmation','adjacency_confirmation','semantic_promotion','build_authorized','ready'):
  if candidate.get(key) is not False:raise ValueError('OP001 unit scope candidate was promoted')
 if (candidate.get('building_scope_fact') or {}).get('building_exterior_root_confirmation') is not False:raise ValueError('OP001 building exterior root was fabricated')
 if candidate!=build_op001_unit_scope_candidate(doc,_skip_validate=True):raise ValueError('OP001 unit scope source/geometry drift')
 return deepcopy(dict(candidate))
def main():
 doc=json.loads(SOURCE.read_text());result=build_op001_unit_scope_candidate(doc);OUT.mkdir(parents=True,exist_ok=True);(OUT/'op001-unit-scope-candidate.json').write_text(json.dumps(result,indent=2)+'\n');(OUT/'REPORT.md').write_text('# OP001 unit-scope entrance candidate\n\nOP001 lies between the physical common-core face and the lobby semantic cell. ENTRY/Flat-101/door-swing pixels support a common-to-unit hypothesis even though OP001 does not intersect the whole-building outer boundary. Candidate-only; no unit root, pair, traversal, adjacency, score or build is confirmed.\n');print(result['candidate_hash'])
if __name__=='__main__':main()
__all__=['build_op001_unit_scope_candidate','validate_op001_unit_scope_candidate']
