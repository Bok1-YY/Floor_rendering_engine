# -*- coding: utf-8 -*-
"""Safe local CLI for the agent-workflow store; it never executes commands."""
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

from Floor_engine_server.whole_home_agent_workflow import (  # noqa: E402
    archive_source_snapshot,
    claim_task,
    complete_task,
    create_workflow,
    get_workflow,
    heartbeat_task,
    recover_workflow_projection,
    transition_workflow,
)
from Floor_engine_server.server_schemas import WholeHomeAgentTaskResult  # noqa: E402


def _json_file(path: str) -> dict:
    full = os.path.realpath(path)
    with open(full, 'r', encoding='utf-8') as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError('JSON input must be an object')
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)

    create = sub.add_parser('create')
    create.add_argument('--request', required=True, help='JSON file with create_workflow fields')

    get = sub.add_parser('get')
    get.add_argument('workflow_id')

    claim = sub.add_parser('claim')
    claim.add_argument('workflow_id')
    claim.add_argument('task_id')
    claim.add_argument('agent_id')
    claim.add_argument('--expected-version', type=int, required=True)
    claim.add_argument('--lease-seconds', type=int, default=300)
    claim.add_argument('--idempotency-key', required=True)

    heartbeat = sub.add_parser('heartbeat')
    heartbeat.add_argument('workflow_id')
    heartbeat.add_argument('task_id')
    heartbeat.add_argument('--lease-token', required=True)
    heartbeat.add_argument('--expected-version', type=int, required=True)
    heartbeat.add_argument('--lease-seconds', type=int, default=300)
    heartbeat.add_argument('--idempotency-key', required=True)

    complete = sub.add_parser('complete')
    complete.add_argument('workflow_id')
    complete.add_argument('task_id')
    complete.add_argument('--lease-token', required=True)
    complete.add_argument('--expected-version', type=int, required=True)
    complete.add_argument('--result', required=True, help='JSON file with structured task result')
    complete.add_argument('--idempotency-key', required=True)

    transition = sub.add_parser('transition')
    transition.add_argument('workflow_id')
    transition.add_argument('transition_action', choices=('pause', 'resume', 'cancel'))
    transition.add_argument('--expected-version', type=int, required=True)
    transition.add_argument('--reason', default='')
    transition.add_argument('--idempotency-key', required=True)

    recover = sub.add_parser('recover')
    recover.add_argument('workflow_id')

    snapshot = sub.add_parser('snapshot-source')
    snapshot.add_argument('workflow_id')
    snapshot.add_argument('paths', nargs='+', help='Repo-relative source paths')
    snapshot.add_argument('--expected-version', type=int, required=True)
    snapshot.add_argument('--idempotency-key', required=True)

    contract = sub.add_parser('test-contract')
    contract.add_argument('workflow_id')

    args = parser.parse_args(argv)
    if args.action == 'create':
        response = create_workflow(**_json_file(args.request))
    elif args.action == 'get':
        response = get_workflow(args.workflow_id)
    elif args.action == 'claim':
        response = claim_task(
            workflow_id=args.workflow_id, task_id=args.task_id,
            agent_id=args.agent_id, expected_version=args.expected_version,
            lease_seconds=args.lease_seconds, idempotency_key=args.idempotency_key)
    elif args.action == 'heartbeat':
        response = heartbeat_task(
            workflow_id=args.workflow_id, task_id=args.task_id,
            lease_token=args.lease_token, expected_version=args.expected_version,
            lease_seconds=args.lease_seconds, idempotency_key=args.idempotency_key)
    elif args.action == 'complete':
        result = WholeHomeAgentTaskResult.model_validate(
            _json_file(args.result)).model_dump()
        response = complete_task(
            workflow_id=args.workflow_id, task_id=args.task_id,
            lease_token=args.lease_token, expected_version=args.expected_version,
            result=result, idempotency_key=args.idempotency_key)
    elif args.action == 'transition':
        response = transition_workflow(
            workflow_id=args.workflow_id, action=args.transition_action,
            expected_version=args.expected_version, reason=args.reason,
            idempotency_key=args.idempotency_key)
    elif args.action == 'recover':
        response = recover_workflow_projection(args.workflow_id)
    elif args.action == 'snapshot-source':
        response = archive_source_snapshot(
            workflow_id=args.workflow_id, source_root=REPO,
            relative_paths=args.paths, expected_version=args.expected_version,
            idempotency_key=args.idempotency_key)
    else:
        response = get_workflow(args.workflow_id)['test_collection_contract']
    json.dump(response, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
