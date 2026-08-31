from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
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
from tools.fastloop_research.contract import _validate_walls, analyze_wall_junctions, compute_anchor_set_hash, derive_wall_junctions
from tools.fastloop_research.mechanical import verify_contract_geometry, verify_wall_opening_voids
from tools.fastloop_research.engine import _discover_blender, _python_has_ifcopenshell


def sample_bundle() -> dict:
    bundle = {
        "schema": "research-structure-bundle-v1",
        "source": {
            "schema": "source-provenance-v2",
            "source_file_hash": hashlib.sha256(b"two-room-source").hexdigest(),
            "normalized_hash": hashlib.sha256(b"two-room-normalized").hexdigest(),
            "raw_pixel_hash": hashlib.sha256(b"two-room-pixels").hexdigest(),
            "exif_orientation": 1,
            "orientation_policy": "exif_transpose-v1",
            "canonical_visible_size": [1200, 900],
            "coordinate_space": "normalized-evidence-1000-v1",
            "normalized_to_metric_3x3": [[0.006, 0.0, 0.0], [0.0, -0.004, 4.0], [0.0, 0.0, 1.0]],
            "scale_anchor_id": "P-SCALE",
            "anchors": [
                {"anchor_id": "P-SCALE", "kind": "scale", "points_norm": [[0.0, 1000.0], [1000.0, 1000.0]], "points_metric_m": [[0.0, 0.0], [6.0, 0.0]], "distance_mm": 6000.0},
                {"anchor_id": "P-ENTRY", "kind": "entrance", "points_norm": [[166.6666666667, 1000.0]], "points_metric_m": [[1.0, 0.0]], "distance_mm": None}
            ],
            "anchor_opening_bindings": [{
                "anchor_id": "P-ENTRY", "anchor_kind": "entrance",
                "opening_id": "OPEN-ENTRY"
            }]
        },
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
                    "jamb_before_support": {"mode": "same_wall_margin", "supporting_wall_id": "W-SOUTH-A", "junction_id": None, "face_distance_m": 1.0, "effective_support_m": 1.0, "provenance": "fixture", "solid_provenance": "wall solid"},
                    "jamb_after_support": {"mode": "same_wall_margin", "supporting_wall_id": "W-SOUTH-A", "junction_id": None, "face_distance_m": 1.1, "effective_support_m": 1.1, "provenance": "fixture", "solid_provenance": "wall solid"},
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
                    "jamb_before_support": {"mode": "same_wall_margin", "supporting_wall_id": "W-NORTH-B", "junction_id": None, "face_distance_m": 1.0, "effective_support_m": 1.0, "provenance": "fixture", "solid_provenance": "wall solid"},
                    "jamb_after_support": {"mode": "same_wall_margin", "supporting_wall_id": "W-NORTH-B", "junction_id": None, "face_distance_m": 1.0, "effective_support_m": 1.0, "provenance": "fixture", "solid_provenance": "wall solid"},
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
    bundle["wall_branch_graph"]["junctions"] = derive_wall_junctions(bundle["wall_branch_graph"]["walls"])
    bundle["source"]["anchor_set_hash"] = compute_anchor_set_hash(
        bundle["source"]["coordinate_space"], bundle["source_hash"],
        bundle["source"]["normalized_hash"], bundle["source"]["anchors"],
    )
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
            "exceeds 50mm",
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
    with pytest.raises(ResearchModelError, match="dangling endpoint|collinear_overlap"):
        validate_bundle(bundle)


def test_opening_rejects_direction_mismatch_even_with_endpoints_inside_50mm() -> None:
    bundle = sample_bundle()
    opening = bundle["opening_contract"]["openings"][0]
    opening["segment_m"] = [[1.0, 0.0], [1.4, 0.04]]
    opening["width_m"] = round(math.dist(*opening["segment_m"]), 6)
    rehash(bundle)
    with pytest.raises(ResearchModelError, match="more than 5 degrees"):
        validate_bundle(bundle)


@pytest.mark.parametrize("mutate,match", [
    (lambda source: source["anchors"].append(deepcopy(source["anchors"][1])), "duplicate ID"),
    (lambda source: source["anchor_opening_bindings"][0].update({"opening_id": "MISSING"}), "unknown opening"),
    (lambda source: source["anchor_opening_bindings"][0].update({"anchor_kind": "opening"}), "anchor kind"),
])
def test_source_provenance_rejects_invalid_anchor_opening_bindings(mutate, match: str) -> None:
    bundle = sample_bundle()
    mutate(bundle["source"])
    rehash(bundle)
    with pytest.raises(ResearchModelError, match=match):
        validate_bundle(bundle)


def test_validator_recomputes_binding_and_rejects_forged_moved_opening() -> None:
    bundle = sample_bundle()
    entry = bundle["opening_contract"]["openings"][0]
    entry["segment_m"] = [[1.5, 0.0], [2.4, 0.0]]
    entry["jamb_before_support"].update(face_distance_m=1.5, effective_support_m=1.5)
    entry["jamb_after_support"].update(face_distance_m=0.6, effective_support_m=0.6)
    rehash(bundle)
    with pytest.raises(ResearchModelError, match="independently derived point-to-opening distance"):
        validate_bundle(bundle)


def test_wall_junction_classifier_rejects_unsplit_t_x_overlap_and_near_gap() -> None:
    def wall(wall_id, a, b):
        return {"id": wall_id, "centerline_m": [a, b]}
    cases = [
        ([wall("A", [0, 0], [4, 0]), wall("B", [2, 0], [2, 2])], "t_junction", "pass"),
        ([wall("A", [0, 0], [4, 0]), wall("B", [2, -1], [2, 1])], "unsplit_x_junction", "error"),
        ([wall("A", [0, 0], [4, 0]), wall("B", [3, 0], [5, 0])], "collinear_overlap", "error"),
        ([wall("A", [0, 0], [1, 0]), wall("B", [1.02, 0], [1.02, 1])], "near_miss_gap", "error"),
    ]
    for walls, expected, severity in cases:
        assert expected in {item["kind"] for item in analyze_wall_junctions(walls) if item["severity"] == severity}


def test_junction_derivation_classifies_perpendicular_l_and_atomic_t_x() -> None:
    def wall(wall_id, a, b):
        return {"id": wall_id, "centerline_m": [a, b]}
    assert derive_wall_junctions([wall("A", [0, 0], [1, 0]), wall("B", [1, 0], [1, 1])])[0]["kind"] == "L"
    atomic_t = [wall("A", [-1, 0], [0, 0]), wall("B", [0, 0], [1, 0]), wall("C", [0, 0], [0, 1])]
    assert derive_wall_junctions(atomic_t)[0]["kind"] == "T"
    atomic_x = [*atomic_t, wall("D", [0, -1], [0, 0])]
    assert derive_wall_junctions(atomic_x)[0]["kind"] == "X"


@pytest.mark.parametrize("walls,kind", [
    ([([0, 0], [4, 0]), ([2, 0], [2, 2])], "T"),
    ([([0, 0], [4, 0]), ([2, -1], [2, 1])], "X"),
])
def test_wall_graph_rejects_non_atomic_t_and_x_even_when_declared(walls, kind: str) -> None:
    records = [{"id": f"W{index}", "centerline_m": [list(segment[0]), list(segment[1])], "thickness_m": 0.12, "base_m": 0.0, "height_m": 2.8, "left_space_id": "A", "right_space_id": "B", "source": "fixture", "confirmed": True} for index, segment in enumerate(walls, 1)]
    junctions = derive_wall_junctions(records)
    assert junctions[0]["kind"] == kind
    with pytest.raises(ResearchModelError, match=f"non-atomic {kind}"):
        _validate_walls({"version": "wall-branch-graph-v1", "walls": records, "junctions": junctions}, {"A", "B"})


def _artifact_geometry(bundle: dict) -> dict:
    return {
        "schema": "research-artifact-geometry-v1",
        "structure_hash": bundle["structure_hash"],
        "walls": [{key: deepcopy(wall[key]) for key in ("id", "centerline_m", "thickness_m", "base_m", "height_m")} for wall in bundle["wall_branch_graph"]["walls"]],
        "openings": [{key: deepcopy(opening[key]) for key in ("id", "segment_m", "width_m", "sill_m", "head_m")} for opening in bundle["opening_contract"]["openings"]],
        "spaces": [{"id": space["id"], "point_m": deepcopy(space["point_m"])} for space in bundle["spaces"]],
    }


def test_contract_to_artifact_coordinate_verifier_enforces_one_millimetre() -> None:
    bundle = sample_bundle()
    assert verify_contract_geometry(bundle, _artifact_geometry(bundle))["status"] == "pass"
    shifted = _artifact_geometry(bundle)
    shifted["walls"][0]["centerline_m"][0][0] += 0.002
    with pytest.raises(ResearchModelError, match="1mm/0.1-degree"):
        verify_contract_geometry(bundle, shifted)


def test_mesh_void_verifier_rejects_filled_wall_even_when_opening_empty_remains() -> None:
    bundle = sample_bundle()
    wall = next(wall for wall in bundle["wall_branch_graph"]["walls"] if wall["id"] == "W-SOUTH-A")
    opening = next(opening for opening in bundle["opening_contract"]["openings"] if opening["id"] == "OPEN-ENTRY")
    cut = wall_mesh(wall, [opening])
    assert verify_wall_opening_voids(wall, [opening], cut["vertices"], cut["faces"])["status"] == "pass"
    filled = wall_mesh(wall, [])
    with pytest.raises(ResearchModelError, match="mesh occupancy disagrees with opening void"):
        verify_wall_opening_voids(wall, [opening], filled["vertices"], filled["faces"])


def test_nonzero_wall_base_keeps_height_as_dimension_not_top_elevation() -> None:
    bundle = sample_bundle()
    west = next(wall for wall in bundle["wall_branch_graph"]["walls"] if wall["id"] == "W-WEST")
    west.update({"base_m": 0.5, "height_m": 2.3})
    rehash(bundle)
    validate_bundle(bundle)
    report = _artifact_geometry(bundle)
    assert next(wall for wall in report["walls"] if wall["id"] == "W-WEST")["height_m"] == 2.3
    assert verify_contract_geometry(bundle, report)["status"] == "pass"


def test_return_wall_face_can_support_a_jamb_with_less_than_50mm_owner_margin() -> None:
    bundle = sample_bundle()
    entry = bundle["opening_contract"]["openings"][0]
    entry["segment_m"] = [[0.01, 0.0], [0.91, 0.0]]
    entry["jamb_before_support"] = {
        "mode": "return_wall_face", "supporting_wall_id": "W-WEST",
        "junction_id": "J-001", "face_distance_m": 0.0,
        "effective_support_m": 0.11, "provenance": "human-confirmed return",
        "solid_provenance": "W-WEST closed wall mesh",
    }
    entry["jamb_after_support"].update({"face_distance_m": 2.09, "effective_support_m": 2.09})
    source_entry = next(anchor for anchor in bundle["source"]["anchors"] if anchor["anchor_id"] == "P-ENTRY")
    source_entry["points_norm"] = [[83.3333333333, 1000.0]]
    source_entry["points_metric_m"] = [[0.5, 0.0]]
    bundle["source"]["anchor_set_hash"] = compute_anchor_set_hash(bundle["source"]["coordinate_space"], bundle["source_hash"], bundle["source"]["normalized_hash"], bundle["source"]["anchors"])
    bundle["wall_branch_graph"]["junctions"][0].update({"kind": "return", "provenance": "human-confirmed return"})
    rehash(bundle)
    assert validate_bundle(bundle)["structure_hash"] == bundle["structure_hash"]


def test_return_support_rejects_forged_effective_length_below_50mm_union() -> None:
    bundle = sample_bundle()
    entry = bundle["opening_contract"]["openings"][0]
    entry["segment_m"] = [[0.01, 0.0], [0.91, 0.0]]
    entry["jamb_before_support"] = {
        "mode": "return_wall_face", "supporting_wall_id": "W-WEST",
        "junction_id": "J-001", "face_distance_m": 0.0,
        "effective_support_m": 0.06, "provenance": "forged",
        "solid_provenance": "thin wall",
    }
    entry["jamb_after_support"].update({"face_distance_m": 2.09, "effective_support_m": 2.09})
    next(wall for wall in bundle["wall_branch_graph"]["walls"] if wall["id"] == "W-WEST")["thickness_m"] = 0.06
    bundle["wall_branch_graph"]["junctions"][0].update({"kind": "return", "provenance": "human-confirmed return"})
    source_entry = next(anchor for anchor in bundle["source"]["anchors"] if anchor["anchor_id"] == "P-ENTRY")
    source_entry["points_norm"] = [[83.3333333333, 1000.0]]
    source_entry["points_metric_m"] = [[0.5, 0.0]]
    bundle["source"]["anchor_set_hash"] = compute_anchor_set_hash(bundle["source"]["coordinate_space"], bundle["source_hash"], bundle["source"]["normalized_hash"], bundle["source"]["anchors"])
    rehash(bundle)
    with pytest.raises(ResearchModelError, match="wall-solid union support is below 50mm|declared effective support"):
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
