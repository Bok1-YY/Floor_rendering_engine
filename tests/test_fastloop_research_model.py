from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

from tools.fastloop_research import (
    ResearchModelError,
    compute_structure_hash,
    run_research_model,
    validate_bundle,
)
from tools.fastloop_research.contract import wall_mesh
from tools.fastloop_research.engine import _discover_blender, _python_has_ifcopenshell


def sample_bundle() -> dict:
    bundle = {
        "schema": "research-structure-bundle-v1",
        "source": {"id": "two-room-fixture", "kind": "unit-test"},
        "project": {"id": "two-room-research"},
        "source_hash": hashlib.sha256(b"two-room-source").hexdigest(),
        "structure_hash": "0" * 64,
        "outer_boundary_m": [[0.0, 0.0], [6.0, 0.0], [6.0, 4.0], [0.0, 4.0]],
        "spaces": [
            {"id": "SPACE-A", "label": "Room A", "point_m": [1.5, 2.0]},
            {"id": "SPACE-B", "label": "Room B", "point_m": [4.5, 2.0]},
        ],
        "wall_branch_graph": {
            "version": "wall-branch-graph-v1",
            "walls": [
                {
                    "id": "W-SOUTH-A",
                    "centerline_m": [[0.0, 0.0], [3.0, 0.0]],
                    "thickness_m": 0.20,
                    "base_m": 0.0,
                    "height_m": 2.80,
                    "left_space_id": "SPACE-A",
                    "right_space_id": "exterior",
                    "source": "human",
                    "confirmed": True,
                },
                {
                    "id": "W-SOUTH-B",
                    "centerline_m": [[3.0, 0.0], [6.0, 0.0]],
                    "thickness_m": 0.20,
                    "base_m": 0.0,
                    "height_m": 2.80,
                    "left_space_id": "SPACE-B",
                    "right_space_id": "exterior",
                    "source": "human",
                    "confirmed": True,
                },
                {
                    "id": "W-EAST",
                    "centerline_m": [[6.0, 0.0], [6.0, 4.0]],
                    "thickness_m": 0.20,
                    "base_m": 0.0,
                    "height_m": 2.80,
                    "left_space_id": "SPACE-B",
                    "right_space_id": "exterior",
                    "source": "human",
                    "confirmed": True,
                },
                {
                    "id": "W-NORTH-B",
                    "centerline_m": [[6.0, 4.0], [3.0, 4.0]],
                    "thickness_m": 0.20,
                    "base_m": 0.0,
                    "height_m": 2.80,
                    "left_space_id": "SPACE-B",
                    "right_space_id": "exterior",
                    "source": "human",
                    "confirmed": True,
                },
                {
                    "id": "W-NORTH-A",
                    "centerline_m": [[3.0, 4.0], [0.0, 4.0]],
                    "thickness_m": 0.20,
                    "base_m": 0.0,
                    "height_m": 2.80,
                    "left_space_id": "SPACE-A",
                    "right_space_id": "exterior",
                    "source": "human",
                    "confirmed": True,
                },
                {
                    "id": "W-WEST",
                    "centerline_m": [[0.0, 4.0], [0.0, 0.0]],
                    "thickness_m": 0.20,
                    "base_m": 0.0,
                    "height_m": 2.80,
                    "left_space_id": "SPACE-A",
                    "right_space_id": "exterior",
                    "source": "human",
                    "confirmed": True,
                },
                {
                    "id": "W-MIDDLE",
                    "centerline_m": [[3.0, 0.0], [3.0, 4.0]],
                    "thickness_m": 0.12,
                    "base_m": 0.0,
                    "height_m": 2.80,
                    "left_space_id": "SPACE-A",
                    "right_space_id": "SPACE-B",
                    "source": "human",
                    "confirmed": True,
                },
            ],
        },
        "opening_contract": {
            "version": "opening-contract-v1",
            "junction_clearance_m": 0.05,
            "openings": [
                {
                    "id": "OPEN-ENTRY",
                    "kind": "entrance",
                    "owning_wall_id": "W-SOUTH-A",
                    "segment_m": [[1.0, 0.0], [1.9, 0.0]],
                    "width_m": 0.9,
                    "sill_m": 0.0,
                    "head_m": 2.1,
                    "swing_direction": "not_shown",
                    "side_a_space_id": "SPACE-A",
                    "side_b_space_id": "exterior",
                    "jamb_before_supported": True,
                    "jamb_after_supported": True,
                    "junction_clearance_m": 0.05,
                    "junction_diagnostics": [],
                    "confirmed": True,
                    "source": "human",
                },
                {
                    "id": "OPEN-WINDOW",
                    "kind": "window",
                    "owning_wall_id": "W-NORTH-B",
                    "segment_m": [[5.0, 4.0], [4.0, 4.0]],
                    "width_m": 1.0,
                    "sill_m": 0.9,
                    "head_m": 2.1,
                    "swing_direction": None,
                    "side_a_space_id": "SPACE-B",
                    "side_b_space_id": "exterior",
                    "jamb_before_supported": True,
                    "jamb_after_supported": True,
                    "junction_clearance_m": 0.05,
                    "junction_diagnostics": [],
                    "confirmed": True,
                    "source": "human",
                },
            ],
        },
        "adjacency_truth": {
            "version": "adjacency-truth-v1",
            "edges": [
                {
                    "id": "ADJ-ENTRY",
                    "space_a_id": "exterior",
                    "space_b_id": "SPACE-A",
                    "kind": "door",
                    "opening_id": "OPEN-ENTRY",
                    "confirmed": True,
                },
                {
                    "id": "ADJ-PASSAGE",
                    "space_a_id": "SPACE-A",
                    "space_b_id": "SPACE-B",
                    "kind": "open_passage",
                    "opening_id": None,
                    "confirmed": True,
                },
            ],
            "confirmed": True,
        },
        "assumptions": {
            "scale_m_per_unit": 1.0,
            "floor_slab_thickness_m": 0.12,
            "research_only": True,
        },
        "unresolved_issues": ["Internal open passage has no modeled frame."],
    }
    bundle["structure_hash"] = compute_structure_hash(bundle)
    return bundle


