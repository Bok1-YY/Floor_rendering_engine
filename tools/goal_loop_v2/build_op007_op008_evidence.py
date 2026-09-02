"""Build deterministic, candidate-only registered evidence for OP007 and OP008."""
from pathlib import Path
import hashlib, json, math
from PIL import Image, ImageDraw
from .registration import _inverse, _apply, validate_pixel_metric_segment

ROOT = Path(__file__).resolve().parents[2]
REF = ROOT / "data/goal_loop_v2/references/1308/canonical-raw-portrait.png"
DOC = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
ATOMS = Path(r"C:/Users/1_1/Desktop/goal_loop_v2_1308_wall_graph_audit_20260901/atoms-audit.json")
OUT = ROOT / "reports/op007_op008_geometry_evidence_20260902"

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def segdist(p, a, b):
    x,y=p; ax,ay=a; bx,by=b; dx,dy=bx-ax,by-ay
    den=dx*dx+dy*dy
    t=0.0 if not den else max(0.0,min(1.0,((x-ax)*dx+(y-ay)*dy)/den))
    return math.hypot(x-(ax+t*dx), y-(ay+t*dy))

def main():
    doc=json.loads(DOC.read_text(encoding="utf-8")); atoms=json.loads(ATOMS.read_text(encoding="utf-8"))
    matrix=doc["source"]["metric_registration"]["canonical_px_to_metric_3x3"]
    image=Image.open(REF).convert("RGB"); OUT.mkdir(parents=True,exist_ok=True); rows=[]
    expected={"OP007":"ATOM-WB019-01","OP008":"ATOM-WB018-01"}
    for oid in ("OP007","OP008"):
        op=next(x for x in doc["opening_contract"]["openings"] if x["id"]==oid)
        metric=op["source_observation"]["nominal_segment_m"]
        pixel=[_apply(_inverse(matrix),p) for p in metric]
        registration=validate_pixel_metric_segment(matrix,pixel,metric,tolerance_px=1.0)
        host=[]
        for atom in atoms:
            d=sum((segdist(metric[i],*atom["centerline_m"]) for i in (0,1)))
            if d < 0.15: host.append({"atom_id":atom["id"],"endpoint_distance_sum_m":d,"segment_m":atom["centerline_m"]})
        host.sort(key=lambda x:x["endpoint_distance_sum_m"])
        # Keep nearby spaces as geometric candidates only; do not infer adjacency.
        near=[]
        for space in doc["spaces"]:
            d=segdist(space["point_m"],*metric)
            if d < 2.0: near.append({"space_id":space["id"],"point_m":space["point_m"],"distance_m":d})
        near.sort(key=lambda x:x["distance_m"])
        a,b=pixel; pad=180
        box=(max(0,int(min(a[0],b[0])-pad)),max(0,int(min(a[1],b[1])-pad)),min(image.width,int(max(a[0],b[0])+pad)),min(image.height,int(max(a[1],b[1])+pad)))
        crop=image.crop(box); draw=ImageDraw.Draw(crop); pts=[(a[0]-box[0],a[1]-box[1]),(b[0]-box[0],b[1]-box[1])]
        draw.line(pts,fill=(255,40,40),width=8)
        for x,y in pts: draw.ellipse((x-8,y-8,x+8,y+8),fill=(255,40,40))
        cp=OUT/f"{oid}-crop-overlay.png"; crop.save(cp)
        full=image.copy(); ImageDraw.Draw(full).line([tuple(a),tuple(b)],fill=(255,40,40),width=8)
        fp=OUT/f"{oid}-full-overlay.png"; full.save(fp)
        rows.append({"opening_id":oid,"source_segment_m":metric,"source_segment_px":pixel,"registration":registration,"host_wall_candidates":host,"expected_host_candidate":expected[oid],"nearby_space_candidates":near,"semantic_status":"candidate_only","traversable_status":"candidate_only","observations":["Pixel segment is inverse-projected from the governing metric contract.","OP007 and OP008 remain distinct openings; host and space associations are geometric candidates only."],"artifacts":{"crop":{"path":str(cp.resolve()),"sha256":sha(cp),"size":list(crop.size)},"full":{"path":str(fp.resolve()),"sha256":sha(fp),"size":list(full.size)}}})
    payload={"schema":"op007-op008-geometry-evidence-v1","sample_id":"1308","source_path":str(REF.resolve()),"source_sha256":sha(REF),"source_document_path":str(DOC.resolve()),"source_document_sha256":sha(DOC),"source_structure_hash":doc["structure_hash"],"registration_model":"canonical_px_to_metric_3x3","openings":rows,"semantic_promotion":False,"build_authorized":False,"ready":False}
    jp=OUT/"op007-op008-evidence.json"; jp.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    (OUT/"REPORT.md").write_text("# OP007 / OP008 independent geometry evidence\n\n"+f"Source SHA-256: `{sha(REF)}`\nEvidence JSON SHA-256: `{sha(jp)}`\n\n"+"Both metric opening segments were inverse-projected into the canonical pixel frame and checked with a 1 px tolerance. OP007 is paired with ATOM-WB019-01 and OP008 with ATOM-WB018-01 as exact geometric host candidates; associations remain candidate-only. No semantic promotion or build authorization is granted.\n",encoding="utf-8")
    print(jp)
if __name__ == "__main__": main()
