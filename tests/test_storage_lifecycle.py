import base64
import hashlib
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from Floor_engine_server import records
from Floor_engine_server import storage_maintenance
from Floor_engine_server.runtime_paths import resolve_data_dir
from Floor_engine_server.storage_assets import (
    output_thumb_cache_path,
    store_sample_image,
)
from Floor_engine_server.storage_maintenance import (
    audit_storage, cleanup_storage, list_quarantine, purge_quarantine,
    quarantine_orphans, restore_quarantine,
)


def _jpeg_bytes(color="tan"):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 50), color).save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def test_source_runtime_defaults_to_project_data(monkeypatch, tmp_path):
    monkeypatch.delenv("FLOOR_DATA_DIR", raising=False)
    assert resolve_data_dir(str(tmp_path)) == str(tmp_path / "data")
    custom = tmp_path / "custom"
    monkeypatch.setenv("FLOOR_DATA_DIR", str(custom))
    assert resolve_data_dir(str(tmp_path)) == str(custom)


def test_content_addressed_sample_reuses_one_file(tmp_path):
    out = tmp_path / "output_files"
    out.mkdir(exist_ok=True)
    image = Image.new("RGB", (800, 1000), "tan")
    first = store_sample_image(image, str(out))
    second = store_sample_image(image, str(out))
    assert first == second
    assert len(list((out / "_samples").glob("*.jpg"))) == 1


def test_concurrent_sample_writes_are_atomic(tmp_path):
    out = tmp_path / "output_files"
    out.mkdir(exist_ok=True)

    def write():
        return store_sample_image(Image.new("RGB", (64, 64), "sienna"), str(out))

    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda _index: write(), range(40)))
    assert len(set(paths)) == 1
    files = list((out / "_samples").iterdir())
    assert len(files) == 1 and files[0].suffix == ".jpg"


def test_legacy_inline_samples_migrate_to_one_asset(tmp_path, monkeypatch):
    out = tmp_path / "output_files"
    material = out / "oak"
    material.mkdir(parents=True)
    monkeypatch.setattr(records, "MAIN_OUTPUT_DIR", str(out))
    encoded = base64.b64encode(_jpeg_bytes()).decode("ascii")
    path = material / "oak_记录.json"
    records.save_records_file(str(path), [
        {"id": "r1", "sample_image_b64": encoded, "results": []},
        {"id": "r2", "sample_image_b64": encoded, "results": []},
    ])

    assert records.migrate_record_file(str(path)) is True
    migrated = records.load_records_file(str(path))
    assert migrated[0]["sample_image_file"] == migrated[1]["sample_image_file"]
    assert all("sample_image_b64" not in row for row in migrated)
    assert len(list((out / "_samples").glob("*.jpg"))) == 1
    assert records.migrate_record_file(str(path)) is False


def test_cleanup_rewrites_refs_backs_up_and_is_idempotent(tmp_path):
    base = tmp_path / "data"
    out = base / "output_files"
    thumbs = base / "_ng_thumbs"
    material = out / "oak"
    material.mkdir(parents=True)
    thumbs.mkdir(parents=True)
    sample = _jpeg_bytes()
    first = out / "oak_sample_one.jpg"
    second = out / "oak_sample_two.jpg"
    first.write_bytes(sample)
    second.write_bytes(sample)
    record = material / "oak_记录.json"
    record.write_text(json.dumps([
        {"id": "r1", "sample_image_file": first.name, "results": []},
        {"id": "r2", "sample_image_file": second.name, "results": []},
    ]), encoding="utf-8")
    (thumbs / "out_old.jpg").write_bytes(b"cache")
    (thumbs / "out_old_2.jpg").write_bytes(b"cache")

    before = audit_storage(output_dir=str(out), thumb_dir=str(thumbs), base_dir=str(base))
    assert before["samples"]["duplicate_files"] == 1
    assert before["thumbnails"]["files"] == 2
    result = cleanup_storage(
        before["snapshot_id"], output_dir=str(out), thumb_dir=str(thumbs), base_dir=str(base),
    )

    rows = json.loads(record.read_text(encoding="utf-8"))
    assert rows[0]["sample_image_file"] == rows[1]["sample_image_file"]
    assert rows[0]["sample_image_file"].startswith("_samples/")
    assert not first.exists() and not second.exists()
    assert len(list((out / "_samples").glob("*.jpg"))) == 1
    assert list(thumbs.iterdir()) == []
    assert (base / result["backup_manifest"]).is_file()
    assert result["sample_files_reduced"] == 1
    assert result["freed_bytes"] == len(sample) + len(b"cache") * 2

    again = cleanup_storage(
        result["snapshot_id"], output_dir=str(out), thumb_dir=str(thumbs), base_dir=str(base),
    )
    assert again["rewritten_records"] == 0
    assert again["removed_sample_files"] == 0
    assert again["removed_thumbnail_files"] == 0


