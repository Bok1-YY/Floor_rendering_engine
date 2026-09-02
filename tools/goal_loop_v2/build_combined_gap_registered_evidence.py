"""Register the canonical source and combined Blender renders to identical metric windows."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.fastloop_research.v21_contract import validate_v21_document
from tools.goal_loop_v2.build_combined_gap_plan import validate as validate_combined_plan
from tools.goal_loop_v2.registration import _apply, _inverse

SOURCE_DOCUMENT = ROOT / "data/goal_loop_v2/references/1308/reference-coordinate-authorized-v21.json"
SOURCE_IMAGE = ROOT / "data/goal_loop_v2/references/1308/canonical-raw-portrait.png"
PLAN = ROOT / "reports/combined_gap_plan_20260903/plan.json"
COMBINED_DIR = ROOT / "artifacts/goal_loop_v2/1308/research_combined_xy_gap_v001"
COMBINED_MANIFEST = COMBINED_DIR / "artifact_manifest.json"
COMBINED_VALIDATION = COMBINED_DIR / "validation.json"
COMBINED_BUILDER = ROOT / "tools/goal_loop_v2/blender_combined_gap_layer.py"
OUT = ROOT / "reports/combined_gap_registered_evidence_20260903"
EXPECTED_IDS = ("OP001", "OP002", "OP003", "OP004", "OP006", "OP007", "OP008", "OP009", "OP010")
EXPECTED_EXCLUDED = ("OP005", "OP011", "PORTAL-WB011-WB006-01", "OP012")
FAIL_CLOSED = (
    "source_correction_authorized",
    "xy_experiment_confirmation",
    "cut_confirmation",
    "pair_confirmation",
    "adjacency_confirmation",
    "semantic_promotion",
    "build_authorized",
    "ready",
)
REQUIRED_ARTIFACT_KINDS = {
    "checkpoint_blend",
    "blender_source",
    "portable_glb",
    "render_top",
    "render_northeast",
    "render_northwest",
    "validation",
    *(f"render_gap_closeup_{opening_id}" for opening_id in EXPECTED_IDS),
}


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT)).replace("\\", "/")


def _matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [
            sum(a[row][index] * b[index][column] for index in range(3))
            for column in range(3)
        ]
        for row in range(3)
    ]


def _output_to_source_matrix(
    metric_to_source: list[list[float]],
    center: list[float],
    scale: float,
    resolution: int,
) -> list[list[float]]:
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
        normalized[0][0],
        normalized[0][1],
        normalized[0][2],
        normalized[1][0],
        normalized[1][1],
        normalized[1][2],
        normalized[2][0],
        normalized[2][1],
    )


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        Path(r"C:/Windows/Fonts/arial.ttf"),
        Path(r"C:/Windows/Fonts/msyh.ttc"),
    ):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _image_binding(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        size = list(image.size)
    return {
        "path": _relative(path),
        "bytes": path.stat().st_size,
        "sha256": _file_hash(path),
        "size": size,
    }


def _output_artifact(path: Path, out_dir: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        size = list(image.size)
    return {
        "relative_path": str(path.relative_to(out_dir)).replace("\\", "/"),
        "bytes": path.stat().st_size,
        "sha256": _file_hash(path),
        "size": size,
    }


def _assert_fail_closed(value: Mapping[str, Any], *, context: str) -> None:
    for key in FAIL_CLOSED:
        if value.get(key) is not False:
            raise ValueError(f"{context} promoted or omitted {key}")
    if value.get("score_effect") != "none":
        raise ValueError(f"{context} score drift")


def _validate_combined_artifacts(
    manifest_path: Path,
    validation_path: Path,
    combined_dir: Path,
    *,
    plan: Mapping[str, Any],
    source_document_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = _read_json(manifest_path)
    validation = _read_json(validation_path)
    _assert_fail_closed(manifest, context="combined manifest")
    _assert_fail_closed(validation, context="combined validation")
    if (
        manifest.get("schema") != "blender-combined-gap-layer-artifact-manifest-v1"
        or manifest.get("branch_id") != "1308-combined-xy-gap-research-v001"
        or manifest.get("plan_candidate_hash") != plan["candidate_hash"]
        or manifest.get("plan_file_sha256") != _file_hash(PLAN)
        or manifest.get("source_structure_hash") != plan["source_structure_hash"]
        or manifest.get("source_document_sha256") != source_document_sha256
        or manifest.get("wall_piece_count") != 43
        or manifest.get("untouched_atom_count") != 26
        or manifest.get("host_atom_count") != 9
        or manifest.get("host_piece_count") != 17
        or manifest.get("opening_elements") != 0
        or manifest.get("wall_height_m") != 2.8
        or manifest.get("artifact_path_mode") != "relative_to_manifest"
        or manifest.get("evidence_plan_portable") is not False
        or manifest.get("artifact_files_relocatable_with_manifest") is not True
        or manifest.get("research_only") is not True
        or manifest.get("not_for_construction") is not True
    ):
        raise ValueError("combined artifact manifest drift")
    rows = manifest.get("artifacts")
    if not isinstance(rows, list):
        raise ValueError("combined artifact rows missing")
    artifacts = {}
    for row in rows:
        kind = row.get("kind")
        relative_path = row.get("relative_path")
        if kind in artifacts or not isinstance(relative_path, str) or Path(relative_path).name != relative_path:
            raise ValueError("combined artifact kind/path drift")
        path = combined_dir / relative_path
        if not path.is_file() or path.stat().st_size != row.get("bytes") or _file_hash(path) != row.get("sha256"):
            raise ValueError(f"combined artifact bytes/hash drift: {kind}")
        artifacts[str(kind)] = dict(row)
    if set(artifacts) != REQUIRED_ARTIFACT_KINDS:
        raise ValueError("combined artifact coverage drift")
    if (combined_dir / artifacts["validation"]["relative_path"]).resolve() != validation_path.resolve():
        raise ValueError("combined validation binding drift")
    if (
        validation.get("schema") != "blender-combined-gap-layer-validation-v1"
        or validation.get("branch_id") != manifest["branch_id"]
        or validation.get("plan_candidate_hash") != plan["candidate_hash"]
        or validation.get("plan_file_sha256") != _file_hash(PLAN)
        or validation.get("source_structure_hash") != plan["source_structure_hash"]
        or validation.get("source_document_sha256") != source_document_sha256
        or validation.get("expected_wall_piece_count") != 43
        or validation.get("actual_wall_piece_count") != 43
        or validation.get("actual_untouched_piece_count") != 26
        or validation.get("actual_host_piece_count") != 17
        or validation.get("included_opening_ids") != list(EXPECTED_IDS)
        or validation.get("excluded_opening_ids") != list(EXPECTED_EXCLUDED)
        or validation.get("non_host_count_errors") != []
        or validation.get("host_count_errors") != []
        or validation.get("topology_errors") != []
        or validation.get("property_errors") != []
        or validation.get("gap_overlap_errors") != []
        or validation.get("opening_elements") != 0
        or validation.get("validation_camera_count") != 4
        or validation.get("gap_z_policy") != "full_height_visualization_only"
        or validation.get("evidence_plan_portable") is not False
        or validation.get("research_only") is not True
        or validation.get("not_for_construction") is not True
        or validation.get("pass") is not True
    ):
        raise ValueError("combined artifact validation drift")
    return manifest, validation, artifacts


def _register_source(
    source_image: Image.Image,
    metric_to_source: list[list[float]],
    window: Mapping[str, Any],
) -> tuple[Image.Image, list[list[float]], float]:
    center = [float(value) for value in window["center_m"]]
    scale = float(window["ortho_scale_m"])
    resolution = list(window["resolution_px"])
    if resolution != [1200, 1200] or scale <= 0:
        raise ValueError("metric window drift")
    matrix = _output_to_source_matrix(metric_to_source, center, scale, resolution[0])
    registered = source_image.transform(
        tuple(resolution),
        Image.Transform.PERSPECTIVE,
        _perspective_coefficients(matrix),
        resample=Image.Resampling.BICUBIC,
        fillcolor="white",
    )
    mapped_center = list(_apply(matrix, [resolution[0] / 2.0, resolution[1] / 2.0]))
    expected_center = list(_apply(metric_to_source, center))
    center_error = math.dist(mapped_center, expected_center)
    return registered, matrix, center_error


def _metric_to_output(point: list[float], window: Mapping[str, Any]) -> tuple[float, float]:
    center = window["center_m"]
    scale = float(window["ortho_scale_m"])
    resolution = int(window["resolution_px"][0])
    return (
        (float(point[0]) - (float(center[0]) - scale / 2.0)) * resolution / scale,
        (float(center[1]) + scale / 2.0 - float(point[1])) * resolution / scale,
    )


def _full_locator(
    registered_source: Image.Image,
    plan: Mapping[str, Any],
    window: Mapping[str, Any],
) -> Image.Image:
    locator = registered_source.copy()
    draw = ImageDraw.Draw(locator)
    font = _font(24)
    small = _font(18)
    for index, row in enumerate(plan["plans"]):
        source_a, source_b = (_metric_to_output(point, window) for point in row["source_gap_segment_m"])
        projected_a, projected_b = (_metric_to_output(point, window) for point in row["projected_segment_m"])
        draw.line((source_a, source_b), fill=(0, 120, 255), width=8)
        draw.line((projected_a, projected_b), fill=(235, 40, 160), width=3)
        midpoint = ((projected_a[0] + projected_b[0]) / 2.0, (projected_a[1] + projected_b[1]) / 2.0)
        offset_x = 8 if index % 2 == 0 else -72
        draw.rectangle(
            (midpoint[0] + offset_x - 3, midpoint[1] - 26, midpoint[0] + offset_x + 66, midpoint[1] + 2),
            fill="white",
        )
        draw.text((midpoint[0] + offset_x, midpoint[1] - 24), row["opening_id"], fill=(180, 0, 100), font=small)
    draw.rectangle((15, 15, 725, 92), fill="white", outline="black", width=2)
    draw.line((30, 42, 120, 42), fill=(0, 120, 255), width=8)
    draw.text((135, 27), "source segment", fill="black", font=font)
    draw.line((370, 42, 460, 42), fill=(235, 40, 160), width=4)
    draw.text((475, 27), "projected model gap", fill="black", font=font)
    draw.text((30, 62), "XY CANDIDATES ONLY / NO OPENING SEMANTICS", fill=(150, 0, 0), font=small)
    return locator


def _full_composite(
    registered_source: Image.Image,
    model: Image.Image,
    locator: Image.Image,
) -> Image.Image:
    canvas = Image.new("RGB", (3760, 1370), "white")
    canvas.paste(registered_source, (40, 120))
    canvas.paste(model, (1280, 120))
    canvas.paste(locator, (2520, 120))
    draw = ImageDraw.Draw(canvas)
    title = _font(30)
    body = _font(22)
    draw.text((40, 24), "REGISTERED SOURCE / CLEAN", fill="black", font=title)
    draw.text((1280, 24), "COMBINED BLENDER TOP / CLEAN", fill="black", font=title)
    draw.text((2520, 24), "SOURCE LOCATOR / IDS ONLY", fill="black", font=title)
    draw.text(
        (40, 62),
        "SAME CENTER + ORTHO SCALE + 1200x1200 / 43 WALL PIECES / 9 XY GAP CANDIDATES",
        fill=(120, 0, 0),
        font=body,
    )
    draw.text(
        (40, 1330),
        "NO DOOR/WINDOW TYPE / NO Z-HEAD-SILL / NO TRAVERSABILITY-ADJACENCY / NOT FOR CONSTRUCTION",
        fill=(140, 0, 0),
        font=body,
    )
    return canvas


def _local_composite(
    opening_id: str,
    registered_source: Image.Image,
    model: Image.Image,
    window: Mapping[str, Any],
) -> Image.Image:
    canvas = Image.new("RGB", (2520, 1370), "white")
    canvas.paste(registered_source, (40, 120))
    canvas.paste(model, (1280, 120))
    draw = ImageDraw.Draw(canvas)
    title = _font(30)
    body = _font(22)
    draw.text((40, 24), f"{opening_id} / REGISTERED SOURCE / CLEAN", fill="black", font=title)
    draw.text((1280, 24), f"{opening_id} / COMBINED MODEL CLOSEUP", fill="black", font=title)
    draw.text(
        (40, 62),
        f"same metric window center={json.dumps(window['center_m'])} scale={float(window['ortho_scale_m']):.6f}m",
        fill="black",
        font=body,
    )
    draw.text(
        (40, 1330),
        "XY GAP RESEARCH ONLY / NO TYPE / NO Z / NO ROOM PAIR / NO ADJACENCY / NOT FOR CONSTRUCTION",
        fill=(140, 0, 0),
        font=body,
    )
    return canvas


def build(
    *,
    out_dir: Path = OUT,
    source_document_path: Path = SOURCE_DOCUMENT,
    source_image_path: Path = SOURCE_IMAGE,
    plan_path: Path = PLAN,
    combined_dir: Path = COMBINED_DIR,
    combined_manifest_path: Path = COMBINED_MANIFEST,
    combined_validation_path: Path = COMBINED_VALIDATION,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    out_dir = Path(out_dir)
    source_document_path = Path(source_document_path)
    source_image_path = Path(source_image_path)
    plan_path = Path(plan_path)
    combined_dir = Path(combined_dir)
    combined_manifest_path = Path(combined_manifest_path)
    combined_validation_path = Path(combined_validation_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    document = validate_v21_document(_read_json(source_document_path))
    plan = _read_json(plan_path)
    validate_combined_plan(plan)
    if plan["source_structure_hash"] != document["structure_hash"] or plan["source_document_sha256"] != _file_hash(source_document_path):
        raise ValueError("registered evidence source/plan drift")
    canonical = document["source"]["canonical"]
    if (
        canonical["file_sha256"] != _file_hash(source_image_path)
        or canonical["size_px"] != list(Image.open(source_image_path).size)
    ):
        raise ValueError("registered evidence canonical image drift")
    manifest, validation, artifacts = _validate_combined_artifacts(
        combined_manifest_path,
        combined_validation_path,
        combined_dir,
        plan=plan,
        source_document_sha256=_file_hash(source_document_path),
    )

    source_image = Image.open(source_image_path).convert("RGB")
    metric_to_source = _inverse(document["source"]["metric_registration"]["canonical_px_to_metric_3x3"])
    full_window = deepcopy(validation["full_top_metric_window"])
    full_registered, full_matrix, full_center_error = _register_source(source_image, metric_to_source, full_window)
    if full_center_error > 1e-6:
        raise RuntimeError("full-plan center registration error")
    full_source_path = out_dir / "full-registered-source-clean.png"
    full_registered.save(full_source_path)
    model_top_path = combined_dir / artifacts["render_top"]["relative_path"]
    model_top = Image.open(model_top_path).convert("RGB")
    if model_top.size != (1200, 1200):
        raise ValueError("combined full top render size drift")
    locator = _full_locator(full_registered, plan, full_window)
    locator_path = out_dir / "full-source-gap-locator.png"
    locator.save(locator_path)
    full_composite = _full_composite(full_registered, model_top, locator)
    full_composite_path = out_dir / "full-registered-composite.png"
    full_composite.save(full_composite_path)

    plan_rows = {row["opening_id"]: row for row in plan["plans"]}
    rows = []
    for opening_id in EXPECTED_IDS:
        window = deepcopy(validation["gap_closeup_metric_windows"][opening_id])
        plan_row = plan_rows[opening_id]
        expected_center = [
            (float(plan_row["projected_segment_m"][0][0]) + float(plan_row["projected_segment_m"][1][0])) / 2.0,
            (float(plan_row["projected_segment_m"][0][1]) + float(plan_row["projected_segment_m"][1][1])) / 2.0,
        ]
        expected_scale = max(2.4, float(plan_row["projected_width_m"]) * 2.5)
        if (
            any(not math.isclose(float(a), float(b), abs_tol=1e-9) for a, b in zip(window["center_m"], expected_center))
            or not math.isclose(float(window["ortho_scale_m"]), expected_scale, abs_tol=1e-9)
            or window["resolution_px"] != [1200, 1200]
        ):
            raise ValueError(f"{opening_id} combined closeup window drift")
        registered, matrix, center_error = _register_source(source_image, metric_to_source, window)
        if center_error > 1e-6:
            raise RuntimeError(f"{opening_id} center registration error")
        source_window_path = out_dir / f"{opening_id}-registered-source-clean.png"
        registered.save(source_window_path)
        kind = f"render_gap_closeup_{opening_id}"
        model_path = combined_dir / artifacts[kind]["relative_path"]
        model = Image.open(model_path).convert("RGB")
        if model.size != (1200, 1200) or model_path.parent.resolve() != combined_dir.resolve():
            raise ValueError(f"{opening_id} combined closeup binding drift")
        composite = _local_composite(opening_id, registered, model, window)
        composite_path = out_dir / f"{opening_id}-registered-composite.png"
        composite.save(composite_path)
        rows.append(
            {
                "opening_id": opening_id,
                "variant_hash": plan_row["variant_hash"],
                "metric_window": window,
                "output_px_to_source_px_3x3": matrix,
                "center_registration_error_px": center_error,
                "registered_source": _output_artifact(source_window_path, out_dir),
                "combined_model_closeup": _image_binding(model_path),
                "composite": _output_artifact(composite_path, out_dir),
                "xy_candidate": True,
                "source_correction_authorized": False,
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

    contact = Image.new("RGB", (3000, 1650), "white")
    for index, row in enumerate(rows):
        tile = Image.open(out_dir / row["composite"]["relative_path"]).convert("RGB")
        tile.thumbnail((980, 533), Image.Resampling.LANCZOS)
        x = (index % 3) * 1000 + 10
        y = (index // 3) * 550 + 8
        contact.paste(tile, (x, y))
    contact_path = out_dir / "combined-nine-gap-contact-sheet.png"
    contact.save(contact_path)

    result = {
        "schema": "combined-gap-registered-evidence-v2",
        "source_structure_hash": document["structure_hash"],
        "source_document_sha256": _file_hash(source_document_path),
        "source_image": _image_binding(source_image_path),
        "plan_file_sha256": _file_hash(plan_path),
        "plan_candidate_hash": plan["candidate_hash"],
        "combined_builder_sha256": _file_hash(COMBINED_BUILDER),
        "combined_manifest_file_sha256": _file_hash(combined_manifest_path),
        "combined_validation_file_sha256": _file_hash(combined_validation_path),
        "combined_blender_source_sha256": artifacts["blender_source"]["sha256"],
        "combined_portable_glb_sha256": artifacts["portable_glb"]["sha256"],
        "opening_ids": list(EXPECTED_IDS),
        "excluded_opening_ids": list(EXPECTED_EXCLUDED),
        "full_plan": {
            "metric_window": full_window,
            "output_px_to_source_px_3x3": full_matrix,
            "center_registration_error_px": full_center_error,
            "registered_source": _output_artifact(full_source_path, out_dir),
            "combined_model_top": _image_binding(model_top_path),
            "source_gap_locator": _output_artifact(locator_path, out_dir),
            "composite": _output_artifact(full_composite_path, out_dir),
        },
        "rows": rows,
        "contact_sheet": _output_artifact(contact_path, out_dir),
        "registration_contract": "same_center_same_ortho_scale_same_orientation_same_1200x1200_resolution",
        "model_scope": "single_combined_43_piece_wall_set",
        "output_path_mode": "relative_to_evidence_file",
        "evidence_plan_portable": False,
        "source_correction_authorized": False,
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
    result["candidate_hash"] = _candidate_hash(
        {key: value for key, value in result.items() if key != "candidate_hash"}
    )
    return result if _skip_validate else validate(
        result,
        out_dir=out_dir,
        source_document_path=source_document_path,
        source_image_path=source_image_path,
        plan_path=plan_path,
        combined_dir=combined_dir,
        combined_manifest_path=combined_manifest_path,
        combined_validation_path=combined_validation_path,
    )


def _validate_image_artifact(artifact: Mapping[str, Any], base: Path, expected_size: list[int]) -> None:
    relative_path = artifact.get("relative_path")
    if not isinstance(relative_path, str) or Path(relative_path).name != relative_path:
        raise ValueError("registered output relative path drift")
    path = base / relative_path
    if not path.is_file() or path.stat().st_size != artifact.get("bytes") or _file_hash(path) != artifact.get("sha256"):
        raise ValueError("registered output bytes/hash drift")
    with Image.open(path) as image:
        if list(image.size) != expected_size or artifact.get("size") != expected_size:
            raise ValueError("registered output image size drift")


def validate(
    candidate: Mapping[str, Any],
    *,
    out_dir: Path = OUT,
    source_document_path: Path = SOURCE_DOCUMENT,
    source_image_path: Path = SOURCE_IMAGE,
    plan_path: Path = PLAN,
    combined_dir: Path = COMBINED_DIR,
    combined_manifest_path: Path = COMBINED_MANIFEST,
    combined_validation_path: Path = COMBINED_VALIDATION,
    rebuild: bool = True,
) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    out_dir = Path(out_dir)
    document = validate_v21_document(_read_json(source_document_path))
    metric_to_source = _inverse(document["source"]["metric_registration"]["canonical_px_to_metric_3x3"])
    if (
        actual.get("schema") != "combined-gap-registered-evidence-v2"
        or actual.get("opening_ids") != list(EXPECTED_IDS)
        or actual.get("excluded_opening_ids") != list(EXPECTED_EXCLUDED)
        or actual.get("registration_contract") != "same_center_same_ortho_scale_same_orientation_same_1200x1200_resolution"
        or actual.get("model_scope") != "single_combined_43_piece_wall_set"
        or actual.get("output_path_mode") != "relative_to_evidence_file"
        or actual.get("evidence_plan_portable") is not False
    ):
        raise ValueError("combined registered evidence identity/policy drift")
    _assert_fail_closed(actual, context="combined registered evidence")
    if len(actual.get("rows", [])) != len(EXPECTED_IDS):
        raise ValueError("combined registered evidence row count drift")
    full = actual["full_plan"]
    expected_full_matrix = _output_to_source_matrix(
        metric_to_source,
        full["metric_window"]["center_m"],
        float(full["metric_window"]["ortho_scale_m"]),
        1200,
    )
    if full["output_px_to_source_px_3x3"] != expected_full_matrix or full["center_registration_error_px"] > 1e-6:
        raise ValueError("full registered evidence transform drift")
    _validate_image_artifact(full["registered_source"], out_dir, [1200, 1200])
    _validate_image_artifact(full["source_gap_locator"], out_dir, [1200, 1200])
    _validate_image_artifact(full["composite"], out_dir, [3760, 1370])
    model_top = ROOT / full["combined_model_top"]["path"]
    if (
        model_top.parent.resolve() != Path(combined_dir).resolve()
        or model_top.stat().st_size != full["combined_model_top"]["bytes"]
        or _file_hash(model_top) != full["combined_model_top"]["sha256"]
        or full["combined_model_top"]["size"] != [1200, 1200]
    ):
        raise ValueError("full combined model binding drift")
    for opening_id, row in zip(EXPECTED_IDS, actual["rows"]):
        if row.get("opening_id") != opening_id or row.get("xy_candidate") is not True:
            raise ValueError("combined registered row identity drift")
        _assert_fail_closed(row, context=opening_id)
        expected_matrix = _output_to_source_matrix(
            metric_to_source,
            row["metric_window"]["center_m"],
            float(row["metric_window"]["ortho_scale_m"]),
            1200,
        )
        if row["output_px_to_source_px_3x3"] != expected_matrix or row["center_registration_error_px"] > 1e-6:
            raise ValueError(f"{opening_id} registered transform drift")
        _validate_image_artifact(row["registered_source"], out_dir, [1200, 1200])
        _validate_image_artifact(row["composite"], out_dir, [2520, 1370])
        model = ROOT / row["combined_model_closeup"]["path"]
        if (
            model.parent.resolve() != Path(combined_dir).resolve()
            or "opening_xy_variants_v001" in str(model)
            or model.stat().st_size != row["combined_model_closeup"]["bytes"]
            or _file_hash(model) != row["combined_model_closeup"]["sha256"]
            or row["combined_model_closeup"]["size"] != [1200, 1200]
        ):
            raise ValueError(f"{opening_id} combined closeup binding drift")
    _validate_image_artifact(actual["contact_sheet"], out_dir, [3000, 1650])
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("combined registered candidate hash drift")
    if rebuild:
        expected = build(
            out_dir=out_dir,
            source_document_path=Path(source_document_path),
            source_image_path=Path(source_image_path),
            plan_path=Path(plan_path),
            combined_dir=Path(combined_dir),
            combined_manifest_path=Path(combined_manifest_path),
            combined_validation_path=Path(combined_validation_path),
            _skip_validate=True,
        )
        if actual != expected:
            raise ValueError("combined registered evidence/rebuild drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    result = build(out_dir=args.out)
    evidence_path = args.out / "evidence.json"
    evidence_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.out / "REPORT.md").write_text(
        "# Combined gap registered evidence v2\n\n"
        "The canonical source and the single 43-piece combined Blender model are compared in identical metric "
        "windows. The bundle includes one full-plan clean/locator composite and nine clean registered local pairs. "
        "No isolated model render is reused. This is XY research evidence only; it makes no opening type, Z, room, "
        "traversability, adjacency, source-correction, score, or formal-build claim.\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_output_to_source_matrix", "_candidate_hash"]