def rehash(bundle: dict) -> dict:
    bundle["structure_hash"] = compute_structure_hash(bundle)
    return bundle


def test_strict_bundle_validates_and_wall_mesh_cuts_door_and_window() -> None:
    bundle = validate_bundle(sample_bundle())
    wall_by_id = {wall["id"]: wall for wall in bundle["wall_branch_graph"]["walls"]}
    by_wall = {}
    for opening in bundle["opening_contract"]["openings"]:
        by_wall.setdefault(opening["owning_wall_id"], []).append(opening)

    door_mesh = wall_mesh(wall_by_id["W-SOUTH-A"], by_wall["W-SOUTH-A"])
    window_mesh = wall_mesh(wall_by_id["W-NORTH-B"], by_wall["W-NORTH-B"])
    assert door_mesh["non_manifold_edges"] == 0
    assert window_mesh["non_manifold_edges"] == 0
    assert door_mesh["opening_cuts"] == [
        {
            "id": "OPEN-ENTRY",
            "kind": "entrance",
            "start_m": 1.0,
            "end_m": 1.9,
            "sill_m": 0.0,
            "head_m": 2.1,
        }
    ]
    assert window_mesh["opening_cuts"][0]["sill_m"] == 0.9
    assert window_mesh["opening_cuts"][0]["head_m"] == 2.1
    # Global Z cuts intentionally split the unchanged jamb spans too; this
    # gives every neighboring cell shared vertices/faces and a manifold grid.
    assert door_mesh["occupied_cells"] == 5
    assert window_mesh["occupied_cells"] == 8


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda bundle: bundle.update({"unexpected": True}), "exact keys"),
        (
            lambda bundle: bundle["assumptions"].update({"scale_m_per_unit": 0.001}),
            "require scale 1.0",
        ),
        (
            lambda bundle: bundle["opening_contract"]["openings"][0].update(
                {"segment_m": [[1.0, 0.08], [1.9, 0.08]]}
            ),
            "more than 50mm",
        ),
        (
            lambda bundle: bundle["adjacency_truth"].update(
                {
                    "edges": [
                        bundle["adjacency_truth"]["edges"][0]
                    ]
                }
            ),
            "unreachable",
        ),
    ],
)
def test_invalid_input_is_rejected_before_modeling(mutate, match: str) -> None:
    bundle = sample_bundle()
    mutate(bundle)
    rehash(bundle)
    with pytest.raises(ResearchModelError, match=match):
        validate_bundle(bundle)


def test_structure_hash_binds_the_entire_contract() -> None:
    bundle = sample_bundle()
    bundle["unresolved_issues"].append("Changed evidence")
    with pytest.raises(ResearchModelError, match="canonical hash"):
        validate_bundle(bundle)


def test_opening_rejects_collinear_wall_inside_jamb_protection() -> None:
    bundle = sample_bundle()
    overlapping = deepcopy(bundle["wall_branch_graph"]["walls"][0])
    overlapping.update(
        {
            "id": "W-SOUTH-OVERLAP",
            "centerline_m": [[0.5, 0.0], [2.2, 0.0]],
        }
    )
    bundle["wall_branch_graph"]["walls"].append(overlapping)
    rehash(bundle)
    with pytest.raises(ResearchModelError, match="overlaps collinear wall"):
        validate_bundle(bundle)


