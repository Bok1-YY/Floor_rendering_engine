import io
import json
import os
import asyncio

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from PIL import Image

from Floor_engine_server import records
from Floor_engine_server import server_api
from Floor_engine_server.models import new_job


def test_result_files_are_unique_even_in_same_second(tmp_path, monkeypatch):
    monkeypatch.setattr(records, "MAIN_OUTPUT_DIR", str(tmp_path))
    hint = str(tmp_path / "oak" / "oak_优化图.png")
    first = records._save_api_result_jpg(Image.new("RGB", (2, 2), "red"), "Nano Banana 2", hint)
    second = records._save_api_result_jpg(Image.new("RGB", (2, 2), "blue"), "Nano Banana 2", hint)

    assert first != second
    assert os.path.exists(first) and os.path.exists(second)
    assert Image.open(first).getpixel((0, 0))[0] > 200
    assert Image.open(second).getpixel((0, 0))[2] > 200


def test_record_migration_removes_plaintext_and_adds_result_ids(tmp_path):
    path = tmp_path / "oak_记录.json"
    records._save_records(str(path), [{
        "id": "r1",
        "prompt_en": "secret b2",
        "prompt_en_pro": "secret pro",
        "results": [{"model_label": "Pro"}],
    }])

    assert records.migrate_record_storage(str(path)) is True
    migrated = records._load_records(str(path))[0]
    assert "prompt_en" not in migrated and "prompt_en_pro" not in migrated
    assert records._deobfuscate(migrated["_pe"]) == "secret b2"
    assert records._deobfuscate(migrated["_pe_pro"]) == "secret pro"
    assert migrated["results"][0]["result_id"].startswith("res_")
    assert os.path.exists(str(path) + ".schema_v1.bak")
    assert records.migrate_record_storage(str(path)) is False


def test_queue_persistence_obfuscates_retry_prompts(tmp_path, monkeypatch):
    state = tmp_path / ".queue_state.json"
    monkeypatch.setattr(records, "QUEUE_STATE_FILE", str(state))
    job = new_job("oak", "now")
    job.retry_ctx = {"api_key": "key", "cpt": "secret b2", "cpt_pro": "secret pro"}

    records.persist_jobs([job])
    raw = state.read_text(encoding="utf-8")
    assert "secret b2" not in raw and '"api_key"' not in raw
    restored = records.load_persisted_jobs()[0]
    assert restored.retry_ctx["cpt"] == "secret b2"
    assert restored.retry_ctx["cpt_pro"] == "secret pro"


def test_record_api_response_redacts_prompt_fields(tmp_path, monkeypatch):
    path = tmp_path / "oak_记录.json"
    path.write_text(json.dumps([{
        "id": "r1", "prompt_en": "plain", "prompt_en_pro": "plain pro",
        "_pe": "encoded", "_pe_pro": "encoded pro", "sample_image_b64": "large",
        "results": [{"result_id": "res_1"}],
    }]), encoding="utf-8")
    monkeypatch.setattr(server_api, "MAIN_OUTPUT_DIR", str(tmp_path))

    response = server_api.load_records(str(path))[0]
    assert not ({"prompt_en", "prompt_en_pro", "_pe", "_pe_pro", "sample_image_b64"} & response.keys())


def test_record_api_exposes_legacy_color_match_reference(tmp_path, monkeypatch):
    out_dir = tmp_path / "output_files"
    material_dir = out_dir / "oak"
    material_dir.mkdir(parents=True, exist_ok=True)
    path = material_dir / "oak_记录.json"
    path.write_text(json.dumps([{"id": "r1", "results": []}]), encoding="utf-8")
    ref = material_dir / "oak_优化图.png"
    Image.new("RGB", (8, 8), "tan").save(ref)
    monkeypatch.setattr(server_api, "MAIN_OUTPUT_DIR", str(out_dir))

    response = server_api.load_records(str(path))[0]

    assert response["color_match_ref_path"] == os.path.realpath(ref)
    assert response["color_match_ref_url"] == "/outputs/oak/oak_优化图.png"


def test_record_list_includes_favorite_count(tmp_path, monkeypatch):
    path = tmp_path / "oak_记录.json"
    path.write_text(json.dumps([{
        "id": "r1",
        "results": [
            {"result_id": "res_1", "favorite": True},
            {"result_id": "res_2", "favorite": False},
            {"result_id": "res_3", "favorite": True},
        ],
    }]), encoding="utf-8")
    monkeypatch.setattr(server_api, "scan_json_files", lambda: [str(path)])

    response = server_api.list_records()

    assert response == [{
        "json_path": str(path),
        "labels": [(" |  | ", "r1")],
        "favorite_count": 2,
    }]


def test_output_route_never_serves_record_json(tmp_path, monkeypatch):
    record = tmp_path / "oak_记录.json"
    record.write_text('{"prompt_en":"secret"}', encoding="utf-8")
    monkeypatch.setattr(server_api, "MAIN_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(records, "MAIN_OUTPUT_DIR", str(tmp_path))

    response = server_api.serve_output_image(record.name)
    assert response.status_code == 404


def test_client_image_paths_must_stay_in_upload_dir(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    outside = tmp_path / "outside.jpg"
    Image.new("RGB", (2, 2)).save(outside)
    monkeypatch.setattr(server_api, "UPLOAD_DIR", str(uploads))

    with pytest.raises(HTTPException) as exc:
        server_api._require_upload_image_path(str(outside), "参照图")
    assert exc.value.status_code == 400


def test_running_job_cannot_be_deleted(monkeypatch):
    job = new_job("oak", "now")
    job.status = "running"
    monkeypatch.setattr(server_api, "_job_history", [job])
    monkeypatch.setattr(server_api, "_persist_jobs", lambda: None)

    with pytest.raises(HTTPException) as exc:
        server_api.delete_job(job.job_id)
    assert exc.value.status_code == 409


def test_invalid_image_payload_is_rejected(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(server_api, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr("Floor_engine_server.config.UPLOAD_DIR", str(uploads))
    upload = server_api.UploadFile(filename="fake.jpg", file=io.BytesIO(b"not an image"))

    with pytest.raises(HTTPException) as exc:
        server_api._save_upload(upload, "")
    assert exc.value.status_code == 400
    assert list(uploads.iterdir()) == []


def test_asgi_lifespan_health_and_origin_guard(monkeypatch):
    monkeypatch.setattr(server_api, "migrate_all_record_storage", lambda: 0)
    monkeypatch.setattr(server_api, "load_persisted_jobs", lambda: [])
    monkeypatch.setattr(server_api, "_load_config", lambda: {"max_concurrent_per_model": 1})

    async def exercise():
        async with server_api.lifespan(server_api.app):
            assert server_api.healthz() == {"ok": True}
            request = server_api.Request({
                "type": "http",
                "method": "POST",
                "path": "/api/jobs/cancel-all",
                "headers": [(b"origin", b"https://attacker.invalid")],
            })

            async def allowed(_request):
                return Response(status_code=204)

            blocked = await server_api.reject_cross_origin_mutations(request, allowed)
            assert blocked.status_code == 403

    asyncio.run(exercise())
