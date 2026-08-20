import copy

from Floor_engine_server.whole_home_geometry import geometry_facts_hash
from Floor_engine_server.whole_home_geometry_kernel import compile_geometry_manifest
from Floor_engine_server.whole_home_global_topology import build_global_wall_topology


def _row(handle, points):
    return {
        "points": points,
        "cad_provenance": {
            "handle": handle,
            "source_handle": handle,
            "root_handle": handle,
        },
    }


def _two_room_wall_faces(offset_x=0.0, offset_z=0.0):
    def points(values):
        return [[x + offset_x, z + offset_z] for x, z in values]

    return [
        _row("outer-a", points([(0, 0), (8, 0), (8, 6), (0, 6), (0, 0)])),
        _row("outer-b", points([(.24, .24), (7.76, .24), (7.76, 5.76), (.24, 5.76), (.24, .24)])),
        _row("partition-left-a", points([(4.0, .24), (4.0, 2.40)])),
        _row("partition-left-b", points([(4.0, 3.20), (4.0, 5.76)])),
        _row("partition-right-a", points([(4.12, .24), (4.12, 2.40)])),
        _row("partition-right-b", points([(4.12, 3.20), (4.12, 5.76)])),
    ]


def test_global_topology_builds_one_render_shell_and_closes_door_only_for_spaces():
    result = build_global_wall_topology(
        _two_room_wall_faces(),
        opening_candidates=[{
            "candidate_id": "door", "kind": "door", "width_m": .8,
            "status": "accepted", "axis_segment_cad_m": [[4.06, 2.4], [4.06, 3.2]],
            "source_handles": ["door-source"],
        }],
        wall_height_m=2.8,
    )
    summary = result["summary"]
    assert summary["method"] == "cad-global-wall-topology-v1"
    assert summary["source_segment_count"] == 12
    assert summary["source_coverage_ratio"] == 1.0
    assert summary["wall_footprint_count"] == 1
    assert summary["wall_component_count"] == 1
    assert summary["space_candidate_count"] == 2
    assert summary["topology_close_radius_m"] == .45
    assert summary["fine_topology_close_radius_m"] == .16
    footprint = result["wall_footprints"][0]
    assert footprint["source_representation"] == "global_wall_footprint"
    assert footprint["review_status"] == "needs_review"
    # The rendered footprint preserves the 800mm doorway instead of filling
    # it with the room-discovery closing radius.
    assert len(footprint["interior_rings"]) >= 1


def test_source_backed_wide_opening_axis_closes_spaces_without_filling_render_wall():
    rows = _two_room_wall_faces()
    # Widen the partition gap to 1.4 m: the capped global closing radius alone
    # cannot bridge it.  The exact source-backed closed-position door axis can.
    rows[2] = _row("partition-left-a", [(4.0, .24), (4.0, 2.10)])
    rows[3] = _row("partition-left-b", [(4.0, 3.50), (4.0, 5.76)])
    rows[4] = _row("partition-right-a", [(4.12, .24), (4.12, 2.10)])
    rows[5] = _row("partition-right-b", [(4.12, 3.50), (4.12, 5.76)])

    without_axis = build_global_wall_topology(rows)
    review_candidate = {
        "candidate_id": "door-wide", "kind": "door", "status": "review",
        "width_m": 1.4,
        "axis_segment_cad_m": [[4.06, 2.10], [4.06, 3.50]],
        "source_handles": ["door-arc"],
    }
    with_review_axis = build_global_wall_topology(
        rows, opening_candidates=[review_candidate])
    accepted_candidate = copy.deepcopy(review_candidate)
    accepted_candidate["status"] = "accepted"
    with_axis = build_global_wall_topology(
        rows, opening_candidates=[accepted_candidate])

    assert without_axis["summary"]["space_candidate_count"] == 1
    assert with_review_axis["summary"]["space_candidate_count"] == 1
    assert with_review_axis["summary"]["opening_width_sample_count"] == 0
    assert with_review_axis["summary"]["opening_axis_barrier_count"] == 0
    assert with_axis["summary"]["space_candidate_count"] == 2
    assert with_axis["summary"]["opening_width_sample_count"] == 1
    assert with_axis["summary"]["opening_axis_barrier_count"] == 1
    assert with_axis["summary"]["opening_axis_barriers"][0]["candidate_id"] == "door-wide"
    # Room discovery uses the barrier, but render wall geometry remains source
    # derived and preserves the physical doorway as an opening in its ring.
    assert (with_axis["summary"]["wall_area_m2"]
            == without_axis["summary"]["wall_area_m2"])


