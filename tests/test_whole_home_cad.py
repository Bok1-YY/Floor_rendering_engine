# -*- coding: utf-8 -*-
import asyncio
import copy
import io
import json
import math
import os
import subprocess
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image
from pydantic import ValidationError

from Floor_engine_server import (
    routes_whole_home, server_schemas, whole_home_cad, whole_home_engine, whole_home_learning,
)


ASCII_DXF = b"0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1032\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"


def test_detached_oversized_site_perimeter_is_not_a_production_wall():
    from shapely.geometry import LineString, Polygon

    occupied = Polygon([(0, 0), (6, 0), (6, 8), (0, 8)])
    house_band = occupied.buffer(.2, join_style=2).difference(occupied)
    site_band = LineString([
        (-3, -4), (-3, 12), (9, 12), (9, -4),
    ]).buffer(.12, cap_style=2, join_style=2)

    def footprint(identifier, polygon):
        return {
            'id': identifier, 'source_representation': 'global_wall_footprint',
            'review_status': 'needs_review',
            'points': [{'x': x, 'z': z}
                       for x, z in list(polygon.exterior.coords)[:-1]],
            'interior_rings': [[{'x': x, 'z': z}
                                for x, z in list(ring.coords)[:-1]]
                               for ring in polygon.interiors],
        }

    assemblies = [{
        'id': 'house-wall', 'source_representation': 'paired_faces',
        'review_status': 'accepted', 'centerline': [[0, .1], [6, .1]],
        'footprint_polygon': [[0, 0], [6, 0], [6, .2], [0, .2]],
        'thickness_m': .2, 'length_m': 6.0,
        'source_entity_handles': ['house-a', 'house-b'],
    }, {
        'id': 'site-wall', 'source_representation': 'paired_faces',
        'review_status': 'accepted', 'centerline': [[-3, -4], [-3, 12]],
        'footprint_polygon': [[-3.12, -4], [-2.88, -4],
                              [-2.88, 12], [-3.12, 12]],
        'thickness_m': .24, 'length_m': 16.0,
        'source_entity_handles': ['site-a', 'site-b'],
    }]
    summary = {
        'status': 'proved', 'wall_component_count': 2,
        'wall_footprint_count': 2, 'wall_area_m2': 20.0,
        'decision_basis': [],
    }

    result_assemblies, result_footprints, result_summary = \
        whole_home_cad._exclude_detached_site_boundary_components(
            assemblies,
            [footprint('house', house_band), footprint('site', site_band)],
            [occupied], summary, origin_x=0, origin_z=0)

    assert [row['id'] for row in result_footprints] == ['house']
    house = next(row for row in result_assemblies
                 if row['id'] == 'house-wall')
    assert house['review_status'] == 'accepted'
    site = next(row for row in result_assemblies if row['id'] == 'site-wall')
    assert site['review_status'] == 'rejected'
    assert site['source_representation'] == 'detached_site_boundary_evidence'
    assert not site.get('centerline') and not site.get('footprint_polygon')
    proof = site['detached_site_boundary_evidence']
    assert proof['component_to_physical_space_distance_m'] >= .35
    assert proof['maximum_component_to_occupied_span_ratio'] >= 1.25
    assert result_summary['detached_site_boundary_component_count'] == 1
    assert result_summary['wall_component_count'] == 1


def test_oversized_site_perimeter_touching_house_uses_source_wall_clip():
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union

    occupied = Polygon([(0, 0), (6, 0), (6, 8), (0, 8)])
    house_band = occupied.buffer(.2, join_style=2).difference(occupied)
    site_lines = [
        LineString([(-3, -4), (-3, 12)]),
        LineString([(-3, 12), (9, 12)]),
        LineString([(9, 12), (9, -4)]),
    ]
    site_bands = [line.buffer(.12, cap_style=2, join_style=2)
                  for line in site_lines]
    # This bridge reproduces a global union where the site outline touches
    # the house even though each long source site wall is spatially separate
    # from every physical room.
    bridge = LineString([(9, 12), (6.1, 8.1)]).buffer(
        .12, cap_style=2, join_style=2)
    coalesced = unary_union([house_band, bridge, *site_bands]).buffer(0)
    tiny_inside = LineString([(2.9, 4), (3.1, 4)]).buffer(
        .05, cap_style=2)

    def footprint(identifier, polygon):
        return {
            'id': identifier, 'source_representation': 'global_wall_footprint',
            'review_status': 'needs_review',
            'points': [{'x': x, 'z': z}
                       for x, z in list(polygon.exterior.coords)[:-1]],
            'interior_rings': [[{'x': x, 'z': z}
                                for x, z in list(ring.coords)[:-1]]
                               for ring in polygon.interiors],
        }

    assemblies = [{
        'id': 'house-wall', 'source_representation': 'paired_faces',
        'review_status': 'accepted', 'centerline': [[0, .1], [6, .1]],
        'footprint_polygon': [[0, 0], [6, 0], [6, .2], [0, .2]],
        'thickness_m': .2, 'length_m': 6.0,
        'source_entity_handles': ['house-a', 'house-b'],
    }]
    for number, (line, band) in enumerate(zip(site_lines, site_bands), 1):
        assemblies.append({
            'id': f'site-wall-{number}',
            'source_representation': 'paired_faces',
            'review_status': 'accepted',
            'centerline': [list(line.coords[0]), list(line.coords[-1])],
            'footprint_polygon': [list(point)
                                  for point in list(band.exterior.coords)[:-1]],
            'thickness_m': .24, 'length_m': line.length,
            'source_entity_handles': [f'site-{number}-a', f'site-{number}-b'],
        })
    compact_line = LineString([(-2.8, 12), (-2.5, 12)])
    compact_band = compact_line.buffer(.04, cap_style=2, join_style=2)
    assemblies.append({
        'id': 'site-compact-detail',
        'source_representation': 'paired_faces',
        'review_status': 'accepted',
        'centerline': [list(compact_line.coords[0]),
                       list(compact_line.coords[-1])],
        'footprint_polygon': [list(point)
                              for point in list(compact_band.exterior.coords)[:-1]],
        'thickness_m': .08, 'length_m': compact_line.length,
        'source_entity_handles': ['site-compact-a', 'site-compact-b'],
    })

    result_assemblies, result_footprints, result_summary = \
        whole_home_cad._exclude_detached_site_boundary_components(
            assemblies,
            [footprint('coalesced', coalesced),
             footprint('tiny-inside', tiny_inside)],
            [occupied], {
                'status': 'proved', 'wall_component_count': 2,
                'wall_footprint_count': 2, 'wall_area_m2': 30.0,
                'decision_basis': [],
            }, origin_x=0, origin_z=0)

    assert next(row for row in result_assemblies
                if row['id'] == 'house-wall')['review_status'] == 'accepted'
    rejected = [row for row in result_assemblies
                if row['id'].startswith('site-wall-')]
    assert len(rejected) == 3
    assert {row['review_status'] for row in rejected} == {'rejected'}
    assert all(row['detached_site_boundary_evidence']['method']
               == 'cad_oversized_coalesced_site_boundary_clip_v1'
               for row in rejected)
    assert result_summary['detached_site_boundary_assembly_count'] == 3
    compact = next(row for row in result_assemblies
                   if row['id'] == 'site-compact-detail')
    assert compact['review_status'] == 'rejected'
    assert compact['source_representation'] \
        == 'nonspace_projected_geometry_evidence'
    assert not compact.get('centerline') and not compact.get('footprint_polygon')
    assert compact['nonspace_projected_geometry_evidence']['method'] \
        == 'cad_nonspace_geometry_within_oversized_site_plan_v1'
    assert result_summary['nonspace_projected_geometry_assembly_count'] == 1
    assert all(Polygon(
        [(point['x'], point['z']) for point in row['points']],
        [[(point['x'], point['z']) for point in ring]
         for ring in row.get('interior_rings') or []],
    ).distance(occupied) <= .35 + 1e-9 for row in result_footprints)


def test_multiview_projected_detail_trial_allows_bounded_dense_source_scope(
        monkeypatch):
    from shapely.geometry import Polygon

    selected_rows = [{"entity_index": index, "points": [(0, 0), (1, 0)]}
                     for index in range(1000)]
    unresolved = []
    for decision_index in range(207):
        index = decision_index if decision_index < 190 else decision_index - 190
        representation = "human_confirmed_ambiguous"
        if decision_index == 0:
            representation = "closed_footprint"
        elif decision_index == 1:
            representation = "invalid_closed_footprint"
        unresolved.append({
            "id": f"pending-{decision_index}",
            "source_representation": representation,
            "review_status": "needs_review",
            "source_entities": [{"entity_index": index}],
        })
    space = Polygon([(0, 0), (4, 0), (4, 3), (0, 3)])
    topology = {
        "_space_polygons": [space],
        "wall_footprints": [],
        "summary": {"status": "proved", "wall_area_m2": 10.0},
    }
    accepted_trial = [{
        "id": "accepted", "source_representation": "paired_faces",
        "review_status": "accepted", "footprint_polygon": [
            [0, 0], [1, 0], [1, .1], [0, .1]],
        "centerline": [[0, .05], [1, .05]], "thickness_m": .1,
    }]
    monkeypatch.setattr(
        whole_home_cad, "build_wall_assemblies",
        lambda *args, **kwargs: copy.deepcopy(accepted_trial))
    monkeypatch.setattr(
        whole_home_cad, "build_global_wall_topology",
        lambda *args, **kwargs: copy.deepcopy(topology))
    monkeypatch.setattr(
        whole_home_cad, "_resolve_wall_evidence_with_global_topology",
        lambda assemblies, *args, **kwargs: assemblies)

    result = whole_home_cad._prune_topology_invariant_projected_detail(
        selected_rows, unresolved, topology, {
            "status": "proved",
            "authority_proof_method": "cad_multi_view_orthographic_plan_view_v1",
        })

    assert result["status"] == "proved"
    assert result["excluded_entity_count"] == 190
    assert result["excluded_entity_ratio"] == pytest.approx(.19)
    assert result["unresolved_assembly_decision_count"] == 207
    assert result["duplicate_segment_decision_count"] == 17
    assert result["thresholds"]["maximum_excluded_entity_count"] == 250
    assert result["thresholds"]["maximum_excluded_entity_ratio"] == .20
    assert len(result["_terminal_evidence_assemblies"]) == 190
    assert {row["source_representation"] for row in
            result["_terminal_evidence_assemblies"]} == {
            "projected_detail_evidence"}


def test_projected_detail_trial_compares_physical_rooms_not_fixture_voids(
        monkeypatch):
    from shapely.geometry import Polygon

    selected_rows = [
        {"entity_index": index, "points": [(0, 0), (1, 0)]}
        for index in range(10)
    ]
    unresolved = [{
        "id": "pending-fixture-boundary",
        "source_representation": "human_confirmed_ambiguous",
        "review_status": "needs_review",
        "source_entities": [{"entity_index": 0}],
    }]
    room = Polygon([(0, 0), (4, 0), (4, 3), (0, 3)])
    fixture_void = Polygon([(5, 0), (5.4, 0), (5.4, .4), (5, .4)])
    original_topology = {
        "_space_polygons": [room, fixture_void],
        "wall_footprints": [],
        "summary": {"status": "proved", "wall_area_m2": 10.0},
    }
    trial_topology = {
        "_space_polygons": [room],
        "wall_footprints": [],
        "summary": {"status": "proved", "wall_area_m2": 10.0},
    }
    accepted_trial = [{
        "id": "accepted", "source_representation": "paired_faces",
        "review_status": "accepted", "footprint_polygon": [
            [0, 0], [1, 0], [1, .1], [0, .1]],
        "centerline": [[0, .05], [1, .05]], "thickness_m": .1,
    }]
    monkeypatch.setattr(
        whole_home_cad, "build_wall_assemblies",
        lambda *args, **kwargs: copy.deepcopy(accepted_trial))
    monkeypatch.setattr(
        whole_home_cad, "build_global_wall_topology",
        lambda *args, **kwargs: copy.deepcopy(trial_topology))
    monkeypatch.setattr(
        whole_home_cad, "_resolve_wall_evidence_with_global_topology",
        lambda assemblies, *args, **kwargs: assemblies)

    result = whole_home_cad._prune_topology_invariant_projected_detail(
        selected_rows, unresolved, original_topology,
        {"status": "proved", "authority_proof_method": "single_root"},
        semantic_anchors=[{
            "anchor_id": "room-label", "source_kind": "text",
            "semantic_profile": "living_room", "point_m": [2, 1.5],
        }],
    )

    assert result["status"] == "proved"
    assert result["space_comparison_scope"] == "classified_physical_spaces"
    assert result["original_raw_topology_space_count"] == 2
    assert result["trial_raw_topology_space_count"] == 1
    assert result["original_space_count"] == 1
    assert result["trial_space_count"] == 1
    assert result["space_union_iou"] == 1.0


def test_projected_detail_counterfactual_partition_keeps_room_closing_source(
        monkeypatch):
    from shapely.geometry import Polygon

    selected_rows = [
        {"entity_index": index, "points": [(0, 0), (1, 0)]}
        for index in range(30)
    ]
    pending = [{
        "id": f"pending-{index}",
        "source_representation": "human_confirmed_ambiguous",
        "review_status": "needs_review",
        "source_entity_handles": [f"H{index}"],
        "source_entities": [{
            "entity_index": index, "handle": f"H{index}",
            "source_handle": f"H{index}", "root_handle": f"H{index}",
        }],
    } for index in range(4)]
    exposed_dependency = {
        "id": "pending-exposed-dependency",
        "source_representation": "human_confirmed_ambiguous",
        "review_status": "needs_review",
        "source_entity_handles": ["H5"],
        "source_entities": [{
            "entity_index": 5, "handle": "H5",
            "source_handle": "H5", "root_handle": "H5",
        }],
    }
    room = Polygon([(0, 0), (4, 0), (4, 3), (0, 3)])
    changed_room = Polygon([(0, 0), (2, 0), (2, 3), (0, 3)])
    original_topology = {
        "_space_polygons": [room], "wall_footprints": [],
        "summary": {"status": "proved", "wall_area_m2": 10.0},
    }

    def build_trial(rows, **_kwargs):
        indexes = {int(row["entity_index"]) for row in rows}
        trial = [copy.deepcopy(pending[index])
                 for index in range(4) if index in indexes]
        if 3 not in indexes:
            trial.append(copy.deepcopy(exposed_dependency))
        return trial

    def build_topology(rows, **_kwargs):
        indexes = {int(row["entity_index"]) for row in rows}
        topology = copy.deepcopy(original_topology)
        topology["_space_polygons"] = [room if 2 in indexes else changed_room]
        return topology

    monkeypatch.setattr(whole_home_cad, "build_wall_assemblies", build_trial)
    monkeypatch.setattr(
        whole_home_cad, "build_global_wall_topology", build_topology)
    monkeypatch.setattr(
        whole_home_cad, "_resolve_wall_evidence_with_global_topology",
        lambda assemblies, *args, **kwargs: assemblies)

    result = whole_home_cad._partition_topology_invariant_projected_detail(
        selected_rows, pending, original_topology,
        {"status": "proved", "authority_proof_method": "single_root"},
        semantic_anchors=[{
            "anchor_id": "room-label", "source_kind": "text",
            "semantic_profile": "living_room", "point_m": [1, 1],
        }],
    )

    assert result["status"] == "proved"
    assert result["safe_source_entity_indexes"] == [0, 1]
    assert result["essential_topology_source_entity_indexes"] == [2, 3]
    assert result["trial_unresolved_wall_assembly_count"] == 0
    assert result["remaining_unresolved_source_entity_indexes"] == []
    representations = [row["source_representation"] for row in
                       result["_terminal_evidence_assemblies"]]
    assert representations.count("projected_detail_evidence") == 2
    assert representations.count(
        "projected_topology_boundary_evidence") == 1
    assert representations.count(
        "projected_geometry_dependency_evidence") == 1
    boundary = next(
        row for row in result["_terminal_evidence_assemblies"]
        if row["source_representation"]
        == "projected_topology_boundary_evidence")
    proof = boundary["projected_topology_boundary_evidence"]
    assert proof["reference_physical_space_count"] == 1
    assert proof["counterfactual_physical_space_count"] == 1
    assert proof["counterfactual_space_union_iou"] == .5
    dependency = next(
        row for row in result["_terminal_evidence_assemblies"]
        if row["source_representation"]
        == "projected_geometry_dependency_evidence")
    dependency_proof = dependency["projected_geometry_dependency_evidence"]
    assert dependency_proof[
        "counterfactual_trial_unresolved_wall_assembly_count"] >= 1
    assert dependency_proof[
        "counterfactual_new_unresolved_source_indexes"] == [5]


def test_multiview_projected_detail_trial_can_prove_only_micro_detail_subset(
        monkeypatch):
    from shapely.geometry import Polygon

    selected_rows = [{"entity_index": index, "points": [(0, 0), (1, 0)]}
                     for index in range(1000)]
    selected_rows[200]["points"] = [(0, 0), (.05, 0)]
    unresolved = [{
        "id": f"pending-{index}",
        "source_representation": "human_confirmed_ambiguous",
        "review_status": "needs_review",
        "source_entities": [{"entity_index": index}],
        "global_topology_resolution_audit": {
            "source_length_m": .05 if index < 120 else .5,
        },
    } for index in range(150)]
    remaining = copy.deepcopy(unresolved[120:])
    accepted_micro = {
        "id": "accepted-micro",
        "source_representation": "centerline_with_measured_thickness",
        "review_status": "accepted",
        "source_entities": [{"entity_index": 200}],
    }
    space = Polygon([(0, 0), (4, 0), (4, 3), (0, 3)])
    topology = {
        "_space_polygons": [space], "wall_footprints": [],
        "summary": {"status": "proved", "wall_area_m2": 10.0},
    }

    def build_trial(rows, **_kwargs):
        indexes = {int(row["entity_index"]) for row in rows}
        trial = copy.deepcopy(remaining)
        if 200 in indexes:
            exposed = copy.deepcopy(accepted_micro)
            exposed.update(
                source_representation="human_confirmed_ambiguous",
                review_status="needs_review",
            )
            trial.append(exposed)
        return trial

    monkeypatch.setattr(
        whole_home_cad, "build_wall_assemblies",
        build_trial)
    monkeypatch.setattr(
        whole_home_cad, "build_global_wall_topology",
        lambda *args, **kwargs: copy.deepcopy(topology))
    monkeypatch.setattr(
        whole_home_cad, "_resolve_wall_evidence_with_global_topology",
        lambda assemblies, *args, **kwargs: assemblies)

    result = whole_home_cad._prune_topology_invariant_projected_detail(
        selected_rows, [*unresolved, accepted_micro], topology, {
            "status": "proved",
            "authority_proof_method": "cad_multi_view_orthographic_plan_view_v1",
        })

    assert result["status"] == "proved"
    assert result["pruning_mode"] == "sub_75mm_micro_detail_partial_scope"
    assert result["excluded_entity_count"] == 121
    assert result["trial_unresolved_wall_assembly_count"] == 30
    assert result["trial_unresolved_removed_source_count"] == 0
    assert result["trial_new_unresolved_source_count"] == 0
    assert result["remaining_unresolved_source_entity_indexes"] == list(
        range(120, 150))
    assert len(result["_terminal_evidence_assemblies"]) == 121
    assert result["terminal_source_entity_count"] == 121
    assert len(result["_trial_assemblies"]) == 30
    assert result["dependency_closure_retry_count"] == 1
    assert result["dependency_closure_source_indexes"] == [200]


def test_single_projection_projected_detail_trial_keeps_narrow_scope_limit():
    selected_rows = [{"entity_index": index, "points": [(0, 0), (1, 0)]}
                     for index in range(1000)]
    unresolved = [{
        "id": f"pending-{index}",
        "source_representation": "human_confirmed_ambiguous",
        "review_status": "needs_review",
        "source_entities": [{"entity_index": index}],
    } for index in range(190)]
    result = whole_home_cad._prune_topology_invariant_projected_detail(
        selected_rows, unresolved,
        {"_space_polygons": [], "summary": {"status": "proved"}},
        {"status": "proved", "authority_proof_method": "single_root"},
    )

    assert result == {
        "schema_version": 1,
        "method": "cad_projected_detail_topology_invariance_v1",
        "status": "unresolved",
        "reason": "excluded_detail_scope_too_large",
    }


def test_labeled_terminal_open_connection_requires_geometry_semantics_and_topology_delta():
    def row(handle, points):
        return {
            "points": points,
            "cad_provenance": {
                "source_handle": handle, "root_handle": handle,
            },
        }

    x = 4.7
    rows = [
        row("outer-a", [(0, 0), (6, 0), (6, 4), (0, 4), (0, 0)]),
        row("outer-b", [(.24, .24), (5.76, .24), (5.76, 3.76),
                         (.24, 3.76), (.24, .24)]),
        row("v1", [(x, .24), (x, 1.0)]),
        row("v2", [(x + .12, .24), (x + .12, 1.0)]),
        row("h1", [(x, 1.6), (5.76, 1.6)]),
        row("h2", [(x, 1.72), (5.76, 1.72)]),
    ]
    assemblies = [{
        "id": "terminal", "source_representation": "paired_faces",
        "review_status": "accepted",
        "centerline": [[x + .06, .24], [x + .06, 1.0]],
        "footprint_polygon": [[x, .24], [x + .12, .24],
                              [x + .12, 1.0], [x, 1.0]],
        "thickness_m": .12, "height_m": 2.8,
        "source_entity_handles": ["v1", "v2"],
        "source_entities": [{"entity_index": 2}, {"entity_index": 3}],
    }, {
        "id": "transverse", "source_representation": "paired_faces",
        "review_status": "accepted",
        "centerline": [[x, 1.66], [5.76, 1.66]],
        "footprint_polygon": [[x, 1.6], [5.76, 1.6],
                              [5.76, 1.72], [x, 1.72]],
        "thickness_m": .12, "height_m": 2.8,
        "source_entity_handles": ["h1", "h2"],
        "source_entities": [{"entity_index": 4}, {"entity_index": 5}],
    }]
    windows = [{
        "candidate_id": f"window-{index}", "kind": "window",
        "status": "accepted", "width_m": 1.2,
        "axis_segment_cad_m": [[1.0, y], [2.2, y]],
        "source_handles": ["outer-a"],
    } for index, y in enumerate((0.0, .24, 4.0), 1)]
    anchors = [{
        "anchor_id": "store", "semantic_profile": "storage",
        "point_m": [5.2, .9],
        "cad_provenance": {"source_handle": "text-store"},
    }, {
        "anchor_id": "kitchen", "semantic_profile": "kitchen",
        "point_m": [5.2, 2.5],
        "cad_provenance": {"source_handle": "text-kitchen"},
    }]

    candidates, resolved, evidence = (
        whole_home_cad._infer_labeled_terminal_open_connections(
            rows, assemblies, windows, anchors, origin_x=0, origin_z=0))

    assert evidence["status"] == "proved"
    assert evidence["proved_count"] == 1
    assert len(candidates) == 4
    assert candidates[-1]["kind"] == "open_connection"
    assert candidates[-1]["width_m"] == pytest.approx(.6)
    assert candidates[-1]["axis_segment_cad_m"] == [[4.76, 1.0], [4.76, 1.66]]
    assert len(resolved) == 3
    host = resolved[-1]
    assert host["source_representation"] == "terminal_open_connection_host"
    assert host["terminal_open_connection_evidence"][
        "topology_space_count_delta"] == 1
    assert host["terminal_open_connection_evidence"][
        "closed_space_semantic_anchor_ids"] == ["store"]

    # Geometry alone is not allowed to guess which of many unlabeled wall-end
    # gaps is a room portal.  Removing either side of the semantic pair fails
    # closed and leaves the input arrays unchanged.
    no_candidate, no_host, unresolved = (
        whole_home_cad._infer_labeled_terminal_open_connections(
            rows, assemblies, windows, anchors[:1], origin_x=0, origin_z=0))
    assert unresolved["status"] == "unresolved"
    assert unresolved["proved_count"] == 0
    assert no_candidate == windows
    assert no_host == assemblies


def test_attached_exterior_space_requires_two_nested_boundary_chains():
    def row(index, first, second):
        return {
            "entity_index": index, "entity_type": "LINE",
            "points": [first, second],
            "bbox": [min(first[0], second[0]), min(first[1], second[1]),
                     max(first[0], second[0]), max(first[1], second[1])],
            "cad_provenance": {
                "source_handle": f"attached-{index}",
                "root_handle": f"attached-{index}",
            },
        }

    geometry = [
        row(0, (0, 0), (10, 0)), row(1, (10, 0), (10, 8)),
        row(2, (10, 8), (0, 8)), row(3, (0, 8), (0, 0)),
        row(4, (0, 2), (-1, 2)), row(5, (-1, 2), (-1, 6)),
        row(6, (-1, 6), (0, 6)),
        row(7, (0, 2.1), (-.9, 2.1)), row(8, (-.9, 2.1), (-.9, 5.9)),
        row(9, (-.9, 5.9), (0, 5.9)),
    ]

    proof = whole_home_cad._prove_attached_exterior_double_boundary(
        geometry, [0, 1, 2, 3], [0, 0, 10, 8])

    assert proof["status"] == "proved"
    assert proof["promoted_entity_indexes"] == [4, 5, 6, 7, 8, 9]
    assert proof["expanded_candidate_bbox_m"] == [-1.0, 0.0, 10.0, 8.0]
    assert len(proof["spaces"]) == 1
    space = proof["spaces"][0]
    assert space["attachment_side"] == "left"
    assert space["measured_boundary_separation_m"] == pytest.approx(.1)
    assert space["area_m2"] == pytest.approx(4.0)

    unproved = whole_home_cad._prove_attached_exterior_double_boundary(
        geometry[:7], [0, 1, 2, 3], [0, 0, 10, 8])
    assert unproved["status"] == "unresolved"
    assert unproved["promoted_entity_indexes"] == []


def test_complete_window_frame_binds_when_wall_mask_correctly_contains_a_gap():
    footprints = [{
        "points": [
            {"x": 0, "z": -.12}, {"x": 1, "z": -.12},
            {"x": 1, "z": .12}, {"x": 0, "z": .12},
        ],
    }, {
        "points": [
            {"x": 2, "z": -.12}, {"x": 3, "z": -.12},
            {"x": 3, "z": .12}, {"x": 2, "z": .12},
        ],
    }]
    candidate = {
        "candidate_id": "window-gap", "kind": "window", "status": "review",
        "width_m": 1.0, "axis_segment_cad_m": [[101, -20], [102, -20]],
        "source_root_handle": "window-root",
        "source_handles": ["rail-a", "rail-b", "jamb-a", "jamb-b"],
        "source_entity_indexes": [10, 11, 12, 13],
        "reason_codes": ["opening_wall_assembly_unresolved"],
        "evidence_geometry": {
            "grouping_method": "loose_maximal_parallel_rail_pair",
            "opposite_wall_face_support": True,
            "seed_rail_separation_m": .24,
            "signed_wall_face_offsets_m": [-.11, -.11, .13],
            "wall_endpoint_support_distance_m": [.10, .10],
            "interior_wall_overlap_ratio": 1.0,
            "long_rail_count": 4, "cross_member_count": 2,
        },
    }

    candidates, assemblies = whole_home_cad._bind_openings_to_global_wall_footprints(
        [candidate], [], footprints, origin_x=100, origin_z=-20)

    assert candidates[0]["status"] == "accepted"
    assert candidates[0]["wall_assembly_id"].startswith(
        "cad_wall_frame_opening_host_")
    assert "opening_wall_assembly_unresolved" not in candidates[0]["reason_codes"]
    assert assemblies[0]["source_representation"] == "frame_geometry_opening_host"
    assert assemblies[0]["thickness_m"] == pytest.approx(.24)


def test_sparse_window_uses_measured_wall_face_span_not_frame_rail_spacing():
    footprints = [{
        "points": [
            {"x": 0, "z": -.12}, {"x": 1, "z": -.12},
            {"x": 1, "z": .12}, {"x": 0, "z": .12},
        ],
    }, {
        "points": [
            {"x": 2, "z": -.12}, {"x": 3, "z": -.12},
            {"x": 3, "z": .12}, {"x": 2, "z": .12},
        ],
    }]
    candidate = {
        "candidate_id": "sparse-window-gap", "kind": "window",
        "status": "review", "width_m": 1.0,
        "axis_segment_cad_m": [[101, -.01], [102, -.01]],
        "source_handles": ["rail-a", "rail-b"],
        "source_entity_indexes": [10, 11],
        "reason_codes": ["opening_wall_assembly_unresolved"],
        "evidence_geometry": {
            "grouping_method": "loose_maximal_parallel_rail_pair",
            "opposite_wall_face_support": True,
            "seed_rail_separation_m": .06,
            "signed_wall_face_offsets_m": [-.11, -.11, .13, .13],
            "wall_endpoint_support_distance_m": [.04, .04],
            "interior_wall_overlap_ratio": 0.0,
            "long_rail_count": 2, "cross_member_count": 0,
            "sparse_frame_evidence": {
                "method": "sparse_parallel_frame_unique_wall_gap_v1",
                "source_row_count": 2,
                "negative_wall_face_support_count": 2,
                "positive_wall_face_support_count": 2,
                "supported_wall_band_width_m": .24,
                "wall_band_midpoint_offset_m": .01,
            },
        },
    }

    candidates, assemblies = whole_home_cad._bind_openings_to_global_wall_footprints(
        [candidate], [], footprints, origin_x=100, origin_z=0)

    assert candidates[0]["status"] == "accepted"
    host = assemblies[0]
    assert host["source_representation"] == "frame_geometry_opening_host"
    assert host["thickness_m"] == pytest.approx(.24)
    assert host["thickness_m"] != pytest.approx(.06)
    assert host["thickness_source"] == "cad_sparse_frame_supported_wall_face_span"
    proof = host["frame_geometry_opening_evidence"]
    assert proof["method"] == "cad_sparse_window_frame_wall_face_host_v1"
    assert proof["opening_axis_cad_m"] == [[101.0, 0.0], [102.0, 0.0]]


