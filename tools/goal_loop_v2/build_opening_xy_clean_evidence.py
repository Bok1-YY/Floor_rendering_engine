"""Build byte-exact, no-overlay XY evidence for every cuttable opening candidate."""
from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.candidate_opening_cut_impact import validate_candidate_opening_cut_impact
from tools.goal_loop_v2.registration import _apply, _inverse, validate_pixel_metric_segment

SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
RAW_IMAGE = ROOT / "data/goal_loop_v2/references/1308/canonical-raw-portrait.png"
CUT_MATRIX = ROOT / "reports/candidate_opening_cut_impact_20260902/candidate-opening-cut-impact.json"
OUT = ROOT / "reports/opening_xy_clean_evidence_20260902"
EXPECTED_INCLUDED = ("OP001", "OP002", "OP003", "OP004", "OP006", "OP007", "OP008", "OP009", "OP010")
EXPECTED_ACTIVE_EXCLUDED = ("OP005", "OP011", "PORTAL-WB011-WB006-01")
FAIL_CLOSED = ("cut_confirmation", "pair_confirmation", "adjacency_confirmation", "semantic_promotion", "build_authorized", "ready")


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _artifact(path: Path, role: str, source_pixels_untouched: bool) -> dict[str, Any]:
    return {
        "role": role,
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": _file_hash(path),
        "source_pixels_untouched": source_pixels_untouched,
    }


def _clearance(segment: list[list[float]], rectangle: list[int], stroke_width: int) -> float:
    xs = [point[0] for point in segment]
    ys = [point[1] for point in segment]
    left, top, right, bottom = rectangle
    return min(min(xs) - left, right - max(xs), min(ys) - top, bottom - max(ys)) - stroke_width


