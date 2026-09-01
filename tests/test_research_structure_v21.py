from __future__ import annotations

from copy import deepcopy

import pytest

from tests.test_research_structure_v2 import v2_fixture
from tools.fastloop_research.v2_contract import V2ContractError
from tools.fastloop_research.v21_contract import (
    assess_v21_build_readiness,
    compute_v21_structure_hash,
    upgrade_v2_to_v21,
    v21_mapping_metadata,
    validate_v21_document,
)


def _rehash(document):
    document["structure_hash"] = compute_v21_structure_hash(document)
    return document


def v21_fixture_with_gap():
    document = upgrade_v2_to_v21(v2_fixture())
    graph = document["wall_graph"]
    graph["branches"].append({"id": "BRANCH-EAST", "centerline_m": [[5, 0], [9, 0]], "status": "candidate", "evidence_refs": ["VIEW-EVIDENCE"]})
    graph["atoms"].append({"id": "WALL-EAST", "branch_id": "BRANCH-EAST", "branch_interval": [0, 1], "centerline_m": [[5, 0], [9, 0]], "thickness_m": 0.2, "base_m": 0, "height_m": 2.8, "left_space_id": "exterior", "right_space_id": "SPACE-LIVING", "start_node_id": "NODE-EAST-START", "end_node_id": "NODE-EAST-END", "status": "candidate", "evidence_refs": ["VIEW-EVIDENCE"], "assumption_ids": ["ASSUME-Z"]})
    graph["junctions"].extend([
        {"id": "NODE-EAST-START", "kind": "endpoint", "axis_point_m": [5, 0], "termination_kind": "source_termination", "incidents": [{"atom_id": "WALL-EAST", "end": "start", "role": "terminating", "attachment": "axis", "contact_point_m": [5, 0]}], "solid_union_policy": "cap", "status": "candidate", "evidence_refs": ["VIEW-EVIDENCE"]},
        {"id": "NODE-EAST-END", "kind": "endpoint", "axis_point_m": [9, 0], "termination_kind": "exterior_boundary", "incidents": [{"atom_id": "WALL-EAST", "end": "end", "role": "terminating", "attachment": "axis", "contact_point_m": [9, 0]}], "solid_union_policy": "cap", "status": "candidate", "evidence_refs": ["VIEW-EVIDENCE"]},
    ])
    terminal_before = {"id": "TERM-BEFORE", "side": "before", "atom_id": "WALL-SOUTH", "node_id": "NODE-1", "atom_end": "end", "face": "end_cap", "face_point_m": [4, 0], "evidence_refs": ["VIEW-EVIDENCE"], "status": "candidate"}
    terminal_after = {"id": "TERM-AFTER", "side": "after", "atom_id": "WALL-EAST", "node_id": "NODE-EAST-START", "atom_end": "start", "face": "start_cap", "face_point_m": [5, 0], "evidence_refs": ["VIEW-EVIDENCE"], "status": "candidate"}
    opening = {"id": "PORTAL-GAP", "source_observation": {"kind": "door", "nominal_segment_m": [[4, 0], [5, 0]], "nominal_width_m": 1.0, "anchor_id": None, "evidence_refs": ["VIEW-EVIDENCE"], "status": "candidate"}, "build_disposition": "place_in_preexisting_gap", "build_kind": "door", "host": {"mode": "preexisting_gap", "owning_wall_atom_id": None, "wall_cut_policy": "none", "gap_terminals": [terminal_before, terminal_after]}, "effective_void": {"segment_m": [[4, 0], [5, 0]], "width_m": 1.0, "sill_m": 0, "head_m": 2.1, "host_cut_scope": "none_preexisting_gap", "derivation": {"method": "fit_between_gap_terminals", "governing_geometry": "confirmed_gap_faces", "evidence_refs": ["VIEW-EVIDENCE"]}, "status": "candidate"}, "swing_direction": "not_shown", "traversable": True, "side_a_space_id": "exterior", "side_b_space_id": "SPACE-LIVING", "jamb_before": {"mode": "gap_terminal_face", "supporting_atom_ids": ["WALL-SOUTH"], "junction_id": "NODE-1", "terminal_id": "TERM-BEFORE", "face_distance_m": 0, "effective_support_m": 0.2, "evidence_refs": ["VIEW-EVIDENCE"], "status": "candidate"}, "jamb_after": {"mode": "gap_terminal_face", "supporting_atom_ids": ["WALL-EAST"], "junction_id": "NODE-EAST-START", "terminal_id": "TERM-AFTER", "face_distance_m": 0, "effective_support_m": 0.2, "evidence_refs": ["VIEW-EVIDENCE"], "status": "candidate"}, "status": "candidate", "assumption_ids": ["ASSUME-Z"], "superseded_interpretations": []}
    document["opening_contract"]["openings"].append(opening)
    document["adjacency_truth"]["status"] = "candidate"
    document["adjacency_truth"]["edges"].append({"id": "EDGE-GAP", "space_a_id": "exterior", "space_b_id": "SPACE-LIVING", "kind": "door", "opening_id": "PORTAL-GAP", "status": "candidate", "evidence_refs": ["VIEW-EVIDENCE"]})
    return _rehash(document)


