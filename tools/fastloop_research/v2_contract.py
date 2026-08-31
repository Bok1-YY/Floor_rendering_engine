"""Document and build-readiness gates for ``research-structure-bundle-v2``.

V2 deliberately separates evidence preservation from model execution.  A
candidate document may retain unresolved observations without becoming a
Blender/IFC input.  ``assess_v2_build_readiness`` is the only promotion gate.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import math
import re
from typing import Any, Mapping

from .contract import canonical_json


SCHEMA = "research-structure-bundle-v2"
SOURCE_SCHEMA = "source-provenance-v3"
WALL_SCHEMA = "atomic-wall-junction-graph-v2"
OPENING_SCHEMA = "opening-contract-v2"
ADJACENCY_SCHEMA = "adjacency-truth-v2"
ASSUMPTION_SCHEMA = "assumption-registry-v2"
TOP_KEYS = {
    "schema", "project", "source_hash", "structure_hash", "source",
    "outer_boundary", "spaces", "wall_graph", "opening_contract",
    "adjacency_truth", "assumptions", "unresolved_issues",
}
STATUSES = {"confirmed", "candidate", "unresolved", "legacy_confirmed"}
ANCHOR_STATUSES = {"human_confirmed", "source_confirmed", "source_candidate", "derived_candidate", "unresolved", "legacy_confirmed"}
BUILD_DISPOSITIONS = {"cut", "retain_solid", "evidence_only", "exclude_pending_resolution"}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,95}$")


class V2ContractError(ValueError):
    """Raised when an evidence document is malformed or internally false."""


def _fail(message: str) -> None:
    raise V2ContractError(message)


def _exact(value: Any, keys: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{path}: expected object")
    actual = set(value)
    if actual != keys:
        _fail(f"{path}: exact keys required; missing={sorted(keys-actual)}, extra={sorted(actual-keys)}")
    return value


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        _fail(f"{path}: invalid stable ID")
    return value


def _hash(value: Any, path: str) -> str:
    if not isinstance(value, str) or not HASH_RE.fullmatch(value):
        _fail(f"{path}: expected lowercase SHA-256")
    return value


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        _fail(f"{path}: expected finite number")
    return float(value)


def _point(value: Any, path: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        _fail(f"{path}: expected [x,y]")
    return _number(value[0], f"{path}[0]"), _number(value[1], f"{path}[1]")


def _matrix(value: Any, path: str) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != 3 or any(not isinstance(row, list) or len(row) != 3 for row in value):
        _fail(f"{path}: expected 3x3 matrix")
    matrix = [[_number(item, f"{path}[{r}][{c}]") for c, item in enumerate(row)] for r, row in enumerate(value)]
    if any(abs(item) > 1e-12 for item in matrix[2][:2]) or not math.isclose(matrix[2][2], 1.0, abs_tol=1e-12):
        _fail(f"{path}: affine last row must be [0,0,1]")
    return matrix


def _status(value: Any, path: str) -> str:
    if value not in STATUSES:
        _fail(f"{path}: unsupported status")
    return str(value)


def _unique_ids(rows: Any, path: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(rows, list):
        _fail(f"{path}: expected array")
    result: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            _fail(f"{path}[{index}]: expected object")
        stable_id = _identifier(row.get("id"), f"{path}[{index}].id")
        if stable_id in result:
            _fail(f"{path}[{index}].id: duplicate {stable_id}")
        result[stable_id] = row
    return result


def compute_v2_structure_hash(document: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(document))
    payload.pop("structure_hash", None)
    return hashlib.sha256(canonical_json(_hash_normalize(payload))).hexdigest()


_ID_REFERENCE_ARRAY_KEYS = {
    "evidence_refs", "assumption_ids", "supporting_atom_ids", "entity_refs",
}
_GEOMETRY_ARRAY_KEYS = {
    "polygon_m", "centerline_m", "nominal_segment_m", "segment_m",
    "points_px", "branch_interval", "size_px", "canonical_px", "metric_m",
    "axis_point_m", "contact_point_m", "raw_to_canonical_3x3",
    "canonical_to_view_3x3", "canonical_px_to_metric_3x3",
}


def _hash_normalize(value: Any, key: str = "") -> Any:
    """Apply the v2 schema's semantic array-order policy before hashing."""

    if isinstance(value, Mapping):
        return {name: _hash_normalize(child, name) for name, child in value.items()}
    if isinstance(value, list):
        normalized = [_hash_normalize(child, key) for child in value]
        if key in _GEOMETRY_ARRAY_KEYS:
            return normalized
        if key in _ID_REFERENCE_ARRAY_KEYS and all(isinstance(child, str) for child in normalized):
            return sorted(normalized)
        if all(isinstance(child, Mapping) and isinstance(child.get("id"), str) for child in normalized):
            return sorted(normalized, key=lambda child: child["id"])
        if key == "incidents" and all(isinstance(child, Mapping) for child in normalized):
            return sorted(normalized, key=lambda child: (str(child.get("atom_id")), str(child.get("end"))))
        if key == "targets" and all(isinstance(child, Mapping) for child in normalized):
            return sorted(normalized, key=lambda child: (str(child.get("entity_kind")), str(child.get("entity_id")), str(child.get("field"))))
        return normalized
    return value


