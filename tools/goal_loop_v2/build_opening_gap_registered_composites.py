"""Register source pixels and Blender close-ups into identical metric windows."""
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
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.build_opening_gap_composites import VARIANTS, _validate_variant
from tools.goal_loop_v2.build_opening_gap_variant_plans import validate as validate_plans
from tools.goal_loop_v2.build_opening_xy_clean_evidence import EXPECTED_INCLUDED, validate as validate_evidence
from tools.goal_loop_v2.build_opening_xy_review_bundle import validate as validate_review
from tools.goal_loop_v2.registration import _apply, _inverse

SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
PLANS = ROOT / "reports/opening_gap_variant_plans_20260902/plans.json"
EVIDENCE = ROOT / "reports/opening_xy_clean_evidence_20260902/evidence.json"
REVIEW = ROOT / "reports/opening_xy_review_bundle_20260902/bundle.json"
BUILDER = ROOT / "tools/goal_loop_v2/blender_opening_gap_variant.py"
OUT = ROOT / "reports/opening_gap_registered_composites_20260903"
FAIL_CLOSED = ("xy_experiment_confirmation", "cut_confirmation", "pair_confirmation", "adjacency_confirmation", "semantic_promotion", "build_authorized", "ready")


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[row][k] * b[k][column] for k in range(3)) for column in range(3)] for row in range(3)]


def _output_to_source_matrix(metric_to_source: list[list[float]], center: list[float], scale: float, resolution: int) -> list[list[float]]:
    pixel_to_metric = [
        [scale / resolution, 0.0, center[0] - scale / 2.0],
        [0.0, -scale / resolution, center[1] + scale / 2.0],
        [0.0, 0.0, 1.0],
    ]
    return _matmul(metric_to_source, pixel_to_metric)


def _perspective_coefficients(matrix: list[list[float]]) -> tuple[float, ...]:
    normalizer = matrix[2][2]
    normalized = [[value / normalizer for value in row] for row in matrix]
    return (
        normalized[0][0], normalized[0][1], normalized[0][2],
        normalized[1][0], normalized[1][1], normalized[1][2],
        normalized[2][0], normalized[2][1],
    )


