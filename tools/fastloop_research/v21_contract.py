"""Add-only v2.1 gap-portal document validation and readiness metadata."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from typing import Any, Mapping

from .contract import canonical_json
from .v2_contract import V2ContractError, compute_v2_structure_hash, validate_v2_document


SCHEMA = "research-structure-bundle-v2.1"
SOURCE_SCHEMA = "source-provenance-v3.1"
OPENING_SCHEMA = "opening-contract-v2.1"
TOP_KEYS = {"schema", "project", "source_hash", "structure_hash", "source", "outer_boundary", "spaces", "wall_graph", "opening_contract", "adjacency_truth", "assumptions", "unresolved_issues"}
OPENING_KEYS = {"id", "source_observation", "build_disposition", "build_kind", "host", "effective_void", "swing_direction", "traversable", "side_a_space_id", "side_b_space_id", "jamb_before", "jamb_after", "status", "assumption_ids", "superseded_interpretations"}
HOST_KEYS = {"mode", "owning_wall_atom_id", "wall_cut_policy", "gap_terminals"}
TERMINAL_KEYS = {"id", "side", "atom_id", "node_id", "atom_end", "face", "face_point_m", "evidence_refs", "status"}
VOID_KEYS = {"segment_m", "width_m", "sill_m", "head_m", "host_cut_scope", "derivation", "status"}
DERIVATION_KEYS = {"method", "governing_geometry", "evidence_refs"}
JAMB_KEYS = {"mode", "supporting_atom_ids", "junction_id", "terminal_id", "face_distance_m", "effective_support_m", "evidence_refs", "status"}
HISTORY_KEYS = {"id", "origin", "captured_from_structure_hash", "captured_from_artifact_sha256", "captured_payload_sha256", "former_source_observation", "former_host", "former_effective_void", "former_jamb_before", "former_jamb_after", "status", "reason_code", "evidence_refs"}
EXCLUDED_KEYS = {"id", "classification", "geometry", "attachments", "build_policy", "status", "evidence_refs", "note"}


def _fail(message: str):
    raise V2ContractError(message)


def _exact(value, keys, path):
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail(f"{path}: exact keys required")
    return value


def _point(value, path):
    if not isinstance(value, list) or len(value) != 2 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(float(x)) for x in value):
        _fail(f"{path}: expected finite [x,y]")
    return float(value[0]), float(value[1])


def _hash(value, path):
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        _fail(f"{path}: expected lowercase sha256")
    return value


def compute_v21_structure_hash(document: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(document))
    payload.pop("structure_hash", None)
    for path in (("spaces",), ("wall_graph", "branches"), ("wall_graph", "atoms"), ("wall_graph", "junctions"), ("opening_contract", "openings"), ("assumptions", "items"), ("unresolved_issues",), ("source", "anchors"), ("source", "excluded_linear_features")):
        parent = payload
        for key in path[:-1]:
            parent = parent[key]
        if isinstance(parent.get(path[-1]), list) and all(isinstance(item, Mapping) and "id" in item for item in parent[path[-1]]):
            parent[path[-1]] = sorted(parent[path[-1]], key=lambda item: item["id"])
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _to_v2_projection(document: Mapping[str, Any]) -> dict[str, Any]:
    projected = deepcopy(dict(document))
    projected["schema"] = "research-structure-bundle-v2"
    projected["source"]["schema"] = "source-provenance-v3"
    projected["source"].pop("excluded_linear_features", None)
    projected["opening_contract"]["version"] = "opening-contract-v2"
    rows = []
    for opening in projected["opening_contract"]["openings"]:
        host = opening.pop("host")
        opening.pop("superseded_interpretations")
        effective = opening.get("effective_void")
        if effective is not None:
            effective.pop("derivation")
        can_project_cut = opening["build_disposition"] == "cut" and host and host["mode"] == "wall_cut" and opening.get("side_a_space_id") is not None and opening.get("side_b_space_id") is not None and (opening.get("build_kind") == "window" or opening.get("traversable") is True)
        if can_project_cut:
            opening["owning_wall_atom_id"] = host["owning_wall_atom_id"]
            for side in ("jamb_before", "jamb_after"):
                if opening[side] is not None:
                    opening[side].pop("terminal_id")
        else:
            opening.update(build_disposition="exclude_pending_resolution", build_kind=None, owning_wall_atom_id=None, effective_void=None, swing_direction=None, traversable=False, side_a_space_id=None, side_b_space_id=None, jamb_before=None, jamb_after=None)
        rows.append(opening)
    projected["opening_contract"]["openings"] = rows
    for edge in projected["adjacency_truth"]["edges"]:
        if edge["status"] == "confirmed" and edge.get("opening_id") and next((row for row in rows if row["id"] == edge["opening_id"]), {}).get("build_disposition") != "cut":
            edge["status"] = "candidate"
    if projected["adjacency_truth"]["status"] == "confirmed" and any(edge["status"] != "confirmed" for edge in projected["adjacency_truth"]["edges"]):
        projected["adjacency_truth"]["status"] = "candidate"
    projected["structure_hash"] = compute_v2_structure_hash(projected)
    return projected


def upgrade_v2_to_v21(document: Mapping[str, Any]) -> dict[str, Any]:
    source = validate_v2_document(document)
    result = deepcopy(source)
    result["schema"] = SCHEMA
    result["source"]["schema"] = SOURCE_SCHEMA
    result["source"]["excluded_linear_features"] = []
    result["opening_contract"]["version"] = OPENING_SCHEMA
    for opening in result["opening_contract"]["openings"]:
        owner = opening.pop("owning_wall_atom_id")
        history = []
        effective = opening.get("effective_void")
        former = {"host": {"mode": "wall_cut", "owning_wall_atom_id": owner, "wall_cut_policy": "subtract_effective_void", "gap_terminals": []} if owner else None, "effective_void": deepcopy(effective), "jamb_before": deepcopy(opening.get("jamb_before")), "jamb_after": deepcopy(opening.get("jamb_after"))}
        if effective is not None:
            effective["derivation"] = {"method": "legacy_v2", "governing_geometry": "legacy_v2_contract", "evidence_refs": deepcopy(opening["source_observation"]["evidence_refs"])}
        if opening["build_disposition"] == "cut":
            opening["host"] = former["host"]
            for side in ("jamb_before", "jamb_after"):
                opening[side]["terminal_id"] = None
        else:
            if any(value is not None for value in former.values()):
                historical = {"source_observation": deepcopy(opening["source_observation"]), **former}
                payload_hash = hashlib.sha256(canonical_json(historical)).hexdigest()
                history.append({"id": f"HISTORY-{opening['id']}-V2", "origin": "active_document", "captured_from_structure_hash": source["structure_hash"], "captured_from_artifact_sha256": None, "captured_payload_sha256": payload_hash, "former_source_observation": historical["source_observation"], "former_host": former["host"], "former_effective_void": former["effective_void"], "former_jamb_before": former["jamb_before"], "former_jamb_after": former["jamb_after"], "status": "superseded_legacy_v2", "reason_code": "explicit_v2_to_v2_1_migration", "evidence_refs": deepcopy(opening["source_observation"]["evidence_refs"])})
            opening.update(host=None, effective_void=None, jamb_before=None, jamb_after=None)
        opening["superseded_interpretations"] = history
    result["structure_hash"] = compute_v21_structure_hash(result)
    return result


def _atom_face_error(point, atom, face):
    p = _point(point, "face point")
    a, b = _point(atom["centerline_m"][0], "atom"), _point(atom["centerline_m"][1], "atom")
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy); tx, ty = dx / length, dy / length; nx, ny = -ty, tx
    along = (p[0]-a[0])*tx + (p[1]-a[1])*ty; normal = (p[0]-a[0])*nx + (p[1]-a[1])*ny; half = float(atom["thickness_m"])*0.5
    expected = {"start_cap": abs(along), "end_cap": abs(along-length), "left_side": abs(normal-half), "right_side": abs(normal+half)}[face]
    within = -0.001 <= along <= length+0.001 and abs(normal) <= half+0.001
    return expected if within else math.inf


def validate_v21_document(document: Mapping[str, Any]) -> dict[str, Any]:
    root = _exact(document, TOP_KEYS, "bundle")
    if root["schema"] != SCHEMA:
        _fail("bundle.schema: expected v2.1")
    supplied = _hash(root["structure_hash"], "structure_hash")
    if supplied != compute_v21_structure_hash(root):
        _fail("structure_hash drift")
    source = root["source"]
    if source.get("schema") != SOURCE_SCHEMA or set(source) != {"schema", "original", "canonical", "views", "metric_registration", "anchors", "excluded_linear_features"}:
        _fail("source exact v3.1 keys required")
    # Reuse all shared source/graph/assumption/reference checks through an
    # explicit compatibility projection; this never writes back or upgrades.
    validate_v2_document(_to_v2_projection(root))
    atoms = {row["id"]: row for row in root["wall_graph"]["atoms"]}
    nodes = {row["id"]: row for row in root["wall_graph"]["junctions"]}
    openings = {row["id"]: row for row in root["opening_contract"]["openings"]}
    structural_ids = set(atoms) | {row["id"] for row in root["wall_graph"]["branches"]} | set(nodes)
    excluded_ids = set()
    canonical_size=source["canonical"]["size_px"]
    for feature in source["excluded_linear_features"]:
        feature = _exact(feature, EXCLUDED_KEYS, "excluded_linear_feature")
        if feature["id"] in excluded_ids or feature["id"] in structural_ids:
            _fail("excluded feature ID collides with structural graph")
        excluded_ids.add(feature["id"])
        if feature["classification"] not in {"joinery", "furniture", "fixture", "dimension_line", "low_partition", "other_nonstructural"} or feature["build_policy"] != "exclude_from_full_height_structure":
            _fail("invalid excluded linear feature classification/policy")
        if feature["status"] not in {"confirmed","candidate","unresolved","legacy_confirmed"} or not isinstance(feature["note"],str) or not feature["note"].strip() or not isinstance(feature["evidence_refs"],list) or not feature["evidence_refs"]:
            _fail("invalid excluded linear feature status/evidence/note")
        geometry = _exact(feature["geometry"], {"space", "primitive", "points_px"}, "excluded geometry")
        if geometry["space"] != "canonical_px" or geometry["primitive"] not in {"segment", "polyline"} or len(geometry["points_px"]) < 2:
            _fail("invalid excluded feature geometry")
        points=[_point(point,"excluded point") for point in geometry["points_px"]]
        if any(not (0<=point[0]<=canonical_size[0] and 0<=point[1]<=canonical_size[1]) for point in points):_fail("excluded feature geometry outside canonical pixels")
        endpoint_indices=set()
        for attachment in feature["attachments"]:
            attachment = _exact(attachment, {"endpoint_index", "relationship", "target_atom_id", "target_face", "contact_point_m", "status"}, "excluded attachment")
            index=attachment["endpoint_index"]
            if index not in {0,len(points)-1} or index in endpoint_indices or attachment["status"] not in {"confirmed","candidate","unresolved","legacy_confirmed"}:_fail("excluded attachment endpoint/status invalid")
            endpoint_indices.add(index)
            if attachment["relationship"] == "wall_face_attachment":
                if attachment["target_atom_id"] not in atoms or attachment["target_face"] not in {"start_cap","end_cap","left_side","right_side"} or attachment["contact_point_m"] is None or _atom_face_error(attachment["contact_point_m"],atoms[attachment["target_atom_id"]],attachment["target_face"])>0.001+1e-9:
                    _fail("excluded feature has invalid structural face attachment")
            elif attachment["relationship"] == "intentional_free_end":
                if any(attachment[key] is not None for key in ("target_atom_id","target_face","contact_point_m")):_fail("intentional free end must not cite structural target")
            else:_fail("unsupported excluded attachment relationship")
        if endpoint_indices != {0,len(points)-1}:_fail("excluded feature must classify both geometry endpoints")
    if root["opening_contract"]["version"] != OPENING_SCHEMA:
        _fail("opening_contract.version: expected v2.1")
    for opening in openings.values():
        opening = _exact(opening, OPENING_KEYS, f"opening {opening.get('id')}")
        host = opening["host"]
        disposition = opening["build_disposition"]
        if disposition in {"retain_solid", "evidence_only", "exclude_pending_resolution"}:
            if host is not None or opening["build_kind"] is not None or opening["effective_void"] is not None or opening["jamb_before"] is not None or opening["jamb_after"] is not None or opening["traversable"] not in {False, None}:
                _fail(f"opening {opening['id']}: nonbuild disposition leaks build semantics")
        else:
            host = _exact(host, HOST_KEYS, f"opening {opening['id']}.host")
        if disposition == "cut":
            if host["mode"] != "wall_cut" or host["owning_wall_atom_id"] not in atoms or host["wall_cut_policy"] != "subtract_effective_void" or host["gap_terminals"] != []:
                _fail(f"opening {opening['id']}: invalid wall_cut host")
        if disposition == "place_in_preexisting_gap":
            if host["mode"] != "preexisting_gap" or host["owning_wall_atom_id"] is not None or host["wall_cut_policy"] != "none" or len(host["gap_terminals"]) != 2:
                _fail(f"opening {opening['id']}: invalid gap host")
            terminals = {}
            for terminal in host["gap_terminals"]:
                terminal = _exact(terminal, TERMINAL_KEYS, "gap terminal")
                if terminal["side"] in terminals or terminal["side"] not in {"before", "after"} or terminal["atom_id"] not in atoms:
                    _fail("gap terminals must be unique before/after atoms")
                atom = atoms[terminal["atom_id"]]
                if terminal["face"] in {"start_cap", "end_cap"}:
                    expected_end = "start" if terminal["face"] == "start_cap" else "end"
                    if terminal["atom_end"] != expected_end or terminal["node_id"] != atom[f"{expected_end}_node_id"]:
                        _fail("cap terminal atom_end/node mismatch")
                elif terminal["face"] in {"left_side", "right_side"}:
                    if terminal["atom_end"] is not None or terminal["node_id"] is not None:
                        _fail("side-face terminal cannot cite atom endpoint")
                else:
                    _fail("unsupported terminal face")
                if _atom_face_error(terminal["face_point_m"], atom, terminal["face"]) > 0.001 + 1e-9:
                    _fail("terminal is more than 1mm from cited atom face")
                terminals[terminal["side"]] = terminal
            if set(terminals) != {"before", "after"} or terminals["before"]["id"] == terminals["after"]["id"]:
                _fail("gap portal requires two distinct terminal sides")
            effective = _validate_effective(opening["effective_void"], "gap effective")
            if effective["host_cut_scope"] != "none_preexisting_gap" or effective["derivation"]["method"] != "fit_between_gap_terminals":
                _fail("gap portal effective void must derive from gap terminals")
            for index, side in enumerate(("before", "after")):
                if math.dist(_point(effective["segment_m"][index], "effective"), _point(terminals[side]["face_point_m"], "terminal")) > 0.001 + 1e-9:
                    _fail("effective gap endpoint differs from terminal face")
                jamb = _exact(opening[f"jamb_{side}"], JAMB_KEYS, f"jamb {side}")
                if jamb["mode"] != "gap_terminal_face" or jamb["terminal_id"] != terminals[side]["id"] or jamb["supporting_atom_ids"] != [terminals[side]["atom_id"]]:
                    _fail("gap jamb does not bind matching terminal/atom")
            if opening["jamb_before"]["terminal_id"] == opening["jamb_after"]["terminal_id"]:
                _fail("both jambs cannot share one terminal")
            if terminals["before"]["atom_id"] == terminals["after"]["atom_id"]:
                _fail("preexisting gap terminals must cite distinct supporting atoms")
            nominal = opening["source_observation"]["nominal_segment_m"]
            n0, n1 = _point(nominal[0], "nominal"), _point(nominal[1], "nominal")
            e0, e1 = _point(effective["segment_m"][0], "effective"), _point(effective["segment_m"][1], "effective")
            nv, ev = (n1[0]-n0[0],n1[1]-n0[1]), (e1[0]-e0[0],e1[1]-e0[1])
            cosine = abs((nv[0]*ev[0]+nv[1]*ev[1]) / max(math.hypot(*nv)*math.hypot(*ev),1e-12))
            angle = math.degrees(math.acos(max(-1.0,min(1.0,cosine))))
            if angle > 0.1 + 1e-9 or math.dist(((n0[0]+n1[0])/2,(n0[1]+n1[1])/2),((e0[0]+e1[0])/2,(e0[1]+e1[1])/2)) > 0.001 + 1e-9 or max(math.dist(n0,e0),math.dist(n1,e1)) > 0.001 + 1e-9:
                _fail("gap source nominal and effective vectors/endpoints are inconsistent")
        elif opening["effective_void"] is not None:
            effective = _validate_effective(opening["effective_void"], "wall-cut effective")
            if effective["host_cut_scope"] != "owning_wall_atom_only":
                _fail("wall-cut effective scope mismatch")
        for history in opening["superseded_interpretations"]:
            history = _exact(history, HISTORY_KEYS, "superseded interpretation")
            payload = {"source_observation": history["former_source_observation"], "host": history["former_host"], "effective_void": history["former_effective_void"], "jamb_before": history["former_jamb_before"], "jamb_after": history["former_jamb_after"]}
            if history["captured_payload_sha256"] != hashlib.sha256(canonical_json(payload)).hexdigest():
                _fail("superseded interpretation payload hash mismatch")
            if history["origin"] == "active_document":
                if history["captured_from_structure_hash"] is None or history["captured_from_artifact_sha256"] is not None:
                    _fail("active-document history provenance is incomplete")
            elif history["origin"] == "external_artifact":
                if history["captured_from_structure_hash"] is not None or history["captured_from_artifact_sha256"] is None or any(payload[key] is None for key in payload):
                    _fail("external-artifact history requires exact artifact hash and all five former fields")
                _hash(history["captured_from_artifact_sha256"], "history artifact hash")
            else:
                _fail("unknown superseded interpretation origin")
    if excluded_ids & ({opening_id for opening_id in openings} | {terminal["atom_id"] for opening in openings.values() if opening.get("host") for terminal in opening["host"].get("gap_terminals", [])}):
        _fail("excluded feature leaked into opening topology")
    return deepcopy(dict(root))


def _validate_effective(value, path):
    effective = _exact(value, VOID_KEYS, path)
    first, second = _point(effective["segment_m"][0], path), _point(effective["segment_m"][1], path)
    if abs(math.dist(first, second) - float(effective["width_m"])) > 0.001 + 1e-9 or float(effective["head_m"]) <= float(effective["sill_m"]):
        _fail(f"{path}: invalid geometry")
    derivation = _exact(effective["derivation"], DERIVATION_KEYS, f"{path}.derivation")
    if derivation["method"] not in {"equal_to_source_nominal", "clip_to_jamb_face", "fit_between_gap_terminals", "legacy_v2"}:
        _fail("unsupported effective derivation")
    return effective


def v21_mapping_metadata(document: Mapping[str, Any]) -> dict[str, Any]:
    doc = validate_v21_document(document)
    wall_cut = sorted(row["id"] for row in doc["opening_contract"]["openings"] if row["build_disposition"] == "cut")
    portals = sorted(row["id"] for row in doc["opening_contract"]["openings"] if row["build_disposition"] == "place_in_preexisting_gap")
    excluded = sorted(row["id"] for row in doc["source"]["excluded_linear_features"])
    return {"schema": "research-v2.1-build-mapping-metadata-v1", "structure_hash": doc["structure_hash"], "wall_cut_opening_ids": wall_cut, "gap_portal_ids": portals, "excluded_linear_feature_ids": excluded, "wall_mesh_inputs": wall_cut, "portal_wall_cut_applied": {opening_id: False for opening_id in portals}, "ifc_relation_expectations": {**{opening_id: {"void_relations": 1, "fill_relations": 1} for opening_id in wall_cut}, **{opening_id: {"void_relations": 0, "fill_relations": 0, "spatial_containment_relations": 1} for opening_id in portals}}}


def assess_v21_build_readiness(document: Mapping[str, Any]) -> dict[str, Any]:
    doc = validate_v21_document(document)
    blockers = set()
    for group in (doc["spaces"], doc["wall_graph"]["branches"], doc["wall_graph"]["atoms"], doc["wall_graph"]["junctions"]):
        for row in group:
            if row["status"] != "confirmed": blockers.add(f"not_confirmed:{row['id']}")
    if doc["outer_boundary"]["status"] != "confirmed": blockers.add("outer_boundary_not_confirmed")
    edges_by_opening = {}
    for edge in doc["adjacency_truth"]["edges"]:
        if edge["status"] == "confirmed" and edge.get("opening_id"):
            edges_by_opening.setdefault(edge["opening_id"], []).append(edge)
    if doc["adjacency_truth"]["status"] != "confirmed": blockers.add("adjacency_not_confirmed")
    for opening in doc["opening_contract"]["openings"]:
        if opening["build_disposition"] == "exclude_pending_resolution": blockers.add(f"opening_pending:{opening['id']}")
        if opening["build_disposition"] == "place_in_preexisting_gap":
            statuses = [opening["status"], opening["source_observation"]["status"], opening["effective_void"]["status"], opening["jamb_before"]["status"], opening["jamb_after"]["status"], *[t["status"] for t in opening["host"]["gap_terminals"]]]
            if any(status != "confirmed" for status in statuses): blockers.add(f"gap_portal_not_confirmed:{opening['id']}")
            if opening["traversable"] is not True or opening["side_a_space_id"] is None or opening["side_b_space_id"] is None or opening["side_a_space_id"] == opening["side_b_space_id"]:
                blockers.add(f"gap_portal_traversal_spaces_invalid:{opening['id']}")
            edges = edges_by_opening.get(opening["id"], [])
            if opening["traversable"] is True and (len(edges) != 1 or {edges[0]["space_a_id"], edges[0]["space_b_id"]} != {opening["side_a_space_id"], opening["side_b_space_id"]}): blockers.add(f"gap_portal_adjacency_invalid:{opening['id']}")
            segment = opening["effective_void"]["segment_m"]
            terminals = {row["side"]: row for row in opening["host"]["gap_terminals"]}
            terminal_atoms = {row["atom_id"] for row in terminals.values()}
            for atom in doc["wall_graph"]["atoms"]:
                overlap = _segment_atom_overlap_length(segment, atom)
                allowed = 0.001 if atom["id"] in terminal_atoms else 0.0
                if overlap > allowed + 1e-9:
                    blockers.add(f"gap_portal_wall_overlap:{opening['id']}:{atom['id']}")
            first, second = _point(segment[0], "portal"), _point(segment[1], "portal")
            vector = (second[0]-first[0],second[1]-first[1]); length=math.hypot(*vector); direction=(vector[0]/length,vector[1]/length)
            atoms_by_id={row["id"]:row for row in doc["wall_graph"]["atoms"]}
            for side,index in (("before",0),("after",1)):
                terminal=terminals[side]; interval=_line_atom_interval(segment[index],direction,atoms_by_id[terminal["atom_id"]])
                actual=0.0 if interval is None or min(abs(interval[0]),abs(interval[1]))>0.001+1e-9 else max(0.0,interval[1]-interval[0])
                if actual < 0.05-1e-9:
                    blockers.add(f"gap_portal_jamb_support_insufficient:{opening['id']}:{side}")
    for issue in doc["unresolved_issues"]:
        if issue["status"] == "open" and issue["blocks_build"]: blockers.add(issue["id"])
    for assumption in doc["assumptions"]["items"]:
        if assumption["build_policy"] == "block_build": blockers.add(f"assumption:{assumption['id']}")
    return {"schema": "research-v2.1-readiness-v1", "structure_hash": doc["structure_hash"], "ready": not blockers, "blocker_ids": sorted(blockers), "mapping_metadata": v21_mapping_metadata(doc)}


def _line_atom_interval(point, direction, atom):
    p=_point(point,"line"); a=_point(atom["centerline_m"][0],"atom"); b=_point(atom["centerline_m"][1],"atom")
    dx,dy=b[0]-a[0],b[1]-a[1]; length=math.hypot(dx,dy); tx,ty=dx/length,dy/length; nx,ny=-ty,tx; half=float(atom["thickness_m"])*.5
    lower,upper=-math.inf,math.inf
    for origin,slope,minimum,maximum in [((p[0]-a[0])*tx+(p[1]-a[1])*ty,direction[0]*tx+direction[1]*ty,0.0,length),((p[0]-a[0])*nx+(p[1]-a[1])*ny,direction[0]*nx+direction[1]*ny,-half,half)]:
        if abs(slope)<=1e-12:
            if origin<minimum-1e-9 or origin>maximum+1e-9:return None
            continue
        values=sorted(((minimum-origin)/slope,(maximum-origin)/slope)); lower=max(lower,values[0]); upper=min(upper,values[1])
        if upper<lower:return None
    return lower,upper


def _segment_atom_overlap_length(segment,atom):
    first,second=_point(segment[0],"segment"),_point(segment[1],"segment"); vector=(second[0]-first[0],second[1]-first[1]); length=math.hypot(*vector)
    if length<=1e-12:return 0.0
    interval=_line_atom_interval([first[0],first[1]],(vector[0]/length,vector[1]/length),atom)
    if interval is None:return 0.0
    return max(0.0,min(length,interval[1])-max(0.0,interval[0]))


__all__ = ["SCHEMA", "compute_v21_structure_hash", "upgrade_v2_to_v21", "validate_v21_document", "assess_v21_build_readiness", "v21_mapping_metadata"]
