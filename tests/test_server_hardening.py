import io
import base64
import json
import os
import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError
from fastapi import HTTPException
from fastapi.responses import Response
from PIL import Image

from Floor_engine_server import server_state, server_helpers, routes_jobs, routes_library
from Floor_engine_server import records
from Floor_engine_server import server_api
from Floor_engine_server.models import new_job
from Floor_engine_server.server_schemas import FreeJobSubmitRequest, GenParams
from Floor_engine_server.task_registry import TaskRegistry


def test_floor_coverage_schema_defaults_and_range_validation():
    defaults = GenParams(workflow_mode="纯效果图 (生成全新空间)")
    assert (defaults.floor_coverage_min, defaults.floor_coverage_max) == (40, 50)

    custom = GenParams(
        workflow_mode="纯效果图 (生成全新空间)",
        floor_coverage_min=55,
        floor_coverage_max=65,
    )
    assert (custom.floor_coverage_min, custom.floor_coverage_max) == (55, 65)

    with pytest.raises(ValidationError):
        GenParams(
            workflow_mode="纯效果图 (生成全新空间)",
            floor_coverage_min=70,
            floor_coverage_max=60,
        )
    with pytest.raises(ValidationError):
        GenParams(
            workflow_mode="纯效果图 (生成全新空间)",
            floor_coverage_min=5,
        )


def test_result_files_are_unique_even_in_same_second(tmp_path, monkeypatch):
    monkeypatch.setattr(records, "MAIN_OUTPUT_DIR", str(tmp_path))
    hint = str(tmp_path / "oak" / "oak_优化图.png")
    first = records.save_api_result_jpg(Image.new("RGB", (2, 2), "red"), "Nano Banana 2", hint)
    second = records.save_api_result_jpg(Image.new("RGB", (2, 2), "blue"), "Nano Banana 2", hint)

    assert first != second
    assert os.path.exists(first) and os.path.exists(second)
    assert Image.open(first).getpixel((0, 0))[0] > 200
    assert Image.open(second).getpixel((0, 0))[2] > 200


def test_record_migration_removes_plaintext_and_adds_result_ids(tmp_path):
    path = tmp_path / "oak_记录.json"
    records.save_records_file(str(path), [{
        "id": "r1",
        "prompt_en": "secret b2",
        "prompt_en_pro": "secret pro",
        "results": [{"model_label": "Pro"}],
    }])

    assert records.migrate_record_storage(str(path)) is True
    migrated = records.load_records_file(str(path))[0]
    assert "prompt_en" not in migrated and "prompt_en_pro" not in migrated
    assert records.deobfuscate_text(migrated["_pe"]) == "secret b2"
    assert records.deobfuscate_text(migrated["_pe_pro"]) == "secret pro"
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


def test_queue_v3_persists_only_model_runs_and_loads_legacy_fields(tmp_path, monkeypatch):
    state_path = tmp_path / ".queue_state.json"
    monkeypatch.setattr(records, "QUEUE_STATE_FILE", str(state_path))
    legacy = new_job("oak", "now")
    legacy.b2_path = "b2.jpg"
    legacy.b2_paths = ["b2.jpg"]
    legacy.b2_idx = 0
    records.persist_jobs([legacy])
    raw = json.loads(state_path.read_text(encoding="utf-8"))[0]
    assert raw["_schema_version"] == 3
    assert "b2_path" not in raw and "b2_paths" not in raw
    assert raw["model_runs"]["b2"]["paths"] == ["b2.jpg"]

    state_path.write_text(json.dumps([{
        "job_id": "legacy", "display_name": "old", "ts": "now",
        "model_filter": "both", "b2_path": "old.jpg", "b2_paths": ["old.jpg"],
        "b2_idx": 0, "pro_path": None, "pro_paths": [],
    }]), encoding="utf-8")
    restored = records.load_persisted_jobs()[0]
    assert restored.model_runs["b2"]["paths"] == ["old.jpg"]
    assert restored.b2_path == "old.jpg"


