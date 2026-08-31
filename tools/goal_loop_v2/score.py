from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.goal_loop_v2.common import load_state, read_json


def _layer(report: Mapping[str, Any], expected: Mapping[str, int], name: str) -> dict[str, Any]:
    if report.get("schema") != "goal-loop-v2-score-layer-v1" or report.get("layer") != name:
        raise ValueError(f"{name}: report schema/layer mismatch")
    rows = report.get("checks")
    if not isinstance(rows, list):
        raise ValueError(f"{name}: checks must be an array")
    by_id = {row.get("id"): row for row in rows if isinstance(row, dict)}
    if set(by_id) != set(expected) or len(rows) != len(expected):
        raise ValueError(f"{name}: check IDs must exactly match the stable contract")
    earned = 0.0
    hard_failures: list[str] = []
    for check_id, weight in expected.items():
        row = by_id[check_id]
        if row.get("status") not in {"pass", "fail"}:
            raise ValueError(f"{name}.{check_id}: status must be pass/fail")
        if row["status"] == "pass":
            earned += weight
        else:
            hard_failures.append(check_id)
    return {"score": round(earned, 3), "hard_failures": hard_failures, "hard_failure_count": len(hard_failures)}


def score_layers(source_report: Mapping[str, Any], artifact_report: Mapping[str, Any], *, previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    _, contract = load_state()
    scoring = contract["score_contract"]
    identity_keys = ("sample_id", "source_hash", "reference_hash", "scoring_version")
    source_identity = {key: source_report.get(key) for key in identity_keys}
    artifact_identity = {key: artifact_report.get(key) for key in identity_keys}
    if source_identity != artifact_identity or source_identity["scoring_version"] != scoring["scoring_version"]:
        raise ValueError("source/artifact score identity mismatch")
    if source_identity["sample_id"] not in contract["samples"] or any(not isinstance(source_identity[key], str) or len(source_identity[key]) != 64 for key in ("source_hash", "reference_hash")):
        raise ValueError("score identity sample/hash is invalid")
    source = _layer(source_report, scoring["source_contract"]["checks"], "source_contract")
    artifact = _layer(artifact_report, scoring["artifact_mechanical"]["checks"], "artifact_mechanical")
    total = min(source["score"], artifact["score"])
    hard_failure_count = source["hard_failure_count"] + artifact["hard_failure_count"]
    if previous is not None and any(previous.get(key) != source_identity[key] for key in identity_keys):
        raise ValueError("previous score identity mismatch")
    known_hard_ids = set(scoring["source_contract"]["checks"]) | set(scoring["artifact_mechanical"]["checks"])
    previous_hard_set = set((previous or {}).get("hard_failures") or [])
    if not previous_hard_set <= known_hard_ids:
        raise ValueError("previous score contains unknown hard blocker IDs")
    current_hard_set = set(source["hard_failures"]) | set(artifact["hard_failures"])
    previous_score = float((previous or {}).get("total_score") or 0.0)
    delta = round(total - previous_score, 3)
    hard_reduction = len(previous_hard_set) - len(current_hard_set)
    no_new_hard = current_hard_set <= previous_hard_set
    strict_subset = current_hard_set < previous_hard_set
    progressed = previous is None or (delta >= contract["minimum_score_gain"] and no_new_hard) or strict_subset
    return {
        "schema": "goal-loop-v2-score-v1",
        "source_contract_score": source["score"],
        "artifact_mechanical_score": artifact["score"],
        "total_score": total,
        "hard_failures": [*source["hard_failures"], *artifact["hard_failures"]],
        "hard_failure_count": hard_failure_count,
        "score_delta": delta,
        "hard_blocker_reduction": hard_reduction,
        "progressed": progressed,
        "accepted": total >= contract["minimum_score"] and hard_failure_count == 0,
        "combination": "min(source_contract_score, artifact_mechanical_score)",
        **source_identity,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score non-compensating Goal-Loop v2 source/artifact layers.")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args(argv)
    result = score_layers(read_json(args.source), read_json(args.artifact), previous=read_json(args.previous) if args.previous else None)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
