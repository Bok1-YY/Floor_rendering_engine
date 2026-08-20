# -*- coding: utf-8 -*-
"""Pure geometry rules for CAD physical spaces and semantic zones.

The CAD parser owns walls and raw closed faces.  This module only classifies
those faces and applies explicit human semantic operations; it never invents
or moves CAD wall/opening geometry and never calls a provider.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any


class CadSpaceError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = copy.deepcopy(details or {})

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.details}


PHYSICAL_SPACE_TYPES = {
    "enclosed_room", "open_plan", "circulation", "wet_suite", "balcony", "service", "other",
}
SEMANTIC_ZONE_TYPES = {
    "living_room", "dining_room", "foyer", "kitchen", "bedroom", "primary_bedroom",
    "secondary_bedroom", "bathroom", "balcony", "circulation", "storage", "utility", "other",
    "unassigned",
}


def _polygon(points: list[Any]):
    from shapely.geometry import Polygon  # type: ignore
    coordinates = [
        (float(point.get("x")), float(point.get("z")))
        if isinstance(point, dict) else (float(point[0]), float(point[1]))
        for point in points
    ]
    value = Polygon(coordinates)
    if not value.is_valid or value.geom_type != "Polygon" or value.is_empty or value.area < .05:
        raise CadSpaceError("cad_space_polygon_invalid", "空间 polygon 无效或面积过小")
    return value


def _points(shape, *, origin_x: float = 0.0, origin_z: float = 0.0) -> list[dict]:
    points: list[dict] = []
    for x, z in list(shape.exterior.coords)[:-1]:
        point = {"x": round(float(x) - origin_x, 5),
                 "z": round(float(z) - origin_z, 5)}
        if not points or point != points[-1]:
            points.append(point)
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return points


def _valid_polygon_component(shape: Any, *, anchor: tuple[float, float] | None = None):
    """Return a valid polygon without inventing a semantic boundary.

    Exact half-plane intersections can acquire almost-coincident vertices.
    Repair and normalise them before the five-decimal metre serialisation so
    the downstream compatibility room never receives a self-intersection.
    """
    from shapely.geometry import Point  # type: ignore

    candidate = shape
    if not candidate.is_valid:
        try:
            from shapely import make_valid  # type: ignore
            candidate = make_valid(candidate)
        except (ImportError, AttributeError):
            candidate = candidate.buffer(0)
    if candidate.geom_type == "MultiPolygon":
        containing = []
        if anchor is not None:
            point = Point(*anchor)
            containing = [part for part in candidate.geoms if part.covers(point)]
        candidate = max(containing or list(candidate.geoms), key=lambda part: float(part.area))
    if candidate.geom_type != "Polygon" or candidate.is_empty:
        return None
    try:
        from shapely import set_precision  # type: ignore
        candidate = set_precision(candidate, 1e-5, mode="valid_output")
    except (ImportError, AttributeError, TypeError):
        candidate = candidate.buffer(0)
    if candidate.geom_type == "MultiPolygon":
        containing = []
        if anchor is not None:
            point = Point(*anchor)
            containing = [part for part in candidate.geoms if part.covers(point)]
        candidate = max(containing or list(candidate.geoms), key=lambda part: float(part.area))
    return candidate if candidate.geom_type == "Polygon" and candidate.is_valid else None


def _interior_rings(shape, *, origin_x: float = 0.0, origin_z: float = 0.0) -> list[list[dict]]:
    return [[
        {"x": round(float(x) - origin_x, 5), "z": round(float(z) - origin_z, 5)}
        for x, z in list(ring.coords)[:-1]
    ] for ring in shape.interiors]


def _stable_face_id(points: list[tuple[float, float]]) -> str:
    rounded = [(round(float(x), 6), round(float(z), 6)) for x, z in points]
    variants = []
    for source in (rounded, list(reversed(rounded))):
        variants.extend(source[index:] + source[:index] for index in range(len(source)))
    canonical = min(variants) if variants else []
    digest = hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()
    return f"cad_face_{digest[:16]}"


def classify_raw_faces(polygons: list[Any], *, origin_x: float, origin_z: float,
                       text_anchors: list[dict],
                       surface_regions: list[dict] | None = None,
                       ) -> tuple[list[dict], list[dict]]:
    """Return audited raw faces and conservative physical-space candidates."""
    from shapely.geometry import Point, Polygon  # type: ignore
    from shapely.ops import unary_union  # type: ignore

    surface_shapes: list[tuple[Any, dict]] = []
    for region in surface_regions or []:
        if (str(region.get("method") or "")
                != "cad_hatch_boundary_surface_evidence_v1"):
            continue
        components = []
        for points in region.get("polygons_m") or []:
            try:
                candidate = Polygon([
                    (float(point[0]), float(point[1])) for point in points])
            except (TypeError, ValueError, IndexError):
                continue
            if not candidate.is_valid:
                candidate = candidate.buffer(0)
            if (candidate.geom_type == "Polygon" and not candidate.is_empty
                    and float(candidate.area) >= .001):
                components.append(candidate)
        if not components:
            continue
        surface_shapes.append((unary_union(components).buffer(0), region))

    entries = []
    for value in polygons:
        shape = value if getattr(value, "geom_type", "") == "Polygon" else _polygon(value)
        original_valid = bool(shape.is_valid)
        repair = None
        if not original_valid:
            repaired = shape.buffer(0)
            area_delta = abs(float(repaired.area) - float(shape.area))
            repair = {
                "method": "shapely_buffer_zero_diagnostic_v1",
                "original_valid": False, "result_type": repaired.geom_type,
                "area_delta_m2": round(area_delta, 8),
                "accepted": bool(
                    repaired.geom_type == "Polygon"
                    and area_delta <= max(.01, abs(float(shape.area)) * .005)),
            }
            if repair["accepted"]:
                shape = repaired
        world = [(float(x), float(z)) for x, z in list(shape.exterior.coords)[:-1]]
        face_id = _stable_face_id(world)
        anchors = [
            copy.deepcopy(row) for row in text_anchors
            if isinstance(row.get("point_m"), (list, tuple))
            and len(row["point_m"]) >= 2
            and shape.covers(Point(float(row["point_m"][0]), float(row["point_m"][1])))
        ]
        surface_evidence = []
        for surface_shape, surface_region in surface_shapes:
            intersection_area = float(shape.intersection(surface_shape).area)
            if intersection_area <= .001:
                continue
            face_coverage = intersection_area / max(float(shape.area), 1e-9)
            surface_coverage = intersection_area / max(
                float(surface_shape.area), 1e-9)
            surface_evidence.append({
                "method": "cad_face_hatch_surface_intersection_v1",
                "source_handle": str(surface_region.get("source_handle") or ""),
                "root_handle": str(surface_region.get("root_handle") or ""),
                "hatch_area_m2": round(float(surface_shape.area), 8),
                "intersection_area_m2": round(intersection_area, 8),
                "face_coverage_ratio": round(face_coverage, 8),
                "hatch_coverage_ratio": round(surface_coverage, 8),
                "boundary_path_count": int(
                    surface_region.get("boundary_path_count") or 0),
                "solid_fill": bool(surface_region.get("solid_fill")),
                "cad_provenance": copy.deepcopy(
                    surface_region.get("cad_provenance") or {}),
                "decision_basis": [
                    "source_hatch_and_physical_face_area_intersection",
                    "pattern_layer_colour_not_used_for_classification",
                ],
            })
        rectangle = shape.minimum_rotated_rectangle
        rectangle_points = list(rectangle.exterior.coords)
        lengths = [math.dist(rectangle_points[i], rectangle_points[i + 1]) for i in range(4)]
        short_side, long_side = min(lengths), max(lengths)
        entries.append({
            "face_id": face_id,
            "shape": shape,
            "area_m2": float(shape.area),
            "short_side_m": short_side,
            "long_side_m": long_side,
            "anchors": anchors,
            "surface_evidence": surface_evidence,
            "origin_x": float(origin_x), "origin_z": float(origin_z),
            "interior_ring_count": len(shape.interiors),
            "original_valid": original_valid,
            "repair_evidence": repair,
            "cad_polygon_m": [[round(x, 8), round(z, 8)] for x, z in world],
            "cad_interior_rings_m": [[
                [round(float(x), 8), round(float(z), 8)]
                for x, z in list(ring.coords)[:-1]
            ] for ring in shape.interiors],
            "polygon": [
                {"x": round(x - origin_x, 5), "z": round(z - origin_z, 5)}
                for x, z in world
            ],
            "interior_rings": _interior_rings(
                shape, origin_x=origin_x, origin_z=origin_z),
        })

    # MTEXT insertion points describe text alignment, not necessarily the
    # visible glyph centre.  A left-aligned room label can therefore land a
    # few millimetres inside the surrounding wall band instead of inside its
    # floor face.  Bind such a semantic TEXT anchor only when it is not
    # covered by any face and exactly one viable room-sized face lies within
    # the same 20 mm tolerance used by the CAD node contract.  Ambiguous
    # boundary labels remain unassigned; geometry is never enlarged or moved.
    for source_anchor in text_anchors:
        if (str(source_anchor.get("source_kind") or "") != "text"
                or not str(source_anchor.get("semantic_profile") or "").strip()
                or not isinstance(source_anchor.get("point_m"), (list, tuple))
                or len(source_anchor["point_m"]) < 2):
            continue
        point = Point(float(source_anchor["point_m"][0]),
                      float(source_anchor["point_m"][1]))
        if any(entry["shape"].covers(point) for entry in entries):
            continue
        nearby = [
            (float(entry["shape"].distance(point)), entry)
            for entry in entries
            if entry["area_m2"] >= 1.5
            and entry["short_side_m"] >= .35
            and (entry["original_valid"]
                 or (entry.get("repair_evidence") or {}).get("accepted"))
            and float(entry["shape"].distance(point)) <= .02 + 1e-9
        ]
        if len(nearby) != 1:
            continue
        distance, entry = nearby[0]
        bound_anchor = copy.deepcopy(source_anchor)
        bound_anchor["anchor_binding_evidence"] = {
            "method": "unique_near_boundary_text_anchor_v1",
            "distance_to_physical_face_m": round(distance, 8),
            "maximum_distance_m": .02,
            "candidate_face_count": 1,
        }
        entry["anchors"].append(bound_anchor)

    raw_faces: list[dict] = []
    accepted: list[dict] = []
    for entry in entries:
        reasons: list[str] = []
        shape = entry["shape"]
        nested_children = [
            other for other in entries if other is not entry
            and entry["area_m2"] > other["area_m2"] * 1.08
            and (other["anchors"] or (other["area_m2"] >= 1.5 and other["short_side_m"] >= .35))
            and other["shape"].within(shape)
        ]
        semantic_anchors = [row for row in entry["anchors"] if row.get("semantic_profile")]
        if nested_children:
            reasons.append("nested_outer_frame")
        if not entry["original_valid"] and not (entry.get("repair_evidence") or {}).get("accepted"):
            reasons.append("invalid_face_repair_unsupported")
        if entry["interior_ring_count"]:
            # polygonize can leave structural islands inside an otherwise
            # valid room face (for example a stair/core wall loop).  A room
            # label proves the exterior semantics, but a label does not prove
            # that the island is void floor area.  The accepted physical-space
            # footprint therefore records only the exterior; the raw audit
            # still preserves every interior ring for review.
            if semantic_anchors:
                # Preserve the raw loop in the audit, but promote the exterior
                # as the usable face.  This mirrors what a human CAD reviewer
                # means by labelling the surrounding room/circulation region.
                entry["shape"] = type(shape)(shape.exterior)
                entry["area_m2"] = float(entry["shape"].area)
                entry["interior_rings"] = []
                shape = entry["shape"]
            else:
                reasons.append("unlabelled_face_holes")
        if not semantic_anchors and any(
            re.search(r"(?:room|space).*(?:boundary|outline)|(?:boundary|outline).*(?:room|space)",
                      str(((anchor.get("cad_provenance") or {}).get("layer") or "")), re.I)
            for anchor in entry["anchors"]
        ):
            reasons.append("unlabelled_room_boundary_remainder")
        if (not semantic_anchors and entry["area_m2"] < 1.5):
            reasons.append("unlabelled_small_face")
        # Unlabelled exterior wall bands in real residential IFC/CAD sources
        # are commonly 0.35--0.60 m thick.  A sub-0.65 m face with aspect >=4
        # is not a usable circulation space and is safe to classify as the
        # interior of a closed double-line wall footprint.
        if (not semantic_anchors and entry["short_side_m"] < .65
                and entry["long_side_m"] / max(entry["short_side_m"], .001) >= 4):
            reasons.append("double_line_wall_strip")
        row = {
            key: copy.deepcopy(value) for key, value in entry.items()
            if key != "shape"
        }
        row.update(
            disposition="excluded" if reasons else "physical_space_candidate",
            filter_reasons=reasons,
            text_anchor_ids=[str(value.get("anchor_id") or "") for value in entry["anchors"]],
            manual_eligible=not bool(
                entry["interior_ring_count"]
                or (not entry["original_valid"] and not (entry.get("repair_evidence") or {}).get("accepted"))),
        )
        raw_faces.append(row)
        if not reasons:
            accepted.append(entry)
    return raw_faces, accepted


def initial_space_layers(accepted_faces: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Create physical spaces and non-overlapping semantic cells from CAD labels.

    Multiple *open-plan compatible* labels (living/dining/kitchen/foyer) inside
    one uninterrupted face may be semantic zones.  Bedroom/bath/storage labels
    in one face instead prove that physical wall topology is missing; those are
    blocked for review and never turned into nearest-anchor diagonal geometry.
    """
    from shapely.geometry import Point  # type: ignore

    physical_spaces: list[dict] = []
    semantic_zones: list[dict] = []
    unresolved: list[dict] = []
    plan_bounds = None
    if accepted_faces:
        min_x = min(float(entry["shape"].bounds[0]) for entry in accepted_faces)
        min_z = min(float(entry["shape"].bounds[1]) for entry in accepted_faces)
        max_x = max(float(entry["shape"].bounds[2]) for entry in accepted_faces)
        max_z = max(float(entry["shape"].bounds[3]) for entry in accepted_faces)
        plan_bounds = (min_x, min_z, max_x, max_z)
    for index, entry in enumerate(accepted_faces, 1):
        shape = _valid_polygon_component(entry["shape"])
        if shape is None:
            raise CadSpaceError(
                "cad_space_polygon_invalid",
                "物理空间在毫米级精度规范化后无有效 polygon",
                {"face_id": str(entry.get("face_id") or "")},
            )
        physical_id = f"physical_{entry['face_id'][9:]}"
        anchors = [row for row in entry.get("anchors") or [] if row.get("semantic_profile")]
        # A real TEXT/MTEXT room annotation is stronger semantic evidence than
        # a furniture INSERT name such as "Dining Set" or "Toilet - top".
        # Once a face contains any recognised text label, exclude insert names
        # from that face's room-name aggregation.  Inserts remain a bounded
        # fallback for drawings that genuinely have no semantic text.
        text_semantic_anchors = [
            row for row in anchors if str(row.get("source_kind") or "") == "text"
        ]
        if text_semantic_anchors:
            anchors = text_semantic_anchors
        inferred_anchor = None
        space_markers = [
            row for row in entry.get("anchors") or []
            if str(row.get("source_kind") or "") == "space_marker"
            and str(row.get("space_marker") or "")
        ]
        if not anchors:
            bed_markers: list[dict] = []
            for marker in space_markers:
                raw_point = marker.get("point_m") or []
                try:
                    point = (float(raw_point[0]), float(raw_point[1]))
                except (TypeError, ValueError, IndexError):
                    continue
                if any(math.dist(
                        point,
                        (float(existing["point_m"][0]),
                         float(existing["point_m"][1]))) <= .20
                       for existing in bed_markers):
                    continue
                if str(marker.get("space_marker") or "").lower() == "bed":
                    bed_markers.append(marker)
            if len(bed_markers) == 1:
                source_marker = bed_markers[0]
                inferred_anchor = {
                    "anchor_id": (
                        f"inferred_bedroom_{entry['face_id'][9:]}"),
                    "source_kind": "space_marker_inference",
                    "text": "卧室（床位几何证据）",
                    "semantic_profile": "bedroom",
                    "reference_profile": "bedroom",
                    "point_m": copy.deepcopy(source_marker.get("point_m")),
                    "semantic_inference": {
                        "method": "cad_enclosed_space_bed_marker_v1",
                        "confidence": .96,
                        "source_marker_anchor_id": str(
                            source_marker.get("anchor_id") or ""),
                        "source_marker_kind": "bed",
                        "deduplicated_bed_marker_count": 1,
                        "decision_basis": [
                            "source_backed_high_specificity_bed_footprint",
                            "bed_marker_is_inside_one_closed_physical_space",
                            "exactly_one_deduplicated_bed_marker_in_space",
                            "generic_bedroom_only_no_primary_secondary_guess",
                        ],
                    },
                }
                anchors = [inferred_anchor]
        # Furnished residential drawings frequently omit the word "balcony"
        # while still drawing the balcony as a narrow, enclosed perimeter
        # space.  This is a geometry-backed semantic fallback, not a wall
        # inference: it is allowed only for an unlabelled elongated face that
        # is flush with the global plan envelope.  Interior corridors and
        # square unlabelled rooms remain hard-review items.
        if not anchors and plan_bounds is not None:
            bounds = tuple(float(value) for value in shape.bounds)
            boundary_sides = [
                side for side, value, plan_value in (
                    ("min_x", bounds[0], plan_bounds[0]),
                    ("min_z", bounds[1], plan_bounds[1]),
                    ("max_x", bounds[2], plan_bounds[2]),
                    ("max_z", bounds[3], plan_bounds[3]),
                ) if abs(value - plan_value) <= .05
            ]
            adjacent_perimeter_corner = bool(
                {"min_x", "max_x"}.intersection(boundary_sides)
                and {"min_z", "max_z"}.intersection(boundary_sides))
            full_surface = next((
                row for row in entry.get("surface_evidence") or []
                if str(row.get("method") or "")
                == "cad_face_hatch_surface_intersection_v1"
                and float(row.get("face_coverage_ratio") or 0) >= .90
                and float(row.get("hatch_coverage_ratio") or 0) >= .90
                and str(row.get("source_handle") or "")
                and int(row.get("boundary_path_count") or 0) >= 1
            ), None)
            if (full_surface is not None and adjacent_perimeter_corner
                    and 2.0 <= float(shape.area) <= 15.0):
                point = shape.representative_point()
                inferred_anchor = {
                    "anchor_id": f"inferred_patio_{entry['face_id'][9:]}",
                    "text": "室外铺装空间（几何推断）",
                    "semantic_profile": "balcony",
                    "reference_profile": "balcony",
                    "point_m": [float(point.x), float(point.y)],
                    "semantic_inference": {
                        "method": "cad_perimeter_hatch_surface_space_v1",
                        "confidence": .94,
                        "area_m2": round(float(shape.area), 6),
                        "boundary_sides": boundary_sides,
                        "adjacent_perimeter_corner": True,
                        "hatch_source_handle": str(
                            full_surface.get("source_handle") or ""),
                        "face_coverage_ratio": float(
                            full_surface.get("face_coverage_ratio") or 0),
                        "hatch_coverage_ratio": float(
                            full_surface.get("hatch_coverage_ratio") or 0),
                        "decision_basis": [
                            "unlabelled_source_closed_physical_face",
                            "source_hatch_covers_entire_face",
                            "face_touches_two_adjacent_plan_perimeter_sides",
                            "pattern_layer_colour_not_used_for_classification",
                        ],
                    },
                }
                anchors = [inferred_anchor]
        if not anchors and plan_bounds is not None:
            rectangle = shape.minimum_rotated_rectangle
            rectangle_points = list(rectangle.exterior.coords)
            lengths = [
                math.dist(rectangle_points[i], rectangle_points[i + 1])
                for i in range(4)
            ]
            short_side, long_side = min(lengths), max(lengths)
            bounds = tuple(float(value) for value in shape.bounds)
            boundary_sides = [
                side for side, value, plan_value in (
                    ("min_x", bounds[0], plan_bounds[0]),
                    ("min_z", bounds[1], plan_bounds[1]),
                    ("max_x", bounds[2], plan_bounds[2]),
                    ("max_z", bounds[3], plan_bounds[3]),
                ) if abs(value - plan_value) <= .05
            ]
            if (2.0 <= float(shape.area) <= 12.0
                    and .75 <= short_side <= 1.8
                    and long_side / max(short_side, .001) >= 2.25
                    and boundary_sides):
                point = shape.representative_point()
                inferred_anchor = {
                    "anchor_id": f"inferred_balcony_{entry['face_id'][9:]}",
                    "text": "阳台（几何推断）",
                    "semantic_profile": "balcony",
                    "reference_profile": "balcony",
                    "point_m": [float(point.x), float(point.y)],
                    "semantic_inference": {
                        "method": "cad_perimeter_elongated_space_v1",
                        "confidence": .82,
                        "area_m2": round(float(shape.area), 6),
                        "short_side_m": round(short_side, 6),
                        "long_side_m": round(long_side, 6),
                        "aspect_ratio": round(long_side / max(short_side, .001), 6),
                        "boundary_sides": boundary_sides,
                    },
                }
                anchors = [inferred_anchor]
        profiles = {str(row.get("semantic_profile") or "") for row in anchors}
        if len(profiles) > 1:
            space_type = "open_plan"
        elif "balcony" in profiles:
            space_type = "balcony"
        elif "circulation" in profiles:
            space_type = "circulation"
        elif "bathroom" in profiles:
            space_type = "wet_suite"
        elif profiles.intersection({"storage", "utility"}):
            space_type = "service"
        else:
            space_type = "enclosed_room"
        physical_spaces.append({
            "id": physical_id,
            "label": str((anchors[0] if anchors else {}).get("text") or f"物理空间 {index}")[:100],
            "space_type": space_type,
            "face_ids": [entry["face_id"]],
            "polygon": _points(
                shape, origin_x=float(entry.get("origin_x") or 0),
                origin_z=float(entry.get("origin_z") or 0)),
            "interior_rings": _interior_rings(
                shape, origin_x=float(entry.get("origin_x") or 0),
                origin_z=float(entry.get("origin_z") or 0)),
            "selected": True, "source": "cad_local_faces_v1",
            **({"semantic_inference": copy.deepcopy(inferred_anchor["semantic_inference"])}
               if inferred_anchor else {}),
        })
        if not anchors:
            unresolved.append({
                "code": "cad_room_semantics_unresolved", "physical_space_id": physical_id,
                "message": "CAD 物理空间缺少可解释的本地文字/块房型标签",
            })
            semantic_zones.append({
                "id": f"zone_{entry['face_id'][9:]}_unassigned",
                "physical_space_id": physical_id,
                "label": f"待命名空间 {index}",
                "zone_type": "unassigned",
                "reference_room_profile": "",
                "geometry": {"kind": "polygon", "points": _points(
                    shape, origin_x=float(entry.get("origin_x") or 0),
                    origin_z=float(entry.get("origin_z") or 0))},
                "source": "cad_unassigned_physical_space_v1",
                "text_anchor_ids": [],
                "semantic_status": "needs_review",
            })
            continue
        unique: list[dict] = []
        for anchor in anchors:
            duplicate_index = next((
                i for i, existing in enumerate(unique)
                if str(existing.get("semantic_profile") or "") == str(anchor.get("semantic_profile") or "")
                and math.dist(
                    (float(existing["point_m"][0]), float(existing["point_m"][1])),
                    (float(anchor["point_m"][0]), float(anchor["point_m"][1]))) <= .6
            ), None)
            if duplicate_index is None:
                unique.append(anchor)
                continue
            existing = unique[duplicate_index]
            existing_text = str(existing.get("text") or "")
            candidate_text = str(anchor.get("text") or "")
            existing_specific = str(existing.get("reference_profile") or "") not in {
                "", str(existing.get("semantic_profile") or "")}
            candidate_specific = str(anchor.get("reference_profile") or "") not in {
                "", str(anchor.get("semantic_profile") or "")}
            candidate_cjk = bool(re.search(r"[\u3400-\u9fff]", candidate_text))
            existing_cjk = bool(re.search(r"[\u3400-\u9fff]", existing_text))
            if (candidate_specific, candidate_cjk, len(candidate_text)) > (
                    existing_specific, existing_cjk, len(existing_text)):
                unique[duplicate_index] = anchor
        open_plan_profiles = {
            "living_room", "dining_room", "kitchen", "foyer", "circulation",
        }
        unique_profiles = {
            str(row.get("semantic_profile") or "") for row in unique}
        compatible_reference_profiles = {
            str(row.get("reference_profile") or "") for row in unique
            if str(row.get("reference_profile") or "")
        }
        if len(unique) > 1 and not unique_profiles.issubset(open_plan_profiles):
            physical_spaces[-1]["space_type"] = "unresolved_composite"
            physical_spaces[-1]["semantic_blocker"] = (
                "cad_physical_boundary_missing_for_enclosed_room_labels")
            unresolved.append({
                "code": "cad_physical_boundary_missing_for_enclosed_room_labels",
                "physical_space_id": physical_id,
                "message": (
                    "同一未分隔物理面包含卧室/卫浴/储物等封闭房间标签；"
                    "缺少墙体边界证据，禁止按文字位置生成斜切房间"),
                "semantic_profiles": sorted(unique_profiles),
                "text_anchor_ids": [
                    str(row.get("anchor_id") or "") for row in unique],
            })
            semantic_zones.append({
                "id": f"zone_{entry['face_id'][9:]}_unassigned",
                "physical_space_id": physical_id,
                "label": " / ".join(str(row.get("text") or "") for row in unique)[:100],
                "zone_type": "unassigned",
                "reference_room_profile": "",
                "geometry": {"kind": "polygon", "points": _points(
                    shape, origin_x=float(entry.get("origin_x") or 0),
                    origin_z=float(entry.get("origin_z") or 0))},
                "source": "cad_unresolved_composite_physical_space_v1",
                "text_anchor_ids": [str(row.get("anchor_id") or "") for row in unique],
                "semantic_status": "needs_review",
            })
            continue
        cells = []
        if len(unique) == 1:
            cells = [(unique[0], shape)]
        elif len(compatible_reference_profiles) == 1:
            # Several labels can describe one intentional open-plan room
            # without implying a partition.  The parser's room lexicon maps
            # compatible uses such as LIVING + DINING to the same canonical
            # reference profile.  Keep the exact physical polygon once and
            # retain every observed label as evidence.
            reference_profile = next(iter(compatible_reference_profiles))
            semantic_zones.append({
                "id": f"zone_{entry['face_id'][9:]}_compatible_open_plan",
                "physical_space_id": physical_id,
                "label": " / ".join(
                    str(row.get("text") or "") for row in unique)[:100],
                "zone_type": reference_profile,
                "reference_room_profile": reference_profile,
                "geometry": {"kind": "polygon", "points": _points(
                    shape, origin_x=float(entry.get("origin_x") or 0),
                    origin_z=float(entry.get("origin_z") or 0))},
                "source": "cad_compatible_open_plan_semantic_collection_v1",
                "text_anchor_ids": [
                    str(row.get("anchor_id") or "") for row in unique],
                "semantic_status": "complete",
                "observed_semantic_profiles": sorted(unique_profiles),
                "decision_basis": [
                    "single_source_physical_space",
                    "all_labels_share_one_canonical_reference_profile",
                    "no_semantic_partition_geometry_created",
                ],
            })
            continue
        elif "living_room" in unique_profiles:
            # Living/dining/foyer labels describe uses inside one real open
            # physical space.  They do not justify Voronoi walls, but they do
            # provide enough semantic evidence to make the room usable by the
            # camera/material pipeline.  Keep the one exact polygon and retain
            # every observed use as evidence; choose living_room only as the
            # primary render/profile identity.
            semantic_zones.append({
                "id": f"zone_{entry['face_id'][9:]}_open_plan",
                "physical_space_id": physical_id,
                "label": " / ".join(str(row.get("text") or "") for row in unique)[:100],
                "zone_type": "living_room",
                "reference_room_profile": "living_room",
                "geometry": {"kind": "polygon", "points": _points(
                    shape, origin_x=float(entry.get("origin_x") or 0),
                    origin_z=float(entry.get("origin_z") or 0))},
                "source": "cad_open_plan_semantic_collection_v2",
                "text_anchor_ids": [str(row.get("anchor_id") or "") for row in unique],
                "semantic_status": "complete",
                "observed_semantic_profiles": sorted(unique_profiles),
                "decision_basis": [
                    "single_source_physical_space",
                    "living_room_label_present",
                    "compatible_open_plan_use_labels_only",
                    "no_semantic_partition_geometry_created",
                ],
            })
            continue
        else:
            # Text anchors prove that several uses share one physical space;
            # they do not prove a wall or a semantic boundary.  The old
            # nearest-anchor half-plane split produced diagonal/Voronoi rooms
            # that do not exist in the CAD.  Keep the exact physical polygon
            # as one auditable, unassigned semantic zone until a user draws a
            # real semantic partition.
            unresolved.append({
                "code": "cad_semantic_partition_required",
                "physical_space_id": physical_id,
                "message": "开放物理空间含多个用途标签；文字不足以证明分区边界",
                "semantic_profiles": sorted(unique_profiles),
                "text_anchor_ids": [str(row.get("anchor_id") or "") for row in unique],
            })
            semantic_zones.append({
                "id": f"zone_{entry['face_id'][9:]}_open_plan",
                "physical_space_id": physical_id,
                "label": " / ".join(str(row.get("text") or "") for row in unique)[:100],
                "zone_type": "unassigned",
                "reference_room_profile": "",
                "geometry": {"kind": "polygon", "points": _points(
                    shape, origin_x=float(entry.get("origin_x") or 0),
                    origin_z=float(entry.get("origin_z") or 0))},
                "source": "cad_open_plan_semantic_collection_v1",
                "text_anchor_ids": [str(row.get("anchor_id") or "") for row in unique],
                "semantic_status": "needs_review",
                "observed_semantic_profiles": sorted(unique_profiles),
            })
            continue
        for zone_index, (anchor, cell) in enumerate(cells, 1):
            semantic_zones.append({
                "id": f"zone_{entry['face_id'][9:]}_{zone_index}",
                "physical_space_id": physical_id,
                "label": str(anchor.get("text") or f"分区 {zone_index}")[:100],
                "zone_type": str(anchor.get("semantic_profile") or "other")[:80],
                "reference_room_profile": str(anchor.get("reference_profile") or "")[:80],
                "geometry": {"kind": "polygon", "points": _points(
                    cell, origin_x=float(entry.get("origin_x") or 0),
                    origin_z=float(entry.get("origin_z") or 0))},
                "source": (
                    "cad_geometry_boundary_inference_v1"
                    if anchor.get("semantic_inference")
                    else ("cad_text_voronoi_v1" if len(cells) > 1
                          else "cad_text_containment_v1")
                ),
                "text_anchor_ids": [str(anchor.get("anchor_id") or "")],
                **({"semantic_inference": copy.deepcopy(anchor["semantic_inference"])}
                   if anchor.get("semantic_inference") else {}),
            })
    return physical_spaces, semantic_zones, unresolved