def _validate_source(source: Any, source_hash: str) -> dict[str, Any]:
    source = _exact(source, {"schema", "original", "canonical", "views", "metric_registration", "anchors"}, "source")
    if source["schema"] != SOURCE_SCHEMA:
        _fail(f"source.schema: expected {SOURCE_SCHEMA}")

    original = _exact(source["original"], {"file_sha256", "pixel_sha256", "size_px", "exif_orientation"}, "source.original")
    if _hash(original["file_sha256"], "source.original.file_sha256") != source_hash:
        _fail("source.original.file_sha256: must equal source_hash")
    _hash(original["pixel_sha256"], "source.original.pixel_sha256")
    original_size = original["size_px"]
    if not isinstance(original_size, list) or len(original_size) != 2 or any(int(item) <= 0 for item in original_size):
        _fail("source.original.size_px: expected positive [w,h]")
    if isinstance(original["exif_orientation"], bool) or not isinstance(original["exif_orientation"], int) or not 1 <= original["exif_orientation"] <= 8:
        _fail("source.original.exif_orientation: expected 1..8")

    canonical = _exact(source["canonical"], {"file_sha256", "pixel_sha256", "size_px", "orientation_policy", "raw_to_canonical_3x3"}, "source.canonical")
    _hash(canonical["file_sha256"], "source.canonical.file_sha256")
    _hash(canonical["pixel_sha256"], "source.canonical.pixel_sha256")
    if canonical["orientation_policy"] not in {"raw_identity", "exif_transpose", "ignore_invalid_exif_user_confirmed_raw"}:
        _fail("source.canonical.orientation_policy: unsupported")
    _matrix(canonical["raw_to_canonical_3x3"], "source.canonical.raw_to_canonical_3x3")
    canonical_size = canonical["size_px"]
    if not isinstance(canonical_size, list) or len(canonical_size) != 2 or any(int(item) <= 0 for item in canonical_size):
        _fail("source.canonical.size_px: expected positive [w,h]")
    if canonical["orientation_policy"] in {"raw_identity", "ignore_invalid_exif_user_confirmed_raw"} and canonical["pixel_sha256"] != original["pixel_sha256"]:
        _fail("source.canonical.pixel_sha256: raw-identity policy must preserve decoded pixels")

    view_ids: set[str] = set()
    if not isinstance(source["views"], list) or not source["views"]:
        _fail("source.views: expected non-empty array")
    for index, raw in enumerate(source["views"]):
        path = f"source.views[{index}]"
        view = _exact(raw, {"id", "role", "file_sha256", "pixel_sha256", "size_px", "canonical_to_view_3x3"}, path)
        view_id = _identifier(view["id"], f"{path}.id")
        if view_id in view_ids:
            _fail(f"{path}.id: duplicate")
        view_ids.add(view_id)
        if view["role"] not in {"normalized_evidence", "crop", "overlay_base"}:
            _fail(f"{path}.role: unsupported")
        _hash(view["file_sha256"], f"{path}.file_sha256")
        _hash(view["pixel_sha256"], f"{path}.pixel_sha256")
        if not isinstance(view["size_px"], list) or len(view["size_px"]) != 2 or any(int(item) <= 0 for item in view["size_px"]):
            _fail(f"{path}.size_px: expected positive [w,h]")
        _matrix(view["canonical_to_view_3x3"], f"{path}.canonical_to_view_3x3")

    registration = _exact(source["metric_registration"], {"model", "solver", "canonical_px_to_metric_3x3", "control_points", "max_residual_m", "tolerance_m", "scale_anchor_id"}, "source.metric_registration")
    if registration["model"] != "affine-2d" or registration["solver"] not in {"exact", "least_squares"}:
        _fail("source.metric_registration: unsupported model/solver")
    affine = _matrix(registration["canonical_px_to_metric_3x3"], "source.metric_registration.canonical_px_to_metric_3x3")
    determinant = affine[0][0] * affine[1][1] - affine[0][1] * affine[1][0]
    if abs(determinant) <= 1e-12:
        _fail("source.metric_registration: affine transform must be invertible")
    tolerance = _number(registration["tolerance_m"], "source.metric_registration.tolerance_m")
    residual = _number(registration["max_residual_m"], "source.metric_registration.max_residual_m")
    if tolerance <= 0 or residual > tolerance + 1e-12:
        _fail("source.metric_registration: residual exceeds tolerance")

    anchor_by_id = _unique_ids(source["anchors"], "source.anchors")
    scale_id = _identifier(registration["scale_anchor_id"], "source.metric_registration.scale_anchor_id")
    if scale_id not in anchor_by_id:
        _fail("source.metric_registration.scale_anchor_id: unknown anchor")
    for anchor_id, raw in anchor_by_id.items():
        path = f"source.anchors[{anchor_id}]"
        anchor = _exact(raw, {"id", "kind", "geometry", "measured_distance_mm", "status", "evidence_asset_id", "note"}, path)
        if anchor["kind"] not in {"scale", "space", "entrance", "opening", "wall_axis", "outer_boundary", "fixed_feature", "ignore", "dimension"}:
            _fail(f"{path}.kind: unsupported")
        if anchor["status"] not in ANCHOR_STATUSES:
            _fail(f"{path}.status: unsupported anchor status")
        geometry = _exact(anchor["geometry"], {"space", "primitive", "points_px"}, f"{path}.geometry")
        if geometry["space"] != "canonical_px" or geometry["primitive"] not in {"point", "segment", "polyline", "polygon", "bbox"}:
            _fail(f"{path}.geometry: unsupported")
        if not isinstance(geometry["points_px"], list) or not geometry["points_px"]:
            _fail(f"{path}.geometry.points_px: expected non-empty")
        for point_index, point in enumerate(geometry["points_px"]):
            x, y = _point(point, f"{path}.geometry.points_px[{point_index}]")
            if not 0 <= x <= canonical_size[0] or not 0 <= y <= canonical_size[1]:
                _fail(f"{path}.geometry.points_px[{point_index}]: outside canonical image")
        if anchor["evidence_asset_id"] not in view_ids:
            _fail(f"{path}.evidence_asset_id: unknown view")
        if anchor["kind"] == "scale":
            if len(geometry["points_px"]) != 2 or _number(anchor["measured_distance_mm"], f"{path}.measured_distance_mm") <= 0:
                _fail(f"{path}: scale requires segment and positive distance")
        elif anchor["measured_distance_mm"] is not None:
            _fail(f"{path}.measured_distance_mm: only scale anchors may set distance")

    controls = registration["control_points"]
    if not isinstance(controls, list) or len(controls) < 3:
        _fail("source.metric_registration.control_points: expected at least three")
    canonical_controls: list[tuple[float, float]] = []
    for index, raw in enumerate(controls):
        control = _exact(raw, {"id", "canonical_px", "metric_m", "evidence_refs"}, f"source.metric_registration.control_points[{index}]")
        px = _point(control["canonical_px"], "control.canonical_px")
        canonical_controls.append(px)
        expected = _point(control["metric_m"], "control.metric_m")
        actual = (affine[0][0]*px[0] + affine[0][1]*px[1] + affine[0][2], affine[1][0]*px[0] + affine[1][1]*px[1] + affine[1][2])
        if math.dist(actual, expected) > tolerance + 1e-12:
            _fail("source.metric_registration.control_points: affine residual exceeds tolerance")
    non_collinear = any(
        abs((b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])) > 1e-9
        for first, a in enumerate(canonical_controls)
        for second, b in enumerate(canonical_controls[first+1:], first+1)
        for c in canonical_controls[second+1:]
    )
    if not non_collinear:
        _fail("source.metric_registration.control_points: require three non-collinear controls")
    return deepcopy(dict(source))