def test_single_root_expanded_window_frame_uses_entity_rows_as_source_evidence():
    footprints = [{
        "points": [
            {"x": 0, "z": -.075}, {"x": 1, "z": -.075},
            {"x": 1, "z": .075}, {"x": 0, "z": .075},
        ],
    }, {
        "points": [
            {"x": 2, "z": -.075}, {"x": 3, "z": -.075},
            {"x": 3, "z": .075}, {"x": 2, "z": .075},
        ],
    }]
    candidate = {
        "candidate_id": "root-window-gap", "kind": "window",
        "status": "review", "width_m": 1.0,
        "axis_segment_cad_m": [[101, 0], [102, 0]],
        "source_root_handle": "window-insert",
        "source_handles": ["window-insert"],
        "source_entity_indexes": [10, 11, 12, 13, 14],
        "reason_codes": ["opening_wall_assembly_unresolved"],
        "evidence_geometry": {
            "grouping_method": "source_root",
            "opposite_wall_face_support": True,
            "short_span_m": .15,
            "signed_wall_face_offsets_m": [-.075, -.075, .075, .075],
            "wall_endpoint_support_distance_m": [.075, .075],
            "interior_wall_overlap_ratio": 0.0,
            "long_rail_count": 6, "cross_member_count": 6,
        },
    }

    candidates, assemblies = whole_home_cad._bind_openings_to_global_wall_footprints(
        [candidate], [], footprints, origin_x=100, origin_z=0)

    assert candidates[0]["status"] == "accepted"
    host = assemblies[0]
    assert host["source_representation"] == "frame_geometry_opening_host"
    assert host["thickness_m"] == pytest.approx(.15)
    assert host["thickness_source"] == "cad_root_frame_supported_wall_face_span"
    proof = host["frame_geometry_opening_evidence"]
    assert proof["method"] == "cad_root_window_frame_wall_face_host_v1"
    assert proof["source_row_count"] == 5

    insufficient = copy.deepcopy(candidate)
    insufficient["status"] = "review"
    insufficient["source_entity_indexes"] = [10, 11, 12]
    insufficient_candidates, insufficient_assemblies = (
        whole_home_cad._bind_openings_to_global_wall_footprints(
            [insufficient], [], footprints, origin_x=100, origin_z=0))
    assert insufficient_candidates[0]["status"] == "review"
    assert insufficient_assemblies == []


def test_repeated_collinear_window_inherits_unique_reference_wall_thickness():
    footprints = [{
        "points": [
            {"x": -.15, "z": 1.26}, {"x": .15, "z": 1.26},
            {"x": .15, "z": 1.36}, {"x": -.15, "z": 1.36},
        ],
    }, {
        "points": [
            {"x": -.15, "z": 1.79}, {"x": .15, "z": 1.79},
            {"x": .15, "z": 1.89}, {"x": -.15, "z": 1.89},
        ],
    }]
    reference_host = {
        "id": "reference-wall", "source_representation": "paired_faces",
        "review_status": "accepted", "centerline": [[.05, 0], [.05, .533]],
        "footprint_polygon": [[-.07, 0], [.17, 0], [.17, .533], [-.07, .533]],
        "thickness_m": .24, "height_m": 2.8,
        "source_entity_handles": ["wall-face-a", "wall-face-b"],
    }
    reference = {
        "candidate_id": "window-reference", "kind": "window", "status": "accepted",
        "width_m": .533, "axis_segment_cad_m": [[0, 0], [0, .533]],
        "wall_assembly_id": "reference-wall",
        "source_handles": ["r1", "r2", "r3", "r4", "r5", "r6"],
        "evidence_geometry": {
            "grouping_method": "loose_maximal_parallel_rail_pair",
            "long_rail_count": 3, "cross_member_count": 2,
            "seed_rail_separation_m": .13,
        },
    }
    current = {
        "candidate_id": "window-current", "kind": "window", "status": "review",
        "width_m": .533, "axis_segment_cad_m": [[0, 1.311], [0, 1.844]],
        "source_handles": ["c1", "c2", "c3", "c4", "c5", "c6"],
        "source_entity_indexes": [10, 11, 12, 13, 14, 15],
        "reason_codes": ["opening_wall_assembly_unresolved"],
        "evidence_geometry": {
            "grouping_method": "loose_maximal_parallel_rail_pair",
            "opposite_wall_face_support": True,
            "long_rail_count": 3, "cross_member_count": 2,
            "seed_rail_separation_m": .13,
            "signed_wall_face_offsets_m": [-.16, .06, .28],
            "wall_endpoint_support_distance_m": [.16, .16],
            "interior_wall_overlap_ratio": 0.0,
        },
    }

    candidates, assemblies = whole_home_cad._bind_openings_to_global_wall_footprints(
        [reference, current], [reference_host], footprints, origin_x=0, origin_z=0)

    bound = next(row for row in candidates
                 if row["candidate_id"] == "window-current")
    assert bound["status"] == "accepted"
    host = next(row for row in assemblies if row.get("id") == bound["wall_assembly_id"])
    assert host["source_representation"] == "repeated_window_frame_opening_host"
    assert host["thickness_m"] == pytest.approx(.24)
    proof = host["repeated_window_frame_opening_evidence"]
    assert proof["reference_candidate_id"] == "window-reference"
    assert proof["axis_interval_gap_m"] == pytest.approx(.778)


def test_door_swing_selects_the_only_axis_whose_two_jambs_have_matching_walls():
    footprints = [{
        "points": [
            {"x": 0, "z": -.1}, {"x": 1, "z": -.1},
            {"x": 1, "z": .1}, {"x": 0, "z": .1},
        ],
    }, {
        "points": [
            {"x": 1.8, "z": -.1}, {"x": 3, "z": -.1},
            {"x": 3, "z": .1}, {"x": 1.8, "z": .1},
        ],
    }]
    candidate = {
        "candidate_id": "door-gap", "kind": "door", "status": "review",
        "width_m": .8,
        # Open-leaf direction: its remote endpoint does not reach a jamb.
        "axis_segment_cad_m": [[101, -20], [101, -19.2]],
        "source_root_handle": "door-arc", "source_handles": ["door-arc"],
        "source_entity_indexes": [20],
        "reason_codes": ["circular_swing_arc", "radial_door_leaf",
                         "wall_network_supported", "opening_wall_assembly_unresolved"],
        "evidence_geometry": {"axis_candidates": [{
            "axis_segment_cad_m": [[101, -20], [101.8, -20]],
        }]},
    }

    candidates, assemblies = whole_home_cad._bind_openings_to_global_wall_footprints(
        [candidate], [], footprints, origin_x=100, origin_z=-20)

    assert candidates[0]["status"] == "accepted"
    assert candidates[0]["axis_segment_cad_m"] == [[101.0, -20.0], [101.8, -20.0]]
    host = assemblies[0]
    assert host["source_representation"] == "door_swing_geometry_opening_host"
    assert host["thickness_m"] == pytest.approx(.2)
    assert host["door_swing_geometry_opening_evidence"]["viable_axis_count"] == 1


def test_accepted_door_with_undersized_local_host_rebinds_to_global_jamb_host():
    footprints = [{
        "points": [
            {"x": 0, "z": -.1}, {"x": 1, "z": -.1},
            {"x": 1, "z": .1}, {"x": 0, "z": .1},
        ],
    }, {
        "points": [
            {"x": 1.8, "z": -.1}, {"x": 3, "z": -.1},
            {"x": 3, "z": .1}, {"x": 1.8, "z": .1},
        ],
    }]
    short_host = {
        "id": "short-local-jamb", "source_representation": "paired_faces",
        "review_status": "accepted", "centerline": [[1.0, 0], [1.15, 0]],
        "footprint_polygon": [[1.0, -.1], [1.15, -.1],
                              [1.15, .1], [1.0, .1]],
        "thickness_m": .2, "height_m": 2.8,
        "source_entity_handles": ["short-a", "short-b"],
    }
    candidate = {
        "candidate_id": "accepted-door-short-host", "kind": "door",
        "status": "accepted", "wall_assembly_id": "short-local-jamb",
        "width_m": .8,
        # The local pass bound this open-leaf axis to a short jamb fragment.
        "axis_segment_cad_m": [[101, -20], [101, -19.2]],
        "source_root_handle": "door-arc", "source_handles": ["door-arc"],
        "source_entity_indexes": [20],
        "reason_codes": ["circular_swing_arc", "radial_door_leaf",
                         "wall_network_supported", "canonical_wall_axis_bound"],
        "evidence_geometry": {"axis_candidates": [{
            "axis_segment_cad_m": [[101, -20], [101.8, -20]],
        }]},
    }

    candidates, assemblies = whole_home_cad._bind_openings_to_global_wall_footprints(
        [candidate], [short_host], footprints, origin_x=100, origin_z=-20)

    bound = candidates[0]
    assert bound["status"] == "accepted"
    assert bound["wall_assembly_id"] != "short-local-jamb"
    assert "local_opening_host_capacity_insufficient" in bound["reason_codes"]
    superseded = bound["evidence_geometry"]["superseded_local_opening_host"]
    assert superseded["host_centerline_length_m"] == pytest.approx(.15)
    assert superseded["opening_width_m"] == pytest.approx(.8)
    host = next(row for row in assemblies
                if row.get("id") == bound["wall_assembly_id"])
    assert host["source_representation"] == "door_swing_geometry_opening_host"
    assert host["length_m"] == pytest.approx(.8)


def test_nearly_coincident_door_axis_projections_form_one_physical_choice():
    footprints = [{
        "points": [
            {"x": 0, "z": -.1}, {"x": 1, "z": -.1},
            {"x": 1, "z": .1}, {"x": 0, "z": .1},
        ],
    }, {
        "points": [
            {"x": 1.8, "z": -.1}, {"x": 3, "z": -.1},
            {"x": 3, "z": .1}, {"x": 1.8, "z": .1},
        ],
    }]
    candidate = {
        "candidate_id": "door-duplicate-projections", "kind": "door",
        "status": "review", "width_m": .8,
        "axis_segment_cad_m": [[101, -20], [101, -19.2]],
        "source_root_handle": "door-arc", "source_handles": ["door-arc"],
        "source_entity_indexes": [20],
        "reason_codes": ["circular_swing_arc", "radial_door_leaf",
                         "wall_network_supported",
                         "opening_wall_assembly_unresolved"],
        "evidence_geometry": {"axis_candidates": [{
            "axis_segment_cad_m": [[101, -20], [101.8, -20]],
        }, {
            "axis_segment_cad_m": [[101, -19.985], [101.8, -19.985]],
        }]},
    }

    candidates, assemblies = whole_home_cad._bind_openings_to_global_wall_footprints(
        [candidate], [], footprints, origin_x=100, origin_z=-20)

    assert candidates[0]["status"] == "accepted"
    proof = assemblies[0]["door_swing_geometry_opening_evidence"]
    assert proof["raw_viable_axis_count"] == 2
    assert proof["viable_axis_count"] == 1
    assert proof["selected_axis_equivalent_projection_count"] == 2


def test_same_partition_door_axes_up_to_120mm_form_one_physical_choice():
    footprints = [{
        "points": [
            {"x": 0, "z": -.2}, {"x": 1, "z": -.2},
            {"x": 1, "z": .3}, {"x": 0, "z": .3},
        ],
    }, {
        "points": [
            {"x": 1.8, "z": -.2}, {"x": 3, "z": -.2},
            {"x": 3, "z": .3}, {"x": 1.8, "z": .3},
        ],
    }]
    candidate = {
        "candidate_id": "door-dense-partition-projections", "kind": "door",
        "status": "review", "width_m": .8,
        "axis_segment_cad_m": [[101, -20], [101, -19.2]],
        "source_root_handle": "door-arc", "source_handles": ["door-arc"],
        "source_entity_indexes": [20],
        "reason_codes": ["circular_swing_arc", "radial_door_leaf",
                         "wall_network_supported",
                         "opening_wall_assembly_unresolved"],
        "evidence_geometry": {"axis_candidates": [{
            "axis_segment_cad_m": [[101, -20], [101.8, -20]],
        }, {
            "axis_segment_cad_m": [[101, -19.945], [101.8, -19.945]],
        }, {
            "axis_segment_cad_m": [[101, -19.89], [101.8, -19.89]],
        }]},
    }

    candidates, assemblies = whole_home_cad._bind_openings_to_global_wall_footprints(
        [candidate], [], footprints, origin_x=100, origin_z=-20)

    assert candidates[0]["status"] == "accepted"
    proof = assemblies[0]["door_swing_geometry_opening_evidence"]
    assert proof["raw_viable_axis_count"] == 3
    assert proof["viable_axis_count"] == 1
    assert proof["selected_axis_equivalent_projection_count"] == 3
    assert proof["axis_equivalence_hausdorff_tolerance_m"] == pytest.approx(.12)


def test_parallel_leaf_without_arc_uses_jamb_proof_not_false_wall_mask_axis():
    footprints = [{
        "points": [
            {"x": 0, "z": -.1}, {"x": 1, "z": -.1},
            {"x": 1, "z": .1}, {"x": 0, "z": .1},
        ],
    }, {
        "points": [
            {"x": 1.9, "z": -.1}, {"x": 3, "z": -.1},
            {"x": 3, "z": .1}, {"x": 1.9, "z": .1},
        ],
    }]
    leaf_evidence = {
        "method": "cad_parallel_door_leaf_without_arc_v1",
        "source_row_count": 3, "parallel_rail_count": 3,
        "leaf_length_m": .9, "leaf_length_spread_m": .005,
        "leaf_angle_spread_deg": .2,
        "hinge_endpoint_cluster_radius_m": .02,
        "free_endpoint_cluster_radius_m": .02,
        "hinge_wall_distance_m": .1,
        "free_endpoint_wall_distance_m": .65,
        "selected_wall_face_separation_m": .2,
        "axis_candidates": [
            {"axis_segment_cad_m": [[101, 0], [101.9, 0]]},
            # This alternate lies inside the left wall.  Generic wall-mask
            # coverage would prefer it, but it cannot prove two jambs.
            {"axis_segment_cad_m": [[100.1, 0], [101, 0]]},
        ],
    }
    candidate = {
        "candidate_id": "door-leaf-gap", "kind": "door", "status": "review",
        "width_m": .9, "axis_segment_cad_m": [[101, 0], [101.9, 0]],
        "source_handles": ["leaf-a", "leaf-b", "leaf-c"],
        "source_entity_indexes": [20, 21, 22],
        "reason_codes": [
            "parallel_door_leaf_rails", "hinge_endpoint_wall_supported",
            "swing_leaf_without_arc", "wall_network_supported",
            "opening_wall_assembly_unresolved",
        ],
        "evidence_geometry": leaf_evidence,
    }

    candidates, assemblies = whole_home_cad._bind_openings_to_global_wall_footprints(
        [candidate], [], footprints, origin_x=100, origin_z=0)

    assert candidates[0]["status"] == "accepted"
    assert candidates[0]["axis_segment_cad_m"] == [[101.0, 0.0], [101.9, 0.0]]
    host = assemblies[0]
    assert host["source_representation"] == "door_swing_geometry_opening_host"
    proof = host["door_swing_geometry_opening_evidence"]
    assert proof["method"] == "cad_door_leaf_unique_jamb_host_v1"
    assert proof["parallel_leaf_without_arc_evidence"][
        "parallel_rail_count"] == 3
    assert proof["viable_axis_count"] == 1


def test_inset_arc_leaf_uses_measured_wall_pair_and_transverse_jamb_projection():
    footprints = [{
        "points": [
            {"x": 0, "z": 0}, {"x": 1.05, "z": 0},
            {"x": 1.05, "z": .15}, {"x": 0, "z": .15},
        ],
    }, {
        "points": [
            {"x": 2.05, "z": -.3}, {"x": 2.35, "z": -.3},
            {"x": 2.35, "z": .5}, {"x": 2.05, "z": .5},
        ],
    }]
    axis = [[102.05, .075], [101.018, .075]]
    projection = {
        "axis_segment_cad_m": axis,
        "projection_method":
            "cad_arc_leaf_wall_pair_transverse_jamb_projection_v1",
        "wall_face_entity_indexes": [1, 2],
        "transverse_jamb_entity_index": 5,
        "wall_face_source_handles": ["face-a", "face-b", "jamb"],
        "wall_face_separation_m": .15,
        "hinge_to_wall_centerline_offset_m": .035,
        "transverse_jamb_snap_distance_m": .05,
        "transverse_jamb_angle_difference_deg": 90.0,
    }
    candidate = {
        "candidate_id": "inset-arc-door", "kind": "door", "status": "review",
        "width_m": 1.032, "axis_segment_cad_m": axis,
        "source_handles": ["arc", "leaf"], "source_entity_indexes": [20, 21],
        "reason_codes": ["circular_swing_arc", "radial_door_leaf",
                         "wall_network_supported", "opening_wall_assembly_unresolved"],
        "evidence_geometry": {
            "arc_radius_m": 1.1,
            "axis_candidates": [projection],
        },
    }

    candidates, assemblies = whole_home_cad._bind_openings_to_global_wall_footprints(
        [candidate], [], footprints, origin_x=100, origin_z=0)

    assert candidates[0]["status"] == "accepted"
    host = assemblies[0]
    proof = host["door_swing_geometry_opening_evidence"]
    assert proof["method"] == \
        "cad_door_swing_wall_pair_transverse_jamb_host_v1"
    assert host["thickness_m"] == pytest.approx(.15)
    assert host["thickness_source"] == \
        "cad_arc_projected_wall_face_pair_thickness"
    assert proof["projected_arc_transverse_jamb_evidence"][
        "transverse_jamb_entity_index"] == 5


def test_corner_adjacent_door_uses_two_unique_terminal_wall_supports():
    footprints = [{
        "points": [
            {"x": .88, "z": -.6}, {"x": .98, "z": -.6},
            {"x": .98, "z": 0}, {"x": .88, "z": 0},
        ],
    }, {
        "points": [
            {"x": 1.82, "z": -.1}, {"x": 2.5, "z": -.1},
            {"x": 2.5, "z": .1}, {"x": 1.82, "z": .1},
        ],
    }]
    assemblies = [{
        "id": "left-corner-wall", "source_representation": "paired_faces",
        "review_status": "accepted", "centerline": [[.93, -.6], [.93, 0]],
        "footprint_polygon": [[.88, -.6], [.98, -.6], [.98, 0], [.88, 0]],
        "thickness_m": .2, "height_m": 2.8,
        "source_entity_handles": ["left-a", "left-b"],
    }, {
        "id": "right-wall", "source_representation": "paired_faces",
        "review_status": "accepted", "centerline": [[1.82, 0], [2.5, 0]],
        "footprint_polygon": [[1.82, -.1], [2.5, -.1], [2.5, .1], [1.82, .1]],
        "thickness_m": .2, "height_m": 2.8,
        "source_entity_handles": ["right-a", "right-b"],
    }]
    candidate = {
        "candidate_id": "door-corner", "kind": "door", "status": "review",
        "width_m": .8,
        "axis_segment_cad_m": [[1, 0], [1, .8]],
        "source_root_handle": "door-arc", "source_handles": ["door-arc"],
        "source_entity_indexes": [20],
        "reason_codes": ["circular_swing_arc", "radial_door_leaf",
                         "wall_network_supported", "opening_wall_assembly_unresolved"],
        "evidence_geometry": {"axis_candidates": [{
            "axis_segment_cad_m": [[1, 0], [1.8, 0]],
        }]},
    }

    candidates, result = whole_home_cad._bind_openings_to_global_wall_footprints(
        [candidate], assemblies, footprints, origin_x=0, origin_z=0)

    assert candidates[0]["status"] == "accepted"
    host = next(row for row in result
                if row.get("id") == candidates[0]["wall_assembly_id"])
    proof = host["door_swing_geometry_opening_evidence"]
    assert proof["method"] == \
        "cad_door_swing_unique_terminal_wall_support_v1"
    assert {row["wall_assembly_id"] for row in proof["terminal_wall_supports"]} == {
        "left-corner-wall", "right-wall"}
    assert host["thickness_source"] == "cad_door_terminal_wall_support_thickness"


def test_proved_global_wall_consumes_only_stable_physical_space_boundary_evidence():
    from shapely.geometry import Polygon

    pending_wall = {
        "id": "pending-boundary", "source_representation": "human_confirmed_ambiguous",
        "review_status": "needs_review", "source_centerline": [[.2, .2], [3.8, .2]],
        "source_entity_handles": ["wall-face"], "reason_codes": [
            "cad_wall_representation_unresolved"],
        "production_blockers": ["cad_wall_representation_unresolved"],
        "cad_provenance": {},
    }
    inside_furniture = {
        **copy.deepcopy(pending_wall), "id": "pending-furniture",
        "source_centerline": [[.2, 1.0], [3.8, 1.0]],
        "source_entity_handles": ["furniture-line"],
    }
    footprints = [{
        "id": "global-wall", "review_status": "accepted",
        "points": [
            {"x": 0, "z": 0}, {"x": 4, "z": 0},
            {"x": 4, "z": .2}, {"x": 0, "z": .2},
        ],
        "interior_rings": [],
    }]
    spaces = [Polygon([(0, .2), (4, .2), (4, 3), (0, 3)])]
    summary = {"method": "cad-global-wall-topology-v1", "status": "proved"}

    resolved = whole_home_cad._resolve_wall_evidence_with_global_topology(
        [pending_wall, inside_furniture], footprints, spaces, summary,
        origin_x=0, origin_z=0)

    assert resolved[0]["source_representation"] == "global_topology_evidence"
    assert resolved[0]["review_status"] == "rejected"
    proof = resolved[0]["global_topology_evidence"]
    assert proof["source_wall_mask_coverage_ratio"] == 1.0
    assert [row["width_m"] for row in proof["cross_sections"]] == [.2, .2, .2]
    assert resolved[1]["review_status"] == "needs_review"


def test_proved_semantic_envelope_contours_are_audit_only_after_global_topology():
    from shapely.geometry import Polygon

    assemblies = [{
        "id": "outer-contour", "source_representation": "closed_footprint",
        "review_status": "needs_review", "source_entity_handles": ["outer"],
        "reason_codes": ["closed_perimeter_wall_role_unproven"],
        "production_blockers": ["cad_closed_perimeter_wall_role_unproven"],
    }, {
        "id": "unrelated-contour", "source_representation": "closed_footprint",
        "review_status": "needs_review", "source_entity_handles": ["other"],
        "reason_codes": ["closed_perimeter_wall_role_unproven"],
        "production_blockers": ["cad_closed_perimeter_wall_role_unproven"],
    }]
    footprints = [{
        "id": "global-wall", "review_status": "accepted",
        "points": [{"x": 0, "z": 0}, {"x": 4, "z": 0},
                   {"x": 4, "z": .2}, {"x": 0, "z": .2}],
        "interior_rings": [],
    }]
    envelope = {
        "method": "cad_semantic_nested_building_envelope_v1",
        "status": "proved", "outer_source_handles": ["outer"],
        "inner_source_handles": ["inner"], "semantic_anchor_count": 4,
        "semantic_profiles": ["bedroom", "kitchen"],
    }

    resolved = whole_home_cad._resolve_wall_evidence_with_global_topology(
        assemblies, footprints,
        [Polygon([(0, .2), (4, .2), (4, 3), (0, 3)])],
        {"method": "cad-global-wall-topology-v1", "status": "proved"},
        origin_x=0, origin_z=0, building_envelope_evidence=envelope)

    assert resolved[0]["review_status"] == "rejected"
    assert resolved[0]["source_representation"] == (
        "global_topology_envelope_evidence")
    assert resolved[0]["production_blockers"] == []
    assert resolved[0]["global_topology_envelope_evidence"][
        "source_entity_handles"] == ["outer"]
    assert resolved[1]["review_status"] == "needs_review"


def test_proved_global_corner_chain_requires_two_unique_local_wall_supports():
    from shapely.geometry import Polygon

    def pending(identifier, handle, centerline):
        return {
            "id": identifier,
            "source_representation": "human_confirmed_ambiguous",
            "review_status": "needs_review",
            "source_centerline": centerline,
            "source_entity_handles": [handle],
            "reason_codes": ["cad_wall_representation_unresolved"],
            "production_blockers": ["cad_wall_representation_unresolved"],
            "cad_provenance": {},
        }

    def accepted(identifier, points, centerline=None):
        return {
            "id": identifier,
            "source_representation": "paired_faces",
            "review_status": "accepted",
            "footprint_polygon": points,
            "centerline": centerline or [points[0], points[1]],
            "thickness_m": .2,
            "source_entity_handles": [f"{identifier}-face-a", f"{identifier}-face-b"],
        }

    # The two short source runs form one unambiguous L-shaped cap.  Their free
    # endpoints terminate in different accepted local walls.  A deliberately
    # broad global mask proves coverage but cannot by itself assign either run
    # a wall thickness; this is provenance-only junction evidence.
    assemblies = [
        accepted("left-wall", [[0, -.1], [.2, -.1], [.2, .1], [0, .1]]),
        accepted("upper-wall", [[.6, .4], [.8, .4], [.8, .8], [.6, .8]],
                 [[.7, .4], [.7, .8]]),
        pending("corner-horizontal", "corner-h", [[.2, 0], [.7, 0]]),
        pending("corner-vertical", "corner-v", [[.7, 0], [.7, .4]]),
    ]
    footprints = [{
        "id": "proved-global-wall", "review_status": "accepted",
        "points": [
            {"x": 0, "z": -.1}, {"x": .8, "z": -.1},
            {"x": .8, "z": .8}, {"x": 0, "z": .8},
        ],
        "interior_rings": [],
    }]
    spaces = [Polygon([(2, 2), (3, 2), (3, 3), (2, 3)])]

    resolved = whole_home_cad._resolve_wall_evidence_with_global_topology(
        assemblies, footprints, spaces,
        {"method": "cad-global-wall-topology-v1", "status": "proved"},
        origin_x=0, origin_z=0)

    corner_rows = resolved[2:]
    assert [row["source_representation"] for row in corner_rows] == [
        "junction_evidence", "junction_evidence"]
    assert all(row["review_status"] == "rejected" for row in corner_rows)
    proof = corner_rows[0]["junction_evidence"]
    assert proof["support_method"] == "proved_global_topology_corner_chain_v1"
    assert {row["wall_assembly_id"] for row in proof["supports"]} == {
        "left-wall", "upper-wall"}
    assert proof["chain_source_handles"] == ["corner-h", "corner-v"]
    assert proof["source_wall_mask_coverage_ratios"] == [1.0, 1.0]

    # An equally-close second local support makes the physical assignment
    # ambiguous, so the same source evidence must remain review-blocking.
    ambiguous = copy.deepcopy(assemblies)
    ambiguous.insert(2, accepted(
        "left-wall-duplicate", [[0, -.1], [.2, -.1], [.2, .1], [0, .1]]))
    unresolved = whole_home_cad._resolve_wall_evidence_with_global_topology(
        ambiguous, footprints, spaces,
        {"method": "cad-global-wall-topology-v1", "status": "proved"},
        origin_x=0, origin_z=0)
    assert unresolved[-2]["review_status"] == "needs_review"
    assert unresolved[-1]["review_status"] == "needs_review"


def test_proved_global_strip_resolves_only_measured_centerline_or_boundary_roles():
    from shapely.geometry import Polygon

    def pending(identifier, x):
        return {
            "id": identifier,
            "source_representation": "human_confirmed_ambiguous",
            "review_status": "needs_review",
            "source_centerline": [[x, 0], [x, 1]],
            "source_entity_handles": [identifier],
            "reason_codes": ["cad_wall_representation_unresolved"],
            "production_blockers": ["cad_wall_representation_unresolved"],
            "cad_provenance": {},
        }

    def footprint(identifier, min_x, max_x):
        return {
            "id": identifier, "review_status": "accepted",
            "points": [
                {"x": min_x, "z": 0}, {"x": max_x, "z": 0},
                {"x": max_x, "z": 1}, {"x": min_x, "z": 1},
            ],
            "interior_rings": [],
        }

    assemblies = [
        pending("single-center", 1.0),
        pending("measured-outer-face", 2.24),
        # A line floating inside a broad strip is neither its centreline nor
        # either measured boundary face and must remain review-blocking.
        pending("unsupported-strip-position", 3.06),
    ]
    footprints = [
        footprint("single-strip", .94, 1.06),
        footprint("measured-strip", 2.0, 2.24),
        footprint("ambiguous-strip", 3.0, 3.24),
    ]
    summary = {
        "method": "cad-global-wall-topology-v1", "status": "proved",
        "inferred_single_run_width_m": .12,
        "measured_spacing_p75_m": .24,
    }

    resolved = whole_home_cad._resolve_wall_evidence_with_global_topology(
        assemblies, footprints,
        [Polygon([(10, 10), (11, 10), (11, 11), (10, 11)])], summary,
        origin_x=0, origin_z=0)

    assert resolved[0]["review_status"] == "rejected"
    assert resolved[0]["global_topology_evidence"]["strip_role"] == \
        "inferred_single_run_centerline"
    assert resolved[1]["review_status"] == "rejected"
    assert resolved[1]["global_topology_evidence"]["strip_role"] == \
        "measured_wall_boundary_face"
    assert resolved[2]["review_status"] == "needs_review"


def test_global_topology_consumes_transverse_wall_width_connector_only():
    from shapely.geometry import Polygon

    connector = {
        "id": "wall-cap", "source_representation": "human_confirmed_ambiguous",
        "review_status": "needs_review", "source_centerline": [[.5, 0], [.5, .15]],
        "source_entity_handles": ["cap"],
        "reason_codes": ["cad_wall_representation_unresolved"],
        "production_blockers": ["cad_wall_representation_unresolved"],
        "cad_provenance": {},
    }
    floating = copy.deepcopy(connector)
    floating.update(
        id="floating-short", source_centerline=[[.3, .3], [.3, .45]])
    footprints = [{
        "id": "global-wall", "review_status": "accepted",
        "points": [{"x": 0, "z": 0}, {"x": 1, "z": 0},
                   {"x": 1, "z": .15}, {"x": 0, "z": .15}],
        "interior_rings": [],
    }]
    summary = {
        "method": "cad-global-wall-topology-v1", "status": "proved",
        "inferred_single_run_width_m": .15,
        "measured_spacing_p75_m": .15,
    }

    resolved = whole_home_cad._resolve_wall_evidence_with_global_topology(
        [connector, floating], footprints,
        [Polygon([(2, 2), (3, 2), (3, 3), (2, 3)])], summary,
        origin_x=0, origin_z=0)

    assert resolved[0]["review_status"] == "rejected"
    assert resolved[0]["source_representation"] == (
        "global_topology_connector_evidence")
    proof = resolved[0]["global_topology_connector_evidence"]
    assert proof["endpoint_wall_boundary_distances_m"] == [0.0, 0.0]
    assert proof["reference_wall_width_m"] == .15
    # Same length without proved wall-mask coverage cannot self-prove as a
    # transverse wall cap.
    assert resolved[1]["review_status"] == "needs_review"

    wide_connector = copy.deepcopy(connector)
    wide_connector.update(
        id="wide-wall-cap", source_centerline=[[.5, 0], [.5, .24]])
    wide_footprint = [{
        **footprints[0],
        "points": [{"x": 0, "z": 0}, {"x": 1, "z": 0},
                   {"x": 1, "z": .24}, {"x": 0, "z": .24}],
    }]
    wide_summary = {
        **summary, "inferred_single_run_width_m": .10,
        "measured_spacing_p75_m": .24,
    }
    wide = whole_home_cad._resolve_wall_evidence_with_global_topology(
        [wide_connector], wide_footprint,
        [Polygon([(2, 2), (3, 2), (3, 3), (2, 3)])], wide_summary,
        origin_x=0, origin_z=0)[0]
    assert wide["review_status"] == "rejected"
    assert wide["global_topology_connector_evidence"][
        "reference_wall_width_m"] == pytest.approx(.24)


