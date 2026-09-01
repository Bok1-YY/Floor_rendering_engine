from pathlib import Path
from PIL import Image, ImageDraw
import hashlib, json, math
ROOT=Path(__file__).resolve().parents[2]; REF=ROOT/'data/goal_loop_v2/references/1308'; OUT=ROOT/'reports/op001_entrance_evidence_20260901'; OUT.mkdir(parents=True,exist_ok=True)
im=Image.open(REF/'canonical-raw-portrait.png').convert('RGB')
def px(p): return (497+p[0]/0.007318435754189944,2382-p[1]/0.007272209026128266)
seg=[[3.432346,5.62869],[4.39838,5.62869]]; a,b=map(px,seg); box=tuple(int(v) for v in (min(a[0],b[0])-260,min(a[1],b[1])-260,max(a[0],b[0])+260,max(a[1],b[1])+260))
crop=im.crop(box); d=ImageDraw.Draw(crop); d.line([(a[0]-box[0],a[1]-box[1]),(b[0]-box[0],b[1]-box[1])],fill='red',width=8); crop.save(OUT/'op001-crop-overlay.png')
full=im.copy(); d=ImageDraw.Draw(full); d.line([a,b],fill='red',width=10); full.save(OUT/'op001-full-overlay.png')
sha=lambda p: hashlib.sha256(p.read_bytes()).hexdigest(); length=math.dist(*seg)
data={'schema':'op001-entrance-evidence-v1','sample_id':'1308','opening_id':'OP001','source_segment_m':seg,'source_segment_px':[list(a),list(b)],'crop_box_px':list(box),'source_sha256':sha(REF/'canonical-raw-portrait.png'),'nearest_host_candidate':'ATOM-WB016-02','nearest_host_midpoint_distance_m':0.015638,'nearest_host_segment_distance_m':0.011283,'length_m':length,'closed_wall_break_proven':False,'door_swing_or_jamb_proven':True,'visual_observation':'crop visibly contains a dashed door swing and vertical door/jamb line at the right end; exact opening endpoints and host-wall cut remain unproven','conclusion':'candidate_only; visual door-swing evidence is present, but exact closed wall break, endpoint ownership, and traversable side-space relation are not fully proven','semantic_promotion':False,'build_authorized':False,'artifacts':{}}
for p in (OUT/'op001-crop-overlay.png',OUT/'op001-full-overlay.png'): data['artifacts'][p.stem]={'path':str(p.resolve()),'sha256':sha(p),'size':list(Image.open(p).size)}
(OUT/'op001-evidence.json').write_text(json.dumps(data,indent=2),encoding='utf-8'); ej=sha(OUT/'op001-evidence.json')
(OUT/'REPORT.md').write_text(f'# OP001 entrance evidence\n\nSource SHA: `{data["source_sha256"]}`\nEvidence JSON SHA: `{ej}`\n\nOP001 is a horizontal {length:.6f} m source segment near `ATOM-WB016-02`. The crop visibly contains a dashed door swing and a vertical door/jamb line at the right end. However, exact opening endpoints, host-wall cut ownership, and traversable side-space relation remain unproven. Candidate-only; no semantic promotion or build authorization.\n',encoding='utf-8')
