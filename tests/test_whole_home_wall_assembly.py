# -*- coding: utf-8 -*-
import copy
import math

import pytest
from shapely.geometry import Polygon

from Floor_engine_server import whole_home_wall_assembly as wall_assembly


def _row(points, *, handle, layer="A-WALL", closed=False, **extra):
    return {
        "entity_type": "LWPOLYLINE" if closed else "LINE",
        "points": points,
        "closed": closed,
        "layer": layer,
        "cad_provenance": {
            "handle": handle,
            "root_handle": f"root-{handle}",
            "source_handle": handle,
            "layer": layer,
            "effective_layer": layer,
            "block": "WALL_CHILD",
            "insert_chain": [{"handle": "insert-1", "block": "WALL_CHILD"}],
        },
        **extra,
    }


def _valid_pair(*, separation=.20, overlap=1.0, angle_deg=0.0):
    length = 4.0
    shifted = length * (1.0 - overlap)
    angle = math.radians(angle_deg)
    second_start = (shifted, separation)
    second_end = (shifted + length * math.cos(angle),
                  separation + length * math.sin(angle))
    return [
        _row([(0, 0), (length, 0)], handle="10"),
        _row([second_start, second_end], handle="11"),
    ]


def test_paired_faces_produce_true_centerline_thickness_footprint_and_evidence():
    assemblies = wall_assembly.build_wall_assemblies(_valid_pair())

    assert len(assemblies) == 1
    result = assemblies[0]
    assert result["source_representation"] == "paired_faces"
    assert result["review_status"] == "accepted"
    assert result["start"] == {"x": 0.0, "z": 0.1}
    assert result["end"] == {"x": 4.0, "z": 0.1}
    assert result["thickness_m"] == pytest.approx(.20)
    assert result["thickness_source"] == "cad_geometry"
    assert Polygon(result["footprint_polygon"]).area == pytest.approx(.8)
    assert result["pairing_evidence"]["projected_overlap_ratio"] == 1.0
    assert result["source_entity_handles"] == ["10", "11"]
    assert result["source_root_handles"] == ["root-10", "root-11"]
    assert result["source_layers"] == ["A-WALL"]
    assert len(result["source_insert_chains"]) == 2
    assert result["source_insert_chains"][0][0]["block"] == "WALL_CHILD"
    assert len(result["cad_provenance"]["source_entities"]) == 2


def test_long_face_pairs_with_multiple_disjoint_short_face_intervals():
    rows = [
        _row([(0, 0), (8, 0)], handle="long"),
        _row([(0, .2), (3, .2)], handle="short-a"),
        _row([(4, .2), (8, .2)], handle="short-b"),
    ]

    assemblies = wall_assembly.build_wall_assemblies(rows)

    assert len(assemblies) == 2
    assert all(row["review_status"] == "accepted" for row in assemblies)
    assert sorted(row["length_m"] for row in assemblies) == [3.0, 4.0]
    assert all("long" in row["source_entity_handles"] for row in assemblies)
    long_intervals = sorted(
        row["pairing_evidence"]["source_intervals_m"]["0"] for row in assemblies)
    assert long_intervals == [[0.0, 3.0], [4.0, 8.0]]


def test_duplicate_pair_candidate_cannot_reoccupy_a_source_interval():
    rows = [
        _row([(0, 0), (4, 0)], handle="long"),
        _row([(0, .2), (4, .2)], handle="face-a"),
        _row([(0, .2), (4, .2)], handle="face-a-duplicate"),
    ]

    assemblies = wall_assembly.build_wall_assemblies(rows)

    accepted = [row for row in assemblies if row["review_status"] == "accepted"]
    redundant = [row for row in assemblies if row["review_status"] == "rejected"]
    assert len(accepted) == 1
    assert len(redundant) == 1
    assert accepted[0]["length_m"] == 4.0
    assert redundant[0]["source_entity_handles"] in (["face-a"], ["face-a-duplicate"])
    assert redundant[0]["reason_codes"] == [
        "cad_wall_source_redundant_with_accepted_footprint"]


def test_model_origin_translation_does_not_rewrite_cad_source_coordinates():
    rows = [
        _row([(100, -40), (104, -40)], handle="12"),
        _row([(100, -39.8), (104, -39.8)], handle="13"),
    ]
    result = wall_assembly.build_wall_assemblies(
        rows, origin_x=100, origin_z=-40)[0]

    assert result["start"] == {"x": 0.0, "z": .1}
    assert result["end"] == {"x": 4.0, "z": .1}
    assert result["source_entities"][0]["source_segment_m"] == [
        [100.0, -40.0], [104.0, -40.0]
    ]
    assert result["source_entities"][0]["model_segment_m"] == [
        [0.0, 0.0], [4.0, 0.0]
    ]


@pytest.mark.parametrize("rows", [
    _valid_pair(separation=.059),
    _valid_pair(separation=.601),
    _valid_pair(overlap=.799),
    _valid_pair(angle_deg=1.01),
])
def test_pairing_fails_closed_outside_any_v1_threshold(rows):
    assemblies = wall_assembly.build_wall_assemblies(rows)

    assert len(assemblies) == 2
    assert {row["source_representation"] for row in assemblies} == {
        "human_confirmed_ambiguous"
    }
    assert all(row["review_status"] == "needs_review" for row in assemblies)
    assert all(row["thickness_m"] is None for row in assemblies)
    assert all(row["footprint_polygon"] is None for row in assemblies)


@pytest.mark.parametrize("rows", [
    _valid_pair(separation=.06),
    _valid_pair(separation=.60),
    _valid_pair(overlap=.80),
    _valid_pair(angle_deg=1.0),
])
def test_pairing_accepts_exact_v1_threshold_boundaries(rows):
    assemblies = wall_assembly.build_wall_assemblies(rows)

    assert len(assemblies) == 1
    assert assemblies[0]["source_representation"] == "paired_faces"


def test_node_snap_is_limited_to_twenty_millimetres_and_preserves_source_segments():
    rows = [
        _row([(0, 0), (2, 0)], handle="20", source_representation="centerline",
             thickness_m=.18, thickness_source="cad_attribute"),
        _row([(2.019, 0), (4, 0)], handle="21", source_representation="centerline",
             thickness_m=.18, thickness_source="cad_attribute"),
        _row([(4.021, 0), (6, 0)], handle="22", source_representation="centerline",
             thickness_m=.18, thickness_source="cad_attribute"),
    ]
    assemblies = wall_assembly.build_wall_assemblies(rows)

    assert assemblies[0]["end"] == assemblies[1]["start"]
    assert assemblies[1]["end"] != assemblies[2]["start"]
    # CAD provenance remains the original unsnapped segment, not rewritten proof.
    assert assemblies[0]["source_entities"][0]["source_segment_m"] == [[0.0, 0.0], [2.0, 0.0]]
    assert assemblies[1]["source_entities"][0]["source_segment_m"] == [[2.019, 0.0], [4.0, 0.0]]


def test_transitive_node_chain_cannot_move_any_endpoint_beyond_snap_limit():
    rows = [
        _row([(0, y), (1, y)], handle=f"chain-{index}",
             source_representation="centerline", thickness_m=.1,
             thickness_source="cad_attribute")
        for index, y in enumerate((0, .019, .038, .057), 1)
    ]
    assemblies = wall_assembly.build_wall_assemblies(rows)

    for assembly in assemblies:
        entity = assembly["source_entities"][0]
        source = entity["source_segment_m"]
        model = entity["model_segment_m"]
        assert max(math.dist(source[index], model[index]) for index in (0, 1)) <= .02


def test_explicit_centerline_requires_a_declared_positive_thickness_and_source():
    unresolved = wall_assembly.build_wall_assemblies([
        _row([(0, 0), (3, 0)], handle="30", source_representation="centerline"),
    ])[0]
    assert unresolved["source_representation"] == "human_confirmed_ambiguous"
    assert unresolved["thickness_m"] is None
    assert "cad_centerline_thickness_unresolved" in unresolved["reason_codes"]

    resolved = wall_assembly.build_wall_assemblies([
        _row([(0, 0), (3, 0)], handle="31", source_representation="centerline",
             thickness_m=.24, thickness_source="cad_attribute"),
    ])[0]
    assert resolved["source_representation"] == "centerline"
    assert resolved["thickness_m"] == .24
    assert resolved["thickness_source"] == "cad_attribute"
    assert Polygon(resolved["footprint_polygon"]).area == pytest.approx(.72)


def test_unpaired_wall_line_never_silently_becomes_a_120mm_centerline():
    result = wall_assembly.build_wall_assemblies([
        _row([(0, 0), (3, 0)], handle="40"),
    ])[0]

    assert result["source_representation"] == "human_confirmed_ambiguous"
    assert result["resolved_as"] is None
    assert result["thickness_m"] is None
    assert result["footprint_polygon"] is None
    assert result["production_blockers"] == ["cad_wall_representation_unresolved"]


def test_pending_line_fully_covered_by_accepted_footprint_is_rejected_as_redundant():
    rows = [
        _row([(0, 0), (4, 0), (4, .2), (0, .2), (0, 0)],
             handle="footprint", closed=True),
        _row([(0, .1), (4, .1)], handle="duplicate-axis"),
    ]

    assemblies = wall_assembly.build_wall_assemblies(rows)

    accepted = next(row for row in assemblies if row["review_status"] == "accepted")
    redundant = next(row for row in assemblies if row["review_status"] == "rejected")
    assert redundant["source_representation"] == "redundant_evidence"
    assert redundant["resolved_as"] == "redundant_evidence"
    assert redundant["production_blockers"] == []
    assert redundant["reason_codes"] == [
        "cad_wall_source_redundant_with_accepted_footprint"]
    proof = redundant["redundancy_evidence"]
    assert proof["accepted_wall_assembly_id"] == accepted["id"]
    assert proof["footprint_buffer_m"] == .02
    assert proof["coverage_ratio"] == 1.0
    assert proof["uncovered_length_m"] == 0.0
    assert proof["axis_angle_difference_deg"] == 0.0


def test_pending_line_covered_by_union_of_collinear_accepted_walls_is_redundant():
    rows = [
        _row([(0, 0), (2, 0), (2, .2), (0, .2), (0, 0)],
             handle="left-footprint", closed=True),
        _row([(2, 0), (4, 0), (4, .2), (2, .2), (2, 0)],
             handle="right-footprint", closed=True),
        _row([(0, .1), (4, .1)], handle="spanning-duplicate"),
    ]

    assemblies = wall_assembly.build_wall_assemblies(rows)

    redundant = next(row for row in assemblies
                     if row["source_entity_handles"] == ["spanning-duplicate"])
    assert redundant["review_status"] == "rejected"
    proof = redundant["redundancy_evidence"]
    assert len(proof["accepted_wall_assembly_ids"]) == 2
    assert proof["coverage_ratio"] == 1.0
    assert proof["uncovered_length_m"] == 0.0


def test_pending_line_with_one_percent_endpoint_gap_remains_production_unresolved():
    rows = [
        _row([(0, 0), (4, 0), (4, .2), (0, .2), (0, 0)],
             handle="footprint", closed=True),
        # The 20 mm support buffer covers approximately 99% of this line; any
        # real uncovered tail must keep it pending.
        _row([(-.0604, .1), (4.02, .1)], handle="endpoint-gap"),
    ]

    assemblies = wall_assembly.build_wall_assemblies(rows)

    pending = next(row for row in assemblies if row["review_status"] == "needs_review")
    assert pending["source_entity_handles"] == ["endpoint-gap"]
    assert pending["production_blockers"] == ["cad_wall_representation_unresolved"]
    assert all(row["review_status"] != "rejected" for row in assemblies)


def test_pending_parallel_line_outside_accepted_footprint_remains_unresolved():
    rows = [
        _row([(0, 0), (4, 0), (4, .2), (0, .2), (0, 0)],
             handle="footprint", closed=True),
        _row([(0, .221), (4, .221)], handle="parallel-outside"),
    ]

    assemblies = wall_assembly.build_wall_assemblies(rows)

    pending = next(row for row in assemblies if row["review_status"] == "needs_review")
    assert pending["source_entity_handles"] == ["parallel-outside"]
    assert all(row["review_status"] != "rejected" for row in assemblies)


def test_cross_line_inside_accepted_footprint_remains_unresolved():
    rows = [
        _row([(0, 0), (4, 0), (4, .2), (0, .2), (0, 0)],
             handle="footprint", closed=True),
        _row([(2, 0), (2, .2)], handle="cross-line"),
    ]

    assemblies = wall_assembly.build_wall_assemblies(rows)

    pending = next(row for row in assemblies if row["review_status"] == "needs_review")
    assert pending["source_entity_handles"] == ["cross-line"]
    assert all(row["review_status"] != "rejected" for row in assemblies)


def test_short_corner_cap_covered_by_union_of_perpendicular_walls_is_junction_evidence():
    rows = [
        _row([(0, 0), (2, 0), (2, .2), (0, .2), (0, 0)],
             handle="horizontal-wall", closed=True),
        _row([(2, 0), (2.2, 0), (2.2, 2), (2, 2), (2, 0)],
             handle="vertical-wall", closed=True),
        _row([(1.92, .1), (2.2, .1)], handle="corner-cap"),
    ]

    assemblies = wall_assembly.build_wall_assemblies(rows)

    cap = next(row for row in assemblies
               if row["source_entity_handles"] == ["corner-cap"])
    assert cap["review_status"] == "rejected"
    assert cap["source_representation"] == "junction_evidence"
    assert cap["junction_evidence"]["coverage_ratio"] == 1.0
    assert any(row.get("coverage_method")
               == "accepted_wall_footprint_union_v1"
               for row in cap["junction_evidence"]["supports"])