def build(*, out_dir: Path = OUT, _skip_validate: bool = False) -> dict[str, Any]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    document = validate_v21_document(json.loads(SOURCE.read_text(encoding="utf-8")))
    plans = json.loads(PLANS.read_text(encoding="utf-8"))
    validate_plans(plans)
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    validate_evidence(evidence, rebuild=False)
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    validate_review(review)
    if tuple(plans["opening_ids"]) != EXPECTED_INCLUDED or tuple(review["included_for_xy_experiment"]) != EXPECTED_INCLUDED:
        raise ValueError("registered composite coverage drift")
    raw_source_path = Path(evidence["source_image_path"])
    raw_source_hash = _file_hash(raw_source_path)
    source_image = Image.open(raw_source_path).convert("RGB")
    metric_to_source = _inverse(document["source"]["metric_registration"]["canonical_px_to_metric_3x3"])
    plan_rows = {row["opening_id"]: row for row in plans["plans"]}
    rows = []

    for opening_id in EXPECTED_INCLUDED:
        plan = plan_rows[opening_id]
        manifest, validation, artifacts = _validate_variant(opening_id, plan["variant_hash"])
        model_path = Path(artifacts["render_closeup_top"]["path"])
        model_image = Image.open(model_path).convert("RGB")
        center = [float(value) for value in validation["gap_center_m"]]
        scale = float(validation["closeup_camera_ortho_scale_m"])
        resolution = int(validation["closeup_render_resolution_px"][0])
        if validation["closeup_render_resolution_px"] != [1200, 1200] or model_image.size != (1200, 1200):
            raise ValueError("registered composite model resolution drift")
        output_to_source = _output_to_source_matrix(metric_to_source, center, scale, resolution)
        registered_source = source_image.transform(
            (resolution, resolution),
            Image.Transform.PERSPECTIVE,
            _perspective_coefficients(output_to_source),
            resample=Image.Resampling.BICUBIC,
            fillcolor="white",
        )
        source_window_path = out_dir / f"{opening_id}-registered-source.png"
        registered_source.save(source_window_path)

        canvas = Image.new("RGB", (2520, 1370), "white")
        canvas.paste(registered_source, (40, 120))
        canvas.paste(model_image, (1280, 120))
        draw = ImageDraw.Draw(canvas)
        draw.text((40, 24), f"{opening_id} | REGISTERED SOURCE | SAME METRIC WINDOW", fill="black")
        draw.text((1280, 24), "BLENDER GAP CLOSEUP | SAME METRIC WINDOW", fill="black")
        draw.text((40, 58), f"center m: {json.dumps(center)} | ortho scale: {scale:.6f} m | 1200x1200", fill="black")
        draw.text((40, 1330), "XY RESEARCH ONLY | SOURCE PIXELS RESAMPLED WITHOUT OVERLAY | NO TYPE/Z/ADJACENCY CLAIM", fill="black")
        composite_path = out_dir / f"{opening_id}-registered-composite.png"
        canvas.save(composite_path)

        mapped_center = list(_apply(output_to_source, [resolution / 2.0, resolution / 2.0]))
        expected_source_center = list(_apply(metric_to_source, center))
        center_error = ((mapped_center[0] - expected_source_center[0]) ** 2 + (mapped_center[1] - expected_source_center[1]) ** 2) ** 0.5
        if center_error > 1e-6 or _file_hash(raw_source_path) != raw_source_hash:
            raise RuntimeError("registered source transform/source-file integrity failure")
        rows.append(
            {
                "opening_id": opening_id,
                "variant_hash": plan["variant_hash"],
                "variant_manifest_sha256": _file_hash(VARIANTS / opening_id / "artifact_manifest.json"),
                "variant_validation_sha256": _file_hash(artifacts["validation"]["path"]),
                "metric_window": {"center_m": center, "ortho_scale_m": scale, "resolution_px": [resolution, resolution], "meters_per_pixel": scale / resolution},
                "output_px_to_source_px_3x3": output_to_source,
                "center_registration_error_px": center_error,
                "registered_source": {"path": str(source_window_path.resolve()), "bytes": source_window_path.stat().st_size, "sha256": _file_hash(source_window_path), "size": [resolution, resolution], "synthetic_overlay": False, "resampled_from_source": True},
                "model_closeup": {"path": str(model_path.resolve()), "bytes": model_path.stat().st_size, "sha256": _file_hash(model_path), "size": [model_image.width, model_image.height]},
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
        "schema": "opening-gap-registered-composites-v1",
        "source_structure_hash": document["structure_hash"],
        "source_image_path": str(raw_source_path.resolve()),
        "source_image_sha256": raw_source_hash,
        "plans_file_sha256": _file_hash(PLANS),
        "plans_candidate_hash": plans["candidate_hash"],
        "evidence_file_sha256": _file_hash(EVIDENCE),
        "evidence_candidate_hash": evidence["candidate_hash"],
        "review_file_sha256": _file_hash(REVIEW),
        "review_candidate_hash": review["candidate_hash"],
        "variant_builder_sha256": _file_hash(BUILDER),
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
    if actual.get("schema") != "opening-gap-registered-composites-v1" or tuple(actual.get("opening_ids", ())) != EXPECTED_INCLUDED:
        raise ValueError("registered composite schema/coverage drift")
    for key in FAIL_CLOSED:
        if actual.get(key) is not False:
            raise ValueError("registered composite bundle was promoted")
    if actual.get("score_effect") != "none":
        raise ValueError("registered composite score drift")
    for row in actual["rows"]:
        if any(row.get(key) is not False for key in FAIL_CLOSED) or row.get("score_effect") != "none":
            raise ValueError("registered composite row was promoted")
        if row["center_registration_error_px"] > 1e-6 or row["registered_source"]["size"] != [1200, 1200] or row["model_closeup"]["size"] != [1200, 1200] or row["composite"]["size"] != [2520, 1370]:
            raise ValueError("registered composite metric window drift")
        for name in ("registered_source", "model_closeup", "composite"):
            artifact = row[name]
            path = Path(artifact["path"])
            if not path.is_file() or path.stat().st_size != artifact["bytes"] or _file_hash(path) != artifact["sha256"]:
                raise ValueError("registered composite artifact drift")
        if Path(row["model_closeup"]["path"]).parent.name != row["opening_id"]:
            raise ValueError("registered composite reused wrong variant render")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("registered composite candidate hash drift")
    if rebuild:
        expected = build(out_dir=out_dir, _skip_validate=True)
        if actual != expected:
            raise ValueError("registered composite evidence/transform drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    result = build(out_dir=args.out)
    (args.out / "registered-composites.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out / "REPORT.md").write_text(
        "# Opening gap registered composites v1\n\nSource and Blender panes share the same metric center, orthographic scale, orientation, and 1200x1200 resolution. "
        "Prior independently-fitted composites remain review history and are not used for final XY selection.\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_output_to_source_matrix"]
