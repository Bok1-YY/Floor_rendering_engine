import copy

from shapely.geometry import Polygon
from shapely.ops import unary_union

from Floor_engine_server.whole_home_geometry_kernel import (
    GEOMETRY_KERNEL_VERSION,
    _surface_triangles,
    compile_geometry_manifest,
    manifest_triangles,
)


def _model():
    return {
        "schema_version": 3,
        "coordinate_system": "metres-y-up",
        "walls": [{
            "id": "wall-a", "start": {"x": 0, "z": 0}, "end": {"x": 6, "z": 0},
            "thickness_m": .2, "height_m": 2.8,
        }],
        "rooms": [{
            "id": "room-a", "polygon": [
                {"x": 0, "z": 0}, {"x": 6, "z": 0},
                {"x": 6, "z": 4}, {"x": 0, "z": 4},
            ],
        }],
        "openings": [{
            "id": "door-a", "wall_id": "wall-a", "offset_m": 2,
            "width_m": 1, "height_m": 2.1, "sill_height_m": 0,
            "review_status": "accepted",
        }],
        "fixed_objects": [], "cameras": [],
    }


def test_manifest_is_deterministic_and_excludes_camera_state():
    model = _model()
    first = compile_geometry_manifest(model, registration_hash="registration")
    model["cameras"] = [{"id": "camera"}]
    second = compile_geometry_manifest(model, registration_hash="registration")
    assert first["manifest_hash"] == second["manifest_hash"]
    assert first["geometry_kernel_version"] == GEOMETRY_KERNEL_VERSION
    assert len(first["vertices"]) == len(second["vertices"])


def test_door_creates_two_wall_columns_and_header():
    manifest = compile_geometry_manifest(_model())
    wall_parts = manifest["wall_parts"]
    assert len(wall_parts) == 3
    assert manifest["opening_voids"][0]["opening_id"] == "door-a"
    triangles = manifest_triangles(manifest)
    assert len(triangles) == sum(len(row["indices"]) for row in manifest["parts"]) // 3


def test_opening_matches_wall_assembly_even_when_legacy_wall_id_is_also_present():
    model = _model()
    model["walls"][0]["wall_assembly_id"] = "assembly-a"
    model["wall_assemblies"] = [{
        "id": "assembly-a", "source_representation": "paired_faces",
        "centerline": [{"x": 0, "z": 0}, {"x": 6, "z": 0}],
        "footprint_polygon": [[0, -.1], [6, -.1], [6, .1], [0, .1]],
        "thickness_m": .2, "height_m": 2.8,
    }]
    model["openings"][0].update(
        wall_id="wall-a", wall_assembly_id="assembly-a")

    manifest = compile_geometry_manifest(model)

    assert len(manifest["wall_parts"]) == 3
    opening_band = [
        row for row in manifest["wall_parts"]
        if abs(float(row["bounds_min"][0]) - 2.0) <= 1e-8
        and abs(float(row["bounds_max"][0]) - 3.0) <= 1e-8
    ]
    assert len(opening_band) == 1
    assert opening_band[0]["bounds_min"][1] == 2.1


def test_floor_projection_matches_source_room_polygon():
    manifest = compile_geometry_manifest(_model())
    floor = manifest["floor_parts"][0]
    vertices = manifest["vertices"]
    indices = floor["indices"]
    triangles = [Polygon([(vertices[index][0], vertices[index][2]) for index in indices[offset:offset + 3]])
                 for offset in range(0, len(indices), 3)]
    area = sum(item.area for item in triangles)
    assert area == 24