def test_cleanup_rejects_stale_snapshot(tmp_path):
    base = tmp_path / "data"
    out = base / "output_files"
    thumbs = base / "_ng_thumbs"
    out.mkdir(parents=True)
    thumbs.mkdir()
    audit = audit_storage(output_dir=str(out), thumb_dir=str(thumbs), base_dir=str(base))
    (thumbs / "new.jpg").write_bytes(b"changed")
    try:
        cleanup_storage(audit["snapshot_id"], output_dir=str(out), thumb_dir=str(thumbs), base_dir=str(base))
    except RuntimeError as exc:
        assert str(exc) == "storage_snapshot_changed"
    else:
        raise AssertionError("stale snapshot was accepted")


def test_cleanup_restores_already_written_records_when_later_write_fails(tmp_path, monkeypatch):
    base = tmp_path / "data"
    out = base / "output_files"
    thumbs = base / "_ng_thumbs"
    thumbs.mkdir(parents=True)
    originals = {}
    for index, color in enumerate(("tan", "sienna"), start=1):
        material = out / f"m{index}"
        material.mkdir(parents=True)
        sample = out / f"m{index}_sample_old.jpg"
        sample.write_bytes(_jpeg_bytes(color))
        record = material / f"m{index}_记录.json"
        record.write_text(json.dumps([
            {"id": f"r{index}", "sample_image_file": sample.name, "results": []},
        ]), encoding="utf-8")
        originals[record] = record.read_bytes()

    audit = audit_storage(output_dir=str(out), thumb_dir=str(thumbs), base_dir=str(base))
    original_save = records.save_records_file
    calls = 0

    def fail_second(path, rows):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected write failure")
        return original_save(path, rows)

    monkeypatch.setattr(records, "save_records_file", fail_second)
    try:
        storage_maintenance.cleanup_storage(
            audit["snapshot_id"], output_dir=str(out), thumb_dir=str(thumbs), base_dir=str(base),
        )
    except OSError as exc:
        assert "injected" in str(exc)
    else:
        raise AssertionError("injected failure did not abort cleanup")

    for record, original in originals.items():
        assert record.read_bytes() == original
    assert len(list(out.glob("*_sample_old.jpg"))) == 2


def test_result_file_deleted_only_after_last_record_reference(tmp_path, monkeypatch):
    out = tmp_path / "output_files"
    thumbs = tmp_path / "thumbs"
    material = out / "oak"
    material.mkdir(parents=True)
    thumbs.mkdir()
    monkeypatch.setattr(records, "MAIN_OUTPUT_DIR", str(out))
    monkeypatch.setattr(records, "THUMB_DIR", str(thumbs))
    image = out / "shared.jpg"
    Image.new("RGB", (8, 8), "red").save(image)
    thumb = output_thumb_cache_path(str(image), 480, str(thumbs))
    os.makedirs(os.path.dirname(thumb), exist_ok=True)
    with open(thumb, "wb") as handle:
        handle.write(b"thumb")
    record = material / "oak_记录.json"
    records.save_records_file(str(record), [
        {"id": "r1", "results": [{"result_id": "a", "result_image_file": image.name}]},
        {"id": "r2", "results": [{"result_id": "b", "result_image_file": image.name}]},
    ])

    first = records.delete_result_image(str(record), "r1", "a")
    assert first["kept_shared"] is True and image.exists()
    second = records.delete_result_image(str(record), "r2", "b")
    assert second["file_deleted"] is True and not image.exists()
    assert second["thumbnail_files_deleted"] == 1 and not os.path.exists(thumb)


