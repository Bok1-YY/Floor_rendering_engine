from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.goal_loop_v2.common import CURRENT_PATH, atomic_write_json, load_state, now_utc, validate_state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resume only the action recorded in Goal-Loop v2 CURRENT.json.")
    parser.add_argument("--dry-run", action="store_true", help="validate and print without changing state")
    args = parser.parse_args(argv)
    state, contract = load_state()
    if state["goal_status"] == "complete":
        raise SystemExit("Goal-Loop v2 is already complete; no resume action exists")
    result = {"dry_run": args.dry_run, "active_sample": state["active_sample"], "stage": state["stage"], "iteration": state["iteration"], "next_action": state["next_action"]}
    if not args.dry_run and state["goal_status"] == "paused":
        state["goal_status"] = "running"
        state["updated_at"] = now_utc()
        atomic_write_json(CURRENT_PATH, validate_state(state, contract))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