def test_global_topology_consumes_boundary_micro_detail_but_not_own_buffer():
    from shapely.geometry import Polygon

    boundary = {
        "id": "boundary-micro",
        "source_representation": "human_confirmed_ambiguous",
        "review_status": "needs_review",
        "source_centerline": [[.2, 0], [.24, 0]],
        "source_entity_handles": ["micro"],
        "reason_codes": ["cad_wall_representation_unresolved"],
        "production_blockers": ["cad_wall_representation_unresolved"],
        "cad_provenance": {},
    }
    footprint = [{
        "id": "wall", "review_status": "accepted",
        "points": [{"x": 0, "z": 0}, {"x": 1, "z": 0},
                   {"x": 1, "z": .1}, {"x": 0, "z": .1}],
        "interior_rings": [],
    }]
    summary = {
        "method": "cad-global-wall-topology-v1", "status": "proved",
        "inferred_single_run_width_m": .1, "measured_spacing_p75_m": .24,
    }

    result = whole_home_cad._resolve_wall_evidence_with_global_topology(
        [boundary], footprint,
        [Polygon([(2, 2), (3, 2), (3, 3), (2, 3)])], summary,
        origin_x=0, origin_z=0)[0]
    assert result["review_status"] == "rejected"
    assert result["global_topology_micro_evidence"]["method"] == \
        "proved_global_wall_boundary_micro_detail_v1"

    own_buffer = copy.deepcopy(boundary)
    own_buffer["source_centerline"] = [[.48, .03], [.52, .03]]
    tiny_footprint = [{
        **footprint[0],
        "points": [{"x": .45, "z": 0}, {"x": .55, "z": 0},
                   {"x": .55, "z": .06}, {"x": .45, "z": .06}],
    }]
    unresolved = whole_home_cad._resolve_wall_evidence_with_global_topology(
        [own_buffer], tiny_footprint,
        [Polygon([(2, 2), (3, 2), (3, 3), (2, 3)])], summary,
        origin_x=0, origin_z=0)[0]
    assert unresolved["review_status"] == "needs_review"


def test_global_topology_consumes_short_terminal_wall_boundary_face_only():
    from shapely.geometry import Polygon

    boundary_face = {
        "id": "wall-end-face",
        "source_representation": "human_confirmed_ambiguous",
        "review_status": "needs_review",
        "source_centerline": [[0, 0], [.15, 0]],
        "source_entity_handles": ["end-face"],
        "reason_codes": ["cad_wall_representation_unresolved"],
        "production_blockers": ["cad_wall_representation_unresolved"],
        "cad_provenance": {},
    }
    footprints = [{
        "id": "vertical-wall", "review_status": "accepted",
        "points": [{"x": 0, "z": 0}, {"x": .15, "z": 0},
                   {"x": .15, "z": 1}, {"x": 0, "z": 1}],
        "interior_rings": [],
    }]
    summary = {
        "method": "cad-global-wall-topology-v1", "status": "proved",
        "inferred_single_run_width_m": .15,
        "measured_spacing_p75_m": .15,
    }

    resolved = whole_home_cad._resolve_wall_evidence_with_global_topology(
        [boundary_face], footprints,
        [Polygon([(2, 2), (3, 2), (3, 3), (2, 3)])], summary,
        origin_x=0, origin_z=0)[0]

    assert resolved["review_status"] == "rejected"
    assert resolved["source_representation"] == \
        "global_topology_boundary_evidence"
    proof = resolved["global_topology_boundary_evidence"]
    assert proof["method"] == "proved_global_wall_short_boundary_face_v1"
    assert proof["midpoint_wall_boundary_distance_m"] == 0.0


def test_global_topology_consumes_six_sample_piecewise_wall_role_only():
    from shapely.geometry import Polygon

    source = {
        "id": "corner-source",
        "source_representation": "human_confirmed_ambiguous",
        "review_status": "needs_review",
        "source_centerline": [[.05, 0], [.05, .8]],
        "source_entity_handles": ["corner"],
        "reason_codes": ["cad_wall_representation_unresolved"],
        "production_blockers": ["cad_wall_representation_unresolved"],
        "cad_provenance": {},
    }
    footprints = [{
        "id": "global-wall", "review_status": "accepted",
        "points": [
            {"x": 0, "z": 0}, {"x": .1, "z": 0},
            {"x": .1, "z": .4}, {"x": .15, "z": .4},
            {"x": .15, "z": .8}, {"x": .05, "z": .8},
            {"x": .05, "z": .4}, {"x": 0, "z": .4},
        ],
        "interior_rings": [],
    }]
    summary = {
        "method": "cad-global-wall-topology-v1", "status": "proved",
        "inferred_single_run_width_m": .1, "measured_spacing_p75_m": .24,
    }

    resolved = whole_home_cad._resolve_wall_evidence_with_global_topology(
        [source], footprints,
        [Polygon([(2, 2), (3, 2), (3, 3), (2, 3)])], summary,
        origin_x=0, origin_z=0)[0]

    assert resolved["review_status"] == "rejected"
    assert resolved["source_representation"] == \
        "global_topology_piecewise_evidence"
    proof = resolved["global_topology_piecewise_evidence"]
    assert proof["classified_cross_section_count"] >= 6
    assert {row["source_role"] for row in proof["classified_cross_sections"]} == {
        "centerline", "negative_boundary_face"}


def test_five_centered_sections_prove_local_wall_thicker_than_global_default():
    from shapely.geometry import Polygon

    pending = {
        "id": "local-exterior-centerline",
        "source_representation": "human_confirmed_ambiguous",
        "review_status": "needs_review",
        "source_centerline": [[0, 0], [0, 1]],
        "source_entity_handles": ["local-center"],
        "reason_codes": ["cad_wall_representation_unresolved"],
        "production_blockers": ["cad_wall_representation_unresolved"],
        "cad_provenance": {},
    }
    footprints = [{
        "id": "local-thick-wall", "review_status": "accepted",
        "points": [{"x": -.1, "z": 0}, {"x": .1, "z": 0},
                   {"x": .1, "z": 1}, {"x": -.1, "z": 1}],
        "interior_rings": [],
    }]
    summary = {
        "method": "cad-global-wall-topology-v1", "status": "proved",
        "inferred_single_run_width_m": .15,
        "measured_spacing_p75_m": .15,
    }

    resolved = whole_home_cad._resolve_wall_evidence_with_global_topology(
        [pending], footprints,
        [Polygon([(2, 2), (3, 2), (3, 3), (2, 3)])], summary,
        origin_x=0, origin_z=0)

    assert resolved[0]["review_status"] == "rejected"
    proof = resolved[0]["global_topology_evidence"]
    assert proof["strip_role"] == "independently_supported_local_centerline"
    assert proof["reference_width_m"] == .2
    assert len(proof["cross_sections"]) == 3


def test_five_stable_sections_prove_slightly_inset_local_wall_boundary_face():
    from shapely.geometry import Polygon

    def pending(identifier):
        return {
            "id": identifier,
            "source_representation": "human_confirmed_ambiguous",
            "review_status": "needs_review",
            "source_centerline": [[0, 0], [0, 1]],
            "source_entity_handles": [identifier],
            "reason_codes": ["cad_wall_representation_unresolved"],
            "production_blockers": ["cad_wall_representation_unresolved"],
            "cad_provenance": {},
        }

    def footprint(identifier, positive_edge):
        return {
            "id": identifier, "review_status": "accepted",
            "points": [
                {"x": -.16, "z": 0}, {"x": positive_edge, "z": 0},
                {"x": positive_edge, "z": 1}, {"x": -.16, "z": 1},
            ],
            "interior_rings": [],
        }

    summary = {
        "method": "cad-global-wall-topology-v1", "status": "proved",
        "inferred_single_run_width_m": .15,
        "measured_spacing_p75_m": .15,
    }
    supported = whole_home_cad._resolve_wall_evidence_with_global_topology(
        [pending("local-face")], [footprint("local-strip", .035)],
        [Polygon([(2, 2), (3, 2), (3, 3), (2, 3)])], summary,
        origin_x=0, origin_z=0)[0]

    assert supported["review_status"] == "rejected"
    proof = supported["global_topology_evidence"]
    assert proof["strip_role"] == \
        "independently_supported_local_boundary_face"
    assert proof["reference_width_m"] == .195
    assert proof["consistent_wall_side"] == "positive"
    assert len(proof["independent_cross_sections"]) >= 5

    # Fifty-five millimetres from the nearest mask edge is not a source wall
    # face under the strict local-boundary contract.
    unsupported = whole_home_cad._resolve_wall_evidence_with_global_topology(
        [pending("too-far-inset")], [footprint("wide-local-strip", .055)],
        [Polygon([(2, 2), (3, 2), (3, 3), (2, 3)])], summary,
        origin_x=0, origin_z=0)[0]
    assert unsupported["review_status"] == "needs_review"


def test_short_measured_wall_face_extension_terminates_at_unique_global_corner():
    from shapely.geometry import Polygon

    accepted = {
        "id": "accepted-wall", "source_representation": "paired_faces",
        "review_status": "accepted",
        "centerline": [[0, 0], [1, 0]],
        "footprint_polygon": [[0, -.1], [1, -.1], [1, .1], [0, .1]],
        "thickness_m": .2,
        "source_entity_handles": ["face-a", "face-b"],
    }
    pending = {
        "id": "outer-face-extension",
        "source_representation": "human_confirmed_ambiguous",
        "review_status": "needs_review",
        "source_centerline": [[1, .1], [1.4, .1]],
        "source_entity_handles": ["extension-face"],
        "reason_codes": ["cad_wall_representation_unresolved"],
        "production_blockers": ["cad_wall_representation_unresolved"],
        "cad_provenance": {},
    }
    global_footprint = [{
        "id": "global-wall", "review_status": "accepted",
        "points": [
            {"x": 0, "z": -.1}, {"x": 1.4, "z": -.1},
            {"x": 1.4, "z": .1}, {"x": 0, "z": .1},
        ],
        "interior_rings": [],
    }]
    summary = {
        "method": "cad-global-wall-topology-v1", "status": "proved",
        # Deliberately not the local .2 m strip width: the generic strip-role
        # resolver cannot self-consume this row before terminal proof runs.
        "inferred_single_run_width_m": .12,
        "measured_spacing_p75_m": .40,
    }

    resolved = whole_home_cad._resolve_wall_evidence_with_global_topology(
        [accepted, pending], global_footprint,
        [Polygon([(10, 10), (11, 10), (11, 11), (10, 11)])], summary,
        origin_x=0, origin_z=0)

    extension = resolved[1]
    assert extension["review_status"] == "rejected"
    proof = extension["junction_evidence"]
    assert proof["support_method"] == \
        "accepted_wall_face_global_corner_extension_v1"
    assert proof["supports"][0]["wall_assembly_id"] == "accepted-wall"
    assert proof["supports"][0]["face_offset_delta_m"] == 0.0
    assert proof["supports"][0]["terminal_forward_outside_samples"] == [True, True]


def test_accepted_opening_axis_gives_coincident_threshold_terminal_evidence():
    assembly = {
        "id": "pending-threshold",
        "source_representation": "human_confirmed_ambiguous",
        "resolved_as": None,
        "source_centerline": [[1.0, 2.0], [2.2, 2.0]],
        "footprint_polygon": None,
        "centerline": None,
        "thickness_m": None,
        "thickness_source": "unresolved",
        "review_status": "needs_review",
        "confidence_grade": "C", "confidence": 0.0,
        "legacy_wall_compatible": False,
        "reason_codes": ["cad_wall_representation_unresolved"],
        "production_blockers": ["cad_wall_representation_unresolved"],
        "source_entity_handles": ["threshold-source"],
    }
    accepted = {
        "candidate_id": "door-double", "status": "accepted",
        "wall_assembly_id": "accepted-host",
        "axis_segment_cad_m": [[101.0, -38.0], [102.2, -38.0]],
    }

    result = whole_home_cad._resolve_wall_evidence_coincident_with_accepted_openings(
        [assembly], [accepted], origin_x=100.0, origin_z=-40.0)[0]

    assert result["source_representation"] == "opening_evidence"
    assert result["review_status"] == "rejected"
    assert result["production_blockers"] == []
    assert result["opening_evidence"]["candidate_id"] == "door-double"
    assert result["opening_evidence"]["source_coverage_ratio"] == pytest.approx(1.0)
    assert result["opening_evidence"]["opening_axis_coverage_ratio"] == pytest.approx(1.0)

    offset = copy.deepcopy(assembly)
    offset["source_centerline"] = [[1.0, 2.03], [2.2, 2.03]]
    unresolved = whole_home_cad._resolve_wall_evidence_coincident_with_accepted_openings(
        [offset], [accepted], origin_x=100.0, origin_z=-40.0)[0]
    assert unresolved["source_representation"] == "human_confirmed_ambiguous"
    assert unresolved["review_status"] == "needs_review"

    ambiguous = whole_home_cad._resolve_wall_evidence_coincident_with_accepted_openings(
        [assembly], [accepted, {**accepted, "candidate_id": "door-copy"}],
        origin_x=100.0, origin_z=-40.0)[0]
    assert ambiguous["source_representation"] == "human_confirmed_ambiguous"


def test_proved_opening_frame_consumes_unique_parallel_companion_rail_only():
    assembly = {
        "id": "frame-inner-rail",
        "source_representation": "human_confirmed_ambiguous",
        "source_centerline": [[1.0, 1.955], [1.8, 1.955]],
        "footprint_polygon": None, "centerline": None, "thickness_m": None,
        "review_status": "needs_review",
        "reason_codes": ["cad_wall_representation_unresolved"],
        "production_blockers": ["cad_wall_representation_unresolved"],
        "source_entity_handles": ["inner-rail"], "cad_provenance": {},
    }
    candidate = {
        "candidate_id": "window-frame", "status": "accepted",
        "wall_assembly_id": "window-host",
        "axis_segment_cad_m": [[101.0, -38.0], [101.8, -38.0]],
        "evidence_geometry": {
            "bbox_m": [101.0, -38.115, 101.8, -37.885],
            "short_span_m": .23, "long_rail_count": 2,
            "cross_member_count": 2, "opposite_wall_face_support": True,
        },
    }

    result = whole_home_cad._resolve_wall_evidence_coincident_with_accepted_openings(
        [assembly], [candidate], origin_x=100.0, origin_z=-40.0)[0]

    assert result["review_status"] == "rejected"
    assert result["opening_evidence"]["method"] == \
        "accepted_opening_frame_companion_rail_v1"
    assert result["opening_evidence"]["measured_lateral_offset_m"] == \
        pytest.approx(.045)

    outside = copy.deepcopy(assembly)
    outside["source_centerline"] = [[1.0, 1.70], [1.8, 1.70]]
    unresolved = whole_home_cad._resolve_wall_evidence_coincident_with_accepted_openings(
        [outside], [candidate], origin_x=100.0, origin_z=-40.0)[0]
    assert unresolved["review_status"] == "needs_review"

    ambiguous = whole_home_cad._resolve_wall_evidence_coincident_with_accepted_openings(
        [assembly], [candidate, {**candidate, "candidate_id": "window-copy"}],
        origin_x=100.0, origin_z=-40.0)[0]
    assert ambiguous["review_status"] == "needs_review"


def test_slightly_longer_threshold_may_contain_but_not_merely_overlap_opening_axis():
    assembly = {
        "id": "containing-threshold",
        "source_representation": "human_confirmed_ambiguous",
        "source_centerline": [[.98, 2.0], [1.88, 2.0]],
        "footprint_polygon": None, "centerline": None, "thickness_m": None,
        "review_status": "needs_review",
        "reason_codes": ["cad_wall_representation_unresolved"],
        "production_blockers": ["cad_wall_representation_unresolved"],
        "source_entity_handles": ["threshold-source"],
    }
    accepted = {
        "candidate_id": "door-865", "status": "accepted",
        "wall_assembly_id": "accepted-host",
        "axis_segment_cad_m": [[101.0, -38.0], [101.865, -38.0]],
    }

    resolved = whole_home_cad._resolve_wall_evidence_coincident_with_accepted_openings(
        [assembly], [accepted], origin_x=100.0, origin_z=-40.0)[0]

    assert resolved["review_status"] == "rejected"
    proof = resolved["opening_evidence"]
    assert proof["method"] == "accepted_opening_contained_threshold_axis_v1"
    assert proof["opening_axis_coverage_ratio"] == pytest.approx(1.0)
    assert proof["source_axis_overhang_m"] == pytest.approx([.02, .015])

    excessive = copy.deepcopy(assembly)
    excessive["source_centerline"] = [[.93, 2.0], [1.88, 2.0]]
    unresolved = whole_home_cad._resolve_wall_evidence_coincident_with_accepted_openings(
        [excessive], [accepted], origin_x=100.0, origin_z=-40.0)[0]
    assert unresolved["review_status"] == "needs_review"


def test_two_point_return_path_parallel_to_opening_face_uses_measured_host_thickness():
    host = {
        "id": "accepted-window-host", "review_status": "accepted",
        "thickness_m": .2286,
    }
    source = {
        "id": "closed-return-face",
        "source_representation": "invalid_closed_footprint",
        "resolved_as": None,
        "source_centerline": [[1.0, 2.1143], [2.2192, 2.1143]],
        "footprint_polygon": None, "centerline": None,
        "thickness_m": None, "thickness_source": "unresolved",
        "review_status": "needs_review", "confidence_grade": "C",
        "confidence": 0.0, "legacy_wall_compatible": False,
        "reason_codes": ["cad_wall_footprint_invalid"],
        "production_blockers": ["cad_wall_footprint_invalid"],
        "source_entity_handles": ["window-face-return"],
        "cad_provenance": {},
        "degenerate_return_path_evidence": {
            "method": "cad_closed_two_point_return_path_v1",
            "unique_point_count": 2,
            "unique_axis_model_m": [[1.0, 2.1143], [2.2192, 2.1143]],
            "unique_axis_length_m": 1.2192,
            "source_path_length_m": 2.4384,
            "return_length_ratio": 2.0,
        },
    }
    opening = {
        "candidate_id": "window-1", "status": "accepted",
        "wall_assembly_id": "accepted-window-host",
        "axis_segment_cad_m": [[101.0, -38.0], [102.2192, -38.0]],
    }

    resolved = whole_home_cad._resolve_wall_evidence_coincident_with_accepted_openings(
        [host, source], [opening], origin_x=100.0, origin_z=-40.0)
    result = next(row for row in resolved if row["id"] == "closed-return-face")

    assert result["review_status"] == "rejected"
    assert result["source_representation"] == "opening_evidence"
    proof = result["opening_evidence"]
    assert proof["method"] == "accepted_opening_parallel_wall_face_v1"
    assert proof["host_wall_thickness_m"] == pytest.approx(.2286)
    assert proof["expected_half_thickness_m"] == pytest.approx(.1143)
    assert proof["measured_lateral_offset_m"] == pytest.approx(.1143)
    assert proof["half_thickness_offset_delta_m"] == pytest.approx(0.0)
    assert proof["source_axial_coverage_ratio"] == pytest.approx(1.0)

    wrong_offset = copy.deepcopy(source)
    wrong_offset["source_centerline"] = [[1.0, 2.05], [2.2192, 2.05]]
    unresolved = whole_home_cad._resolve_wall_evidence_coincident_with_accepted_openings(
        [host, wrong_offset], [opening], origin_x=100.0, origin_z=-40.0)
    assert next(row for row in unresolved if row["id"] == "closed-return-face")[
        "review_status"] == "needs_review"

    ambiguous = whole_home_cad._resolve_wall_evidence_coincident_with_accepted_openings(
        [host, source], [opening, {**opening, "candidate_id": "window-copy"}],
        origin_x=100.0, origin_z=-40.0)
    assert next(row for row in ambiguous if row["id"] == "closed-return-face")[
        "review_status"] == "needs_review"


def test_short_source_segment_owned_by_one_accepted_opening_is_not_a_wall():
    assembly = {
        "id": "door-glyph-segment",
        "source_representation": "human_confirmed_ambiguous",
        "source_centerline": [[1.0, 2.0], [1.115, 2.0]],
        "footprint_polygon": None, "centerline": None, "thickness_m": None,
        "review_status": "needs_review",
        "reason_codes": ["cad_wall_representation_unresolved"],
        "production_blockers": ["cad_wall_representation_unresolved"],
        "source_entity_handles": ["door-root"], "cad_provenance": {},
    }
    candidate = {
        "candidate_id": "door-axis", "status": "accepted", "kind": "door",
        "wall_assembly_id": "door-host", "source_handles": ["door-root"],
        "axis_segment_cad_m": [[101, -18], [101.813, -18]],
    }

    resolved = whole_home_cad._resolve_wall_evidence_coincident_with_accepted_openings(
        [assembly], [candidate], origin_x=100, origin_z=-20)

    assert resolved[0]["review_status"] == "rejected"
    assert resolved[0]["opening_evidence"]["method"] == (
        "accepted_opening_source_handle_ownership_v1")


def test_accepted_opening_axis_endpoint_gives_perpendicular_jamb_terminal_evidence():
    assembly = {
        "id": "pending-jamb",
        "source_representation": "human_confirmed_ambiguous",
        "resolved_as": None,
        "source_centerline": [[1.0, 2.0], [1.0, 2.2286]],
        "footprint_polygon": None,
        "centerline": None,
        "thickness_m": None,
        "thickness_source": "unresolved",
        "review_status": "needs_review",
        "confidence_grade": "C", "confidence": 0.0,
        "legacy_wall_compatible": False,
        "reason_codes": ["cad_wall_representation_unresolved"],
        "production_blockers": ["cad_wall_representation_unresolved"],
        "source_entity_handles": ["jamb-source"],
        "cad_provenance": {
            "wall_assembly_source_representation":
                "human_confirmed_ambiguous",
        },
    }
    accepted = {
        "candidate_id": "door-at-jamb", "status": "accepted",
        "wall_assembly_id": "accepted-host",
        "axis_segment_cad_m": [[101.0, -38.0], [102.2, -38.0]],
    }

    result = whole_home_cad._resolve_wall_evidence_coincident_with_accepted_openings(
        [assembly], [accepted], origin_x=100.0, origin_z=-40.0)[0]

    assert result["source_representation"] == "junction_evidence"
    assert result["review_status"] == "rejected"
    assert result["production_blockers"] == []
    proof = result["junction_evidence"]
    assert proof["support_method"] == "accepted_opening_axis_endpoint_jamb_v1"
    assert proof["supports"][0]["candidate_id"] == "door-at-jamb"
    assert proof["supports"][0]["endpoint_distance_m"] == pytest.approx(0.0)
    assert result["cad_provenance"][
        "wall_assembly_source_representation"] == "junction_evidence"

    centered = copy.deepcopy(assembly)
    centered["id"] = "centered-jamb"
    centered["source_centerline"] = [[1.0, 1.925], [1.0, 2.075]]
    centered_result = (
        whole_home_cad._resolve_wall_evidence_coincident_with_accepted_openings(
            [centered], [accepted], origin_x=100.0, origin_z=-40.0)[0])
    assert centered_result["review_status"] == "rejected"
    assert centered_result["junction_evidence"]["supports"][0][
        "endpoint_distance_m"] == pytest.approx(0.0)


def test_staggered_wall_faces_resolve_only_between_two_unique_transverse_terminals():
    def accepted(identifier, centerline, footprint, thickness, entities):
        return {
            "id": identifier, "source_representation": "paired_faces",
            "resolved_as": "paired_faces", "centerline": centerline,
            "footprint_polygon": footprint, "thickness_m": thickness,
            "thickness_source": "cad_geometry", "height_m": 2.8,
            "height_source": "project_default_assumption",
            "review_status": "accepted", "confidence": 1.0,
            "source_entity_handles": [row["handle"] for row in entities],
            "source_entities": entities,
        }

    pending = {
        "id": "pending-face", "source_representation": "human_confirmed_ambiguous",
        "source_centerline": [[.2, 0], [.2, 1.0]],
        "footprint_polygon": None, "centerline": None, "thickness_m": None,
        "thickness_source": "unresolved", "review_status": "needs_review",
        "source_entity_handles": ["pending"],
        "source_entities": [{"handle": "pending", "root_handle": "pending",
                             "model_segment_m": [[.2, 0], [.2, 1.0]]}],
        "reason_codes": ["cad_wall_representation_unresolved"],
        "production_blockers": ["cad_wall_representation_unresolved"],
        "cad_provenance": {
            "wall_assembly_source_representation": "human_confirmed_ambiguous"},
    }
    continuation = accepted(
        "continued-wall", [[.1, 1.2], [.1, 3.0]],
        [[0, 1.2], [.2, 1.2], [.2, 3], [0, 3]], .2,
        [{"handle": "continued-face", "root_handle": "continued-face",
          "model_segment_m": [[.2, 1.2], [.2, 3.0]]},
         {"handle": "mate-face", "root_handle": "mate-face",
          "model_segment_m": [[0, .2], [0, 3.0]]}],
    )
    lower = accepted(
        "lower-terminal", [[-1, .1], [0, .1]],
        [[-1, 0], [0, 0], [0, .2], [-1, .2]], .2,
        [{"handle": "lower-a", "model_segment_m": [[-1, 0], [0, 0]]},
         {"handle": "lower-b", "model_segment_m": [[-1, .2], [0, .2]]}],
    )
    upper = accepted(
        "upper-terminal", [[.2, 1.1], [2, 1.1]],
        [[.2, 1], [2, 1], [2, 1.2], [.2, 1.2]], .2,
        [{"handle": "upper-a", "model_segment_m": [[.2, 1], [2, 1]]},
         {"handle": "upper-b", "model_segment_m": [[.2, 1.2], [2, 1.2]]}],
    )

    result = whole_home_cad._resolve_collinear_wall_face_continuations(
        [pending, continuation, lower, upper])

    resolved = result[0]
    assert resolved["source_representation"] == "collinear_face_continuation"
    assert resolved["review_status"] == "accepted"
    assert resolved["centerline"] == [[.1, .1], [.1, 1.1]]
    assert resolved["thickness_m"] == pytest.approx(.2)
    proof = resolved["collinear_face_continuation_evidence"]
    assert proof["projected_overlap_ratio"] == pytest.approx(.8)
    assert {row["wall_assembly_id"] for row in proof["terminal_supports"]} == {
        "lower-terminal", "upper-terminal"}

    ambiguous = whole_home_cad._resolve_collinear_wall_face_continuations(
        [pending, continuation, lower, {**upper, "id": "upper-copy"}, upper])
    assert ambiguous[0]["review_status"] == "needs_review"


def _reference_asset_fixture(tmp_path, *, size=(500, 500)):
    contract = copy.deepcopy(whole_home_cad.JUSTEASY_REFERENCE_CONTRACT)
    root = tmp_path / 'reference-assets'
    folder = root / contract['contract_id']
    folder.mkdir(parents=True)
    for index, slot in enumerate(contract['slots'], 1):
        asset = slot['reference_asset']
        path = folder / asset['filename']
        Image.new('RGB', size, (index * 17 % 255, index * 29 % 255, index * 41 % 255)).save(
            path, format='JPEG')
        asset['sha256'] = whole_home_cad.sha256_file(str(path))
    return contract, root


def _cad_upload(name: str, payload: bytes):
    return SimpleNamespace(filename=name, file=io.BytesIO(payload))


def _cad_model():
    origin_x, origin_z = 100.0, -40.0
    model_points = [
        ((0.0, 0.0), (4.0, 0.0)), ((4.0, 0.0), (4.0, 3.0)),
        ((4.0, 3.0), (0.0, 3.0)), ((0.0, 3.0), (0.0, 0.0)),
    ]
    walls = []
    for index, (start, end) in enumerate(model_points, 1):
        walls.append({
            'id': f'w{index}', 'start': {'x': start[0], 'z': start[1]},
            'end': {'x': end[0], 'z': end[1]}, 'kind': 'exterior',
            'thickness_m': .12, 'height_m': 2.8,
            'source': 'cad', 'confidence': 1.0,
            'cad_provenance': {
                'handle': f'{index:X}', 'root_handle': f'{index:X}',
                'source_handle': f'{index:X}', 'layer': 'A-WALL',
                'raw_layer': 'A-WALL', 'effective_layer': 'A-WALL', 'block': '',
                'source_kind': 'LINE', 'transform': [], 'insert_chain': [],
                'confidence': 1.0,
                'source_segment_m': [
                    [start[0] + origin_x, start[1] + origin_z],
                    [end[0] + origin_x, end[1] + origin_z],
                ],
            },
        })
    room_polygon = [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)]
    model = {
        'schema_version': 2, 'model_id': 'cad-model', 'coordinate_system': 'metres-y-up',
        'width_m': 4, 'depth_m': 3, 'wall_height_m': 2.8, 'wall_thickness_m': .12,
        'scale': {'status': 'cad_authoritative', 'method': '$INSUNITS'},
        'walls': walls, 'openings': [], 'fixed_objects': [], 'cameras': [],
        'rooms': [{
            'id': 'living', 'label': '客厅', 'room_type': 'living_room',
            'reference_room_profile': 'living_room', 'semantic_profile': 'living_room',
            'semantic_status': 'complete', 'selected': True, 'source': 'cad', 'confidence': 1.0,
            'floor_elevation_m': 0.0, 'ceiling_height_m': 2.8,
            'polygon': [{'x': x, 'z': z} for x, z in room_polygon],
            'cad_provenance': {
                'handle': '1', 'root_handle': '1', 'source_handle': '1',
                'layer': 'A-WALL', 'raw_layer': 'A-WALL',
                'effective_layer': 'A-WALL', 'block': '', 'source_kind': 'polygonize',
                'transform': [], 'insert_chain': [], 'confidence': 1.0,
                'source_polygon_m': [[x + origin_x, z + origin_z] for x, z in room_polygon],
                'boundary_sources': [],
            },
        }],
        'room_contracts': [], 'uncertainties': [],
        'cad_to_model': {'type': 'translation', 'x': -origin_x, 'z': -origin_z},
        'model_to_cad': {'type': 'translation', 'x': origin_x, 'z': origin_z},
    }
    model['cad_facts_hash'] = whole_home_cad.cad_facts_hash(model)
    return model