def _halfplane(shape, start: tuple[float, float], end: tuple[float, float], side: str):
    from shapely.geometry import Polygon  # type: ignore

    dx, dz = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dz)
    if length < .01:
        raise CadSpaceError("cad_semantic_split_line_invalid", "语义分割线长度不足")
    ux, uz = dx / length, dz / length
    nx, nz = -uz, ux
    if side == "right":
        nx, nz = -nx, -nz
    min_x, min_z, max_x, max_z = shape.bounds
    reach = max(max_x - min_x, max_z - min_z, 1.0) * 20
    return Polygon([
        (start[0] - ux * reach, start[1] - uz * reach),
        (end[0] + ux * reach, end[1] + uz * reach),
        (end[0] + ux * reach + nx * reach * 2, end[1] + uz * reach + nz * reach * 2),
        (start[0] - ux * reach + nx * reach * 2, start[1] - uz * reach + nz * reach * 2),
    ])


def geometry_shape(geometry: dict, physical_shape):
    kind = str(geometry.get("kind") or "polygon")
    if kind == "polygon":
        shape = _polygon(geometry.get("points") or [])
    elif kind == "rectangle":
        from shapely.geometry import box  # type: ignore
        min_x, min_z = float(geometry.get("min_x")), float(geometry.get("min_z"))
        max_x, max_z = float(geometry.get("max_x")), float(geometry.get("max_z"))
        if max_x - min_x < .05 or max_z - min_z < .05:
            raise CadSpaceError("cad_semantic_rectangle_invalid", "语义矩形尺寸无效")
        shape = box(min_x, min_z, max_x, max_z)
    elif kind == "split_halfplane":
        start, end = geometry.get("start") or {}, geometry.get("end") or {}
        side = str(geometry.get("side") or "")
        if side not in {"left", "right"}:
            raise CadSpaceError("cad_semantic_split_side_invalid", "语义分割线 side 必须为 left 或 right")
        shape = _halfplane(
            physical_shape, (float(start.get("x")), float(start.get("z"))),
            (float(end.get("x")), float(end.get("z"))), side,
        ).intersection(physical_shape)
    else:
        raise CadSpaceError("cad_semantic_geometry_kind_invalid", "未知语义分区几何类型")
    if shape.geom_type != "Polygon" or shape.is_empty or shape.area < .05:
        raise CadSpaceError("cad_semantic_geometry_invalid", "语义分区必须形成单一有效区域")
    return shape


