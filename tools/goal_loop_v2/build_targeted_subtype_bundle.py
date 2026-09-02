"""Bind an original subtype advisory to a tighter-crop cue-attribution review."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.fastloop_research.contract import canonical_json
from tools.goal_loop_v2.build_clean_subtype_bundle import validate as validate_original
from tools.goal_loop_v2.build_targeted_subtype_evidence import validate as validate_evidence
from tools.goal_loop_v2.fal_targeted_subtype_review import parse

FAIL_CLOSED = (
    "source_subtype_confirmation", "effective_void_confirmation", "vertical_parameters_reviewed",
    "traversability_confirmation", "pair_confirmation", "adjacency_confirmation",
    "source_correction_authorized", "semantic_promotion", "build_authorized", "ready",
)


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _assert_false(value: Mapping[str, Any], context: str) -> None:
    for key in FAIL_CLOSED:
        if value.get(key) is not False:
            raise ValueError(f"{context} promoted {key}")
    if value.get("score_effect") != "none":
        raise ValueError(f"{context} score drift")


def build(
    opening_id: str,
    *,
    original_bundle_path: Path,
    original_result_path: Path,
    targeted_evidence_path: Path,
    targeted_result_path: Path,
    _skip_validate: bool = False,
) -> dict[str, Any]:
    paths = [Path(path) for path in (original_bundle_path, original_result_path, targeted_evidence_path, targeted_result_path)]
    original_bundle_path, original_result_path, targeted_evidence_path, targeted_result_path = paths
    original = json.loads(original_bundle_path.read_text(encoding="utf-8"))
    validate_original(original, opening_id, result_path=original_result_path)
    evidence = json.loads(targeted_evidence_path.read_text(encoding="utf-8"))
    validate_evidence(
        evidence,
        opening_id,
        evidence["targeted_crop_box_px"],
        out_dir=targeted_evidence_path.parent,
    )
    selected = json.loads(targeted_result_path.read_text(encoding="utf-8"))
    _assert_false(selected, "targeted selected result")
    if (
        selected.get("schema") != "fal-targeted-clean-subtype-review-v1"
        or selected.get("opening_id") != opening_id
        or selected.get("model") != "google/gemini-2.5-flash"
        or selected.get("http_status") != 200
        or selected.get("usable_advisory") is not True
        or selected.get("validation_error") is not None
        or selected.get("transport_error") is not None
        or selected.get("targeted_evidence_file_sha256") != _file_hash(targeted_evidence_path)
        or selected.get("targeted_evidence_candidate_hash") != evidence["candidate_hash"]
        or selected.get("base_evidence_candidate_hash") != evidence["base_evidence_candidate_hash"]
        or selected.get("source_structure_hash") != evidence["source_structure_hash"]
        or selected.get("host_atom_id") != evidence["host_atom_id"]
        or selected.get("segment_m") != evidence["segment_m"]
        or selected.get("targeted_crop_box_px") != evidence["targeted_crop_box_px"]
        or selected.get("targeted_cue_attribution_advisory_only") is not True
    ):
        raise ValueError("targeted selected result identity/evidence drift")
    raw_response = selected["raw_response"]
    if selected.get("raw_response_sha256") != _candidate_hash(raw_response):
        raise ValueError("targeted selected raw hash drift")
    parsed = parse(raw_response["choices"][0]["message"]["content"], opening_id)
    if parsed != selected.get("parsed"):
        raise ValueError("targeted selected parsed/raw drift")
    cost = (selected.get("usage") or {}).get("cost")
    if not isinstance(cost, (int, float)) or cost < 0:
        raise ValueError("targeted selected cost missing")
    resolved = (
        parsed["visual_kind"] == "door"
        and parsed["wall_break_visible"] == "yes"
        and parsed["swing_arc_visible"] == "yes"
        and parsed["neighboring_opening_cue_visible"] == "no"
        and parsed["target_swing_cue_attributable_to_target"] == "yes"
        and parsed["confidence"] in {"high", "medium"}
    )
    result = {
        "schema": "targeted-subtype-remediation-bundle-v1",
        "opening_id": opening_id,
        "original_advisory": {
            "bundle_file_sha256": _file_hash(original_bundle_path),
            "bundle_candidate_hash": original["candidate_hash"],
            "selected_result_file_sha256": _file_hash(original_result_path),
            "visual_subtype_candidate": original["visual_subtype_candidate"],
            "preserved_as_history": True,
        },
        "targeted_evidence": {
            "file_sha256": _file_hash(targeted_evidence_path),
            "candidate_hash": evidence["candidate_hash"],
            "crop_box_px": evidence["targeted_crop_box_px"],
            "targeted_raw_crop": deepcopy(evidence["artifacts"]["targeted_raw_crop"]),
            "parent_raw_crop": deepcopy(evidence["artifacts"]["parent_raw_crop"]),
        },
        "targeted_selected_result": {
            "file_sha256": _file_hash(targeted_result_path),
            "raw_response_sha256": selected["raw_response_sha256"],
            "parsed": parsed,
            "cost_usd": float(cost),
            "canonical_result": selected,
        },
        "visual_subtype_candidate": parsed["visual_kind"],
        "neighboring_visual_cues_present": parsed["neighboring_opening_cue_visible"] != "no",
        "target_cue_isolated": parsed["target_swing_cue_attributable_to_target"] == "yes",
        "subtype_use_status": "resolved_after_tighter_crop" if resolved else "explicit_unresolved",
        "accepted_for_downstream_research_with_quarantine": resolved,
        "targeted_review_cost_usd": float(cost),
        **{key: False for key in FAIL_CLOSED},
        "score_effect": "none",
        "candidate_hash": "0" * 64,
    }
    result["candidate_hash"] = _candidate_hash({key: value for key, value in result.items() if key != "candidate_hash"})
    return result if _skip_validate else validate(
        result,
        opening_id,
        original_bundle_path=original_bundle_path,
        original_result_path=original_result_path,
        targeted_evidence_path=targeted_evidence_path,
        targeted_result_path=targeted_result_path,
    )


def validate(candidate: Mapping[str, Any], opening_id: str, **kwargs) -> dict[str, Any]:
    actual = deepcopy(dict(candidate))
    expected = build(opening_id, _skip_validate=True, **kwargs)
    if actual != expected:
        raise ValueError("targeted remediation bundle evidence/derivation drift")
    _assert_false(actual, "targeted remediation bundle")
    if (
        actual.get("opening_id") != opening_id
        or actual.get("subtype_use_status") != "resolved_after_tighter_crop"
        or actual.get("accepted_for_downstream_research_with_quarantine") is not True
        or actual["original_advisory"]["preserved_as_history"] is not True
        or actual.get("neighboring_visual_cues_present") is not False
        or actual.get("target_cue_isolated") is not True
    ):
        raise ValueError("targeted remediation bundle scope drift")
    payload = {key: value for key, value in actual.items() if key != "candidate_hash"}
    if actual.get("candidate_hash") != _candidate_hash(payload):
        raise ValueError("targeted remediation bundle hash drift")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opening-id", required=True)
    parser.add_argument("--original-bundle", required=True, type=Path)
    parser.add_argument("--original-result", required=True, type=Path)
    parser.add_argument("--targeted-evidence", required=True, type=Path)
    parser.add_argument("--targeted-result", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    selected_path = args.out / "targeted-selected-result.json"
    selected_path.write_text(json.dumps(json.loads(args.targeted_result.read_text(encoding="utf-8")), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = build(
        args.opening_id,
        original_bundle_path=args.original_bundle,
        original_result_path=args.original_result,
        targeted_evidence_path=args.targeted_evidence,
        targeted_result_path=selected_path,
    )
    (args.out / "bundle.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.out / "REPORT.md").write_text(
        f"# {args.opening_id} tighter-crop subtype remediation\n\n"
        "The wider-crop advisory is preserved as history. The tighter pixel-exact crop excludes the neighboring "
        "opening cue, and Gemini attributes the visible swing cue to the target segment. Resolution remains visual "
        "research only; source subtype, vertical, pair, traversability, adjacency, score, and build stay unconfirmed.\n",
        encoding="utf-8",
    )
    print(result["candidate_hash"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build", "validate", "_candidate_hash"]