def validate_v2_document(document: Mapping[str, Any]) -> dict[str, Any]:
    root = _exact(document, TOP_KEYS, "bundle")
    if root["schema"] != SCHEMA:
        _fail(f"bundle.schema: expected {SCHEMA}")
    source_hash = _hash(root["source_hash"], "bundle.source_hash")
    supplied_hash = _hash(root["structure_hash"], "bundle.structure_hash")
    if supplied_hash != compute_v2_structure_hash(root):
        _fail("bundle.structure_hash: content hash mismatch")
    project = _exact(root["project"], {"project_id", "revision", "sample_id"}, "bundle.project")
    _identifier(project["project_id"], "bundle.project.project_id")
    _identifier(project["sample_id"], "bundle.project.sample_id")
    if isinstance(project["revision"], bool) or not isinstance(project["revision"], int) or project["revision"] < 1:
        _fail("bundle.project.revision: expected positive integer")
    _validate_source(root["source"], source_hash)

    outer = _exact(root["outer_boundary"], {"polygon_m", "status", "evidence_refs"}, "outer_boundary")
    if not isinstance(outer["polygon_m"], list) or len(outer["polygon_m"]) < 4:
        _fail("outer_boundary.polygon_m: expected at least four points")
    for index, point in enumerate(outer["polygon_m"]):
        _point(point, f"outer_boundary.polygon_m[{index}]")
    _status(outer["status"], "outer_boundary.status")

    spaces = _unique_ids(root["spaces"], "spaces")
    for space_id, raw in spaces.items():
        space = _exact(raw, {"id", "label", "point_m", "status", "evidence_refs"}, f"spaces[{space_id}]")
        if not isinstance(space["label"], str) or not space["label"].strip():
            _fail(f"spaces[{space_id}].label: required")
        _point(space["point_m"], f"spaces[{space_id}].point_m")
        _status(space["status"], f"spaces[{space_id}].status")

    graph = _exact(root["wall_graph"], {"version", "branches", "atoms", "junctions"}, "wall_graph")
    if graph["version"] != WALL_SCHEMA:
        _fail(f"wall_graph.version: expected {WALL_SCHEMA}")
    branches = _unique_ids(graph["branches"], "wall_graph.branches")
    atoms = _unique_ids(graph["atoms"], "wall_graph.atoms")
    junctions = _unique_ids(graph["junctions"], "wall_graph.junctions")
    for branch_id, raw in branches.items():
        branch = _exact(raw, {"id", "centerline_m", "status", "evidence_refs"}, f"wall_graph.branches[{branch_id}]")
        if not isinstance(branch["centerline_m"], list) or len(branch["centerline_m"]) < 2:
            _fail(f"wall_graph.branches[{branch_id}].centerline_m: invalid")
        _status(branch["status"], f"wall_graph.branches[{branch_id}].status")
    for atom_id, raw in atoms.items():
        atom = _exact(raw, {"id", "branch_id", "branch_interval", "centerline_m", "thickness_m", "base_m", "height_m", "left_space_id", "right_space_id", "start_node_id", "end_node_id", "status", "evidence_refs", "assumption_ids"}, f"wall_graph.atoms[{atom_id}]")
        if atom["branch_id"] not in branches:
            _fail(f"wall_graph.atoms[{atom_id}].branch_id: unknown")
        interval = atom["branch_interval"]
        if not isinstance(interval, list) or len(interval) != 2 or not 0 <= _number(interval[0], "branch interval") < _number(interval[1], "branch interval") <= 1:
            _fail(f"wall_graph.atoms[{atom_id}].branch_interval: invalid")
        if not isinstance(atom["centerline_m"], list) or len(atom["centerline_m"]) != 2:
            _fail(f"wall_graph.atoms[{atom_id}].centerline_m: invalid")
        _point(atom["centerline_m"][0], "atom centerline")
        _point(atom["centerline_m"][1], "atom centerline")
        if _number(atom["thickness_m"], "atom thickness") <= 0 or _number(atom["height_m"], "atom height") <= 0:
            _fail(f"wall_graph.atoms[{atom_id}]: invalid dimensions")
        _status(atom["status"], f"wall_graph.atoms[{atom_id}].status")
    incident_coverage: set[tuple[str, str]] = set()
    for junction_id, raw in junctions.items():
        junction = _exact(raw, {"id", "kind", "axis_point_m", "termination_kind", "incidents", "solid_union_policy", "status", "evidence_refs"}, f"wall_graph.junctions[{junction_id}]")
        if junction["kind"] not in {"endpoint", "L", "T", "X", "continuation"}:
            _fail(f"wall_graph.junctions[{junction_id}].kind: unsupported")
        _point(junction["axis_point_m"], "junction axis point")
        _status(junction["status"], f"wall_graph.junctions[{junction_id}].status")
        if not isinstance(junction["incidents"], list) or not junction["incidents"]:
            _fail(f"wall_graph.junctions[{junction_id}].incidents: required")
        for index, incident in enumerate(junction["incidents"]):
            incident = _exact(incident, {"atom_id", "end", "role", "attachment", "contact_point_m"}, f"junction incident {junction_id}[{index}]")
            atom_id = _identifier(incident["atom_id"], "junction incident atom")
            if atom_id not in atoms or incident["end"] not in {"start", "end"}:
                _fail(f"junction incident {junction_id}: unknown atom/end")
            key = (atom_id, incident["end"])
            if key in incident_coverage:
                _fail(f"junction incident {junction_id}: duplicate atom endpoint")
            incident_coverage.add(key)
            if atoms[atom_id][f"{incident['end']}_node_id"] != junction_id:
                _fail(f"junction incident {junction_id}: atom node reference mismatch")
            _point(incident["contact_point_m"], "junction contact point")
    expected_endpoints = {(atom_id, end) for atom_id in atoms for end in ("start", "end")}
    if incident_coverage != expected_endpoints:
        _fail("wall_graph.junctions: every atom endpoint must have exactly one incident")

    opening_contract = _exact(root["opening_contract"], {"version", "minimum_jamb_support_m", "openings"}, "opening_contract")
    if opening_contract["version"] != OPENING_SCHEMA or not math.isclose(_number(opening_contract["minimum_jamb_support_m"], "minimum jamb"), 0.05, abs_tol=1e-12):
        _fail("opening_contract: unsupported version/minimum")
    openings = _unique_ids(opening_contract["openings"], "opening_contract.openings")
    cut_openings: set[str] = set()
    for opening_id, raw in openings.items():
        opening = _exact(raw, {"id", "source_observation", "build_disposition", "build_kind", "owning_wall_atom_id", "effective_void", "swing_direction", "traversable", "side_a_space_id", "side_b_space_id", "jamb_before", "jamb_after", "status", "assumption_ids"}, f"opening_contract.openings[{opening_id}]")
        _status(opening["status"], f"opening_contract.openings[{opening_id}].status")
        if opening["build_disposition"] not in BUILD_DISPOSITIONS:
            _fail(f"opening_contract.openings[{opening_id}].build_disposition: unsupported")
        observation = _exact(opening["source_observation"], {"kind", "nominal_segment_m", "nominal_width_m", "anchor_id", "evidence_refs", "status"}, f"opening {opening_id} observation")
        _status(observation["status"], f"opening {opening_id} observation status")
        if not isinstance(observation["nominal_segment_m"], list) or len(observation["nominal_segment_m"]) != 2:
            _fail(f"opening {opening_id}: invalid nominal segment")
        _point(observation["nominal_segment_m"][0], "opening nominal segment")
        _point(observation["nominal_segment_m"][1], "opening nominal segment")
        if _number(observation["nominal_width_m"], "opening nominal width") <= 0:
            _fail(f"opening {opening_id}: invalid nominal width")
        if observation["anchor_id"] is not None and observation["anchor_id"] not in {item["id"] for item in root["source"]["anchors"]}:
            _fail(f"opening {opening_id}: unknown source anchor")
        if opening["build_disposition"] == "cut":
            cut_openings.add(opening_id)
            if opening["build_kind"] not in {"entrance", "door", "window"} or opening["owning_wall_atom_id"] not in atoms or opening["effective_void"] is None:
                _fail(f"opening {opening_id}: cut requires kind, owner and effective void")
            effective = _exact(opening["effective_void"], {"segment_m", "width_m", "sill_m", "head_m", "host_cut_scope", "status"}, f"opening {opening_id} effective_void")
            _status(effective["status"], f"opening {opening_id} effective status")
            if effective["host_cut_scope"] != "owning_wall_atom_only" or _number(effective["head_m"], "head") <= _number(effective["sill_m"], "sill"):
                _fail(f"opening {opening_id}: invalid effective void")
            for support_name in ("jamb_before", "jamb_after"):
                support = opening[support_name]
                if not isinstance(support, Mapping):
                    _fail(f"opening {opening_id}.{support_name}: required for cut")
                support = _exact(support, {"mode", "supporting_atom_ids", "junction_id", "face_distance_m", "effective_support_m", "evidence_refs", "status"}, f"opening {opening_id}.{support_name}")
                if support["mode"] not in {"same_wall_solid", "return_wall_face", "crossing_wall_jamb"}:
                    _fail(f"opening {opening_id}.{support_name}.mode: unsupported")
                if not isinstance(support["supporting_atom_ids"], list) or any(atom not in atoms for atom in support["supporting_atom_ids"]):
                    _fail(f"opening {opening_id}.{support_name}: unknown supporting atom")
                if support["junction_id"] is not None and support["junction_id"] not in junctions:
                    _fail(f"opening {opening_id}.{support_name}: unknown junction")
                _status(support["status"], f"opening {opening_id}.{support_name}.status")
            if opening["side_a_space_id"] not in {*spaces, "exterior"} or opening["side_b_space_id"] not in {*spaces, "exterior"} or opening["side_a_space_id"] == opening["side_b_space_id"]:
                _fail(f"opening {opening_id}: invalid side spaces")
            if opening["build_kind"] in {"entrance", "door"} and opening["traversable"] is not True:
                _fail(f"opening {opening_id}: door/entrance cut must be traversable")
            if opening["build_kind"] == "window" and opening["traversable"] is not False:
                _fail(f"opening {opening_id}: window cut must be non-traversable")
        else:
            forbidden = (opening["build_kind"], opening["owning_wall_atom_id"], opening["effective_void"], opening["jamb_before"], opening["jamb_after"], opening["swing_direction"], opening["side_a_space_id"], opening["side_b_space_id"])
            if any(item is not None for item in forbidden):
                _fail(f"opening {opening_id}: non-cut evidence must not create build geometry")
            if opening["traversable"] not in {False, None}:
                _fail(f"opening {opening_id}: non-cut evidence cannot be traversable")

    adjacency = _exact(root["adjacency_truth"], {"version", "status", "entrance_opening_id", "edges"}, "adjacency_truth")
    if adjacency["version"] != ADJACENCY_SCHEMA:
        _fail(f"adjacency_truth.version: expected {ADJACENCY_SCHEMA}")
    _status(adjacency["status"], "adjacency_truth.status")
    entrance_id = adjacency["entrance_opening_id"]
    if entrance_id is not None and entrance_id not in openings:
        _fail("adjacency_truth.entrance_opening_id: unknown opening")
    if adjacency["status"] == "confirmed" and entrance_id is not None and (entrance_id not in cut_openings or openings[entrance_id]["build_kind"] != "entrance"):
        _fail("adjacency_truth.entrance_opening_id: confirmed truth must reference a cut entrance")
    edges = _unique_ids(adjacency["edges"], "adjacency_truth.edges")
    for edge_id, raw in edges.items():
        edge = _exact(raw, {"id", "space_a_id", "space_b_id", "kind", "opening_id", "status", "evidence_refs"}, f"adjacency edge {edge_id}")
        if edge["space_a_id"] not in spaces and edge["space_a_id"] != "exterior" or edge["space_b_id"] not in spaces and edge["space_b_id"] != "exterior":
            _fail(f"adjacency edge {edge_id}: unknown space")
        if edge["space_a_id"] == edge["space_b_id"] or edge["kind"] not in {"door", "open_passage"}:
            _fail(f"adjacency edge {edge_id}: invalid spaces/kind")
        edge_status = _status(edge["status"], f"adjacency edge {edge_id}.status")
        if edge["kind"] == "door" and edge["opening_id"] not in openings:
            _fail(f"adjacency edge {edge_id}: door requires a known opening")
        if edge["kind"] == "door" and edge_status == "confirmed":
            if edge["opening_id"] not in cut_openings:
                _fail(f"adjacency edge {edge_id}: confirmed door requires cut opening")
            opening = openings[edge["opening_id"]]
            if opening["build_kind"] not in {"entrance", "door"} or {edge["space_a_id"], edge["space_b_id"]} != {opening["side_a_space_id"], opening["side_b_space_id"]}:
                _fail(f"adjacency edge {edge_id}: spaces/kind differ from opening")
        if edge["kind"] == "open_passage" and edge["opening_id"] is not None:
            _fail(f"adjacency edge {edge_id}: open passage requires null opening")

    assumptions = _exact(root["assumptions"], {"schema", "research_only", "items"}, "assumptions")
    if assumptions["schema"] != ASSUMPTION_SCHEMA or assumptions["research_only"] is not True:
        _fail("assumptions: unsupported schema/policy")
    assumption_rows = _unique_ids(assumptions["items"], "assumptions.items")
    for assumption_id, raw in assumption_rows.items():
        assumption = _exact(raw, {"id", "category", "targets", "value", "unit", "basis", "status", "build_policy", "evidence_refs", "disclosure"}, f"assumptions.items[{assumption_id}]")
        if assumption["category"] not in {"z_geometry", "wall_thickness", "scope", "semantic", "legacy_migration"} or assumption["basis"] not in {"research_default", "source_inference", "legacy_equal_nominal_effective", "human_accepted_research_assumption"}:
            _fail(f"assumptions.items[{assumption_id}]: unsupported category/basis")
        if assumption["status"] not in {"unverified", "human_accepted", "superseded"} or assumption["build_policy"] not in {"allow_research_only", "block_build"}:
            _fail(f"assumptions.items[{assumption_id}]: unsupported status/policy")
        if not isinstance(assumption["targets"], list) or not assumption["targets"] or not isinstance(assumption["disclosure"], str) or not assumption["disclosure"].strip():
            _fail(f"assumptions.items[{assumption_id}]: targets/disclosure required")
        for target in assumption["targets"]:
            target = _exact(target, {"entity_kind", "entity_id", "field"}, f"assumptions.items[{assumption_id}].targets")
            entity_id = target["entity_id"]
            known = {
                "bundle": {None}, "wall_branch": set(branches), "wall_atom": set(atoms),
                "opening": set(openings), "space": set(spaces), "outer_boundary": {None},
            }
            if target["entity_kind"] not in known or entity_id not in known[target["entity_kind"]]:
                _fail(f"assumptions.items[{assumption_id}]: unknown target")
    known_assumptions = set(assumption_rows)
    for atom_id, atom in atoms.items():
        if any(item not in known_assumptions for item in atom["assumption_ids"]):
            _fail(f"wall_graph.atoms[{atom_id}].assumption_ids: unknown assumption")
    for opening_id, opening in openings.items():
        if any(item not in known_assumptions for item in opening["assumption_ids"]):
            _fail(f"opening_contract.openings[{opening_id}].assumption_ids: unknown assumption")
    issue_rows = _unique_ids(root["unresolved_issues"], "unresolved_issues")
    for issue_id, raw in issue_rows.items():
        issue = _exact(raw, {"id", "severity", "category", "entity_refs", "status", "message", "blocks_reference_freeze", "blocks_build", "evidence_refs"}, f"unresolved_issues[{issue_id}]")
        if issue["severity"] not in {"hard", "advisory"} or issue["status"] not in {"open", "accepted_research_assumption", "resolved", "superseded"}:
            _fail(f"unresolved_issues[{issue_id}]: unsupported severity/status")
        if not isinstance(issue["blocks_reference_freeze"], bool) or not isinstance(issue["blocks_build"], bool) or not isinstance(issue["message"], str) or not issue["message"].strip():
            _fail(f"unresolved_issues[{issue_id}]: invalid blocking/message fields")
    return deepcopy(dict(root))


