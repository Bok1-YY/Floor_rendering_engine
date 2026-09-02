"""Build a labeled evidence composite for a generic no-cut vertical display."""
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
from tools.goal_loop_v2.build_opening_vertical_display_plan import validate as validate_plan
from tools.goal_loop_v2.build_opening_vertical_provenance_audit import validate as validate_audit
from tools.goal_loop_v2.build_opening_xy_clean_evidence import validate as validate_clean

CLEAN_EVIDENCE = ROOT / "reports/opening_xy_clean_evidence_20260902/evidence.json"
FAIL_CLOSED = (
    "source_vertical_confirmation",
    "source_subtype_confirmation",
    "effective_void_confirmation",
    "traversability_confirmation",
    "pair_confirmation",
    "adjacency_confirmation",
    "root_confirmation",
    "source_correction_authorized",
    "semantic_promotion",
    "build_authorized",
    "ready",
)


def default_inputs(opening_id: str) -> dict[str, Path]:
    slug = opening_id.lower()
    return {
        "plan_path": ROOT / f"reports/{slug}_vertical_display_plan_20260903/plan.json",
        "audit_path": ROOT / f"reports/{slug}_vertical_provenance_20260903/audit.json",
        "subtype_bundle_path": ROOT / f"reports/{slug}_clean_subtype_20260903/bundle.json",
        "subtype_result_path": ROOT / f"reports/{slug}_clean_subtype_20260903/selected-result.json",
        "display_dir": ROOT / f"artifacts/goal_loop_v2/1308/{slug}_layer3b_vertical_research_v001",
    }


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _font(size: int) -> ImageFont.ImageFont:
    path = Path(r"C:/Windows/Fonts/arial.ttf")
    return ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default()


def _binding(path: Path, *, root_relative: bool) -> dict[str, Any]:
    with Image.open(path) as image:
        size = list(image.size)
    return {
        ("path" if root_relative else "relative_path"): (
            str(path.resolve().relative_to(ROOT)).replace("\\", "/")
            if root_relative
            else path.name
        ),
        "bytes": path.stat().st_size,
        "sha256": _file_hash(path),
        "size": size,
    }


def _assert_false(value: Mapping[str, Any], context: str) -> None:
    for key in FAIL_CLOSED:
        if value.get(key) is not False:
            raise ValueError(f"{context} promoted {key}")
    if value.get("score_effect") != "none":
        raise ValueError(f"{context} score drift")


