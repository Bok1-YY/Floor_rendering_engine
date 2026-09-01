"""Build OP002 pixel evidence from the governing metric segment (candidate-only)."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
from PIL import Image, ImageDraw
from .registration import validate_pixel_metric_segment, _inverse, _apply

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data/goal_loop_v2/references/1308/canonical-raw-portrait.png"
DOC = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
OUT = ROOT / "reports/op002_vertical_evidence_20260901"

def sha(p: Path) -> str: return hashlib.sha256(p.read_bytes()).hexdigest()

def main() -> None:
    doc = json.loads(DOC.read_text(encoding="utf-8"))
    metric = next(x for x in doc["opening_contract"]["openings"] if x["id"] == "OP002")["effective_void"]["segment_m"]
    matrix = doc["source"]["metric_registration"]["canonical_px_to_metric_3x3"]
    pixel = [_apply(_inverse(matrix), p) for p in metric]
    pixel = [[float(x), float(y)] for x, y in pixel]
    check = validate_pixel_metric_segment(matrix, pixel, metric, tolerance_px=1.0)
    im = Image.open(SRC).convert("RGB")
    a, b = pixel
    pad = 180
    box = (max(0, int(min(a[0],b[0])-pad)), max(0, int(min(a[1],b[1])-pad)), min(im.width, int(max(a[0],b[0])+pad)), min(im.height, int(max(a[1],b[1])+pad)))
    OUT.mkdir(parents=True, exist_ok=True)
    crop = im.crop(box); d = ImageDraw.Draw(crop)
    pts = [(a[0]-box[0],a[1]-box[1]),(b[0]-box[0],b[1]-box[1])]
    d.line(pts, fill=(0,160,255), width=8)
    for x,y in pts: d.ellipse((x-8,y-8,x+8,y+8), fill=(0,160,255))
    cp = OUT / "op002-vertical-crop-overlay.png"; crop.save(cp)
    full = im.copy(); ImageDraw.Draw(full).line([tuple(a),tuple(b)], fill=(0,160,255), width=8)
    fp = OUT / "op002-vertical-full-overlay.png"; full.save(fp)
    payload = {"schema":"op002-door-evidence-v2","sample_id":"1308","opening_id":"OP002","source_path":str(SRC.resolve()),"source_sha256":sha(SRC),"source_document_path":str(DOC.resolve()),"source_document_sha256":sha(DOC),"source_structure_hash":doc["structure_hash"],"registration_model":"canonical_px_to_metric_3x3","metric_segment_m":metric,"source_segment_px":pixel,"registration_validation":check,"host_atom_id":"ATOM-WB006-02","host_relation":"exact_collinear_overlap","candidate_side_a_space_id":"bedroom_01","candidate_side_b_space_id":"bedroom_corridor","semantic_status":"candidate_only","traversable_status":"candidate_only","observations":["Pixel segment is inverse-projected from governing metric OP002; no axis reinterpretation was applied.","This evidence supersedes the stale horizontal pixel packet for registration purposes only.","This packet does not promote door type, jamb, height, adjacency, or build authorization."],"artifacts":{"crop_overlay":{"path":str(cp.resolve()),"sha256":sha(cp),"size":list(Image.open(cp).size)},"full_overlay":{"path":str(fp.resolve()),"sha256":sha(fp),"size":list(Image.open(fp).size)}},"semantic_promotion":False,"build_authorized":False,"ready":False}
    jp = OUT / "op002-vertical-evidence.json"; jp.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    (OUT/"REPORT.md").write_text(f"# OP002 vertical registration evidence (candidate-only)\n\nSource SHA-256: `{payload['source_sha256']}`\n\nDocument SHA-256: `{payload['source_document_sha256']}`\n\nEvidence JSON SHA-256: `{sha(jp)}`\n\nThe blue segment is the inverse projection of the governing metric OP002 segment. Registration max endpoint error: `{check['max_endpoint_error_px']:.9f}px`. No semantic promotion or build authorization is granted.\n",encoding="utf-8")
    print(jp)

if __name__ == "__main__": main()
