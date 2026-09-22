"""Durable admission receipts. A receipt outlives the queue card it identifies."""
import hashlib
import json
import os
import math
import re
from pathlib import Path
import tempfile
import threading
import time
import uuid

from fastapi import HTTPException

from . import records

LOCK = threading.RLock()
VERSION = 1


def unavailable():
    return HTTPException(503, {'code': 'submission_storage_unavailable',
                              'message': '提交凭据无法可靠读取或保存；未自动重新提交'})


def canonical_id(value):
    parsed = uuid.UUID(str(value))
    if parsed.version != 4:
        raise ValueError('UUID v4 required')
    return str(parsed)


def folder():
    root = Path(records.MAIN_OUTPUT_DIR).resolve()
    path = root / '.job_submissions'
    if not path.resolve().is_relative_to(root):
        raise ValueError('Invalid submission directory')
    return path


def atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.submission_', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def identity():
    try:
        with LOCK:
            root = folder()
            marker = root / 'store.json'
            if not marker.exists():
                # Never manufacture a new identity over orphaned receipts.
                if root.exists() and any(root.iterdir()):
                    raise ValueError('Missing store identity')
                atomic_write(marker, {'version': VERSION, 'store_id': str(uuid.uuid4())})
            data = json.loads(marker.read_text(encoding='utf-8'))
            if data['version'] != VERSION:
                raise ValueError('Unsupported store version')
            return canonical_id(data['store_id'])
    except Exception as exc:
        raise unavailable() from exc


def health():
    try:
        return {'version': VERSION, 'store_id': identity(), 'ready': True}
    except HTTPException:
        return {'version': VERSION, 'store_id': None, 'ready': False}


def require_store(store_id):
    actual = identity()
    if store_id != actual:
        raise HTTPException(409, {'code': 'submission_store_mismatch',
                                 'message': '当前数据目录与原提交不同，请核对服务和数据目录'})
    return actual


def receipt_path(submission_id):
    key = canonical_id(submission_id)
    root = folder()
    result = root / key[:2] / (key + '.json')
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError('Invalid receipt path')
    return result


def read(submission_id):
    try:
        path = receipt_path(submission_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        if (data['version'] != VERSION or data['submission_id'] != canonical_id(submission_id)
                or data['store_id'] != identity()
                or data['phase'] not in ('prepared', 'dispatch_committed')
                or data['kind'] not in ('job', 'free')
                or not isinstance(data['digest'], str) or not re.fullmatch(r'[0-9a-f]{64}', data['digest'])
                or not isinstance(data['created_at'], (int, float)) or not math.isfinite(data['created_at'])
                or not isinstance(data['job_id'], str) or not re.fullmatch(r'job_[0-9a-f]{32}', data['job_id'])):
            raise ValueError('Invalid receipt')
        return data
    except Exception as exc:
        raise unavailable() from exc


def write(receipt):
    try:
        atomic_write(receipt_path(receipt['submission_id']), receipt)
    except Exception as exc:
        raise unavailable() from exc


def digest(kind, request):
    payload = request.model_dump(mode='json', exclude={'submission_id', 'submission_store_id', 'api_key'})
    encoded = json.dumps({'version': VERSION, 'kind': kind, 'payload': payload},
                         sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def prepared(submission_id, store_id, kind, fingerprint, job_id):
    return {'version': VERSION, 'submission_id': submission_id, 'store_id': store_id,
            'kind': kind, 'digest': fingerprint, 'job_id': job_id,
            'created_at': time.time(), 'phase': 'prepared'}