def test_cad_insert_preserves_rotation_scale_extent_room_and_reference_contract(monkeypatch, tmp_path):
    import ezdxf

    document = ezdxf.new("R2018")
    document.header["$INSUNITS"] = 6
    document.layers.add("A-WALL")
    document.layers.add("FURNITURE")
    modelspace = document.modelspace()
    points = [(0, 0), (8, 0), (8, 6), (0, 6)]
    for first, second in zip(points, points[1:] + points[:1]):
        modelspace.add_line(first, second, dxfattribs={"layer": "A-WALL"})
    modelspace.add_text("主卧", dxfattribs={"insert": (4, 3), "height": .2})
    block = document.blocks.new("BED_BLOCK")
    block.add_lwpolyline([(-1, -.5), (1, -.5), (1, .5), (-1, .5)], close=True,
                         dxfattribs={"layer": "FURNITURE"})
    modelspace.add_blockref("BED_BLOCK", (4, 3), dxfattribs={
        "layer": "FURNITURE", "rotation": 30, "xscale": 1.5, "yscale": .75,
    })
    path = tmp_path / "insert-anchor.dxf"
    document.saveas(path)
    monkeypatch.setattr(whole_home_cad, "CAD_ROOT", str(tmp_path / "cad-artifacts"))

    model, report = whole_home_cad.parse_dxf(str(path), "insert-anchor")
    assert report["reference_anchor_report"]["status"] == "ready"
    assert len(model["fixed_objects"]) == 1
    bed = model["fixed_objects"][0]
    assert bed["room_id"] == model["rooms"][0]["id"]
    assert bed["rotation_y_deg"] == pytest.approx(30)
    assert bed["insert_scale"] == {"x": 1.5, "y": .75}
    assert bed["size_source"] == "cad_expanded_virtual_entities_bbox_2d"
    assert bed["height_source"] == "render_proxy_role_default_not_cad_fact"
    assert bed["size"]["x"] == pytest.approx(3, abs=.01)
    assert bed["size"]["z"] == pytest.approx(.75, abs=.01)
    contract = model["room_contracts"][0]
    assert contract["required_role_groups"] == [["bed"]]
    assert contract["status"] == "complete"


def test_unknown_cad_insert_extent_blocks_reference_without_placeholder(monkeypatch, tmp_path):
    import ezdxf

    document = ezdxf.new("R2018")
    document.header["$INSUNITS"] = 6
    document.layers.add("A-WALL")
    document.layers.add("FURNITURE")
    modelspace = document.modelspace()
    points = [(0, 0), (8, 0), (8, 6), (0, 6)]
    for first, second in zip(points, points[1:] + points[:1]):
        modelspace.add_line(first, second, dxfattribs={"layer": "A-WALL"})
    modelspace.add_text("主卧", dxfattribs={"insert": (4, 3), "height": .2})
    document.blocks.new("BED_EMPTY")
    modelspace.add_blockref("BED_EMPTY", (4, 3), dxfattribs={"layer": "FURNITURE", "rotation": 18})
    path = tmp_path / "unknown-anchor.dxf"
    document.saveas(path)
    monkeypatch.setattr(whole_home_cad, "CAD_ROOT", str(tmp_path / "cad-artifacts"))
    model, report = whole_home_cad.parse_dxf(str(path), "unknown-anchor")
    bed = model["fixed_objects"][0]
    assert bed["size"] == {"x": 0.0, "y": .55, "z": 0.0}
    assert bed["size_source"] == "unknown"
    assert bed["reference_anchor_ready"] is False
    assert "cad_fixed_object_extent_unknown" in bed["reference_anchor_blockers"]
    assert report["reference_anchor_report"]["status"] == "blocked"
    groups = whole_home_cad._required_role_groups("kitchen", {"kitchen_run", "hob", "sink", "fridge"})
    assert groups == [["kitchen_run"], ["hob"], ["sink"], ["fridge"]]
    assert whole_home_cad._required_role_groups("bathroom_master", {"toilet"}) == [
        ["toilet"], ["shower_zone"], ["basin"]]


def _cad_report():
    return {'structural_entity_count': 4, 'ignored_nonstructural_count': 0,
            'alignment_metrics': {
        'structural_entity_count': 4, 'ignored_nonstructural_count': 0,
        'wall_boundary_p95_m': 0.0, 'opening_count': 0,
        'opening_endpoint_errors': 0, 'opening_width_errors': 0,
        'room_nonoverlap': True, 'room_overlap_area_m2': 0.0,
        'room_coverage': 1.0, 'outer_wall_closed': True,
        'cad_derivation_coverage': 1.0,
    }}


def test_cad_upload_is_atomic_sanitized_and_validates_magic(monkeypatch, tmp_path):
    monkeypatch.setattr(whole_home_cad, 'UPLOAD_DIR', str(tmp_path))
    saved = whole_home_cad.save_cad_upload(_cad_upload(r'..\folder\恶意 名称.dxf', ASCII_DXF))
    assert os.path.dirname(saved['path']) == str(tmp_path)
    assert saved['format'] == 'dxf'
    assert saved['version'] == 'AC1032'
    assert os.path.isfile(saved['path'])
    assert not list(tmp_path.glob('*.upload*'))


def test_cad_upload_rejects_magic_and_size_without_persisting(monkeypatch, tmp_path):
    monkeypatch.setattr(whole_home_cad, 'UPLOAD_DIR', str(tmp_path))
    with pytest.raises(whole_home_cad.CadError, match='不是可识别'):
        whole_home_cad.save_cad_upload(_cad_upload('fake.dxf', b'not dxf'))
    with pytest.raises(whole_home_cad.CadError) as error:
        whole_home_cad.save_cad_upload(_cad_upload('large.dxf', ASCII_DXF), max_bytes=8)
    assert error.value.code == 'cad_upload_too_large'
    assert not list(tmp_path.glob('cad_*'))


def test_dwg_magic_and_version_are_audited(tmp_path):
    path = tmp_path / 'source.dwg'
    path.write_bytes(b'AC1032' + b'\0' * 32)
    report = whole_home_cad.inspect_cad_file(str(path))
    assert report['format'] == 'dwg'
    assert report['version'] == 'AC1032'
    assert report['version_name'] == '2018+'
    assert len(report['sha256']) == 64


def test_dxf_bypasses_converter_and_commercial_authorization(monkeypatch, tmp_path):
    path = tmp_path / 'source.dxf'
    path.write_bytes(ASCII_DXF)
    monkeypatch.setattr(whole_home_cad, 'dwg_commercial_use_authorized', lambda: False)
    monkeypatch.setattr(whole_home_cad.subprocess, 'Popen',
                        lambda *args, **kwargs: pytest.fail('DXF must not spawn converter'))
    output, evidence = whole_home_cad.convert_dwg_to_ascii_dxf(str(path), 'project')
    assert output == str(path)
    assert evidence['status'] == 'not_required'


def test_dwg_converter_present_but_unauthorized_is_zero_subprocess(monkeypatch, tmp_path):
    source = tmp_path / 'source.dwg'
    source.write_bytes(b'AC1032' + b'\0' * 32)
    converter = tmp_path / 'ODAFileConverter.exe'
    converter.write_bytes(b'placeholder')
    monkeypatch.setattr(whole_home_cad, 'dwg_commercial_use_authorized', lambda: False)
    monkeypatch.setattr(whole_home_cad.subprocess, 'Popen',
                        lambda *args, **kwargs: pytest.fail('unauthorized converter must not run'))
    with pytest.raises(whole_home_cad.CadError) as error:
        whole_home_cad.convert_dwg_to_ascii_dxf(
            str(source), 'project', executable=str(converter))
    assert error.value.code == 'dwg_converter_commercial_authorization_required'


def test_authorized_converter_uses_controlled_documented_arguments(monkeypatch, tmp_path):
    source = tmp_path / 'source.dwg'
    source.write_bytes(b'AC1032' + b'\0' * 32)
    converter = tmp_path / 'ODAFileConverter.exe'
    converter.write_bytes(b'placeholder')
    monkeypatch.setattr(whole_home_cad, 'CAD_ROOT', str(tmp_path / 'cad-assets'))
    monkeypatch.setattr(whole_home_cad, 'dwg_commercial_use_authorized', lambda: True)
    captured = {}

    class Process:
        returncode = 0
        pid = 1234

        def __init__(self, args, **kwargs):
            captured['args'] = args
            captured['kwargs'] = kwargs
            output = os.path.join(args[2], 'source.dxf')
            with open(output, 'wb') as handle:
                handle.write(ASCII_DXF)

        def communicate(self, timeout=None):
            captured['timeout'] = timeout
            return 'ok', ''

    monkeypatch.setattr(whole_home_cad.subprocess, 'Popen', Process)
    output, evidence = whole_home_cad.convert_dwg_to_ascii_dxf(
        str(source), 'project', executable=str(converter), timeout=17)
    assert captured['args'][3:] == ['ACAD2018', 'DXF', '0', '1', 'source.dwg']
    assert captured['kwargs']['shell'] is False
    assert captured['timeout'] == 17
    assert os.path.isfile(output)
    assert evidence['status'] == 'done'


def test_authorized_converter_timeout_kills_process_tree(monkeypatch, tmp_path):
    source = tmp_path / 'source.dwg'
    source.write_bytes(b'AC1032' + b'\0' * 32)
    converter = tmp_path / 'ODAFileConverter.exe'
    converter.write_bytes(b'placeholder')
    monkeypatch.setattr(whole_home_cad, 'CAD_ROOT', str(tmp_path / 'cad-assets'))
    monkeypatch.setattr(whole_home_cad, 'dwg_commercial_use_authorized', lambda: True)
    killed = []

    class Process:
        returncode = None
        pid = 4321

        def __init__(self, *args, **kwargs):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired('ODAFileConverter.exe', timeout)
            return '', ''

        def kill(self):
            killed.append(['kill'])

    monkeypatch.setattr(whole_home_cad.subprocess, 'Popen', Process)
    monkeypatch.setattr(whole_home_cad.subprocess, 'run',
                        lambda args, **kwargs: killed.append(args))
    with pytest.raises(whole_home_cad.CadError) as error:
        whole_home_cad.convert_dwg_to_ascii_dxf(
            str(source), 'project', executable=str(converter), timeout=.01)
    assert error.value.code == 'oda_timeout'
    if os.name == 'nt':
        assert killed and killed[0][:2] == ['taskkill', '/PID']
    else:
        assert killed == [['kill']]


def test_cad_runtime_status_separates_availability_and_license_without_path(monkeypatch, tmp_path):
    converter = tmp_path / 'ODAFileConverter.exe'
    converter.write_bytes(b'x')
    monkeypatch.setattr(whole_home_cad, 'detect_oda_executable', lambda: str(converter))
    monkeypatch.setattr(whole_home_cad, 'detect_acadsharp_converter', lambda: {})
    monkeypatch.setattr(whole_home_cad, 'dwg_commercial_use_authorized', lambda: False)
    monkeypatch.setattr(whole_home_cad.subprocess, 'Popen',
                        lambda *args, **kwargs: pytest.fail('status must not spawn subprocesses'))
    monkeypatch.setattr(whole_home_cad.subprocess, 'run',
                        lambda *args, **kwargs: pytest.fail('status must not spawn subprocesses'))
    status = whole_home_cad.cad_runtime_status()
    assert status['converter_available'] is True
    assert status['commercial_use_authorized'] is False
    assert status['ready_for_dwg'] is False
    assert 'converter_executable' not in status
    assert str(converter) not in repr(status)


def test_acadsharp_runtime_is_ready_under_mit_without_oda_authorization(monkeypatch, tmp_path):
    converter = tmp_path / 'FloorEngine.ACadSharpDwgConverter.dll'
    host = tmp_path / 'dotnet.exe'
    converter.write_bytes(b'acadsharp')
    host.write_bytes(b'dotnet')
    monkeypatch.setattr(whole_home_cad, 'detect_acadsharp_converter', lambda: {
        'kind': 'dotnet_dll', 'tool_path': str(converter),
        'host_path': str(host), 'license': 'MIT',
    })
    monkeypatch.setattr(whole_home_cad, 'detect_oda_executable', lambda: '')
    monkeypatch.setattr(whole_home_cad, 'dwg_commercial_use_authorized', lambda: False)
    monkeypatch.setattr(whole_home_cad.subprocess, 'Popen',
                        lambda *args, **kwargs: pytest.fail('status must not spawn subprocesses'))
    status = whole_home_cad.cad_runtime_status()
    assert status['ready_for_dwg'] is True
    assert status['converter_adapter'] == 'acadsharp_mit_v1'
    assert status['converter_license'] == 'MIT'
    assert status['commercial_use_authorized'] is True
    assert status['acadsharp_available'] is True
    assert str(converter) not in repr(status)


def test_acadsharp_converter_uses_isolated_exact_files_without_oda_flag(monkeypatch, tmp_path):
    source = tmp_path / 'source.dwg'
    source.write_bytes(b'AC1018' + b'\0' * 32)
    converter = tmp_path / 'FloorEngine.ACadSharpDwgConverter.dll'
    host = tmp_path / 'dotnet.exe'
    converter.write_bytes(b'acadsharp')
    host.write_bytes(b'dotnet')
    monkeypatch.setattr(whole_home_cad, 'CAD_ROOT', str(tmp_path / 'cad-assets'))
    monkeypatch.setattr(whole_home_cad, 'detect_acadsharp_converter', lambda: {
        'kind': 'dotnet_dll', 'tool_path': str(converter),
        'host_path': str(host), 'license': 'MIT',
    })
    monkeypatch.setattr(whole_home_cad, 'dwg_commercial_use_authorized', lambda: False)
    captured = {}

    class Process:
        returncode = 0
        pid = 8181

        def __init__(self, args, **kwargs):
            captured['args'] = args
            captured['kwargs'] = kwargs
            with open(args[3], 'wb') as handle:
                handle.write(ASCII_DXF)

        def communicate(self, timeout=None):
            captured['timeout'] = timeout
            return json.dumps({
                'ok': True, 'adapter': 'acadsharp', 'adapter_version': '3.6.35.0',
                'source_version': 'AC1018', 'output_bytes': len(ASCII_DXF),
            }), ''

    monkeypatch.setattr(whole_home_cad.subprocess, 'Popen', Process)
    output, evidence = whole_home_cad.convert_dwg_to_ascii_dxf(
        str(source), 'project', timeout=23)
    assert captured['args'][0:2] == [str(host), str(converter)]
    assert os.path.basename(captured['args'][2]) == 'source.dwg'
    assert os.path.basename(captured['args'][3]) == 'source.dxf'
    assert captured['kwargs']['shell'] is False
    assert captured['timeout'] == 23
    assert os.path.isfile(output)
    assert evidence['adapter'] == 'acadsharp_mit_v1'
    assert evidence['license'] == 'MIT'
    assert evidence['source_version'] == 'AC1018'


def test_acadsharp_mojibake_and_zero_default_normalization_are_deterministic():
    ezdxf = pytest.importorskip('ezdxf')
    assert whole_home_cad._repair_legacy_cad_text('A-ÐÂ½¨Ç½Ìå') == 'A-新建墙体'
    assert whole_home_cad._repair_legacy_cad_text('W-3¿Õ¼äÃû³Æ') == 'W-3空间名称'
    assert whole_home_cad._repair_legacy_cad_text('A-WALL') == 'A-WALL'
    assert whole_home_cad._is_structural_wall_semantics('原始结构') is True

    document = ezdxf.new('R2018')
    block = document.blocks.new('fixture')
    line = block.add_line((0, 0), (1, 0))
    line.dxf.thickness = 0.0
    line.dxf.extrusion = (0.0, 0.0, 0.0)
    insert = document.modelspace().add_blockref('fixture', (2, 3))
    repair = whole_home_cad._sanitize_dxf_defaults(document)
    assert repair['zero_thickness_removed'] >= 1
    assert repair['zero_extrusion_removed'] >= 0
    virtual = list(insert.virtual_entities())
    assert virtual and virtual[0].dxftype() == 'LINE'
    assert tuple(round(value, 6) for value in virtual[0].dxf.start.xyz) == (2.0, 3.0, 0.0)


def test_validate_cad_model_recomputes_back_projection_and_requires_provenance():
    model = _cad_model()
    validation = whole_home_cad.validate_cad_model(model, _cad_report())
    assert validation['hard_errors'] == []
    assert validation['alignment_metrics']['wall_boundary_p95_m'] == 0.0

    model['walls'][0]['start']['x'] += .2
    validation = whole_home_cad.validate_cad_model(model, _cad_report())
    codes = {row['code'] for row in validation['hard_errors']}
    assert 'cad_wall_alignment_failed' in codes

    model = _cad_model()
    model['rooms'][0]['cad_provenance'].pop('source_polygon_m')
    validation = whole_home_cad.validate_cad_model(model, _cad_report())
    assert 'cad_provenance_incomplete' in {row['code'] for row in validation['hard_errors']}


def test_normalize_preserves_cad_affine_hash_and_nested_provenance():
    model = _cad_model()
    normalized = whole_home_engine.normalize_model(model, source='cad')
    assert normalized['cad_to_model'] == model['cad_to_model']
    assert normalized['model_to_cad'] == model['model_to_cad']
    assert normalized['cad_facts_hash'] == model['cad_facts_hash']
    assert whole_home_cad.cad_facts_hash(normalized) == model['cad_facts_hash']
    assert normalized['walls'][0]['cad_provenance']['source_segment_m'][0] == [100.0, -40.0]
    assert normalized['rooms'][0]['cad_provenance']['source_polygon_m'][0] == [100.0, -40.0]


def test_cad_semantic_overlay_allows_labels_and_only_unobserved_ai_proxies():
    before = _cad_model()
    before['fixed_objects'].append({
        'id': 'cad-bed', 'name': 'B01', 'kind': 'bed',
        'position': {'x': 2, 'y': 0, 'z': 1},
        'size': {'x': 1.5, 'y': .55, 'z': 2.0},
        'source': 'cad', 'observed': True,
        'cad_provenance': {'handle': 'B01', 'root_handle': 'B01', 'source_handle': 'B01'},
    })
    before['cad_facts_hash'] = whole_home_cad.cad_facts_hash(before)
    after = copy.deepcopy(before)
    after['rooms'][0].update(label='AI 补充的客餐厅', semantic_profile='living_room')
    after['fixed_objects'][0].update(
        room_id='room-living-ai-binding',
        reference_anchor_ready=True,
        reference_anchor_blockers=[],
    )
    after['fixed_objects'][0]['cad_provenance']['encoded_layer'] = 'legacy-display-only'
    after['fixed_objects'].append({
        'id': 'proxy-sofa', 'name': '沙发布局代理', 'kind': 'sofa',
        'position': {'x': 2, 'y': 0, 'z': 1}, 'size': {'x': 2, 'y': .8, 'z': .9},
        'source': 'ai', 'purpose': 'layout_proxy', 'observed': False,
    })
    assert whole_home_cad.cad_facts_hash(after) == whole_home_cad.cad_facts_hash(before)
    assert whole_home_cad.validate_cad_semantic_overlay(before, after)['hard_errors'] == []

    injected = copy.deepcopy(after)
    injected['fixed_objects'].append({
        'id': 'invented-basin', 'source': 'ai', 'purpose': 'observed_architecture',
        'observed': True, 'position': {'x': 1, 'y': 0, 'z': 1},
    })
    assert 'cad_semantic_observed_object_injected' in {
        row['code'] for row in whole_home_cad.validate_cad_semantic_overlay(before, injected)['hard_errors']}

    moved = copy.deepcopy(after)
    moved['walls'][0]['start']['x'] += .1
    assert 'cad_semantic_facts_changed' in {
        row['code'] for row in whole_home_cad.validate_cad_semantic_overlay(before, moved)['hard_errors']}


def test_cad_hybrid_is_normalized_before_hash_and_semantic_proxies_keep_facts_stable(tmp_path):
    candidate = _cad_model()
    preview = tmp_path / 'cad-preview.png'
    Image.new('RGB', (400, 300), 'white').save(preview)
    report = _cad_report()
    report.update({
        'semantic_preview_path': str(preview),
        'semantic_preview_mapping': {
            'image_width': 400, 'image_height': 300, 'padding': 0,
            'pixels_per_metre': 100, 'cad_bbox_m': [100, -40, 104, -37],
        },
        'hard_errors': [{'code': 'cad_room_semantics_unresolved'}],
    })
    ai_model = {
        'width_m': 4, 'depth_m': 3, 'ai_model': 'gemini-test',
        'walls': [], 'openings': [],
        'rooms': [{
            'id': 'living', 'label': '客厅', 'room_type': 'living_room',
            'confidence': .9,
            'polygon': [
                {'x': 0, 'z': 3}, {'x': 4, 'z': 3},
                {'x': 4, 'z': 0}, {'x': 0, 'z': 0},
            ],
        }, {
            'id': 'bathroom', 'label': '卫生间', 'room_type': 'bathroom',
            'confidence': .85,
            'polygon': [
                {'x': 0, 'z': 3}, {'x': 1, 'z': 3},
                {'x': 1, 'z': 0}, {'x': 0, 'z': 0},
            ],
        }],
        'fixed_objects': [
            {'id': 'sofa-ai', 'name': '沙发', 'kind': 'sofa', 'room_id': 'living',
             'position': {'x': 2, 'z': 1.5}, 'size': {'x': 2, 'y': .8, 'z': .8}},
            {'id': 'tv-ai', 'name': '电视', 'kind': 'tv', 'room_id': 'living',
             'position': {'x': 2, 'z': .3}, 'size': {'x': 1.4, 'y': .8, 'z': .2}},
        ],
    }
    hybrid, hybrid_report = whole_home_cad.cad_hybrid_model_from_ai(
        candidate, report, ai_model)
    assert hybrid_report['hard_errors'] == []
    assert hybrid['rooms'][0]['source'] == 'ai_edited'
    assert hybrid['cad_semantic_derivation']['semantic_overlap_area_before_m2'] > 0
    assert hybrid['cad_semantic_derivation']['semantic_overlap_repairs']
    assert {row['code'] for row in hybrid['reference_anchor_report']['hard_errors']} == {
        'cad_room_required_anchor_missing'}
    from shapely.geometry import Polygon
    room_shapes = [Polygon([(point['x'], point['z']) for point in room['polygon']])
                   for room in hybrid['rooms']]
    assert room_shapes[0].intersection(room_shapes[1]).area == pytest.approx(0)
    assert hybrid['cad_facts_hash'] == whole_home_cad.cad_facts_hash(hybrid)
    assert whole_home_cad.cad_facts_hash(
        whole_home_engine.normalize_model(hybrid, source='cad')) == hybrid['cad_facts_hash']
    assert whole_home_engine.normalize_model(hybrid, source='cad')[
        'cad_semantic_derivation']['method'] == 'gemini_room_polygon_on_audited_cad_raster_v1'

    semantic = copy.deepcopy(hybrid)
    semantic['fixed_objects'].append({
        'id': 'lamp-proxy', 'name': 'AI 灯具代理', 'kind': 'fixed_furniture',
        'semantic_role': 'fixed_furniture', 'room_id': 'living',
        'position': {'x': 1, 'y': 0, 'z': 1}, 'size': {'x': .3, 'y': 1.5, 'z': .3},
        'source': 'ai', 'purpose': 'layout_proxy', 'observed': False,
        'review_status': 'accepted',
    })
    semantic = whole_home_engine.normalize_model(semantic, source='cad')
    assert whole_home_cad.validate_cad_semantic_overlay(hybrid, semantic)['hard_errors'] == []


def test_hybrid_reference_anchor_report_excludes_unknown_opening_without_reusing_stale_orphans():
    model = {
        'fixed_objects': [{
            'id': 'cad_sofa', 'room_id': 'living', 'reference_anchor_ready': True,
        }],
        'openings': [{
            'id': 'cad_unknown_door', 'reference_anchor_ready': False,
            'reference_anchor_blockers': ['cad_opening_extent_unknown'],
        }],
        'room_contracts': [{
            'room_id': 'living', 'status': 'complete', 'missing_role_groups': [],
        }],
        'reference_anchor_report': {
            'status': 'blocked',
            'hard_errors': [{'code': 'cad_fixed_object_room_not_unique', 'object_id': 'cad_sofa'}],
        },
    }
    report = whole_home_cad.refresh_hybrid_reference_anchor_report(model)
    assert report['status'] == 'ready'
    assert report['hard_errors'] == []
    assert report['excluded_opening_ids'] == ['cad_unknown_door']
    assert report['warnings'][0]['openings'][0]['action'] == (
        'retained_as_cad_evidence_excluded_from_reference_camera_anchors')

    model['fixed_objects'][0].update(
        reference_anchor_ready=False,
        reference_anchor_blockers=['cad_fixed_object_extent_unknown'],
    )
    report = whole_home_cad.refresh_hybrid_reference_anchor_report(model)
    assert report['status'] == 'blocked'
    assert report['hard_errors'][0]['object_id'] == 'cad_sofa'


def test_cad_project_runtime_refreshes_stale_raw_anchor_report_without_read_side_write(monkeypatch):
    project_id = 'cad-runtime-anchor-refresh'
    stored = {
        'project_id': project_id,
        'source_type': 'cad',
        'model': {
            'cad_semantic_derivation': {
                'method': 'gemini_room_polygon_on_audited_cad_raster_v1',
            },
            'fixed_objects': [{
                'id': 'cad_bed', 'room_id': 'bedroom', 'reference_anchor_ready': True,
            }],
            'openings': [],
            'room_contracts': [{
                'room_id': 'bedroom', 'status': 'complete', 'missing_role_groups': [],
            }],
            'reference_anchor_report': {
                'status': 'blocked',
                'hard_errors': [{'code': 'cad_fixed_object_room_not_unique', 'object_id': 'cad_bed'}],
            },
        },
    }
    monkeypatch.setattr(routes_whole_home, 'load_project', lambda value: copy.deepcopy(stored))
    runtime = routes_whole_home._project_entry(project_id)
    assert runtime['model']['reference_anchor_report']['status'] == 'ready'
    assert stored['model']['reference_anchor_report']['status'] == 'blocked'


def test_cad_project_creation_is_local_only_and_persists_failed_evidence(monkeypatch):
    model, report = _cad_model(), _cad_report()
    report['source'] = {'format': 'dxf', 'version': 'AC1032', 'sha256': 'a' * 64}
    report['conversion'] = {'status': 'not_required'}
    persisted = []
    monkeypatch.setattr(routes_whole_home, '_persist_project', lambda project: persisted.append(project.copy()))
    monkeypatch.setattr(routes_whole_home, 'require_managed_cad_path', lambda path: path)
    monkeypatch.setattr(routes_whole_home, '_sha256_file', lambda path: 'a' * 64)
    monkeypatch.setattr(routes_whole_home, 'ingest_cad',
                        lambda path, project_id: (model, report, 'preview.png'))
    monkeypatch.setattr(routes_whole_home, 'load_config',
                        lambda: pytest.fail('CAD create must not load Gemini credentials'))
    request = server_schemas.WholeHomeProjectRequest(
        cad_path='managed.dxf', reference_url='https://vr.justeasy.cn/view/16770314-test')
    result = asyncio.run(routes_whole_home.create_whole_home_project(request))
    assert result['status'] == 'done'
    assert result['source_type'] == 'cad'
    assert result['cad_source'] == {
        'name': 'managed.dxf', 'sha256': 'a' * 64,
        'format': 'dxf', 'version': 'AC1032', 'size_bytes': 0,
    }
    assert 'path' not in result['cad_source']
    assert result['reference_contract']['contract_id'] == 'justeasy_16770314_static_v1'
    assert persisted[-1]['cad_import']['cad_facts_hash'] == whole_home_cad.cad_facts_hash(model)

    blocked_report = {'hard_errors': [{'code': 'cad_no_closed_regions'}]}
    monkeypatch.setattr(routes_whole_home, 'ingest_cad', lambda *args, **kwargs: (_ for _ in ()).throw(
        whole_home_cad.CadError('cad_hard_review_required', 'blocked',
                                details={'parse_report': blocked_report, 'model': model})))
    result = asyncio.run(routes_whole_home.create_whole_home_project(
        server_schemas.WholeHomeProjectRequest(cad_path='managed.dxf')))
    assert result['status'] == 'needs_review'
    assert result['stage'].startswith('CAD 已生成可检查的 3D 草稿')
    assert result['model']['schema_version'] == model['schema_version']
    assert result['model']['walls'] == model['walls']
    assert result['revision'] == 1
    assert result['error'] == ''
    assert result['parse_report']['hard_error_summary'][0]['code'] == 'cad_no_closed_regions'
    assert persisted[-1]['cad_candidate_model_summary']['room_count'] == 1
    assert persisted[-1]['cad_space_draft_pointer']['sha256']
    assert persisted[-1]['operations'][-1]['type'] == 'cad_import_needs_review'


def test_cad_project_with_unresolved_wall_assemblies_is_not_reported_done(monkeypatch):
    model, report = _cad_model(), _cad_report()
    report['source'] = {'format': 'dxf', 'version': 'AC1032', 'sha256': 'a' * 64}
    report['conversion'] = {'status': 'not_required'}
    report['alignment_metrics'] = {
        'production_unresolved_wall_assembly_count': 7,
    }
    persisted = []
    monkeypatch.setattr(routes_whole_home, '_persist_project',
                        lambda project: persisted.append(copy.deepcopy(project)))
    monkeypatch.setattr(routes_whole_home, 'require_managed_cad_path', lambda path: path)
    monkeypatch.setattr(routes_whole_home, '_sha256_file', lambda path: 'a' * 64)
    monkeypatch.setattr(routes_whole_home, 'ingest_cad',
                        lambda path, project_id: (model, report, 'preview.png'))

    result = asyncio.run(routes_whole_home.create_whole_home_project(
        server_schemas.WholeHomeProjectRequest(cad_path='managed.dxf')))

    assert result['status'] == 'needs_review'
    assert '7 个墙体证据待解决' in result['stage']
    assert persisted[-1]['operations'][-1]['type'] == 'cad_import_local_needs_review'
    assert (persisted[-1]['operations'][-1]['payload']
            ['production_unresolved_wall_assembly_count'] == 7)


def test_cad_report_summary_preserves_public_unit_and_inventory_counts(tmp_path):
    report_path = tmp_path / 'report.json'
    report_path.write_text('{}', encoding='utf-8')
    summary = whole_home_cad.cad_report_summary({
        'report_path': str(report_path), 'insunits': 4,
        'resolved_insunits': 6, 'declared_unit_scale_to_m': .001,
        'unit_scale_to_m': 1.0,
        'unit_resolution': {
            'method': 'cad_explicit_annotation_unit_resolution_v1',
            'declared_insunits': 4, 'resolved_insunits': 6,
        },
        'structural_entity_count': 619,
        'selected_structural_entity_count': 233,
        'ignored_nonstructural_count': 350,
        'layers': {'A-WALL': 233, 'A-TEXT': 63},
        'blocks': {'door': 12, 'window': 7, 'bed': 3},
    })
    assert summary['insunits'] == 4
    assert summary['resolved_insunits'] == 6
    assert summary['declared_unit_scale_to_m'] == .001
    assert summary['unit_scale_to_m'] == 1.0
    assert summary['unit_resolution']['method'] == 'cad_explicit_annotation_unit_resolution_v1'
    assert summary['structural_entity_count'] == 619
    assert summary['selected_structural_entity_count'] == 233
    assert summary['ignored_nonstructural_count'] == 350
    assert summary['layer_count'] == 2
    assert summary['block_count'] == 3