def test_v2_remains_readable_and_v21_upgrade_is_explicit():
    source = v2_fixture()
    upgraded = upgrade_v2_to_v21(source)
    assert source["schema"] == "research-structure-bundle-v2"
    assert validate_v21_document(upgraded)["schema"] == "research-structure-bundle-v2.1"
    assert upgraded["structure_hash"] != source["structure_hash"]


def test_v21_accepts_coordinate_status_without_promoting_anchor_semantics():
    document=v21_fixture_with_gap();entry=next(row for row in document["source"]["anchors"] if row["id"]=="ANCHOR-ENTRY");semantic=entry["status"];entry["coordinate_status"]="source_confirmed_coordinate";_rehash(document)
    validate_v21_document(document)
    assert entry["status"]==semantic


def test_candidate_gap_portal_is_document_valid_not_ready_and_never_wall_cut():
    document = v21_fixture_with_gap()
    assert validate_v21_document(document)["structure_hash"] == document["structure_hash"]
    readiness = assess_v21_build_readiness(document)
    assert readiness["ready"] is False
    metadata = v21_mapping_metadata(document)
    assert "PORTAL-GAP" in metadata["gap_portal_ids"]
    assert "PORTAL-GAP" not in metadata["wall_mesh_inputs"]
    assert metadata["ifc_relation_expectations"]["PORTAL-GAP"]["void_relations"] == 0


def test_fully_confirmed_gap_portal_with_real_clearance_and_one_edge_can_be_ready():
    document=v21_fixture_with_gap()
    for group in (document["spaces"],document["wall_graph"]["branches"],document["wall_graph"]["atoms"],document["wall_graph"]["junctions"]):
        for row in group: row["status"]="confirmed"
    portal=next(row for row in document["opening_contract"]["openings"] if row["id"]=="PORTAL-GAP")
    portal["status"]=portal["source_observation"]["status"]=portal["effective_void"]["status"]="confirmed"; portal["traversable"]=True
    portal["jamb_before"]["status"]=portal["jamb_after"]["status"]="confirmed"
    for terminal in portal["host"]["gap_terminals"]:terminal["status"]="confirmed"
    document["adjacency_truth"]["status"]="confirmed"
    for edge in document["adjacency_truth"]["edges"]:edge["status"]="confirmed"
    document["unresolved_issues"]=[]; _rehash(document)
    assert assess_v21_build_readiness(document)["ready"] is True


@pytest.mark.parametrize("attack", ["one_terminal", "same_terminal", "off_face", "wall_cut_leak"])
def test_fake_gap_portals_fail_closed(attack):
    document = v21_fixture_with_gap()
    opening = next(row for row in document["opening_contract"]["openings"] if row["id"] == "PORTAL-GAP")
    if attack == "one_terminal": opening["host"]["gap_terminals"].pop()
    if attack == "same_terminal": opening["host"]["gap_terminals"][1] = deepcopy(opening["host"]["gap_terminals"][0])
    if attack == "off_face": opening["host"]["gap_terminals"][1]["face_point_m"] = [5.002, 0]
    if attack == "wall_cut_leak": opening["host"]["wall_cut_policy"] = "subtract_effective_void"
    _rehash(document)
    with pytest.raises(V2ContractError): validate_v21_document(document)


def test_fake_confirmed_adjacency_does_not_make_candidate_portal_ready():
    document = v21_fixture_with_gap()
    document["adjacency_truth"]["status"] = "confirmed"
    for edge in document["adjacency_truth"]["edges"]: edge["status"] = "confirmed"
    _rehash(document)
    assert assess_v21_build_readiness(document)["ready"] is False


@pytest.mark.parametrize("attack", ["missing", "duplicate", "wrong_spaces"])
def test_confirmed_gap_portal_requires_exactly_one_matching_confirmed_adjacency(attack):
    document = v21_fixture_with_gap()
    portal = next(row for row in document["opening_contract"]["openings"] if row["id"] == "PORTAL-GAP")
    portal["status"] = portal["source_observation"]["status"] = portal["effective_void"]["status"] = "confirmed"
    portal["jamb_before"]["status"] = portal["jamb_after"]["status"] = "confirmed"
    for terminal in portal["host"]["gap_terminals"]: terminal["status"] = "confirmed"
    document["adjacency_truth"]["status"] = "confirmed"
    gap_edge = next(row for row in document["adjacency_truth"]["edges"] if row["opening_id"] == "PORTAL-GAP")
    gap_edge["status"] = "confirmed"
    if attack == "missing": document["adjacency_truth"]["edges"].remove(gap_edge)
    if attack == "duplicate": document["adjacency_truth"]["edges"].append({**deepcopy(gap_edge), "id": "EDGE-GAP-DUP"})
    if attack == "wrong_spaces":
        document["spaces"].append({"id": "SPACE-OTHER", "label": "Other", "point_m": [1, 1], "status": "candidate", "evidence_refs": ["VIEW-EVIDENCE"]})
        gap_edge["space_b_id"] = "SPACE-OTHER"
    _rehash(document)
    blockers = assess_v21_build_readiness(document)["blocker_ids"]
    assert "gap_portal_adjacency_invalid:PORTAL-GAP" in blockers