def test_physical_space_is_the_floor_authority_for_open_plan_semantic_zones():
    model = _model()
    model["physical_spaces"] = [{
        "id": "physical-open-plan",
        "polygon": [
            {"x": 0, "z": 0}, {"x": 6, "z": 0},
            {"x": 6, "z": 4}, {"x": 0, "z": 4},
        ],
    }]
    model["rooms"] = [{
        "id": "living-zone", "polygon": [
            {"x": 0, "z": 0}, {"x": 3, "z": 0},
            {"x": 3, "z": 4}, {"x": 0, "z": 4},
        ],
    }, {
        "id": "kitchen-zone", "polygon": [
            {"x": 3, "z": 0}, {"x": 6, "z": 0},
            {"x": 6, "z": 4}, {"x": 3, "z": 4},
        ],
    }]

    manifest = compile_geometry_manifest(model)

    assert len(manifest["floor_parts"]) == 1
    assert manifest["floor_parts"][0]["entity_id"] == "physical-open-plan"
    assert manifest["floor_parts"][0]["source_kind"] == "physical_space"


def test_wall_assembly_footprint_is_preferred_over_duplicate_legacy_wall():
    model = _model()
    model["walls"][0]["wall_assembly_id"] = "assembly-a"
    model["wall_assemblies"] = [{
        "id": "assembly-a", "source_representation": "closed_footprint",
        "footprint_polygon": [[0, -.1], [6, -.1], [6, .1], [0, .1]],
        "height_m": 2.8,
    }]
    model["openings"] = []
    manifest = compile_geometry_manifest(model)
    wall_parts = manifest["wall_parts"]
    assert len(wall_parts) == 1
    assert wall_parts[0]["source_kind"] == "wall_assembly"


def test_manifest_hash_changes_when_locked_geometry_changes():
    first = compile_geometry_manifest(_model())
    changed = copy.deepcopy(_model())
    changed["walls"][0]["end"]["x"] = 6.2
    second = compile_geometry_manifest(changed)
    assert first["manifest_hash"] != second["manifest_hash"]


def test_constrained_floor_triangulation_preserves_concavity_and_hole():
    polygon = Polygon(
        [(0, 0), (6, 0), (6, 2), (2, 2), (2, 6), (0, 6)],
        holes=[[(.5, .5), (1.5, .5), (1.5, 1.5), (.5, 1.5)]],
    )

    triangles = _surface_triangles(polygon, 0.0, True)
    projection = unary_union([
        Polygon([(point[0], point[2]) for point in triangle])
        for triangle in triangles
    ])

    assert polygon.symmetric_difference(projection).area <= 1e-8
    assert projection.covers(Polygon([(2.1, .1), (5.9, .1), (5.9, 1.9), (2.1, 1.9)]))
    assert not projection.covers(Polygon([(.6, .6), (1.4, .6), (1.4, 1.4), (.6, 1.4)]))


def test_mismatched_global_wall_footprint_falls_back_to_confirmed_assembly():
    model = _model()
    model["openings"] = []
    model["walls"][0]["wall_assembly_id"] = "assembly-a"
    model["wall_assemblies"] = [{
        "id": "assembly-a", "source_representation": "closed_footprint",
        "footprint_polygon": [[0, -.1], [6, -.1], [6, .1], [0, .1]],
        "height_m": 2.8,
    }]
    model["global_wall_footprints"] = [{
        "id": "bad-global", "points": [[-1, -1], [7, -1], [7, 1], [-1, 1]],
        "height_m": 2.8,
    }]

    manifest = compile_geometry_manifest(model)

    assert manifest["global_wall_footprint_selection"]["decision"] == (
        "rejected_fallback_to_wall_assemblies")
    assert {part["source_kind"] for part in manifest["wall_parts"]} == {"wall_assembly"}


def test_matching_global_wall_footprint_may_replace_confirmed_assembly():
    model = _model()
    model["openings"] = []
    model["walls"][0]["wall_assembly_id"] = "assembly-a"
    footprint = [[0, -.1], [6, -.1], [6, .1], [0, .1]]
    model["wall_assemblies"] = [{
        "id": "assembly-a", "source_representation": "closed_footprint",
        "footprint_polygon": footprint, "height_m": 2.8,
    }]
    model["global_wall_footprints"] = [{
        "id": "matched-global", "points": footprint, "height_m": 2.8,
    }]

    manifest = compile_geometry_manifest(model)

    assert manifest["global_wall_footprint_selection"]["decision"] == "selected"
    assert {part["source_kind"] for part in manifest["wall_parts"]} == {
        "cad_global_topology"}