def test_short_corner_cap_can_use_two_distinct_endpoint_supports_without_fake_coverage():
    rows = [
        _row([(0, 0), (2, 0), (2, .3), (0, .3), (0, 0)],
             handle="horizontal-wall", closed=True),
        _row([(2.15, .3), (2.45, .3), (2.45, 2), (2.15, 2), (2.15, .3)],
             handle="offset-vertical-wall", closed=True),
        _row([(2, .2), (2.3, .2)], handle="corner-face-cap"),
    ]

    assemblies = wall_assembly.build_wall_assemblies(rows)

    cap = next(row for row in assemblies
               if row["source_entity_handles"] == ["corner-face-cap"])
    assert cap["review_status"] == "rejected"
    proof = cap["junction_evidence"]
    assert proof["support_method"] == (
        "two_endpoint_distinct_accepted_wall_support_v1")
    assert proof["endpoint_support_ratio"] == 1.0
    assert proof["coverage_ratio"] < 1.0
    assert {row["endpoint_index"] for row in proof["supports"]} == {0, 1}


def test_wall_face_cap_equal_to_thickness_at_axis_end_is_terminal_evidence():
    rows = [
        _row([(0, .1), (.2, .1), (.2, 3), (0, 3), (0, .1)],
             handle="vertical-wall", closed=True),
        _row([(0, 0), (.2, 0)], handle="wall-face-cap"),
    ]

    assemblies = wall_assembly.build_wall_assemblies(rows)

    cap = next(row for row in assemblies
               if row["source_entity_handles"] == ["wall-face-cap"])
    assert cap["review_status"] == "rejected"
    assert cap["source_representation"] == "junction_evidence"
    proof = cap["junction_evidence"]
    assert proof["support_method"] == "single_accepted_wall_face_cap_v1"
    assert proof["supports"][0]["source_length_m"] == pytest.approx(.2)
    assert proof["supports"][0]["expected_half_thickness_offset_m"] == \
        pytest.approx(.1)


def test_closed_polyline_is_preserved_as_source_footprint():
    row = _row([(1, 2), (5, 2), (5, 2.2), (1, 2.2), (1, 2)],
               handle="50", closed=True)
    result = wall_assembly.build_wall_assemblies([row])[0]

    assert result["source_representation"] == "closed_footprint"
    assert result["resolved_as"] == "closed_footprint"
    assert result["footprint_polygon"] == [[1.0, 2.0], [5.0, 2.0],
                                             [5.0, 2.2], [1.0, 2.2]]
    assert Polygon(result["footprint_polygon"]).area == pytest.approx(.8)
    assert result["thickness_m"] == pytest.approx(.2)
    assert result["opening_axis"] in (
        [[1.0, 2.1], [5.0, 2.1]], [[5.0, 2.1], [1.0, 2.1]])
    assert result["source_entities"][0]["source_polygon_m"][-1] == [1.0, 2.0]


def test_invalid_closed_footprint_is_rejected_instead_of_repaired_silently():
    bow_tie = _row([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)],
                   handle="51", closed=True)

    result = wall_assembly.build_wall_assemblies([bow_tie])[0]

    assert result["source_representation"] == "invalid_closed_footprint"
    assert result["resolved_as"] is None
    assert result["review_status"] == "needs_review"
    assert result["reason_codes"] == ["cad_wall_footprint_invalid"]
    assert result["production_blockers"] == ["cad_wall_footprint_invalid"]
    assert result["source_entity_handles"] == ["51"]


@pytest.mark.parametrize("points", [
    [(0, 0), (1, 0), (0, 0)],
    [(0, 0), (0, 0)],
    [(0, 0), (1, 0), (2, 0), (0, 0)],
])
def test_degenerate_closed_footprint_is_audited_instead_of_leaking_shapely_error(points):
    row = _row(points, handle="degenerate-closed", closed=True)

    result = wall_assembly.build_wall_assemblies([row])[0]

    assert result["source_representation"] == "invalid_closed_footprint"
    assert result["review_status"] == "needs_review"
    assert result["reason_codes"] == ["cad_wall_footprint_invalid"]
    assert result["validation_error"]["entity_index"] == 0


def test_two_point_closed_return_path_preserves_unique_axis_for_later_audit():
    row = _row([(1, 2), (2.2192, 2), (1, 2)],
               handle="opening-face-return", closed=True)

    result = wall_assembly.build_wall_assemblies([row])[0]

    assert result["source_representation"] == "invalid_closed_footprint"
    assert result["review_status"] == "needs_review"
    assert result["source_centerline"] == [[1.0, 2.0], [2.2192, 2.0]]
    proof = result["degenerate_return_path_evidence"]
    assert proof["method"] == "cad_closed_two_point_return_path_v1"
    assert proof["unique_point_count"] == 2
    assert proof["unique_axis_length_m"] == pytest.approx(1.2192)
    assert proof["source_path_length_m"] == pytest.approx(2.4384)
    assert proof["return_length_ratio"] == pytest.approx(2.0)


def test_human_confirmation_resolves_ambiguous_wall_with_audited_measurement():
    ambiguous = wall_assembly.build_wall_assemblies([
        _row([(0, 0), (3, 0)], handle="60"),
    ])[0]
    result = wall_assembly.confirm_ambiguous_assembly(
        ambiguous, thickness_m=.22, reviewer="tester", reason="现场尺寸标注 220mm")

    assert result["id"] == ambiguous["id"]
    assert result["source_representation"] == "human_confirmed_ambiguous"
    assert result["resolved_as"] == "centerline"
    assert result["review_status"] == "accepted"
    assert result["thickness_m"] == .22
    assert result["thickness_source"] == "human_measurement"
    assert result["human_review"]["reviewer"] == "tester"
    assert result["source_entity_handles"] == ["60"]


def test_human_confirmation_requires_reviewer_reason_and_ambiguous_source():
    ambiguous = wall_assembly.build_wall_assemblies([
        _row([(0, 0), (3, 0)], handle="61"),
    ])[0]
    with pytest.raises(wall_assembly.WallAssemblyError) as error:
        wall_assembly.confirm_ambiguous_assembly(
            ambiguous, thickness_m=.2, reviewer="", reason="")
    assert error.value.code == "cad_wall_review_audit_missing"

    accepted = wall_assembly.build_wall_assemblies(_valid_pair())[0]
    with pytest.raises(wall_assembly.WallAssemblyError) as error:
        wall_assembly.confirm_ambiguous_assembly(
            accepted, thickness_m=.2, reviewer="tester", reason="wrong target")
    assert error.value.code == "cad_wall_not_ambiguous"


def _paired_wall():
    return wall_assembly.build_wall_assemblies(_valid_pair())[0]


def _opening(wall_id, offset, width, **extra):
    return {
        "id": extra.pop("id", "opening-1"),
        "wall_assembly_id": wall_id,
        "kind": extra.pop("kind", "door"),
        "start_offset_m": offset,
        "width_m": width,
        "reviewer": extra.pop("reviewer", "tester"),
        "reason": extra.pop("reason", "CAD 图上可见开口"),
        "base_revision": 7,
        "operation_id": "op-1",
        **extra,
    }


def test_manual_opening_is_bound_to_wall_interval_and_source_evidence():
    wall = _paired_wall()
    annotation = _opening(wall["id"], .5, .9)
    result = wall_assembly.bind_manual_opening_annotations([wall], [annotation])[0]

    assert result["wall_assembly_id"] == wall["id"]
    assert result["wall_id"] == wall["id"]
    assert result["offset_m"] == .5
    assert result["width_m"] == .9
    assert result["source_kind"] == "human_annotated_on_vector_source"
    assert result["nearby_source_handles"] == ["10", "11"]
    assert result["reviewer"] == "tester"
    assert result["cad_provenance"]["wall_assembly_id"] == wall["id"]
    assert result["evidence_geometry"]["opening_axis_segment_m"] == [[.5, .1], [1.4, .1]]


def test_manual_opening_allows_touching_intervals_but_rejects_positive_overlap():
    wall = _paired_wall()
    first = _opening(wall["id"], .5, 1.0, id="first")
    touching = _opening(wall["id"], 1.5, .8, id="touching")
    results = wall_assembly.bind_manual_opening_annotations([wall], [first, touching])
    assert [row["id"] for row in results] == ["first", "touching"]

    overlap = _opening(wall["id"], 1.49, .8, id="overlap")
    with pytest.raises(wall_assembly.WallAssemblyError) as error:
        wall_assembly.bind_manual_opening_annotations([wall], [first, overlap])
    assert error.value.code == "cad_opening_overlap"


def test_manual_opening_checks_existing_openings_for_overlap():
    wall = _paired_wall()
    existing = [{"id": "native-door", "wall_id": wall["id"],
                 "offset_m": 1.0, "width_m": .9}]
    annotation = _opening(wall["id"], 1.5, .8)

    with pytest.raises(wall_assembly.WallAssemblyError) as error:
        wall_assembly.bind_manual_opening_annotations(
            [wall], [annotation], existing_openings=existing)
    assert error.value.code == "cad_opening_overlap"
    assert error.value.details["conflicting_opening_id"] == "native-door"


@pytest.mark.parametrize(("annotation", "code"), [
    ({"wall_assembly_id": "missing", "kind": "door", "start_offset_m": 0,
      "width_m": .8, "reviewer": "t", "reason": "r"}, "cad_opening_wall_not_found"),
    (_opening("WALL_ID", -.01, .8), "cad_opening_interval_outside_wall"),
    (_opening("WALL_ID", 3.5, .6), "cad_opening_interval_outside_wall"),
    (_opening("WALL_ID", .5, 0), "cad_opening_width_invalid"),
    (_opening("WALL_ID", .5, .8, kind="hole"), "cad_opening_kind_invalid"),
    (_opening("WALL_ID", .5, .8, reviewer=""), "cad_opening_review_audit_missing"),
])
def test_manual_opening_validation_fails_closed(annotation, code):
    wall = _paired_wall()
    annotation = copy.deepcopy(annotation)
    if annotation.get("wall_assembly_id") == "WALL_ID":
        annotation["wall_assembly_id"] = wall["id"]

    with pytest.raises(wall_assembly.WallAssemblyError) as error:
        wall_assembly.bind_manual_opening_annotations([wall], [annotation])
    assert error.value.code == code


def test_manual_opening_cannot_bind_unresolved_ambiguous_wall():
    wall = wall_assembly.build_wall_assemblies([
        _row([(0, 0), (3, 0)], handle="70"),
    ])[0]

    with pytest.raises(wall_assembly.WallAssemblyError) as error:
        wall_assembly.bind_manual_opening_annotations(
            [wall], [_opening(wall["id"], .5, .8)])
    assert error.value.code == "cad_opening_wall_unresolved"


def test_complex_closed_footprint_requires_specific_wall_run_before_opening_binding():
    wall = wall_assembly.build_wall_assemblies([
        _row([(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4), (0, 0)],
             handle="80", closed=True),
    ])[0]
    assert wall["source_representation"] == "closed_footprint"
    assert wall["opening_blockers"] == ["cad_closed_footprint_opening_axis_ambiguous"]

    with pytest.raises(wall_assembly.WallAssemblyError) as error:
        wall_assembly.bind_manual_opening_annotations(
            [wall], [_opening(wall["id"], .5, .8)])
    assert error.value.code == "cad_opening_axis_unresolved"


def _group_row(points, *, index, root, kind="LINE", closed=False, block=""):
    row = _row(points, handle=f"source-{index}", layer="ARBITRARY", closed=closed)
    row.update(entity_index=index, entity_type=kind, aci_color=6)
    row["cad_provenance"].update(
        root_handle=root, block=block,
        insert_chain=([{"handle": root, "block": block or "opaque"}] if block else []),
    )
    return row


def test_role_decomposition_detects_generic_window_frame_without_names_or_colours():
    rows = [
        _group_row([(0, 0), (1, 0)], index=1, root="wall-a"),
        _group_row([(2, .2), (4, .2)], index=2, root="wall-b"),
        _group_row([(1, .06), (2, .06)], index=3, root="frame", block="opaque"),
        _group_row([(1, .14), (2, .14)], index=4, root="frame", block="opaque"),
        _group_row([(1, .06), (1, .14)], index=5, root="frame", block="opaque"),
        _group_row([(2, .06), (2, .14)], index=6, root="frame", block="opaque"),
    ]
    result = wall_assembly.decompose_cad_entity_roles(rows)

    assert {row["entity_index"] for row in result["wall_rows"]} == {1, 2}
    assert {row["entity_index"] for row in result["opening_evidence_rows"]} == {3, 4, 5, 6}
    assert result["summary"]["retained_wall_entity_count"] == 2
    assert result["summary"]["opening_evidence_entity_count"] == 4
    assert result["raw_opening_summary"]["candidate_count"] == 1
    candidate = result["raw_opening_candidates"][0]
    assert candidate["kind"] == "window"
    assert candidate["width_m"] == pytest.approx(1.0)
    assert candidate["confidence"] >= .9
    assert "parallel_frame_rails" in candidate["reason_codes"]
    evidence = next(row for row in result["evidence"] if row["root_handle"] == "frame")
    assert evidence["role"] == "opening_symbol"
    assert evidence["blocks"] == ["opaque"]  # retained evidence, not a classifier token
    assert evidence["colors"] == [6]


