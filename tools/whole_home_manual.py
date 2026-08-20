# -*- coding: utf-8 -*-
"""Provider-free local CLI for guarded legacy reconciliation.

The default and ``dry-run`` commands only read the explicitly named files.
``apply`` requires every value emitted by the immediately preceding dry-run,
an operator-chosen idempotency key, the dynamic confirmation phrase and a
non-overwriting backup directory.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT = os.path.dirname(REPO)
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)
if 'Floor_engine_server' not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        'Floor_engine_server', os.path.join(REPO, '__init__.py'),
        submodule_search_locations=[REPO])
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load Floor Engine package')
    package = importlib.util.module_from_spec(spec)
    sys.modules['Floor_engine_server'] = package
    spec.loader.exec_module(package)

from Floor_engine_server.whole_home_autopilot import (  # noqa: E402
    DevelopmentAutopilotError,
)
from Floor_engine_server.whole_home_manual import (  # noqa: E402
    apply_legacy_reconciliation,
    preview_legacy_reconciliation,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        'action', nargs='?', default='dry-run', choices=('dry-run', 'apply'))
    parser.add_argument('--session', required=True, help='Exact legacy session JSON path')
    parser.add_argument(
        '--run', action='append', default=[],
        help='Exact run JSON path; repeat for every run bound to the session')
    parser.add_argument('--expected-session-sha256')
    parser.add_argument('--expected-run-manifest-sha256')
    parser.add_argument('--expected-state-version', type=int)
    parser.add_argument('--expected-plan-hash')
    parser.add_argument('--idempotency-key')
    parser.add_argument('--confirmation-phrase')
    parser.add_argument('--backup-dir')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == 'dry-run':
            result = preview_legacy_reconciliation(
                session_path=args.session, run_paths=args.run)
        else:
            required = {
                '--expected-session-sha256': args.expected_session_sha256,
                '--expected-run-manifest-sha256': args.expected_run_manifest_sha256,
                '--expected-state-version': args.expected_state_version,
                '--expected-plan-hash': args.expected_plan_hash,
                '--idempotency-key': args.idempotency_key,
                '--confirmation-phrase': args.confirmation_phrase,
                '--backup-dir': args.backup_dir,
            }
            missing = [name for name, value in required.items() if value in (None, '')]
            if missing:
                raise ValueError('apply requires: ' + ', '.join(missing))
            result = apply_legacy_reconciliation(
                session_path=args.session,
                run_paths=args.run,
                expected_session_sha256=args.expected_session_sha256,
                expected_run_manifest_sha256=args.expected_run_manifest_sha256,
                expected_state_version=args.expected_state_version,
                expected_plan_hash=args.expected_plan_hash,
                idempotency_key=args.idempotency_key,
                confirmation_phrase=args.confirmation_phrase,
                backup_dir=args.backup_dir,
            )
    except DevelopmentAutopilotError as ex:
        print(json.dumps({
            'ok': False, 'error': ex.to_dict(),
            'provider_imported': False, 'provider_calls': 0,
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as ex:
        print(json.dumps({
            'ok': False,
            'error': {'code': 'manual_cli_invalid_input', 'message': str(ex)},
            'provider_imported': False, 'provider_calls': 0,
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({'ok': True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
