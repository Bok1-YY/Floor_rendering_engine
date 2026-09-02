"""Build source-registered, fail-closed visual evidence for OP011."""
from pathlib import Path
import hashlib, json
from PIL import Image, ImageDraw
from .registration import _inverse, _apply, validate_pixel_metric_segment

ROOT = Path(__file__).resolve().parents[2]
REF = ROOT / "data/goal_loop_v2/references/1308/canonical-raw-portrait.png"
DOC = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
OUT = ROOT / "reports/op011_geometry_evidence_20260902"

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    doc = json.loads(DOC.read_text(encoding="utf-8"))
    matrix = doc["source"]["metric_registration"]["canonical_px_to_metric_3x3"]
    opening = next(x for x in doc["opening_contract"]["openings"] if x["id"] == "OP011")
    metric = opening["source_observation"]["nominal_segment_m"]
    pixel = [list(_apply(_inverse(matrix), point)) for point in metric]
    registration = validate_pixel_metric_segment(matrix, pixel, metric, tolerance_px=1.0)
    image = Image.open(REF).convert("RGB")
    OUT.mkdir(parents=True, exist_ok=True)
    a, b = pixel
    pad = 180
    box = (max(0, int(min(a[0], b[0]) - pad)), max(0, int(min(a[1], b[1]) - pad)), min(image.width, int(max(a[0], b[0]) + pad)), min(image.height, int(max(a[1], b[1]) + pad)))
    crop = image.crop(box)
    points = [(a[0] - box[0], a[1] - box[1]), (b[0] - box[0], b[1] - box[1])]
    draw = ImageDraw.Draw(crop); draw.line(points, fill=(255, 40, 40), width=8)
    for x, y in points: draw.ellipse((x-8, y-8, x+8, y+8), fill=(255, 40, 40))
    cp = OUT / "OP011-crop-overlay.png"; crop.save(cp)
    full = image.copy(); ImageDraw.Draw(full).line([tuple(a), tuple(b)], fill=(255, 40, 40), width=8)
    fp = OUT / "OP011-full-overlay.png"; full.save(fp)
    payload = {
        "schema": "op011-geometry-evidence-v1", "sample_id": "1308", "opening_id": "OP011",
        "source_path": str(REF.resolve()), "source_sha256": sha(REF),
        "source_document_path": str(DOC.resolve()), "source_document_sha256": sha(DOC),
        "source_structure_hash": doc["structure_hash"], "registration_model": "canonical_px_to_metric_3x3",
        "source_status": opening["source_observation"]["status"], "source_kind": opening["source_observation"]["kind"],
        "source_segment_m": metric, "source_segment_px": pixel, "registration": registration,
        "host_wall_candidates": [], "effective_void": None, "jamb_before": None, "jamb_after": None,
        "side_a_space_id": None, "side_b_space_id": None, "semantic_status": "candidate_only",
        "traversable_status": "candidate_only", "observations": [
            "Metric segment is inverse-projected through the governing source registration.",
            "OP011 remains an unresolved glazed-interface coordinate fact.",
            "No host, void, jamb, type, space, adjacency, traversability, score, or build fact is inferred."],
        "artifacts": {"crop": {"path": str(cp.resolve()), "sha256": sha(cp), "size": list(crop.size)}, "full": {"path": str(fp.resolve()), "sha256": sha(fp), "size": list(full.size)}},
        "semantic_promotion": False, "build_authorized": False, "ready": False,
    }
    jp = OUT / "op011-evidence.json"; jp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    report = f"# OP011 independent geometry evidence\n\nSource SHA-256: `{sha(REF)}`\nEvidence JSON SHA-256: `{sha(jp)}`\n\nOP011 is a source-confirmed `glazed_interface` coordinate fact. Its metric segment is inverse-projected into the canonical pixel frame with maximum endpoint error `{registration['max_endpoint_error_px']:.6f} px` (tolerance 1 px). Host, effective void, jamb, opening subtype, bounded spaces, traversability, adjacency, score, and build authorization remain unresolved.\n"
    (OUT / "REPORT.md").write_text(report, encoding="utf-8")
    print(jp)

if __name__ == "__main__": main()