def test_record_api_response_redacts_prompt_fields(tmp_path, monkeypatch):
    path = tmp_path / "oak_记录.json"
    path.write_text(json.dumps([{
        "id": "r1", "prompt_en": "plain", "prompt_en_pro": "plain pro",
        "_pe": "encoded", "_pe_pro": "encoded pro", "sample_image_b64": "large",
        "results": [{"result_id": "res_1"}],
    }]), encoding="utf-8")
    monkeypatch.setattr(server_helpers, "MAIN_OUTPUT_DIR", str(tmp_path))

    response = routes_library.load_records(str(path))[0]
    assert not ({"prompt_en", "prompt_en_pro", "_pe", "_pe_pro", "sample_image_b64"} & response.keys())


def test_record_api_defaults_legacy_pano_audit_projection(tmp_path, monkeypatch):
    path = tmp_path / "pano_记录.json"
    path.write_text(json.dumps([{
        "id": "pano-1", "immutable_audit": True,
        "pano_audit": {"candidate_sha256": "a" * 64},
        "results": [{"result_id": "pano-result"}],
    }]), encoding="utf-8")
    monkeypatch.setattr(server_helpers, "MAIN_OUTPUT_DIR", str(tmp_path))

    response = routes_library.load_records(str(path))[0]

    assert response["pano_audit"]["projection"] == "equirectangular"


def test_record_api_exposes_legacy_color_match_reference(tmp_path, monkeypatch):
    out_dir = tmp_path / "output_files"
    material_dir = out_dir / "oak"
    material_dir.mkdir(parents=True, exist_ok=True)
    path = material_dir / "oak_记录.json"
    path.write_text(json.dumps([{"id": "r1", "results": []}]), encoding="utf-8")
    ref = material_dir / "oak_优化图.png"
    Image.new("RGB", (8, 8), "tan").save(ref)
    monkeypatch.setattr(server_helpers, "MAIN_OUTPUT_DIR", str(out_dir))

    response = routes_library.load_records(str(path))[0]

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
    monkeypatch.setattr(routes_library, "scan_json_files", lambda: [str(path)])

    response = routes_library.list_records()

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
    monkeypatch.setattr(server_helpers, "UPLOAD_DIR", str(uploads))

    with pytest.raises(HTTPException) as exc:
        server_helpers.require_upload_image_path(str(outside), "参照图")
    assert exc.value.status_code == 400


def test_running_job_cannot_be_deleted(monkeypatch):
    job = new_job("oak", "now")
    job.status = "running"
    # 独立注册表:不带 on_persist → persist() 空操作,天然隔离落盘
    jobs = TaskRegistry("jobs", max_entries=60,
                        is_terminal=server_state.job_is_terminal, newest_first=True)
    jobs.add(job.job_id, job)
    monkeypatch.setattr(server_state, "JOBS", jobs)

    with pytest.raises(HTTPException) as exc:
        routes_jobs.delete_job(job.job_id)
    assert exc.value.status_code == 409


