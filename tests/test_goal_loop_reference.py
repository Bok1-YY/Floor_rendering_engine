from __future__ import annotations

from tools.goal_loop_v2.reference import _wall_document


def test_wall_proposal_is_split_into_atomic_x_incidents() -> None:
    def wall(wall_id: str, start, end):
        return {
            "id": wall_id,
            "proposed_centerline_m": [start, end],
            "nominal_thickness_m": 0.2,
            "height_m": 2.8,
        }

    proposal = {
        "wall_graph": {
            "walls": [wall("WALL-A", [0, 1], [2, 1]), wall("WALL-B", [1, 0], [1, 2])],
            "junctions": [{"id": "JUNCTION-X", "kind": "X", "point_m": [1, 1], "incident_wall_ids": ["WALL-A", "WALL-B"]}],
            "endpoint_diagnostics": [
                {"wall_id": wall_id, "endpoint_index": endpoint, "wall_face_support_candidates": []}
                for wall_id in ("WALL-A", "WALL-B")
                for endpoint in (0, 1)
            ],
        }
    }
    graph = _wall_document(proposal)
    assert len(graph["branches"]) == 2
    assert len(graph["atoms"]) == 4
    junction = next(row for row in graph["junctions"] if row["id"] == "JUNCTION-X")
    assert junction["kind"] == "X"
    assert len(junction["incidents"]) == 4
    assert {row["attachment"] for row in junction["incidents"]} == {"axis"}


def test_reference_adapter_source_contains_no_sample_specific_ids() -> None:
    source = (__import__("pathlib").Path(__file__).parents[1] / "tools" / "goal_loop_v2" / "reference.py").read_text(encoding="utf-8")
    import re
    assert not re.search(r"1308|121m2|\bOP\d|\bWB\d", source)
