"""Build raw OP011 crop and a separated locator image."""
import json,hashlib,sys
from pathlib import Path
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.goal_loop_v2.registration import _inverse,_apply,validate_pixel_metric_segment
SRC=ROOT/'data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json';HOST=ROOT/'reports/op011_host_scope_candidate_20260902/op011-host-scope-candidate.json';RAW=ROOT/'data/goal_loop_v2/references/1308/canonical-raw-portrait.png';OUT=ROOT/'reports/op011_uncontaminated_evidence_20260902'
def hs(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def build():
 d=json.loads(SRC.read_text());host=json.loads(HOST.read_text());im=Image.open(RAW).convert('RGB');seg=next(o for o in d['opening_contract']['openings'] if o['id']=='OP011')['source_observation']['nominal_segment_m'];m=d['source']['metric_registration']['canonical_px_to_metric_3x3'];px=[list(_apply(_inverse(m),p)) for p in seg];reg=validate_pixel_metric_segment(m,px,seg,1.0);pad=120;box=(max(0,int(min(p[0] for p in px)-pad)),max(0,int(min(p[1] for p in px)-pad)),min(im.width,int(max(p[0] for p in px)+pad)),min(im.height,int(max(p[1] for p in px)+pad)));crop=im.crop(box);OUT.mkdir(parents=True,exist_ok=True);cp=OUT/'OP011-raw-crop.png';crop.save(cp);full=im.copy();dr=ImageDraw.Draw(full);x0,y0=min(p[0] for p in px)-35,min(p[1] for p in px)-35;x1,y1=max(p[0] for p in px)+35,max(p[1] for p in px)+35;dr.rectangle((x0,y0,x1,y1),outline=(255,200,0),width=4);dr.line([(x0-1,y0-1),(x0-1,y0-1)],fill=(255,200,0));dr.text((x0-30,y0-30),'OP011',fill=(255,200,0),stroke_width=2,stroke_fill=(0,0,0));fp=OUT/'OP011-locator.png';full.save(fp)
 out={'schema':'op011-uncontaminated-evidence-v1','opening_id':'OP011','source_document_sha256':hs(SRC),'source_structure_hash':d['structure_hash'],'source_image_sha256':hs(RAW),'host_scope_candidate_sha256':hs(HOST),'host_scope_candidate_hash':host.get('candidate_hash'),'registered_segment_m':seg,'registered_segment_px':px,'registration':reg,'crop_box_px':list(box),'original_image_size':[im.width,im.height],'locator_min_clearance_px':30,'source_pixels_untouched':True,'artifacts':{'raw_crop':{'path':str(cp.resolve()),'bytes':cp.stat().st_size,'sha256':hs(cp)},'locator':{'path':str(fp.resolve()),'bytes':fp.stat().st_size,'sha256':hs(fp)}},'semantic_promotion':False,'traversability_confirmation':False,'adjacency_confirmation':False,'score_effect':'none','build_authorized':False,'ready':False,'candidate_hash':'0'*64};out['candidate_hash']=hashlib.sha256(json.dumps({k:v for k,v in out.items() if k!='candidate_hash'},sort_keys=True,separators=(',',':')).encode()).hexdigest();return out
def main():
 r=build();OUT.mkdir(parents=True,exist_ok=True);(OUT/'op011-uncontaminated-evidence.json').write_text(json.dumps(r,indent=2)+'\n');print(r['candidate_hash'])
if __name__=='__main__':main()