def assess_v2_build_readiness(document: Mapping[str, Any]) -> dict[str, Any]:
    doc = validate_v2_document(document)
    blockers: set[str] = set()

    def require_confirmed(status: str, blocker: str) -> None:
        if status != "confirmed":
            blockers.add(blocker)

    require_confirmed(doc["outer_boundary"]["status"], "outer_boundary_not_confirmed")
    for row in doc["spaces"]:
        require_confirmed(row["status"], f"space_not_confirmed:{row['id']}")
    group_labels = {"branches": "branch", "atoms": "atom", "junctions": "junction"}
    for group in ("branches", "atoms", "junctions"):
        for row in doc["wall_graph"][group]:
            require_confirmed(row["status"], f"wall_{group_labels[group]}_not_confirmed:{row['id']}")

    build_openings: list[str] = []
    evidence_only: list[str] = []
    for opening in doc["opening_contract"]["openings"]:
        disposition = opening["build_disposition"]
        if disposition == "cut":
            build_openings.append(opening["id"])
            require_confirmed(opening["status"], f"opening_not_confirmed:{opening['id']}")
            require_confirmed(opening["source_observation"]["status"], f"opening_source_not_confirmed:{opening['id']}")
            require_confirmed(opening["effective_void"]["status"], f"opening_void_not_confirmed:{opening['id']}")
            for side in ("jamb_before", "jamb_after"):
                require_confirmed(opening[side]["status"], f"opening_support_not_confirmed:{opening['id']}:{side}")
        elif disposition == "evidence_only":
            evidence_only.append(opening["id"])
            require_confirmed(opening["status"], f"evidence_only_not_confirmed:{opening['id']}")
        elif disposition == "retain_solid":
            require_confirmed(opening["status"], f"retain_solid_not_confirmed:{opening['id']}")
            require_confirmed(opening["source_observation"]["status"], f"retain_solid_source_not_confirmed:{opening['id']}")
        elif disposition == "exclude_pending_resolution":
            blockers.add(f"opening_pending_resolution:{opening['id']}")

    require_confirmed(doc["adjacency_truth"]["status"], "adjacency_not_confirmed")
    for edge in doc["adjacency_truth"]["edges"]:
        require_confirmed(edge["status"], f"adjacency_edge_not_confirmed:{edge['id']}")
    for assumption in doc["assumptions"]["items"]:
        if assumption["build_policy"] == "block_build" or assumption["status"] != "human_accepted":
            blockers.add(f"assumption_blocks_build:{assumption['id']}")
    for issue in doc["unresolved_issues"]:
        if issue["status"] == "open" and issue["blocks_build"]:
            blockers.add(issue["id"])
    return {
        "schema": "research-structure-v2-readiness-v1",
        "ready": not blockers,
        "blocker_ids": sorted(blockers),
        "build_opening_ids": sorted(build_openings),
        "evidence_only_opening_ids": sorted(evidence_only),
        "structure_hash": doc["structure_hash"],
    }


__all__ = [
    "SCHEMA", "V2ContractError", "assess_v2_build_readiness",
    "compute_v2_structure_hash", "validate_v2_document",
]
