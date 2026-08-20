from __future__ import annotations

import copy

import pytest

from Floor_engine_Linux.whole_home_geometry import (
    GeometryContractError,
    build_geometry_acceptance_report,
    build_geometry_manifest,
    geometry_facts_hash,
    migrate_legacy_project_geometry,
    production_readiness,
    validate_geometry_manifest,
)
from Floor_engine_Linux.whole_home_geometry_kernel import (
    GeometryKernelError,
    compile_geometry_manifest,
)


def _model(*, wall_status="accepted", opening_status="accepted"):
    return {
        "geometry_schema_version": 3,
        "input_grade": "vector_authoritative",
        "coordinate_system": "metres-y-up",
        "wall_height_m": 2.8,
        "project_id": "project-a",
        "revision": 9,
        "updated_at": 999,
        "wall_assemblies": [{
            "id": "assembly-a",
            "source_representation": "paired_faces",
            "centerline": [[0, 0], [5, 0]],
            "footprint_polygon": [[0, -.1], [5, -.1], [5, .1], [0, .1]],
            "thickness_m": .2,
            "height_m": 2.8,
            "review_status": wall_status,
        }],
        "walls": [{
            "id": "legacy-a", "wall_assembly_id": "assembly-a",
            "start": {"x": 0, "z": 0}, "end": {"x": 5, "z": 0},
            "thickness_m": .2, "height_m": 2.8,
        }],
        "rooms": [{
            "id": "room-a",
            "polygon": [[0, 0], [5, 0], [5, 4], [0, 4]],
            "floor_elevation_m": 0, "ceiling_height_m": 2.8,
        }],
        "openings": [{
            "id": "door-a", "wall_assembly_id": "assembly-a", "kind": "door",
            "offset_m": 1.5, "width_m": 1, "height_m": 2.1,
            "sill_height_m": 0, "review_status": opening_status,
        }],
        "fixed_objects": [{"id": "sofa", "updated_at": 10}],
        "cameras": [{"id": "camera-a"}],
    }


def _passing_metrics():
    return {
        "cad": {
            "provenance_coverage": 1, "wall_assembly_coverage": 1,
            "boundary_p95_m": 0, "boundary_max_m": 0,
            "max_room_area_relative_error": 0, "room_coverage": 1,
            "room_overlap_area_m2": 0, "outer_max_gap_m": 0,
            "opening_eligible_count": 1, "opening_center_width_p95_m": 0,
            "orphan_opening_count": 0, "outside_opening_count": 0,
            "overlapping_opening_count": 0, "unresolved_wall_count": 0,
            "unresolved_opening_count": 0,
        },
        "manifest": {
            "floor_footprint_iou": 1,
            "wall_footprint_symmetric_difference_m2": 0,
            "wall_footprint_symmetric_difference_ratio": 0,
            "opening_interval_error_m": 0, "projection_iou": 1,
            "orphan_manifest_opening_count": 0,
        },
    }


def test_geometry_fingerprint_ignores_project_history_ids_order_and_accepted_alias():
    first = _model()
    changed = copy.deepcopy(first)
    changed.update(project_id="different", revision=100, updated_at=123456, created_at=1)
    changed["cameras"] = [{"id": "different-camera"}]
    changed["fixed_objects"] = [{"id": "different-decoration"}]
    changed["wall_assemblies"][0]["id"] = "renamed-wall"
    changed["wall_assemblies"][0]["review_status"] = "confirmed"
    changed["walls"][0].update(id="renamed-legacy", wall_assembly_id="renamed-wall")
    changed["rooms"][0]["id"] = "renamed-room"
    changed["openings"][0].update(id="renamed-door", wall_assembly_id="renamed-wall")
    assert geometry_facts_hash(first) == geometry_facts_hash(changed)


def test_geometry_fingerprint_accepts_variable_thickness_closed_footprint_only():
    footprint = _model(opening_status="pending")
    footprint["wall_assemblies"][0].update(
        source_representation="closed_footprint",
        centerline=[], thickness_m=None,
    )
    value = geometry_facts_hash(footprint)
    assert len(value) == 64

    defaulted_centerline = _model()
    defaulted_centerline["wall_assemblies"][0]["thickness_m"] = None
    defaulted_centerline["wall_assemblies"][0]["footprint_polygon"] = []
    assert len(geometry_facts_hash(defaulted_centerline)) == 64