def test_invalid_image_payload_is_rejected(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(server_helpers, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr("Floor_engine_server.config.UPLOAD_DIR", str(uploads))
    upload = server_helpers.UploadFile(filename="fake.jpg", file=io.BytesIO(b"not an image"))

    with pytest.raises(HTTPException) as exc:
        server_helpers.save_upload(upload, "")
    assert exc.value.status_code == 400
    assert list(uploads.iterdir()) == []


def test_asgi_lifespan_health_and_origin_guard(monkeypatch):
    monkeypatch.setattr(server_api, "migrate_all_record_storage", lambda: 0)
    monkeypatch.setattr(server_api, "load_persisted_jobs", lambda: [])
    monkeypatch.setattr(server_api, "load_config", lambda: {"max_concurrent_per_model": 1})

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


def test_free_job_schema_enforces_prompt_slots_and_models():
    valid = FreeJobSubmitRequest(prompt="  按第一张图的构图生成  ", image_paths=["a.png"])
    assert valid.prompt.startswith("  ")  # 验空用 strip，真正发送保留原文
    with pytest.raises(ValidationError):
        FreeJobSubmitRequest(prompt="   ", image_paths=["a.png"])
    with pytest.raises(ValidationError):
        FreeJobSubmitRequest(prompt="x", image_paths=[])
    with pytest.raises(ValidationError):
        FreeJobSubmitRequest(prompt="x", image_paths=["1", "2", "3", "4"])
    with pytest.raises(ValidationError):
        FreeJobSubmitRequest(prompt="x", image_paths=["1"], model_targets=["sd35"])


def test_free_record_keeps_user_prompt_visible_and_slot_order(tmp_path, monkeypatch):
    monkeypatch.setattr(records, "MAIN_OUTPUT_DIR", str(tmp_path))
    slots = [str(tmp_path / "slot2.png"), str(tmp_path / "slot1.png")]
    json_path, record_id = records.create_free_generation_record(
        slots[0], "使用第二张图的色彩", slots, ["b2", "pro"], "16:9", "4K")

    record = records.load_records_file(json_path)[0]
    assert record["id"] == record_id
    assert record["user_prompt"] == "使用第二张图的色彩"
    assert record["gen_context"]["free_image_paths"] == slots
    assert "_pe" not in record and "prompt_en" not in record


def test_google_and_fal_free_inputs_keep_slot_order(tmp_path, monkeypatch):
    from Floor_engine_server import api as api_mod

    paths = []
    for index, color in enumerate(("red", "green", "blue"), start=1):
        path = tmp_path / f"slot{index}.png"
        Image.new("RGB", (2, 2), color).save(path)
        paths.append(str(path))

    captured = []
    captured_urls = []

    class RejectedResponse:
        status_code = 400
        text = "stop"

        def json(self):
            return {}

    def fake_post(_url, **kwargs):
        captured_urls.append(_url)
        captured.append(kwargs["json"])
        return RejectedResponse()

    monkeypatch.setattr(
        api_mod,
        "load_config",
        lambda: {
            "retry_attempts": 1,
            "fal_model_map": {
                "gemini-3-pro-image-preview": "custom/nano-pro/edit",
            },
        },
    )
    monkeypatch.setattr(api_mod._req, "post", fake_post)
    api_mod.call_gemini_generate("k", "m", " exact prompt ", paths[0], input_image_paths=paths)
    google_parts = captured[-1]["contents"][0]["parts"]
    assert google_parts[0] == {"text": " exact prompt "}
    assert [base64.b64decode(part["inlineData"]["data"]) for part in google_parts[1:]] == [
        Path(path).read_bytes() for path in paths
    ]

    api_mod.call_fal_generate("k", "gemini-3-pro-image", " exact prompt ", paths[0],
                              input_image_paths=paths)
    assert captured_urls[-1] == "https://fal.run/custom/nano-pro/edit"
    fal_payload = captured[-1]
    assert fal_payload["prompt"] == " exact prompt "
    assert [uri.split(",", 1)[1] for uri in fal_payload["image_urls"]] == [
        base64.b64encode(Path(path).read_bytes()).decode("ascii") for path in paths
    ]


def test_create_free_job_registers_queue_without_starting_network(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    slot = uploads / "slot.png"
    Image.new("RGB", (2, 2), "red").save(slot)
    jobs = TaskRegistry("jobs", max_entries=60,
                        is_terminal=server_state.job_is_terminal, newest_first=True)
    spawned = []

    def capture(coro):
        spawned.append(coro)

    monkeypatch.setattr(server_helpers, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(server_state, "JOBS", jobs)
    monkeypatch.setattr(server_state, "spawn", capture)
    monkeypatch.setattr(routes_jobs, "load_config", lambda: {"gemini_api_key": "k"})
    request = FreeJobSubmitRequest(prompt="原样指令", image_paths=[str(slot)], model_targets=["pro"])

    view = asyncio.run(routes_jobs.create_free_job(request))
    assert view["workflow_mode"].startswith("自由创作")
    assert view["model_targets"] == ["pro"]
    assert len(jobs.snapshot()) == 1 and len(spawned) == 1
    spawned[0].close()  # 本测试只验证入队回执，不启动付费 worker

    duplicate = FreeJobSubmitRequest(
        prompt="x", image_paths=[str(slot), str(uploads / "." / "slot.png")], model_targets=["b2"])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes_jobs.create_free_job(duplicate))
    assert exc.value.status_code == 422
