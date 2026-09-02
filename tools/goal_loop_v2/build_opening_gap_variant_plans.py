"""Build one-opening-at-a-time, full-height XY gap experiment plans."""
from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.build_opening_xy_clean_evidence import EXPECTED_INCLUDED, validate as validate_evidence
from tools.goal_loop_v2.build_opening_xy_review_bundle import validate as validate_review
from tools.goal_loop_v2.candidate_opening_cut_impact import validate_candidate_opening_cut_impact

SOURCE = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
EVIDENCE = ROOT / "reports/opening_xy_clean_evidence_20260902/evidence.json"
REVIEW = ROOT / "reports/opening_xy_review_bundle_20260902/bundle.json"
CUT_MATRIX = ROOT / "reports/candidate_opening_cut_impact_20260902/candidate-opening-cut-impact.json"
LAYER1_MANIFEST = ROOT / "artifacts/goal_loop_v2/1308/research_source_faithful_v001/artifact_manifest.json"
OUT = ROOT / "reports/opening_gap_variant_plans_20260902"
EXPECTED_EXCLUDED = ("OP005", "OP011", "PORTAL-WB011-WB006-01", "OP012")
FAIL_CLOSED = ("xy_experiment_confirmation", "cut_confirmation", "pair_confirmation", "adjacency_confirmation", "semantic_promotion", "build_authorized", "ready")


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _validate_layer1_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    manifest = deepcopy(dict(value))
    if manifest.get("schema") != "blender-research-wall-layer-artifact-manifest-v1" or manifest.get("wall_object_count") != 35 or manifest.get("opening_cuts") != 0:
        raise ValueError("Layer1 manifest identity/count drift")
    if manifest.get("research_only") is not True or manifest.get("not_for_construction") is not True or manifest.get("formal_build_authorized") is not False:
        raise ValueError("Layer1 manifest authorization drift")
    for artifact in manifest.get("artifacts", []):
        path = Path(artifact["path"])
        if not path.is_file() or path.stat().st_size != artifact["bytes"] or _file_hash(path) != artifact["sha256"]:
            raise ValueError("Layer1 artifact drift")
    return manifest