def test_role_decomposition_groups_loose_window_rails_by_geometry_and_wall_end_support():
    selected = [
        _group_row([(0, 0), (1, 0)], index=1, root="wall-left-a"),
        _group_row([(2, 0), (4, 0)], index=2, root="wall-right-a"),
        _group_row([(0, .2), (1, .2)], index=3, root="wall-left-b"),
        _group_row([(2, .2), (4, .2)], index=4, root="wall-right-b"),
    ]
    # Every primitive deliberately has a different root handle, matching raw
    # model-space window drafting in real DWG case 03.
    context = [
        _group_row([(1, .06), (2, .06)], index=20, root="loose-20"),
        _group_row([(1, .14), (2, .14)], index=21, root="loose-21"),
        _group_row([(1, .06), (1, .14)], index=22, root="loose-22"),
        _group_row([(2, .06), (2, .14)], index=23, root="loose-23"),
        _group_row([(1.5, .06), (1.5, .14)], index=24, root="loose-24"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(selected, context_rows=context)

    assert result["raw_opening_summary"]["candidate_count"] == 1
    candidate = result["raw_opening_candidates"][0]
    assert candidate["kind"] == "window"
    assert candidate["width_m"] == pytest.approx(1.0)
    assert set(candidate["source_entity_indexes"]) == {20, 21, 22, 23, 24}
    assert "loose_frame_geometry_component" in candidate["reason_codes"]
    assert candidate["evidence_geometry"]["grouping_method"] == (
        "loose_maximal_parallel_rail_pair")
    assert max(candidate["evidence_geometry"]["wall_endpoint_support_distance_m"]) <= .11


def test_loose_parallel_fixture_away_from_wall_gap_is_not_a_window():
    selected = [
        _group_row([(0, 0), (4, 0)], index=1, root="wall-a"),
        _group_row([(0, .2), (4, .2)], index=2, root="wall-b"),
    ]
    context = [
        _group_row([(1, 1.0), (2, 1.0)], index=20, root="fixture-20"),
        _group_row([(1, 1.1), (2, 1.1)], index=21, root="fixture-21"),
        _group_row([(1, 1.0), (1, 1.1)], index=22, root="fixture-22"),
        _group_row([(2, 1.0), (2, 1.1)], index=23, root="fixture-23"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(selected, context_rows=context)

    assert result["raw_opening_summary"]["candidate_count"] == 0


def test_frame_centred_between_continuous_wall_faces_remains_an_opening():
    rows = [
        _group_row(
            [(0, 0), (4, 0), (4, .2), (0, .2), (0, 0)],
            index=1, root="wall-strip", kind="LWPOLYLINE", closed=True,
        ),
        _group_row([(1, .06), (2, .06)], index=2, root="frame", block="opaque"),
        _group_row([(1, .14), (2, .14)], index=3, root="frame", block="opaque"),
        _group_row([(1, .06), (1, .14)], index=4, root="frame", block="opaque"),
        _group_row([(2, .06), (2, .14)], index=5, root="frame", block="opaque"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(rows)

    assert [row["entity_index"] for row in result["wall_rows"]] == [1]
    assert result["summary"]["role_counts"]["wall_footprint"] == 1
    assert result["summary"]["reason_counts"]["closed_elongated_wall_band_geometry"] == 1
    assert result["raw_opening_summary"]["candidate_count"] == 1


def test_frame_with_both_wall_faces_on_same_side_is_not_an_opening():
    selected = [
        _group_row([(0, .1), (4, .1)], index=1, root="wall-near"),
        _group_row([(0, .25), (4, .25)], index=2, root="wall-far"),
    ]
    context = [
        _group_row([(1, -.1), (2, -.1)], index=20, root="same-side-frame"),
        _group_row([(1, .1), (2, .1)], index=21, root="same-side-frame"),
        _group_row([(1, -.1), (1, .1)], index=22, root="same-side-frame"),
        _group_row([(2, -.1), (2, .1)], index=23, root="same-side-frame"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(selected, context_rows=context)

    assert result["raw_opening_summary"]["candidate_count"] == 0


def test_loose_multi_panel_window_merges_overlapping_subframes_to_full_axis():
    selected = [
        _group_row([(0, 0), (4, 0)], index=1, root="wall-a"),
        _group_row([(0, .2), (4, .2)], index=2, root="wall-b"),
    ]
    primitives = [
        ((1, .06), (2.6, .06)), ((1, .14), (2.6, .14)),
        ((1, .08), (1.75, .08)), ((1.85, .08), (2.6, .08)),
        ((1, .12), (1.75, .12)), ((1.85, .12), (2.6, .12)),
        ((1, .06), (1, .14)), ((2.6, .06), (2.6, .14)),
        ((1.75, .06), (1.75, .14)), ((1.85, .06), (1.85, .14)),
    ]
    context = [
        _group_row([start, end], index=20 + offset, root=f"panel-{offset}")
        for offset, (start, end) in enumerate(primitives)
    ]

    result = wall_assembly.decompose_cad_entity_roles(selected, context_rows=context)

    assert result["raw_opening_summary"]["candidate_count"] == 1
    candidate = result["raw_opening_candidates"][0]
    assert candidate["width_m"] == pytest.approx(1.6)
    assert candidate["axis_segment_cad_m"][0] == pytest.approx([1.0, .1])
    assert candidate["axis_segment_cad_m"][1] == pytest.approx([2.6, .1])
    assert "adjacent_subframe_geometry_merged" in candidate["reason_codes"]
    assert candidate["evidence_geometry"]["merged_subframe_count"] >= 2


def test_role_decomposition_filters_repeated_compact_insert_signature_generically():
    rows = [
        _group_row([(0, 0), (4, 0)], index=1, root="wall"),
    ]
    index = 10
    for group_number, offset in enumerate((1.0, 2.0, 3.0), 1):
        root = f"glyph-{group_number}"
        for start, end in (
            ((offset, .40), (offset + .10, .40)),
            ((offset + .10, .40), (offset + .10, .46)),
            ((offset + .10, .46), (offset, .46)),
            ((offset, .46), (offset, .40)),
            ((offset, .40), (offset + .10, .46)),
            ((offset + .10, .40), (offset, .46)),
        ):
            rows.append(_group_row([start, end], index=index, root=root, block="same-shape"))
            index += 1
    result = wall_assembly.decompose_cad_entity_roles(rows)

    assert [row["entity_index"] for row in result["wall_rows"]] == [1]
    assert len(result["context_rows"]) == 18
    assert result["summary"]["reason_counts"]["repeated_compact_geometry_signature"] == 18
    assert all(row["role"] == "context_fixture" for row in result["evidence"]
               if row["root_handle"].startswith("glyph-"))


def test_role_decomposition_classifies_high_specificity_nested_cabinet_as_context():
    rows = [_group_row([(0, 0), (8, 0)], index=1, root="wall")]
    rectangles = [
        [(4.20, 4.70), (5.80, 4.70), (5.80, 5.30), (4.20, 5.30), (4.20, 4.70)],
        [(4.22, 4.72), (5.78, 4.72), (5.78, 5.28), (4.22, 5.28), (4.22, 4.72)],
        [(4.24, 4.74), (5.76, 4.74), (5.76, 5.26), (4.24, 5.26), (4.24, 4.74)],
    ]
    for index, points in enumerate(rectangles, 2):
        rows.append(_group_row(points, index=index, root=f"outline-{index}",
                               kind="LWPOLYLINE", closed=True))
    rows.append(_group_row([(4.25, 4.75), (5.75, 5.25)], index=5, root="diagonal"))
    rows.append(_group_row([(4.25, 5.25), (5.75, 4.75)], index=6, root="diagonal-2"))

    result = wall_assembly.decompose_cad_entity_roles(rows)

    assert [row["entity_index"] for row in result["wall_rows"]] == [1]
    assert {row["entity_index"] for row in result["context_rows"]} == {2, 3, 4, 5, 6}
    assert result["review_rows"] == []
    assert result["summary"]["reason_counts"]["nested_compact_closed_contours"] == 5
    assert result["summary"]["confidence_counts"]["high"] == 6


def test_compact_fixture_envelope_uses_context_x_and_nested_contours_to_demote_walls():
    selected = [
        _group_row(
            [(0, 0), (2.4, 0), (2.4, .6), (0, .6), (0, 0)],
            index=1, root="opaque-envelope", kind="LWPOLYLINE", closed=True,
        ),
        _group_row([(0, 0), (2.4, 0)], index=2, root="opaque-face-a"),
        _group_row([(0, .6), (2.4, .6)], index=3, root="opaque-face-b"),
    ]
    context = [
        _group_row(
            [(.02, .02), (2.38, .02), (2.38, .58), (.02, .58), (.02, .02)],
            index=20, root="detail-outline-a", kind="LWPOLYLINE", closed=True,
        ),
        _group_row(
            [(.04, .04), (2.36, .04), (2.36, .56), (.04, .56), (.04, .04)],
            index=21, root="detail-outline-b", kind="LWPOLYLINE", closed=True,
        ),
        _group_row([(.08, .06), (1.12, .54)], index=22, root="detail-diagonal-a"),
        _group_row([(.08, .54), (1.12, .06)], index=23, root="detail-diagonal-b"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    assert result["wall_rows"] == []
    assert {row["entity_index"] for row in result["context_rows"]} == {1, 2, 3}
    assert result["summary"]["reason_counts"]["compact_fixture_envelope_covered"] == 3
    evidence = next(row for row in result["evidence"]
                    if row["root_handle"] == "opaque-envelope")
    envelope = evidence["fixture_envelopes"][0]
    assert envelope["evidence_kind"] == "compact_fixture_envelope_v1"
    assert envelope["nested_contour_count"] == 2
    assert envelope["internal_x_pair_count"] >= 1
    assert set(envelope["diagonal_source_handles"]) == {
        "source-22", "source-23"}


def test_compact_fixture_envelope_does_not_demote_column_or_independent_wall_band():
    selected = [
        _group_row(
            [(0, 0), (.6, 0), (.6, .4), (0, .4), (0, 0)],
            index=1, root="column", kind="LWPOLYLINE", closed=True,
        ),
        _group_row(
            [(1, 0), (4, 0), (4, .2), (1, .2), (1, 0)],
            index=2, root="independent-wall-band", kind="LWPOLYLINE", closed=True,
        ),
    ]
    context = [
        _group_row(
            [(.02, .02), (.58, .02), (.58, .38), (.02, .38), (.02, .02)],
            index=20, root="column-outline-a", kind="LWPOLYLINE", closed=True,
        ),
        _group_row(
            [(.04, .04), (.56, .04), (.56, .36), (.04, .36), (.04, .04)],
            index=21, root="column-outline-b", kind="LWPOLYLINE", closed=True,
        ),
        _group_row([(.06, .05), (.54, .35)], index=22, root="column-cross-a"),
        _group_row([(.06, .35), (.54, .05)], index=23, root="column-cross-b"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    assert {row["entity_index"] for row in result["wall_rows"]} == {2}
    assert result["context_rows"] == []
    assert {row["entity_index"] for row in result["review_rows"]} == {1}
    assert result["summary"]["role_counts"]["wall_footprint"] == 1
    assert result["summary"]["reason_counts"]["closed_geometry_wall_band_unproven"] == 1
    assert "compact_fixture_envelope_covered" not in result["summary"]["reason_counts"]


def test_compact_closed_rectangles_are_review_not_automatic_3d_walls():
    selected = [
        _group_row(
            [(0, 0), (.8, 0), (.8, .6), (0, .6), (0, 0)],
            index=1, root="cabinet-scale", kind="LWPOLYLINE", closed=True,
        ),
        _group_row(
            [(1, 0), (1.45, 0), (1.45, .2), (1, .2), (1, 0)],
            index=2, root="fixture-scale", kind="LWPOLYLINE", closed=True,
        ),
    ]

    result = wall_assembly.decompose_cad_entity_roles(selected)

    assert result["wall_rows"] == []
    assert {row["entity_index"] for row in result["review_rows"]} == {1, 2}
    assert result["summary"]["reason_counts"]["closed_geometry_wall_band_unproven"] == 2


def test_low_fill_l_shaped_closed_polygon_is_proved_as_uniform_wall_band():
    selected = [_group_row(
        [(0, 0), (2, 0), (2, .2), (.2, .2),
         (.2, 2), (0, 2), (0, 0)],
        index=1, root="l-wall-band", kind="LWPOLYLINE", closed=True,
    )]

    result = wall_assembly.decompose_cad_entity_roles(selected)

    assert result["review_rows"] == []
    assert len(result["wall_rows"]) == 1
    assert result["summary"]["reason_counts"][
        "closed_uniform_wall_band_geometry"] == 1
    evidence = result["evidence"][0]["closed_wall_band_evidence"]
    assert evidence["method"] == "cad_closed_uniform_wall_band_v1"
    assert evidence["wall_thickness_m"] == pytest.approx(.2)
    assert evidence["polygon_fill_ratio"] < .6
    assembly = wall_assembly.build_wall_assemblies(result["wall_rows"])[0]
    assert assembly["review_status"] == "accepted"
    assert assembly["footprint_polygon"]
    assert assembly["thickness_m"] == pytest.approx(.2)
    assert assembly["centerline_scope"] == "representative_wall_band_axis"


def test_large_closed_perimeter_stays_draft_geometry_but_not_accepted_wall_volume():
    selected = [_group_row(
        [(0, 0), (4, 0), (4, 3), (0, 3), (0, 0)],
        index=1, root="room-perimeter", kind="LWPOLYLINE", closed=True,
    )]

    result = wall_assembly.decompose_cad_entity_roles(selected)
    assert [row["entity_index"] for row in result["wall_rows"]] == [1]
    assert result["review_rows"] == []
    assert result["summary"]["reason_counts"]["closed_perimeter_wall_role_unproven"] == 1
    assembly = wall_assembly.build_wall_assemblies(result["wall_rows"])[0]
    assert assembly["review_status"] == "needs_review"
    assert assembly["production_blockers"] == ["cad_closed_perimeter_wall_role_unproven"]


def test_duplicate_outer_and_inner_perimeters_become_four_measured_wall_faces():
    outer = [(0, 0), (10, 0), (10, 7), (0, 7), (0, 0)]
    inner = [(.2, .2), (9.8, .2), (9.8, 6.8), (.2, 6.8), (.2, .2)]
    selected = [
        _group_row(outer, index=1, root="outer-a", kind="LWPOLYLINE", closed=True),
        _group_row(outer, index=2, root="outer-b", kind="LWPOLYLINE", closed=True),
        _group_row(outer, index=3, root="outer-c", kind="LWPOLYLINE", closed=True),
        _group_row(inner, index=4, root="inner-a", kind="LWPOLYLINE", closed=True),
        _group_row(inner, index=5, root="inner-b", kind="LWPOLYLINE", closed=True),
    ]

    result = wall_assembly.decompose_cad_entity_roles(selected)

    assert result["review_rows"] == []
    assert result["summary"]["perimeter_wall_shell_count"] == 1
    assert result["summary"]["perimeter_wall_shell_fragment_count"] == 8
    assert result["summary"]["role_counts"]["structural_evidence"] == 5
    assert len(result["wall_rows"]) == 8
    shell_proof = next(
        evidence["perimeter_wall_shell_evidence"]
        for evidence in result["evidence"]
        if evidence.get("root_handle") == "cad_duplicate_perimeter_shell_1")
    assert shell_proof["outer_duplicate_count"] == 3
    assert shell_proof["inner_duplicate_count"] == 2
    assert shell_proof["measured_wall_thickness_m"] == pytest.approx(.2)

    assemblies = wall_assembly.build_wall_assemblies(result["wall_rows"])
    assert len(assemblies) == 4
    assert all(row["review_status"] == "accepted" for row in assemblies)
    assert all(row["source_representation"] == "paired_faces"
               for row in assemblies)
    assert {round(row["thickness_m"], 6) for row in assemblies} == {.2}


def test_one_outer_and_one_inner_perimeter_do_not_self_prove_wall_shell():
    selected = [
        _group_row(
            [(0, 0), (10, 0), (10, 7), (0, 7), (0, 0)],
            index=1, root="outer-only", kind="LWPOLYLINE", closed=True),
        _group_row(
            [(.2, .2), (9.8, .2), (9.8, 6.8), (.2, 6.8), (.2, .2)],
            index=2, root="inner-only", kind="LWPOLYLINE", closed=True),
    ]

    result = wall_assembly.decompose_cad_entity_roles(selected)

    assert result["summary"]["perimeter_wall_shell_count"] == 0
    assert result["summary"]["perimeter_wall_shell_fragment_count"] == 0
    assert result["summary"]["reason_counts"][
        "closed_perimeter_wall_role_unproven"] == 2
    assemblies = wall_assembly.build_wall_assemblies(result["wall_rows"])
    assert len(assemblies) == 2
    assert all(row["review_status"] == "needs_review" for row in assemblies)


def test_capped_leaf_arc_and_return_path_bind_door_on_duplicate_wall_shell():
    outer = [(0, 0), (10, 0), (10, 7), (0, 7), (0, 0)]
    inner = [(.2, .2), (9.8, .2), (9.8, 6.8), (.2, 6.8), (.2, .2)]
    selected = [
        _group_row(outer, index=1, root="outer-a", kind="LWPOLYLINE", closed=True),
        _group_row(outer, index=2, root="outer-b", kind="LWPOLYLINE", closed=True),
        _group_row(outer, index=3, root="outer-c", kind="LWPOLYLINE", closed=True),
        _group_row(inner, index=4, root="inner-a", kind="LWPOLYLINE", closed=True),
        _group_row(inner, index=5, root="inner-b", kind="LWPOLYLINE", closed=True),
        _group_row(
            [(10, 4), (10, 5), (10, 4)], index=6,
            root="door-return-face", kind="LWPOLYLINE", closed=True),
    ]
    # The two leaf faces intentionally differ in length, like the production
    # DWG; a free-end cap proves that they are one thin open door leaf.
    context = [
        _group_row([(9.9, 5.0), (8.95, 5.0)], index=20, root="leaf-a"),
        _group_row([(8.95, 4.95), (9.84, 4.95)], index=21, root="leaf-b"),
        _group_row([(8.95, 4.95), (8.95, 5.0)], index=22, root="leaf-cap"),
        _group_row(
            [(8.95, 4.975), (9.27957576, 4.35457576), (9.9, 4.025)],
            index=23, root="mismatched-swing", kind="ARC"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    doors = [row for row in result["raw_opening_candidates"]
             if row["kind"] == "door"]
    assert len(doors) == 1
    door = doors[0]
    assert door["evidence_geometry"]["method"] == \
        "cad_capped_leaf_shell_face_swing_v1"
    assert door["width_m"] == pytest.approx(1.0)
    assert door["evidence_geometry"]["wall_thickness_m"] == pytest.approx(.2)
    assert door["evidence_geometry"]["leaf_rail_source_handles"] == [
        "source-20", "source-21"]
    assert {row["entity_index"] for row in result["opening_evidence_rows"]} == {6}

    assemblies = wall_assembly.build_wall_assemblies(result["wall_rows"])
    bound = wall_assembly.bind_raw_geometry_openings(
        result["raw_opening_candidates"], assemblies)
    bound_door = next(row for row in bound if row["kind"] == "door")
    assert bound_door["status"] == "accepted"
    assert bound_door["wall_assembly_id"]
    assert bound_door["axis_segment_cad_m"] in (
        [[9.9, 4.0], [9.9, 5.0]], [[9.9, 5.0], [9.9, 4.0]])

    without_arc = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context[:-1])
    assert not [row for row in without_arc["raw_opening_candidates"]
                if row["kind"] == "door"]


def test_role_decomposition_does_not_remove_double_face_wall_with_internal_cross_line():
    rows = [
        _group_row([(0, 0), (4, 0)], index=1, root="wall-face-a"),
        _group_row([(0, .2), (4, .2)], index=2, root="wall-face-b"),
        _group_row([(2, 0), (2, .2)], index=3, root="wall-junction"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(rows)

    assert {row["entity_index"] for row in result["wall_rows"]} == {1, 2, 3}
    assert result["context_rows"] == []
    assert "nested_compact_closed_contours" not in result["summary"]["reason_counts"]
    assemblies = wall_assembly.build_wall_assemblies(result["wall_rows"])
    assert sum(row["review_status"] == "accepted" for row in assemblies) == 1


def test_source_proved_attached_exterior_short_connector_is_audit_only():
    row = _group_row(
        [(0, 0), (0, .0381)], index=81,
        root="attached-short-connector")
    row["attached_exterior_boundary_evidence"] = {
        "method": "cad_attached_exterior_double_boundary_v1",
        "space_id": "balcony-1", "chain_kind": "outer",
        "chain_entity_indexes": [78, 79, 80, 81, 82],
        "measured_boundary_separation_m": .1143,
        "attachment_side": "left",
    }

    result = wall_assembly.decompose_cad_entity_roles([row])

    assert result["wall_rows"] == []
    assert result["review_rows"] == []
    assert result["summary"]["role_counts"]["structural_evidence"] == 1
    assert result["summary"]["reason_counts"][
        "attached_exterior_short_chain_connector_evidence"] == 1
    assert result["evidence"][0]["attached_exterior_boundary_evidence"][
        "space_id"] == "balcony-1"


def test_raw_quarter_swing_and_radial_leaf_are_detected_without_block_semantics():
    selected = [
        _group_row([(0, 0), (1, 0)], index=1, root="wall-left"),
        _group_row([(1.9, 0), (4, 0)], index=2, root="wall-right"),
    ]
    arc_points = [
        (1 + .9 * math.cos(math.radians(angle)), .9 * math.sin(math.radians(angle)))
        for angle in range(0, 91, 10)
    ]
    context = [
        _group_row(arc_points, index=20, root="raw-a", kind="ARC"),
        _group_row([(1, 0), (1.9, 0)], index=21, root="raw-b", kind="LINE"),
    ]
    result = wall_assembly.decompose_cad_entity_roles(selected, context_rows=context)

    assert result["raw_opening_summary"]["candidate_count"] == 1
    candidate = result["raw_opening_candidates"][0]
    assert candidate["kind"] == "door"
    assert candidate["width_m"] == pytest.approx(.9, abs=.01)
    assert candidate["axis_segment_cad_m"] == [[1.0, 0.0], [1.9, 0.0]]
    assert set(candidate["source_entity_indexes"]) == {20, 21}
    assert "circular_swing_arc" in candidate["reason_codes"]


def test_tessellated_quarter_swing_and_radial_leaf_recover_exported_door():
    selected = [
        _group_row([(0, 0), (1, 0)], index=1, root="wall-left"),
        _group_row([(1.9, 0), (4, 0)], index=2, root="wall-right"),
    ]
    arc_points = [
        (1 + .9 * math.cos(math.radians(angle)),
         .9 * math.sin(math.radians(angle)))
        for angle in range(0, 91, 6)
    ]
    context = [
        _group_row([first, second], index=20 + number,
                   root=f"arc-chord-{number}", kind="LINE")
        for number, (first, second) in enumerate(zip(
            arc_points, arc_points[1:]))
    ]
    context.append(_group_row(
        [(1, 0), (1.9, 0)], index=60, root="radial-leaf", kind="LINE"))

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    assert result["raw_opening_summary"]["candidate_count"] == 1
    candidate = result["raw_opening_candidates"][0]
    assert candidate["axis_segment_cad_m"] == [[1.0, 0.0], [1.9, 0.0]]
    assert candidate["width_m"] == pytest.approx(.9, abs=.01)
    assert set(candidate["source_entity_indexes"]) == {
        *range(20, 35), 60}
    assert "tessellated_circular_swing_chain" in candidate["reason_codes"]
    proof = candidate["evidence_geometry"]["tessellated_arc_chain"]
    assert proof["method"] == "cad_tessellated_circular_swing_chain_v1"
    assert proof["chord_count"] == 15


def test_single_polyline_quarter_swing_and_radial_leaf_recover_exported_door():
    selected = [
        _group_row([(0, 0), (1, 0)], index=1, root="wall-left"),
        _group_row([(1.9, 0), (4, 0)], index=2, root="wall-right"),
    ]
    arc_points = [
        (1 + .9 * math.cos(math.radians(angle)),
         .9 * math.sin(math.radians(angle)))
        for angle in range(0, 91, 10)
    ]
    context = [
        _group_row(
            arc_points, index=20, root="polyline-arc", kind="LWPOLYLINE"),
        _group_row(
            [(1, 0), (1.9, 0)], index=21, root="radial-leaf", kind="LINE"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    assert result["raw_opening_summary"]["candidate_count"] == 1
    candidate = result["raw_opening_candidates"][0]
    assert candidate["axis_segment_cad_m"] == [[1.0, 0.0], [1.9, 0.0]]
    assert candidate["width_m"] == pytest.approx(.9, abs=.01)
    assert set(candidate["source_entity_indexes"]) == {20, 21}
    proof = candidate["evidence_geometry"]["tessellated_arc_chain"]
    assert proof["source_encoding"] == "single_open_polyline"
    assert proof["source_entity_count"] == 1
    assert proof["chord_count"] == 9


def test_single_circular_polyline_without_radial_leaf_is_not_a_door():
    selected = [
        _group_row([(0, 0), (1, 0)], index=1, root="wall-left"),
        _group_row([(1.9, 0), (4, 0)], index=2, root="wall-right"),
    ]
    arc_points = [
        (1 + .9 * math.cos(math.radians(angle)),
         .9 * math.sin(math.radians(angle)))
        for angle in range(0, 91, 10)
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected,
        context_rows=[_group_row(
            arc_points, index=20, root="polyline-curve", kind="LWPOLYLINE")],
    )

    assert result["raw_opening_summary"]["candidate_count"] == 0


def test_tessellated_curve_without_radial_leaf_is_not_a_door():
    selected = [
        _group_row([(0, 0), (1, 0)], index=1, root="wall-left"),
        _group_row([(1.9, 0), (4, 0)], index=2, root="wall-right"),
    ]
    arc_points = [
        (1 + .9 * math.cos(math.radians(angle)),
         .9 * math.sin(math.radians(angle)))
        for angle in range(0, 91, 6)
    ]
    context = [
        _group_row([first, second], index=20 + number,
                   root=f"curve-{number}", kind="LINE")
        for number, (first, second) in enumerate(zip(
            arc_points, arc_points[1:]))
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    assert result["raw_opening_summary"]["candidate_count"] == 0


def test_tessellated_swing_motif_away_from_wall_network_is_not_a_door():
    selected = [_group_row([(0, 0), (4, 0)], index=1, root="wall")]
    arc_points = [
        (1 + .9 * math.cos(math.radians(angle)),
         3 + .9 * math.sin(math.radians(angle)))
        for angle in range(0, 91, 6)
    ]
    context = [
        _group_row([first, second], index=20 + number,
                   root=f"curve-{number}", kind="LINE")
        for number, (first, second) in enumerate(zip(
            arc_points, arc_points[1:]))
    ]
    context.append(_group_row(
        [(1, 3), (1.9, 3)], index=60, root="radial", kind="LINE"))

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    assert result["raw_opening_summary"]["candidate_count"] == 0


def test_selected_door_arc_and_leaf_become_opening_evidence_not_wall_rows():
    arc_points = [
        (1 + .9 * math.cos(math.radians(angle)), .9 * math.sin(math.radians(angle)))
        for angle in range(0, 91, 10)
    ]
    selected = [
        _group_row([(0, 0), (1, 0)], index=1, root="wall-left"),
        _group_row([(1.9, 0), (4, 0)], index=2, root="wall-right"),
        _group_row(arc_points, index=20, root="door-root", kind="ARC"),
        _group_row([(1, 0), (1.9, 0)], index=21, root="door-root", kind="LINE"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(selected)

    assert {row["entity_index"] for row in result["wall_rows"]} == {1, 2}
    assert {row["entity_index"] for row in result["opening_evidence_rows"]} == {20, 21}
    assert result["summary"]["retained_wall_entity_count"] == 2
    assert result["summary"]["opening_evidence_entity_count"] == 2
    evidence = next(row for row in result["evidence"]
                    if row["root_handle"] == "door-root")
    assert evidence["role"] == "opening_symbol"
    assert evidence["retained_entity_indexes"] == []
    assert "opening_root_geometry_not_wall" in evidence["reason_codes"]


def test_compact_opening_root_consumes_sibling_symbol_caps_not_fake_walls():
    arc_points = [
        (1 + .9 * math.cos(math.radians(angle)),
         .9 * math.sin(math.radians(angle)))
        for angle in range(0, 91, 10)
    ]
    selected = [
        _group_row([(0, 0), (1, 0)], index=1, root="wall-left"),
        _group_row([(1.9, 0), (4, 0)], index=2, root="wall-right"),
        _group_row(arc_points, index=20, root="door-root", kind="ARC"),
        _group_row([(1, 0), (1.9, 0)], index=21, root="door-root"),
        _group_row([(1, 0), (1, .045)], index=22, root="door-root"),
        _group_row([(1.9, 0), (1.9, .045)], index=23, root="door-root"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(selected)

    assert {row["entity_index"] for row in result["wall_rows"]} == {1, 2}
    assert {row["entity_index"] for row in result["opening_evidence_rows"]} == {
        20, 21, 22, 23}
    proof = next(row for row in result["evidence"]
                 if row["root_handle"] == "door-root")
    assert proof["retained_entity_indexes"] == []
    assert "proved_compact_opening_source_root" in proof["reason_codes"]


def test_isolated_compact_arc_is_context_but_wall_connected_arc_remains():
    isolated = [
        (.2 + .1 * math.cos(math.radians(angle)),
         1.0 + .1 * math.sin(math.radians(angle)))
        for angle in range(0, 181, 20)
    ]
    connected = [
        (2.0 + .2 * math.cos(math.radians(angle)),
         .2 * math.sin(math.radians(angle)))
        for angle in range(0, 91, 10)
    ]
    selected = [
        _group_row([(0, 0), (2.2, 0)], index=1, root="wall-left"),
        _group_row([(2.0, .2), (4, .2)], index=2, root="wall-right"),
        _group_row(isolated, index=20, root="fixture-arc", kind="ARC"),
        _group_row(connected, index=21, root="wall-arc", kind="ARC"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(selected)

    assert 20 in {row["entity_index"] for row in result["context_rows"]}
    assert 21 in {row["entity_index"] for row in result["wall_rows"]}
    isolated_proof = next(row for row in result["evidence"]
                          if row["root_handle"] == "fixture-arc")
    assert isolated_proof["reason_codes"] == [
        "isolated_compact_arc_detail_not_wall"]


def test_compact_arc_inside_proved_service_space_envelope_is_fixture_detail():
    outer = [(0, 0), (6, 0), (6, 6), (0, 6), (0, 0)]
    inner = [(.2, .2), (5.8, .2), (5.8, 5.8), (.2, 5.8), (.2, .2)]
    arc = [
        (3 + .1 * math.cos(math.radians(angle)),
         3 + .1 * math.sin(math.radians(angle)))
        for angle in range(0, 181, 20)
    ]
    selected = [
        _group_row(outer, index=1, root="outer", kind="LWPOLYLINE", closed=True),
        _group_row(inner, index=2, root="inner", kind="LWPOLYLINE", closed=True),
        _group_row([(0, 3), (3.1, 3)], index=3, root="connected-run"),
        _group_row(arc, index=20, root="service-arc", kind="ARC"),
    ]
    anchors = [
        {"anchor_id": "kitchen", "semantic_profile": "kitchen",
         "reference_profile": "kitchen", "point_m": [3, 2.4]},
        {"anchor_id": "bed", "semantic_profile": "bedroom",
         "reference_profile": "bedroom", "point_m": [1, 4]},
        {"anchor_id": "bath", "semantic_profile": "bathroom",
         "reference_profile": "bathroom", "point_m": [5, 4]},
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, semantic_anchors=anchors)

    assert result["semantic_building_envelope_evidence"]["status"] == "proved"
    assert 20 in {row["entity_index"] for row in result["context_rows"]}
    proof = next(row for row in result["evidence"]
                 if row["root_handle"] == "service-arc")
    assert proof["reason_codes"] == [
        "semantic_service_space_compact_arc_detail_not_wall"]


def test_inset_hinge_door_symbol_uses_leaf_length_and_opposite_arc_endpoint():
    selected = [
        _group_row([(-2, 0), (0, 0)], index=1, root="wall-left"),
        _group_row([(.75, 0), (3, 0)], index=2, root="wall-right"),
    ]
    center = (.12, .12)
    radius = math.hypot(.63, .12)
    # The symbol ARC is centred 150 mm away from the drawn leaf hinge, while
    # both leaf tips are exact arc endpoints.  This matches the real 03 DWG.
    start_angle = math.atan2(-.12, .63)
    end_angle = math.atan2(.63, -.12)
    if end_angle < start_angle:
        end_angle += math.tau
    arc_points = [
        (center[0] + radius * math.cos(start_angle + (end_angle - start_angle) * i / 12),
         center[1] + radius * math.sin(start_angle + (end_angle - start_angle) * i / 12))
        for i in range(13)
    ]
    context = [
        _group_row(arc_points, index=20, root="door", kind="ARC"),
        _group_row([(0, 0), (0, .75)], index=21, root="door", kind="LINE"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(selected, context_rows=context)

    assert result["raw_opening_summary"]["candidate_count"] == 1
    candidate = result["raw_opening_candidates"][0]
    assert candidate["width_m"] == pytest.approx(.75, abs=.01)
    assert candidate["axis_segment_cad_m"][0] == pytest.approx([0, 0], abs=.02)
    assert candidate["axis_segment_cad_m"][1] == pytest.approx([.75, 0], abs=.02)
    assert candidate["evidence_geometry"]["hinge_inset_from_arc_center_m"] == pytest.approx(
        math.hypot(.12, .12), abs=.01)
    assert candidate["evidence_geometry"]["leaf_tip_to_arc_endpoint_m"] <= .01


def test_open_door_leaf_uses_wall_supported_opposite_arc_endpoint_as_opening_axis():
    selected = [
        _group_row([(0, 0), (1, 0)], index=1, root="wall-left"),
        _group_row([(1.9, 0), (4, 0)], index=2, root="wall-right"),
    ]
    arc_points = [
        (1 + .9 * math.cos(math.radians(angle)), .9 * math.sin(math.radians(angle)))
        for angle in range(0, 91, 10)
    ]
    context = [
        _group_row(arc_points, index=20, root="raw-a", kind="ARC"),
        # The same door is displayed open, perpendicular to its host wall.
        _group_row([(1, 0), (1, .9)], index=21, root="raw-b", kind="LINE"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(selected, context_rows=context)

    candidate = result["raw_opening_candidates"][0]
    assert candidate["axis_segment_cad_m"] == [[1.0, 0.0], [1.9, 0.0]]
    evidence = candidate["evidence_geometry"]
    assert evidence["selected_closed_axis_source"] == "opposite_arc_endpoint"
    assert evidence["drawn_leaf_axis_cad_m"] == [[1.0, 0.0], [1.0, .9]]


def test_inset_arc_hinge_projects_only_to_local_wall_pair_and_transverse_jamb():
    # The far pair deliberately has the same spacing and a lexicographically
    # smaller coordinate.  Without the 200 mm hinge-to-centreline gate it wins
    # the projection score and moves the door roughly three metres away.
    selected = [
        _group_row([(0, 0), (1.05, 0)], index=1, root="near-face-a"),
        _group_row([(0, .15), (1.05, .15)], index=2, root="near-face-b"),
        _group_row([(0, -3), (1.05, -3)], index=3, root="far-face-a"),
        _group_row([(0, -2.85), (1.05, -2.85)], index=4, root="far-face-b"),
        _group_row([(2.05, -3.5), (2.05, .5)], index=5,
                   root="transverse-jamb"),
    ]
    centre = (2.1, .075)
    radius = 1.1
    arc_points = [
        (centre[0] + radius * math.cos(math.radians(angle)),
         centre[1] + radius * math.sin(math.radians(angle)))
        for angle in range(180, 226, 5)
    ]
    leaf_tip = arc_points[-1]
    context = [
        _group_row(arc_points, index=20, root="arc", kind="ARC"),
        _group_row([(2.0, .075), leaf_tip], index=21,
                   root="inset-leaf", kind="LINE"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    candidate = result["raw_opening_candidates"][0]
    evidence = candidate["evidence_geometry"]
    assert evidence["selected_closed_axis_source"] == (
        "measured_wall_face_pair_and_transverse_jamb")
    assert candidate["axis_segment_cad_m"][0] == pytest.approx([2.05, .075])
    assert candidate["axis_segment_cad_m"][1][1] == pytest.approx(.075)
    assert math.dist(*candidate["axis_segment_cad_m"]) == pytest.approx(
        candidate["width_m"])
    projected = next(row for row in evidence["axis_candidates"]
                     if row.get("projection_method"))
    assert projected["hinge_to_wall_centerline_offset_m"] <= .20


def test_three_parallel_open_leaf_rails_recover_door_without_swing_arc():
    selected = [
        _group_row([(-2, -.1), (0, -.1)], index=1, root="left-face-a"),
        _group_row([(-2, .1), (0, .1)], index=2, root="left-face-b"),
        _group_row([(.9, -.1), (3, -.1)], index=3, root="right-face-a"),
        _group_row([(.9, .1), (3, .1)], index=4, root="right-face-b"),
    ]
    diagonal = math.sqrt(.9 ** 2 / 2)
    context = [
        _group_row([(0, -.02), (diagonal, diagonal - .02)],
                   index=20, root="leaf-rail-a", kind="LINE"),
        _group_row([(0, 0), (diagonal, diagonal)],
                   index=21, root="leaf-rail-b", kind="LINE"),
        _group_row([(0, .02), (diagonal, diagonal + .02)],
                   index=22, root="leaf-rail-c", kind="LINE"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    assert result["raw_opening_summary"]["candidate_count"] == 1
    candidate = result["raw_opening_candidates"][0]
    assert candidate["kind"] == "door"
    assert candidate["width_m"] == pytest.approx(.9)
    assert set(candidate["source_entity_indexes"]) == {20, 21, 22}
    assert "swing_leaf_without_arc" in candidate["reason_codes"]
    evidence = candidate["evidence_geometry"]
    assert evidence["method"] == "cad_parallel_door_leaf_without_arc_v1"
    assert evidence["parallel_rail_count"] == 3
    assert evidence["hinge_wall_distance_m"] == pytest.approx(.10)
    assert evidence["free_endpoint_wall_distance_m"] >= .20
    assert candidate["axis_segment_cad_m"][0] == pytest.approx([0, 0], abs=1e-6)
    assert candidate["axis_segment_cad_m"][1] == pytest.approx([.9, 0], abs=1e-6)

    locally_plausible_wall = {
        "id": "continuous-local-wall", "review_status": "accepted",
        "opening_axis": [[-2, 0], [3, 0]], "centerline": [[-2, 0], [3, 0]],
        "thickness_m": .2, "source_entity_handles": ["wall-a", "wall-b"],
    }
    locally_bound = wall_assembly.bind_raw_geometry_openings(
        [candidate], [locally_plausible_wall])[0]
    assert locally_bound["status"] == "review"
    assert "parallel_leaf_requires_global_jamb_proof" in locally_bound[
        "reason_codes"]


def test_one_or_two_diagonal_context_rails_cannot_create_a_door():
    selected = [
        _group_row([(-2, -.1), (0, -.1)], index=1, root="left-face-a"),
        _group_row([(-2, .1), (0, .1)], index=2, root="left-face-b"),
        _group_row([(.9, -.1), (3, -.1)], index=3, root="right-face-a"),
        _group_row([(.9, .1), (3, .1)], index=4, root="right-face-b"),
    ]
    diagonal = math.sqrt(.9 ** 2 / 2)
    context = [
        _group_row([(0, -.02), (diagonal, diagonal - .02)],
                   index=20, root="ambiguous-a", kind="LINE"),
        _group_row([(0, .02), (diagonal, diagonal + .02)],
                   index=21, root="ambiguous-b", kind="LINE"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    assert result["raw_opening_summary"]["candidate_count"] == 0


def test_mirrored_double_leaf_swings_merge_to_one_hinge_to_hinge_opening():
    selected = [
        _group_row([(-2, 0), (0, 0)], index=1, root="wall-left"),
        _group_row([(1.2, 0), (3, 0)], index=2, root="wall-right"),
        # Source drawings commonly retain a threshold/face line through the
        # opening; it supplies the same wall-network support as real case 05.
        _group_row([(0, 0), (1.2, 0)], index=3, root="threshold"),
    ]
    left_arc = [
        (.6 * math.cos(math.radians(angle)),
         .6 * math.sin(math.radians(angle)))
        for angle in range(0, 91, 10)
    ]
    right_arc = [
        (1.2 + .6 * math.cos(math.radians(angle)),
         .6 * math.sin(math.radians(angle)))
        for angle in range(90, 181, 10)
    ]
    context = [
        _group_row(left_arc, index=20, root="left-arc", kind="ARC"),
        _group_row([(0, 0), (0, .6)], index=21, root="left-leaf", kind="LINE"),
        _group_row(right_arc, index=22, root="right-arc", kind="ARC"),
        _group_row([(1.2, 0), (1.2, .6)], index=23, root="right-leaf", kind="LINE"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    assert result["raw_opening_summary"]["candidate_count"] == 1
    candidate = result["raw_opening_candidates"][0]
    assert candidate["kind"] == "door"
    assert candidate["width_m"] == pytest.approx(1.2)
    assert sorted(candidate["axis_segment_cad_m"]) == [[0.0, 0.0], [1.2, 0.0]]
    assert candidate["evidence_geometry"]["double_leaf_door"] is True
    assert "mirrored_double_leaf_geometry_merged" in candidate["reason_codes"]


def test_regular_stair_treads_with_continuous_endpoint_rail_are_context():
    selected = [
        _group_row([(0, index * .18), (1, index * .18)],
                   index=10 + index, root=f"tread-{index}")
        for index in range(7)
    ]
    selected.extend([
        _group_row([(1, 0), (1, 1.08)], index=30, root="stair-rail"),
        _group_row([(2, 2), (6, 2)], index=31, root="real-wall-a"),
        _group_row([(2, 2.2), (6, 2.2)], index=32, root="real-wall-b"),
    ])

    result = wall_assembly.decompose_cad_entity_roles(selected)

    assert result["summary"]["context_entity_count"] == 7
    assert {row["entity_index"] for row in result["context_rows"]} == set(range(10, 17))
    assert {row["entity_index"] for row in result["wall_rows"]} == {30, 31, 32}
    tread_evidence = next(
        row for row in result["evidence"] if row["entity_indexes"] == [10])
    proof = tread_evidence["stair_runs"][0]
    assert proof["tread_count"] == 7
    assert proof["median_spacing_m"] == pytest.approx(.18)
    assert proof["supporting_rail"]["entity_index"] == 30
    assert "regular_stair_tread_run" in tread_evidence["reason_codes"]


def test_regular_parallel_segments_without_endpoint_rail_remain_wall_evidence():
    selected = [
        _group_row([(0, index * .18), (1, index * .18)],
                   index=10 + index, root=f"parallel-{index}")
        for index in range(7)
    ]

    result = wall_assembly.decompose_cad_entity_roles(selected)

    assert result["summary"]["context_entity_count"] == 0
    assert {row["entity_index"] for row in result["wall_rows"]} == set(range(10, 17))


def test_fragmented_opposite_stair_stringer_and_landing_edges_are_context():
    selected = [
        _group_row([(0, 1 + index * .2), (1, 1 + index * .2)],
                   index=10 + index, root=f"tread-{index}")
        for index in range(8)
    ]
    selected.extend([
        # One continuous rail may also be a real house wall and is retained.
        _group_row([(1, 0), (1, 3)], index=30, root="house-side-rail"),
        # The opposite stair stringer is split around drafting/landing joins.
        _group_row([(0, 0), (0, 1.75)], index=31, root="outer-stringer-low"),
        _group_row([(0, 1.65), (0, 3)], index=32, root="outer-stringer-high"),
        _group_row([(0, 0), (1.5, 0)], index=33, root="lower-landing-edge"),
        _group_row([(0, 3), (1, 3)], index=34, root="upper-landing-edge"),
        _group_row([(3, 0), (6, 0)], index=40, root="unrelated-wall-a"),
        _group_row([(3, .2), (6, .2)], index=41, root="unrelated-wall-b"),
    ])

    result = wall_assembly.decompose_cad_entity_roles(selected)

    context_indexes = {row["entity_index"] for row in result["context_rows"]}
    assert set(range(10, 18)).issubset(context_indexes)
    assert {31, 32, 33, 34}.issubset(context_indexes)
    assert {30, 40, 41}.issubset({
        row["entity_index"] for row in result["wall_rows"]})
    stringer = next(row for row in result["evidence"]
                    if row["entity_indexes"] == [31])
    proof = stringer["stair_runs"][0]
    assert proof["fragmented_opposite_rail"]["entity_indexes"] == [31, 32]
    assert {row["entity_index"] for row in proof["landing_edges"]} == {33, 34}


def test_long_house_wall_sharing_one_terminal_stair_tread_is_split_not_discarded():
    selected = [
        _group_row([(0, 1 + index * .2), (1, 1 + index * .2)],
                   index=10 + index, root=f"tread-{index}")
        for index in range(8)
    ]
    selected.extend([
        _group_row([(1, 0), (1, 3)], index=30, root="house-side-rail"),
        _group_row([(0, 0), (0, 1.75)], index=31, root="outer-stringer-low"),
        _group_row([(0, 1.65), (0, 3)], index=32, root="outer-stringer-high"),
        # The first metre is a stair landing/tread; x=1..4 is a real wall.
        _group_row([(0, .8), (4, .8)], index=33, root="shared-landing-wall"),
    ])

    result = wall_assembly.decompose_cad_entity_roles(selected)

    structural = next(row for row in result["wall_rows"]
                      if row["entity_index"] == 33)
    context = next(row for row in result["context_rows"]
                   if row["entity_index"] == 33)
    assert structural["points"] == [(1.0, .8), (4.0, .8)]
    assert context["points"] == [(0.0, .8), (1.0, .8)]
    assert structural["partial_geometry_role"] == "structural_wall_remainder"
    assert context["partial_geometry_role"] == "stair_landing_context_fragment"
    assert result["summary"]["partial_context_fragment_count"] == 1
    proof = next(row for row in result["evidence"]
                 if row["entity_indexes"] == [33])
    assert proof["partial_context_splits"][0][
        "stair_interval_overlap_ratio"] == pytest.approx(1.0)


def _curve_rich_fixture_group(*, root, start_index, min_x, min_y):
    return [
        _group_row([(min_x, min_y), (min_x + .30, min_y)],
                   index=start_index, root=root, kind="ARC"),
        _group_row([(min_x + .30, min_y), (min_x + .30, min_y + .20)],
                   index=start_index + 1, root=root, kind="ARC"),
        _group_row([(min_x + .30, min_y + .20), (min_x, min_y + .20)],
                   index=start_index + 2, root=root),
        _group_row([(min_x, min_y + .20), (min_x, min_y)],
                   index=start_index + 3, root=root),
        _group_row([(min_x + .06, min_y + .04),
                    (min_x + .24, min_y + .16)],
                   index=start_index + 4, root=root),
        _group_row([(min_x + .06, min_y + .16),
                    (min_x + .24, min_y + .04)],
                   index=start_index + 5, root=root),
    ]


def test_equal_offset_l_counter_requires_curve_rich_fixture_in_each_arm():
    selected = [
        _group_row([(0, 0), (2, 0)], index=1, root="inner-horizontal"),
        _group_row([(2, 0), (2, 1.5)], index=2, root="inner-vertical"),
        # Unrelated real double-face wall must remain untouched.
        _group_row([(4, 0), (7, 0)], index=3, root="wall-a"),
        _group_row([(4, .2), (7, .2)], index=4, root="wall-b"),
    ]
    context = [
        _group_row([(0, -.6), (2.6, -.6)], index=20, root="outer-horizontal"),
        _group_row([(2.6, -.6), (2.6, 1.5)], index=21, root="outer-vertical"),
        *_curve_rich_fixture_group(
            root="fixture-bottom", start_index=30, min_x=.6, min_y=-.5),
        *_curve_rich_fixture_group(
            root="fixture-right", start_index=40, min_x=2.15, min_y=.5),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    assert {row["entity_index"] for row in result["context_rows"]} == {1, 2}
    assert {row["entity_index"] for row in result["wall_rows"]} == {3, 4}
    assert result["summary"]["reason_counts"][
        "fitted_counter_inner_edge_geometry"] == 2
    proof = next(row for row in result["evidence"]
                 if row["entity_indexes"] == [1])["counter_bands"][0]
    assert proof["evidence_kind"] == "curve_rich_fitted_counter_band_v1"
    assert proof["arm_offsets_m"] == pytest.approx([.6, .6])
    assert len(proof["fixture_groups"]) == 2
    assert all(row["curved_primitive_count"] == 2
               for row in proof["fixture_groups"])


def test_equal_offset_structural_l_without_two_independent_fixtures_stays_wall():
    selected = [
        _group_row([(0, 0), (2, 0)], index=1, root="inner-horizontal"),
        _group_row([(2, 0), (2, 1.5)], index=2, root="inner-vertical"),
    ]
    outer = [
        _group_row([(0, -.6), (2.6, -.6)], index=20, root="outer-horizontal"),
        _group_row([(2.6, -.6), (2.6, 1.5)], index=21, root="outer-vertical"),
    ]
    no_fixture = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=outer)
    assert {row["entity_index"] for row in no_fixture["wall_rows"]} == {1, 2}

    one_fixture = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=[
            *outer,
            *_curve_rich_fixture_group(
                root="only-one", start_index=30, min_x=.6, min_y=-.5),
        ])
    assert {row["entity_index"] for row in one_fixture["wall_rows"]} == {1, 2}
    assert "fitted_counter_inner_edge_geometry" not in one_fixture[
        "summary"]["reason_counts"]


def test_context_singleton_bridge_with_two_independent_wall_supports_is_recovered():
    selected = [
        _group_row([(0, 0), (2, 0)], index=1, root="bottom-support"),
        _group_row([(0, 2), (2, 2)], index=2, root="top-support"),
    ]
    bridge = _group_row([(2, 0), (2, 2)], index=20, root="omitted-edge")

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=[bridge])

    assert {row["entity_index"] for row in result["wall_rows"]} == {1, 2, 20}
    assert result["summary"]["supplemental_structural_entity_count"] == 1
    proof = next(row for row in result["evidence"]
                 if row["root_handle"] == "omitted-edge")
    assert proof["reason_codes"] == ["context_singleton_endpoint_bridge_geometry"]
    assert {row["entity_index"] for row in proof[
        "endpoint_bridge_evidence"]["endpoint_supports"]} == {1, 2}


def test_context_line_without_two_distinct_supports_is_not_promoted():
    selected = [
        _group_row([(0, 0), (2, 0)], index=1, root="only-support"),
    ]
    context = [
        _group_row([(2, 0), (2, 2)], index=20, root="one-ended"),
        _group_row([(1, 0), (1.2, 0)], index=21, root="short-crossbar"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    assert {row["entity_index"] for row in result["wall_rows"]} == {1}
    assert result["summary"]["supplemental_structural_entity_count"] == 0


def test_context_parallel_band_outside_wall_gauge_is_not_promoted_as_wall():
    selected = [
        _group_row([(0, 0), (0, 3)], index=1, root="left-support"),
        _group_row([(4, 0), (4, 3)], index=2, root="right-support"),
    ]
    context = [
        _group_row([(0, 2.5), (4, 2.5)], index=20, root="fitted-rail-a"),
        _group_row([(0, 1.8), (4, 1.8)], index=21, root="fitted-rail-b"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    assert {row["entity_index"] for row in result["wall_rows"]} == {1, 2}
    assert result["summary"]["supplemental_structural_entity_count"] == 0
    assert result["summary"]["supplemental_context_fixture_count"] == 2
    proofs = [row for row in result["evidence"]
              if row.get("reason_codes") == ["paired_context_nonwall_geometry"]]
    assert {row["entity_indexes"][0] for row in proofs} == {20, 21}
    assert {row["nonwall_context_evidence"]["nonwall_width_class"]
            for row in proofs} == {"too_wide_for_wall_band"}


def test_supported_wall_gauge_context_pair_remains_eligible_for_wall_assembly():
    selected = [
        _group_row([(0, 0), (0, 3)], index=1, root="left-support"),
        _group_row([(4, 0), (4, 3)], index=2, root="right-support"),
    ]
    context = [
        _group_row([(0, 2.1), (4, 2.1)], index=20, root="wall-face-a"),
        _group_row([(0, 1.9), (4, 1.9)], index=21, root="wall-face-b"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    assert {row["entity_index"] for row in result["wall_rows"]} == {1, 2, 20, 21}
    assert result["summary"]["supplemental_structural_entity_count"] == 2
    assert result["summary"]["supplemental_context_fixture_count"] == 0


def test_narrow_context_rails_with_mirrored_nonmeeting_diagonals_are_fixture():
    selected = [
        _group_row([(0, 0), (0, 3)], index=1, root="left-support"),
        _group_row([(2, 0), (2, 3)], index=2, root="right-support"),
        _group_row([(.5, 0), (.5, 3)], index=3, root="inner-left-support"),
        _group_row([(1.5, 0), (1.5, 3)], index=4, root="inner-right-support"),
    ]
    context = [
        _group_row([(0, 2.0), (2, 2.0)], index=20, root="thin-rail-a"),
        _group_row([(0, 1.97), (2, 1.97)], index=21, root="thin-rail-b"),
        _group_row([(0, 1.97), (.5, 1.47)], index=22, root="slope-a"),
        _group_row([(2, 1.97), (1.5, 1.47)], index=23, root="slope-b"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=context)

    assert not {20, 21, 22, 23}.intersection(
        row["entity_index"] for row in result["wall_rows"])
    fixture_proofs = [row["nonwall_context_evidence"] for row in result["evidence"]
                      if row.get("reason_codes") == ["paired_context_nonwall_geometry"]]
    assert len(fixture_proofs) == 4
    assert {row["method"] for row in fixture_proofs} == {
        "paired_context_nonwall_band_v1",
        "paired_context_trapezoidal_fixture_side_v1",
    }


def test_sparse_two_rail_window_requires_repeated_two_sided_wall_gap_support():
    selected = [
        _group_row([(.05, 0), (.05, 1)], index=1, root="left-face-low"),
        _group_row([(.05, 2), (.05, 3)], index=2, root="left-face-high"),
        _group_row([(.21, 0), (.21, 1)], index=3, root="right-face-low"),
        _group_row([(.21, 2), (.21, 3)], index=4, root="right-face-high"),
    ]
    sparse_frame = [
        _group_row([(.07, 1), (.07, 2)], index=20, root="sparse-rail-a"),
        _group_row([(.13, 1), (.13, 2)], index=21, root="sparse-rail-b"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=sparse_frame)

    candidate = next(row for row in result["raw_opening_candidates"]
                     if set(row["source_entity_indexes"]) == {20, 21})
    assert candidate["kind"] == "window"
    assert candidate["status"] == "review"
    assert "sparse_frame_unique_wall_gap_geometry" in candidate["reason_codes"]
    proof = candidate["evidence_geometry"]["sparse_frame_evidence"]
    assert proof["method"] == "sparse_parallel_frame_unique_wall_gap_v1"
    assert proof["negative_wall_face_support_count"] == 2
    assert proof["positive_wall_face_support_count"] == 2
    assert proof["supported_wall_band_width_m"] == pytest.approx(.16)


def test_sparse_parallel_furniture_without_opposite_wall_faces_is_not_window():
    selected = [
        _group_row([(0, 0), (0, 3)], index=1, root="one-wall-face"),
        _group_row([(4, 0), (4, 3)], index=2, root="far-wall-face"),
    ]
    furniture = [
        _group_row([(1, 1), (2, 1)], index=20, root="furniture-rail-a"),
        _group_row([(1, 1.1), (2, 1.1)], index=21, root="furniture-rail-b"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=furniture)

    assert not any(set(row["source_entity_indexes"]) == {20, 21}
                   for row in result["raw_opening_candidates"])


def test_sparse_exterior_window_can_use_repeated_one_sided_wall_face_support():
    selected = [
        _group_row([(0, 0), (0, 1)], index=1, root="outer-face-low"),
        _group_row([(0, 2), (0, 3)], index=2, root="outer-face-high"),
        # Establish a non-degenerate plan bbox without providing an opposite
        # parallel face near the sparse frame.
        _group_row([(5, 0), (5, 3)], index=3, root="far-structure"),
    ]
    sparse_frame = [
        _group_row([(.03, 1), (.03, 2)], index=20, root="exterior-rail-a"),
        _group_row([(.07, 1), (.07, 2)], index=21, root="exterior-rail-b"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        selected, context_rows=sparse_frame)

    candidate = next(row for row in result["raw_opening_candidates"]
                     if set(row["source_entity_indexes"]) == {20, 21})
    proof = candidate["evidence_geometry"]["sparse_frame_evidence"]
    assert proof["method"] == "sparse_parallel_frame_exterior_wall_face_v1"
    assert proof["single_side_wall_face_support_count"] == 2
    assert proof["selected_plan_boundary_distance_m"] <= .5
    assert candidate["evidence_geometry"]["opposite_wall_face_support"] is False
    assert "single_exterior_wall_face_support" in candidate["reason_codes"]


def _three_compact_cells(*, root, start_index, origin=(4.0, 4.0)):
    rows = []
    index = start_index
    for column in range(3):
        left = origin[0] + column * .32
        bottom = origin[1]
        for first, second in (
            ((left, bottom), (left + .24, bottom)),
            ((left + .24, bottom), (left + .24, bottom + .40)),
            ((left + .24, bottom + .40), (left, bottom + .40)),
            ((left, bottom + .40), (left, bottom)),
        ):
            rows.append(_group_row(
                [first, second], index=index, root=root, block="opaque"))
            index += 1
    return rows


def test_dense_compact_single_root_is_context_only_when_isolated_from_walls():
    wall = _group_row([(0, 0), (8, 0)], index=1, root="real-wall")
    isolated = _three_compact_cells(root="isolated-detail", start_index=20)

    result = wall_assembly.decompose_cad_entity_roles([wall, *isolated])

    assert {row["entity_index"] for row in result["wall_rows"]} == {1}
    assert {row["entity_index"] for row in result["context_rows"]} == set(
        range(20, 32))
    proof = next(row for row in result["evidence"]
                 if row["root_handle"] == "isolated-detail")
    assert len(proof["dense_fixture_groups"]) == 1
    proof = proof["dense_fixture_groups"][0]
    assert proof["polygonized_face_count"] == 3
    assert proof["external_source_line_distance_m"] >= .05

    touching = _three_compact_cells(
        root="touching-structure", start_index=40, origin=(4.0, 0.0))
    retained = wall_assembly.decompose_cad_entity_roles([wall, *touching])
    assert set(range(40, 52)).issubset({
        row["entity_index"] for row in retained["wall_rows"]})
    assert "compact_dense_isolated_root_geometry" not in retained[
        "summary"]["reason_counts"]


def _curve_marked_dense_furniture(*, include_circle=True, root="furniture"):
    rows = []
    next_index = 200
    if include_circle:
        circle = [
            (1 + .12 * math.cos(step * math.pi / 8),
             .6 + .12 * math.sin(step * math.pi / 8))
            for step in range(17)
        ]
        rows.append(_group_row(
            circle, index=next_index, root=root, kind="CIRCLE",
            closed=True, block="opaque"))
        next_index += 1
    rows.extend([
        _group_row([(.2, .15), (1, 1.05)], index=next_index,
                   root=root, block="opaque"),
        _group_row([(1.8, .15), (1, 1.05)], index=next_index + 1,
                   root=root, block="opaque"),
    ])
    next_index += 2
    for row_number in range(9):
        y = .1 + row_number * .125
        rows.append(_group_row(
            [(0, y), (2, y)], index=next_index,
            root=root, block="opaque"))
        next_index += 1
    for column_number in range(9):
        x = .1 + column_number * .225
        rows.append(_group_row(
            [(x, 0), (x, 1.2)], index=next_index,
            root=root, block="opaque"))
        next_index += 1
    return rows


def test_curve_marked_dense_insert_is_context_without_external_isolation():
    furniture = _curve_marked_dense_furniture()
    # An unrelated source crosses the fixture, so the older global isolation
    # proof deliberately cannot pass.
    crossing = _group_row([(-1, .6), (3, .6)], index=500, root="other-source")

    result = wall_assembly.decompose_cad_entity_roles([*furniture, crossing])

    fixture_indexes = {row["entity_index"] for row in furniture}
    assert fixture_indexes.issubset({
        row["entity_index"] for row in result["context_rows"]})
    assert result["summary"]["reason_counts"][
        "curve_marked_dense_furniture_geometry"] == len(furniture)
    proof = next(row for row in result["evidence"]
                 if row["root_handle"] == "furniture")["dense_fixture_groups"][0]
    assert proof["evidence_kind"] == "curve_marked_dense_furniture_v1"
    assert proof["circle_marker_count"] == 1
    assert proof["mirrored_diagonal_pair_count"] >= 1
    assert proof["external_source_line_distance_m"] == 0


def test_dense_insert_without_curve_marker_is_not_auto_classified_as_furniture():
    rows = _curve_marked_dense_furniture(
        include_circle=False, root="unproved-structure")

    result = wall_assembly.decompose_cad_entity_roles(rows)

    assert "curve_marked_dense_furniture_geometry" not in result[
        "summary"]["reason_counts"]


def _curve_marked_compact_fixture(*, include_circle=True,
                                  root="compact-fixture"):
    segments = [
        ((0, 0), (.6, 0)), ((.6, 0), (.6, .5)),
        ((.6, .5), (0, .5)), ((0, .5), (0, 0)),
        ((.1, 0), (.1, .5)), ((.2, 0), (.2, .5)),
        ((.3, 0), (.3, .5)), ((.4, 0), (.4, .5)),
        ((.5, 0), (.5, .5)),
        ((0, .1), (.6, .1)), ((0, .2), (.6, .2)),
        ((0, .3), (.6, .3)), ((0, .4), (.6, .4)),
        ((.05, .05), (.55, .45)), ((.05, .45), (.55, .05)),
        ((.15, .05), (.45, .45)), ((.15, .45), (.45, .05)),
    ]
    rows = [_group_row(
        [start, end], index=700 + index, root=root, block="opaque")
        for index, (start, end) in enumerate(segments)]
    if include_circle:
        circle = [
            (.3 + .08 * math.cos(step * math.pi / 8),
             .25 + .08 * math.sin(step * math.pi / 8))
            for step in range(17)
        ]
        rows.append(_group_row(
            circle, index=799, root=root, kind="CIRCLE", closed=True,
            block="opaque"))
    return rows


def test_curve_marked_compact_fixture_is_context_but_plain_wall_grid_is_not():
    fixture = _curve_marked_compact_fixture()
    crossing = _group_row(
        [(-1, .25), (2, .25)], index=900, root="external-wall")

    result = wall_assembly.decompose_cad_entity_roles(
        [*fixture, crossing])

    fixture_indexes = {row["entity_index"] for row in fixture}
    assert fixture_indexes.issubset({
        row["entity_index"] for row in result["context_rows"]})
    assert result["summary"]["reason_counts"][
        "curve_marked_compact_fixture_geometry"] == len(fixture)
    proof = next(row for row in result["evidence"]
                 if row["root_handle"] == "compact-fixture")[
                     "dense_fixture_groups"][0]
    assert proof["evidence_kind"] == "curve_marked_compact_fixture_v1"
    assert proof["primitive_count"] >= 16
    assert proof["circle_marker_count"] == 1

    plain_grid = _curve_marked_compact_fixture(
        include_circle=False, root="plain-wall-grid")
    retained = wall_assembly.decompose_cad_entity_roles(plain_grid)
    assert "curve_marked_compact_fixture_geometry" not in retained[
        "summary"]["reason_counts"]


def test_oversized_parallel_insert_frame_and_micro_cross_marks_are_not_walls():
    parallel = [
        _group_row([(0, 0), (0, 1.3)], index=810, root="wide-frame",
                   block="opaque"),
        _group_row([(.8, 0), (.8, 1.3)], index=811, root="wide-frame",
                   block="opaque"),
    ]
    micro = [
        _group_row([(2, 0), (2.04, .05)], index=820, root="micro-a"),
        _group_row([(2.04, 0), (2, .05)], index=821, root="micro-b"),
        _group_row([(2, .05), (2.04, .05)], index=822, root="micro-c"),
    ]

    result = wall_assembly.decompose_cad_entity_roles([*parallel, *micro])

    context_indexes = {row["entity_index"] for row in result["context_rows"]}
    assert {810, 811, 820, 821, 822}.issubset(context_indexes)
    assert result["summary"]["reason_counts"][
        "oversized_parallel_insert_frame_geometry"] == 2
    assert result["summary"]["reason_counts"][
        "micro_cross_marker_geometry"] == 3
    micro_proof = next(
        row["micro_cross_marker_evidence"] for row in result["evidence"]
        if row["root_handle"] == "micro-a")
    assert micro_proof["evidence_kind"] == "micro_cross_marker_v1"
    assert micro_proof["primitive_count"] == 3
    assert micro_proof["crossing_pairs"]

    ordinary = [
        _group_row([(4, 0), (4, 1.3)], index=830, root="wall-band",
                   block="opaque"),
        _group_row([(4.24, 0), (4.24, 1.3)], index=831, root="wall-band",
                   block="opaque"),
        _group_row([(6, 0), (6.1, 0)], index=840, root="tee-a"),
        _group_row([(6.05, 0), (6.05, .1)], index=841, root="tee-b"),
        _group_row([(6, .1), (6.1, .1)], index=842, root="tee-c"),
    ]
    retained = wall_assembly.decompose_cad_entity_roles(ordinary)
    assert "oversized_parallel_insert_frame_geometry" not in retained[
        "summary"]["reason_counts"]
    assert "micro_cross_marker_geometry" not in retained[
        "summary"]["reason_counts"]


def _compact_three_cell_insert(*, root="compact-appliance", start_index=600):
    segments = [
        ((0, 0), (.6, 0)), ((.6, 0), (.6, .6)),
        ((.6, .6), (0, .6)), ((0, .6), (0, 0)),
        ((0, .2), (.6, .2)), ((0, .4), (.6, .4)),
        ((.1, .05), (.1, .15)), ((.3, .05), (.3, .15)),
        ((.5, .05), (.5, .15)), ((.2, .45), (.2, .55)),
        ((.4, .45), (.4, .55)),
    ]
    return [
        _group_row([first, second], index=start_index + offset,
                   root=root, block="opaque")
        for offset, (first, second) in enumerate(segments)
    ]


def test_compact_multicell_insert_requires_nearby_fixture_semantic_anchor():
    appliance = _compact_three_cell_insert()
    crossing = _group_row([(-1, .3), (2, .3)], index=700, root="other-source")
    kitchen_anchor = [{
        "anchor_id": "kitchen-source", "point_m": [.9, .3],
        "semantic_profile": "kitchen", "source_handle": "TEXT-1",
    }]

    accepted = wall_assembly.decompose_cad_entity_roles(
        [*appliance, crossing], semantic_anchors=kitchen_anchor)

    appliance_indexes = {row["entity_index"] for row in appliance}
    assert appliance_indexes.issubset({
        row["entity_index"] for row in accepted["context_rows"]})
    assert accepted["summary"]["reason_counts"][
        "semantic_compact_multicell_fixture_geometry"] == len(appliance)
    proof = next(row for row in accepted["evidence"]
                 if row["root_handle"] == "compact-appliance")[
                     "dense_fixture_groups"][0]
    assert proof["evidence_kind"] == "semantic_compact_multicell_fixture_v1"
    assert proof["polygonized_face_count"] == 3
    assert proof["nearby_semantic_fixture_anchors"][0][
        "semantic_profile"] == "kitchen"

    no_anchor = wall_assembly.decompose_cad_entity_roles(
        [*appliance, crossing])
    bedroom_only = wall_assembly.decompose_cad_entity_roles(
        [*appliance, crossing], semantic_anchors=[{
            "anchor_id": "bed", "point_m": [.9, .3],
            "semantic_profile": "bedroom",
        }])
    assert "semantic_compact_multicell_fixture_geometry" not in no_anchor[
        "summary"]["reason_counts"]
    assert "semantic_compact_multicell_fixture_geometry" not in bedroom_only[
        "summary"]["reason_counts"]


def test_semantic_nested_building_envelope_excludes_dimension_scaffolding():
    rows = [
        _group_row([(0, 0), (10, 0), (10, 6), (6, 6),
                    (6, 8), (0, 8), (0, 0)],
                   index=1, root="outer-face", kind="LWPOLYLINE", closed=True),
        _group_row([(.3, .3), (9.7, .3), (9.7, 5.7), (5.7, 5.7),
                    (5.7, 7.7), (.3, 7.7), (.300000000002, .3)],
                   index=2, root="inner-face", kind="LWPOLYLINE", closed=False),
        _group_row([(2, 2), (8, 2)], index=3, root="real-wall-a"),
        _group_row([(2, 2.2), (8, 2.2)], index=4, root="real-wall-b"),
        _group_row([(-2, -2), (12, -2), (12, 12), (-2, 12), (-2, -2)],
                   index=10, root="plot-frame", kind="LWPOLYLINE", closed=True),
        _group_row([(-3, 4), (13, 4)], index=11, root="dimension-axis"),
        _group_row([(11, 1), (11.4, 1), (11.4, 1.4), (11, 1.4), (11, 1)],
                   index=12, root="outside-callout", kind="LWPOLYLINE", closed=True),
    ]
    anchors = [
        {"anchor_id": "bed", "point_m": [2, 6],
         "semantic_profile": "bedroom", "reference_profile": "bedroom"},
        {"anchor_id": "kitchen", "point_m": [7, 2.8],
         "semantic_profile": "kitchen", "reference_profile": "kitchen"},
        {"anchor_id": "living", "point_m": [5, 5],
         "semantic_profile": "living_room", "reference_profile": "living_room"},
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        rows, semantic_anchors=anchors)

    proof = result["semantic_building_envelope_evidence"]
    assert proof["status"] == "proved"
    assert proof["outer_entity_index"] == 1
    assert proof["inner_entity_index"] == 2
    assert proof["measured_wall_thickness_m"] == pytest.approx(.3)
    assert proof["semantic_profiles"] == ["bedroom", "kitchen", "living_room"]
    assert result["summary"]["semantic_building_envelope_filtered_entity_count"] == 3
    assert {row["entity_index"] for row in result["context_rows"]}.issuperset(
        {10, 11, 12})
    assert {3, 4}.issubset({
        row["entity_index"] for row in result["wall_rows"]})
    axis_evidence = next(
        row for row in result["evidence"] if row["root_handle"] == "dimension-axis")
    assert axis_evidence["reason_codes"] == [
        "dimension_scaffolding_crosses_semantic_envelope"]
    assert axis_evidence["semantic_building_envelope_filter_evidence"][
        "metrics"]["outside_endpoint_count"] == 2


def test_semantic_nested_building_envelope_fails_closed_without_room_proof():
    rows = [
        _group_row([(0, 0), (10, 0), (10, 8), (0, 8), (0, 0)],
                   index=1, root="outer-face", kind="LWPOLYLINE", closed=True),
        _group_row([(.3, .3), (9.7, .3), (9.7, 7.7), (.3, 7.7), (.3, .3)],
                   index=2, root="inner-face", kind="LWPOLYLINE", closed=True),
        _group_row([(-3, 4), (13, 4)], index=11, root="possible-wall"),
    ]
    insufficient_anchors = [
        {"anchor_id": "bed-1", "point_m": [2, 6],
         "semantic_profile": "bedroom"},
        {"anchor_id": "bed-2", "point_m": [7, 6],
         "semantic_profile": "bedroom"},
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        rows, semantic_anchors=insufficient_anchors)

    assert result["semantic_building_envelope_evidence"] == {}
    assert result["summary"]["semantic_building_envelope_status"] == "unproved"
    assert 11 in {row["entity_index"] for row in result["wall_rows"]}


def test_exterior_section_callout_root_cannot_leave_shallow_false_wall():
    rows = [
        _group_row([(0, 0), (10, 0), (10, 8), (0, 8), (0, 0)],
                   index=1, root="outer-face", kind="LWPOLYLINE", closed=True),
        _group_row([(.3, .3), (9.7, .3), (9.7, 7.7), (.3, 7.7), (.3, .3)],
                   index=2, root="inner-face", kind="LWPOLYLINE", closed=True),
        _group_row([(-1.0, 3.0), (.15, 3.0)],
                   index=20, root="section-callout", kind="LWPOLYLINE"),
        _group_row([(-.95, 2.85), (-.62, 3.0), (-.95, 3.15)],
                   index=21, root="section-callout", kind="LWPOLYLINE"),
        # A single shallow line without a non-collinear same-root companion
        # remains fail-closed wall evidence.
        _group_row([(-1.0, 5.0), (.15, 5.0)],
                   index=22, root="possible-exterior-return"),
    ]
    anchors = [
        {"anchor_id": "bed", "point_m": [2, 6],
         "semantic_profile": "bedroom", "reference_profile": "bedroom"},
        {"anchor_id": "kitchen", "point_m": [7, 2],
         "semantic_profile": "kitchen", "reference_profile": "kitchen"},
        {"anchor_id": "living", "point_m": [5, 5],
         "semantic_profile": "living_room", "reference_profile": "living_room"},
    ]

    result = wall_assembly.decompose_cad_entity_roles(
        rows, semantic_anchors=anchors)

    context_indexes = {row["entity_index"] for row in result["context_rows"]}
    assert {20, 21}.issubset(context_indexes)
    assert 22 in {row["entity_index"] for row in result["wall_rows"]}
    proof = next(row for row in result["evidence"]
                 if row["root_handle"] == "section-callout")
    assert proof["role"] == "context_fixture"
    assert proof["reason_codes"] == [
        "exterior_section_callout_shallow_envelope_contact"]
    assert proof["semantic_building_envelope_filter_evidence"][
        "method"] == "cad_exterior_section_callout_root_filter_v1"


def test_raw_opening_binding_requires_parallel_supported_canonical_wall_axis():
    candidate = {
        "candidate_id": "door-1", "kind": "door", "status": "review", "confidence": .96,
        "width_m": .9, "center_cad_m": [101.45, -40.0],
        "axis_segment_cad_m": [[101.0, -40.0], [101.9, -40.0]],
        "reason_codes": ["circular_swing_arc"], "source_handles": ["arc", "leaf"],
        "source_entity_indexes": [1, 2], "evidence_geometry": {},
    }
    assembly = {
        "id": "assembly-1", "review_status": "accepted", "thickness_m": .2,
        "opening_axis": [[0, 0], [4, 0]], "source_entity_handles": ["face-a", "face-b"],
    }
    bound = wall_assembly.bind_raw_geometry_openings(
        [candidate], [assembly], origin_x=100, origin_z=-40)[0]

    assert bound["status"] == "accepted"
    assert bound["wall_assembly_id"] == "assembly-1"
    assert bound["offset_m"] == pytest.approx(1.0)
    assert bound["width_m"] == pytest.approx(.9)
    assert bound["wall_source_handles"] == ["face-a", "face-b"]
    assert "canonical_wall_axis_bound" in bound["reason_codes"]
    assert "opening_wall_assembly_unresolved" not in bound["reason_codes"]

    perpendicular = copy.deepcopy(candidate)
    perpendicular["axis_segment_cad_m"] = [[101.0, -40.0], [101.0, -39.1]]
    perpendicular["center_cad_m"] = [101.0, -39.55]
    unresolved = wall_assembly.bind_raw_geometry_openings(
        [perpendicular], [assembly], origin_x=100, origin_z=-40)[0]
    assert unresolved["status"] == "review"
    assert "opening_wall_assembly_unresolved" in unresolved["reason_codes"]


def test_raw_opening_binding_can_select_a_canonical_alternate_swing_axis():
    wall = wall_assembly.build_wall_assemblies(_valid_pair())[0]
    candidate = {
        "candidate_id": "open-door", "kind": "door", "status": "review",
        "confidence": .96, "width_m": .9,
        "center_cad_m": [1.0, .55],
        "axis_segment_cad_m": [[1.0, .1], [1.0, 1.0]],
        "reason_codes": ["circular_swing_arc"],
        "source_handles": ["arc", "leaf"],
        "evidence_geometry": {"axis_candidates": [{
            "axis_segment_cad_m": [[1.0, .1], [1.9, .1]],
        }]},
    }

    result = wall_assembly.bind_raw_geometry_openings([candidate], [wall])[0]

    assert result["status"] == "accepted"
    assert result["axis_segment_cad_m"] == [[1.0, .1], [1.9, .1]]
    assert result["center_cad_m"] == [1.45, .1]
    assert "alternate_swing_axis_selected_at_canonical_binding" in result["reason_codes"]


def test_circular_swing_door_cannot_bind_to_a_clipped_short_wall_interval():
    candidate = {
        "candidate_id": "door-clipped", "kind": "door", "status": "review",
        "confidence": .96, "width_m": .9,
        "axis_segment_cad_m": [[.8, 0], [1.7, 0]],
        "reason_codes": ["circular_swing_arc", "radial_door_leaf"],
        "source_handles": ["arc", "leaf"], "evidence_geometry": {},
    }
    short_wall = {
        "id": "short-wall", "review_status": "accepted", "thickness_m": .2,
        "opening_axis": [[0, 0], [1.5, 0]], "source_entity_handles": ["face"],
    }

    result = wall_assembly.bind_raw_geometry_openings(
        [candidate], [short_wall])[0]

    assert result["status"] == "review"
    assert "opening_wall_assembly_unresolved" in result["reason_codes"]


def test_proven_opening_gap_stitches_two_collinear_equal_thickness_wall_hosts():
    left = wall_assembly.build_wall_assemblies([
        _row([(0, 0), (1, 0)], handle="left-a"),
        _row([(0, .2), (1, .2)], handle="left-b"),
    ], id_prefix="left-")[0]
    right = wall_assembly.build_wall_assemblies([
        _row([(1.9, 0), (4, 0)], handle="right-a"),
        _row([(1.9, .2), (4, .2)], handle="right-b"),
    ], id_prefix="right-")[0]
    candidate = {
        "candidate_id": "door-gap", "kind": "door", "status": "review",
        "width_m": .84, "axis_segment_cad_m": [[1.03, 0], [1.87, 0]],
        "reason_codes": ["circular_swing_arc", "wall_network_supported"],
    }

    stitched = wall_assembly.stitch_wall_assemblies_across_openings(
        [left, right], [candidate])

    assert len(stitched) == 1
    host = stitched[0]
    assert host["source_representation"] == "opening_host_stitch"
    assert host["review_status"] == "accepted"
    assert host["centerline"] == [[0.0, .1], [4.0, .1]]
    assert host["thickness_m"] == .2
    assert set(host["source_entity_handles"]) == {
        "left-a", "left-b", "right-a", "right-b"}
    bound = wall_assembly.bind_raw_geometry_openings([candidate], stitched)[0]
    assert bound["status"] == "accepted"
    assert bound["wall_assembly_id"] == host["id"]


def test_window_frame_extends_one_host_only_when_continuous_source_face_covers_axis():
    source_wall = wall_assembly.build_wall_assemblies([
        _row([(0, 0), (3.2, 0)], handle="outer-face"),
        _row([(0, .2), (3.2, .2)], handle="inner-face"),
    ])[0]
    # Simulate the production pairing interval ending inside a multi-panel
    # window while retaining the untouched source face in provenance.
    source_wall.update(
        centerline=[[0, .1], [1.5, .1]], opening_axis=[[0, .1], [1.5, .1]],
        start={"x": 0, "z": .1}, end={"x": 1.5, "z": .1}, length_m=1.5,
        footprint_polygon=[[0, 0], [1.5, 0], [1.5, .2], [0, .2]],
    )
    candidate = {
        "candidate_id": "window-overlay", "kind": "window", "status": "review",
        "width_m": 1.6, "axis_segment_cad_m": [[.8, .1], [2.4, .1]],
        "source_handles": ["rail-a", "rail-b", "jamb-a", "jamb-b"],
        "reason_codes": ["parallel_frame_rails", "opposite_wall_face_support"],
        "evidence_geometry": {
            "grouping_method": "loose_maximal_parallel_rail_pair",
            "opposite_wall_face_support": True,
            "seed_rail_separation_m": .2,
        },
    }

    stitched = wall_assembly.stitch_wall_assemblies_across_openings(
        [source_wall], [candidate])

    assert len(stitched) == 1
    host = stitched[0]
    assert host["source_representation"] == "window_frame_host_extension"
    assert host["centerline"] == [[0.0, .1], [3.2, .1]]
    proof = host["window_frame_host_evidence"]
    assert proof["source_wall_assembly_id"] == source_wall["id"]
    assert proof["source_face_handle"] in {"outer-face", "inner-face"}
    bound = wall_assembly.bind_raw_geometry_openings([candidate], stitched)[0]
    assert bound["status"] == "accepted"
    assert bound["wall_assembly_id"] == host["id"]


def test_overlapping_invalid_closed_and_open_dense_curves_are_context_fixture():
    invalid_closed = [
        (.30 * math.sin(angle),
         .40 * math.sin(angle) * math.cos(angle))
        for angle in [2 * math.pi * value / 126 for value in range(127)]
    ]
    invalid_closed.append(invalid_closed[0])
    open_companion = [
        (.32 * math.cos(angle), .24 * math.sin(angle))
        for angle in [2 * math.pi * value / 100 for value in range(94)]
    ]
    selected = [
        _group_row(invalid_closed, index=200, root="direct-closed",
                   kind="POLYLINE", closed=True),
        _group_row(open_companion, index=201, root="direct-open",
                   kind="POLYLINE", closed=False),
        _group_row([(1, 0), (4, 0)], index=202, root="wall"),
    ]

    result = wall_assembly.decompose_cad_entity_roles(selected)

    wall_indexes = {row["entity_index"] for row in result["wall_rows"]}
    assert wall_indexes == {202}
    assert result["summary"]["reason_counts"][
        "overlapping_invalid_closed_and_open_curved_fixture"] == 2
    evidence = next(
        row for row in result["evidence"]
        if row["root_handle"] == "direct-closed")
    proof = next(
        row for row in evidence["dense_fixture_groups"]
        if row["evidence_kind"] == "direct_dense_curved_fixture_pair_v1")
    assert proof["entity_indexes"] == [200, 201]
    assert {member["closed"] for member in proof["members"]} == {False, True}
    assert any(member["invalid_closed"] for member in proof["members"])


def test_single_invalid_dense_curve_is_not_enough_to_suppress_wall_evidence():
    invalid_closed = [
        (.30 * math.sin(angle),
         .40 * math.sin(angle) * math.cos(angle))
        for angle in [2 * math.pi * value / 126 for value in range(127)]
    ]
    invalid_closed.append(invalid_closed[0])

    result = wall_assembly.decompose_cad_entity_roles([
        _group_row(invalid_closed, index=200, root="direct-closed",
                   kind="POLYLINE", closed=True),
    ])

    assert [row["entity_index"] for row in result["wall_rows"]] == [200]
    assert "overlapping_invalid_closed_and_open_curved_fixture" not in (
        result["summary"]["reason_counts"])