def test_distinct_bed_markers_select_smallest_topology_scale_that_separates_rooms():
    rows = _two_room_wall_faces()
    # A 1.2 m unclassified doorway is wider than the normal 450 mm closing
    # radius can bridge.  Two source-backed bed centres prove that treating
    # the result as one bedroom is a topology conflict, but they do not add a
    # rendered wall or pretend that the review doorway is accepted.
    rows[2] = _row("partition-left-a", [(4.0, .24), (4.0, 2.20)])
    rows[3] = _row("partition-left-b", [(4.0, 3.40), (4.0, 5.76)])
    rows[4] = _row("partition-right-a", [(4.12, .24), (4.12, 2.20)])
    rows[5] = _row("partition-right-b", [(4.12, 3.40), (4.12, 5.76)])

    baseline = build_global_wall_topology(rows)
    result = build_global_wall_topology(rows, semantic_anchors=[
        {"anchor_id": "bed-left", "space_marker": "bed", "point_m": [2, 3]},
        {"anchor_id": "bed-right", "space_marker": "bed", "point_m": [6, 3]},
    ])

    assert baseline["summary"]["space_candidate_count"] == 1
    assert result["summary"]["space_candidate_count"] == 2
    assert result["summary"]["topology_close_radius_base_m"] == .45
    assert result["summary"]["topology_close_radius_m"] == .6
    assert result["summary"]["adaptive_topology_selected"] is True
    assert result["summary"]["space_marker_kinds"] == {"bed": 2}
    assert result["summary"]["adaptive_topology_trials"][0][
        "marker_conflict_count"] == 1
    assert result["summary"]["adaptive_topology_trials"][-3][
        "marker_conflict_count"] == 0
    # Space discovery changes, render geometry does not.
    assert result["summary"]["wall_area_m2"] == baseline["summary"]["wall_area_m2"]


def test_multiscale_topology_recovers_narrow_space_without_duplicating_open_plan():
    x = 4.7
    rows = [
        _row("outer-a", [(0, 0), (6, 0), (6, 4), (0, 4), (0, 0)]),
        _row("outer-b", [(.24, .24), (5.76, .24), (5.76, 3.76),
                          (.24, 3.76), (.24, .24)]),
        _row("store-left-a", [(x, .24), (x, 1.0)]),
        _row("store-left-b", [(x + .12, .24), (x + .12, 1.0)]),
        _row("store-top-a", [(x, 1.6), (5.76, 1.6)]),
        _row("store-top-b", [(x, 1.72), (5.76, 1.72)]),
    ]
    openings = [{
        "candidate_id": "store-open", "kind": "open_connection",
        "status": "accepted", "width_m": .6,
        "axis_segment_cad_m": [[x + .06, 1.0], [x + .06, 1.66]],
        "source_handles": ["store-left-a", "store-top-a"],
    }]
    # Three ordinary facade openings make the coarse pass use its 550 mm
    # cap, reproducing the real plan where a 1 m-class store would otherwise
    # be erased.  Their axes lie on source walls and do not partition rooms.
    for index, y in enumerate((0.0, .24, 4.0), 1):
        openings.append({
            "candidate_id": f"window-{index}", "kind": "window",
            "status": "accepted", "width_m": 1.2,
            "axis_segment_cad_m": [[1.0, y], [2.2, y]],
            "source_handles": ["outer-a"],
        })

    result = build_global_wall_topology(
        rows, opening_candidates=openings,
        semantic_anchors=[{
            "anchor_id": "store-label", "semantic_profile": "storage",
            "point_m": [5.2, .9],
        }])
    summary = result["summary"]

    assert summary["topology_close_radius_m"] == .55
    assert summary["fine_topology_close_radius_m"] == .16
    assert summary["coarse_space_candidate_count"] == 1
    assert summary["fine_space_candidate_count"] == 2
    assert summary["fine_supplement_space_count"] == 1
    assert summary["fine_supplement_evidence"][0]["semantic_anchors"] == [{
        "anchor_id": "store-label", "semantic_profile": "storage",
        "reference_profile": "",
    }]
    assert summary["space_candidate_count"] == 2
    assert sorted(round(space.area, 4) for space in result["_space_polygons"])[0] == 1.2682


