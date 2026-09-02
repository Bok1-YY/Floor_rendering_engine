"""Render source-registered, non-selecting room-pair evidence composites."""
from pathlib import Path
import hashlib, json, math
from PIL import Image, ImageDraw
from .registration import _inverse, _apply, validate_pixel_metric_segment
from .opening_side_candidates import build_opening_side_space_candidate

ROOT=Path(__file__).resolve().parents[2]; REF=ROOT/'data/goal_loop_v2/references/1308/canonical-raw-portrait.png'; DOC=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json'; OUT=ROOT/'reports/room_pair_composites_20260902'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def label(draw, xy, text): draw.text((xy[0]+7,xy[1]+7), text, fill=(255,255,0), stroke_width=2, stroke_fill=(0,0,0))
def main():
 d=json.loads(DOC.read_text(encoding='utf-8')); matrix=d['source']['metric_registration']['canonical_px_to_metric_3x3']; im=Image.open(REF).convert('RGB'); OUT.mkdir(parents=True,exist_ok=True); side=build_opening_side_space_candidate(d); rows=[]
 for row in side['openings']:
  if row['opening_id'] not in {f'OP{i:03d}' for i in range(1,12)}: continue
  oid=row['opening_id']; metric=row['source_segment_m']; px=[list(_apply(_inverse(matrix),p)) for p in metric]; reg=validate_pixel_metric_segment(matrix,px,metric,1.0); points=[tuple(px[0]),tuple(px[1])]; anchors=[]
  for si,s in enumerate(row['sides']):
   for cand in s['candidates']:
    q=_apply(_inverse(matrix),cand['anchor_point_m']); anchors.append({'label':('A' if si==0 else 'B')+str(cand['rank']),'space_id':cand['space_id'],'point_m':cand['anchor_point_m'],'point_px':list(q),'side_id':s['side_id'],'rank':cand['rank']})
  allxy=points+[tuple(x['point_px']) for x in anchors]; pad=150; box=(max(0,int(min(x[0] for x in allxy)-pad)),max(0,int(min(x[1] for x in allxy)-pad)),min(im.width,int(max(x[0] for x in allxy)+pad)),min(im.height,int(max(x[1] for x in allxy)+pad)))
  crop=im.crop(box); cd=ImageDraw.Draw(crop); local=[(x-box[0],y-box[1]) for x,y in points]; cd.line(local,fill=(255,40,40),width=7)
  mid=((local[0][0]+local[1][0])/2,(local[0][1]+local[1][1])/2); dx=local[1][0]-local[0][0];dy=local[1][1]-local[0][1]; L=math.hypot(dx,dy); nx,ny=-dy/L,dx/L
  for sign,text in ((1,'A'),(-1,'B')):
   tip=(mid[0]+sign*nx*65,mid[1]+sign*ny*65); cd.line([mid,tip],fill=(0,255,255),width=5); label(cd,tip,text)
  for a in anchors:
   q=(a['point_px'][0]-box[0],a['point_px'][1]-box[1]); cd.ellipse((q[0]-7,q[1]-7,q[0]+7,q[1]+7),fill=(255,180,0)); label(cd,q,a['label'])
  cp=OUT/f'{oid}-room-pair-crop.png'; crop.save(cp); full=im.copy(); fd=ImageDraw.Draw(full); fd.line(points,fill=(255,40,40),width=7); fm=((points[0][0]+points[1][0])/2,(points[0][1]+points[1][1])/2)
  for sign,text in ((1,'A'),(-1,'B')):
   tip=(fm[0]+sign*nx*65,fm[1]+sign*ny*65); fd.line([fm,tip],fill=(0,255,255),width=5); label(fd,tip,text)
  for a in anchors:
   q=a['point_px']; fd.ellipse((q[0]-7,q[1]-7,q[0]+7,q[1]+7),fill=(255,180,0)); label(fd,q,a['label'])
  fp=OUT/f'{oid}-room-pair-full.png'; full.save(fp); rows.append({'opening_id':oid,'source_segment_m':metric,'source_segment_px':px,'registration':reg,'anchor_map':anchors,'artifacts':{'crop':{'path':str(cp.resolve()),'sha256':sha(cp),'size':list(crop.size)},'full':{'path':str(fp.resolve()),'sha256':sha(fp),'size':list(full.size)}},'pair_selected':False,'semantic_promotion':False,'adjacency_confirmation':False,'build_authorized':False})
 payload={'schema':'room-pair-composite-evidence-v1','sample_id':'1308','source_document_sha256':sha(DOC),'source_structure_hash':d['structure_hash'],'source_image_sha256':sha(REF),'registration_model':'canonical_px_to_metric_3x3','openings':rows,'semantic_promotion':False,'adjacency_confirmation':False,'build_authorized':False,'ready':False}
 jp=OUT/'room-pair-composites.json'; jp.write_text(json.dumps(payload,indent=2)+'\n',encoding='utf-8'); (OUT/'REPORT.md').write_text(f'# OP001–OP011 room-pair composite evidence\n\nSource SHA-256: `{sha(REF)}`\nEvidence JSON SHA-256: `{sha(jp)}`\n\nEleven independent composites show only the registered segment, directed A/B arrows, and neutral A1–A3/B1–B3 candidate labels mapped to source space IDs. No candidate pair is selected or promoted. All registration endpoint errors are required to be ≤1 px.\n',encoding='utf-8'); print(jp)
if __name__=='__main__': main()