@pytest.mark.parametrize(
    "mutation",
    [
        lambda model: model["wall_assemblies"][0].update(thickness_m=.25),
        lambda model: model["rooms"][0]["polygon"].__setitem__(2, [5.2, 4]),
        lambda model: model["openings"][0].update(width_m=1.2),
        lambda model: model["openings"][0].update(kind="window"),
    ],
)
def test_geometry_fingerprint_changes_for_plan_geometry_or_topology(mutation):
    model = _model()
    changed = copy.deepcopy(model)
    mutation(changed)
    assert geometry_facts_hash(model) != geometry_facts_hash(changed)


def test_wall_direction_and_adjusted_opening_offset_have_same_geometry_fingerprint():
    model = _model()
    reversed_model = copy.deepcopy(model)
    reversed_model["wall_assemblies"][0]["centerline"] = [[5, 0], [0, 0]]
    reversed_model["walls"][0].update(start={"x": 5, "z": 0}, end={"x": 0, "z": 0})
    reversed_model["openings"][0]["offset_m"] = 5 - (1.5 + 1)
    assert geometry_facts_hash(model) == geometry_facts_hash(reversed_model)


def test_only_confirmed_or_accepted_wall_assemblies_enter_strict_manifest():
    pending = _model(wall_status="needs_review", opening_status="pending")
    assert compile_geometry_manifest(pending)["wall_parts"] == []
    assert compile_geometry_manifest(pending)["opening_voids"] == []

    accepted = compile_geometry_manifest(_model(wall_status="accepted"))
    confirmed = compile_geometry_manifest(_model(wall_status="confirmed"))
    assert accepted["wall_parts"]
    assert accepted["manifest_hash"] == confirmed["manifest_hash"]
    assert accepted["model_facts_hash"] == confirmed["model_facts_hash"]


def test_pending_opening_does_not_cut_wall_or_enter_opening_voids():
    pending = compile_geometry_manifest(_model(opening_status="pending"))
    accepted = compile_geometry_manifest(_model(opening_status="accepted"))
    assert pending["opening_voids"] == []
    assert len(pending["wall_parts"]) == 1
    assert len(accepted["opening_voids"]) == 1
    assert len(accepted["wall_parts"]) == 3


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"wall_assembly_id": "missing"}, "no confirmed host wall"),
        ({"width_m": 0}, "invalid dimensions"),
        ({"offset_m": -0.1}, "invalid dimensions"),
        ({"offset_m": 4.5, "width_m": 1}, "beyond its host wall"),
        ({"sill_height_m": 1, "height_m": 2}, "above its host wall"),
    ],
)
def test_confirmed_opening_must_have_valid_owner_interval_and_dimensions(update, message):
    model = _model()
    model["openings"][0].update(update)
    with pytest.raises(GeometryKernelError, match=message):
        compile_geometry_manifest(model)


def test_manifest_validator_rejects_orphan_and_incomplete_strict_opening():
    manifest = compile_geometry_manifest(_model())
    orphan = copy.deepcopy(manifest)
    orphan.pop("manifest_hash")
    orphan["opening_voids"][0].update(wall_assembly_id="missing", wall_id="")
    with pytest.raises(GeometryContractError) as error:
        validate_geometry_manifest(orphan)
    assert error.value.code == "manifest_opening_orphan"

    incomplete = copy.deepcopy(manifest)
    incomplete.pop("manifest_hash")
    incomplete["opening_voids"][0].pop("height_m")
    with pytest.raises(GeometryContractError) as error:
        validate_geometry_manifest(incomplete)
    assert error.value.code == "manifest_opening_dimensions_missing"


def test_manifest_compatibility_parts_cannot_smuggle_extra_wall_geometry():
    manifest = compile_geometry_manifest(_model())
    tampered = copy.deepcopy(manifest)
    tampered.pop("manifest_hash")
    extra = copy.deepcopy(tampered["wall_parts"][0])
    extra.update(id="wall:pending", entity_id="pending-wall")
    tampered["parts"].append(extra)
    with pytest.raises(GeometryContractError) as error:
        validate_geometry_manifest(tampered)
    assert error.value.code == "manifest_parts_index_mismatch"