def test_excluded_joinery_is_hash_bound_and_forbidden_from_structural_ids():
    document = upgrade_v2_to_v21(v2_fixture())
    feature = {"id": "JOINERY-LINE", "classification": "joinery", "geometry": {"space": "canonical_px", "primitive": "segment", "points_px": [[1, 1], [2, 1]]}, "attachments": [{"endpoint_index": 0, "relationship": "wall_face_attachment", "target_atom_id": "WALL-SOUTH", "target_face": "right_side", "contact_point_m": [1, -0.1], "status": "candidate"}, {"endpoint_index": 1, "relationship": "intentional_free_end", "target_atom_id": None, "target_face": None, "contact_point_m": None, "status": "confirmed"}], "build_policy": "exclude_from_full_height_structure", "status": "candidate", "evidence_refs": ["VIEW-EVIDENCE"], "note": "joinery"}
    document["source"]["excluded_linear_features"].append(feature); _rehash(document)
    assert "JOINERY-LINE" in v21_mapping_metadata(document)["excluded_linear_feature_ids"]
    attacked = deepcopy(document); attacked["wall_graph"]["branches"][0]["id"] = "JOINERY-LINE"; _rehash(attacked)
    with pytest.raises(V2ContractError): validate_v21_document(attacked)
    attacked=deepcopy(document); attacked["source"]["excluded_linear_features"][0]["attachments"][1]["target_atom_id"]="WALL-SOUTH"; _rehash(attacked)
    with pytest.raises(V2ContractError):validate_v21_document(attacked)
    attacked=deepcopy(document); attacked["source"]["excluded_linear_features"][0]["geometry"]["points_px"][1]=[1000,1000]; _rehash(attacked)
    with pytest.raises(V2ContractError):validate_v21_document(attacked)


def test_bridge_atom_across_gap_blocks_readiness():
    document=v21_fixture_with_gap(); graph=document["wall_graph"]
    graph["branches"].append({"id":"BRIDGE-BRANCH","centerline_m":[[4.5,-.5],[4.5,.5]],"status":"confirmed","evidence_refs":["VIEW-EVIDENCE"]})
    graph["atoms"].append({"id":"BRIDGE-ATOM","branch_id":"BRIDGE-BRANCH","branch_interval":[0,1],"centerline_m":[[4.5,-.5],[4.5,.5]],"thickness_m":.1,"base_m":0,"height_m":2.8,"left_space_id":None,"right_space_id":None,"start_node_id":"BRIDGE-N0","end_node_id":"BRIDGE-N1","status":"confirmed","evidence_refs":["VIEW-EVIDENCE"],"assumption_ids":["ASSUME-Z"]})
    graph["junctions"].extend([{"id":"BRIDGE-N0","kind":"endpoint","axis_point_m":[4.5,-.5],"termination_kind":"source_termination","incidents":[{"atom_id":"BRIDGE-ATOM","end":"start","role":"terminating","attachment":"axis","contact_point_m":[4.5,-.5]}],"solid_union_policy":"cap","status":"confirmed","evidence_refs":["VIEW-EVIDENCE"]},{"id":"BRIDGE-N1","kind":"endpoint","axis_point_m":[4.5,.5],"termination_kind":"source_termination","incidents":[{"atom_id":"BRIDGE-ATOM","end":"end","role":"terminating","attachment":"axis","contact_point_m":[4.5,.5]}],"solid_union_policy":"cap","status":"confirmed","evidence_refs":["VIEW-EVIDENCE"]}])
    _rehash(document)
    assert "gap_portal_wall_overlap:PORTAL-GAP:BRIDGE-ATOM" in assess_v21_build_readiness(document)["blocker_ids"]


def test_one_millimetre_terminal_support_blocks_readiness_even_if_reported_support_is_large():
    document=v21_fixture_with_gap(); graph=document["wall_graph"]
    branch=next(row for row in graph["branches"] if row["id"]=="BRANCH-SOUTH"); branch["centerline_m"]=[[3.999,0],[4,0]]
    atom=next(row for row in graph["atoms"] if row["id"]=="WALL-SOUTH"); atom["centerline_m"]=[[3.999,0],[4,0]]
    node=next(row for row in graph["junctions"] if row["id"]=="NODE-0"); node["axis_point_m"]=[3.999,0]; node["incidents"][0]["contact_point_m"]=[3.999,0]
    portal=next(row for row in document["opening_contract"]["openings"] if row["id"]=="PORTAL-GAP"); portal["jamb_before"]["effective_support_m"]=99.0
    _rehash(document)
    assert "gap_portal_jamb_support_insufficient:PORTAL-GAP:before" in assess_v21_build_readiness(document)["blocker_ids"]
