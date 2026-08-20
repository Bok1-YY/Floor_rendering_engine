"""Run the public dwg_test corpus through the production CAD ingest contract."""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY.parent))

from Floor_engine_Linux import whole_home_cad  # noqa: E402
from Floor_engine_Linux.config import UPLOAD_DIR  # noqa: E402


def _codes(rows: object) -> list[str]:
    return [str(row.get("code") or "") for row in rows or [] if isinstance(row, dict)]


def _case_summary(item: dict, run_id: str) -> dict:
    source = REPOSITORY / "dwg_test" / item["filename"]
    managed = Path(UPLOAD_DIR) / f"dwg_test_{run_id}_{item['id']}_{source.name}"
    shutil.copy2(source, managed)
    started = time.monotonic()
    report: dict = {}
    model: dict = {}
    row = {
        "id": item["id"], "difficulty": item.get("difficulty"),
        "filename": item["filename"], "source_sha256": item["sha256"],
        "visual_status": "not_reviewed",
        "source_preview_path": str(REPOSITORY / "dwg_test" / item["preview"]),
    }
    try:
        model, report, preview = whole_home_cad.ingest_cad(
            str(managed), f"dwg-production-{run_id}-{item['id']}", timeout=180)
        row.update(status="pass", code="", preview_path=preview)
    except whole_home_cad.CadError as ex:
        details = ex.details or {}
        report = details.get("parse_report") or {}
        model = details.get("model") or {}
        row.update(status="blocked", code=ex.code, message=ex.message,
                   preview_path=report.get("preview_path"))
    except Exception as ex:  # the baseline must archive an unexpected traceback
        row.update(status="exception", code=type(ex).__name__, message=str(ex),
                   traceback=traceback.format_exc())
    metrics = report.get("alignment_metrics") or {}
    unresolved_wall_assemblies = [
        value for value in model.get("wall_assemblies") or []
        if value.get("review_status") not in {
            "accepted", "confirmed", "rejected", "reject"}
        or (value.get("review_status") in {"accepted", "confirmed"}
            and (not value.get("footprint_polygon")
                 or not value.get("centerline")
                 or not value.get("thickness_m")))
    ]
    unresolved_wall_summary = Counter(
        (str(value.get("source_representation") or ""),
         str(value.get("review_status") or ""),
         str(value.get("reason") or ""))
        for value in unresolved_wall_assemblies)
    row.update(
        duration_s=round(time.monotonic() - started, 3),
        selected_candidate_id=report.get("selected_candidate_id") or "",
        rooms=len(model.get("rooms") or []), walls=len(model.get("walls") or []),
        wall_assemblies=len(model.get("wall_assemblies") or []),
        openings=len(model.get("openings") or []),
        production_unresolved_wall_assembly_count=metrics.get(
            "production_unresolved_wall_assembly_count"),
        hard_errors=_codes(report.get("hard_errors")),
        warnings=_codes(report.get("warnings")),
        selected_entity_role_summary=copy.deepcopy(
            report.get("selected_entity_role_summary")),
        raw_opening_summary=copy.deepcopy(report.get("raw_opening_summary")),
        global_wall_topology=copy.deepcopy(report.get("global_wall_topology")),
        unresolved_wall_assembly_summary=[{
            "source_representation": key[0], "review_status": key[1],
            "reason": key[2], "count": count,
        } for key, count in sorted(
            unresolved_wall_summary.items(),
            key=lambda row: (-row[1], row[0]))],
        unresolved_wall_assembly_evidence=[{
            key: copy.deepcopy(value.get(key)) for key in (
                "id", "source_representation", "review_status", "reason",
                "reason_codes", "production_blockers", "source_centerline",
                "source_entity_handles", "source_entities",
                "global_topology_resolution_audit",
            )
        } for value in unresolved_wall_assemblies[:100]],
        report_path=report.get("report_path") or "",
    )
    row["parser_status"] = str(row.get("status") or "")
    unresolved_count = row.get("production_unresolved_wall_assembly_count")
    if (row.get("status") == "pass" and isinstance(unresolved_count, (int, float))
            and unresolved_count > 0):
        # Mirror the public project route: a locally parseable draft with
        # unresolved production wall evidence is not a completed model.
        row.update(
            status="needs_review",
            code="cad_wall_assembly_review_required",
            message=(f"CAD 已生成可检查的 3D 草稿；仍有 {int(unresolved_count)} "
                     "个墙体证据待解决"),
        )
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", default="01,02,03,04,05,06,07,08,09,10")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    manifest = json.loads((REPOSITORY / "dwg_test" / "manifest.json").read_text(
        encoding="utf-8"))
    wanted = {value.strip() for value in args.ids.split(",") if value.strip()}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cases = []
    for item in manifest["files"]:
        if item["id"] not in wanted:
            continue
        row = _case_summary(item, run_id)
        cases.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    payload = {
        "schema_version": 1, "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ingest_contract": "whole_home_cad.ingest_cad",
        "cases": cases,
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, output)
    print(f"baseline={output}")
    return 0 if all(row["status"] != "exception" for row in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
