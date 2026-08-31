from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.goal_loop_v2.common import load_state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read and validate the durable Goal-Loop v2 state.")
    parser.add_argument("--json", action="store_true", help="print the complete state as JSON")
    args = parser.parse_args(argv)
    state, _ = load_state()
    if args.json:
        print(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(f"{state['goal_status']} · {state['active_sample']} · {state['stage']} · iteration {state['iteration']}/2")
        print(f"NEXT: {state['next_action']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
