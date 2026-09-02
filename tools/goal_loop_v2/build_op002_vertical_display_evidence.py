"""Build a labeled visual-evidence composite for the OP002 Layer3B display."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.goal_loop_v2.build_opening_xy_clean_evidence import validate as validate_clean_evidence
from tools.goal_loop_v2.build_op002_vertical_display_plan import validate as validate_display_plan
from tools.goal_loop_v2.build_op002_vertical_provenance_audit import validate as validate_vertical_audit

PLAN = ROOT / "reports/op002_vertical_display_plan_20260903/plan.json"
AUDIT = ROOT / "reports/op002_vertical_provenance_20260903/audit.json"
CLEAN_EVIDENCE = ROOT / "reports/opening_xy_clean_evidence_20260902/evidence.json"
DISPLAY_DIR = ROOT / "artifacts/goal_loop_v2/1308/op002_layer3b_vertical_research_v001"
DISPLAY_MANIFEST = DISPLAY_DIR / "artifact_manifest.json"
DISPLAY_VALIDATION = DISPLAY_DIR / "validation.json"
DISPLAY_BUILDER = ROOT / "tools/goal_loop_v2/blender_op002_vertical_research_display.py"
OUT = ROOT / "reports/op002_vertical_display_evidence_20260903"
FAIL_CLOSED = (
    "source_vertical_confirmation",
    "source_subtype_confirmation",
    "effective_void_confirmation",
    "traversability_confirmation",
    "adjacency_confirmation",
    "source_correction_authorized",
    "semantic_promotion",
    "build_authorized",
    "ready",
)


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _font(size: int) -> ImageFont.ImageFont:
    for path in (Path(r"C:/Windows/Fonts/arial.ttf"), Path(r"C:/Windows/Fonts/msyh.ttc")):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _assert_fail_closed(value: Mapping[str, Any], *, context: str) -> None:
    for key in FAIL_CLOSED:
        if value.get(key) is not False:
            raise ValueError(f"{context} promoted or omitted {key}")
    if value.get("score_effect") != "none":
        raise ValueError(f"{context} score drift")


def _image_binding(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        size = list(image.size)
    return {
        "path": str(path.resolve().relative_to(ROOT)).replace("\\", "/"),
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


def _validate_display_artifacts(
    manifest_path: Path,
    validation_path: Path,
    display_dir: Path,
    *,
    plan: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = _read_json(manifest_path)
    validation = _read_json(validation_path)
    _assert_fail_closed(manifest, context="Layer3B display manifest")
    _assert_fail_closed(validation, context="Layer3B display validation")
    if (
        manifest.get("schema") != "blender-op002-layer3b-vertical-research-artifact-manifest-v1"
        or manifest.get("branch_id") != plan["branch_id"]
        or manifest.get("plan_candidate_hash") != plan["candidate_hash"]
        or manifest.get("plan_file_sha256") != _file_hash(PLAN)
        or manifest.get("source_structure_hash") != plan["source_structure_hash"]
        or manifest.get("source_document_sha256") != plan["source_document_sha256"]
        or manifest.get("wall_count") != 35
        or manifest.get("guide_count") != 2
        or manifest.get("opening_cuts") != 0
        or manifest.get("opening_elements") != 0
        or manifest.get("door_leaf_created") is not False
        or manifest.get("ifc_opening_created") is not False
        or manifest.get("wall_height_m") != 2.8
        or manifest.get("head_level_m") != 2.1
        or manifest.get("sill_level_m") is not None
        or manifest.get("artifact_path_mode") != "relative_to_manifest"
        or manifest.get("research_only") is not True
        or manifest.get("not_for_construction") is not True
    ):
        raise ValueError("Layer3B display manifest drift")
    artifacts = {}
    for row in manifest.get("artifacts", []):
        kind = row.get("kind")
        relative_path = row.get("relative_path")
        if kind in artifacts or not isinstance(relative_path, str) or Path(relative_path).name != relative_path:
            raise ValueError("Layer3B display artifact path/kind drift")
        path = display_dir / relative_path
        if not path.is_file() or path.stat().st_size != row.get("bytes") or _file_hash(path) != row.get("sha256"):
            raise ValueError("Layer3B display artifact bytes/hash drift")
        artifacts[str(kind)] = dict(row)
    if set(artifacts) != {
        "checkpoint_blend",
        "blender_source",
        "portable_glb",
        "render_top",
        "render_northeast",
        "render_front_closeup",
        "validation",
    }:
        raise ValueError("Layer3B display artifact coverage drift")
    if (display_dir / artifacts["validation"]["relative_path"]).resolve() != validation_path.resolve():
        raise ValueError("Layer3B validation binding drift")
    if (
        validation.get("schema") != "blender-op002-layer3b-vertical-research-validation-v1"
        or validation.get("branch_id") != manifest["branch_id"]
        or validation.get("plan_candidate_hash") != plan["candidate_hash"]
        or validation.get("expected_wall_count") != 35
        or validation.get("actual_wall_count") != 35
        or validation.get("distinct_source_atom_count") != 35
        or validation.get("actual_guide_count") != 2
        or validation.get("wall_topology_errors") != []
        or validation.get("wall_property_errors") != []
        or validation.get("guide_topology_errors") != []
        or validation.get("guide_property_errors") != []
        or validation.get("guide_bound_errors") != []
        or validation.get("forbidden_objects") != []
        or validation.get("wall_height_m") != 2.8
        or validation.get("head_level_m") != 2.1
        or validation.get("sill_level_m") is not None
        or validation.get("opening_cuts") != 0
        or validation.get("opening_elements") != 0
        or validation.get("opening_geometry_created") is not False
        or validation.get("floor_cut_created") is not False
        or validation.get("sill_geometry_created") is not False
        or validation.get("door_leaf_created") is not False
        or validation.get("lintel_structural_element_created") is not False
        or validation.get("ifc_opening_created") is not False
        or validation.get("ifc_void_or_fill_created") is not False
        or validation.get("pass") is not True
    ):
        raise ValueError("Layer3B display structural validation drift")
    return manifest, validation, artifacts


def _fit_panel(image: Image.Image) -> Image.Image:
    fitted = ImageOps.contain(image.convert("RGB"), (1200, 1200), Image.Resampling.LANCZOS)
    panel = Image.new("RGB", (1200, 1200), "white")
    panel.paste(fitted, ((1200 - fitted.width) // 2, (1200 - fitted.height) // 2))
    return panel


def _composite(
    raw_crop: Image.Image,
    top: Image.Image,
    northeast: Image.Image,
    front: Image.Image,
) -> Image.Image:
    canvas = Image.new("RGB", (5000, 1390), "white")
    panels = [_fit_panel(raw_crop), top.convert("RGB"), northeast.convert("RGB"), front.convert("RGB")]
    x_positions = [40, 1280, 2520, 3760]
    for x, panel in zip(x_positions, panels):
        canvas.paste(panel, (x, 120))
    draw = ImageDraw.Draw(canvas)
    title = _font(28)
    body = _font(21)
    headers = [
        "SOURCE RAW CROP / VISUAL CANDIDATE ONLY",
        "TOP / 35 INTACT WALLS + BLUE XY LOCATOR",
        "NORTHEAST / NO OPENING CUT",
        "FRONT / ORANGE HEAD GUIDE ON INTACT WALL",
    ]
    for x, header in zip(x_positions, headers):
        draw.text((x, 22), header, fill="black", font=title)
    draw.text(
        (40, 62),
        "WALL 2.8m = UNVERIFIED RESEARCH ASSUMPTION / HEAD 2.1m = UNVERIFIED RESEARCH ASSUMPTION",
        fill=(150, 60, 0),
        font=body,
    )
    draw.text(
        (40, 92),
        "SILL = UNKNOWN / 0 CUTS / 0 OPENING OBJECTS / 0 DOOR LEAVES / 0 IFC",
        fill=(150, 0, 0),
        font=body,
    )
    draw.text(
        (40, 1350),
        "DISPLAY CLARITY REVIEW ONLY / NO SOURCE VERTICAL OR EFFECTIVE-VOID CONFIRMATION / NOT FOR CONSTRUCTION",
        fill=(150, 0, 0),
        font=body,
    )
    return canvas


def build(
    *,
    out_dir: Path = OUT,
    plan_path: Path = PLAN,
    audit_path: Path = AUDIT,
    clean_evidence_path: Path = CLEAN_EVIDENCE,
    display_dir: Path = DISPLAY_DIR,
    manifest_path: Path = DISPLAY_MANIFEST,
    validation_path: Path = DISPLAY_VALIDATION,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    out_dir = Path(out_dir)
    plan_path = Path(plan_path)
    audit_path = Path(audit_path)
    clean_evidence_path = Path(clean_evidence_path)
    display_dir = Path(display_dir)
    manifest_path = Path(manifest_path)
    validation_path = Path(validation_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = _read_json(plan_path)
    validate_display_plan(plan)
    audit = _read_json(audit_path)
    validate_vertical_audit(audit)
    clean = _read_json(clean_evidence_path)
    validate_clean_evidence(clean, rebuild=False)
    row = next(item for item in clean["openings"] if item["opening_id"] == "OP002")
    if row["artifacts"]["raw_crop"]["source_pixels_untouched"] is not True:
        raise ValueError("Layer3B display evidence raw crop drift")
    manifest, validation, artifacts = _validate_display_artifacts(
        manifest_path,
        validation_path,
        display_dir,
        plan=plan,
    )
    raw_path = Path(row["artifacts"]["raw_crop"]["path"])
    top_path = display_dir / artifacts["render_top"]["relative_path"]
    ne_path = display_dir / artifacts["render_northeast"]["relative_path"]
    front_path = display_dir / artifacts["render_front_closeup"]["relative_path"]
    with Image.open(raw_path) as raw, Image.open(top_path) as top, Image.open(ne_path) as ne, Image.open(front_path) as front:
        if top.size != (1200, 1200) or ne.size != (1200, 1200) or front.size != (1200, 1200):
            raise ValueError("Layer3B display render size drift")
        composite = _composite(raw, top, ne, front)
    composite_path = out_dir / "op002-layer3b-display-composite.png"
    composite.save(composite_path)
    result = {
        "schema": "op002-layer3b-display-evidence-v1",
        "opening_id": "OP002",
        "source_structure_hash": plan["source_structure_hash"],
        "display_plan_file_sha256": _file_hash(plan_path),
        "display_plan_candidate_hash": plan["candidate_hash"],
        "vertical_audit_file_sha256": _file_hash(audit_path),
        "vertical_audit_candidate_hash": audit["candidate_hash"],
        "clean_evidence_file_sha256": _file_hash(clean_evidence_path),
        "clean_evidence_candidate_hash": clean["candidate_hash"],
        "display_builder_sha256": _file_hash(DISPLAY_BUILDER),
        "display_manifest_file_sha256": _file_hash(manifest_path),
        "display_validation_file_sha256": _file_hash(validation_path),
        "blender_source_sha256": artifacts["blender_source"]["sha256"],
        "portable_glb_sha256": artifacts["portable_glb"]["sha256"],
        "image_bindings": {
            "raw_crop": _image_binding(raw_path),
            "top": _image_binding(top_path),
            "northeast": _image_binding(ne_path),
            "front_closeup": _image_binding(front_path),
            "composite": _output_artifact(composite_path, out_dir),
        },
        "display_contract": {
            "wall_count": manifest["wall_count"],
            "guide_count": manifest["guide_count"],
            "guide_roles": manifest["guide_roles"],
            "opening_cuts": 0,
            "opening_elements": 0,
            "wall_height_m": {"value": 2.8, "provenance": "unverified_research_assumption"},
            "head_level_m": {"value": 2.1, "provenance": "unverified_research_assumption"},
            "sill_level_m": {"value": None, "provenance": "unknown_not_authorized"},
            "door_leaf_created": False,
            "ifc_opening_created": False,
        },
        "visual_review_scope": "display_clarity_and_nonsemantic_separation_only",
        "source_vertical_confirmation": False,
        "source_subtype_confirmation": False,
        "effective_void_confirmation": False,
        "traversability_confirmation": False,
        "adjacency_confirmation": False,
        "source_correction_authorized": False,
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
        plan_path=plan_path,
        audit_path=audit_path,
        clean_evidence_path=clean_evidence_path,
        display_dir=display_dir,
        manifest_path=manifest_path,
        validation_path=validation_path,
    )


def validate(
    candidate: Mapping[str, Any],
    *,
    out_dir: Path = OUT,
    plan_path: Path = PLAN,
    audit_path: Path = AUDIT,
    clean_evidence_path: Path = CLEAN_EVIDENCE,
    display_dir: Path = DISPLAY_DIR,
    manifest_path: Path = DISPLAY_MANIFEST,
    validation_path: Path = DISPLAY_VALIDATION,
) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(
        out_dir=Path(out_dir),
        plan_path=Path(plan_path),
        audit_path=Path(audit_path),
        clean_evidence_path=Path(clean_evidence_path),
        display_dir=Path(display_dir),
        manifest_path=Path(manifest_path),
        validation_path=Path(validation_path),
        _skip_validate=True,
    )
    if actual != expected:
        raise ValueError("Layer3B display evidence/derivation drift")
    _assert_fail_closed(actual, context="Layer3B display evidence")
    if (
        actual.get("schema") != "op002-layer3b-display-evidence-v1"
        or actual.get("visual_review_scope") != "display_clarity_and_nonsemantic_separation_only"
        or actual["display_contract"]["opening_cuts"] != 0
        or actual["display_contract"]["opening_elements"] != 0
        or actual["display_contract"]["sill_level_m"]["value"] is not None
        or actual["display_contract"]["door_leaf_created"] is not False
        or actual["display_contract"]["ifc_opening_created"] is not False
    ):
        raise ValueError("Layer3B display evidence scope drift")
    composite = actual["image_bindings"]["composite"]
    path = Path(out_dir) / composite["relative_path"]
    if (
        not path.is_file()
        or path.stat().st_size != composite["bytes"]
        or _file_hash(path) != composite["sha256"]
        or composite["size"] != [5000, 1390]
    ):
        raise ValueError("Layer3B display composite drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("Layer3B display evidence candidate hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    result = build(out_dir=args.out)
    (args.out / "evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.out / "REPORT.md").write_text(
        "# OP002 Layer3B display evidence v1\n\n"
        "The composite binds the clean source crop, intact-wall top and northeast renders, and the corrected front "
        "close-up where the blue XY locator and orange 2.1 m research-assumption guide are visible. The Blender branch "
        "contains 35 intact walls, two non-semantic guides, zero cuts, zero opening elements, no sill geometry, no door "
        "leaf, and no IFC opening. Review scope is display clarity only.\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_candidate_hash"]