def test_annotation_unit_resolution_uses_two_explicit_room_dimensions_only():
    selected = {'candidate_id': 'cad_plan_1', 'bbox_m': [0, 0, .16002, .19177]}
    texts = [
        {'text': "BEDROOM 10'-2\" X 9'-0\"", 'point_m': [.05, .06],
         'cad_provenance': {'handle': 'A1'}},
        {'text': "KITCHEN 8'-0\" X 6'-6\"", 'point_m': [.10, .12],
         'cad_provenance': {'handle': 'A2'}},
    ]

    resolution = whole_home_cad._infer_annotation_unit_resolution(
        selected, texts, declared_units=1, declared_scale=.0254)

    assert resolution['resolved_insunits'] == 6
    assert resolution['resolved_metres_per_unit'] == 1.0
    assert resolution['scale_correction_factor'] == pytest.approx(1 / .0254)
    assert len(resolution['dimension_evidence']) == 2
    assert resolution['area_evidence'] == []
    assert resolution['candidate_decisions'][-1]['unit'] == 'm'
    assert resolution['candidate_decisions'][-1]['accepted_by'] == ['room_dimension_fit']


def test_annotation_unit_resolution_accepts_total_area_in_bounded_title_margin():
    selected = {'candidate_id': 'cad_plan_1', 'bbox_m': [0, 0, .18552, .21057]}
    texts = [{
        'text': '645 SQ.ft total area', 'point_m': [.02, -.045],
        'cad_provenance': {'handle': 'AREA1'},
    }]

    resolution = whole_home_cad._infer_annotation_unit_resolution(
        selected, texts, declared_units=1, declared_scale=.0254)

    assert resolution['resolved_insunits'] == 6
    assert resolution['resolved_metres_per_unit'] == 1.0
    assert resolution['dimension_evidence'] == []
    assert resolution['area_evidence'][0]['area_m2'] == pytest.approx(59.922461)
    assert (resolution['area_evidence'][0]['annotation_scope']
            == 'selected_plan_bbox_plus_25pct_margin')
    assert resolution['candidate_decisions'][-1]['accepted_by'] == ['total_area_fit']


def test_annotation_unit_resolution_fails_closed_without_unique_explicit_evidence():
    selected = {'candidate_id': 'cad_plan_1', 'bbox_m': [0, 0, .254, .254]}
    # A single room dimension is deliberately insufficient and the area label
    # sits beyond the bounded 25% annotation margin.
    texts = [
        {'text': "ROOM 6'-0\" X 6'-0\"", 'point_m': [.1, .1]},
        {'text': '100 SQ.ft', 'point_m': [.1, -.2]},
    ]
    assert whole_home_cad._infer_annotation_unit_resolution(
        selected, texts, declared_units=1, declared_scale=.0254) is None


def test_metric_plan_metadata_unit_resolution_requires_independent_cad_evidence():
    selected = {'candidate_id': 'cad_plan_1', 'bbox_m': [0, 0, .3556, .3302]}
    texts = [{
        'text': 'Ground Floor Plan', 'point_m': [.15, -.01],
        'text_height_m': .2 * .0254,
        'cad_provenance': {'handle': 'TITLE1'},
    }]
    styles = [{
        'name': 'Standard', 'dimscale': 1.0, 'dimlfac': 1.0,
        'dimtxt': .18, 'dimasz': .18,
    }]

    resolution = whole_home_cad._infer_metric_plan_metadata_unit_resolution(
        selected, texts, styles, measurement_system=1,
        declared_units=1, declared_scale=.0254)

    assert resolution['method'] == 'cad_metric_plan_metadata_unit_resolution_v1'
    assert resolution['resolved_insunits'] == 6
    assert resolution['resolved_metres_per_unit'] == 1.0
    assert resolution['floor_plan_title_evidence'][0]['handle'] == 'TITLE1'
    assert resolution['candidate_decisions'][-1]['accepted_by'] == [
        'metric_measurement_header',
        'associated_floor_plan_title_height',
        'unit_scale_one_dimension_style',
        'bounded_modelspace_extent',
    ]

    assert whole_home_cad._infer_metric_plan_metadata_unit_resolution(
        selected, texts, styles, measurement_system=0,
        declared_units=1, declared_scale=.0254) is None
    assert whole_home_cad._infer_metric_plan_metadata_unit_resolution(
        selected, [{**texts[0], 'text_height_m': 0}], styles,
        measurement_system=1, declared_units=1, declared_scale=.0254) is None
    assert whole_home_cad._infer_metric_plan_metadata_unit_resolution(
        selected, texts, [{**styles[0], 'dimscale': 100}],
        measurement_system=1, declared_units=1, declared_scale=.0254) is None


def test_metric_plan_metadata_unit_resolution_accepts_room_anchors_and_metric_dimensions():
    selected = {
        'candidate_id': 'cad_plan_1',
        'bbox_m': [.07568, .27338, .38123, .78138],
    }
    texts = [
        {'text': 'LIVING', 'point_m': [.18545, .45271],
         'text_height_m': .125 * .0254,
         'cad_provenance': {'handle': 'FCC'}},
        {'text': 'KITCHEN', 'point_m': [.25476, .37551],
         'text_height_m': .125 * .0254,
         'cad_provenance': {'handle': 'FCD'}},
        {'text': 'BEDROOM 1', 'point_m': [.25221, .45982],
         'text_height_m': .125 * .0254,
         'cad_provenance': {'handle': 'FCF'}},
        {'text': 'BEDROOM 2', 'point_m': [.17258, .50587],
         'text_height_m': .125 * .0254,
         'cad_provenance': {'handle': 'FD0'}},
    ]
    styles = [{
        'name': 'Standard', 'dimscale': 1.0, 'dimlfac': 1.0,
        'dimtxt': .18, 'dimasz': .18,
    }]
    dimensions = [
        {'raw_measurement': 10.0, 'text_override': '<>m',
         'dimension_style': 'EJES', 'cad_provenance': {'handle': 'FD2'}},
        {'raw_measurement': 20.0, 'text_override': '<>m',
         'dimension_style': 'EJES', 'cad_provenance': {'handle': 'FD3'}},
    ]

    resolution = whole_home_cad._infer_metric_plan_metadata_unit_resolution(
        selected, texts, styles, dimensions, measurement_system=1,
        declared_units=1, declared_scale=.0254)

    assert resolution['resolved_insunits'] == 6
    assert resolution['resolved_metres_per_unit'] == 1.0
    assert resolution['floor_plan_title_evidence'] == []
    assert len(resolution['room_anchor_evidence']) == 4
    assert resolution['room_anchor_semantic_profiles'] == [
        'bedroom', 'kitchen', 'living_room']
    assert len(resolution['explicit_metric_dimension_evidence']) == 2
    assert resolution['candidate_decisions'][-1]['accepted_by'] == [
        'metric_measurement_header',
        'associated_room_label_heights',
        'explicit_metric_dimension_overrides',
        'multiple_room_semantic_profiles',
        'unit_scale_one_dimension_style',
        'bounded_modelspace_extent',
    ]

    assert whole_home_cad._infer_metric_plan_metadata_unit_resolution(
        selected, texts, styles, dimensions[:1], measurement_system=1,
        declared_units=1, declared_scale=.0254) is None
    assert whole_home_cad._infer_metric_plan_metadata_unit_resolution(
        selected, texts[:2], styles, dimensions, measurement_system=1,
        declared_units=1, declared_scale=.0254) is None
    assert whole_home_cad._infer_metric_plan_metadata_unit_resolution(
        selected, [*texts, {
            'text': 'FRONT ELEVATION', 'point_m': [.3, .6],
            'text_height_m': .3 * .0254,
            'cad_provenance': {'handle': 'NEG'},
        }], styles, dimensions, measurement_system=1,
        declared_units=1, declared_scale=.0254) is None


def test_ingest_cad_enriches_and_repersists_hard_review_evidence(monkeypatch, tmp_path):
    source = tmp_path / 'managed.dwg'
    source.write_bytes(b'AC1021' + b'\0' * 64)
    dxf = tmp_path / 'converted.dxf'
    dxf.write_bytes(ASCII_DXF)
    model, report = _cad_model(), _cad_report()
    old_report = tmp_path / 'old-report.json'
    old_report.write_text('{}', encoding='utf-8')
    report['report_path'] = str(old_report)

    monkeypatch.setattr(whole_home_cad, 'require_managed_cad_path', lambda path: str(source))
    monkeypatch.setattr(whole_home_cad, 'inspect_cad_file', lambda path: {
        'format': 'dwg', 'version': 'AC1021', 'sha256': 'a' * 64,
    })
    monkeypatch.setattr(whole_home_cad, 'convert_dwg_to_ascii_dxf',
                        lambda *args, **kwargs: (str(dxf), {
                            'status': 'converted', 'output_path': str(dxf)}))
    monkeypatch.setattr(whole_home_cad, 'parse_dxf', lambda *args, **kwargs: (
        _ for _ in ()).throw(whole_home_cad.CadError(
            'cad_hard_review_required', 'review', status_code=409,
            details={'parse_report': copy.deepcopy(report), 'model': copy.deepcopy(model)})))
    monkeypatch.setattr(whole_home_cad, 'render_cad_floorplan_preview',
                        lambda *args, **kwargs: 'preview.png')
    captured = {}

    def persist(project_id, value, purpose='derived'):
        captured.update(copy.deepcopy(value))
        return {**copy.deepcopy(value), 'report_path': 'enriched-report.json'}

    monkeypatch.setattr(whole_home_cad, 'persist_cad_report', persist)
    with pytest.raises(whole_home_cad.CadError) as error:
        whole_home_cad.ingest_cad(str(source), 'home-review')
    assert error.value.code == 'cad_hard_review_required'
    assert error.value.status_code == 409
    enriched = error.value.details['parse_report']
    assert enriched['source']['sha256'] == 'a' * 64
    assert enriched['conversion']['output_path'] == str(dxf)
    assert enriched['preview_path'] == 'preview.png'
    assert enriched['report_path'] == 'enriched-report.json'
    assert captured['report_path'] == str(old_report)


def test_cad_project_creation_without_audit_draft_remains_failed(monkeypatch):
    persisted = []
    monkeypatch.setattr(
        routes_whole_home, '_persist_project',
        lambda project: persisted.append(copy.deepcopy(project)))
    monkeypatch.setattr(routes_whole_home, 'require_managed_cad_path', lambda path: path)
    monkeypatch.setattr(routes_whole_home, 'ingest_cad', lambda *args, **kwargs: (
        _ for _ in ()).throw(whole_home_cad.CadError(
            'dxf_parse_failed', 'invalid source', details={})))
    result = asyncio.run(routes_whole_home.create_whole_home_project(
        server_schemas.WholeHomeProjectRequest(cad_path='managed.dxf')))
    assert result['status'] == 'failed'
    assert result['error'] == 'invalid source'
    assert not result['model'].get('schema_version')
    assert not result['model']['walls']
    assert not result['model']['rooms']
    assert persisted[-1]['operations'][-1]['type'] == 'cad_import_failed'


def test_cad_generic_model_put_is_rejected(monkeypatch):
    project = {'project_id': 'cad-project', 'source_type': 'cad', 'revision': 1}
    monkeypatch.setattr(routes_whole_home, '_project_entry', lambda project_id: project)
    request = server_schemas.WholeHomeModelSaveRequest(base_revision=1, model={})
    with pytest.raises(HTTPException) as error:
        routes_whole_home.save_whole_home_model('cad-project', request)
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'cad_geometry_read_only'


def test_cad_reparse_returns_202_without_waiting_for_ingest(monkeypatch):
    model = _cad_model()
    project = {
        'project_id': 'cad-project', 'source_type': 'cad', 'revision': 3,
        'verified': True, 'verified_revision': 3, 'model': model,
        'cad_source': {'path': 'managed.dxf'}, 'parse_report': _cad_report(),
        'captures': [], 'operations': [],
    }
    monkeypatch.setattr(routes_whole_home, 'load_project', lambda project_id: project)
    monkeypatch.setattr(routes_whole_home, 'require_managed_cad_path', lambda path: path)
    monkeypatch.setattr(routes_whole_home, '_sha256_file', lambda path: 'source-hash')
    monkeypatch.setattr(routes_whole_home, 'create_cad_reparse_operation', lambda *args, **kwargs: ({
        'project_id': 'cad-project', 'operation_id': 'operation-1', 'status': 'queued',
        'base_revision': 3, 'base_state_hash': whole_home_engine.state_hash(project),
        'candidate_id': '', 'created_at': 1, 'stage': 'queued', 'progress': 0,
    }, True))
    spawned = []
    def capture_spawn(coro):
        spawned.append(coro)
        coro.close()
    monkeypatch.setattr(routes_whole_home.state, 'spawn', capture_spawn)
    result = asyncio.run(routes_whole_home.reparse_whole_home_cad(
        'cad-project', server_schemas.WholeHomeCadReparseRequest(base_revision=3)))
    assert result.status_code == 202
    assert json.loads(result.body)['status'] == 'queued'
    assert len(spawned) == 1


def test_cad_hash_mismatch_blocks_before_floor_or_api_key(monkeypatch):
    model = _cad_model()
    project = {
        'project_id': 'cad-project', 'source_type': 'cad', 'verified': True,
        'model': model, 'cad_import': {'cad_facts_hash': 'tampered'},
        'parse_report': _cad_report(),
    }
    monkeypatch.setattr(routes_whole_home, '_project_entry', lambda project_id: project)
    monkeypatch.setattr(routes_whole_home, 'list_learning_runs', lambda project_id='': [])
    monkeypatch.setattr(routes_whole_home, 'require_upload_image_path',
                        lambda *args, **kwargs: pytest.fail('CAD gate must run before input resolution'))
    monkeypatch.setattr(routes_whole_home, 'load_config',
                        lambda: pytest.fail('CAD gate must run before credentials'))
    request = server_schemas.WholeHomeRunRequest(
        project_id='cad-project', capture_ids=['capture'], floor_path='floor.png')
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes_whole_home._create_whole_home_run(request))
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'cad_facts_hash_mismatch'


def test_cad_semantic_layout_discards_geometry_mutation(monkeypatch):
    model = _cad_model()
    project = {
        'project_id': 'cad-project', 'source_type': 'cad', 'revision': 1,
        'floorplan_path': '', 'model': model, 'parse_report': _cad_report(),
        'cad_import': {'cad_facts_hash': whole_home_cad.cad_facts_hash(model)},
        'captures': [], 'operations': [],
    }
    mutated = copy.deepcopy(model)
    mutated['walls'][0]['end']['x'] += .25
    monkeypatch.setattr(routes_whole_home, '_project_entry', lambda project_id: project)
    monkeypatch.setattr(routes_whole_home, 'analyze_semantic_layout',
                        lambda *args: (mutated, '', 'gemini-test'))
    monkeypatch.setattr(routes_whole_home, '_persist_project',
                        lambda project: pytest.fail('mutated semantic result must not persist'))
    request = server_schemas.WholeHomeSemanticLayoutRequest(base_revision=1, api_key='test-key')
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes_whole_home.rebuild_whole_home_semantic_layout('cad-project', request))
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'cad_semantic_facts_changed'


def test_reference_schema_keeps_two_living_axes_and_nine_slots_times_two_models():
    slots = whole_home_cad.JUSTEASY_REFERENCE_CONTRACT['slots']
    groups = [
        server_schemas.WholeHomeCaptureGroup(
            room_id=('living' if slot['slot_id'].startswith('living_') else f'room-{index}'),
            slot_id=slot['slot_id'], primary_capture_id=f'capture-{index}')
        for index, slot in enumerate(slots, 1)
    ]
    request = server_schemas.WholeHomeRunRequest(
        project_id='project', material_mode='reference',
        reference_contract_id='justeasy_16770314_static_v1', capture_groups=groups)
    rows = routes_whole_home._result_rows(
        request,
        [{
            'room_id': group.room_id, 'slot_id': group.slot_id,
            'primary_capture_id': group.primary_capture_id, 'fallback_capture_ids': [],
        } for group in groups],
        legacy=False,
    )
    assert len(rows) == 18
    living_slots = {row['slot_id'] for row in rows if row['room_id'] == 'living'}
    assert living_slots == {'living_openplan_axis', 'living_tv_window_axis'}
    assert {row['model_key'] for row in rows} == {'b2', 'pro'}

    with pytest.raises(ValidationError, match='at most one group per slot'):
        server_schemas.WholeHomeRunRequest(
            project_id='project', material_mode='reference',
            reference_contract_id='justeasy_16770314_static_v1',
            capture_groups=[groups[0], groups[0]],
        )


def test_reference_contract_has_nine_unique_audited_assets_and_scenes():
    slots = whole_home_cad.JUSTEASY_REFERENCE_CONTRACT['slots']
    assets = [slot['reference_asset'] for slot in slots]
    viewpoints = [slot['reference_viewpoint'] for slot in slots]
    assert len(slots) == 9
    assert len({row['asset_id'] for row in assets}) == 9
    assert len({row['sha256'] for row in assets}) == 9
    assert len({row['scene_id'] for row in viewpoints}) == 9
    assert all(row['expected_mime'] == 'image/jpeg' for row in assets)
    assert all((row['expected_width'], row['expected_height']) == (500, 500) for row in assets)
    assert all('?' not in row['public_thumb_url'] for row in assets)
    assert all(row['export_allowed'] is False for row in assets)
    assert all(row['point_mapping']['status'] == 'not_available' for row in viewpoints)
    assert all(row['landing_policy']['mode'] == 'cad_semantic_relative_region' for row in viewpoints)
    assert all(row['yaw_policy'] == 'flexible' for row in viewpoints)


def test_reference_explicit_second_living_and_bed_slots_never_use_name_heuristics():
    contract = whole_home_cad.JUSTEASY_REFERENCE_CONTRACT
    living = whole_home_cad.reference_slot_for_room(
        contract, {'label': '客厅', 'reference_room_profile': 'living_room'},
        {'name': '机位 1'}, reference_slot_id='living_tv_window_axis', require_explicit=True)
    bedroom = whole_home_cad.reference_slot_for_room(
        contract, {'label': '次卧', 'reference_room_profile': 'bedroom_secondary'},
        {'name': '机位 1'}, reference_slot_id='secondary_bed_dark_headwall', require_explicit=True)
    assert living['reference_asset']['asset_id'] == '02_living_b'
    assert bedroom['reference_asset']['asset_id'] == '06_secondary_bed_b'
    assert whole_home_cad.reference_slot_for_room(
        contract, {'label': '客厅'}, {'name': '机位 2'}, require_explicit=True) == {}


def test_reference_asset_resolver_is_local_only_and_fail_closed(tmp_path):
    contract, root = _reference_asset_fixture(tmp_path)
    resolved = whole_home_cad.resolve_reference_assets(
        contract, require_all=True, asset_root=str(root))
    assets = [slot['reference_asset'] for slot in resolved['slots']]
    assert all(row['status'] == 'verified' for row in assets)
    assert all((row['width'], row['height'], row['mime']) == (500, 500, 'image/jpeg') for row in assets)
    public = whole_home_cad.public_reference_contract(resolved)
    assert all('local_path' not in slot['reference_asset'] for slot in public['slots'])

    missing = copy.deepcopy(contract)
    os.remove(root / missing['contract_id'] / missing['slots'][0]['reference_asset']['filename'])
    with pytest.raises(whole_home_cad.CadError) as error:
        whole_home_cad.resolve_reference_assets(missing, require_all=True, asset_root=str(root))
    assert error.value.code == 'reference_assets_unavailable'
    assert error.value.details['hard_errors'][0]['code'] == 'missing'


def test_reference_asset_resolver_rejects_tamper_and_wrong_dimensions(tmp_path):
    contract, root = _reference_asset_fixture(tmp_path)
    first = contract['slots'][0]['reference_asset']
    (root / contract['contract_id'] / first['filename']).write_bytes(b'tampered')
    with pytest.raises(whole_home_cad.CadError) as tamper:
        whole_home_cad.resolve_reference_assets(contract, require_all=True, asset_root=str(root))
    assert any(row['code'] == 'sha256_mismatch' for row in tamper.value.details['hard_errors'])

    contract, root = _reference_asset_fixture(tmp_path / 'dimensions', size=(640, 500))
    with pytest.raises(whole_home_cad.CadError) as dimensions:
        whole_home_cad.resolve_reference_assets(contract, require_all=True, asset_root=str(root))
    assert all(row['code'] == 'dimension_mismatch' for row in dimensions.value.details['hard_errors'])


def test_reference_learning_coverage_is_slot_scoped_across_same_room(monkeypatch):
    run = {
        'run_id': 'run', 'project_id': 'project', 'workflow_id': 'workflow',
        'generation_spec_hash': 'spec', 'created_at': 1,
        'material_mode': 'reference',
        'capture_groups': [
            {'room_id': 'living', 'slot_id': 'living_openplan_axis'},
            {'room_id': 'living', 'slot_id': 'living_tv_window_axis'},
        ],
        'results': [],
    }
    artifacts = [
        {'artifact_id': 'a', 'result_id': 'ra', 'room_id': 'living',
         'slot_id': 'living_openplan_axis', 'model_key': 'b2'},
        {'artifact_id': 'b', 'result_id': 'rb', 'room_id': 'living',
         'slot_id': 'living_tv_window_axis', 'model_key': 'b2'},
    ]
    monkeypatch.setattr(whole_home_learning, 'list_learning_runs', lambda project_id='': [run])
    monkeypatch.setattr(whole_home_learning, 'enumerate_reviewable_artifacts', lambda value: artifacts)
    monkeypatch.setattr(whole_home_learning, '_feedback', lambda value: {
        'current': {'artifact_reviews': {'a': {'review_status': 'pass'}}}})
    monkeypatch.setattr(whole_home_learning, 'load_project', lambda project_id: {
        'project_id': project_id, 'verified': True,
        'model': {'rooms': [{'id': 'living', 'selected': True}]},
    })
    monkeypatch.setattr(whole_home_learning, 'get_training_consent', lambda project_id: {'allowed': False})
    summary = whole_home_learning.learning_summary('project')
    assert summary['coverage_scope'] == 'reference_slot'
    assert summary['covered_target_ids'] == ['living_openplan_axis']
    assert summary['uncovered_target_ids'] == ['living_tv_window_axis']
    assert summary['selected_room_count'] == 2


def test_reference_prompt_has_cad_authority_and_no_floor_product_replacement(tmp_path):
    reference_path = tmp_path / '01_living_a.jpg'
    reference_path.write_bytes(b'audited-reference-fixture')
    reference_contract = copy.deepcopy(whole_home_cad.JUSTEASY_REFERENCE_CONTRACT)
    reference_contract['slots'][0]['reference_asset'].update(
        status='verified', local_path=str(reference_path), width=500, height=500,
        mime='image/jpeg')
    project = {
        'model': _cad_model(),
        'reference_contract': reference_contract,
    }
    capture = {
        'room_id': 'living', 'reference_slot_id': 'living_openplan_axis',
        'camera': {
            'id': 'camera', 'name': 'living axis', 'room_id': 'living',
            'reference_slot_id': 'living_openplan_axis',
            'position': {'x': 1, 'y': 1.45, 'z': 1},
            'target': {'x': 3, 'y': 1.45, 'z': 1}, 'focal_length_mm': 24,
        },
    }
    capture.update(structure_path='structure.png', rgb_path='rgb.png', depth_path='depth.png',
                   normal_path='normal.png', edge_path='edge.png', semantic_path='semantic.png')
    prompt, material_paths = whole_home_engine.build_generation_prompt(
        project, capture, {
            'material_mode': 'reference', 'style': 'style', 'lighting': 'daylight',
            'reference_contract_snapshot': reference_contract,
        }, pass_name='material')
    assert 'CAD' in prompt
    assert 'style and composition only' in prompt
    assert 'not flooring-product mode' in prompt
    assert material_paths == [
        'structure.png', 'rgb.png', 'depth.png', 'semantic.png', str(reference_path)]
    structure_prompt, structure_paths = whole_home_engine.build_generation_prompt(
        project, capture, {
            'material_mode': 'reference', 'style': 'style', 'lighting': 'daylight',
            'reference_contract_snapshot': reference_contract,
        }, pass_name='structure')
    assert 'must never substitute a flooring product' in structure_prompt
    assert structure_paths == [
        'rgb.png', 'depth.png', 'normal.png', 'edge.png', 'semantic.png', str(reference_path)]
    assert 'square 1:1 thumbnail' in structure_prompt
    assert 'native horizontal 4:3' in structure_prompt
    assert 'Never crop, stretch, pad or reproduce' in structure_prompt


def test_reference_qa_receives_only_current_slot_reference_after_cad_buffers(monkeypatch, tmp_path):
    current = tmp_path / '01_living_a.jpg'
    other = tmp_path / '02_living_b.jpg'
    current.write_bytes(b'current-slot')
    other.write_bytes(b'other-slot')
    contract = copy.deepcopy(whole_home_cad.JUSTEASY_REFERENCE_CONTRACT)
    contract['slots'][0]['reference_asset'].update(
        status='verified', local_path=str(current), width=500, height=500, mime='image/jpeg')
    contract['slots'][1]['reference_asset'].update(
        status='verified', local_path=str(other), width=500, height=500, mime='image/jpeg')
    project = {'model': _cad_model(), 'reference_contract': contract}
    capture = {
        'room_id': 'living', 'reference_slot_id': 'living_openplan_axis',
        'material_mode': 'reference',
        'camera': {
            'id': 'camera', 'room_id': 'living',
            'reference_slot_id': 'living_openplan_axis',
            'position': {'x': 1, 'y': 1.45, 'z': 1},
            'target': {'x': 3, 'y': 1.45, 'z': 1}, 'focal_length_mm': 24,
        },
        'plan_overlay_path': 'plan.png', 'rgb_path': 'rgb.png', 'depth_path': 'depth.png',
        'normal_path': 'normal.png', 'edge_path': 'edge.png', 'semantic_path': 'semantic.png',
    }
    observed = {}

    def fake_qa(api_key, prompt, paths, schema, max_output_tokens=0):
        observed['prompt'] = prompt
        observed['paths'] = paths
        return ({
            'checks': [], 'hard_fail': False, 'summary': 'fixture',
            'geometry_score': 90, 'camera_score': 90, 'opening_score': 90,
            'material_score': 90, 'room_identity_score': 90, 'fixed_object_score': 90,
        }, None)

    monkeypatch.setattr(whole_home_engine, 'call_gemini_json', fake_qa)
    evaluation, error = whole_home_engine.evaluate_whole_home_phase(
        'key', project, capture, 'candidate.png', '', phase='structure')
    assert error is None
    assert observed['paths'] == [
        'plan.png', 'rgb.png', 'depth.png', 'normal.png', 'edge.png', 'semantic.png',
        str(current), 'candidate.png',
    ]
    assert str(other) not in observed['paths']
    assert 'only geometry authority' in observed['prompt']
    assert evaluation['reference_asset']['asset_id'] == '01_living_a'
    assert evaluation['reference_asset']['scene_id'] == 279876079


def test_reference_run_asset_and_viewpoint_gates_precede_credentials(monkeypatch, tmp_path):
    request = server_schemas.WholeHomeRunRequest(
        project_id='project', material_mode='reference',
        reference_contract_id='justeasy_16770314_static_v1',
        capture_groups=[server_schemas.WholeHomeCaptureGroup(
            room_id='living', slot_id='living_openplan_axis', primary_capture_id='capture')],
    )
    project = {
        'project_id': 'project', 'source_type': 'import', 'verified': True,
        'model': _cad_model(), 'reference_contract': copy.deepcopy(
            whole_home_cad.JUSTEASY_REFERENCE_CONTRACT),
        'captures': [],
    }
    monkeypatch.setattr(routes_whole_home, '_existing_idempotent_run', lambda *args, **kwargs: None)
    monkeypatch.setattr(routes_whole_home, '_project_entry', lambda project_id: copy.deepcopy(project))
    monkeypatch.setattr(whole_home_cad, 'REFERENCE_ASSET_ROOT', str(tmp_path / 'missing-assets'))
    monkeypatch.setattr(routes_whole_home, 'load_config',
                        lambda: pytest.fail('credentials must not load before reference asset gate'))
    with pytest.raises(HTTPException) as missing:
        asyncio.run(routes_whole_home._create_whole_home_run(request))
    assert missing.value.status_code == 409
    assert missing.value.detail['code'] == 'reference_assets_unavailable'

    contract, root = _reference_asset_fixture(tmp_path / 'verified')
    camera = {
        'id': 'camera', 'room_id': 'living', 'reference_slot_id': 'living_openplan_axis',
        'position': {'x': 1, 'y': 1.45, 'z': 1},
        'target': {'x': 3, 'y': 1.45, 'z': 1}, 'focal_length_mm': 24,
    }
    project.update(reference_contract=contract, captures=[{
        'capture_id': 'capture', 'room_id': 'living', 'reference_slot_id': 'living_openplan_axis',
        'camera_id': 'camera', 'camera': camera,
        'edge_path': str(root / contract['contract_id'] /
                         contract['slots'][0]['reference_asset']['filename']),
    }])
    monkeypatch.setattr(whole_home_cad, 'REFERENCE_ASSET_ROOT', str(root))
    monkeypatch.setattr(routes_whole_home, '_valid_capture', lambda *args: True)
    with pytest.raises(HTTPException) as viewpoint:
        asyncio.run(routes_whole_home._create_whole_home_run(request))
    assert viewpoint.value.status_code == 409
    assert viewpoint.value.detail['code'] == 'reference_slot_camera_missing'
    assert 'slot/scene/relative-landing' in viewpoint.value.detail['message']


def test_reference_run_rejects_generic_style_reference_before_credentials(monkeypatch):
    request = server_schemas.WholeHomeRunRequest(
        project_id='project', material_mode='reference', style_ref_path='generic.jpg',
        reference_contract_id='justeasy_16770314_static_v1',
        capture_groups=[server_schemas.WholeHomeCaptureGroup(
            room_id='living', slot_id='living_openplan_axis', primary_capture_id='capture')],
    )
    monkeypatch.setattr(routes_whole_home, '_existing_idempotent_run', lambda *args, **kwargs: None)
    monkeypatch.setattr(routes_whole_home, '_project_entry', lambda project_id: {
        'project_id': 'project', 'source_type': 'import', 'verified': True,
        'model': _cad_model(), 'reference_contract': copy.deepcopy(
            whole_home_cad.JUSTEASY_REFERENCE_CONTRACT), 'captures': [],
    })
    monkeypatch.setattr(routes_whole_home, 'load_config',
                        lambda: pytest.fail('credentials must not load before style-ref rejection'))
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes_whole_home._create_whole_home_run(request))
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'reference_generic_style_ref_forbidden'