def test_delete_record_keeps_shared_sample(tmp_path, monkeypatch):
    out = tmp_path / "output_files"
    thumbs = tmp_path / "thumbs"
    material = out / "oak"
    material.mkdir(parents=True)
    thumbs.mkdir()
    monkeypatch.setattr(records, "MAIN_OUTPUT_DIR", str(out))
    monkeypatch.setattr(records, "THUMB_DIR", str(thumbs))
    sample = out / "sample.jpg"
    sample.write_bytes(_jpeg_bytes())
    record = material / "oak_记录.json"
    records.save_records_file(str(record), [
        {"id": "r1", "sample_image_file": sample.name, "results": []},
        {"id": "r2", "sample_image_file": sample.name, "results": []},
    ])
    first = records.delete_record_entry(str(record), "r1")
    assert first["kept_shared"] == 1 and sample.exists()
    second = records.delete_record_entry(str(record), "r2")
    assert second["files_deleted"] == 1 and not sample.exists()


def test_orphan_quarantine_and_restore_are_hash_verified(tmp_path):
    base = tmp_path / "data"
    out = base / "output_files"
    thumbs = base / "_ng_thumbs"
    out.mkdir(parents=True)
    thumbs.mkdir()
    orphan = out / "legacy.png"
    Image.new("RGB", (12, 8), "blue").save(orphan)
    original_hash = hashlib.sha256(orphan.read_bytes()).hexdigest()
    audit = audit_storage(output_dir=str(out), thumb_dir=str(thumbs), base_dir=str(base))

    result = quarantine_orphans(
        audit["snapshot_id"], ["legacy.png"],
        output_dir=str(out), thumb_dir=str(thumbs), base_dir=str(base),
    )
    entry = result["entries"][0]
    assert not orphan.exists()
    assert entry["sha256"] == original_hash
    assert list_quarantine(base_dir=str(base))[0]["status"] == "quarantined"

    restored = restore_quarantine(entry["entry_id"], output_dir=str(out), base_dir=str(base))
    assert restored["entry"]["status"] == "restored"
    assert orphan.exists()
    assert hashlib.sha256(orphan.read_bytes()).hexdigest() == original_hash


def test_quarantine_purge_requires_retention_and_phrase(tmp_path):
    base = tmp_path / "data"
    out = base / "output_files"
    thumbs = base / "_ng_thumbs"
    out.mkdir(parents=True)
    thumbs.mkdir()
    orphan = out / "legacy.png"
    Image.new("RGB", (12, 8), "blue").save(orphan)
    audit = audit_storage(output_dir=str(out), thumb_dir=str(thumbs), base_dir=str(base))
    result = quarantine_orphans(
        audit["snapshot_id"], ["legacy.png"],
        output_dir=str(out), thumb_dir=str(thumbs), base_dir=str(base),
    )
    entry_id = result["entries"][0]["entry_id"]
    with pytest.raises(RuntimeError, match="quarantine_retention_active"):
        purge_quarantine(entry_id, "永久删除", base_dir=str(base))

    manifest_path = base / "storage_quarantine" / entry_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["purge_eligible_at_epoch"] = 0
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="purge_confirmation_required"):
        purge_quarantine(entry_id, "no", base_dir=str(base))
    purged = purge_quarantine(entry_id, "永久删除", base_dir=str(base))
    assert purged["entry"]["status"] == "purged"
    assert purged["freed_bytes"] > 0
