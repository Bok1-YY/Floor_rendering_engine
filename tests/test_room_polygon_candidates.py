from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.goal_loop_v2.room_polygon_candidates import (
    build_room_polygon_candidate,
    validate_room_polygon_candidate,
)


ROOT = Path(__file__).resolve().parents[1]


def _document():
    atoms = [
        {"id": "W-V", "centerline_m": [[5.0, 0.0], [5.0, 10.0]], "thickness_m": 0.2, "status": "candidate"},
        {"id": "W-H", "centerline_m": [[0.0, 5.0], [10.0, 5.0]], "thickness_m": 0.2, "status": "candidate"},
    ]
    return {
        "structure_hash": "source-hash",
        "outer_boundary": {"polygon_m": [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]], "status": "confirmed"},
        "wall_graph": {"atoms": atoms},
        "spaces": [
            {"id": "NW", "point_m": [2.0, 8.0], "status": "candidate"},
            {"id": "NE", "point_m": [8.0, 8.0], "status": "candidate"},
            {"id": "SW", "point_m": [2.0, 2.0], "status": "candidate"},
            {"id": "SE", "point_m": [8.0, 2.0], "status": "candidate"},
        ],
    }


@pytest.fixture(autouse=True)
def _minimal_validator(monkeypatch):
    import tools.goal_loop_v2.room_polygon_candidates as module
    monkeypatch.setattr(module, "validate_v21_document", lambda value: value)


def test_wall_solid_subtraction_yields_candidate_faces_without_promotion():
    doc = _document()
    candidate = build_room_polygon_candidate(doc)
    assert candidate["coverage"]["face_candidate_count"] == 4
    assert candidate["coverage"]["single_anchor_face_count"] == 4
    assert {face["label_candidate_space_id"] for face in candidate["faces"]} == {"NW", "NE", "SW", "SE"}
    assert all(face["room_boundary_confirmation"] is False for face in candidate["faces"])
    assert candidate["room_polygon_confirmation"] is False
    assert candidate["adjacency_confirmation"] is False
    assert candidate["build_authorized"] is False
    validate_room_polygon_candidate(doc, candidate)


def test_multiple_anchors_and_unlabeled_face_remain_explicitly_ambiguous():
    doc = _document()
    doc["spaces"] = [
        {"id": "NW-A", "point_m": [2.0, 8.0], "status": "candidate"},
        {"id": "NW-B", "point_m": [3.0, 7.0], "status": "candidate"},
    ]
    candidate = build_room_polygon_candidate(doc)
    assert candidate["coverage"]["multi_anchor_face_count"] == 1
    assert candidate["coverage"]["unlabeled_face_count"] == 3
    ambiguous = next(face for face in candidate["faces"] if face["anchor_assignment_status"] == "ambiguous_multiple_anchors")
    assert ambiguous["label_candidate_space_id"] is None
    assert ambiguous["source_anchor_space_ids"] == ["NW-A", "NW-B"]


def test_anchor_on_inferred_wall_and_forged_geometry_fail_closed():
    doc = _document()
    doc["spaces"].append({"id": "ON-WALL", "point_m": [5.0, 2.0], "status": "candidate"})
    candidate = build_room_polygon_candidate(doc)
    wall_assignment = next(row for row in candidate["anchor_assignments"] if row["space_id"] == "ON-WALL")
    assert wall_assignment["relation"] == "inside_inferred_wall_solid"
    assert wall_assignment["face_candidate_ids"] == []

    forged = deepcopy(candidate)
    forged["faces"][0]["area_m2"] += 1.0
    import tools.goal_loop_v2.room_polygon_candidates as module
    forged["candidate_hash"] = module._hash({key: value for key, value in forged.items() if key != "candidate_hash"})
    with pytest.raises(ValueError, match="geometry or assignment drift"):
        validate_room_polygon_candidate(doc, forged)

    promoted = deepcopy(candidate)
    promoted["room_polygon_confirmation"] = True
    with pytest.raises(ValueError, match="promoted"):
        validate_room_polygon_candidate(doc, promoted)


def test_current_1308_dry_run_exposes_multi_anchor_and_unlabeled_faces(monkeypatch):
    import tools.goal_loop_v2.room_polygon_candidates as module
    monkeypatch.undo()
    source = ROOT / "data" / "goal_loop_v2" / "references" / "1308" / "reference-coordinate-authorized-v21.json"
    wall_fact = ROOT / "data" / "goal_loop_v2" / "references" / "1308" / "wall-2d-geometry-fact-authorized-v1.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    candidate = build_room_polygon_candidate(document, source, {"authorized_wall_2d_fact": wall_fact})
    coverage = candidate["coverage"]
    assert coverage["wall_atom_count"] == 35
    assert coverage["space_anchor_count"] == 16
    assert coverage["face_candidate_count"] == 14
    assert coverage["single_anchor_face_count"] == 12
    assert coverage["multi_anchor_face_count"] == 1
    assert coverage["unlabeled_face_count"] == 1
    ambiguous = next(face for face in candidate["faces"] if face["anchor_assignment_status"] == "ambiguous_multiple_anchors")
    assert ambiguous["source_anchor_space_ids"] == ["bedroom_corridor", "kitchen", "living_hall", "lobby"]
    assert candidate["room_polygon_confirmation"] is False
    assert candidate["build_authorized"] is False
