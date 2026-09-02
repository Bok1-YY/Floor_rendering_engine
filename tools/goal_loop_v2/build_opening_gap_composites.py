"""Build side-by-side source/raw and per-variant Blender close-up composites."""
from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.goal_loop_v2.build_opening_gap_variant_plans import validate as validate_plans
from tools.goal_loop_v2.build_opening_xy_clean_evidence import EXPECTED_INCLUDED, validate as validate_evidence
from tools.goal_loop_v2.build_opening_xy_review_bundle import validate as validate_review

PLANS = ROOT / "reports/opening_gap_variant_plans_20260902/plans.json"
EVIDENCE = ROOT / "reports/opening_xy_clean_evidence_20260902/evidence.json"
REVIEW = ROOT / "reports/opening_xy_review_bundle_20260902/bundle.json"
VARIANTS = ROOT / "artifacts/goal_loop_v2/1308/opening_xy_variants_v001"
OUT = ROOT / "reports/opening_gap_composites_20260902"
FAIL_CLOSED = ("xy_experiment_confirmation", "cut_confirmation", "pair_confirmation", "adjacency_confirmation", "semantic_promotion", "build_authorized", "ready")


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    scale = min(size[0] / image.width, size[1] / image.height)
    resized = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
    pane = Image.new("RGB", size, "white")
    pane.paste(resized, ((size[0] - resized.width) // 2, (size[1] - resized.height) // 2))
    return pane


def _validate_variant(opening_id: str, expected_variant_hash: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    manifest_path = VARIANTS / opening_id / "artifact_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "blender-opening-gap-variant-artifact-manifest-v1" or manifest.get("opening_id") != opening_id or manifest.get("variant_hash") != expected_variant_hash:
        raise ValueError("gap variant manifest identity/hash drift")
    if manifest.get("one_opening_only") is not True or manifest.get("research_only") is not True or manifest.get("not_for_construction") is not True or manifest.get("build_authorized") is not False or manifest.get("score_effect") != "none":
        raise ValueError("gap variant manifest authorization drift")
    artifacts = {item["kind"]: item for item in manifest["artifacts"]}
    for artifact in artifacts.values():
        path = Path(artifact["path"])
        if not path.is_file() or path.stat().st_size != artifact["bytes"] or _file_hash(path) != artifact["sha256"]:
            raise ValueError("gap variant artifact drift")
    validation = json.loads(Path(artifacts["validation"]["path"]).read_text(encoding="utf-8"))
    if validation.get("schema") != "blender-opening-gap-variant-validation-v2" or validation.get("opening_id") != opening_id or validation.get("variant_hash") != expected_variant_hash or validation.get("pass") is not True:
        raise ValueError("gap variant validation drift")
    if validation.get("opening_elements") != 0 or validation.get("one_opening_only") is not True or validation.get("build_authorized") is not False or validation.get("score_effect") != "none":
        raise ValueError("gap variant validation authorization drift")
    return manifest, validation, artifacts


def build(*, out_dir: Path = OUT, _skip_validate: bool = False) -> dict[str, Any]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plans = json.loads(PLANS.read_text(encoding="utf-8"))
    validate_plans(plans)
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    validate_evidence(evidence, rebuild=False)
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    validate_review(review)
    if tuple(plans["opening_ids"]) != EXPECTED_INCLUDED or tuple(review["included_for_xy_experiment"]) != EXPECTED_INCLUDED:
        raise ValueError("gap composite coverage drift")
    plan_rows = {row["opening_id"]: row for row in plans["plans"]}
    evidence_rows = {row["opening_id"]: row for row in evidence["openings"]}
    rows = []

    for opening_id in EXPECTED_INCLUDED:
        plan = plan_rows[opening_id]
        manifest, validation, artifacts = _validate_variant(opening_id, plan["variant_hash"])
        source_path = Path(evidence_rows[opening_id]["artifacts"]["raw_crop"]["path"])
        source_hash_before = _file_hash(source_path)
        model_path = Path(artifacts["render_closeup_top"]["path"])
        source_image = Image.open(source_path).convert("RGB")
        model_image = Image.open(model_path).convert("RGB")
        left = _fit(source_image, (740, 680))
        right = _fit(model_image, (740, 680))
        canvas = Image.new("RGB", (1600, 900), "white")
        canvas.paste(left, (40, 120))
        canvas.paste(right, (820, 120))
        draw = ImageDraw.Draw(canvas)
        draw.text((40, 24), f"{opening_id} | SOURCE RAW CROP (NO OVERLAY)", fill="black")
        draw.text((820, 24), "BLENDER GAP CLOSEUP TOP", fill="black")
        draw.text((40, 58), f"source local segment px: {json.dumps(evidence_rows[opening_id]['local_segment_px'])}", fill="black")
        draw.text((820, 58), f"projected XY gap width: {plan['projected_width_m']:.6f} m", fill="black")
        draw.text((40, 830), "FULL-HEIGHT XY RESEARCH ONLY | NO DOOR/WINDOW/Z/ADJACENCY CLAIM", fill="black")
        composite_path = out_dir / f"{opening_id}-composite.png"
        canvas.save(composite_path)
        if _file_hash(source_path) != source_hash_before:
            raise RuntimeError("source crop was modified while composing")
        rows.append(
            {
                "opening_id": opening_id,
                "variant_hash": plan["variant_hash"],
                "variant_manifest_path": str((VARIANTS / opening_id / "artifact_manifest.json").resolve()),
                "variant_manifest_sha256": _file_hash(VARIANTS / opening_id / "artifact_manifest.json"),
                "variant_validation_sha256": _file_hash(artifacts["validation"]["path"]),
                "source_local_segment_px": evidence_rows[opening_id]["local_segment_px"],
                "projected_gap_width_m": plan["projected_width_m"],
                "source_crop": {"path": str(source_path.resolve()), "bytes": source_path.stat().st_size, "sha256": source_hash_before, "source_pixels_untouched": True},
                "model_closeup": {"path": str(model_path.resolve()), "bytes": model_path.stat().st_size, "sha256": _file_hash(model_path), "opening_id": opening_id},
                "composite": {"path": str(composite_path.resolve()), "bytes": composite_path.stat().st_size, "sha256": _file_hash(composite_path), "size": [canvas.width, canvas.height]},
                "xy_experiment_confirmation": False,
                "cut_confirmation": False,
                "pair_confirmation": False,
                "adjacency_confirmation": False,
                "semantic_promotion": False,
                "score_effect": "none",
                "build_authorized": False,
                "ready": False,
            }
        )

    result = {
        "schema": "opening-gap-composites-v2",
        "source_structure_hash": plans["source_structure_hash"],
        "plans_file_sha256": _file_hash(PLANS),
        "plans_candidate_hash": plans["candidate_hash"],
        "evidence_file_sha256": _file_hash(EVIDENCE),
        "evidence_candidate_hash": evidence["candidate_hash"],
        "review_file_sha256": _file_hash(REVIEW),
        "review_candidate_hash": review["candidate_hash"],
        "opening_ids": list(EXPECTED_INCLUDED),
        "rows": rows,
        "xy_experiment_confirmation": False,
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
    return result if _skip_validate else validate(result, out_dir=out_dir)


def validate(candidate: Mapping[str, Any], *, out_dir: Path = OUT, rebuild: bool = True) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    if actual.get("schema") != "opening-gap-composites-v2" or tuple(actual.get("opening_ids", ())) != EXPECTED_INCLUDED:
        raise ValueError("gap composite schema/coverage drift")
    for key in FAIL_CLOSED:
        if actual.get(key) is not False:
            raise ValueError("gap composite bundle was promoted")
    if actual.get("score_effect") != "none":
        raise ValueError("gap composite score drift")
    model_paths = []
    for row in actual["rows"]:
        if any(row.get(key) is not False for key in FAIL_CLOSED) or row.get("score_effect") != "none":
            raise ValueError("gap composite row was promoted")
        for artifact_name in ("source_crop", "model_closeup", "composite"):
            artifact = row[artifact_name]
            path = Path(artifact["path"])
            if not path.is_file() or path.stat().st_size != artifact["bytes"] or _file_hash(path) != artifact["sha256"]:
                raise ValueError("gap composite artifact drift")
        if row["source_crop"].get("source_pixels_untouched") is not True or Path(row["model_closeup"]["path"]).parent.name != row["opening_id"]:
            raise ValueError("gap composite source/model identity drift")
        if row["composite"].get("size") != [1600, 900]:
            raise ValueError("gap composite canvas size drift")
        model_paths.append(row["model_closeup"]["path"])
    if len(set(model_paths)) != len(EXPECTED_INCLUDED):
        raise ValueError("gap composite reused a model render")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("gap composite candidate hash drift")
    if rebuild:
        expected = build(out_dir=out_dir, _skip_validate=True)
        if actual != expected:
            raise ValueError("gap composite evidence/variant drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    result = build(out_dir=args.out)
    (args.out / "composites.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out / "REPORT.md").write_text(
        "# Opening gap source/model composites v2\n\nEach of the nine source raw crops is paired with its own isolated Blender close-up. "
        "No Layer-1 render is reused. All semantics, cuts, pairs, adjacency, score, and build fields remain fail-closed.\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate"]