def test_multiscale_topology_recovers_only_semantic_residual_of_overlapping_face():
    # At the coarse 550 mm closing scale the narrow lower-right room is
    # swallowed into the main face.  At the fine scale it remains source-free
    # space, but its fine face also overlaps the already accepted main room.
    # The parser must retain only the uncovered, labelled residual instead of
    # either dropping the room or duplicating the full fine face.
    x = 4.7
    rows = [
        _row("outer-a", [(0, 0), (6, 0), (6, 4), (0, 4), (0, 0)]),
        _row("outer-b", [(.24, .24), (5.76, .24), (5.76, 3.76),
                          (.24, 3.76), (.24, .24)]),
        _row("narrow-left-a", [(x, .24), (x, 1.0)]),
        _row("narrow-left-b", [(x + .12, .24), (x + .12, 1.0)]),
        _row("narrow-top-a", [(x, 1.9), (5.76, 1.9)]),
        _row("narrow-top-b", [(x, 2.02), (5.76, 2.02)]),
    ]
    openings = [{
        "candidate_id": f"facade-{index}", "kind": "window",
        "status": "accepted", "width_m": 1.2,
        "axis_segment_cad_m": [[1.0, y], [2.2, y]],
        "source_handles": ["outer-a"],
    } for index, y in enumerate((0.0, .24, 4.0), 1)]

    without_anchor = build_global_wall_topology(
        rows, opening_candidates=openings)
    result = build_global_wall_topology(
        rows, opening_candidates=openings,
        semantic_anchors=[{
            "anchor_id": "kitchen-label", "semantic_profile": "kitchen",
            "reference_profile": "kitchen", "point_m": [5.23, .8],
        }])

    assert without_anchor["summary"]["space_candidate_count"] == 1
    assert result["summary"]["coarse_space_candidate_count"] == 1
    assert result["summary"]["fine_space_candidate_count"] == 1
    assert result["summary"]["fine_supplement_space_count"] == 1
    evidence = result["summary"]["fine_supplement_evidence"][0]
    assert evidence["decision"] == (
        "accepted_uncovered_semantic_residual_from_overlapping_fine_face")
    assert evidence["source_fine_face_coarse_overlap_ratio"] > .90
    assert evidence["coarse_overlap_ratio"] == 0.0
    assert evidence["semantic_anchors"] == [{
        "anchor_id": "kitchen-label", "semantic_profile": "kitchen",
        "reference_profile": "kitchen",
    }]
    assert result["summary"]["space_candidate_count"] == 2
    assert sum(space.area for space in result["_space_polygons"]) \
        < sum(space.area for space in without_anchor["_space_polygons"]) + 2.0


def test_global_topology_is_translation_invariant_in_model_coordinates():
    local = build_global_wall_topology(_two_room_wall_faces())
    shifted = build_global_wall_topology(
        _two_room_wall_faces(100.0, -250.0), origin_x=100.0, origin_z=-250.0)
    assert shifted["summary"]["wall_area_m2"] == local["summary"]["wall_area_m2"]
    assert shifted["wall_footprints"][0]["points"] == local["wall_footprints"][0]["points"]
    assert shifted["wall_footprints"][0]["interior_rings"] == local["wall_footprints"][0]["interior_rings"]