def _label(value: Any, field: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        raise CadSpaceError("cad_space_label_required", f"{field} 不能为空")
    return text[:100]


def apply_space_draft(model: dict, raw_faces: list[dict], physical_spaces: list[dict],
                      semantic_zones: list[dict], excluded_face_ids: list[str]) -> tuple[dict, dict]:
    """Validate a full replacement draft and project it onto the legacy rooms view."""
    from shapely.ops import unary_union  # type: ignore

    face_map = {str(row.get("face_id") or ""): row for row in raw_faces}
    eligible = {
        face_id for face_id, row in face_map.items()
        if row.get("manual_eligible") is True
    }
    excluded = set(str(value or "") for value in excluded_face_ids)
    if not excluded.issubset(set(face_map)):
        raise CadSpaceError("cad_face_not_found", "排除列表包含未知 CAD face")
    seen_faces: set[str] = set()
    normalized_spaces: list[dict] = []
    shapes: dict[str, Any] = {}
    seen_ids: set[str] = set()
    for index, row in enumerate(physical_spaces, 1):
        space_id = str(row.get("id") or f"physical_{index}").strip()[:80]
        if not space_id or space_id in seen_ids:
            raise CadSpaceError("cad_physical_space_id_invalid", "物理空间 ID 为空或重复")
        seen_ids.add(space_id)
        face_ids = list(dict.fromkeys(str(value or "") for value in row.get("face_ids") or []))
        if not face_ids or any(value not in eligible for value in face_ids):
            raise CadSpaceError("cad_physical_face_invalid", "物理空间必须引用可保留的 CAD face")
        if seen_faces.intersection(face_ids) or excluded.intersection(face_ids):
            raise CadSpaceError("cad_face_assignment_conflict", "CAD face 被重复保留或同时排除")
        seen_faces.update(face_ids)
        shape = unary_union([_polygon(face_map[value]["polygon"]) for value in face_ids])
        if shape.geom_type != "Polygon" or shape.is_empty:
            raise CadSpaceError("cad_physical_merge_disconnected", "合并的 CAD face 必须连成一个物理空间")
        submitted = row.get("polygon") or []
        if submitted:
            submitted_shape = _polygon(submitted)
            if submitted_shape.symmetric_difference(shape).area > .01:
                raise CadSpaceError("cad_physical_polygon_not_authoritative", "物理空间 polygon 与 CAD face 不一致")
        shapes[space_id] = shape
        source_polygons = [face_map[value].get("cad_polygon_m") or [] for value in face_ids]
        source_union = unary_union([_polygon(value) for value in source_polygons])
        if source_union.geom_type != "Polygon":
            raise CadSpaceError("cad_physical_source_union_invalid", "CAD face 源坐标无法形成单一物理空间")
        space_type = str(row.get("space_type") or "other")
        if space_type not in PHYSICAL_SPACE_TYPES:
            raise CadSpaceError("cad_physical_space_type_invalid", "物理空间类型不在允许枚举内")
        normalized_spaces.append({
            "id": space_id, "label": _label(row.get("label"), "物理空间名称"),
            "space_type": space_type,
            "face_ids": face_ids, "polygon": _points(shape),
            "selected": bool(row.get("selected", True)), "source": "cad_human_confirmed",
            "cad_provenance": {
                "source_kind": "human_union_of_audited_cad_faces",
                "source_handle": face_ids[0], "root_handle": face_ids[0],
                "source_face_ids": copy.deepcopy(face_ids),
                # Multi-face unions are still exact CAD facts; keep every
                # source loop and one exterior for legacy back-projection.
                "source_polygon_m": [
                    [round(float(x), 8), round(float(z), 8)]
                    for x, z in list(source_union.exterior.coords)[:-1]],
                "source_polygons_m": copy.deepcopy(source_polygons),
            },
        })
    if seen_faces.union(excluded.intersection(eligible)) != eligible:
        raise CadSpaceError("cad_face_assignment_incomplete", "每个候选 CAD face 必须明确保留或排除", {
            "unassigned_face_ids": sorted(eligible - seen_faces - excluded),
        })
    for index, left in enumerate(normalized_spaces):
        for right in normalized_spaces[index + 1:]:
            if shapes[left["id"]].intersection(shapes[right["id"]]).area > 1e-5:
                raise CadSpaceError("cad_physical_space_overlap", "物理空间不能重叠")

    zones: list[dict] = []
    zone_shapes: dict[str, Any] = {}
    seen_zone_ids: set[str] = set()
    for index, row in enumerate(semantic_zones, 1):
        zone_id = str(row.get("id") or f"semantic_{index}").strip()[:80]
        physical_id = str(row.get("physical_space_id") or "").strip()
        if not zone_id or zone_id in seen_zone_ids or physical_id not in shapes:
            raise CadSpaceError("cad_semantic_zone_binding_invalid", "语义分区 ID 重复或未绑定物理空间")
        seen_zone_ids.add(zone_id)
        physical_shape = shapes[physical_id]
        geometry = copy.deepcopy(row.get("geometry") or {})
        shape = geometry_shape(geometry, physical_shape)
        if shape.difference(physical_shape).area > 1e-5:
            raise CadSpaceError("cad_semantic_zone_outside_physical", "语义分区必须完全位于物理空间内")
        zone_shapes[zone_id] = shape
        zone_type = str(row.get("zone_type") or "other")
        if zone_type not in SEMANTIC_ZONE_TYPES:
            raise CadSpaceError("cad_semantic_zone_type_invalid", "语义分区类型不在允许枚举内")
        zones.append({
            "id": zone_id, "physical_space_id": physical_id,
            "label": _label(row.get("label"), "语义分区名称"),
            "zone_type": zone_type,
            "geometry": {"kind": "polygon", "points": _points(shape)},
            "source_geometry": geometry, "source": "human",
        })
    for space in normalized_spaces:
        rows = [row for row in zones if row["physical_space_id"] == space["id"]]
        if not rows:
            raise CadSpaceError("cad_semantic_zone_missing", "每个物理空间至少需要一个语义分区", {
                "physical_space_id": space["id"],
            })
        values = [zone_shapes[row["id"]] for row in rows]
        overlap = sum(
            values[i].intersection(values[j]).area
            for i in range(len(values)) for j in range(i + 1, len(values))
        )
        if overlap > 1e-5:
            raise CadSpaceError("cad_semantic_zone_overlap", "同一物理空间内的语义分区不能重叠")
        uncovered = shapes[space["id"]].difference(unary_union(values)).area
        if uncovered > max(.01, shapes[space["id"]].area * .005):
            raise CadSpaceError("cad_semantic_zone_coverage_incomplete", "语义分区必须覆盖整个物理空间", {
                "physical_space_id": space["id"], "uncovered_area_m2": round(uncovered, 5),
            })

    updated = copy.deepcopy(model)
    updated["physical_spaces"] = normalized_spaces
    updated["semantic_zones"] = zones
    updated["excluded_face_ids"] = sorted(excluded)
    physical_by_id = {row["id"]: row for row in normalized_spaces}
    updated["rooms"] = [{
        "id": row["id"], "label": row["label"], "room_type": row["zone_type"],
        "semantic_profile": row["zone_type"], "semantic_status": "complete",
        "physical_space_id": row["physical_space_id"],
        "polygon": copy.deepcopy(row["geometry"]["points"]),
        "area_m2": round(zone_shapes[row["id"]].area, 2),
        "floor_elevation_m": 0.0, "ceiling_height_m": float(updated.get("wall_height_m") or 2.8),
        "selected": bool(physical_by_id[row["physical_space_id"]].get("selected", True)),
        "source": "human", "confidence": 1.0,
        "cad_provenance": {
            "source_kind": "semantic_zone_on_cad_faces",
            "source_handle": "+".join(physical_by_id[row["physical_space_id"]]["face_ids"]),
            "root_handle": physical_by_id[row["physical_space_id"]]["face_ids"][0],
            "source_face_ids": copy.deepcopy(physical_by_id[row["physical_space_id"]]["face_ids"]),
        },
    } for row in zones]
    validate_opening_topology(updated, shapes)
    opening_count = len(updated.get("openings") or [])
    confirmation = {
        "status": "confirmed" if opening_count else "needs_opening_review",
        "reason_codes": [] if opening_count else ["cad_opening_topology_unproven"],
        "physical_space_count": len(normalized_spaces),
        "semantic_zone_count": len(zones), "retained_face_count": len(seen_faces),
        "excluded_face_count": len(excluded), "opening_count": opening_count,
        "validation_version": "cad-space-draft-v1",
    }
    updated["space_confirmation"] = copy.deepcopy(confirmation)
    return updated, confirmation


def validate_opening_topology(model: dict, physical_shapes: dict[str, Any]) -> None:
    from shapely.geometry import Point  # type: ignore

    walls = {str(row.get("id") or ""): row for row in model.get("walls") or []}
    for opening in model.get("openings") or []:
        if opening.get("kind") not in {"door", "open_connection"}:
            continue
        wall = walls.get(str(opening.get("wall_id") or ""))
        if not wall:
            raise CadSpaceError("cad_opening_orphaned", "门洞未绑定 CAD 墙体", {"opening_id": opening.get("id")})
        start, end = wall.get("start") or {}, wall.get("end") or {}
        dx, dz = float(end.get("x", 0)) - float(start.get("x", 0)), float(end.get("z", 0)) - float(start.get("z", 0))
        length = math.hypot(dx, dz)
        if length < .05:
            raise CadSpaceError("cad_opening_wall_invalid", "门洞所在墙体长度无效")
        along = (float(opening.get("offset_m") or 0) + float(opening.get("width_m") or 0) / 2) / length
        center = (float(start.get("x", 0)) + dx * along, float(start.get("z", 0)) + dz * along)
        nx, nz = -dz / length * .12, dx / length * .12
        adjacent = set()
        for point in ((center[0] + nx, center[1] + nz), (center[0] - nx, center[1] - nz)):
            adjacent.update(
                space_id for space_id, shape in physical_shapes.items()
                if shape.buffer(.01).covers(Point(*point))
            )
        if not adjacent or len(adjacent) > 2:
            raise CadSpaceError("cad_opening_topology_invalid", "门洞必须连接一至两个明确物理空间", {
                "opening_id": opening.get("id"), "physical_space_ids": sorted(adjacent),
            })


def model_summary(model: dict) -> dict:
    return {
        "wall_count": len(model.get("walls") or []),
        "global_wall_footprint_count": len(model.get("global_wall_footprints") or []),
        "global_wall_source_coverage_ratio": (
            model.get("global_wall_topology") or {}).get("source_coverage_ratio"),
        "opening_count": len(model.get("openings") or []),
        "physical_space_count": len(model.get("physical_spaces") or []),
        "semantic_zone_count": len(model.get("semantic_zones") or []),
        "room_count": len(model.get("rooms") or []),
    }


def physical_facts_hash(model: dict) -> str:
    physical = [{
        key: copy.deepcopy(row.get(key)) for key in (
            "id", "face_ids", "polygon", "selected", "source", "cad_provenance")
    } for row in model.get("physical_spaces") or []]
    payload = {
        "walls": model.get("walls") or [], "openings": model.get("openings") or [],
        "physical_spaces": physical,
        "excluded_face_ids": sorted(model.get("excluded_face_ids") or []),
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()


def semantic_overlay_hash(model: dict) -> str:
    payload = {
        "semantic_zones": model.get("semantic_zones") or [],
        "rooms_compatibility_projection": model.get("rooms") or [],
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()