def test_review_manifest_snapshots_cad_reference_and_redacts_nested_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(whole_home_engine, 'REVIEW_DIR', str(tmp_path))
    local_reference = str(tmp_path / 'private' / '01_living_a.jpg')
    reference_contract = {
        'contract_id': 'contract', 'slots': [{
            'slot_id': 'living_openplan_axis',
            'reference_asset': {
                'asset_id': '01_living_a', 'sha256': 'asset-hash',
                'width': 500, 'height': 500, 'local_path': local_reference,
                'public_thumb_url': 'https://example.invalid/thumb.jpg?signature=private',
            },
            'reference_viewpoint': {'scene_id': 279876079},
        }],
    }
    run = {
        'run_id': 'cad-reference-run', 'project_id': 'cad-project',
        'material_mode': 'reference', 'reference_contract_id': 'contract',
        'reference_contract_snapshot': reference_contract,
        'reference_asset_snapshots': [{
            'role': 'current_slot_reference', 'slot_id': 'living_openplan_axis',
            'asset_id': '01_living_a', 'sha256': 'asset-hash', 'width': 500,
            'height': 500, 'scene_id': 279876079,
        }],
        'input_manifest': [{
            'path': local_reference, 'role': 'current_slot_reference',
            'asset_id': '01_living_a', 'sha256': 'asset-hash',
        }],
        'benchmark_batch_id': 'batch',
        'cad_source_snapshot': {'path': r'C:\local\source.dxf'},
        'cad_import_snapshot': {'cad_facts_hash': 'hash'},
        'cad_parse_report_snapshot': {'hard_errors': []},
        'results': [{
            'result_id': 'result', 'attempts': [{
                'attempt_id': 'attempt',
                'trace': [{'prompt': 'api_key=deep-secret render', 'authorization': 'Bearer hidden'}],
            }],
        }],
    }
    path = whole_home_engine.save_review_manifest(run)
    payload = json.loads(open(path, encoding='utf-8').read())
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload['material_mode'] == 'reference'
    assert payload['reference_contract_snapshot']['contract_id'] == 'contract'
    assert payload['cad_import_snapshot']['cad_facts_hash'] == 'hash'
    assert 'deep-secret' not in serialized
    assert 'Bearer hidden' not in serialized
    assert local_reference not in serialized
    assert 'signature=private' not in serialized
    assert payload['input_manifest'][0]['path'] == 'reference-asset:01_living_a'
    view = whole_home_engine.run_view(run)
    view_text = json.dumps(view, ensure_ascii=False)
    assert local_reference not in view_text
    assert 'signature=private' not in view_text


def test_dxf_parser_integration_fixture_when_locked_dependencies_are_installed(tmp_path, monkeypatch):
    ezdxf = pytest.importorskip('ezdxf')
    pytest.importorskip('shapely')
    document = ezdxf.new('R2018')
    document.header['$INSUNITS'] = 6
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(100, -40), (104, -40), (104, -37), (100, -37)], close=True,
                              dxfattribs={'layer': 'A-WALL'})
    modelspace.add_text('客厅', dxfattribs={'insert': (102, -38.5), 'height': .2})
    path = tmp_path / 'fixture.dxf'
    document.saveas(path)
    monkeypatch.setattr(whole_home_cad, 'CAD_ROOT', str(tmp_path / 'cad-assets'))
    model, report = whole_home_cad.parse_dxf(str(path), 'fixture-project')
    assert report['unit_scale_to_m'] == 1.0
    assert report['inventory']['LWPOLYLINE'] == 1
    assert model['cad_to_model']['x'] == -100
    assert model['cad_to_model']['z'] == -37
    assert model['cad_to_model']['z_scale'] == -1
    assert model['model_to_cad']['z'] == -37
    assert model['coordinate_contract_version'] == 2
    assert model['coordinate_system'] == 'right-handed-y-up-x-east-z-south-v2'
    assert model['rooms'][0]['reference_room_profile'] == 'living_room'
    assert report['selected_entity_role_summary']['method'] == 'cad_geometry_role_decomposition_v1'
    assert report['selected_entity_role_summary']['input_entity_count'] == 1
    assert report['selected_entity_role_summary']['retained_wall_entity_count'] == 1
    assert report['selected_entity_role_evidence'][0]['source_handles']
    assert report['raw_opening_summary'] == {
        'schema_version': 1, 'method': 'cad_raw_geometry_opening_v1',
        'candidate_count': 0, 'accepted_count': 0, 'review_count': 0,
        'rejected_count': 0, 'kind_counts': {}, 'reason_counts': {},
    }
    summary = whole_home_cad.cad_report_summary(report)
    assert summary['selected_entity_role_summary'] == report['selected_entity_role_summary']
    assert summary['raw_opening_summary'] == report['raw_opening_summary']
    assert 'selected_entity_role_evidence' not in summary
    assert whole_home_cad.validate_cad_model(model, report)['hard_errors'] == []


def test_semantic_plan_composite_joins_adjacent_floor_halves_but_not_section_views():
    candidates = [
        {'candidate_id': 'floor-lower', 'bbox_m': [0, 0, 8, 3.4]},
        {'candidate_id': 'floor-upper', 'bbox_m': [0, 2.9, 8, 6.4]},
        {'candidate_id': 'section-lower', 'bbox_m': [12, 0, 20, 3.4]},
        {'candidate_id': 'section-upper', 'bbox_m': [12, 2.9, 20, 6.4]},
        {'candidate_id': 'remote-plan', 'bbox_m': [30, 0, 38, 6]},
    ]
    texts = [
        {'text': 'KITCHEN', 'point_m': [2, 1]},
        {'text': 'BEDROOM 1', 'point_m': [2, 5]},
        {'text': 'BEDROOM 2', 'point_m': [6, 5]},
        {'text': 'BEDROOM 1', 'point_m': [14, 1]},
        {'text': 'BEDROOM 2', 'point_m': [14, 5]},
        {'text': 'SECTION EE', 'point_m': [16, 3.2]},
        {'text': 'LIVING ROOM', 'point_m': [34, 2]},
    ]

    groups = whole_home_cad._semantic_plan_composite_groups(candidates, texts)

    assert groups == [[0, 1]]
    floor_metrics = whole_home_cad._candidate_semantic_view_metrics(
        [0, 0, 8, 6.4], texts)
    assert floor_metrics['room_anchor_count'] == 3
    assert floor_metrics['negative_view_title_count'] == 0
    section_metrics = whole_home_cad._candidate_semantic_view_metrics(
        [12, 0, 20, 6.4], texts)
    assert section_metrics['negative_view_title_count'] == 1


def test_near_duplicate_candidate_view_requires_containment_and_same_extent():
    base = {
        "bbox_m": [0, 0, 12, 10],
        "structure_entity_indexes": list(range(600)),
    }
    contained = {
        "bbox_m": [0, 0, 12, 10],
        "structure_entity_indexes": [*range(600), 700, 701],
    }
    assert whole_home_cad._near_duplicate_candidate_views(base, contained)

    separate_plan = {
        "bbox_m": [20, 0, 32, 10],
        "structure_entity_indexes": list(range(800, 1400)),
    }
    assert not whole_home_cad._near_duplicate_candidate_views(base, separate_plan)
    materially_different = {
        "bbox_m": [0, 0, 12, 10],
        "structure_entity_indexes": [*range(600), *range(700, 720)],
    }
    assert not whole_home_cad._near_duplicate_candidate_views(
        base, materially_different)


def test_dxf_wrong_inch_header_reparses_with_metric_annotation_and_source_entity_lock(
        tmp_path, monkeypatch):
    ezdxf = pytest.importorskip('ezdxf')
    pytest.importorskip('shapely')
    document = ezdxf.new('R2018')
    document.header['$INSUNITS'] = 1  # incorrect: coordinates and labels prove metres
    modelspace = document.modelspace()
    # Four disconnected structural bands are merged by the initial inch-scale
    # 350 mm clustering tolerance but split after metric correction.  The
    # source-entity lock must preserve the complete proved plan on pass two.
    for points in (
        [(0, 0), (8, 0), (8, .2), (0, .2)],
        [(0, 5.8), (8, 5.8), (8, 6), (0, 6)],
        [(0, .2), (.2, .2), (.2, 5.8), (0, 5.8)],
        [(7.8, .2), (8, .2), (8, 5.8), (7.8, 5.8)],
    ):
        modelspace.add_lwpolyline(points, close=True, dxfattribs={'layer': 'A-WALL'})
    modelspace.add_text("BEDROOM 10'-0\" X 9'-0\"",
                        dxfattribs={'insert': (2, 2), 'height': .2})
    modelspace.add_text("LIVING 12'-0\" X 10'-0\"",
                        dxfattribs={'insert': (5, 4), 'height': .2})
    path = tmp_path / 'wrong-inch-header.dxf'
    document.saveas(path)
    monkeypatch.setattr(whole_home_cad, 'CAD_ROOT', str(tmp_path / 'cad-assets'))

    with pytest.raises(whole_home_cad.CadError) as captured:
        whole_home_cad.parse_dxf(str(path), 'unit-lock-project')
    # The fixture intentionally has no internal room semantics, so the normal
    # hard-review gate remains active.  Unit/candidate evidence is still fully
    # available and must be tested from that review payload.
    model = captured.value.details['model']
    report = captured.value.details['parse_report']

    assert report['insunits'] == 1
    assert report['resolved_insunits'] == 6
    assert report['declared_unit_scale_to_m'] == .0254
    assert report['unit_scale_to_m'] == 1.0
    assert report['unit_resolution']['method'] == (
        'cad_explicit_annotation_unit_resolution_v1')
    assert report['selected_candidate_id'] == 'cad_plan_unit_evidence'
    assert report['selection_method'] == (
        'explicit_annotation_unit_source_entity_lock_v1')
    assert next(row for row in report['candidate_plans']
                if row['candidate_id'] == 'cad_plan_unit_evidence')[
                    'structural_entity_count'] == 4
    assert model['scale']['unit_code'] == 6
    assert model['scale']['declared_unit_code'] == 1
    assert model['semantic_report']['status'] == 'needs_review'
    assert {row['code'] for row in model['semantic_report']['hard_errors']} == {
        'cad_physical_boundary_missing_for_enclosed_room_labels'}


def test_polygon_with_closed_hole_is_not_misreported_as_open_outer_topology():
    geometry = pytest.importorskip('shapely.geometry')
    polygon = geometry.Polygon(
        [(0, 0), (8, 0), (8, 6), (0, 6)],
        holes=[[(3, 2), (5, 2), (5, 4), (3, 4)]],
    )
    # This is the exact Shapely shape that triggered the real DWG false
    # negative: the combined boundary is multi-part, while every ring is
    # individually valid and closed.
    assert polygon.boundary.geom_type == 'MultiLineString'
    assert polygon.boundary.is_ring is False
    assert whole_home_cad._polygonal_topology_closed(polygon) is True
    assert whole_home_cad._polygonal_topology_closed(
        geometry.LineString([(0, 0), (1, 0)])) is False


def test_duplicate_fixed_insert_geometry_is_one_object_with_merged_audit_evidence(
        tmp_path, monkeypatch):
    ezdxf = pytest.importorskip('ezdxf')
    pytest.importorskip('shapely')
    document = ezdxf.new('R2018')
    document.header['$INSUNITS'] = 6
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (5, 0), (5, 4), (0, 4)], close=True,
        dxfattribs={'layer': 'A-WALL'})
    modelspace.add_text('卧室', dxfattribs={'insert': (2.5, 2), 'height': .2})
    bed = document.blocks.new('BED_DUPLICATE')
    bed.add_lwpolyline([(0, 0), (1.8, 0), (1.8, 2), (0, 2)], close=True)
    duplicate_handles = [
        modelspace.add_blockref('BED_DUPLICATE', (.5, .5),
                                dxfattribs={'layer': 'A-FURN'}).dxf.handle
        for _ in range(3)
    ]
    modelspace.add_blockref('BED_DUPLICATE', (2.8, .5),
                            dxfattribs={'layer': 'A-FURN'})
    path = tmp_path / 'duplicate-fixed-inserts.dxf'
    document.saveas(path)
    monkeypatch.setattr(whole_home_cad, 'CAD_ROOT', str(tmp_path / 'cad-assets'))

    model, _ = whole_home_cad.parse_dxf(str(path), 'duplicate-fixed-project')

    beds = [row for row in model['fixed_objects'] if row['semantic_role'] == 'bed']
    assert len(beds) == 2
    merged = next(row for row in beds if row.get('duplicate_source_count') == 3)
    evidence = merged['cad_provenance']['duplicate_geometry_evidence']
    assert len(evidence) == 2
    assert {row['root_handle'] for row in evidence}.issubset(set(duplicate_handles))


def test_accepted_wall_assemblies_are_canonical_and_pending_are_low_review_traces(
        tmp_path, monkeypatch):
    ezdxf = pytest.importorskip('ezdxf')
    pytest.importorskip('shapely')
    document = ezdxf.new('R2018')
    document.header['$INSUNITS'] = 6
    modelspace = document.modelspace()
    # Two measured faces form one accepted assembly; the short unmatched line
    # remains audit evidence and must not become a full-height 3D wall.
    modelspace.add_line((0, 0), (4, 0), dxfattribs={'layer': 'A-WALL'})
    modelspace.add_line((0, .2), (4, .2), dxfattribs={'layer': 'A-WALL'})
    modelspace.add_line((2, .2), (2, .6), dxfattribs={'layer': 'A-WALL'})
    modelspace.add_lwpolyline(
        [(0, 0), (4, 0), (4, 3), (0, 3)], close=True,
        dxfattribs={'layer': 'A-WALL-ROOM-BOUNDARY'})
    modelspace.add_text('客厅', dxfattribs={'insert': (2, 1.5), 'height': .2})
    path = tmp_path / 'canonical-assembly-walls.dxf'
    document.saveas(path)
    monkeypatch.setattr(whole_home_cad, 'CAD_ROOT', str(tmp_path / 'cad-assets'))

    model, _ = whole_home_cad.parse_dxf(str(path), 'canonical-wall-project')

    accepted = [row for row in model['wall_assemblies']
                if row['review_status'] == 'accepted']
    assert accepted
    canonical = [row for row in model['walls']
                 if row.get('review_status') == 'accepted']
    review_traces = [row for row in model['walls']
                     if row.get('review_status') == 'needs_review']
    assert len(canonical) == len(accepted)
    assert {row['wall_assembly_id'] for row in canonical} == {
        row['id'] for row in accepted}
    assert all(row['boundary_kind'] == 'centerline' for row in canonical)
    thickness_by_id = {row['id']: row['thickness_m'] for row in accepted}
    assert all(row['thickness_m'] == thickness_by_id[row['wall_assembly_id']]
               for row in canonical)
    assert review_traces
    assert all(row['source'] == 'cad_review_evidence'
               and row['boundary_kind'] == 'unresolved_review_evidence'
               and row['display_mode'] == 'review_floor_trace'
               and row['height_m'] <= .12
               for row in review_traces)
    rejected_ids = {row['id'] for row in model['wall_assemblies']
                    if row['review_status'] == 'rejected'}
    assert not rejected_ids.intersection(
        row.get('wall_assembly_id') for row in model['walls'])


def test_dxf_block_transform_curves_and_multicluster_gates_when_dependencies_are_installed(
        tmp_path, monkeypatch):
    ezdxf = pytest.importorskip('ezdxf')
    pytest.importorskip('shapely')
    document = ezdxf.new('R2018')
    document.header['$INSUNITS'] = 6
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (4, 0), (4, 3), (0, 3)], close=True,
                              dxfattribs={'layer': 'A-WALL'})
    modelspace.add_text('客厅', dxfattribs={'insert': (2, 1.5), 'height': .2})
    wall_block = document.blocks.new('WALL_CHILD')
    wall_block.add_line((0, 0), (.5, 0))  # DXF layer 0 inherits parent INSERT layer.
    wall_insert = modelspace.add_blockref(
        'WALL_CHILD', (1, 1), dxfattribs={'layer': 'A-WALL'})
    furniture_block = document.blocks.new('FURNITURE_MARK')
    furniture_block.add_line((0, 0), (.5, 0))
    furniture_insert = modelspace.add_blockref(
        'FURNITURE_MARK', (1, 1.5), dxfattribs={'layer': 'A-FURN'})
    modelspace.add_arc(center=(2, 1), radius=.25, start_angle=0, end_angle=90,
                       dxfattribs={'layer': 'A-WALL'})
    modelspace.add_spline([(1, 2), (1.5, 2.2), (2, 2)],
                          dxfattribs={'layer': 'A-WALL'})
    path = tmp_path / 'block-curves.dxf'
    document.saveas(path)
    monkeypatch.setattr(whole_home_cad, 'CAD_ROOT', str(tmp_path / 'cad-assets'))
    model, report = whole_home_cad.parse_dxf(str(path), 'block-project')
    assert report['inventory']['ARC'] == 1
    assert report['inventory']['SPLINE'] == 1
    assert report['structural_entity_count'] >= 4
    assert report['ignored_nonstructural_count'] == 1
    insert_handle = wall_insert.dxf.handle
    transformed = [wall for wall in model['walls']
                   if wall['cad_provenance'].get('root_handle') == insert_handle]
    assert transformed
    provenance = transformed[0]['cad_provenance']
    assert provenance['insert_chain'][0]['block'] == 'WALL_CHILD'
    assert provenance['raw_layer'] == '0'
    assert provenance['effective_layer'] == 'A-WALL'
    furniture_handle = furniture_insert.dxf.handle
    assert not any(wall['cad_provenance'].get('root_handle') == furniture_handle
                   for wall in model['walls'])
    ignored = [row for row in report['ignored_nonstructural_entities']
               if row['cad_provenance'].get('root_handle') == furniture_handle]
    assert ignored
    assert ignored[0]['cad_provenance']['raw_layer'] == '0'
    assert ignored[0]['cad_provenance']['effective_layer'] == 'A-FURN'

    ambiguous = ezdxf.new('R2018')
    ambiguous.header['$INSUNITS'] = 6
    space = ambiguous.modelspace()
    for offset in (0, 20):
        space.add_lwpolyline([(offset, 0), (offset + 4, 0), (offset + 4, 3), (offset, 3)],
                             close=True, dxfattribs={'layer': 'A-WALL'})
        space.add_text('客厅', dxfattribs={'insert': (offset + 2, 1.5), 'height': .2})
    # Non-structural context spans both plans but must not merge wall clusters.
    space.add_line((4, 1), (20, 1), dxfattribs={'layer': 'A-FURN'})
    ambiguous_path = tmp_path / 'ambiguous.dxf'
    ambiguous.saveas(ambiguous_path)
    with pytest.raises(whole_home_cad.CadError) as error:
        whole_home_cad.parse_dxf(str(ambiguous_path), 'ambiguous-project')
    assert error.value.code == 'cad_hard_review_required'
    hard_codes = {row['code'] for row in error.value.details['parse_report']['hard_errors']}
    assert 'cad_ambiguous_plan_candidates' in hard_codes


def test_parse_dxf_filters_repeated_compact_inherited_detail_before_wall_assembly(
        tmp_path, monkeypatch):
    ezdxf = pytest.importorskip('ezdxf')
    document = ezdxf.new('R2018')
    document.header['$INSUNITS'] = 6
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (4, 0), (4, 3), (0, 3)], close=True,
        dxfattribs={'layer': 'A-WALL', 'color': 6})
    modelspace.add_text('客厅', dxfattribs={'insert': (2, 1.5), 'height': .2})
    detail = document.blocks.new('OPAQUE_SYMBOL')
    for start, end in (
        ((0, 0), (.10, 0)), ((.10, 0), (.10, .06)),
        ((.10, .06), (0, .06)), ((0, .06), (0, 0)),
        ((0, 0), (.10, .06)), ((.10, 0), (0, .06)),
    ):
        detail.add_line(start, end, dxfattribs={'layer': '0', 'color': 1})
    for x in (.50, .85, 1.20):
        modelspace.add_blockref('OPAQUE_SYMBOL', (x, .05),
                                dxfattribs={'layer': 'A-WALL'})
    path = tmp_path / 'generic-role-decomposition.dxf'
    document.saveas(path)
    monkeypatch.setattr(whole_home_cad, 'CAD_ROOT', str(tmp_path / 'cad-assets'))

    model, report = whole_home_cad.parse_dxf(str(path), 'role-project')

    role = report['selected_entity_role_summary']
    assert role['input_entity_count'] == 19
    assert role['retained_wall_entity_count'] == 1
    assert role['context_entity_count'] == 18
    assert role['reason_counts']['repeated_compact_geometry_signature'] == 18
    assert report['alignment_metrics']['selected_structural_entity_count'] == 1
    assert len(model['wall_assemblies']) == 1
    assert model['wall_assemblies'][0]['source_representation'] == 'closed_footprint'
    context_evidence = [row for row in report['selected_entity_role_evidence']
                        if row['role'] == 'context_fixture']
    assert len(context_evidence) == 3
    assert all(row['blocks'] == ['OPAQUE_SYMBOL'] for row in context_evidence)
    assert all(row['colors'] == [1] for row in context_evidence)


def test_dxf_without_explicit_wall_semantics_fails_closed_with_audit_report(tmp_path, monkeypatch):
    ezdxf = pytest.importorskip('ezdxf')
    pytest.importorskip('shapely')
    document = ezdxf.new('R2018')
    document.header['$INSUNITS'] = 6
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (4, 0), (4, 3), (0, 3)], close=True,
                              dxfattribs={'layer': 'A-FURN'})
    modelspace.add_text('客厅', dxfattribs={'insert': (2, 1.5), 'height': .2})
    path = tmp_path / 'no-wall-semantics.dxf'
    document.saveas(path)
    monkeypatch.setattr(whole_home_cad, 'CAD_ROOT', str(tmp_path / 'cad-assets'))
    with pytest.raises(whole_home_cad.CadError) as error:
        whole_home_cad.parse_dxf(str(path), 'no-wall-project')
    assert error.value.code == 'cad_wall_semantics_unresolved'
    report = error.value.details['parse_report']
    assert report['structural_entity_count'] == 0
    assert report['ignored_nonstructural_count'] == 1
    assert report['ignored_nonstructural_entities'][0]['layer'] == 'A-FURN'
    assert os.path.isfile(report['report_path'])


def test_compact_anonymous_wall_layer_glyph_is_not_promoted_to_full_height_wall():
    provenance = {'root_handle': '203', 'block': '*U66', 'effective_layer': 'A-WALL'}
    geometry = [{
        'points': [(0, 0), (.5, 0), (.5, .68), (0, .68), (0, 0)],
        'bbox': (0, 0, .5, .68), 'closed': True, 'wall_candidate': True,
        'cad_provenance': copy.deepcopy(provenance),
    }, {
        'points': [(0, 0), (.5, .68)], 'bbox': (0, 0, .5, .68),
        'closed': False, 'wall_candidate': True,
        'cad_provenance': copy.deepcopy(provenance),
    }, {
        'points': [(0, 0), (3, 0)], 'bbox': (0, 0, 3, 0),
        'closed': False, 'wall_candidate': True,
        'cad_provenance': {'root_handle': 'wall-run', 'block': '*U77'},
    }]
    excluded = whole_home_cad._exclude_compact_anonymous_wall_glyphs(geometry)
    assert excluded == [{
        'root_handle': '203', 'block': '*U66', 'bbox_m': [0, 0, .5, .68],
        'entity_indexes': [0, 1], 'reason': 'compact_anonymous_insert_glyph',
    }]
    assert geometry[0]['wall_candidate'] is False
    assert geometry[1]['structural_exclusion_reason'] == 'compact_anonymous_insert_glyph'
    assert geometry[2]['wall_candidate'] is True


def test_cad_space_layers_filter_faces_and_keep_open_plan_semantics_in_model_coordinates():
    from shapely.geometry import Polygon
    from Floor_engine_server import whole_home_cad_space

    world = Polygon([(100, -40), (108, -40), (108, -34), (100, -34)])
    strip = Polygon([(100, -40), (108, -40), (108, -39.8), (100, -39.8)])
    anchors = [{
        'anchor_id': 'living', 'text': '客厅', 'semantic_profile': 'living_room',
        'reference_profile': 'living_room', 'point_m': [102, -37],
        'point': {'x': 2, 'z': 3},
    }, {
        'anchor_id': 'dining', 'text': '餐厅', 'semantic_profile': 'dining_room',
        'reference_profile': 'living_room', 'point_m': [106, -37],
        'point': {'x': 6, 'z': 3},
    }]
    raw, accepted = whole_home_cad_space.classify_raw_faces(
        [world, strip], origin_x=100, origin_z=-40, text_anchors=anchors)
    assert len(accepted) == 1
    assert 'double_line_wall_strip' in next(
        row for row in raw if row['disposition'] == 'excluded')['filter_reasons']
    physical, zones, errors = whole_home_cad_space.initial_space_layers(accepted)
    assert physical[0]['space_type'] == 'open_plan'
    assert errors == []
    assert len(zones) == 1
    assert zones[0]['zone_type'] == 'living_room'
    assert zones[0]['reference_room_profile'] == 'living_room'
    assert zones[0]['source'] == 'cad_compatible_open_plan_semantic_collection_v1'
    assert zones[0]['observed_semantic_profiles'] == ['dining_room', 'living_room']
    assert all(0 <= point['x'] <= 8 and 0 <= point['z'] <= 6
               for zone in zones for point in zone['geometry']['points'])


def test_unique_bed_marker_in_closed_unlabelled_space_infers_generic_bedroom_only():
    from shapely.geometry import Polygon
    from Floor_engine_server import whole_home_cad_space

    bedroom = Polygon([(0, 0), (3.2, 0), (3.2, 3.4), (0, 3.4)])
    marker = {
        'anchor_id': 'bed-source', 'source_kind': 'space_marker',
        'space_marker': 'bed', 'point_m': [1.6, 1.7],
        'cad_provenance': {'root_handle': 'bed-block'},
    }
    _, accepted = whole_home_cad_space.classify_raw_faces(
        [bedroom], origin_x=0, origin_z=0, text_anchors=[marker])
    physical, zones, errors = whole_home_cad_space.initial_space_layers(accepted)

    assert errors == []
    assert physical[0]['space_type'] == 'enclosed_room'
    assert zones[0]['zone_type'] == 'bedroom'
    assert zones[0]['reference_room_profile'] == 'bedroom'
    assert zones[0]['label'] == '卧室（床位几何证据）'
    assert zones[0]['semantic_inference']['method'] == (
        'cad_enclosed_space_bed_marker_v1')
    assert zones[0]['semantic_inference']['decision_basis'][-1] == (
        'generic_bedroom_only_no_primary_secondary_guess')


def test_full_face_hatch_at_two_plan_edges_proves_unlabelled_paved_exterior_space():
    from shapely.geometry import Polygon
    from Floor_engine_server import whole_home_cad_space

    patio = Polygon([(0, 0), (2.6, 0), (2.6, 2.4), (0, 2.4)])
    indoor = Polygon([(2.8, 1.0), (6.0, 1.0), (6.0, 5.0), (2.8, 5.0)])
    surface = {
        'method': 'cad_hatch_boundary_surface_evidence_v1',
        'source_handle': 'hatch-patio', 'root_handle': 'hatch-patio',
        'boundary_path_count': 4, 'solid_fill': False,
        'polygons_m': [[(0, 0), (2.6, 0), (2.6, 2.4), (0, 2.4)]],
        'cad_provenance': {'handle': 'hatch-patio'},
    }
    _, accepted = whole_home_cad_space.classify_raw_faces(
        [patio, indoor], origin_x=0, origin_z=0, text_anchors=[{
            'anchor_id': 'bed', 'source_kind': 'text', 'text': 'BEDROOM',
            'point_m': [4, 3], 'semantic_profile': 'bedroom',
            'reference_profile': 'bedroom',
        }], surface_regions=[surface])
    physical, zones, errors = whole_home_cad_space.initial_space_layers(accepted)

    assert errors == []
    patio_zone = next(row for row in zones if row['zone_type'] == 'balcony')
    assert patio_zone['semantic_inference']['method'] == (
        'cad_perimeter_hatch_surface_space_v1')
    assert patio_zone['semantic_inference']['hatch_source_handle'] == 'hatch-patio'
    patio_space = next(
        row for row in physical if row['id'] == patio_zone['physical_space_id'])
    assert patio_space['space_type'] == 'balcony'


def test_full_face_hatch_without_two_adjacent_plan_edges_does_not_guess_patio():
    from shapely.geometry import Polygon
    from Floor_engine_server import whole_home_cad_space

    left = Polygon([(0, 0), (2, 0), (2, 5), (0, 5)])
    tiled_interior = Polygon([(2.2, 1), (4.2, 1), (4.2, 3), (2.2, 3)])
    right = Polygon([(4.4, 0), (6.4, 0), (6.4, 5), (4.4, 5)])
    surface = {
        'method': 'cad_hatch_boundary_surface_evidence_v1',
        'source_handle': 'hatch-interior', 'root_handle': 'hatch-interior',
        'boundary_path_count': 1, 'solid_fill': False,
        'polygons_m': [[(2.2, 1), (4.2, 1), (4.2, 3), (2.2, 3)]],
    }
    _, accepted = whole_home_cad_space.classify_raw_faces(
        [left, tiled_interior, right], origin_x=0, origin_z=0,
        text_anchors=[], surface_regions=[surface])
    _, zones, errors = whole_home_cad_space.initial_space_layers(accepted)

    assert not any(row['zone_type'] == 'balcony' for row in zones)
    assert any(row['code'] == 'cad_room_semantics_unresolved' for row in errors)


def test_unique_text_anchor_ten_millimetres_outside_room_face_binds_without_moving_geometry():
    from shapely.geometry import Polygon
    from Floor_engine_server import whole_home_cad_space

    store = Polygon([(0, 0), (1.5, 0), (1.5, 2.1), (0, 2.1)])
    anchor = {
        'anchor_id': 'store-label', 'source_kind': 'text',
        'text': 'STORE ROOM', 'semantic_profile': 'storage',
        'reference_profile': 'storage', 'point_m': [-.01, 1.05],
    }

    raw, accepted = whole_home_cad_space.classify_raw_faces(
        [store], origin_x=0, origin_z=0, text_anchors=[anchor])
    physical, zones, errors = whole_home_cad_space.initial_space_layers(accepted)

    assert errors == []
    assert physical[0]['space_type'] == 'service'
    assert zones[0]['zone_type'] == 'storage'
    assert zones[0]['text_anchor_ids'] == ['store-label']
    proof = accepted[0]['anchors'][0]['anchor_binding_evidence']
    assert proof['method'] == 'unique_near_boundary_text_anchor_v1'
    assert proof['distance_to_physical_face_m'] == pytest.approx(.01)
    assert raw[0]['cad_polygon_m'] == [[0.0, 0.0], [1.5, 0.0],
                                      [1.5, 2.1], [0.0, 2.1]]


