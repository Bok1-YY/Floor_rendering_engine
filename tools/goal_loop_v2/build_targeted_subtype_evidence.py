"""Build pixel-exact tighter source crops for contaminated subtype candidates."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.goal_loop_v2.build_opening_xy_clean_evidence import validate as validate_clean

BASE_EVIDENCE = ROOT / "reports/opening_xy_clean_evidence_20260902/evidence.json"
ALLOWED_IDS = ("OP006", "OP008")
FAIL_CLOSED = (
    "source_subtype_confirmation",
    "effective_void_confirmation",
    "vertical_parameters_reviewed",
    "traversability_confirmation",
    "pair_confirmation",
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


def _pixel_hash(image: Image.Image) -> str:
    return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()


def _root_binding(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        size = list(image.size)
        pixel_hash = _pixel_hash(image)
    return {
        "path": str(path.resolve().relative_to(ROOT)).replace("\\", "/"),
        "bytes": path.stat().st_size,
        "sha256": _file_hash(path),
        "pixel_sha256": pixel_hash,
        "size": size,
    }


def _output_binding(path: Path, out_dir: Path, png_bytes: bytes, image: Image.Image) -> dict[str, Any]:
    return {
        "relative_path": str(path.relative_to(out_dir)).replace("\\", "/"),
        "bytes": len(png_bytes),
        "sha256": hashlib.sha256(png_bytes).hexdigest(),
        "pixel_sha256": _pixel_hash(image),
        "size": list(image.size),
    }


def build(
    opening_id: str,
    crop_box_px: list[int],
    *,
    out_dir: Path,
    base_evidence_path: Path = BASE_EVIDENCE,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    if opening_id not in ALLOWED_IDS:
        raise ValueError("opening is not admitted to tighter-crop remediation")
    if len(crop_box_px) != 4 or any(not isinstance(value, int) for value in crop_box_px):
        raise ValueError("tighter crop box must contain four integers")
    left, top, right, bottom = crop_box_px
    if left >= right or top >= bottom:
        raise ValueError("tighter crop box is empty")
    out_dir, base_evidence_path = Path(out_dir), Path(base_evidence_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = json.loads(base_evidence_path.read_text(encoding="utf-8"))
    validate_clean(base, rebuild=False)
    row = next(item for item in base["openings"] if item["opening_id"] == opening_id)
    source_path = Path(base["source_image_path"])
    if _file_hash(source_path) != base["source_image_sha256"]:
        raise ValueError("tighter crop source image drift")
    with Image.open(source_path) as source:
        source_rgb = source.convert("RGB")
        if left < 0 or top < 0 or right > source_rgb.width or bottom > source_rgb.height:
            raise ValueError("tighter crop leaves source image")
        crop = source_rgb.crop((left, top, right, bottom))
        source_region = source_rgb.crop((left, top, right, bottom))
    if crop.tobytes() != source_region.tobytes():
        raise RuntimeError("tighter crop pixel mutation")
    local_segment = [
        [float(point[0]) - left, float(point[1]) - top]
        for point in row["segment_px"]
    ]
    width, height = right - left, bottom - top
    clearances = [
        min(point[0], width - point[0], point[1], height - point[1])
        for point in local_segment
    ]
    if min(clearances) < 15.0:
        raise ValueError("tighter crop leaves insufficient target endpoint clearance")
    crop_path = out_dir / f"{opening_id}-targeted-raw-crop.png"
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    png_bytes = buffer.getvalue()
    if not _skip_validate:
        temporary_path = out_dir / f".{crop_path.name}.{os.getpid()}.tmp"
        temporary_path.write_bytes(png_bytes)
        temporary_path.replace(crop_path)
        with Image.open(crop_path) as saved:
            if saved.convert("RGB").tobytes() != source_region.tobytes():
                raise RuntimeError("saved tighter crop is not source-pixel exact")
    locator_path = Path(row["artifacts"]["locator"]["path"])
    parent_raw_path = Path(row["artifacts"]["raw_crop"]["path"])
    result = {
        "schema": "targeted-clean-subtype-evidence-v1",
        "opening_id": opening_id,
        "purpose": "reduce_neighboring_visual_cue_contamination",
        "source_structure_hash": base["source_structure_hash"],
        "base_evidence_file_sha256": _file_hash(base_evidence_path),
        "base_evidence_candidate_hash": base["candidate_hash"],
        "source_image": _root_binding(source_path),
        "host_atom_id": row["host_atom_id"],
        "segment_m": deepcopy(row["segment_m"]),
        "segment_px": deepcopy(row["segment_px"]),
        "parent_crop_box_px": deepcopy(row["crop_box_px"]),
        "targeted_crop_box_px": list(crop_box_px),
        "targeted_local_segment_px": local_segment,
        "minimum_target_endpoint_clearance_px": min(clearances),
        "artifacts": {
            "targeted_raw_crop": {
                **_output_binding(crop_path, out_dir, png_bytes, crop),
                "role": "pixel_exact_source_crop_semantic_authority",
                "source_pixels_untouched": True,
                "semantic_authority": True,
            },
            "locator": {
                **_root_binding(locator_path),
                "role": "locator_navigation_only",
                "source_pixels_untouched": False,
                "semantic_authority": False,
            },
            "parent_raw_crop": {
                **_root_binding(parent_raw_path),
                "role": "historical_wider_crop_provenance_only",
                "semantic_authority_for_targeted_review": False,
            },
        },
        "neighboring_visual_cues_present": "pending_independent_review",
        "target_cue_isolated": "pending_independent_review",
        "subtype_use_status": "pending_targeted_review",
        "source_subtype_confirmation": False,
        "effective_void_confirmation": False,
        "vertical_parameters_reviewed": False,
        "traversability_confirmation": False,
        "pair_confirmation": False,
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
        opening_id,
        crop_box_px,
        out_dir=out_dir,
        base_evidence_path=base_evidence_path,
    )


def validate(
    candidate: Mapping[str, Any],
    opening_id: str,
    crop_box_px: list[int],
    *,
    out_dir: Path,
    base_evidence_path: Path = BASE_EVIDENCE,
) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(
        opening_id,
        crop_box_px,
        out_dir=Path(out_dir),
        base_evidence_path=Path(base_evidence_path),
        _skip_validate=True,
    )
    if actual != expected:
        raise ValueError("targeted subtype evidence/derivation drift")
    targeted_artifact = actual.get("artifacts", {}).get("targeted_raw_crop", {})
    targeted_path = Path(out_dir) / targeted_artifact.get("relative_path", "")
    if (
        not targeted_path.is_file()
        or targeted_path.stat().st_size != targeted_artifact.get("bytes")
        or _file_hash(targeted_path) != targeted_artifact.get("sha256")
    ):
        raise ValueError("targeted subtype output artifact drift")
    if (
        actual.get("opening_id") != opening_id
        or actual.get("purpose") != "reduce_neighboring_visual_cue_contamination"
        or actual["artifacts"]["targeted_raw_crop"]["semantic_authority"] is not True
        or actual["artifacts"]["locator"]["semantic_authority"] is not False
        or actual["artifacts"]["parent_raw_crop"]["semantic_authority_for_targeted_review"] is not False
        or actual.get("subtype_use_status") != "pending_targeted_review"
    ):
        raise ValueError("targeted subtype evidence scope drift")
    for key in FAIL_CLOSED:
        if actual.get(key) is not False:
            raise ValueError(f"targeted subtype evidence promoted {key}")
    if actual.get("score_effect") != "none":
        raise ValueError("targeted subtype evidence score drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("targeted subtype evidence hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opening-id", required=True)
    parser.add_argument("--crop-box", required=True, nargs=4, type=int)
    parser.add_argument("--base-evidence", type=Path, default=BASE_EVIDENCE)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    result = build(
        args.opening_id,
        args.crop_box,
        out_dir=args.out,
        base_evidence_path=args.base_evidence,
    )
    (args.out / "evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out / "REPORT.md").write_text(
        f"# {args.opening_id} targeted subtype evidence\n\n"
        "A tighter pixel-exact crop was cut directly from the canonical source to reduce neighboring visual cues. "
        "Target-cue isolation remains pending independent review. The historical wider crop is retained as provenance "
        "but is not sent as semantic authority in the targeted review.\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_candidate_hash", "ALLOWED_IDS"]
