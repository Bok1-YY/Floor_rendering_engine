# -*- coding: utf-8 -*-
import copy
import hashlib
import os

import pytest

from Floor_engine_server import whole_home_history as history


@pytest.fixture()
def history_root(tmp_path, monkeypatch):
    output = tmp_path / "outputs"
    snapshots = output / "_whole_home" / "replay_snapshots"
    batches = output / "_whole_home" / "variant_batches"
    snapshots.mkdir(parents=True)
    batches.mkdir(parents=True)
    monkeypatch.setattr(history, "MAIN_OUTPUT_DIR", str(output))
    monkeypatch.setattr(history, "ROOT", str(output / "_whole_home"))
    monkeypatch.setattr(history, "SNAPSHOT_DIR", str(snapshots))
    monkeypatch.setattr(history, "BATCH_DIR", str(batches))
    return output


def _asset(root, name, value=b"asset"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return str(path)


def _project_and_run(root):
    floorplan = _asset(root, "inputs/floorplan.png", b"floorplan")
    floor = _asset(root, "inputs/floor.png", b"floor")
    rgb = _asset(root, "captures/rgb.png", b"rgb")
    depth = _asset(root, "captures/depth.png", b"depth")
    normal = _asset(root, "captures/normal.png", b"normal")
    semantic = _asset(root, "captures/semantic.png", b"semantic")
    capture = {
        "capture_id": "capture-1", "status": "confirmed", "aspect_ratio": "4:3",
        "camera_id": "camera-1", "camera": {"id": "camera-1", "room_id": "room-1"},
        "room_id": "room-1", "rgb_path": rgb, "depth_path": depth,
        "normal_path": normal, "semantic_path": semantic,
    }
    project = {
        "project_id": "home-source", "source_type": "cad", "status": "verified",
        "summary": "Source", "created_at": 10, "updated_at": 20,
        "floorplan_path": floorplan, "model": {"schema_version": 2, "walls": []},
        "revision": 4, "verified": True, "verified_revision": 4,
        "captures": [capture], "operations": [], "api_key": "must-not-leak",
        "geometry_acceptance_required": False,
    }
    run = {
        "run_id": "run-source", "project_id": "home-source", "status": "done",
        "created_at": 30, "updated_at": 40, "floorplan_path": floorplan,
        "floor_path": floor, "style_ref_path": "", "style": "现代自然",
        "lighting": "自然日光", "prompt": "keep geometry", "model_keys": ["b2"],
        "aspect_ratio": "4:3", "resolution": "2K", "model_revision": 4,
        "model_hash": "a" * 64, "model_snapshot": copy.deepcopy(project["model"]),
        "capture_snapshots": [copy.deepcopy(capture)], "input_manifest": [],
        "results": [{
            "result_id": "result-1", "capture_id": "capture-1", "room_id": "room-1",
            "camera_name": "Living", "model_key": "b2", "path": "",
        }],
    }
    return project, run


def test_replay_snapshot_is_stable_redacted_and_tamper_evident(history_root):
    project, run = _project_and_run(history_root)
    first = history.build_replay_snapshot(project, run)
    second = history.build_replay_snapshot(project, run)
    assert first == second
    assert "must-not-leak" not in history.canonical_json(first)
    saved = history.save_replay_snapshot(first)
    assert history.load_replay_snapshot(saved["snapshot_id"]) == saved
    assert history.verify_snapshot_assets(saved) == []

    tampered = copy.deepcopy(saved)
    tampered["project_state"]["summary"] = "changed"
    with pytest.raises(history.WholeHomeHistoryError, match="哈希") as error:
        history.validate_replay_snapshot(tampered)
    assert error.value.code == "history_snapshot_tampered"


def test_legacy_replay_defers_large_asset_hashing_until_fork(history_root, monkeypatch):
    project, run = _project_and_run(history_root)

    def unexpected_hash(_path):
        raise AssertionError("read-only replay must not hash legacy assets")

    monkeypatch.setattr(history, "_file_sha256", unexpected_hash)
    snapshot = history.transient_replay_snapshot(project, run)
    capability = history.replay_capability(snapshot)
    assert snapshot["asset_validation"] == "deferred_until_fork"
    assert all(not row["sha256"] for row in snapshot["asset_manifest"] if row["available"])
    assert capability["can_view"] is True
    assert capability["can_fork"] is True
    assert {row["code"] for row in capability["blockers"]} == {
        "history_asset_verification_deferred"
    }


def test_missing_asset_remains_viewable_but_fork_fails_closed(history_root):
    project, run = _project_and_run(history_root)
    snapshot = history.build_replay_snapshot(project, run)
    target = next(row for row in snapshot["asset_manifest"] if row["role"] == "floor_sample")
    os.unlink(history._resolve_managed_path(target["managed_relative_path"]))
    capability = history.replay_capability(snapshot)
    assert capability["can_view"] is True
    assert capability["can_fork"] is False
    assert capability["status"] == "read_only_only"
    assert capability["blockers"][0]["code"] == "history_asset_missing"


def test_branch_is_new_revision_with_immutable_lineage(history_root):
    project, run = _project_and_run(history_root)
    snapshot = history.build_replay_snapshot(project, run)
    branch = history.prepare_branch_project(
        snapshot, project_id="home-branch", branch_name="Warm branch",
        idempotency_key="fork-once")
    assert branch["project_id"] == "home-branch"
    assert branch["revision"] == 1
    assert branch["verified"] is False
    assert branch["lineage"]["source_run_id"] == "run-source"
    assert branch["lineage"]["source_revision"] == 4
    assert branch["model"] == project["model"]
    assert branch["captures"][0]["capture_id"] == "capture-1"


def test_history_groups_root_project_branch_and_style_runs(history_root):
    project, run = _project_and_run(history_root)
    branch = {
        **copy.deepcopy(project), "project_id": "home-branch", "summary": "Branch",
        "lineage": {"root_project_id": "home-source", "source_run_id": "run-source"},
        "updated_at": 50,
    }
    variant = {
        **copy.deepcopy(run), "run_id": "run-variant", "project_id": "home-branch",
        "created_at": 60, "style": "侘寂", "variant_of_run_id": "run-source",
    }
    value = history.build_history(
        "home-branch", [project, branch], [run, variant], [], limit=100)
    assert value["root_project_id"] == "home-source"
    assert {row["project_id"] for row in value["branches"]} == {"home-source", "home-branch"}
    generation = [row for row in value["events"] if row["type"] == "generation_run"]
    assert {row["run_id"] for row in generation} == {"run-source", "run-variant"}
    assert next(row for row in generation if row["run_id"] == "run-variant")["style"] == "侘寂"


def test_cad_review_drafts_have_user_facing_history_titles(history_root):
    project, _ = _project_and_run(history_root)
    project.update(
        source_type="cad", status="needs_review", revision=1,
        operations=[{
            "type": "cad_import_needs_review", "at": 12,
            "revision": 1, "payload": {"code": "cad_hard_review_required"},
        }],
    )
    value = history.build_history(project["project_id"], [project], [], [])
    event = next(row for row in value["events"]
                 if row["type"] == "cad_import_needs_review")
    assert event["title"] == "CAD 3D 草稿已保存，等待几何复核"
    assert event["model_revision"] == 1


def test_variant_preview_binds_all_items_and_requires_exact_confirmation(history_root):
    project, run = _project_and_run(history_root)
    branch = copy.deepcopy(project)
    branch.update(project_id="home-branch", revision=1, verified_revision=1)
    branch["lineage"] = {"source_run_id": run["run_id"]}
    batch, phrase = history.create_variant_preview(
        batch_id="variant_batch_12345678", project=branch, source_run=run,
        style_spec={
            "style": "侘寂", "lighting": "傍晚暖光", "prompt": "same geometry",
            "floor_path": run["floor_path"], "style_ref_path": "",
            "aspect_ratio": "4:3",
        },
        excluded_artifact_ids=[], project_state_hash="state-one",
        image_call_cap=1, qa_call_cap=2,
    )
    assert batch["status"] == "previewed"
    assert len(batch["items"]) == 1
    assert batch["aggregate_caps"] == {
        "image_calls": 1, "qa_calls": 2, "items": 1, "concurrency": 1,
    }
    with pytest.raises(history.WholeHomeHistoryError) as error:
        history.claim_variant_batch(
            batch, preview_hash=batch["preview_hash"],
            confirmation_phrase="wrong phrase", current_project_state_hash="state-one")
    assert error.value.code == "variant_confirmation_mismatch"

    claimed = history.claim_variant_batch(
        batch, preview_hash=batch["preview_hash"], confirmation_phrase=phrase,
        current_project_state_hash="state-one")
    assert claimed["status"] == "queued"
    repeated = history.claim_variant_batch(
        claimed, preview_hash=batch["preview_hash"], confirmation_phrase="not-needed",
        current_project_state_hash="changed")
    assert repeated["variant_batch_id"] == claimed["variant_batch_id"]


def test_preview_invalidated_when_project_state_changes(history_root):
    project, run = _project_and_run(history_root)
    project["lineage"] = {"source_run_id": run["run_id"]}
    batch, phrase = history.create_variant_preview(
        batch_id="variant_batch_changed", project=project, source_run=run,
        style_spec={"floor_path": run["floor_path"], "aspect_ratio": "4:3"},
        excluded_artifact_ids=[], project_state_hash="before",
        image_call_cap=1, qa_call_cap=2,
    )
    with pytest.raises(history.WholeHomeHistoryError) as error:
        history.claim_variant_batch(
            batch, preview_hash=batch["preview_hash"], confirmation_phrase=phrase,
            current_project_state_hash="after")
    assert error.value.code == "variant_preview_inputs_changed"