def test_near_boundary_text_anchor_stays_unassigned_when_two_faces_are_equally_close():
    from shapely.geometry import Polygon
    from Floor_engine_server import whole_home_cad_space

    left = Polygon([(-2, 0), (-.01, 0), (-.01, 2), (-2, 2)])
    right = Polygon([(.01, 0), (2, 0), (2, 2), (.01, 2)])
    anchor = {
        'anchor_id': 'ambiguous-label', 'source_kind': 'text',
        'text': 'STORE ROOM', 'semantic_profile': 'storage',
        'reference_profile': 'storage', 'point_m': [0, 1],
    }

    _, accepted = whole_home_cad_space.classify_raw_faces(
        [left, right], origin_x=0, origin_z=0, text_anchors=[anchor])
    _, zones, errors = whole_home_cad_space.initial_space_layers(accepted)

    assert len(errors) == 2
    assert all(row['zone_type'] == 'unassigned' for row in zones)


def test_cad_validator_uses_real_intersection_and_reports_invalid_polygon():
    concave = _cad_model()
    concave['rooms'] = [{
        **copy.deepcopy(concave['rooms'][0]), 'id': 'concave',
        'polygon': [{'x': x, 'z': z} for x, z in (
            (0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4))],
    }, {
        **copy.deepcopy(concave['rooms'][0]), 'id': 'neighbor',
        'polygon': [{'x': x, 'z': z} for x, z in (
            (1.1, 1.1), (3.9, 1.1), (3.9, 3.9), (1.1, 3.9))],
    }]
    report = _cad_report()
    codes = {row['code'] for row in whole_home_cad.validate_cad_model(concave, report)['hard_errors']}
    assert 'cad_room_overlap' not in codes

    invalid = _cad_model()
    invalid['rooms'][0]['polygon'] = [
        {'x': 0, 'z': 0}, {'x': 4, 'z': 3}, {'x': 0, 'z': 3}, {'x': 4, 'z': 0}]
    codes = {row['code'] for row in whole_home_cad.validate_cad_model(invalid, report)['hard_errors']}
    assert 'cad_room_polygon_invalid' in codes


def test_manual_space_draft_is_server_union_and_no_opening_stays_blocked():
    from Floor_engine_server import whole_home_cad_space
    raw = [{
        'face_id': 'face-a', 'disposition': 'physical_space_candidate', 'manual_eligible': True,
        'polygon': [{'x': 0, 'z': 0}, {'x': 4, 'z': 0}, {'x': 4, 'z': 3}, {'x': 0, 'z': 3}],
        'cad_polygon_m': [[100, -40], [104, -40], [104, -37], [100, -37]],
    }]
    model = _cad_model()
    model['openings'] = []
    updated, confirmation = whole_home_cad_space.apply_space_draft(
        model, raw,
        [{'id': 'p', 'label': '开放空间', 'space_type': 'open_plan',
          'face_ids': ['face-a'], 'polygon': [], 'selected': True}],
        [{'id': 'living', 'physical_space_id': 'p', 'label': '客厅',
          'zone_type': 'living_room', 'geometry': {
              'kind': 'rectangle', 'min_x': 0, 'min_z': 0, 'max_x': 4, 'max_z': 3}}],
        [])
    assert updated['physical_spaces'][0]['polygon'][2] == {'x': 4.0, 'z': 3.0}
    assert confirmation['status'] == 'needs_opening_review'


def test_public_reparse_operation_redacts_paths_and_restart_interrupts(monkeypatch, tmp_path):
    from Floor_engine_server import whole_home_cad_reparse
    record, created = whole_home_cad_reparse.create_operation(
        str(tmp_path), project_id='project', operation_id='operation_1234',
        base_revision=1, base_state_hash='state', source_path=str(tmp_path / 'source.dxf'),
        source_sha256='sha', candidate_id='', actor='tester')
    assert created is True
    whole_home_cad_reparse.update_operation(
        str(tmp_path), 'project', 'operation_1234', status='failed', stage='failed',
        progress=100, failure_evidence={'report_path': r'C:\private\report.json',
                                       'report_sha256': 'abc', 'hard_error_summary': []})
    public = whole_home_cad_reparse.public_operation(
        whole_home_cad_reparse.get_operation(str(tmp_path), 'project', 'operation_1234'))
    assert 'report_path' not in json.dumps(public)
    assert 'C:\\private' not in json.dumps(public)

    second, _ = whole_home_cad_reparse.create_operation(
        str(tmp_path), project_id='other', operation_id='operation_5678',
        base_revision=1, base_state_hash='state', source_path=str(tmp_path / 'source.dxf'),
        source_sha256='sha', candidate_id='', actor='tester')
    monkeypatch.setattr(whole_home_cad_reparse, 'PROCESS_INSTANCE_ID', 'new-process')
    interrupted = whole_home_cad_reparse.get_operation(
        str(tmp_path), 'other', second['operation_id'])
    assert interrupted['status'] == 'interrupted'


def test_cad_list_view_never_calls_heavy_project_view(monkeypatch, tmp_path):
    monkeypatch.setattr(routes_whole_home, 'CAD_ROOT', str(tmp_path))
    monkeypatch.setattr(
        routes_whole_home, 'project_view',
        lambda *args, **kwargs: pytest.fail('CAD list must not call heavy project_view'))
    project = {
        'project_id': 'cad-list', 'source_type': 'cad', 'status': 'needs_review',
        'revision': 2, 'floorplan_path': '',
        'model': {'physical_spaces': [{}] * 2, 'semantic_zones': [{}] * 3},
        'captures': [{}] * 4, 'operations': [{'large': 'x' * 10000}],
    }
    view = routes_whole_home._whole_home_project_view(project, list_mode=True)
    assert 'model' not in view and 'captures' not in view and 'operations' not in view
    assert view['model_summary']['physical_space_count'] == 2
    assert view['model_summary']['capture_count'] == 4


def test_cad_project_public_view_forwards_role_and_opening_summaries_only(
        monkeypatch, tmp_path):
    monkeypatch.setattr(routes_whole_home, 'CAD_ROOT', str(tmp_path))
    role_summary = {
        'schema_version': 1, 'method': 'cad_geometry_role_decomposition_v1',
        'input_entity_count': 10, 'retained_wall_entity_count': 4,
        'opening_evidence_entity_count': 2, 'context_entity_count': 3,
        'review_entity_count': 1,
    }
    opening_summary = {
        'schema_version': 1, 'method': 'cad_raw_geometry_opening_v1',
        'candidate_count': 2, 'accepted_count': 1, 'review_count': 1,
        'rejected_count': 0,
    }
    project = {
        'project_id': 'cad-public-report', 'source_type': 'cad', 'status': 'needs_review',
        'parse_report': {
            'schema_version': 3, 'source_sha256': 'source-hash',
            'raw_faces': [{'private': 'full-face-evidence'}], 'raw_face_count': 1,
            'selected_entity_role_summary': role_summary,
            'selected_entity_role_evidence': [{'private': 'role-evidence-secret'}],
            'raw_opening_summary': opening_summary,
            'raw_opening_candidates': [{'private': 'opening-evidence-secret'}],
            'hard_errors': [], 'warnings': [], 'candidate_plans': [],
        },
    }

    view = routes_whole_home._whole_home_project_view(project)

    assert view['parse_report']['selected_entity_role_summary'] == role_summary
    assert view['parse_report']['raw_opening_summary'] == opening_summary
    serialized = json.dumps(view['parse_report'])
    assert 'role-evidence-secret' not in serialized
    assert 'opening-evidence-secret' not in serialized


def test_cad_candidate_preview_allows_sibling_parse_artifact(monkeypatch, tmp_path):
    cad_root = tmp_path / 'cad-root'
    project_root = cad_root / 'cad-preview'
    ingest_root = project_root / 'ingest_abc'
    parse_root = project_root / 'parse_def'
    ingest_root.mkdir(parents=True)
    parse_root.mkdir(parents=True)
    preview = parse_root / 'candidate_1.png'
    preview.write_bytes(b'valid-preview')
    project = {
        'project_id': 'cad-preview', 'source_type': 'cad',
        'parse_report': {
            'artifact_directory': str(ingest_root),
            'candidate_plans': [{
                'candidate_id': 'cad_plan_1', 'preview_path': str(preview),
            }],
        },
    }
    monkeypatch.setattr(routes_whole_home, 'CAD_ROOT', str(cad_root))
    monkeypatch.setattr(
        routes_whole_home, 'load_project',
        lambda project_id: copy.deepcopy(project) if project_id == 'cad-preview' else None)

    response = routes_whole_home.get_whole_home_cad_candidate_preview(
        'cad-preview', 'cad_plan_1')

    assert os.path.realpath(response.path) == os.path.realpath(preview)
    assert response.media_type == 'image/png'


def test_cad_candidate_preview_rejects_other_project_artifact(monkeypatch, tmp_path):
    cad_root = tmp_path / 'cad-root'
    ingest_root = cad_root / 'cad-preview' / 'ingest_abc'
    foreign_root = cad_root / 'other-project' / 'parse_def'
    ingest_root.mkdir(parents=True)
    foreign_root.mkdir(parents=True)
    preview = foreign_root / 'candidate_1.png'
    preview.write_bytes(b'foreign-preview')
    project = {
        'project_id': 'cad-preview', 'source_type': 'cad',
        'parse_report': {
            'artifact_directory': str(ingest_root),
            'candidate_plans': [{
                'candidate_id': 'cad_plan_1', 'preview_path': str(preview),
            }],
        },
    }
    monkeypatch.setattr(routes_whole_home, 'CAD_ROOT', str(cad_root))
    monkeypatch.setattr(routes_whole_home, 'load_project', lambda _: copy.deepcopy(project))

    with pytest.raises(HTTPException) as error:
        routes_whole_home.get_whole_home_cad_candidate_preview(
            'cad-preview', 'cad_plan_1')

    assert error.value.status_code == 404


def test_cad_space_draft_route_cas_and_light_response(monkeypatch):
    model = _cad_model()
    model.update(
        physical_spaces=[], semantic_zones=[], excluded_face_ids=[],
        space_model_schema_version=1)
    raw = [{
        'face_id': 'face-a', 'disposition': 'physical_space_candidate', 'manual_eligible': True,
        'polygon': [{'x': 0, 'z': 0}, {'x': 4, 'z': 0}, {'x': 4, 'z': 3}, {'x': 0, 'z': 3}],
        'cad_polygon_m': [[100, -40], [104, -40], [104, -37], [100, -37]],
    }]
    project = {
        'project_id': 'cad-cas', 'source_type': 'cad', 'revision': 2,
        'status': 'needs_review', 'model': model,
        'captures': [{'capture_id': 'old-capture', 'status': 'ready'}],
        'pano_captures': [{'capture_id': 'old-pano', 'status': 'gated'}],
        'operations': [],
        'parse_report': {'raw_faces': raw, 'text_anchors': [], 'model_to_cad': {'x': 100, 'z': -40}},
        'cad_import': {'cad_facts_hash': model['cad_facts_hash']},
    }
    before_hash = whole_home_engine.state_hash(project)
    monkeypatch.setattr(routes_whole_home, 'load_project', lambda project_id: copy.deepcopy(project))
    committed = {}

    def cas(project_id, mutator, expected_state_hash=''):
        assert expected_state_hash == before_hash
        value = mutator(copy.deepcopy(project))
        committed.update(copy.deepcopy(value))
        return value, before_hash, whole_home_engine.state_hash(value)

    monkeypatch.setattr(routes_whole_home, 'cas_update_project', cas)
    request = server_schemas.WholeHomeCadSpaceDraftRequest.model_validate({
        'base_revision': 2, 'base_state_hash': before_hash,
        'operation_id': 'space_op_1234', 'editor_id': 'tester',
        'physical_spaces': [{
            'id': 'open', 'label': '开放空间', 'space_type': 'open_plan',
            'face_ids': ['face-a'], 'polygon': [], 'selected': True}],
        'semantic_zones': [{
            'id': 'living', 'physical_space_id': 'open', 'label': '客厅',
            'zone_type': 'living_room', 'geometry': {
                'kind': 'polygon', 'points': raw[0]['polygon']}}],
        'excluded_face_ids': [],
    })
    response = routes_whole_home.save_whole_home_cad_space_draft('cad-cas', request)
    assert set(response) == {'project_id', 'revision', 'status', 'space_confirmation', 'model_summary'}
    assert response['revision'] == 3
    assert response['space_confirmation']['status'] == 'needs_opening_review'
    assert committed['captures'][0]['status'] == 'stale'
    assert committed['captures'][0]['stale_reason'] == 'cad_space_draft_updated'
    assert committed['captures'][0]['stale_at_revision'] == 3
    assert committed['pano_captures'][0]['status'] == 'stale'
    assert committed['pano_captures'][0]['stale_reason'] == 'cad_space_draft_updated'
    assert committed['pano_captures'][0]['stale_at_revision'] == 3
    assert committed['operations'][-1]['operation_id'] == 'space_op_1234'


def test_cad_space_raw_faces_v2_rebuilds_public_polygon_from_cad_provenance():
    from Floor_engine_server import whole_home_cad_space

    model = _cad_model()
    model.update(coordinate_contract_version=2, depth_m=3)
    report = {
        'cad_to_model': {
            'type': 'affine_plan_v2', 'x': -100, 'z': -37,
            'x_scale': 1, 'z_scale': -1,
        },
        'raw_faces': [{
            'face_id': 'face-a', 'disposition': 'physical_space_candidate',
            'manual_eligible': True,
            'polygon': [
                {'x': 0, 'z': 0}, {'x': 4, 'z': 0},
                {'x': 4, 'z': 3}, {'x': 0, 'z': 3},
            ],
            'cad_polygon_m': [
                [100, -40], [104, -40], [104, -37], [100, -37],
            ],
            'anchors': [{'text': '客厅', 'point_m': [102, -38]}],
        }],
    }

    rows = routes_whole_home._cad_space_raw_faces({}, report, model)

    assert rows[0]['polygon'] == [
        {'x': 0.0, 'z': 3.0}, {'x': 4.0, 'z': 3.0},
        {'x': 4.0, 'z': 0.0}, {'x': 0.0, 'z': 0.0},
    ]
    assert rows[0]['anchors'][0]['point'] == {'x': 2.0, 'z': 1.0}
    assert report['raw_faces'][0]['polygon'][0] == {'x': 0, 'z': 0}

    updated, _ = whole_home_cad_space.apply_space_draft(
        model, rows,
        [{'id': 'p', 'label': '开放空间', 'space_type': 'open_plan',
          'face_ids': ['face-a'], 'polygon': rows[0]['polygon'], 'selected': True}],
        [{'id': 'living', 'physical_space_id': 'p', 'label': '客厅',
          'zone_type': 'living_room', 'geometry': {
              'kind': 'polygon', 'points': rows[0]['polygon']}}],
        [])
    assert updated['physical_spaces'][0]['polygon'] == rows[0]['polygon']


def test_cad_space_editor_model_is_strict_put_round_trip_dto():
    model = _cad_model()
    model['physical_spaces'] = [{
        'id': 'physical-a', 'label': '客厅', 'space_type': 'open_plan',
        'face_ids': ['face-a'], 'polygon': [
            {'x': 0, 'z': 0}, {'x': 4, 'z': 0}, {'x': 4, 'z': 3}, {'x': 0, 'z': 3}],
        'selected': True, 'source': 'cad', 'interior_rings': [],
        'cad_provenance': {'private': 'server-only'},
    }]
    model['semantic_zones'] = [{
        'id': 'zone-a', 'physical_space_id': 'physical-a', 'label': '客厅',
        'zone_type': 'living_room', 'geometry': {
            'kind': 'polygon', 'points': model['physical_spaces'][0]['polygon'],
            'server_evidence': {'private': True}},
        'source': 'cad', 'reference_room_profile': 'living_room',
    }]

    physical_spaces, semantic_zones = routes_whole_home._cad_space_editor_model(model)
    request = server_schemas.WholeHomeCadSpaceDraftRequest.model_validate({
        'base_revision': 1, 'operation_id': 'roundtrip_1234',
        'physical_spaces': physical_spaces, 'semantic_zones': semantic_zones,
    })

    assert request.physical_spaces[0].id == 'physical-a'
    assert set(physical_spaces[0]) == {
        'id', 'label', 'space_type', 'face_ids', 'polygon', 'selected'}
    assert set(semantic_zones[0]) == {
        'id', 'physical_space_id', 'label', 'zone_type', 'geometry'}
    assert set(semantic_zones[0]['geometry']) == {'kind', 'points'}


def test_async_reparse_completion_uses_bound_project_cas(monkeypatch, tmp_path):
    source = tmp_path / 'source.dxf'
    source.write_bytes(ASCII_DXF)
    model, report = _cad_model(), _cad_report()
    report.update(source={'sha256': whole_home_cad.sha256_file(str(source))})
    project = {
        'project_id': 'cad-async', 'source_type': 'cad', 'revision': 5,
        'model': _cad_model(), 'captures': [{'capture_id': 'old', 'status': 'ready'}],
        'pano_captures': [{'capture_id': 'old-pano', 'status': 'ready'}],
        'operations': [], 'cad_import': {},
    }
    before_hash = whole_home_engine.state_hash(project)
    operation = {
        'project_id': 'cad-async', 'operation_id': 'reparse_op_1234',
        'base_revision': 5, 'base_state_hash': before_hash,
        'source_path': str(source), 'source_sha256': whole_home_cad.sha256_file(str(source)),
        'candidate_id': '', 'actor': 'tester',
    }
    monkeypatch.setattr(routes_whole_home, 'CAD_ROOT', str(tmp_path / 'cad-root'))
    monkeypatch.setattr(routes_whole_home, 'ingest_cad',
                        lambda *args, **kwargs: (copy.deepcopy(model), copy.deepcopy(report), 'preview.png'))
    operation_updates = []
    monkeypatch.setattr(routes_whole_home, 'update_cad_reparse_operation',
                        lambda *args, **kwargs: operation_updates.append(copy.deepcopy(kwargs)) or kwargs)
    committed = {}

    def cas(project_id, mutator, expected_state_hash=''):
        assert project_id == 'cad-async'
        assert expected_state_hash == before_hash
        value = mutator(copy.deepcopy(project))
        committed.update(value)
        return value, before_hash, whole_home_engine.state_hash(value)

    monkeypatch.setattr(routes_whole_home, 'cas_update_project', cas)
    asyncio.run(routes_whole_home._run_cad_reparse_operation(operation))
    assert committed['revision'] == 6
    assert committed['captures'][0]['status'] == 'stale'
    assert committed['captures'][0]['stale_at_revision'] == 6
    assert committed['pano_captures'][0]['status'] == 'stale'
    assert committed['pano_captures'][0]['stale_reason'] == 'cad_reparsed'
    assert committed['pano_captures'][0]['stale_at_revision'] == 6
    assert committed['operations'][-1]['operation_id'] == 'reparse_op_1234'
    assert operation_updates[-1]['status'] == 'done'
    assert operation_updates[-1]['progress'] == 100


def test_async_reparse_with_unresolved_wall_evidence_stays_needs_review(monkeypatch, tmp_path):
    source = tmp_path / 'source.dxf'
    source.write_bytes(ASCII_DXF)
    model, report = _cad_model(), _cad_report()
    report.update(
        source={'sha256': whole_home_cad.sha256_file(str(source))},
        alignment_metrics={'production_unresolved_wall_assembly_count': 3},
    )
    project = {
        'project_id': 'cad-async-review', 'source_type': 'cad', 'revision': 5,
        'model': _cad_model(), 'captures': [], 'operations': [], 'cad_import': {},
    }
    before_hash = whole_home_engine.state_hash(project)
    operation = {
        'project_id': 'cad-async-review', 'operation_id': 'reparse_op_unresolved',
        'base_revision': 5, 'base_state_hash': before_hash,
        'source_path': str(source), 'source_sha256': whole_home_cad.sha256_file(str(source)),
        'candidate_id': '', 'actor': 'tester',
    }
    monkeypatch.setattr(routes_whole_home, 'CAD_ROOT', str(tmp_path / 'cad-root'))
    monkeypatch.setattr(routes_whole_home, 'ingest_cad',
                        lambda *args, **kwargs: (copy.deepcopy(model), copy.deepcopy(report),
                                                 'preview.png'))
    operation_updates = []
    monkeypatch.setattr(
        routes_whole_home, 'update_cad_reparse_operation',
        lambda *args, **kwargs: operation_updates.append(copy.deepcopy(kwargs)) or kwargs)
    committed = {}

    def cas(project_id, mutator, expected_state_hash=''):
        assert expected_state_hash == before_hash
        value = mutator(copy.deepcopy(project))
        committed.update(value)
        return value, before_hash, whole_home_engine.state_hash(value)

    monkeypatch.setattr(routes_whole_home, 'cas_update_project', cas)
    asyncio.run(routes_whole_home._run_cad_reparse_operation(operation))

    assert committed['status'] == 'needs_review'
    assert '3 个墙体证据待解决' in committed['stage']
    assert committed['operations'][-1]['type'] == 'cad_reparse_local_needs_review'
    assert operation_updates[-1]['status'] == 'needs_review'


def test_async_reparse_retains_provenance_draft_as_needs_review(monkeypatch, tmp_path):
    source = tmp_path / 'source.dxf'
    source.write_bytes(ASCII_DXF)
    model, report = _cad_model(), _cad_report()
    report.update(
        source={'sha256': whole_home_cad.sha256_file(str(source))},
        artifact_directory=str(tmp_path / 'cad-root' / 'cad-async' / 'parse'),
        hard_errors=[{'code': 'cad_outer_wall_not_closed', 'message': 'open shell'}],
    )
    os.makedirs(report['artifact_directory'], exist_ok=True)
    project = {
        'project_id': 'cad-async', 'source_type': 'cad', 'revision': 5,
        'model': _cad_model(), 'captures': [{'capture_id': 'old', 'status': 'ready'}],
        'operations': [], 'cad_import': {},
    }
    before_hash = whole_home_engine.state_hash(project)
    operation = {
        'project_id': 'cad-async', 'operation_id': 'reparse_op_review',
        'base_revision': 5, 'base_state_hash': before_hash,
        'source_path': str(source), 'source_sha256': whole_home_cad.sha256_file(str(source)),
        'candidate_id': '', 'actor': 'tester',
    }
    monkeypatch.setattr(routes_whole_home, 'CAD_ROOT', str(tmp_path / 'cad-root'))
    monkeypatch.setattr(routes_whole_home, 'ingest_cad', lambda *args, **kwargs: (
        _ for _ in ()).throw(whole_home_cad.CadError(
            'cad_hard_review_required', 'blocked',
            details={'model': copy.deepcopy(model), 'parse_report': copy.deepcopy(report)})))
    operation_updates = []
    monkeypatch.setattr(
        routes_whole_home, 'update_cad_reparse_operation',
        lambda *args, **kwargs: operation_updates.append(copy.deepcopy(kwargs)) or kwargs)
    committed = {}

    def cas(project_id, mutator, expected_state_hash=''):
        assert project_id == 'cad-async'
        assert expected_state_hash == before_hash
        value = mutator(copy.deepcopy(project))
        committed.update(value)
        return value, before_hash, whole_home_engine.state_hash(value)

    monkeypatch.setattr(routes_whole_home, 'cas_update_project', cas)
    asyncio.run(routes_whole_home._run_cad_reparse_operation(operation))
    assert committed['status'] == 'needs_review'
    assert committed['revision'] == 6
    assert committed['model']['schema_version'] == model['schema_version']
    assert committed['cad_error']['code'] == 'cad_hard_review_required'
    assert committed['operations'][-1]['type'] == 'cad_reparse_needs_manual_space_review'
    assert operation_updates[-1]['status'] == 'needs_review'
    assert operation_updates[-1]['result_revision'] == 6


def test_open_plan_semantic_cells_are_valid_after_metric_precision_rounding():
    from shapely.geometry import Polygon

    from Floor_engine_server.whole_home_cad_space import initial_space_layers

    shape = Polygon([
        (0.0, 0.0), (8.000003, 0.0), (8.000003, 4.000004),
        (4.000004, 4.000004), (4.000004, 7.999997), (0.0, 7.999997),
    ])
    anchors = [
        {'anchor_id': 'living', 'text': '客厅', 'point_m': [2.0, 2.0],
         'semantic_profile': 'living_room', 'reference_profile': 'living_room'},
        {'anchor_id': 'dining', 'text': '餐厅', 'point_m': [6.0, 2.0],
         'semantic_profile': 'dining_room', 'reference_profile': 'dining_room'},
        {'anchor_id': 'foyer', 'text': '门厅', 'point_m': [2.0, 6.0],
         'semantic_profile': 'foyer', 'reference_profile': 'foyer'},
    ]
    accepted = [{
        'shape': shape, 'face_id': 'cad_face_openplan', 'anchors': anchors,
        'polygon': [{'x': x, 'z': z} for x, z in list(shape.exterior.coords)[:-1]],
        'interior_rings': [], 'origin_x': 0.0, 'origin_z': 0.0,
    }]

    physical, zones, unresolved = initial_space_layers(accepted)

    assert unresolved == []
    assert len(zones) == 1
    assert zones[0]['zone_type'] == 'living_room'
    assert zones[0]['reference_room_profile'] == 'living_room'
    assert zones[0]['semantic_status'] == 'complete'
    assert zones[0]['source'] == 'cad_open_plan_semantic_collection_v2'
    assert zones[0]['observed_semantic_profiles'] == [
        'dining_room', 'foyer', 'living_room']
    for space in physical:
        points = space.get('polygon') or []
        assert len(points) >= 3
        assert Polygon([(point['x'], point['z']) for point in points]).is_valid
    for zone in zones:
        points = (zone['geometry'] or {}).get('points') or []
        assert len(points) >= 3
        assert Polygon([(point['x'], point['z']) for point in points]).is_valid


def test_opening_side_spaces_reclassify_only_narrow_internal_frame_as_door():
    candidates = [{
        'candidate_id': 'internal-frame', 'kind': 'window', 'status': 'accepted',
        'axis_segment_cad_m': [[4.0, 2.0], [4.0, 2.8]], 'width_m': .8,
        'reason_codes': ['elongated_frame_geometry'],
    }, {
        'candidate_id': 'balcony-frame', 'kind': 'window', 'status': 'accepted',
        'axis_segment_cad_m': [[8.0, 1.0], [8.0, 3.0]], 'width_m': 2.0,
        'reason_codes': ['elongated_frame_geometry'],
    }]
    rooms = [{
        'id': 'bedroom', 'physical_space_id': 'bedroom-physical',
        'label': '卧室', 'room_type': 'bedroom', 'semantic_profile': 'bedroom',
        'polygon': [{'x': 0, 'z': 0}, {'x': 4, 'z': 0},
                    {'x': 4, 'z': 4}, {'x': 0, 'z': 4}],
    }, {
        'id': 'hall', 'physical_space_id': 'hall-physical',
        'label': '门厅', 'room_type': 'foyer', 'semantic_profile': 'foyer',
        'polygon': [{'x': 4, 'z': 0}, {'x': 8, 'z': 0},
                    {'x': 8, 'z': 4}, {'x': 4, 'z': 4}],
    }, {
        'id': 'balcony', 'physical_space_id': 'balcony-physical',
        'label': '阳台', 'room_type': 'balcony', 'semantic_profile': 'balcony',
        'polygon': [{'x': 8, 'z': 0}, {'x': 10, 'z': 0},
                    {'x': 10, 'z': 4}, {'x': 8, 'z': 4}],
    }]

    whole_home_cad._annotate_opening_space_sides(
        candidates, rooms, origin_x=0.0, origin_y=0.0)

    assert candidates[0]['kind'] == 'door'
    assert candidates[0]['source_geometry_kind'] == 'window'
    assert candidates[0]['suggested_kind_confidence'] == .96
    assert candidates[0]['kind_resolution']['decision'] == 'auto_applied'
    assert candidates[1]['kind'] == 'window'
    assert candidates[1]['suggested_kind'] == 'window'
    assert candidates[1]['suggested_kind_reason'] == 'balcony_boundary_and_wide_frame'


def test_opening_distant_second_room_does_not_turn_fixture_frame_into_door():
    candidate = {
        'candidate_id': 'cabinet-frame', 'kind': 'window', 'status': 'accepted',
        'wall_assembly_id': 'cad_wall_global_opening_host_cabinet-frame',
        'axis_segment_cad_m': [[0.0, 0.0], [0.8, 0.0]], 'width_m': .8,
        'reason_codes': ['elongated_frame_geometry'],
    }
    rooms = [{
        'id': 'kitchen', 'physical_space_id': 'kitchen-physical',
        'label': '厨房', 'room_type': 'kitchen', 'semantic_profile': 'kitchen',
        'polygon': [
            {'x': 0, 'z': -.7}, {'x': .8, 'z': -.7},
            {'x': .8, 'z': -.08}, {'x': 0, 'z': -.08}],
    }, {
        'id': 'living', 'physical_space_id': 'living-physical',
        'label': '客厅', 'room_type': 'living_room', 'semantic_profile': 'living_room',
        'polygon': [
            {'x': 0, 'z': .4}, {'x': .8, 'z': .4},
            {'x': .8, 'z': .9}, {'x': 0, 'z': .9}],
    }]

    whole_home_cad._annotate_opening_space_sides(
        [candidate], rooms, origin_x=0.0, origin_y=0.0)

    assert candidate['kind'] == 'window'
    assert candidate['status'] == 'review'
    assert candidate['synthetic_host_disposition'] == 'remove'
    assert candidate['kind_resolution']['decision'] == 'review_required'
    assert candidate['suggested_kind_reason'] == (
        'near_side_adjacency_unresolved_distant_room_hit_not_door_proof')


def test_enclosed_room_labels_in_one_face_fail_closed_without_voronoi_geometry():
    from shapely.geometry import Polygon

    from Floor_engine_server.whole_home_cad_space import initial_space_layers

    shape = Polygon([(0, 0), (8, 0), (8, 6), (0, 6)])
    accepted = [{
        'shape': shape, 'face_id': 'cad_face_missing_walls',
        'anchors': [
            {'anchor_id': 'bed-1', 'text': 'BEDROOM 1', 'point_m': [2, 3],
             'semantic_profile': 'bedroom', 'reference_profile': 'bedroom'},
            {'anchor_id': 'bed-2', 'text': 'BEDROOM 2', 'point_m': [6, 3],
             'semantic_profile': 'bedroom', 'reference_profile': 'bedroom'},
        ],
        'polygon': [{'x': x, 'z': z} for x, z in list(shape.exterior.coords)[:-1]],
        'interior_rings': [], 'origin_x': 0.0, 'origin_z': 0.0,
    }]

    physical, zones, unresolved = initial_space_layers(accepted)

    assert len(zones) == 1
    assert zones[0]['zone_type'] == 'unassigned'
    assert zones[0]['source'] == 'cad_unresolved_composite_physical_space_v1'
    assert physical[0]['space_type'] == 'unresolved_composite'
    assert physical[0]['semantic_blocker'] == (
        'cad_physical_boundary_missing_for_enclosed_room_labels')
    assert unresolved[0]['code'] == (
        'cad_physical_boundary_missing_for_enclosed_room_labels')


