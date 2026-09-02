"""Render neutral geometric adjudication overlays for stable cut candidates."""
from pathlib import Path
import json,hashlib,math
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
def host_geometry(atom,segment):
 a,b=atom['centerline_m'];dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy);nx,ny=-dy/length,dx/length;half=atom['thickness_m']/2
 faces=[[[a[0]+nx*half,a[1]+ny*half],[b[0]+nx*half,b[1]+ny*half]],[[a[0]-nx*half,a[1]-ny*half],[b[0]-nx*half,b[1]-ny*half]]]
 params=[((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(length*length) for p in segment];lo,hi=min(params),max(params);before=max(0,lo*length);after=max(0,(1-hi)*length)
 return faces,{'host_parameters':[round(v,9) for v in params],'jamb_before_m':round(before,9),'jamb_after_m':round(after,9),'minimum_jamb_m':round(min(before,after),9),'candidate_minimum_m':.12,'candidate_sufficient':min(before,after)>=.12}
def draw_geometry(image,box,inv,pts,host,faces):
 dr=ImageDraw.Draw(image);offset=(box[0],box[1])
 def px(p):q=_apply(inv,p);return (q[0]-offset[0],q[1]-offset[1])
 center=[px(p) for p in host['centerline_m']];dr.line(center,fill=(160,40,220),width=5)
 host_faces,_=host_geometry(host,pts)
 for line in host_faces:dr.line([px(p) for p in line],fill=(20,100,255),width=5)
 for index,f in enumerate(faces):
  poly=[px(q) for q in f['polygon']];color=(0,220,220) if index==0 else (255,190,0);dr.line(poly+[poly[0]],fill=color,width=5);dr.text(poly[0],f['label'],fill=color,stroke_width=2,stroke_fill=(0,0,0))
 dr.line([px(p) for p in pts],fill=(255,40,40),width=8)
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 d=json.loads(DOC.read_text()); m=d['source']['metric_registration']['canonical_px_to_metric_3x3']; im=Image.open(REF).convert('RGB'); base=_surface_geometry(build_target_aware_wall_solids(d)['wall_union']['solid_m']); outer=Polygon(d['outer_boundary']['polygon_m']); free=outer.difference(base.intersection(outer)); parts=[p for p in _polygon_parts(free) if p.area>=.05]; impacts=json.loads(MATRIX.read_text())['openings']; rows=[]
 for x in impacts:
  if x['opening_id'] not in IDS:continue
  oid=x['opening_id']; seg=x['segment_m']; px=[list(_apply(_inverse(m),p)) for p in seg]; reg=validate_pixel_metric_segment(m,px,seg,1.0); host=next(a for a in d['wall_graph']['atoms'] if a['id']==x['host_atom_id']); post=base.difference(_cut_polygon(seg,host['thickness_m'],0,1e-6)); merged=x['merged_groups'][0]; faces=[]
  for name,space_id in zip(('F-A','F-B'),merged):
   matches=[]
   for p in parts:
    labels=sorted(s['id'] for s in d['spaces'] if p.covers(Point(s['point_m'])))
    if space_id in labels:matches.append((p,labels))
   if len(matches)!=1 or matches[0][1]!=[space_id]:raise ValueError(f'{oid} pre-cut face for {space_id} is not unique single-anchor')
   p,labels=matches[0];faces.append({'label':name,'space_ids':labels,'polygon_hash':h(list(p.exterior.coords)),'polygon':list(p.exterior.coords)})
  if faces[0]['polygon_hash']==faces[1]['polygon_hash']:raise ValueError(f'{oid} targeted pre-cut faces are identical')
  host_faces,jamb=host_geometry(host,seg)
  pts=[tuple(px[0]),tuple(px[1])]; allxy=pts
  for f in faces:
   allxy += [tuple(_apply(_inverse(m),q)) for q in f['polygon']]
  pad=120; box=(max(0,int(min(q[0] for q in allxy)-pad)),max(0,int(min(q[1] for q in allxy)-pad)),min(im.width,int(max(q[0] for q in allxy)+pad)),min(im.height,int(max(q[1] for q in allxy)+pad)));crop=im.crop(box);inv=_inverse(m);draw_geometry(crop,box,inv,seg,host,faces)
  cp=OUT/f'{oid}-crop.png';crop.save(cp);full=im.copy();draw_geometry(full,(0,0,im.width,im.height),inv,seg,host,faces);fp=OUT/f'{oid}-full.png';full.save(fp);rows.append({'opening_id':oid,'segment_m':seg,'registration':reg,'host_atom_id':host['id'],'host_centerline_m':host['centerline_m'],'host_thickness_m':host['thickness_m'],'host_face_segments_m':host_faces,'jamb_support':jamb,'merged_group':merged,'pre_cut_faces':faces,'post_cut_anchor_groups':x['post_cut_anchor_groups'],'sensitivity_stable':all(s['merged_groups']==x['merged_groups'] for s in x['sensitivity']) and all(s['merged_groups']==x['merged_groups'] for s in x['endpoint_sensitivity']),'artifacts':{'crop':{'path':str(cp.resolve()),'sha256':sha(cp)},'full':{'path':str(fp.resolve()),'sha256':sha(fp)}},'cut_confirmation':False,'pair_confirmation':False,'adjacency_confirmation':False,'build_authorized':False})
 out={'schema':'targeted-cut-adjudication-evidence-v1','source_structure_hash':d['structure_hash'],'target_aware_wall_candidate_hash':json.loads(MATRIX.read_text())['target_aware_wall_candidate_hash'],'cut_impact_matrix_hash':json.loads(MATRIX.read_text())['candidate_hash'],'openings':rows,'semantic_promotion':False,'adjacency_confirmation':False,'build_authorized':False,'ready':False};out['candidate_hash']=h(out);OUT.mkdir(parents=True,exist_ok=True);(OUT/'targeted-cut-adjudication.json').write_text(json.dumps(out,indent=2)+'\n');print(out['candidate_hash'])
if __name__=='__main__':main()
