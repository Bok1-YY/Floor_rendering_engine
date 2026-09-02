"""Render neutral geometric adjudication overlays for stable cut candidates."""
from pathlib import Path
import json,hashlib
from PIL import Image,ImageDraw
from shapely.geometry import Point,Polygon
from tools.fastloop_research.contract import canonical_json
from tools.goal_loop_v2.target_aware_wall_solids import build_target_aware_wall_solids
from tools.goal_loop_v2.op002_opening_cut import _surface_geometry,_cut_polygon
from tools.goal_loop_v2.junction_wall_solids import _polygon_parts
from tools.goal_loop_v2.registration import _inverse,_apply,validate_pixel_metric_segment
ROOT=Path(__file__).resolve().parents[2]; DOC=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'; REF=ROOT/'data/goal_loop_v2/references/1308/canonical-raw-portrait.png'; MATRIX=ROOT/'reports/candidate_opening_cut_impact_20260902/candidate-opening-cut-impact.json'; OUT=ROOT/'reports/targeted_cut_adjudication_20260902'; IDS=('OP003','OP004','OP009')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def h(v):return hashlib.sha256(canonical_json(v)).hexdigest()
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 d=json.loads(DOC.read_text()); m=d['source']['metric_registration']['canonical_px_to_metric_3x3']; im=Image.open(REF).convert('RGB'); base=_surface_geometry(build_target_aware_wall_solids(d)['wall_union']['solid_m']); outer=Polygon(d['outer_boundary']['polygon_m']); free=outer.difference(base.intersection(outer)); parts=[p for p in _polygon_parts(free) if p.area>=.05]; impacts=json.loads(MATRIX.read_text())['openings']; rows=[]
 for x in impacts:
  if x['opening_id'] not in IDS:continue
  oid=x['opening_id']; seg=x['segment_m']; px=[list(_apply(_inverse(m),p)) for p in seg]; reg=validate_pixel_metric_segment(m,px,seg,1.0); host=next(a for a in d['wall_graph']['atoms'] if a['id']==x['host_atom_id']); post=base.difference(_cut_polygon(seg,host['thickness_m'],0,1e-6)); merged=x['merged_groups'][0]; faces=[]
  for name,geom in (('F-A',base),('F-B',base)):
   ids=[]
   for p in parts:
    labels=[s['id'] for s in d['spaces'] if p.covers(Point(s['point_m']))]
    if set(labels)&set(merged):ids.extend(labels); faces.append({'label':name,'space_ids':sorted(set(labels)),'polygon_hash':h(list(p.exterior.coords)),'polygon':list(p.exterior.coords)}) ; break
  pts=[tuple(px[0]),tuple(px[1])]; allxy=pts
  for f in faces:
   allxy += [tuple(_apply(_inverse(m),q)) for q in f['polygon']]
  pad=120; box=(max(0,int(min(q[0] for q in allxy)-pad)),max(0,int(min(q[1] for q in allxy)-pad)),min(im.width,int(max(q[0] for q in allxy)+pad)),min(im.height,int(max(q[1] for q in allxy)+pad))); crop=im.crop(box); dr=ImageDraw.Draw(crop); local=[(q[0]-box[0],q[1]-box[1]) for q in pts]; dr.line(local,fill=(255,40,40),width=8)
  for i,f in enumerate(faces):
   poly=[(_apply(_inverse(m),q)[0]-box[0],_apply(_inverse(m),q)[1]-box[1]) for q in f['polygon']]; dr.line(poly+[poly[0]],fill=(0,255,255),width=4); dr.text(poly[0],f['label'],fill=(255,255,0),stroke_width=2,stroke_fill=(0,0,0))
  cp=OUT/f'{oid}-crop.png';crop.save(cp);full=im.copy();ImageDraw.Draw(full).line(pts,fill=(255,40,40),width=8);fp=OUT/f'{oid}-full.png';full.save(fp);rows.append({'opening_id':oid,'segment_m':seg,'registration':reg,'host_atom_id':host['id'],'host_thickness_m':host['thickness_m'],'merged_group':merged,'pre_cut_faces':faces,'post_cut_anchor_groups':x['post_cut_anchor_groups'],'sensitivity_stable':all(s['merged_groups']==x['merged_groups'] for s in x['sensitivity']) and all(s['merged_groups']==x['merged_groups'] for s in x['endpoint_sensitivity']),'artifacts':{'crop':{'path':str(cp.resolve()),'sha256':sha(cp)},'full':{'path':str(fp.resolve()),'sha256':sha(fp)}},'cut_confirmation':False,'pair_confirmation':False,'adjacency_confirmation':False,'build_authorized':False})
 out={'schema':'targeted-cut-adjudication-evidence-v1','source_structure_hash':d['structure_hash'],'target_aware_wall_candidate_hash':json.loads(MATRIX.read_text())['target_aware_wall_candidate_hash'],'cut_impact_matrix_hash':json.loads(MATRIX.read_text())['candidate_hash'],'openings':rows,'semantic_promotion':False,'adjacency_confirmation':False,'build_authorized':False,'ready':False};out['candidate_hash']=h(out);OUT.mkdir(parents=True,exist_ok=True);(OUT/'targeted-cut-adjudication.json').write_text(json.dumps(out,indent=2)+'\n');print(out['candidate_hash'])
if __name__=='__main__':main()
