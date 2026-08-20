# -*- coding: utf-8 -*-
"""One-time paid confirmations for panorama edit and seam repair.

The preview binds every value that can affect cost or output.  Claiming a
stage is intentionally irreversible: a provider failure still consumes that
stage's allowance, which prevents ambiguous network failures from being
re-submitted automatically.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Optional


PANO_PAID_POLICY = 'pano_paid_preview_v1'
PANO_PAID_PREVIEW_TTL_SECONDS = 15 * 60
_LOCK = threading.RLock()
_PREVIEWS: dict[str, dict] = {}
_BOUND_KEYS_V1 = (
    'policy', 'project_id', 'pano_id', 'source_hash', 'provider', 'endpoint',
    'model_id', 'snapshot_locked', 'output_size', 'edit_prompt_sha256',
    'repair_band_deg', 'actor',
)
_BOUND_KEYS_WITH_REFERENCES = _BOUND_KEYS_V1 + ('consistency_reference_hashes',)
_BOUND_KEYS = _BOUND_KEYS_WITH_REFERENCES + ('engine', 'generation_params')


def _secure_text_equal(left: object, right: object) -> bool:
    """compare_digest 的 str 变体仅支持 ASCII；确认短语允许中文，统一比较 UTF-8 bytes。"""
    return hmac.compare_digest(str(left or '').encode('utf-8'), str(right or '').encode('utf-8'))


def _stable_hash(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def create_pano_paid_preview(*, project_id: str, pano_id: str, source_hash: str,
                             provider: str, endpoint: str, model_id: str,
                             output_size: str, edit_prompt: str,
                             repair_band_deg: float, actor: str,
                             consistency_reference_paths: Optional[list[str]] = None,
                             consistency_reference_hashes: Optional[list[str]] = None,
                             engine: str = 'gpt-image-2',
                             generation_params: Optional[dict] = None) -> dict:
    now = time.time()
    preview_id = f'panopreview_{secrets.token_hex(12)}'
    bound = {
        'policy': PANO_PAID_POLICY,
        'project_id': project_id,
        'pano_id': pano_id,
        'source_hash': source_hash,
        'provider': provider,
        'endpoint': endpoint,
        'model_id': model_id,
        'snapshot_locked': provider == 'openai',
        'output_size': output_size,
        'edit_prompt_sha256': hashlib.sha256(edit_prompt.encode('utf-8')).hexdigest(),
        'repair_band_deg': round(float(repair_band_deg), 4),
        'actor': actor,
        'consistency_reference_hashes': [
            str(value) for value in consistency_reference_hashes or []],
        'engine': str(engine or 'gpt-image-2'),
        'generation_params': dict(generation_params or {}),
    }
    preview_sha256 = _stable_hash(bound)
    confirmation_phrase = f'确认全景付费 {preview_id[-8:]} {preview_sha256[:10]}'
    row = {
        **bound,
        'preview_id': preview_id,
        'preview_sha256': preview_sha256,
        'confirmation_phrase': confirmation_phrase,
        'edit_prompt': edit_prompt,
        'consistency_reference_paths': [
            str(value) for value in consistency_reference_paths or []],
        'created_at': now,
        'expires_at': now + PANO_PAID_PREVIEW_TTL_SECONDS,
        'edit_claimed': False,
        'repair_claimed': False,
    }
    with _LOCK:
        _PREVIEWS[preview_id] = row
    return public_pano_paid_preview(row)


def public_pano_paid_preview(row: dict) -> dict:
    return {
        key: row.get(key) for key in (
            'policy', 'preview_id', 'preview_sha256', 'confirmation_phrase',
            'project_id', 'pano_id', 'source_hash', 'provider', 'endpoint',
            'model_id', 'snapshot_locked', 'output_size', 'edit_prompt_sha256',
            'repair_band_deg', 'consistency_reference_hashes',
            'engine', 'generation_params',
            'created_at', 'expires_at')
    } | {'caps': {
        'edit_calls': 1,
        'repair_calls': 0 if row.get('engine') == 'flux-canny' else 1,
    }}


def persistable_pano_paid_preview(preview_id: str) -> dict:
    """返回可随项目落盘的完整状态；调用方不得把它直接暴露到 API view。"""
    with _LOCK:
        row = _PREVIEWS.get(str(preview_id or ''))
        if not row:
            raise ValueError('pano_paid_preview_missing')
        return dict(row)


def restore_pano_paid_preview(row: dict) -> None:
    """从可信项目 JSON 恢复预览；hash/短语不一致时 fail closed。"""
    if not isinstance(row, dict) or row.get('policy') != PANO_PAID_POLICY:
        raise ValueError('pano_paid_preview_invalid')
    preview_id = str(row.get('preview_id') or '')
    # Pre-v2 previews did not bind cross-hotspot references.  They remain
    # restorable for their original one-shot request, while every new preview
    # includes even an empty reference-hash list in the signed payload.
    if 'engine' in row or 'generation_params' in row:
        keys = _BOUND_KEYS
    elif 'consistency_reference_hashes' in row:
        keys = _BOUND_KEYS_WITH_REFERENCES
    else:
        keys = _BOUND_KEYS_V1
    bound = {key: row.get(key) for key in keys}
    expected_hash = _stable_hash(bound)
    expected_phrase = f'确认全景付费 {preview_id[-8:]} {expected_hash[:10]}'
    if (not preview_id or not _secure_text_equal(row.get('preview_sha256'), expected_hash)
            or not _secure_text_equal(row.get('confirmation_phrase'), expected_phrase)
            or hashlib.sha256(str(row.get('edit_prompt') or '').encode('utf-8')).hexdigest()
            != str(row.get('edit_prompt_sha256') or '')):
        raise ValueError('pano_paid_preview_tampered')
    with _LOCK:
        _PREVIEWS.setdefault(preview_id, dict(row))


def claim_pano_paid_stage(preview_id: str, confirmation_phrase: str, *, stage: str,
                          project_id: str, pano_id: str, source_hash: str,
                          now: Optional[float] = None) -> dict:
    current_time = time.time() if now is None else float(now)
    if stage not in {'edit', 'repair'}:
        raise ValueError('pano_paid_stage_invalid')
    with _LOCK:
        row = _PREVIEWS.get(str(preview_id or ''))
        if not row:
            raise ValueError('pano_paid_preview_missing')
        if current_time > float(row.get('expires_at') or 0):
            raise ValueError('pano_paid_preview_expired')
        expected = str(row.get('confirmation_phrase') or '')
        if not _secure_text_equal(expected, confirmation_phrase):
            raise ValueError('pano_paid_confirmation_mismatch')
        for key, value in (
                ('project_id', project_id), ('pano_id', pano_id), ('source_hash', source_hash)):
            if not _secure_text_equal(row.get(key), value):
                raise ValueError(f'pano_paid_{key}_mismatch')
        claimed_key = f'{stage}_claimed'
        if row.get(claimed_key):
            raise ValueError(f'pano_paid_{stage}_already_claimed')
        if stage == 'repair' and not row.get('edit_claimed'):
            raise ValueError('pano_paid_repair_requires_edit')
        row[claimed_key] = True
        row[f'{stage}_claimed_at'] = current_time
        return dict(row)


def reset_pano_paid_previews_for_tests() -> None:
    with _LOCK:
        _PREVIEWS.clear()


__all__ = [
    'PANO_PAID_POLICY', 'PANO_PAID_PREVIEW_TTL_SECONDS',
    'claim_pano_paid_stage', 'create_pano_paid_preview',
    'persistable_pano_paid_preview', 'public_pano_paid_preview',
    'reset_pano_paid_previews_for_tests', 'restore_pano_paid_preview',
]
