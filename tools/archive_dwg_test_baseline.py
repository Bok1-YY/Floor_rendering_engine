"""Archive every DWG production-baseline outcome into the common records UI.

This command deliberately publishes failed/pending evidence.  It never turns a
production parser result into independent truth and it never upgrades an audit
to passed: that still requires a verified DwgGeometryTruthV1 plus canonical
WebGL/overlay/diff evidence through whole_home_dwg_audit.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY.parent))

from Floor_engine_Linux.whole_home_dwg_audit import (  # noqa: E402
    DwgGeometryTruthV1,
    VISUAL_CHECK_IDS,
    archive_dwg_live_geometry_audit,
)


def default_output_root(*, environment: dict[str, str] | None = None,
                        repository: Path = REPOSITORY) -> Path:
    """Return the output directory used by the source checkout launcher.

    ``start-windows.bat`` / ``start_whole_home_manual.ps1`` set
    ``FLOOR_DATA_DIR=<repository>/data`` before importing the application.
    The audit publisher must resolve the same root even when it is invoked as
    a standalone developer command; otherwise records are valid on disk but
    invisible to the running application's history API.
    """
    values = os.environ if environment is None else environment
    configured = str(values.get("FLOOR_DATA_DIR") or "").strip()
    data_root = Path(configured).expanduser() if configured else repository / "data"
    return data_root.resolve() / "output_files"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _metric(metric_id: str, label: str, actual: float,
            operator: str, threshold: float, unit: str) -> dict[str, Any]:
    return {
        "metric_id": metric_id,
        "label": label,
        "actual": float(actual),
        "operator": operator,
        "threshold": float(threshold),
        "unit": unit,
    }


def _run(case: dict[str, Any], review: dict[str, Any], *, executed_at: str,
         run_id: str) -> dict[str, Any]:
    checks = review.get("checks") or {}
    visual_checks = []
    for check_id in VISUAL_CHECK_IDS:
        raw = checks.get(check_id)
        if not isinstance(raw, list) or len(raw) != 2:
            raise ValueError(f"case {case.get('id')} missing visual check {check_id}")
        visual_checks.append({
            "check_id": check_id,
            "status": str(raw[0]),
            "note": str(raw[1]),
        })

    hard_errors = [str(value) for value in case.get("hard_errors") or [] if str(value)]
    metrics = [
        _metric("geometry_hard_error_count", "几何硬错误数",
                len(hard_errors), "<=", 0, "count"),
        _metric("modeled_wall_count", "正式/复核墙行数",
                int(case.get("walls") or 0), ">=", 1, "count"),
        _metric("modeled_room_count", "已建房间/语义区数量",
                int(case.get("rooms") or 0), ">=", 1, "count"),
    ]
    unresolved = case.get("production_unresolved_wall_assembly_count")
    if isinstance(unresolved, (int, float)):
        metrics.append(_metric(
            "production_unresolved_wall_assembly_count", "未解决墙体证据数",
            float(unresolved), "<=", 0, "count"))
    opening_summary = case.get("raw_opening_summary")
    if isinstance(opening_summary, dict) and int(opening_summary.get("candidate_count") or 0) > 0:
        candidate_count = int(opening_summary["candidate_count"])
        accepted_count = int(opening_summary.get("accepted_count") or 0)
        metrics.append(_metric(
            "raw_opening_binding_ratio", "原始门窗候选绑定率",
            accepted_count / candidate_count, ">=", 1, "ratio"))
    topology = case.get("global_wall_topology")
    if isinstance(topology, dict) and topology.get("source_coverage_ratio") is not None:
        metrics.append(_metric(
            "source_derivation_coverage", "CAD 墙体来源覆盖率",
            float(topology["source_coverage_ratio"]), ">=", 1, "ratio"))

    issues: list[dict[str, Any]] = [
        {"code": code, "severity": "hard"} for code in hard_errors
    ]
    if isinstance(unresolved, (int, float)) and unresolved > 0:
        issues.append({
            "code": "cad_wall_assembly_review_required",
            "severity": "hard",
            "count": int(unresolved),
        })
    issues.extend({
        "code": f"visual_{row['check_id']}_failed",
        "severity": "hard",
        "note": row["note"],
    } for row in visual_checks if row["status"] == "failed")
    issues.append({"code": "independent_truth_pending", "severity": "warning"})

    return {
        "schema_name": "DwgLiveGeometryAuditV1",
        "case_id": str(case["id"]),
        "title": f"DWG {case['id']} production baseline {run_id}",
        "level": str(case.get("difficulty") or "L1"),
        "status": "failed",
        "executed_at": executed_at,
        "source_sha256": str(case["source_sha256"]),
        "project_id": f"offline-dwg-production-{run_id}-{case['id']}",
        "revision": "baseline-1",
        "model_facts_hash": "",
        "geometry_manifest_hash": "",
        "camera_contract_hash": "",
        "metrics": metrics,
        "visual_checks": visual_checks,
        "issues": issues,
    }


def archive_baseline(
    baseline_path: Path,
    review_path: Path,
    *,
    dataset_dir: Path,
    truth_dir: Path,
    output_root: Path,
) -> list[dict[str, Any]]:
    baseline = _load(baseline_path)
    review = _load(review_path)
    reviews = review.get("cases") or {}
    if not isinstance(reviews, dict):
        raise ValueError("multimodal review cases must be an object")
    run_id = str(baseline.get("run_id") or "unknown")
    executed_at = str(review.get("reviewed_at") or baseline.get("generated_at") or "")
    results = []
    for case in baseline.get("cases") or []:
        case_id = str(case.get("id") or "")
        if case_id not in reviews:
            raise ValueError(f"missing multimodal review for case {case_id}")
        source = (dataset_dir / str(case["filename"])).resolve()
        truth_path = (truth_dir / f"{case_id}.truth.json").resolve()
        truth = DwgGeometryTruthV1.load(truth_path, source_path=source)
        evidence: dict[str, Path] = {
            "cad_preview": Path(str(case["source_preview_path"])).resolve(),
            "audit_report": baseline_path.resolve(),
        }
        failure_preview = Path(str(case.get("preview_path") or ""))
        if str(case.get("preview_path") or "") and failure_preview.is_file():
            evidence["failure_screenshot"] = failure_preview.resolve()
        result = archive_dwg_live_geometry_audit(
            truth,
            _run(case, reviews[case_id], executed_at=executed_at, run_id=run_id),
            evidence,
            output_root=output_root,
            source_path=source,
        )
        results.append(result)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("review", type=Path)
    parser.add_argument("--dataset-dir", type=Path, default=REPOSITORY / "dwg_test")
    parser.add_argument("--truth-dir", type=Path, default=REPOSITORY / "dwg_test" / "truth")
    parser.add_argument("--output-root", type=Path, default=default_output_root())
    arguments = parser.parse_args()
    results = archive_baseline(
        arguments.baseline.resolve(),
        arguments.review.resolve(),
        dataset_dir=arguments.dataset_dir.resolve(),
        truth_dir=arguments.truth_dir.resolve(),
        output_root=arguments.output_root.resolve(),
    )
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