def test_unlabelled_elongated_perimeter_spaces_are_audited_balconies_only():
    from shapely.geometry import Polygon

    from Floor_engine_server.whole_home_cad_space import initial_space_layers

    living = Polygon([(0, 0), (6, 0), (6, 5), (0, 5)])
    balcony = Polygon([(6.2, 0), (7.4, 0), (7.4, 4), (6.2, 4)])
    interior_unlabelled = Polygon([(2, 5.2), (4, 5.2), (4, 7.2), (2, 7.2)])
    accepted = [
        {
            'shape': living, 'face_id': 'cad_face_living',
            'anchors': [{'anchor_id': 'living', 'text': '客厅',
                         'semantic_profile': 'living_room',
                         'reference_profile': 'living_room', 'point_m': [3, 2]}],
            'polygon': [{'x': x, 'z': z} for x, z in list(living.exterior.coords)[:-1]],
            'interior_rings': [], 'origin_x': 0.0, 'origin_z': 0.0,
        }, {
            'shape': balcony, 'face_id': 'cad_face_balcony', 'anchors': [],
            'polygon': [{'x': x, 'z': z} for x, z in list(balcony.exterior.coords)[:-1]],
            'interior_rings': [], 'origin_x': 0.0, 'origin_z': 0.0,
        }, {
            'shape': interior_unlabelled, 'face_id': 'cad_face_square', 'anchors': [],
            'polygon': [{'x': x, 'z': z} for x, z in list(interior_unlabelled.exterior.coords)[:-1]],
            'interior_rings': [], 'origin_x': 0.0, 'origin_z': 0.0,
        },
    ]

    physical, zones, unresolved = initial_space_layers(accepted)

    balcony_space = next(row for row in physical if row['id'] == 'physical_balcony')
    balcony_zone = next(row for row in zones if row['physical_space_id'] == 'physical_balcony')
    assert balcony_space['space_type'] == 'balcony'
    assert balcony_space['semantic_inference']['method'] == 'cad_perimeter_elongated_space_v1'
    assert balcony_zone['zone_type'] == 'balcony'
    assert balcony_zone['source'] == 'cad_geometry_boundary_inference_v1'
    assert {row['physical_space_id'] for row in unresolved} == {'physical_square'}


def test_cad_room_profile_recognizes_walk_in_closet_as_storage():
    assert whole_home_cad._room_profile_from_text('衣帽间（3.2sq.m）') == (
        'storage', 'storage')


def test_cad_room_profile_has_bounded_typo_tolerance_and_cultural_rooms():
    assert whole_home_cad._room_profile_from_text("KITCKEN 10' X 8'") == (
        'kitchen', 'kitchen')
    assert whole_home_cad._room_profile_from_text('DINNING') == (
        'living_room', 'dining_room')
    assert whole_home_cad._room_profile_from_text('POOJA') == ('pooja', 'other')
    assert whole_home_cad._room_profile_from_text('PORCH') == ('porch', 'balcony')
    assert whole_home_cad._room_profile_from_text(
        'AREA OF PLOT = 1800 SQ.FT\nAREA OF PORCH = 140 SQ.FT') == ('', '')
    # More than one edit stays unknown; typo tolerance is not a fuzzy catch-all.
    assert whole_home_cad._room_profile_from_text('KITZZEN') == ('', '')


def test_geometry_plan_authority_collapses_98pct_same_source_view_duplicates():
    geometry = []
    index = 0
    # One source-root floor plan split into 125 auditable segments; the centre
    # wall proves two closed regions independently of the two room labels.
    for start, end in (
        ((0, 0), (10, 0)), ((10, 0), (10, 8)),
        ((10, 8), (0, 8)), ((0, 8), (0, 0)),
        ((5, 0), (5, 8)),
    ):
        for part in range(25):
            first = (
                start[0] + (end[0] - start[0]) * part / 25,
                start[1] + (end[1] - start[1]) * part / 25,
            )
            second = (
                start[0] + (end[0] - start[0]) * (part + 1) / 25,
                start[1] + (end[1] - start[1]) * (part + 1) / 25,
            )
            geometry.append({
                'points': [first, second],
                'bbox': (min(first[0], second[0]), min(first[1], second[1]),
                         max(first[0], second[0]), max(first[1], second[1])),
                'cad_provenance': {'root_handle': 'plan-root',
                                   'source_handle': f'wall-{index}'},
            })
            index += 1
    # Two disconnected context strokes remain inside the same plan envelope.
    geometry.extend([{
        'points': [(1, 1), (1.1, 1)], 'bbox': (1, 1, 1.1, 1),
        'cad_provenance': {'root_handle': 'context-a', 'source_handle': 'context-a'},
    }, {
        'points': [(9, 7), (9.1, 7)], 'bbox': (9, 7, 9.1, 7),
        'cad_provenance': {'root_handle': 'context-b', 'source_handle': 'context-b'},
    }])
    texts = [
        {'text': 'LIVING ROOM', 'point_m': [3, 4]},
        {'text': 'KITCHEN', 'point_m': [7, 4]},
    ]

    evidence = whole_home_cad._geometry_only_structural_evidence(
        geometry, texts, [])

    assert evidence['status'] == 'proved'
    assert len(evidence['selected_indexes']) == 125
    superseded = next(row for row in evidence['candidates']
                      if row['proof_status'] == 'superseded_near_duplicate')
    assert superseded['near_duplicate_evidence'][
        'source_entity_overlap_ratio'] == pytest.approx(125 / 127)


def test_text_free_multi_view_export_selects_decisive_two_axis_plan_root():
    geometry = []

    def add_line(root, first, second):
        geometry.append({
            'points': [first, second],
            'bbox': (min(first[0], second[0]), min(first[1], second[1]),
                     max(first[0], second[0]), max(first[1], second[1])),
            'cad_provenance': {
                'root_handle': root,
                'source_handle': f'{root}-{len(geometry)}',
            },
        })

    # The plan has many >=500 mm runs in both orthogonal directions.
    for index in range(32):
        position = index * 10 / 31
        add_line('plan-root', (position, 0), (position, 10))
        add_line('plan-root', (0, position), (10, position))
    # Two independent elevation roots are directionally unbalanced and have a
    # much weaker secondary-axis count.  No view names or layer semantics are
    # supplied to the selector.
    for index in range(40):
        y = index * 4 / 39
        add_line('view-a', (20, y), (30, y))
    for index in range(10):
        x = 20 + index * 10 / 9
        add_line('view-a', (x, 0), (x, 4))
    for index in range(35):
        y = 10 + index * 4 / 34
        add_line('view-b', (20, y), (30, y))
    for index in range(12):
        x = 20 + index * 10 / 11
        add_line('view-b', (x, 10), (x, 14))

    evidence = whole_home_cad._geometry_only_structural_evidence(
        geometry, [], [])

    assert evidence['status'] == 'proved'
    selected = [geometry[index] for index in evidence['selected_indexes']]
    assert {row['cad_provenance']['root_handle'] for row in selected} == {
        'plan-root'}
    winner = next(row for row in evidence['candidates']
                  if row['proof_status'] == 'proved')
    proof = winner['orthographic_plan_root_evidence']
    assert proof['method'] == 'cad_multi_view_orthographic_plan_root_v1'
    assert proof['selected_metrics']['long_axis_run_counts'] == [32, 32]
    assert proof['long_run_balance_selection_ratio'] >= 2.0

    # Two views alone are insufficient to establish a multi-view sheet.
    two_root_geometry = [
        row for row in geometry
        if row['cad_provenance']['root_handle'] != 'view-b']
    unresolved = whole_home_cad._geometry_only_structural_evidence(
        two_root_geometry, [], [])
    assert unresolved['status'] == 'unresolved'


def test_dense_projected_plan_keeps_unique_building_spanning_ink_component():
    def line(index, first, second):
        return {
            'entity_index': index,
            'entity_type': 'LINE',
            'points': [first, second],
            'cad_provenance': {'root_handle': 'plan-root'},
        }

    primary = [
        ((0.0, 0.0), (30.0, 0.0)),
        ((30.0, 0.0), (30.0, 20.0)),
        ((30.0, 20.0), (0.0, 20.0)),
        ((0.0, 20.0), (0.0, 0.0)),
    ]
    primary.extend(
        ((value, 0.0), (value, 3.0))
        for value in [0.1 + number * .14 for number in range(21)])
    primary.extend(
        ((0.0, value), (3.0, value))
        for value in [0.1 + number * .14 for number in range(21)])
    rows = []
    for _repeat in range(14):
        for first, second in primary:
            rows.append(line(len(rows), first, second))
    for number in range(20):
        x = 5.0 + number * .5
        rows.append(line(len(rows), (x, 0.0), (x + .08, .08)))
    # One hundred disconnected furniture glyphs share the same source root.
    # Their names/layers are deliberately absent; only disconnected ink is
    # available to the filter.
    for number in range(100):
        column, row_number = number % 10, number // 10
        x, y = 6.0 + column * 1.4, 5.0 + row_number * 1.2
        for first, second in (
            ((x, y), (x + .4, y)),
            ((x + .4, y), (x + .4, y + .4)),
            ((x + .4, y + .4), (x, y + .4)),
            ((x, y + .4), (x, y)),
        ):
            rows.append(line(len(rows), first, second))
    authority = {'candidates': [{
        'proof_status': 'proved',
        'orthographic_plan_root_evidence': {
            'method': 'cad_multi_view_orthographic_plan_root_v1',
            'selected_root_handle': 'plan-root',
        },
    }]}

    result = whole_home_cad._filter_text_free_projected_plan_structure(
        rows, [0.0, 0.0, 30.0, 20.0], authority)

    assert result['status'] == 'proved'
    assert result['retained_entity_count'] == len(primary) * 14
    assert result['excluded_entity_count'] == 420
    assert result['short_nonorthogonal_detail_entity_count'] == 20
    assert result['primary_width_coverage_ratio'] >= .95
    assert result['primary_depth_coverage_ratio'] >= .95
    assert result['primary_to_runner_area_ratio'] >= 4
    assert result['retained_orthographic_metrics'][
        'long_run_balance_count'] >= 20


def test_dense_projected_plan_does_not_choose_between_two_building_ink_components():
    def line(index, first, second):
        return {
            'entity_index': index,
            'entity_type': 'LINE', 'points': [first, second],
            'cad_provenance': {'root_handle': 'plan-root'},
        }

    rows = []
    for _repeat in range(125):
        for offset in (0.0, 14.0):
            for first, second in (
                ((offset, 0.0), (offset + 10.0, 0.0)),
                ((offset + 10.0, 0.0), (offset + 10.0, 8.0)),
                ((offset + 10.0, 8.0), (offset, 8.0)),
                ((offset, 8.0), (offset, 0.0)),
            ):
                rows.append(line(len(rows), first, second))
    authority = {'candidates': [{
        'proof_status': 'proved',
        'orthographic_plan_root_evidence': {
            'method': 'cad_multi_view_orthographic_plan_root_v1',
            'selected_root_handle': 'plan-root',
        },
    }]}

    result = whole_home_cad._filter_text_free_projected_plan_structure(
        rows, [0.0, 0.0, 24.0, 8.0], authority)

    assert result['status'] == 'unresolved'
    assert result['reason'] == (
        'primary_ink_component_not_unique_or_building_spanning')
    assert result['retained_entity_indexes'] == []


def test_dense_projected_plan_removes_source_proved_bed_but_keeps_wall_face():
    def line(index, first, second):
        return {
            'entity_index': index,
            'entity_type': 'LINE', 'points': [first, second],
            'cad_provenance': {'root_handle': 'plan-root'},
        }

    rows = []
    shell = [
        ((0.0, 0.0), (30.0, 0.0)),
        ((30.0, 0.0), (30.0, 20.0)),
        ((30.0, 20.0), (0.0, 20.0)),
        ((0.0, 20.0), (0.0, 0.0)),
    ]
    for _repeat in range(250):
        for first, second in shell:
            rows.append(line(len(rows), first, second))
    # An independent exterior face proves that the flush frame's right edge
    # is shared structural evidence and must survive furniture removal.
    exterior_pair_index = len(rows)
    rows.append(line(len(rows), (30.1, 0.0), (30.1, 20.0)))
    rows.append(line(len(rows), (30.0, 0.0), (30.1, 0.0)))
    rows.append(line(len(rows), (30.0, 20.0), (30.1, 20.0)))
    bed_indexes = []
    for first, second in (
        ((28.0, 10.0), (28.0, 12.0)),
        ((28.0, 10.0), (30.0, 10.0)),
        ((28.0, 12.0), (30.0, 12.0)),
        ((30.0, 10.0), (30.0, 12.0)),
        ((28.5, 10.4), (29.5, 11.7)),
        ((29.5, 11.7), (29.7, 10.6)),
        ((29.7, 10.6), (28.5, 10.4)),
    ):
        bed_indexes.append(len(rows))
        rows.append(line(len(rows), first, second))
    # Forty-eight tiny chords form rounded pillow/headboard detail.  They are
    # intentionally disconnected from the building ink and have no names.
    circle_points = [
        (29.0 + .35 * math.cos(math.radians(angle)),
         11.0 + .25 * math.sin(math.radians(angle)))
        for angle in [number * 7.5 for number in range(49)]
    ]
    for first, second in zip(circle_points, circle_points[1:]):
        rows.append(line(len(rows), first, second))
    for number in range(300):
        x = 1.0 + (number % 30) * .6
        y = 1.0 + (number // 30) * .6
        rows.append(line(len(rows), (x, y), (x + .02, y)))
    authority = {'candidates': [{
        'proof_status': 'proved',
        'orthographic_plan_root_evidence': {
            'method': 'cad_multi_view_orthographic_plan_root_v1',
            'selected_root_handle': 'plan-root',
        },
    }]}

    result = whole_home_cad._filter_text_free_projected_plan_structure(
        rows, [0.0, 0.0, 30.1, 20.0], authority)

    assert result['status'] == 'proved', result.get('diagnostic')
    removed = set(result['projected_bed_detail_entity_indexes'])
    assert set(bed_indexes[:3]).issubset(removed)
    assert set(bed_indexes[4:]).issubset(result['excluded_entity_indexes'])
    assert bed_indexes[3] not in removed
    assert exterior_pair_index in result['retained_entity_indexes']
    proof = result['projected_bed_detail_evidence'][0]
    assert proof['closed_textile_face_count'] == 1
    assert proof['short_curved_detail_chord_count'] >= 40
    assert bed_indexes[3] in proof[
        'preserved_structural_pair_entity_indexes']


def test_dense_projected_plan_removes_radial_fixture_and_compact_partition():
    def line(index, first, second):
        return {
            'entity_index': index,
            'entity_type': 'LINE', 'points': [first, second],
            'cad_provenance': {'root_handle': 'plan-root'},
        }

    rows = []
    shell = [
        ((0.0, 0.0), (30.0, 0.0)),
        ((30.0, 0.0), (30.0, 20.0)),
        ((30.0, 20.0), (0.0, 20.0)),
        ((0.0, 20.0), (0.0, 0.0)),
    ]
    for _repeat in range(250):
        for first, second in shell:
            rows.append(line(len(rows), first, second))
    center, radius = (1.5, 5.0), .08
    circle_points = [
        (center[0] + radius * math.cos(math.radians(angle)),
         center[1] + radius * math.sin(math.radians(angle)))
        for angle in range(0, 361, 15)
    ]
    circle_indexes = []
    for first, second in zip(circle_points, circle_points[1:]):
        circle_indexes.append(len(rows))
        rows.append(line(len(rows), first, second))
    spoke_indexes = []
    for angle, far_point in (
        (210, (0.0, 4.4)),
        (330, (2.5, 4.2)),
        (30, (2.5, 5.8)),
        (120, (1.0, 6.5)),
    ):
        hub_point = (
            center[0] + radius * math.cos(math.radians(angle)),
            center[1] + radius * math.sin(math.radians(angle)),
        )
        spoke_indexes.append(len(rows))
        rows.append(line(len(rows), hub_point, far_point))
    partition_indexes = []
    for first, second in (
        ((2.5, 4.2), (2.5, 5.8)),
        ((2.6, 4.2), (2.6, 5.8)),
        ((2.5, 4.2), (2.6, 4.2)),
        ((2.5, 5.8), (2.6, 5.8)),
    ):
        partition_indexes.append(len(rows))
        rows.append(line(len(rows), first, second))
    for number in range(300):
        x = 10.0 + (number % 30) * .5
        y = 2.0 + (number // 30) * .5
        rows.append(line(len(rows), (x, y), (x + .02, y)))
    authority = {'candidates': [{
        'proof_status': 'proved',
        'orthographic_plan_root_evidence': {
            'method': 'cad_multi_view_orthographic_plan_root_v1',
            'selected_root_handle': 'plan-root',
        },
    }]}

    result = whole_home_cad._filter_text_free_projected_plan_structure(
        rows, [0.0, 0.0, 30.0, 20.0], authority)

    assert result['status'] == 'proved', result.get('diagnostic')
    assert set(spoke_indexes) == set(
        result['radial_fixture_detail_entity_indexes'])
    assert set(partition_indexes[:2]).issubset(
        result['radial_fixture_partition_entity_indexes'])
    assert set(circle_indexes).issubset(result['excluded_entity_indexes'])
    proof = result['radial_fixture_detail_evidence'][0]
    assert proof['hub_chord_count'] == 24
    assert proof['distinct_spoke_endpoint_count'] == 4
    assert proof['compact_partition_entity_indexes'] == partition_indexes[:2]


def test_dense_projected_plan_removes_connected_repeated_compact_bay_chain():
    def line(index, first, second):
        return {
            'entity_index': index,
            'entity_type': 'LINE', 'points': [first, second],
            'cad_provenance': {'root_handle': 'plan-root'},
        }

    rows = []
    shell = [
        ((0.0, 0.0), (30.0, 0.0)),
        ((30.0, 0.0), (30.0, 20.0)),
        ((30.0, 20.0), (0.0, 20.0)),
        ((0.0, 20.0), (0.0, 0.0)),
    ]
    for _repeat in range(250):
        for first, second in shell:
            rows.append(line(len(rows), first, second))
    # Keep the fixture in the building-spanning component, reproducing a
    # flattened fitted cabinet that touches a long plan line.
    connector_index = len(rows)
    rows.append(line(len(rows), (0.0, 5.0), (10.0, 5.0)))
    bay_indexes = []
    for start, end in ((5.0, 5.6858), (5.7112, 6.397)):
        for first, second in (
            ((10.0, start), (10.0, end)),
            ((10.5, start), (10.5, end)),
            ((10.0, start), (10.5, start)),
            ((10.0, end), (10.5, end)),
            # Short source returns distinguish a projected cabinet bay chain
            # from an ordinary double-line structural wall.
            ((10.0, start), (10.03, start)),
            ((10.0, end), (10.03, end)),
        ):
            bay_indexes.append(len(rows))
            rows.append(line(len(rows), first, second))
    bay_indexes.append(len(rows))
    rows.append(line(len(rows), (10.5, 5.6858), (10.5, 5.7112)))
    # This neighbour is inside the legacy square 180 mm buffer, but extends
    # more than 35 mm across the proved bay-chain axis.  It represents the
    # perpendicular architectural evidence that must survive the bounded
    # axial-only expansion.
    protected_perpendicular_index = len(rows)
    rows.append(line(len(rows), (9.84, 4.98), (10.03, 4.98)))
    for number in range(300):
        x = 12.0 + (number % 30) * .5
        y = 2.0 + (number // 30) * .5
        rows.append(line(len(rows), (x, y), (x + .02, y)))
    authority = {'candidates': [{
        'proof_status': 'proved',
        'orthographic_plan_root_evidence': {
            'method': 'cad_multi_view_orthographic_plan_root_v1',
            'selected_root_handle': 'plan-root',
        },
    }]}

    result = whole_home_cad._filter_text_free_projected_plan_structure(
        rows, [0.0, 0.0, 30.0, 20.0], authority)

    assert result['status'] == 'proved', result.get('diagnostic')
    removed = set(result['projected_compact_bay_detail_entity_indexes'])
    assert set(bay_indexes).issubset(removed)
    assert connector_index in result['retained_entity_indexes']
    assert protected_perpendicular_index not in removed
    proof = result['projected_compact_bay_detail_evidence'][0]
    assert proof['bay_count'] >= 2
    assert proof['trim_return_count'] >= 4
    assert proof['axial_span_m'] == pytest.approx(1.397)
    assert proof['thresholds']['minimum_chain_axial_span_m'] == 1.35
    assert proof['thresholds'][
        'maximum_bounded_axial_extension_distance_m'] == .18
    assert proof['thresholds'][
        'maximum_bounded_lateral_extension_distance_m'] == .035


def test_dense_projected_plan_keeps_adjacent_thin_opening_frames():
    def line(index, first, second):
        return {
            'entity_index': index,
            'entity_type': 'LINE', 'points': [first, second],
            'cad_provenance': {'root_handle': 'plan-root'},
        }

    rows = []
    shell = [
        ((0.0, 0.0), (30.0, 0.0)),
        ((30.0, 0.0), (30.0, 20.0)),
        ((30.0, 20.0), (0.0, 20.0)),
        ((0.0, 20.0), (0.0, 0.0)),
    ]
    for _repeat in range(250):
        for first, second in shell:
            rows.append(line(len(rows), first, second))
    rows.append(line(len(rows), (0.0, 5.0), (10.0, 5.0)))
    frame_indexes = []
    for start, end in ((10.0, 10.6223), (10.7493, 11.3716)):
        for first, second in (
            ((start, 5.0), (end, 5.0)),
            ((start, 5.1016), (end, 5.1016)),
            ((start, 5.0), (start, 5.1016)),
            ((end, 5.0), (end, 5.1016)),
            ((start, 5.0), (start, 5.03)),
            ((end, 5.0), (end, 5.03)),
        ):
            frame_indexes.append(len(rows))
            rows.append(line(len(rows), first, second))
    for number in range(300):
        x = 12.0 + (number % 30) * .5
        y = 2.0 + (number // 30) * .5
        rows.append(line(len(rows), (x, y), (x + .02, y)))
    authority = {'candidates': [{
        'proof_status': 'proved',
        'orthographic_plan_root_evidence': {
            'method': 'cad_multi_view_orthographic_plan_root_v1',
            'selected_root_handle': 'plan-root',
        },
    }]}

    result = whole_home_cad._filter_text_free_projected_plan_structure(
        rows, [0.0, 0.0, 30.0, 20.0], authority)

    assert result['status'] == 'proved', result.get('diagnostic')
    assert not set(frame_indexes).intersection(
        result['projected_compact_bay_detail_entity_indexes'])


def test_dense_projected_plan_keeps_capped_double_wall_without_trim_returns():
    def line(index, first, second):
        return {
            'entity_index': index,
            'entity_type': 'LINE', 'points': [first, second],
            'cad_provenance': {'root_handle': 'plan-root'},
        }

    rows = []
    shell = [
        ((0.0, 0.0), (30.0, 0.0)),
        ((30.0, 0.0), (30.0, 20.0)),
        ((30.0, 20.0), (0.0, 20.0)),
        ((0.0, 20.0), (0.0, 0.0)),
    ]
    for _repeat in range(250):
        for first, second in shell:
            rows.append(line(len(rows), first, second))
    rows.append(line(len(rows), (0.0, 5.0), (10.0, 5.0)))
    wall_indexes = []
    for start, end in ((5.0, 5.6858), (5.7112, 6.397)):
        for first, second in (
            ((10.0, start), (10.0, end)),
            ((10.5, start), (10.5, end)),
            ((10.0, start), (10.5, start)),
            ((10.0, end), (10.5, end)),
        ):
            wall_indexes.append(len(rows))
            rows.append(line(len(rows), first, second))
    wall_indexes.append(len(rows))
    rows.append(line(len(rows), (10.5, 5.6858), (10.5, 5.7112)))
    for number in range(300):
        x = 12.0 + (number % 30) * .5
        y = 2.0 + (number // 30) * .5
        rows.append(line(len(rows), (x, y), (x + .02, y)))
    authority = {'candidates': [{
        'proof_status': 'proved',
        'orthographic_plan_root_evidence': {
            'method': 'cad_multi_view_orthographic_plan_root_v1',
            'selected_root_handle': 'plan-root',
        },
    }]}

    result = whole_home_cad._filter_text_free_projected_plan_structure(
        rows, [0.0, 0.0, 30.0, 20.0], authority)

    assert result['status'] == 'proved', result.get('diagnostic')
    assert result['projected_compact_bay_detail_entity_count'] == 0
    assert set(wall_indexes).issubset(result['retained_entity_indexes'])


@pytest.mark.parametrize(
    ('appliance_face_count', 'compound_micro_count',
     'should_remove', 'should_remove_extension'),
    [(2, 48, True, True), (2, 0, True, False),
     (1, 0, False, False), (0, 0, False, False)],
)
def test_dense_projected_plan_staggered_counter_requires_appliance_faces(
        appliance_face_count, compound_micro_count,
        should_remove, should_remove_extension):
    def line(index, first, second):
        return {
            'entity_index': index,
            'entity_type': 'LINE', 'points': [first, second],
            'cad_provenance': {'root_handle': 'plan-root'},
        }

    rows = []
    shell = [
        ((0.0, 0.0), (30.0, 0.0)),
        ((30.0, 0.0), (30.0, 20.0)),
        ((30.0, 20.0), (0.0, 20.0)),
        ((0.0, 20.0), (0.0, 0.0)),
    ]
    for _repeat in range(250):
        for first, second in shell:
            rows.append(line(len(rows), first, second))
    connector_index = len(rows)
    rows.append(line(len(rows), (0.0, 5.0), (10.0, 5.0)))
    counter_indexes = []

    def counter_line(first, second):
        counter_indexes.append(len(rows))
        rows.append(line(len(rows), first, second))

    counter_line((10.0, 5.0), (10.0, 8.0))
    # Three opposing segments reproduce a flattened counter whose drafting
    # source changes its fixed coordinate by 20 mm along the run.  Deliberate
    # unequal lengths keep it outside the repeated-equal-bay signature.
    for x, start, end in (
        (9.40, 5.00, 5.60),
        (9.42, 5.62, 7.30),
        (9.41, 7.32, 8.00),
    ):
        counter_line((x, start), (x, end))
        counter_line((x, start), (10.0, start))
        counter_line((x, end), (10.0, end))
    for number in range(8):
        y = 5.15 + number * .35
        counter_line((9.98, y), (10.0, y))
    for number in range(appliance_face_count):
        lower = 6.00 + number * .38
        upper = lower + .35
        for first, second in (
            ((9.52, lower), (9.88, lower)),
            ((9.88, lower), (9.88, upper)),
            ((9.88, upper), (9.52, upper)),
            ((9.52, upper), (9.52, lower)),
        ):
            rows.append(line(len(rows), first, second))
    compound_extension_indexes = []
    if appliance_face_count == 2:
        # A dense device frame on the counter's companion side proves that the
        # connected L-shaped run is the same projected fixture component.
        for first, second in (
            ((8.65, 6.40), (9.30, 6.40)),
            ((9.30, 6.40), (9.30, 7.25)),
            ((9.30, 7.25), (8.65, 7.25)),
            ((8.65, 7.25), (8.65, 6.40)),
            ((9.30, 6.40), (9.42, 6.40)),
            ((9.30, 7.25), (9.42, 7.25)),
            ((8.90, 5.20), (8.90, 6.40)),
        ):
            compound_extension_indexes.append(len(rows))
            rows.append(line(len(rows), first, second))
        for number in range(compound_micro_count):
            y = 6.42 + number * .016
            rows.append(line(len(rows), (8.58, y), (8.60, y)))
    for number in range(300):
        x = 12.0 + (number % 30) * .5
        y = 2.0 + (number // 30) * .5
        rows.append(line(len(rows), (x, y), (x + .02, y)))
    authority = {'candidates': [{
        'proof_status': 'proved',
        'orthographic_plan_root_evidence': {
            'method': 'cad_multi_view_orthographic_plan_root_v1',
            'selected_root_handle': 'plan-root',
        },
    }]}

    result = whole_home_cad._filter_text_free_projected_plan_structure(
        rows, [0.0, 0.0, 30.0, 20.0], authority)

    assert result['status'] == 'proved', result.get('diagnostic')
    removed = set(
        result['projected_staggered_counter_detail_entity_indexes'])
    if should_remove:
        assert set(counter_indexes).issubset(removed)
        assert connector_index in result['retained_entity_indexes']
        proof = result['projected_staggered_counter_detail_evidence'][0]
        assert proof['opposite_rail_segment_count'] >= 3
        assert proof['opposite_rail_coverage_ratio'] >= .90
        assert min(proof['terminal_cap_coverage_ratios']) >= .80
        assert proof['aligned_compact_face_pair_count'] >= 1
        assert proof['trim_return_count'] >= 8
        if should_remove_extension:
            assert proof['compound_fixture_extension_entity_count'] >= 50
            assert proof['compound_fixture_device_face_evidence'][0][
                'micro_detail_entity_count'] >= 40
            assert set(compound_extension_indexes).issubset(removed)
        else:
            assert proof['compound_fixture_extension_entity_count'] == 0
            assert not set(compound_extension_indexes).intersection(removed)
    else:
        assert removed == set()
        assert set(counter_indexes).issubset(result['retained_entity_indexes'])


def test_cad_text_room_label_outranks_furniture_insert_semantics():
    from shapely.geometry import Polygon
    from Floor_engine_server.whole_home_cad_space import initial_space_layers

    shape = Polygon([(0, 0), (5, 0), (5, 4), (0, 4)])
    accepted = [{
        'shape': shape, 'face_id': 'cad_face_text_priority',
        'anchors': [
            {'anchor_id': 'text-kitchen', 'source_kind': 'text',
             'text': 'KITCKEN', 'point_m': [2.5, 2],
             'semantic_profile': 'kitchen', 'reference_profile': 'kitchen'},
            {'anchor_id': 'insert-dining', 'source_kind': 'insert',
             'text': 'Dining Set - 36 x 72 in.', 'point_m': [3, 2],
             'semantic_profile': 'dining_room', 'reference_profile': 'living_room'},
        ],
        'polygon': [{'x': x, 'z': z} for x, z in list(shape.exterior.coords)[:-1]],
        'interior_rings': [], 'origin_x': 0.0, 'origin_z': 0.0,
    }]

    _, zones, unresolved = initial_space_layers(accepted)

    assert unresolved == []
    assert len(zones) == 1
    assert zones[0]['label'] == 'KITCKEN'
    assert zones[0]['zone_type'] == 'kitchen'
    assert zones[0]['text_anchor_ids'] == ['text-kitchen']