def test_geometry_manifest_prefers_global_footprint_over_fragment_walls():
    topology = build_global_wall_topology(_two_room_wall_faces())
    model = {
        "geometry_schema_version": 3,
        "input_grade": "vector_authoritative",
        "coordinate_system": "metres-y-up",
        "wall_height_m": 2.8,
        "rooms": [],
        "openings": [],
        "wall_assemblies": [],
        "global_wall_footprints": topology["wall_footprints"],
        "walls": [{
            "id": "legacy-fragment",
            "start": {"x": 0, "z": 0},
            "end": {"x": 1, "z": 0},
            "thickness_m": .1,
            "height_m": 2.8,
            "review_status": "accepted",
        }],
    }
    manifest = compile_geometry_manifest(
        model, project_id="global-topology-test", registration_hash="registration")
    assert len(manifest["wall_parts"]) == 1
    assert manifest["wall_parts"][0]["source_representation"] == "global_wall_footprint"
    assert manifest["wall_parts"][0]["entity_id"] == "cad_global_wall_footprint_1"


def test_global_footprint_participates_in_geometry_facts_hash():
    topology = build_global_wall_topology(_two_room_wall_faces())
    model = {
        "geometry_schema_version": 3,
        "input_grade": "vector_authoritative",
        "coordinate_system": "metres-y-up",
        "wall_height_m": 2.8,
        "wall_assemblies": [],
        "walls": [],
        "rooms": [],
        "openings": [],
        "global_wall_footprints": topology["wall_footprints"],
    }
    changed = copy.deepcopy(model)
    changed["global_wall_footprints"][0]["points"][0]["x"] += .01
    assert geometry_facts_hash(model) != geometry_facts_hash(changed)


def test_mismatched_global_footprint_falls_back_and_preserves_window_sill_and_header():
    topology = build_global_wall_topology(_two_room_wall_faces())
    model = {
        "geometry_schema_version": 3,
        "input_grade": "vector_authoritative",
        "coordinate_system": "metres-y-up",
        "wall_height_m": 2.8,
        "rooms": [],
        "fixed_objects": [],
        "wall_assemblies": [{
            "id": "host", "review_status": "accepted",
            "centerline": [{"x": 0, "z": 0}, {"x": 8, "z": 0}],
            "footprint_polygon": [
                {"x": 0, "z": -.12}, {"x": 8, "z": -.12},
                {"x": 8, "z": .12}, {"x": 0, "z": .12},
            ],
            "thickness_m": .24, "height_m": 2.8,
            "source_representation": "paired_faces",
        }],
        "global_wall_footprints": topology["wall_footprints"],
        "walls": [{
            "id": "wall-host", "wall_assembly_id": "host",
            "start": {"x": 0, "z": 0}, "end": {"x": 8, "z": 0},
            "thickness_m": .24, "height_m": 2.8,
            "review_status": "accepted",
        }],
        "openings": [{
            "id": "window", "wall_id": "wall-host", "wall_assembly_id": "host",
            "kind": "window", "offset_m": 2, "width_m": 1.2,
            "height_m": 1.2, "sill_height_m": .9,
            "review_status": "accepted",
        }],
    }

    manifest = compile_geometry_manifest(
        model, project_id="global-window-test", registration_hash="registration")

    assert manifest["global_wall_footprint_selection"]["decision"] == (
        "rejected_fallback_to_wall_assemblies")
    assert not [
        row for row in manifest["wall_parts"]
        if row.get("source_representation") == "global_opening_vertical_closure"
    ]
    opening_band = [
        row for row in manifest["wall_parts"]
        if abs(float(row["bounds_min"][0]) - 2.0) <= 1e-8
        and abs(float(row["bounds_max"][0]) - 3.2) <= 1e-8
    ]
    assert len(opening_band) == 2
    assert sorted((row["bounds_min"][1], row["bounds_max"][1])
                  for row in opening_band) == [(0.0, .9), (2.1, 2.8)]