def test_clockwise_boundary_survives_revalidation_without_hash_drift() -> None:
    bundle = sample_bundle()
    bundle["outer_boundary_m"] = list(reversed(bundle["outer_boundary_m"]))
    rehash(bundle)
    first = validate_bundle(bundle)
    second = validate_bundle(first)
    assert first == second
    assert second["structure_hash"] == bundle["structure_hash"]


def test_ifc_all_root_guids_are_repeatable(tmp_path: Path) -> None:
    if not _python_has_ifcopenshell(Path(sys.executable)):
        pytest.skip("IfcOpenShell is not installed")
    import ifcopenshell

    from tools.fastloop_research.ifc_builder import build as build_ifc

    bundle = sample_bundle()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    first_path, second_path = tmp_path / "first.ifc", tmp_path / "second.ifc"
    build_ifc(bundle_path, first_path, tmp_path / "first-report.json")
    build_ifc(bundle_path, second_path, tmp_path / "second-report.json")
    first_guids = sorted(entity.GlobalId for entity in ifcopenshell.open(str(first_path)).by_type("IfcRoot"))
    second_guids = sorted(entity.GlobalId for entity in ifcopenshell.open(str(second_path)).by_type("IfcRoot"))
    assert first_guids == second_guids
    assert len(first_guids) == len(set(first_guids))


def test_dependency_discovery_contract() -> None:
    blender = _discover_blender(None)
    if blender is not None:
        assert blender.is_file()
        assert blender.name.lower() in {"blender", "blender.exe"}
    assert _python_has_ifcopenshell(Path(sys.executable)) is True
    assert _python_has_ifcopenshell(Path("definitely-missing-python.exe")) is False


def test_real_blender_and_ifc_small_model(tmp_path: Path) -> None:
    blender = _discover_blender(None)
    if blender is None:
        pytest.skip("Blender executable is not installed")
    result = run_research_model(
        sample_bundle(),
        tmp_path / "runs",
        blender_executable=blender,
        ifc_python=Path(sys.executable),
    )
    assert result["status"] == "mechanical_verified"


def test_packaged_runtime_uses_in_process_ifc_builder(tmp_path: Path, monkeypatch) -> None:
    from tools.fastloop_research import engine
    blender = _discover_blender(None)
    if blender is None:
        pytest.skip("Blender is not installed")
    monkeypatch.setattr(engine, "_running_frozen", lambda: True)
    result = run_research_model(sample_bundle(), tmp_path / "packaged", blender_executable=blender)
    assert result["status"] == "mechanical_verified"
    mechanical_path = Path(result["artifacts"]["mechanical-report.json"]["path"])
    mechanical = json.loads(mechanical_path.read_text(encoding="utf-8"))
    assert mechanical["ifc_process"]["arguments"] == ["in-process", "ifc_builder.build"]
    assert result["ifc_status"] == "pass"
    run_dir = Path(result["output_dir"])
    required = {
        "scene.blend",
        "scene.glb",
        "research.ifc",
        "top.png",
        "north-east.png",
        "north-west.png",
        "model-report.json",
        "mechanical-report.json",
        "unresolved-issues.json",
        "ifc-report.json",
    }
    assert required <= set(result["artifacts"])
    for name in required:
        metadata = result["artifacts"][name]
        path = Path(metadata["path"])
        assert path.is_absolute() and path.is_file()
        assert path.stat().st_size == metadata["bytes"] > 0
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"]

    model_report = json.loads((run_dir / "model-report.json").read_text(encoding="utf-8"))
    assert model_report["counts"] == {
        "floor": 1,
        "wall_branches": 7,
        "opening_semantics": 2,
        "space_semantics": 2,
        "cameras": 3,
        "materials": 0,
        "lights": 0,
    }
    cuts = {
        cut["id"]: cut
        for wall in model_report["wall_branches"]
        for cut in wall["opening_cuts"]
    }
    assert set(cuts) == {"OPEN-ENTRY", "OPEN-WINDOW"}
    assert all(wall["non_manifold_edges"] == 0 for wall in model_report["wall_branches"])
    assert all(wall["modifier_count"] == 0 for wall in model_report["wall_branches"])

    mechanical = json.loads((run_dir / "mechanical-report.json").read_text(encoding="utf-8"))
    assert mechanical["status"] == "mechanical_verified"
    assert mechanical["blender"]["blend"]["status"] == "pass"
    assert mechanical["blender"]["glb"]["status"] == "pass"
    assert mechanical["ifc"]["status"] == "pass"
    assert mechanical["ifc"]["entity_counts"]["IfcWall"] == 7
    assert mechanical["ifc"]["entity_counts"]["IfcDoor"] == 1
    assert mechanical["ifc"]["entity_counts"]["IfcWindow"] == 1

    with pytest.raises(ResearchModelError, match="refusing to overwrite"):
        run_research_model(
            sample_bundle(),
            tmp_path / "packaged",
            blender_executable=blender,
        )