def build(
    document: Mapping[str, Any] | None = None,
    *,
    source_path: Path = SOURCE,
    raw_image_path: Path = RAW_IMAGE,
    cut_matrix_path: Path = CUT_MATRIX,
    out_dir: Path = OUT,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    source_path, raw_image_path, cut_matrix_path, out_dir = map(Path, (source_path, raw_image_path, cut_matrix_path, out_dir))
    doc = validate_v21_document(document or json.loads(source_path.read_text(encoding="utf-8")))
    matrix = json.loads(cut_matrix_path.read_text(encoding="utf-8"))
    validate_candidate_opening_cut_impact(doc, matrix)
    image = Image.open(raw_image_path).convert("RGB")
    transform = doc["source"]["metric_registration"]["canonical_px_to_metric_3x3"]
    inverse = _inverse(transform)
    out_dir.mkdir(parents=True, exist_ok=True)
    openings = []
    active_exclusions = []

    for matrix_row in matrix["openings"]:
        opening_id = matrix_row["opening_id"]
        if not matrix_row["cuttable"]:
            active_exclusions.append(
                {
                    "opening_id": opening_id,
                    "exclusion_kind": "active_matrix_not_cuttable",
                    "classification": matrix_row["classification"],
                    "authority": matrix_row["authority"],
                }
            )
            continue
        segment_m = deepcopy(matrix_row["segment_m"])
        segment_px = [list(_apply(inverse, point)) for point in segment_m]
        registration = validate_pixel_metric_segment(transform, segment_px, segment_m, 1.0)
        crop_padding = 120
        crop_box = [
            max(0, math.floor(min(point[0] for point in segment_px) - crop_padding)),
            max(0, math.floor(min(point[1] for point in segment_px) - crop_padding)),
            min(image.width, math.ceil(max(point[0] for point in segment_px) + crop_padding)),
            min(image.height, math.ceil(max(point[1] for point in segment_px) + crop_padding)),
        ]
        raw_crop = image.crop(tuple(crop_box))
        raw_crop_path = out_dir / f"{opening_id}-raw-crop.png"
        raw_crop.save(raw_crop_path)

        locator_padding = 40
        stroke_width = 4
        rectangle = [
            max(0, math.floor(min(point[0] for point in segment_px) - locator_padding)),
            max(0, math.floor(min(point[1] for point in segment_px) - locator_padding)),
            min(image.width - 1, math.ceil(max(point[0] for point in segment_px) + locator_padding)),
            min(image.height - 1, math.ceil(max(point[1] for point in segment_px) + locator_padding)),
        ]
        clearance = _clearance(segment_px, rectangle, stroke_width)
        if clearance < 30:
            raise ValueError(f"locator clearance below 30 px for {opening_id}: {clearance}")
        locator = image.copy()
        draw = ImageDraw.Draw(locator)
        draw.rectangle(tuple(rectangle), outline=(255, 200, 0), width=stroke_width)
        draw.text((rectangle[0], max(0, rectangle[1] - 24)), opening_id, fill=(255, 200, 0), stroke_width=2, stroke_fill=(0, 0, 0))
        locator_path = out_dir / f"{opening_id}-locator.png"
        locator.save(locator_path)

        openings.append(
            {
                "opening_id": opening_id,
                "authority": matrix_row["authority"],
                "classification": matrix_row["classification"],
                "matrix_cuttable": matrix_row["cuttable"],
                "host_atom_id": matrix_row["host_atom_id"],
                "segment_m": segment_m,
                "segment_px": segment_px,
                "registration": registration,
                "crop_box_px": crop_box,
                "local_segment_px": [[point[0] - crop_box[0], point[1] - crop_box[1]] for point in segment_px],
                "locator_geometry": {"rectangle_px": rectangle, "stroke_width_px": stroke_width, "minimum_clearance_px": clearance},
                "source_pixels_untouched": True,
                "artifacts": {
                    "locator": _artifact(locator_path, "locator_navigation_only", False),
                    "raw_crop": _artifact(raw_crop_path, "byte_exact_source_crop", True),
                },
                "cut_confirmation": False,
                "pair_confirmation": False,
                "adjacency_confirmation": False,
                "semantic_promotion": False,
                "score_effect": "none",
                "build_authorized": False,
            }
        )

    exclusions = sorted(active_exclusions, key=lambda row: row["opening_id"])
    exclusions.append(
        {
            "opening_id": "OP012",
            "exclusion_kind": "historical_quarantine_not_active_source_opening",
            "classification": "quarantined_review_conflict",
            "authority": "rejected_history",
        }
    )
    result = {
        "schema": "opening-xy-clean-evidence-v2",
        "source_document_path": str(source_path.resolve()),
        "source_document_sha256": _file_hash(source_path),
        "source_structure_hash": doc["structure_hash"],
        "source_image_path": str(raw_image_path.resolve()),
        "source_image_sha256": _file_hash(raw_image_path),
        "cut_matrix_path": str(cut_matrix_path.resolve()),
        "cut_matrix_file_sha256": _file_hash(cut_matrix_path),
        "cut_matrix_candidate_hash": matrix["candidate_hash"],
        "opening_ids": [row["opening_id"] for row in openings],
        "exclusions": exclusions,
        "openings": openings,
        "source_pixels_untouched": True,
        "cut_confirmation": False,
        "pair_confirmation": False,
        "adjacency_confirmation": False,
        "semantic_promotion": False,
        "score_effect": "none",
        "build_authorized": False,
        "ready": False,
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _candidate_hash({key: value for key, value in result.items() if key != "candidate_hash"})
    return result if _skip_validate else validate(result, doc, source_path=source_path, raw_image_path=raw_image_path, cut_matrix_path=cut_matrix_path, out_dir=out_dir)


def validate(
    candidate: Mapping[str, Any],
    document: Mapping[str, Any] | None = None,
    *,
    source_path: Path = SOURCE,
    raw_image_path: Path = RAW_IMAGE,
    cut_matrix_path: Path = CUT_MATRIX,
    out_dir: Path = OUT,
    rebuild: bool = True,
) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    if actual.get("schema") != "opening-xy-clean-evidence-v2":
        raise ValueError("opening XY evidence schema drift")
    if tuple(actual.get("opening_ids", ())) != EXPECTED_INCLUDED:
        raise ValueError("opening XY included coverage drift")
    active_excluded = tuple(row["opening_id"] for row in actual.get("exclusions", ()) if row.get("exclusion_kind") == "active_matrix_not_cuttable")
    if active_excluded != EXPECTED_ACTIVE_EXCLUDED or actual["exclusions"][-1].get("opening_id") != "OP012":
        raise ValueError("opening XY exclusion coverage drift")
    for key in FAIL_CLOSED:
        if actual.get(key) is not False:
            raise ValueError("opening XY evidence was promoted")
    if actual.get("score_effect") != "none" or actual.get("source_pixels_untouched") is not True:
        raise ValueError("opening XY fail-closed/source-pixel drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("opening XY candidate hash drift")

    source_image = Image.open(raw_image_path).convert("RGB")
    for row in actual["openings"]:
        if row.get("matrix_cuttable") is not True or row.get("source_pixels_untouched") is not True:
            raise ValueError("opening XY row authority/pixel drift")
        if any(row.get(key) is not False for key in ("cut_confirmation", "pair_confirmation", "adjacency_confirmation", "semantic_promotion", "build_authorized")):
            raise ValueError("opening XY row was promoted")
        for artifact in row["artifacts"].values():
            path = Path(artifact["path"])
            if not path.is_file() or path.stat().st_size != artifact["bytes"] or _file_hash(path) != artifact["sha256"]:
                raise ValueError("opening XY artifact drift")
        crop = Image.open(row["artifacts"]["raw_crop"]["path"]).convert("RGB")
        expected_crop = source_image.crop(tuple(row["crop_box_px"]))
        if crop.size != expected_crop.size or crop.tobytes() != expected_crop.tobytes():
            raise ValueError("opening XY raw crop is not byte-exact source pixels")
        clearance = _clearance(row["segment_px"], row["locator_geometry"]["rectangle_px"], row["locator_geometry"]["stroke_width_px"])
        if abs(clearance - row["locator_geometry"]["minimum_clearance_px"]) > 1e-6 or clearance < 30:
            raise ValueError("opening XY locator clearance drift")

    if rebuild:
        expected = build(document, source_path=source_path, raw_image_path=raw_image_path, cut_matrix_path=cut_matrix_path, out_dir=out_dir, _skip_validate=True)
        if actual != expected:
            raise ValueError("opening XY source/matrix/evidence drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--raw-image", type=Path, default=RAW_IMAGE)
    parser.add_argument("--cut-matrix", type=Path, default=CUT_MATRIX)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    document = json.loads(args.source.read_text(encoding="utf-8"))
    result = build(document, source_path=args.source, raw_image_path=args.raw_image, cut_matrix_path=args.cut_matrix, out_dir=args.out)
    (args.out / "evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out / "REPORT.md").write_text(
        "# Opening XY clean evidence v2\n\nNine cuttable candidate segments have byte-exact raw crops and separate locator images. "
        "All semantics, cuts, pairs, adjacency, score, and build fields remain fail-closed.\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "EXPECTED_INCLUDED"]