def test_production_gate_recomputes_fingerprint_and_rejects_unconfirmed_wall_part():
    model = _model()
    model["wall_assemblies"].append({
        "id": "pending-wall", "review_status": "needs_review",
        "source_representation": "closed_footprint",
        "centerline": [[0, 2], [5, 2]],
        "footprint_polygon": [[0, 1.9], [5, 1.9], [5, 2.1], [0, 2.1]],
        "thickness_m": .2, "height_m": 2.8,
    })
    manifest = compile_geometry_manifest(model, registration_hash="registration", project_id="home", model_revision=4)
    injected_part = copy.deepcopy(manifest["wall_parts"][0])
    injected_part.update(
        id="wall:pending-wall:injected", entity_id="pending-wall",
        wall_assembly_id="pending-wall",
    )
    injected = build_geometry_manifest(**{
        key: copy.deepcopy(value) for key, value in manifest.items()
        if key not in {"manifest_hash", "wall_parts", "parts"}
    }, wall_parts=[*manifest["wall_parts"], injected_part],
       parts=[*manifest["parts"], injected_part])
    report = build_geometry_acceptance_report(
        project_id="home", source_type="cad", input_grade="vector_authoritative",
        source_hash="a" * 64, model_revision=4,
        model_facts_hash=injected["model_facts_hash"], registration_hash="registration",
        cad_facts_hash="cad", geometry_kernel_version=injected["geometry_kernel_version"],
        manifest_hash=injected["manifest_hash"], metrics=_passing_metrics(),
    )
    project = {
        "project_id": "home", "revision": 4, "input_grade": "vector_authoritative",
        "model": model, "source_registration": {
            "source_hash": "a" * 64, "registration_hash": "registration",
            "input_grade": "vector_authoritative",
        },
    }
    readiness = production_readiness(
        project, report, injected,
        current_facts={
            "source_hash": "a" * 64, "model_revision": 4,
            "model_facts_hash": geometry_facts_hash(model),
            "registration_hash": "registration", "cad_facts_hash": "cad",
            "geometry_kernel_version": injected["geometry_kernel_version"],
            "manifest_hash": injected["manifest_hash"],
        },
    )
    assert readiness["ready"] is False
    assert "manifest_contains_unconfirmed_wall" in {row["code"] for row in readiness["reasons"]}


def test_pending_entity_added_after_acceptance_blocks_without_changing_locked_fingerprint():
    model = _model()
    manifest = compile_geometry_manifest(
        model, registration_hash="registration", project_id="home", model_revision=4)
    report = build_geometry_acceptance_report(
        project_id="home", source_type="cad", input_grade="vector_authoritative",
        source_hash="a" * 64, model_revision=4,
        model_facts_hash=manifest["model_facts_hash"], registration_hash="registration",
        cad_facts_hash="cad", geometry_kernel_version=manifest["geometry_kernel_version"],
        manifest_hash=manifest["manifest_hash"], metrics=_passing_metrics(),
    )
    changed = copy.deepcopy(model)
    changed["openings"].append({
        "id": "pending-window", "wall_assembly_id": "assembly-a", "kind": "window",
        "offset_m": 3, "width_m": 1, "height_m": 1.2, "sill_height_m": .9,
        "review_status": "pending",
    })
    assert geometry_facts_hash(changed) == manifest["model_facts_hash"]
    readiness = production_readiness(
        {"project_id": "home", "revision": 4, "input_grade": "vector_authoritative",
         "model": changed, "source_registration": {
             "source_hash": "a" * 64, "registration_hash": "registration",
             "input_grade": "vector_authoritative"}},
        report, manifest,
        current_facts={
            "source_hash": "a" * 64, "model_revision": 4,
            "model_facts_hash": geometry_facts_hash(changed),
            "registration_hash": "registration", "cad_facts_hash": "cad",
            "geometry_kernel_version": manifest["geometry_kernel_version"],
            "manifest_hash": manifest["manifest_hash"],
        },
    )
    assert readiness["ready"] is False
    assert "current_unresolved_opening_review" in {row["code"] for row in readiness["reasons"]}


def test_unenrolled_legacy_wall_without_review_state_remains_renderable():
    legacy = {
        "schema_version": 2,
        "walls": [{
            "id": "legacy-wall", "start": {"x": 0, "z": 0}, "end": {"x": 4, "z": 0},
            "thickness_m": .2, "height_m": 2.8,
        }],
        "rooms": [], "openings": [], "fixed_objects": [],
    }
    assert compile_geometry_manifest(legacy)["wall_parts"]
    locked = copy.deepcopy(legacy)
    locked.update(geometry_schema_version=3, input_grade="vector_authoritative")
    assert compile_geometry_manifest(locked)["wall_parts"] == []

    migrated = migrate_legacy_project_geometry({"verified": True, "model": legacy})
    assert migrated["model"]["input_grade"] == "legacy_unproven"
    assert compile_geometry_manifest(migrated["model"])["wall_parts"]