def _project_gap(segment: list[list[float]], host: Mapping[str, Any]) -> dict[str, Any]:
    a, b = host["centerline_m"]
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    denominator = length * length
    parameters = [((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / denominator for point in segment]
    projected = [[a[0] + value * dx, a[1] + value * dy] for value in parameters]
    signed_offsets = [((point[0] - a[0]) * dy - (point[1] - a[1]) * dx) / length for point in segment]
    return {
        "host_parameters": parameters,
        "projected_segment_m": projected,
        "signed_perpendicular_offsets_m": signed_offsets,
        "maximum_perpendicular_offset_m": max(abs(value) for value in signed_offsets),
        "projected_width_m": math.dist(*projected),
        "source_width_m": math.dist(*segment),
        "host_length_m": length,
    }


def build(*, _skip_validate: bool = False) -> dict[str, Any]:
    document = validate_v21_document(json.loads(SOURCE.read_text(encoding="utf-8")))
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    validate_evidence(evidence, rebuild=False)
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    validate_review(review)
    matrix = json.loads(CUT_MATRIX.read_text(encoding="utf-8"))
    validate_candidate_opening_cut_impact(document, matrix)
    layer1 = _validate_layer1_manifest(json.loads(LAYER1_MANIFEST.read_text(encoding="utf-8")))

    included = tuple(review["included_for_xy_experiment"])
    if included != EXPECTED_INCLUDED:
        raise ValueError("gap variant included coverage drift")
    exclusions = tuple(row["opening_id"] for row in evidence["exclusions"])
    if exclusions != EXPECTED_EXCLUDED:
        raise ValueError("gap variant excluded coverage drift")
    atoms = {atom["id"]: atom for atom in document["wall_graph"]["atoms"]}
    matrix_rows = {row["opening_id"]: row for row in matrix["openings"]}
    plans = []

    for opening_id in included:
        matrix_row = matrix_rows[opening_id]
        if matrix_row["cuttable"] is not True:
            raise ValueError("review included a non-cuttable matrix row")
        host = atoms[matrix_row["host_atom_id"]]
        source_segment = deepcopy(matrix_row["segment_m"])
        projection = _project_gap(source_segment, host)
        parameters = projection["host_parameters"]
        if min(parameters) < -1e-9 or max(parameters) > 1 + 1e-9:
            raise ValueError(f"opening projection leaves host segment: {opening_id}")
        half_thickness = float(host["thickness_m"]) / 2.0
        if projection["maximum_perpendicular_offset_m"] > half_thickness + 1e-6:
            raise ValueError(f"opening leaves host wall solid: {opening_id}")
        projection_mode = "centerline_collinear" if projection["maximum_perpendicular_offset_m"] <= 1e-6 else "orthogonal_projection_within_wall_solid"
        if opening_id != "OP001" and projection_mode != "centerline_collinear":
            raise ValueError(f"unexpected non-collinear opening: {opening_id}")
        if opening_id == "OP001" and projection_mode != "orthogonal_projection_within_wall_solid":
            raise ValueError("OP001 projection sensitivity was lost")

        lo, hi = sorted(parameters)
        remaining = []
        for start, end in ((0.0, lo), (hi, 1.0)):
            if (end - start) * projection["host_length_m"] > 1e-6:
                a, b = host["centerline_m"]
                dx, dy = b[0] - a[0], b[1] - a[1]
                remaining.append(
                    {
                        "host_parameter_interval": [start, end],
                        "centerline_m": [[a[0] + dx * start, a[1] + dy * start], [a[0] + dx * end, a[1] + dy * end]],
                        "length_m": (end - start) * projection["host_length_m"],
                    }
                )
        plan = {
            "variant_id": f"GAP-{opening_id}-ONLY",
            "opening_id": opening_id,
            "host_atom_id": host["id"],
            "host_centerline_m": deepcopy(host["centerline_m"]),
            "host_thickness_m": float(host["thickness_m"]),
            "host_half_thickness_m": half_thickness,
            "source_gap_segment_m": source_segment,
            "projection_mode": projection_mode,
            **projection,
            "projection_within_host_segment": True,
            "projection_within_wall_solid": True,
            "projected_vs_source_width_delta_m": abs(projection["projected_width_m"] - projection["source_width_m"]),
            "before_support_m": lo * projection["host_length_m"],
            "after_support_m": (1 - hi) * projection["host_length_m"],
            "remaining_host_pieces": remaining,
            "short_residual_piece_count_below_0_05m": sum(piece["length_m"] < 0.05 for piece in remaining),
            "expected_wall_object_count": 34 + len(remaining),
            "gap_z_policy": "full_height_visualization_only",
            "source_segment_preserved_as_provenance": True,
            "source_correction_authorized": False,
            "xy_experiment_confirmation": False,
            "cut_confirmation": False,
            "pair_confirmation": False,
            "adjacency_confirmation": False,
            "semantic_promotion": False,
            "score_effect": "none",
            "build_authorized": False,
            "ready": False,
            "variant_hash": "0" * 64,
        }
        plan["variant_hash"] = _candidate_hash({key: value for key, value in plan.items() if key != "variant_hash"})
        plans.append(plan)

    result = {
        "schema": "opening-gap-variant-plans-v2",
        "source_structure_hash": document["structure_hash"],
        "source_document_sha256": _file_hash(SOURCE),
        "evidence_file_sha256": _file_hash(EVIDENCE),
        "evidence_candidate_hash": evidence["candidate_hash"],
        "review_file_sha256": _file_hash(REVIEW),
        "review_candidate_hash": review["candidate_hash"],
        "cut_matrix_file_sha256": _file_hash(CUT_MATRIX),
        "cut_matrix_candidate_hash": matrix["candidate_hash"],
        "layer1_manifest_file_sha256": _file_hash(LAYER1_MANIFEST),
        "layer1_blender_source_sha256": next(item["sha256"] for item in layer1["artifacts"] if item["kind"] == "blender_source"),
        "opening_ids": list(included),
        "excluded_opening_ids": list(exclusions),
        "plans": plans,
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
    return result if _skip_validate else validate(result)


def validate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(_skip_validate=True)
    if actual != expected:
        raise ValueError("gap variant plans evidence/derivation drift")
    if actual.get("opening_ids") != list(EXPECTED_INCLUDED) or actual.get("excluded_opening_ids") != list(EXPECTED_EXCLUDED):
        raise ValueError("gap variant plan coverage drift")
    for key in FAIL_CLOSED:
        if actual.get(key) is not False:
            raise ValueError("gap variant plan bundle was promoted")
    if actual.get("score_effect") != "none":
        raise ValueError("gap variant score drift")
    for plan in actual["plans"]:
        if any(plan.get(key) is not False for key in FAIL_CLOSED) or plan.get("score_effect") != "none":
            raise ValueError("gap variant plan was promoted")
        payload = {key: value for key, value in plan.items() if key != "variant_hash"}
        if plan["variant_hash"] != _candidate_hash(payload):
            raise ValueError("gap variant hash drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("gap variant bundle hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT / "plans.json")
    args = parser.parse_args(argv)
    result = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output.parent / "REPORT.md").write_text(
        "# Opening gap variant plans v2\n\nNine one-opening-only, full-height XY visualization plans were derived. "
        "OP001 uses an explicit within-wall orthogonal projection; the source segment remains provenance. "
        "All source/semantic/cut/pair/adjacency/score/build fields remain fail-closed.\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_project_gap"]