def _panel(image: Image.Image) -> Image.Image:
    fitted = ImageOps.contain(image.convert("RGB"), (1200, 1200), Image.Resampling.LANCZOS)
    result = Image.new("RGB", (1200, 1200), "white")
    result.paste(fitted, ((1200 - fitted.width) // 2, (1200 - fitted.height) // 2))
    return result


def _composite(opening_id: str, raw: Image.Image, top: Image.Image, ne: Image.Image, front: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", (5000, 1390), "white")
    panels = [_panel(raw), top.convert("RGB"), ne.convert("RGB"), front.convert("RGB")]
    xs = [40, 1280, 2520, 3760]
    for x, panel in zip(xs, panels):
        canvas.paste(panel, (x, 120))
    draw = ImageDraw.Draw(canvas)
    title, body = _font(28), _font(21)
    headers = [
        f"{opening_id} SOURCE RAW / VISUAL CANDIDATE ONLY",
        "TOP / 35 INTACT WALLS + BLUE XY LOCATOR",
        "NORTHEAST / ZERO OPENING CUT",
        "FRONT / ORANGE UNBOUND HEAD GUIDE",
    ]
    for x, header in zip(xs, headers):
        draw.text((x, 22), header, fill="black", font=title)
    draw.text(
        (40, 62),
        "WALL 2.8m = UNVERIFIED ASSUMPTION / HEAD GUIDE 2.1m = UNBOUND DEFAULT, NOT OPENING GEOMETRY",
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
        "DISPLAY CLARITY ONLY / NO SOURCE VERTICAL, EFFECTIVE VOID, TRAVERSABILITY, ADJACENCY OR ROOT / NOT FOR CONSTRUCTION",
        fill=(150, 0, 0),
        font=body,
    )
    return canvas


def build(
    opening_id: str,
    *,
    plan_path: Path,
    audit_path: Path,
    subtype_bundle_path: Path,
    subtype_result_path: Path,
    display_dir: Path,
    clean_evidence_path: Path = CLEAN_EVIDENCE,
    out_dir: Path,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    plan_path, audit_path = Path(plan_path), Path(audit_path)
    subtype_bundle_path, subtype_result_path = Path(subtype_bundle_path), Path(subtype_result_path)
    display_dir, clean_evidence_path, out_dir = Path(display_dir), Path(clean_evidence_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    validate_plan(
        plan,
        opening_id,
        audit_path=audit_path,
        subtype_bundle_path=subtype_bundle_path,
        subtype_result_path=subtype_result_path,
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    validate_audit(
        audit,
        opening_id,
        subtype_bundle_path=subtype_bundle_path,
        subtype_result_path=subtype_result_path,
    )
    clean = json.loads(clean_evidence_path.read_text(encoding="utf-8"))
    validate_clean(clean, rebuild=False)
    clean_row = next(row for row in clean["openings"] if row["opening_id"] == opening_id)
    manifest_path, validation_path = display_dir / "artifact_manifest.json", display_dir / "validation.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    _assert_false(manifest, "display manifest")
    _assert_false(validation, "display validation")
    if (
        manifest.get("schema") != "blender-opening-layer3b-vertical-research-manifest-v1"
        or manifest.get("opening_id") != opening_id
        or manifest.get("plan_candidate_hash") != plan["candidate_hash"]
        or manifest.get("plan_file_sha256") != _file_hash(plan_path)
        or manifest.get("source_document_sha256") != plan["source_document_sha256"]
        or manifest.get("wall_count") != 35
        or manifest.get("guide_count") != 2
        or manifest.get("opening_cuts") != 0
        or manifest.get("opening_elements") != 0
        or manifest.get("head_guide_binding") != "unbound_research_default"
        or manifest.get("sill_m") is not None
        or manifest.get("artifact_path_mode") != "relative_to_manifest"
    ):
        raise ValueError("generic display manifest drift")
    artifacts = {}
    for row in manifest["artifacts"]:
        path = display_dir / row["relative_path"]
        if path.stat().st_size != row["bytes"] or _file_hash(path) != row["sha256"]:
            raise ValueError("generic display artifact drift")
        artifacts[row["kind"]] = row
    if set(artifacts) != {"checkpoint_blend", "blender_source", "portable_glb", "render_top", "render_northeast", "render_front_closeup", "validation"}:
        raise ValueError("generic display artifact coverage drift")
    if (
        validation.get("pass") is not True
        or validation.get("actual_wall_count") != 35
        or validation.get("actual_guide_count") != 2
        or validation.get("wall_errors") != []
        or validation.get("guide_errors") != []
        or validation.get("forbidden_objects") != []
        or validation.get("opening_cuts") != 0
        or validation.get("opening_elements") != 0
        or validation.get("head_guide_binding") != "unbound_research_default"
        or validation.get("sill_m") is not None
    ):
        raise ValueError("generic display validation drift")
    raw_path = Path(clean_row["artifacts"]["raw_crop"]["path"])
    paths = {
        key: display_dir / artifacts[kind]["relative_path"]
        for key, kind in (("top", "render_top"), ("northeast", "render_northeast"), ("front_closeup", "render_front_closeup"))
    }
    with Image.open(raw_path) as raw, Image.open(paths["top"]) as top, Image.open(paths["northeast"]) as ne, Image.open(paths["front_closeup"]) as front:
        composite = _composite(opening_id, raw, top, ne, front)
    composite_path = out_dir / f"{opening_id}-layer3b-display-composite.png"
    composite.save(composite_path)
    result = {
        "schema": "opening-layer3b-display-evidence-v1",
        "opening_id": opening_id,
        "source_structure_hash": plan["source_structure_hash"],
        "display_plan_file_sha256": _file_hash(plan_path),
        "display_plan_candidate_hash": plan["candidate_hash"],
        "vertical_audit_file_sha256": _file_hash(audit_path),
        "vertical_audit_candidate_hash": audit["candidate_hash"],
        "clean_evidence_file_sha256": _file_hash(clean_evidence_path),
        "clean_evidence_candidate_hash": clean["candidate_hash"],
        "display_manifest_file_sha256": _file_hash(manifest_path),
        "display_validation_file_sha256": _file_hash(validation_path),
        "image_bindings": {
            "raw_crop": _binding(raw_path, root_relative=True),
            **{key: _binding(path, root_relative=True) for key, path in paths.items()},
            "composite": _binding(composite_path, root_relative=False),
        },
        "display_contract": {
            "wall_count": 35,
            "guide_count": 2,
            "opening_cuts": 0,
            "opening_elements": 0,
            "head_guide_binding": "unbound_research_default",
            "sill_m": None,
            "door_leaf_created": False,
            "ifc_opening_created": False,
        },
        "visual_review_scope": "unbound_guide_display_clarity_only",
        **{key: False for key in FAIL_CLOSED},
        "score_effect": "none",
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _candidate_hash({key: value for key, value in result.items() if key != "candidate_hash"})
    return result if _skip_validate else validate(
        result,
        opening_id,
        plan_path=plan_path,
        audit_path=audit_path,
        subtype_bundle_path=subtype_bundle_path,
        subtype_result_path=subtype_result_path,
        display_dir=display_dir,
        clean_evidence_path=clean_evidence_path,
        out_dir=out_dir,
    )


def validate(candidate: Mapping[str, Any], opening_id: str, **kwargs) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(opening_id, _skip_validate=True, **kwargs)
    if actual != expected:
        raise ValueError("generic display evidence/derivation drift")
    _assert_false(actual, "generic display evidence")
    if (
        actual.get("opening_id") != opening_id
        or actual.get("visual_review_scope") != "unbound_guide_display_clarity_only"
        or actual["display_contract"]["opening_cuts"] != 0
        or actual["display_contract"]["sill_m"] is not None
    ):
        raise ValueError("generic display evidence scope drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("generic display evidence candidate hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opening-id", required=True)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--subtype-bundle", required=True, type=Path)
    parser.add_argument("--subtype-result", required=True, type=Path)
    parser.add_argument("--display-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    result = build(
        args.opening_id,
        plan_path=args.plan,
        audit_path=args.audit,
        subtype_bundle_path=args.subtype_bundle,
        subtype_result_path=args.subtype_result,
        display_dir=args.display_dir,
        out_dir=args.out,
    )
    (args.out / "evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out / "REPORT.md").write_text(
        f"# {args.opening_id} Layer3B display evidence\n\n"
        "The labeled composite shows the source crop, 35-wall intact top/axonometric views, and the front view with "
        "blue XY locator and orange unbound 2.1 m research guide. Sill is unknown and no opening/door/IFC geometry exists.\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "default_inputs", "_candidate_hash"]
