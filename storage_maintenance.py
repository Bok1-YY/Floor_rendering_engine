"""Read-only storage inventory and fail-safe sample/thumbnail cleanup."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from collections import defaultdict
from typing import Iterable

from . import config
from .storage_assets import (
    SAMPLE_DIR_NAME,
    asset_lifecycle_lock,
    clear_thumbnail_cache,
    sha256_file,
    store_sample_bytes,
)


_maintenance_lock = threading.Lock()
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
QUARANTINE_DAYS = 30


def storage_maintenance_lock() -> threading.Lock:
    return _maintenance_lock


def _record_files(output_dir: str) -> list[str]:
    found: list[str] = []
    if not os.path.isdir(output_dir):
        return found
    for root, _dirs, files in os.walk(output_dir):
        for name in files:
            if name.endswith("_记录.json"):
                found.append(os.path.join(root, name))
    return sorted(found)


def _read_records(path: str) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _relative(path: str, base: str) -> str:
    return os.path.relpath(path, base).replace("\\", "/")


def _resolve_rel(rel: str, output_dir: str) -> str:
    if not rel:
        return ""
    try:
        base = os.path.realpath(output_dir)
        path = os.path.realpath(os.path.join(base, str(rel).replace("/", os.sep)))
        return path if os.path.commonpath([base, path]) == base else ""
    except (OSError, ValueError):
        return ""


def _iter_image_strings(value) -> Iterable[str]:
    if isinstance(value, str):
        raw = value.split("?", 1)[0]
        if os.path.splitext(raw)[1].lower() in _IMAGE_EXTS:
            yield raw
    elif isinstance(value, dict):
        for child in value.values():
            yield from _iter_image_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_image_strings(child)


def _resolve_image_string(raw: str, output_dir: str) -> str:
    if raw.startswith("/outputs/"):
        return _resolve_rel(raw[len("/outputs/"):], output_dir)
    if os.path.isabs(raw):
        try:
            base = os.path.realpath(output_dir)
            path = os.path.realpath(raw)
            return path if os.path.commonpath([base, path]) == base else ""
        except (OSError, ValueError):
            return ""
    return _resolve_rel(raw, output_dir)


def _file_signature(path: str, base: str) -> dict:
    stat = os.stat(path)
    return {
        "path": _relative(path, base),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _current_orphan_paths(output_dir: str) -> list[str]:
    referenced: set[str] = set()
    for root, _dirs, files in os.walk(output_dir):
        for name in files:
            if not name.endswith('.json'):
                continue
            try:
                with open(os.path.join(root, name), 'r', encoding='utf-8') as handle:
                    payload = json.load(handle)
                for raw in _iter_image_strings(payload):
                    resolved = _resolve_image_string(raw, output_dir)
                    if resolved:
                        referenced.add(resolved)
            except Exception:
                continue
    orphan_paths = []
    for root, dirs, files in os.walk(output_dir):
        dirs[:] = [name for name in dirs if name != SAMPLE_DIR_NAME]
        for name in files:
            path = os.path.realpath(os.path.join(root, name))
            if (os.path.splitext(name)[1].lower() in _IMAGE_EXTS
                    and path not in referenced and '_sample_' not in name):
                orphan_paths.append(path)
    return sorted(orphan_paths)


def _sample_candidates(output_dir: str, records: list[tuple[str, list[dict]]]) -> tuple[set[str], int, int]:
    paths: set[str] = set()
    inline = 0
    missing = 0
    for _json_path, rows in records:
        for row in rows:
            if not isinstance(row, dict):
                continue
            rel = str(row.get("sample_image_file") or "")
            if rel:
                path = _resolve_rel(rel, output_dir)
                if path and os.path.isfile(path):
                    paths.add(path)
                else:
                    missing += 1
            if row.get("sample_image_b64"):
                inline += 1
    for root, _dirs, files in os.walk(output_dir):
        for name in files:
            path = os.path.join(root, name)
            rel = _relative(path, output_dir)
            if "_sample_" in name or rel.startswith(f"{SAMPLE_DIR_NAME}/"):
                if os.path.splitext(name)[1].lower() in _IMAGE_EXTS:
                    paths.add(path)
    return paths, inline, missing


def audit_storage(*, output_dir: str | None = None, thumb_dir: str | None = None,
                  base_dir: str | None = None) -> dict:
    output_dir = os.path.realpath(output_dir or config.MAIN_OUTPUT_DIR)
    thumb_dir = os.path.realpath(thumb_dir or config.THUMB_DIR)
    base_dir = os.path.realpath(base_dir or config.BASE_DIR)
    record_paths = _record_files(output_dir)
    records = [(path, _read_records(path)) for path in record_paths]

    sample_paths, inline_count, missing_sample_refs = _sample_candidates(output_dir, records)
    sample_groups: dict[str, list[str]] = defaultdict(list)
    sample_bytes = 0
    sample_signatures = []
    for path in sorted(sample_paths):
        try:
            digest = sha256_file(path)
            size = os.path.getsize(path)
            sample_groups[digest].append(path)
            sample_bytes += size
            sample_signatures.append(_file_signature(path, output_dir))
        except OSError:
            continue
    duplicate_files = sum(max(0, len(paths) - 1) for paths in sample_groups.values())
    duplicate_bytes = sum(
        sum(os.path.getsize(path) for path in paths[1:])
        for paths in sample_groups.values() if len(paths) > 1
    )

    thumb_paths = []
    if os.path.isdir(thumb_dir):
        thumb_paths = sorted(
            entry.path for entry in os.scandir(thumb_dir)
            if entry.is_file(follow_symlinks=False)
        )
    thumb_groups: dict[str, int] = defaultdict(int)
    thumb_bytes = 0
    thumb_signatures = []
    for path in thumb_paths:
        try:
            thumb_groups[sha256_file(path)] += 1
            thumb_bytes += os.path.getsize(path)
            thumb_signatures.append(_file_signature(path, thumb_dir))
        except OSError:
            continue

    orphan_paths = _current_orphan_paths(output_dir)

    record_signatures = []
    for path in record_paths:
        try:
            record_signatures.append(_file_signature(path, output_dir))
        except OSError:
            continue
    snapshot_payload = {
        "records": record_signatures,
        "samples": sample_signatures,
        "thumbnails": thumb_signatures,
    }
    snapshot_id = hashlib.sha256(
        json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return {
        "snapshot_id": snapshot_id,
        "scopes": ["samples", "thumbnails"],
        "samples": {
            "files": sum(len(paths) for paths in sample_groups.values()),
            "unique_contents": len(sample_groups),
            "duplicate_files": duplicate_files,
            "bytes": sample_bytes,
            "duplicate_bytes": duplicate_bytes,
            "legacy_inline_records": inline_count,
            "missing_references": missing_sample_refs,
            "examples": [
                {
                    "hash": digest,
                    "copies": len(paths),
                    "example": _relative(paths[0], output_dir),
                }
                for digest, paths in sorted(sample_groups.items()) if len(paths) > 1
            ][:20],
        },
        "thumbnails": {
            "files": len(thumb_paths),
            "unique_contents": len(thumb_groups),
            "duplicate_files": sum(max(0, count - 1) for count in thumb_groups.values()),
            "bytes": thumb_bytes,
        },
        "orphan_results": {
            "files": len(orphan_paths),
            "bytes": sum(os.path.getsize(path) for path in orphan_paths if os.path.isfile(path)),
            "examples": [_relative(path, output_dir) for path in sorted(orphan_paths)[:20]],
            "paths": [_relative(path, output_dir) for path in sorted(orphan_paths)[:50]],
            "report_only": True,
        },
        "records": {
            "files": len(record_paths),
            "entries": sum(len(rows) for _path, rows in records),
        },
        "data_root_label": os.path.basename(base_dir) or base_dir,
    }


def cleanup_storage(snapshot_id: str, *, output_dir: str | None = None,
                    thumb_dir: str | None = None, base_dir: str | None = None) -> dict:
    output_dir = os.path.realpath(output_dir or config.MAIN_OUTPUT_DIR)
    thumb_dir = os.path.realpath(thumb_dir or config.THUMB_DIR)
    base_dir = os.path.realpath(base_dir or config.BASE_DIR)
    from .records import load_records_file, record_file_lock, save_records_file

    with _maintenance_lock, asset_lifecycle_lock():
        before = audit_storage(output_dir=output_dir, thumb_dir=thumb_dir, base_dir=base_dir)
        if before["snapshot_id"] != snapshot_id:
            raise RuntimeError("storage_snapshot_changed")

        stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000_000:09d}"
        backup_dir = os.path.join(base_dir, "storage_backups", stamp)
        os.makedirs(backup_dir, exist_ok=False)
        changed: list[tuple[str, list[dict]]] = []
        backups: dict[str, str] = {}
        canonical_hashes: set[str] = set()
        ref_mappings: list[dict] = []

        for json_path in _record_files(output_dir):
            with record_file_lock(json_path):
                rows = load_records_file(json_path)
            dirty = False
            for row in rows:
                if not isinstance(row, dict):
                    continue
                data = b""
                rel = str(row.get("sample_image_file") or "")
                source = _resolve_rel(rel, output_dir) if rel else ""
                if source and os.path.isfile(source):
                    with open(source, "rb") as handle:
                        data = handle.read()
                elif row.get("sample_image_b64"):
                    data = base64.b64decode(row["sample_image_b64"])
                if not data:
                    continue
                canonical = store_sample_bytes(data, output_dir)
                canonical_hashes.add(os.path.splitext(os.path.basename(canonical))[0])
                if rel != canonical or "sample_image_b64" in row:
                    ref_mappings.append({
                        "record_file": _relative(json_path, output_dir),
                        "record_id": str(row.get("id") or ""),
                        "old_ref": rel or "<inline_base64>",
                        "new_ref": canonical,
                        "sha256": os.path.splitext(os.path.basename(canonical))[0],
                    })
                    row["sample_image_file"] = canonical
                    row.pop("sample_image_b64", None)
                    dirty = True
            if dirty:
                changed.append((json_path, rows))

        saved: list[str] = []
        try:
            for json_path, rows in changed:
                rel = _relative(json_path, output_dir)
                backup = os.path.join(backup_dir, "records", *rel.split("/"))
                os.makedirs(os.path.dirname(backup), exist_ok=True)
                shutil.copy2(json_path, backup)
                backups[json_path] = backup
                with record_file_lock(json_path):
                    save_records_file(json_path, rows)
                saved.append(json_path)
        except Exception:
            for json_path in reversed(saved):
                backup = backups.get(json_path)
                if backup and os.path.isfile(backup):
                    with record_file_lock(json_path):
                        shutil.copy2(backup, json_path)
            raise

        current_sample_refs: set[str] = set()
        for json_path in _record_files(output_dir):
            for row in _read_records(json_path):
                rel = str(row.get("sample_image_file") or "") if isinstance(row, dict) else ""
                path = _resolve_rel(rel, output_dir)
                if path:
                    current_sample_refs.add(path)
                    if not os.path.isfile(path):
                        raise RuntimeError(f"rewritten_sample_missing:{rel}")

        removed_samples = 0
        removed_sample_bytes = 0
        for root, dirs, files in os.walk(output_dir):
            dirs[:] = [name for name in dirs if name != SAMPLE_DIR_NAME]
            for name in files:
                if "_sample_" not in name or os.path.splitext(name)[1].lower() not in _IMAGE_EXTS:
                    continue
                path = os.path.realpath(os.path.join(root, name))
                if path in current_sample_refs:
                    continue
                try:
                    digest = sha256_file(path)
                    canonical = os.path.join(output_dir, SAMPLE_DIR_NAME, f"{digest}.jpg")
                    if digest not in canonical_hashes or not os.path.isfile(canonical) or sha256_file(canonical) != digest:
                        continue
                    size = os.path.getsize(path)
                    os.remove(path)
                    removed_samples += 1
                    removed_sample_bytes += size
                except OSError:
                    continue

        removed_thumbs, removed_thumb_bytes = clear_thumbnail_cache(thumb_dir)
        after = audit_storage(output_dir=output_dir, thumb_dir=thumb_dir, base_dir=base_dir)
        net_freed_bytes = max(
            0,
            int(before['samples']['bytes']) + int(before['thumbnails']['bytes'])
            - int(after['samples']['bytes']) - int(after['thumbnails']['bytes']),
        )
        manifest = {
            "version": 1,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "scope": ["samples", "thumbnails"],
            "snapshot_before": before["snapshot_id"],
            "snapshot_after": after["snapshot_id"],
            "rewritten_records": [_relative(path, output_dir) for path, _rows in changed],
            "record_backups": {
                _relative(path, output_dir): _relative(backup, base_dir)
                for path, backup in backups.items()
            },
            "sample_ref_mappings": ref_mappings,
            "removed_sample_files": removed_samples,
            "removed_sample_bytes": removed_sample_bytes,
            "removed_thumbnail_files": removed_thumbs,
            "removed_thumbnail_bytes": removed_thumb_bytes,
            "sample_files_reduced": int(before['samples']['files']) - int(after['samples']['files']),
            "net_freed_bytes": net_freed_bytes,
            "orphan_results_report_only": after["orphan_results"],
        }
        manifest_path = os.path.join(backup_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        return {
            "ok": True,
            "snapshot_id": after["snapshot_id"],
            "rewritten_records": len(changed),
            "removed_sample_files": removed_samples,
            "removed_thumbnail_files": removed_thumbs,
            "sample_files_reduced": int(before['samples']['files']) - int(after['samples']['files']),
            "freed_bytes": net_freed_bytes,
            "backup_manifest": _relative(manifest_path, base_dir),
            "audit": after,
        }


def _manifest_path(base_dir: str, entry_id: str) -> str:
    if not entry_id or any(ch not in '0123456789abcdefghijklmnopqrstuvwxyz_' for ch in entry_id):
        raise RuntimeError('invalid_quarantine_entry')
    root = os.path.realpath(os.path.join(base_dir, 'storage_quarantine'))
    path = os.path.realpath(os.path.join(root, entry_id, 'manifest.json'))
    if os.path.commonpath([root, path]) != root:
        raise RuntimeError('invalid_quarantine_entry')
    return path


def _write_manifest(path: str, manifest: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def list_quarantine(*, base_dir: str | None = None) -> list[dict]:
    base_dir = os.path.realpath(base_dir or config.BASE_DIR)
    root = os.path.join(base_dir, 'storage_quarantine')
    if not os.path.isdir(root):
        return []
    rows = []
    for entry in os.scandir(root):
        path = os.path.join(entry.path, 'manifest.json')
        if not entry.is_dir(follow_symlinks=False) or not os.path.isfile(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                row = json.load(handle)
            row['purge_eligible'] = time.time() >= float(row.get('purge_eligible_at_epoch') or 0)
            rows.append(row)
        except Exception:
            continue
    return sorted(rows, key=lambda row: float(row.get('quarantined_at_epoch') or 0), reverse=True)


def quarantine_orphans(snapshot_id: str, relpaths: list[str], *, output_dir: str | None = None,
                       thumb_dir: str | None = None, base_dir: str | None = None) -> dict:
    output_dir = os.path.realpath(output_dir or config.MAIN_OUTPUT_DIR)
    thumb_dir = os.path.realpath(thumb_dir or config.THUMB_DIR)
    base_dir = os.path.realpath(base_dir or config.BASE_DIR)
    with _maintenance_lock, asset_lifecycle_lock():
        audit = audit_storage(output_dir=output_dir, thumb_dir=thumb_dir, base_dir=base_dir)
        if audit['snapshot_id'] != snapshot_id:
            raise RuntimeError('storage_snapshot_changed')
        orphans = set(_current_orphan_paths(output_dir))
        selected = []
        for rel in dict.fromkeys(str(value or '') for value in relpaths):
            path = _resolve_rel(rel, output_dir)
            if not path or path not in orphans or not os.path.isfile(path):
                raise RuntimeError(f'orphan_reference_changed:{rel}')
            selected.append((rel.replace('\\', '/'), path))
        entries = []
        for rel, source in selected:
            digest = sha256_file(source)
            size = os.path.getsize(source)
            now = time.time()
            entry_id = f"q_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:12]}"
            manifest_path = _manifest_path(base_dir, entry_id)
            payload = os.path.join(os.path.dirname(manifest_path), 'payload' + os.path.splitext(source)[1].lower())
            os.makedirs(os.path.dirname(payload), exist_ok=False)
            try:
                os.replace(source, payload)
                if sha256_file(payload) != digest:
                    raise RuntimeError('quarantine_hash_mismatch')
                manifest = {
                    'entry_id': entry_id,
                    'original_relpath': rel,
                    'sha256': digest,
                    'size': size,
                    'reason': 'unreferenced',
                    'quarantined_at_epoch': now,
                    'quarantined_at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now)),
                    'purge_eligible_at_epoch': now + QUARANTINE_DAYS * 86400,
                    'purge_eligible_at': time.strftime(
                        '%Y-%m-%d %H:%M:%S', time.localtime(now + QUARANTINE_DAYS * 86400)),
                    'payload_name': os.path.basename(payload),
                    'status': 'quarantined',
                }
                _write_manifest(manifest_path, manifest)
                entries.append(manifest)
            except Exception:
                if os.path.isfile(payload) and not os.path.exists(source):
                    os.makedirs(os.path.dirname(source), exist_ok=True)
                    os.replace(payload, source)
                shutil.rmtree(os.path.dirname(manifest_path), ignore_errors=True)
                raise
        return {
            'ok': True,
            'entries': entries,
            'audit': audit_storage(output_dir=output_dir, thumb_dir=thumb_dir, base_dir=base_dir),
        }


def restore_quarantine(entry_id: str, *, output_dir: str | None = None,
                       base_dir: str | None = None) -> dict:
    output_dir = os.path.realpath(output_dir or config.MAIN_OUTPUT_DIR)
    base_dir = os.path.realpath(base_dir or config.BASE_DIR)
    with _maintenance_lock, asset_lifecycle_lock():
        manifest_path = _manifest_path(base_dir, entry_id)
        if not os.path.isfile(manifest_path):
            raise RuntimeError('quarantine_entry_not_found')
        with open(manifest_path, 'r', encoding='utf-8') as handle:
            manifest = json.load(handle)
        if manifest.get('status') != 'quarantined':
            raise RuntimeError('quarantine_entry_not_active')
        payload = os.path.join(os.path.dirname(manifest_path), str(manifest.get('payload_name') or ''))
        target = _resolve_rel(str(manifest.get('original_relpath') or ''), output_dir)
        if not target:
            raise RuntimeError('invalid_restore_target')
        if os.path.exists(target):
            raise RuntimeError('restore_target_exists')
        if not os.path.isfile(payload) or sha256_file(payload) != manifest.get('sha256'):
            raise RuntimeError('quarantine_payload_invalid')
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.replace(payload, target)
        if sha256_file(target) != manifest.get('sha256'):
            os.replace(target, payload)
            raise RuntimeError('restore_hash_mismatch')
        manifest['status'] = 'restored'
        manifest['restored_at_epoch'] = time.time()
        manifest['restored_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        _write_manifest(manifest_path, manifest)
        return {'ok': True, 'entry': manifest}


def purge_quarantine(entry_id: str, confirmation_phrase: str, *, base_dir: str | None = None) -> dict:
    base_dir = os.path.realpath(base_dir or config.BASE_DIR)
    with _maintenance_lock, asset_lifecycle_lock():
        manifest_path = _manifest_path(base_dir, entry_id)
        if not os.path.isfile(manifest_path):
            raise RuntimeError('quarantine_entry_not_found')
        with open(manifest_path, 'r', encoding='utf-8') as handle:
            manifest = json.load(handle)
        if manifest.get('status') != 'quarantined':
            raise RuntimeError('quarantine_entry_not_active')
        if time.time() < float(manifest.get('purge_eligible_at_epoch') or 0):
            raise RuntimeError('quarantine_retention_active')
        if confirmation_phrase != '永久删除':
            raise RuntimeError('purge_confirmation_required')
        payload = os.path.join(os.path.dirname(manifest_path), str(manifest.get('payload_name') or ''))
        if not os.path.isfile(payload) or sha256_file(payload) != manifest.get('sha256'):
            raise RuntimeError('quarantine_payload_invalid')
        freed = os.path.getsize(payload)
        os.remove(payload)
        manifest['status'] = 'purged'
        manifest['purged_at_epoch'] = time.time()
        manifest['purged_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        manifest['freed_bytes'] = freed
        _write_manifest(manifest_path, manifest)
        return {'ok': True, 'entry': manifest, 'freed_bytes': freed}


__all__ = [
    "QUARANTINE_DAYS", "audit_storage", "cleanup_storage", "list_quarantine",
    "purge_quarantine", "quarantine_orphans", "restore_quarantine",
    "storage_maintenance_lock",
]
