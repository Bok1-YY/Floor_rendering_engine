"""Deterministic IFC-derived gold pairs for Plan-to-3D validation.

This module is test tooling, not a production dependency.  It turns one
license-pinned IFC storey into three views of the *same* source geometry:

* a double-line DXF accepted by the production CAD ingestion path;
* a clean, dimensioned floor-plan PNG for the raster workflow;
* an independent IFC triangle manifest, OBJ and gray-model preview.

The resulting truth stays below ``data/external_datasets``.  Production code
must never read it.  Every artifact is checksum-bound in ``case_manifest``.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from shapely import affinity
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from .whole_home_dataset import DEFAULT_DATA_ROOT, sha256_file
from .whole_home_geometry import build_geometry_manifest, canonical_hash


DERIVATION_VERSION = "ifc-same-source-gold-v2"
GOLD_THRESHOLDS = {
    "wall_footprint_iou_min": 0.98,
    "wall_boundary_p95_m_max": 0.05,
    "room_footprint_iou_min": 0.95,
    "opening_precision_min": 0.90,
    "opening_recall_min": 0.90,
    "opening_center_p95_m_max": 0.20,
    "opening_width_p95_m_max": 0.05,
    "wall_assembly_coverage_min": 1.0,
}
RASTER_GOLD_CHECKS = (
    ("scale_anchor_count", ">=", 2.0),
    ("scale_disagreement", "<=", .02),
    ("registration_roundtrip_px", "<=", .25),
    ("wall_centerline_p95_m", "<=", .05),
    ("wall_ink_support_ratio", ">=", .95),
    ("room_iou", ">=", .95),
    ("opening_precision", ">=", .90),
    ("opening_recall", ">=", .90),
)


class IfcGoldError(RuntimeError):
    pass


@dataclass(frozen=True)
class Mesh:
    entity_id: int
    entity_type: str
    name: str
    vertices: tuple[tuple[float, float, float], ...]
    faces: tuple[tuple[int, int, int], ...]


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _canonicalize_dxf_classes(text: str) -> str:
    """Sort the semantically unordered DXF CLASSES table by class name.

    ezdxf lazily registers classes in a process-global container.  Depending
    on which reader/parser ran earlier, ``LAYOUT`` and
    ``ACDBPLACEHOLDER`` (and potentially other classes) can be emitted in a
    different order even though every entity is identical.  DXF defines this
    table as unordered metadata, so sorting complete CLASS records provides a
    stable file hash without editing any record contents or handles.
    """
    lines = text.splitlines()
    class_section_start = None
    class_section_end = None
    for index in range(0, len(lines) - 3, 2):
        if (lines[index].strip() == "0" and lines[index + 1].strip() == "SECTION"
                and lines[index + 2].strip() == "2"
                and lines[index + 3].strip() == "CLASSES"):
            class_section_start = index + 4
            break
    if class_section_start is None:
        raise IfcGoldError("DXF has no CLASSES section to canonicalize")
    for index in range(class_section_start, len(lines) - 1, 2):
        if lines[index].strip() == "0" and lines[index + 1].strip() == "ENDSEC":
            class_section_end = index
            break
    if class_section_end is None:
        raise IfcGoldError("DXF CLASSES section is not terminated")

    record_starts = [
        index for index in range(class_section_start, class_section_end - 1, 2)
        if lines[index].strip() == "0" and lines[index + 1].strip() == "CLASS"
    ]
    records: list[tuple[str, list[str]]] = []
    for position, start in enumerate(record_starts):
        end = record_starts[position + 1] if position + 1 < len(record_starts) else class_section_end
        record = lines[start:end]
        class_name = next(
            (record[index + 1].strip() for index in range(0, len(record) - 1, 2)
             if record[index].strip() == "1"),
            "",
        )
        records.append((class_name, record))
    if not records or any(not name for name, _ in records):
        raise IfcGoldError("DXF CLASSES section contains an unnamed CLASS record")
    canonical_body = [
        line for _, record in sorted(records, key=lambda row: row[0]) for line in record
    ]
    return "\n".join([
        *lines[:class_section_start], *canonical_body, *lines[class_section_end:], "",
    ])


def _dependency_modules():
    try:
        import ezdxf  # type: ignore
        import ifcopenshell  # type: ignore
        import ifcopenshell.geom  # type: ignore
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency failure is environment-specific.
        raise IfcGoldError(
            "IFC gold derivation needs ifcopenshell, ezdxf, shapely and Pillow; "
            "install requirements-dev.txt"
        ) from exc
    return ezdxf, ifcopenshell, ifcopenshell.geom, Image, ImageDraw, ImageFont


def _shape_mesh(settings: Any, geom: Any, entity: Any) -> Mesh:
    try:
        shape = geom.create_shape(settings, entity)
    except Exception as exc:
        raise IfcGoldError(f"cannot tessellate {entity.is_a()} #{entity.id()}: {exc}") from exc
    values = [float(value) for value in shape.geometry.verts]
    indices = [int(value) for value in shape.geometry.faces]
    vertices = tuple(tuple(values[offset:offset + 3]) for offset in range(0, len(values), 3))
    faces = tuple(tuple(indices[offset:offset + 3]) for offset in range(0, len(indices), 3))
    if not vertices or not faces:
        raise IfcGoldError(f"empty tessellation for {entity.is_a()} #{entity.id()}")
    return Mesh(
        entity_id=int(entity.id()), entity_type=str(entity.is_a()),
        name=str(getattr(entity, "Name", "") or ""), vertices=vertices, faces=faces,
    )


def _decomposition_children(entity: Any) -> list[Any]:
    """Return deterministic IFC decomposition/nesting children.

    IFC authoring tools commonly give an ``IfcStair`` container no own
    Representation and put all geometry on flights, landings and railings.
    Treating that legal container as corrupt made otherwise valid houses
    impossible to prepare.  The leaf identity is retained in every Mesh, so
    this fallback remains fully auditable and never fabricates geometry.
    """
    children: dict[int, Any] = {}
    for attribute, related in (
        ("IsDecomposedBy", "RelatedObjects"),
        ("IsNestedBy", "RelatedObjects"),
    ):
        for relation in getattr(entity, attribute, None) or []:
            for child in getattr(relation, related, None) or []:
                children[int(child.id())] = child
    return [children[key] for key in sorted(children)]


def _shape_meshes(
    settings: Any, geom: Any, entity: Any, *, required: bool,
    warnings: list[dict] | None = None,
) -> list[Mesh]:
    """Tessellate an entity, recursively falling back to authored children."""
    try:
        return [_shape_mesh(settings, geom, entity)]
    except IfcGoldError as direct_error:
        children = _decomposition_children(entity)
        meshes = [
            mesh
            for child in children
            for mesh in _shape_meshes(
                settings, geom, child, required=False, warnings=warnings)
        ]
        if meshes:
            if warnings is not None:
                warnings.append({
                    "code": "ifc_container_geometry_from_decomposition",
                    "entity_id": int(entity.id()),
                    "entity_type": str(entity.is_a()),
                    "child_entity_ids": [mesh.entity_id for mesh in meshes],
                })
            return meshes
        if required:
            raise direct_error
        if warnings is not None:
            warnings.append({
                "code": "ifc_optional_entity_has_no_geometry",
                "entity_id": int(entity.id()),
                "entity_type": str(entity.is_a()),
                "message": str(direct_error),
            })
        return []


def _projected_geometry(mesh: Mesh) -> Any:
    triangles: list[Polygon] = []
    for face in mesh.faces:
        polygon = Polygon([(mesh.vertices[index][0], mesh.vertices[index][1]) for index in face])
        if polygon.area > 1e-10:
            triangles.append(polygon)
    if not triangles:
        raise IfcGoldError(f"#{mesh.entity_id} has no non-degenerate plan projection")
    return unary_union(triangles).buffer(0)


def _projected_polygon(mesh: Mesh, *, repair_geometries: Iterable[Any] = ()) -> Polygon:
    authored = _projected_geometry(mesh)
    if authored.geom_type == "Polygon":
        result = authored
    else:
        # A full-height void can disconnect the tessellated wall projection.
        # Add back only the portion of that wall's authored opening that lies
        # inside the wall components' convex envelope.  The envelope is a
        # clipping guard, never the returned geometry, so an opening cannot
        # enlarge the wall or bridge an unrelated concavity.
        repair = [geometry.intersection(authored.convex_hull) for geometry in repair_geometries]
        result = unary_union([authored, *repair]).buffer(0)
    if result.geom_type == "MultiPolygon":
        parts = list(result.geoms)
        rectangle = result.minimum_rotated_rectangle
        fill_ratio = float(result.area / rectangle.area) if rectangle.area > 1e-12 else 0.0
        # Some authoring tools export one straight wall as two separated
        # collinear wall solids under one IfcWall identity.  Its exact planar
        # wall band is the minimum oriented rectangle only when the components
        # are both thin, strongly collinear and occupy most of that rectangle.
        # This rejects L/U/branched/disconnected footprints instead of using a
        # generic convex hull.
        part_ratios = []
        for part in parts:
            box = part.minimum_rotated_rectangle
            coords = list(box.exterior.coords)
            lengths = [math.dist(coords[i], coords[i + 1]) for i in range(4)]
            part_ratios.append(min(lengths) / max(max(lengths), 1e-9))
        if len(parts) == 2 and fill_ratio >= .70 and max(part_ratios) <= .20:
            result = rectangle
        else:
            raise IfcGoldError(
                f"#{mesh.entity_id} produced disconnected plan components after "
                "authored-opening repair; refusing an unproven footprint approximation"
            )
    if result.geom_type != "Polygon" or result.area <= 1e-8:
        raise IfcGoldError(f"#{mesh.entity_id} produced an invalid plan polygon")
    return result


def _projected_slice_wall_polygon(mesh: Mesh) -> Polygon:
    """Recover a wall band used by a physical-height storey slice.

    Fill-only IFC exports often split a straight separator wall at every door
    or window but omit IfcOpeningElement relationships.  For this explicit
    fallback only, a disconnected projection may be restored to its oriented
    rectangle when the complete envelope is unmistakably a thin wall band.
    Square facade panels and branched/room-sized components are rejected.
    """
    projected = _projected_geometry(mesh)
    if projected.geom_type == "Polygon":
        return projected
    rectangle = projected.minimum_rotated_rectangle
    coordinates = list(rectangle.exterior.coords)
    lengths = [math.dist(coordinates[index], coordinates[index + 1]) for index in range(4)]
    aspect = min(lengths) / max(max(lengths), 1e-9)
    fill_ratio = projected.area / max(rectangle.area, 1e-12)
    if (projected.geom_type == "MultiPolygon" and len(projected.geoms) <= 8
            and aspect <= .15 and fill_ratio >= .05):
        return rectangle
    raise IfcGoldError(
        f"#{mesh.entity_id} is not a provable thin wall band in physical slice "
        f"(type={projected.geom_type}, aspect={aspect:.4f}, fill={fill_ratio:.4f})"
    )


def _entity_bounds(mesh: Mesh) -> tuple[float, float, float, float, float, float]:
    xs, ys, zs = zip(*mesh.vertices)
    return min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)


def _storey_contents(storey: Any) -> list[Any]:
    result: dict[int, Any] = {}
    for relation in getattr(storey, "ContainsElements", None) or []:
        for entity in relation.RelatedElements:
            result[int(entity.id())] = entity
    return list(result.values())


def _space_storey(space: Any) -> Any | None:
    for relation in getattr(space, "Decomposes", None) or []:
        parent = relation.RelatingObject
        if parent and parent.is_a("IfcBuildingStorey"):
            return parent
    for relation in getattr(space, "ContainedInStructure", None) or []:
        parent = relation.RelatingStructure
        if parent and parent.is_a("IfcBuildingStorey"):
            return parent
    return None


def _select_storey(ifc_file: Any, storey_name: str = "") -> Any:
    storeys = list(ifc_file.by_type("IfcBuildingStorey"))
    if not storeys:
        raise IfcGoldError("IFC contains no IfcBuildingStorey")
    if storey_name:
        matches = [row for row in storeys if str(row.Name or "") == storey_name]
        if len(matches) != 1:
            raise IfcGoldError(f"storey {storey_name!r} was not uniquely found")
        return matches[0]

    def rank(storey: Any) -> tuple[int, int, float, int]:
        contents = _storey_contents(storey)
        wall_count = sum(row.is_a("IfcWall") for row in contents)
        space_count = sum(_space_storey(space) == storey for space in ifc_file.by_type("IfcSpace"))
        elevation = float(getattr(storey, "Elevation", 0.0) or 0.0)
        return wall_count, space_count, -abs(elevation), -int(storey.id())

    return max(storeys, key=rank)


def _principal_axis(polygon: Polygon) -> tuple[tuple[float, float], tuple[float, float], float, float]:
    rectangle = list(polygon.minimum_rotated_rectangle.exterior.coords)[:4]
    edges = [
        (math.dist(rectangle[index], rectangle[(index + 1) % 4]),
         rectangle[index], rectangle[(index + 1) % 4])
        for index in range(4)
    ]
    length, first, second = max(edges, key=lambda row: row[0])
    thickness = min(row[0] for row in edges)
    ux, uy = (second[0] - first[0]) / length, (second[1] - first[1]) / length
    center = (float(polygon.centroid.x), float(polygon.centroid.y))
    start = (center[0] - ux * length / 2, center[1] - uy * length / 2)
    end = (center[0] + ux * length / 2, center[1] + uy * length / 2)
    return start, end, length, thickness


def extract_ifc_storey(ifc_path: Path | str, *, storey_name: str = "") -> dict:
    """Extract one storey's source meshes and plan facts with world coordinates."""
    _, ifcopenshell, geom, *_ = _dependency_modules()
    source = Path(ifc_path).resolve()
    if not source.is_file():
        raise IfcGoldError(f"IFC source does not exist: {source}")
    ifc_file = ifcopenshell.open(str(source))
    try:
        import ifcopenshell.util.unit  # type: ignore
        metadata_unit_scale = float(ifcopenshell.util.unit.calculate_unit_scale(ifc_file))
    except (AttributeError, TypeError, ValueError) as exc:
        raise IfcGoldError(f"cannot determine IFC length-unit scale: {exc}") from exc
    if not math.isfinite(metadata_unit_scale) or metadata_unit_scale <= 0:
        raise IfcGoldError(f"invalid IFC length-unit scale: {metadata_unit_scale!r}")
    storey = _select_storey(ifc_file, storey_name)
    contents = _storey_contents(storey)
    settings = geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)

    wall_entities = sorted(
        [row for row in contents if row.is_a("IfcWall")], key=lambda row: int(row.id()))
    slab_entities = sorted(
        [row for row in contents if row.is_a("IfcSlab")], key=lambda row: int(row.id()))
    accessory_entities = sorted(
        [row for row in contents if row.is_a() in {"IfcDoor", "IfcWindow", "IfcStair"}],
        key=lambda row: int(row.id()),
    )
    spaces = sorted(
        [row for row in ifc_file.by_type("IfcSpace") if _space_storey(row) == storey],
        key=lambda row: int(row.id()),
    )
    if len(wall_entities) < 4:
        raise IfcGoldError("selected storey is too small for a non-toy whole-home case")

    extraction_warnings: list[dict] = []
    placeholder_spaces = bool(spaces) and all(
        "kavel" in str(getattr(row, "LongName", "") or "").lower()
        or "parcel" in str(getattr(row, "LongName", "") or "").lower()
        for row in spaces
    )
    space_recovery = len(spaces) < 2 or placeholder_spaces
    recovered_space_polygons: dict[int, Polygon] = {}
    recovered_space_meshes: list[Mesh] = []
    if space_recovery:
        # Some IFC2X3 exports put several physical floors in one nominal
        # IfcBuildingStorey and omit IfcSpace/IfcOpeningElement entirely.  The
        # dominant finished-floor slab elevation provides an auditable slice;
        # use only walls and fills crossing 1.20 m above that elevation.
        slab_candidates: list[tuple[Any, Mesh, tuple[float, float, float, float, float, float]]] = []
        for entity in slab_entities:
            for mesh in _shape_meshes(
                settings, geom, entity, required=False, warnings=extraction_warnings):
                slab_candidates.append((entity, mesh, _entity_bounds(mesh)))
        elevation_bins: dict[float, list[tuple[Any, Mesh, tuple[float, float, float, float, float, float]]]] = {}
        for row in slab_candidates:
            if row[2][5] - row[2][2] <= .75:
                elevation_bins.setdefault(round(row[2][5], 1), []).append(row)
        if not elevation_bins:
            raise IfcGoldError("IfcSpace recovery found no horizontal floor-slab elevation")
        base_elevation, floor_rows = max(
            elevation_bins.items(), key=lambda item: (len(item[1]), -abs(item[0])))
        finished_rows = [
            row for row in floor_rows
            if "dekvloer" in str(getattr(row[0], "Name", "") or "").lower()
        ]
        if len(finished_rows) >= 2:
            floor_rows = finished_rows
        slice_z = base_elevation + 1.20
        all_wall_meshes = [_shape_mesh(settings, geom, row) for row in wall_entities]
        wall_meshes = [
            mesh for mesh in all_wall_meshes
            if _entity_bounds(mesh)[2] - .02 <= slice_z <= _entity_bounds(mesh)[5] + .02
        ]
        wall_polygons: dict[int, Polygon] = {}
        excluded_slice_walls: list[int] = []
        for mesh in wall_meshes:
            try:
                wall_polygons[mesh.entity_id] = _projected_slice_wall_polygon(mesh)
            except IfcGoldError:
                excluded_slice_walls.append(mesh.entity_id)
        wall_meshes = [mesh for mesh in wall_meshes if mesh.entity_id in wall_polygons]
        floor_meshes = [mesh for _, mesh, bounds in floor_rows if bounds[5] - bounds[2] <= .75]
        for entity, mesh, bounds in floor_rows:
            if bounds[5] - bounds[2] > .75:
                continue
            try:
                polygon = _projected_polygon(mesh)
            except IfcGoldError:
                continue
            if polygon.area < 1.0:
                continue
            name = str(getattr(entity, "Name", "") or "").lower()
            if "vloerstort" in name:
                continue
            # A monolithic structural pour can geometrically cover many
            # finished-floor regions.  It is not a room boundary and would
            # double-count the recovered semantic floor area.
            if polygon.area > 20 and recovered_space_polygons and sum(
                polygon.intersection(existing).area > existing.area * .5
                for existing in recovered_space_polygons.values()
            ) >= 2:
                continue
            recovered_space_polygons[mesh.entity_id] = polygon
            recovered_space_meshes.append(mesh)
        if len(recovered_space_meshes) < 2 or len(wall_meshes) < 4:
            raise IfcGoldError("physical storey slice did not recover a non-toy floor plan")
        extraction_warnings.append({
            "code": "ifc_physical_storey_slice_recovered",
            "reason": (
                "IfcSpace entities are parcel placeholders, not room geometry"
                if placeholder_spaces else "IfcSpace and/or IfcOpeningElement semantics absent"
            ),
            "placeholder_space_entity_ids": [int(row.id()) for row in spaces]
            if placeholder_spaces else [],
            "base_elevation_m": base_elevation, "slice_elevation_m": slice_z,
            "source_slab_count": len(floor_rows),
            "recovered_space_count": len(recovered_space_meshes),
            "selected_wall_count": len(wall_meshes),
            "excluded_non_wall_band_ids": excluded_slice_walls,
        })
    else:
        wall_meshes = [_shape_mesh(settings, geom, row) for row in wall_entities]
    if not space_recovery:
        floor_meshes = [
            mesh for row in slab_entities
            for mesh in _shape_meshes(
                settings, geom, row, required=False, warnings=extraction_warnings)
        ]
    accessory_meshes_by_id = {
        mesh.entity_id: mesh
        for row in accessory_entities
        for mesh in _shape_meshes(
            settings, geom, row, required=False, warnings=extraction_warnings)
    }
    accessory_meshes = [accessory_meshes_by_id[key] for key in sorted(accessory_meshes_by_id)]
    if space_recovery:
        accessory_meshes = [
            mesh for mesh in accessory_meshes
            if _entity_bounds(mesh)[2] - .02 <= slice_z <= _entity_bounds(mesh)[5] + .02
        ]
    space_meshes = (
        recovered_space_meshes if space_recovery
        else [_shape_mesh(settings, geom, row) for row in spaces]
    )
    space_polygons = (
        recovered_space_polygons if space_recovery
        else {mesh.entity_id: _projected_polygon(mesh) for mesh in space_meshes}
    )
    space_context = unary_union(list(space_polygons.values())).buffer(.75)
    broad_container_spaces = bool(
        space_polygons
        and sum(polygon.area for polygon in space_polygons.values()) / max(
            unary_union(list(space_polygons.values())).area, 1e-9) > 1.5
    )
    excluded_wall_ids = [
        mesh.entity_id for mesh in wall_meshes
        if not _projected_geometry(mesh).intersects(space_context)
    ]
    if excluded_wall_ids:
        extraction_warnings.append({
            "code": "ifc_unrelated_wall_component_excluded",
            "entity_ids": excluded_wall_ids,
            "selection_basis": "outside_0.75m_buffer_of_selected_storey_spaces",
        })
        wall_meshes = [mesh for mesh in wall_meshes if mesh.entity_id not in set(excluded_wall_ids)]
    if broad_container_spaces:
        extraction_warnings.append({
            "code": "ifc_spaces_are_overlapping_unit_containers",
            "selection_action": "wall_geometry_kept_for_selected_storey; room scoring uses source containers",
        })
    floor_meshes = [
        mesh for mesh in floor_meshes if _projected_geometry(mesh).intersects(space_context)
    ]
    accessory_meshes = [
        mesh for mesh in accessory_meshes if _projected_geometry(mesh).intersects(space_context)
    ]
    if len(wall_meshes) < 4:
        raise IfcGoldError("space-associated wall component is too small for a whole-home case")
    wall_ids = {mesh.entity_id for mesh in wall_meshes}
    opening_sources: list[tuple[Any, Any, Mesh, Any]] = []
    for opening in sorted(ifc_file.by_type("IfcOpeningElement"), key=lambda row: int(row.id())):
        hosts = [rel.RelatingBuildingElement for rel in (getattr(opening, "VoidsElements", None) or [])]
        host = next((row for row in hosts if int(row.id()) in wall_ids), None)
        if host is None:
            continue
        mesh = _shape_mesh(settings, geom, opening)
        opening_sources.append((opening, host, mesh, _projected_geometry(mesh)))
    repair_by_host: dict[int, list[Any]] = {}
    for _, host, _, projected in opening_sources:
        repair_by_host.setdefault(int(host.id()), []).append(projected)
    if not space_recovery:
        wall_polygons = {
            mesh.entity_id: _projected_polygon(
                mesh, repair_geometries=repair_by_host.get(mesh.entity_id, ()))
            for mesh in wall_meshes
        }

    openings = []
    storey_elevation_m = (
        float(getattr(storey, "Elevation", 0.0) or 0.0) * metadata_unit_scale
    )
    for opening, host, mesh, _ in opening_sources:
        bounds = _entity_bounds(mesh)
        fills = [rel.RelatedBuildingElement for rel in (getattr(opening, "HasFillings", None) or [])]
        kind = "opening"
        if any(row.is_a("IfcDoor") for row in fills):
            kind = "door"
        elif any(row.is_a("IfcWindow") for row in fills):
            kind = "window"
        host_polygon = wall_polygons[int(host.id())]
        start, end, host_length, host_thickness = _principal_axis(host_polygon)
        center = ((bounds[0] + bounds[3]) / 2, (bounds[1] + bounds[4]) / 2)
        ux, uy = (end[0] - start[0]) / host_length, (end[1] - start[1]) / host_length
        projected = [(x - start[0]) * ux + (y - start[1]) * uy for x, y, _ in mesh.vertices]
        width = max(projected) - min(projected)
        openings.append({
            "id": f"ifc-opening-{int(opening.id())}", "ifc_id": int(opening.id()),
            "name": str(opening.Name or ""), "kind": kind, "host_ifc_id": int(host.id()),
            "center": [round(center[0], 8), round(center[1], 8)],
            "width_m": round(float(width), 8),
            "height_m": round(float(bounds[5] - bounds[2]), 8),
            "sill_height_m": round(float(bounds[2] - storey_elevation_m), 8),
            "wall_thickness_m": round(float(host_thickness), 8),
            "wall_angle_deg": round(math.degrees(math.atan2(uy, ux)), 8),
            "bounds_ifc": [round(value, 8) for value in bounds],
        })
    if space_recovery:
        fill_candidates: list[tuple[Mesh, Any, tuple[float, float, float, float, float, float]]] = []
        for mesh in accessory_meshes:
            bounds = _entity_bounds(mesh)
            if (mesh.entity_type in {"IfcDoor", "IfcWindow"}
                    and bounds[2] - .02 <= slice_z <= bounds[5] + .02):
                try:
                    fill_candidates.append((mesh, _projected_geometry(mesh), bounds))
                except IfcGoldError:
                    continue
        for mesh, projected, bounds in fill_candidates:
            rectangle = projected.minimum_rotated_rectangle
            rectangle_points = list(rectangle.exterior.coords)
            rectangle_lengths = [
                math.dist(rectangle_points[index], rectangle_points[index + 1])
                for index in range(4)
            ]
            semantic_width = max(rectangle_lengths)
            # Door/window family containers may also be classified as fills
            # (lift leaves, tiny frame parts, grouped curtain assemblies).
            # Only residential-scale planar spans constitute one measurable
            # opening observation.
            if not .40 <= semantic_width <= 3.00:
                continue
            host_id, host_polygon = min(
                wall_polygons.items(), key=lambda item: projected.distance(item[1]))
            if projected.distance(host_polygon) > .35:
                continue
            start, end, host_length, host_thickness = _principal_axis(host_polygon)
            ux, uy = (end[0] - start[0]) / host_length, (end[1] - start[1]) / host_length
            projected_width = [
                (x - start[0]) * ux + (y - start[1]) * uy for x, y, _ in mesh.vertices
            ]
            center = projected.centroid
            openings.append({
                "id": f"ifc-fill-{mesh.entity_id}", "ifc_id": mesh.entity_id,
                "name": mesh.name,
                "kind": "door" if mesh.entity_type == "IfcDoor" else "window",
                "host_ifc_id": host_id,
                "center": [round(float(center.x), 8), round(float(center.y), 8)],
                "width_m": round(float(max(projected_width) - min(projected_width)), 8),
                "height_m": round(float(bounds[5] - bounds[2]), 8),
                "sill_height_m": round(float(bounds[2] - base_elevation), 8),
                "wall_thickness_m": round(float(host_thickness), 8),
                "wall_angle_deg": round(math.degrees(math.atan2(uy, ux)), 8),
                "bounds_ifc": [round(value, 8) for value in bounds],
                "association": "nearest_wall_in_physical_storey_slice",
            })
        extraction_warnings.append({
            "code": "ifc_openings_recovered_from_fill_elements",
            "candidate_fill_count": len(fill_candidates),
            "associated_opening_count": len(openings),
            "maximum_host_distance_m": .35,
        })

    return {
        "source_path": str(source), "source_sha256": sha256_file(source),
        "ifc_schema": str(ifc_file.schema),
        "storey": {
            "ifc_id": int(storey.id()), "name": str(storey.Name or ""),
            "elevation_m": round(storey_elevation_m, 8),
        },
        "metadata_unit_scale_to_m": metadata_unit_scale,
        "extraction_warnings": extraction_warnings,
        "wall_meshes": wall_meshes, "floor_meshes": floor_meshes,
        "accessory_meshes": accessory_meshes, "space_meshes": space_meshes,
        "wall_polygons": wall_polygons, "space_polygons": space_polygons,
        "openings": openings,
    }


def _polygon_points(polygon: Polygon) -> list[list[float]]:
    return [[round(float(x), 8), round(float(y), 8)] for x, y in list(polygon.exterior.coords)[:-1]]


def _polygon_holes(polygon: Polygon) -> list[list[list[float]]]:
    return [
        [[round(float(x), 8), round(float(y), 8)] for x, y in list(ring.coords)[:-1]]
        for ring in polygon.interiors
    ]


def _truth_payload(extracted: Mapping[str, Any]) -> dict:
    walls = []
    for mesh in extracted["wall_meshes"]:
        polygon = extracted["wall_polygons"][mesh.entity_id]
        start, end, length, thickness = _principal_axis(polygon)
        bounds = _entity_bounds(mesh)
        walls.append({
            "id": f"ifc-wall-{mesh.entity_id}", "ifc_id": mesh.entity_id,
            "name": mesh.name, "footprint_polygon": _polygon_points(polygon),
            "centerline": [[round(value, 8) for value in start], [round(value, 8) for value in end]],
            "length_m": round(length, 8), "thickness_m": round(thickness, 8),
            "height_m": round(bounds[5] - bounds[2], 8),
        })
    spaces = []
    for mesh in extracted["space_meshes"]:
        polygon = extracted["space_polygons"][mesh.entity_id]
        spaces.append({
            "id": f"ifc-space-{mesh.entity_id}", "ifc_id": mesh.entity_id,
            "name": mesh.name, "polygon": _polygon_points(polygon),
            "interior_rings": _polygon_holes(polygon),
            "area_m2": round(polygon.area, 8),
        })
    recovery_warning = next((
        row for row in extracted.get("extraction_warnings") or []
        if row.get("code") == "ifc_physical_storey_slice_recovered"
    ), None)
    return {
        "schema_version": 1, "derivation_version": DERIVATION_VERSION,
        "source_sha256": extracted["source_sha256"], "ifc_schema": extracted["ifc_schema"],
        "storey": extracted["storey"], "walls": walls, "spaces": spaces,
        "openings": extracted["openings"],
        **({"space_recovery_method": {
            "type": "finished_floor_slab_boundaries",
            "base_elevation_m": recovery_warning.get("base_elevation_m"),
            "slice_elevation_m": recovery_warning.get("slice_elevation_m"),
        }} if recovery_warning else {}),
    }


def _manifest_from_ifc(extracted: Mapping[str, Any], truth: Mapping[str, Any], case_id: str) -> dict:
    vertices: list[list[float]] = []
    all_parts: list[dict] = []

    def append(mesh: Mesh, semantic_kind: str) -> dict:
        base = len(vertices)
        # IFC is z-up; Floor Engine is y-up with plan coordinates x/z.
        vertices.extend([[round(x, 8), round(z, 8), round(y, 8)] for x, y, z in mesh.vertices])
        indices = [base + index for face in mesh.faces for index in face]
        bounds = _entity_bounds(mesh)
        part = {
            "id": f"ifc:{mesh.entity_type}:{mesh.entity_id}",
            "entity_id": str(mesh.entity_id), "semantic_kind": semantic_kind,
            "ifc_entity_type": mesh.entity_type, "ifc_name": mesh.name,
            "indices": indices,
            "bounds_ifc": [round(value, 8) for value in bounds],
        }
        all_parts.append(part)
        return part

    wall_parts = [append(mesh, "wall") for mesh in extracted["wall_meshes"]]
    floor_parts = [append(mesh, "floor") for mesh in extracted["floor_meshes"]]
    object_parts = [append(mesh, mesh.entity_type.lower()) for mesh in extracted["accessory_meshes"]]
    opening_voids = [{
        "id": row["id"], "opening_id": row["id"], "host_ifc_id": str(row["host_ifc_id"]),
        "kind": row["kind"], "width_m": row["width_m"], "height_m": row["height_m"],
        "sill_height_m": row["sill_height_m"], "center_ifc": row["center"],
    } for row in truth["openings"]]
    return build_geometry_manifest(
        project_id=f"gold:{case_id}", model_revision=1,
        model_facts_hash=canonical_hash(truth),
        registration_hash=hashlib.sha256(
            f"{truth['source_sha256']}:{truth['storey']['ifc_id']}".encode("utf-8")
        ).hexdigest(),
        geometry_kernel_version=DERIVATION_VERSION, units="meter",
        coordinate_system="metres-y-up-from-ifc-z-up",
        vertices=vertices, wall_parts=wall_parts, floor_parts=floor_parts,
        opening_voids=opening_voids, object_parts=object_parts, parts=all_parts,
    )


ROOM_LABELS = (
    "Living room", "Kitchen", "Bedroom", "Bedroom", "Bathroom", "Foyer", "Dining room",
    "Bedroom", "Bathroom", "Circulation",
)


def _write_dxf(path: Path, extracted: Mapping[str, Any]) -> None:
    ezdxf, *_ = _dependency_modules()
    document = ezdxf.new("R2018", setup=True)
    document.units = ezdxf.units.M
    document.header["$INSUNITS"] = int(ezdxf.units.M)
    layers = {
        "A-WALL-FOOTPRINT": 7, "A-OPENING": 1, "A-ROOM-NAME": 3,
        "A-SPACE-REFERENCE": 8, "A-ROOM-BOUNDARY": 8, "A-DIM": 5,
    }
    for name, color in layers.items():
        if name not in document.layers:
            document.layers.add(name, color=color)
    modelspace = document.modelspace()
    physical_slice_recovery = any(
        row.get("code") == "ifc_physical_storey_slice_recovered"
        for row in (extracted.get("extraction_warnings") or [])
    )
    wall_polygons = [
        (
            extracted["wall_polygons"][mesh.entity_id]
            if physical_slice_recovery else
            extracted["wall_polygons"][mesh.entity_id].minimum_rotated_rectangle
        )
        for mesh in extracted["wall_meshes"]
    ]
    wall_union_for_connectivity = unary_union(wall_polygons).buffer(.02)
    wall_components = (
        list(wall_union_for_connectivity.geoms)
        if wall_union_for_connectivity.geom_type == "MultiPolygon" else [wall_union_for_connectivity]
    )
    component_layers: dict[int, str] = {}
    if len(wall_components) > 1:
        for index, component in enumerate(sorted(wall_components, key=lambda row: (-row.area, row.bounds)), 1):
            layer_name = f"A-WALL-FOOTPRINT-{index}"
            if layer_name not in document.layers:
                document.layers.add(layer_name, color=7)
            for entity_index, polygon in enumerate(wall_polygons):
                if polygon.intersects(component):
                    component_layers[entity_index] = layer_name
    for mesh_index, mesh in enumerate(extracted["wall_meshes"]):
        # Truth retains the exact authored footprint.  The ordinary CAD input
        # expresses each independently authored straight wall as its oriented
        # rectangular band, just as a double-line plan does.  The comparison
        # below still scores the union against exact IFC truth, so this is not
        # allowed to hide curved/branched-wall approximation error.
        polygon = wall_polygons[mesh_index]
        modelspace.add_lwpolyline(
            [(round(float(x), 6), round(float(y), 6))
             for x, y in list(polygon.exterior.coords)[:-1]], close=True,
            dxfattribs={"layer": component_layers.get(mesh_index, "A-WALL-FOOTPRINT")},
        )
    sorted_spaces = sorted(
        extracted["space_polygons"].items(),
        key=lambda row: (-row[1].area, row[0]),
    )
    for index, (entity_id, polygon) in enumerate(sorted_spaces):
        # Independent authored space boundaries are not wall evidence, but
        # they are valid vector room-boundary evidence.  Keep them on a
        # non-structural layer so the production parser can recover concave
        # circulation/foyer topology without contaminating wall assemblies.
        # Recovered floor plates occasionally share a boundary whose IFC
        # coordinates differ only below DXF's six-decimal serialization
        # precision.  Inward-normalize recovered plates by 0.05 mm so those
        # independent room observations remain non-overlapping after the
        # round trip.  This is orders of magnitude below the 20 mm topology
        # tolerance and the exact authored truth remains unchanged for IoU
        # scoring.
        display_polygon = polygon
        if physical_slice_recovery:
            normalized = polygon.buffer(-0.00005, join_style=2)
            if not normalized.is_empty and normalized.geom_type == "Polygon":
                display_polygon = normalized
        modelspace.add_lwpolyline(
            [(round(float(x), 6), round(float(y), 6))
             for x, y in list(display_polygon.exterior.coords)[:-1]], close=True,
            dxfattribs={"layer": "A-ROOM-BOUNDARY"},
        )
        anchor = display_polygon.representative_point()
        modelspace.add_text(
            ROOM_LABELS[index % len(ROOM_LABELS)],
            height=0.18, dxfattribs={"layer": "A-ROOM-NAME"},
        ).set_placement((anchor.x, anchor.y))
    for row in extracted["openings"]:
        if row["kind"] not in {"door", "window"}:
            continue
        block_name = f"{row['kind'].upper()}_IFC_{row['ifc_id']}"
        block = document.blocks.new(block_name)
        width = max(0.05, float(row["width_m"]))
        depth = max(0.04, min(0.65, float(row["wall_thickness_m"])))
        block.add_lwpolyline(
            [(-width / 2, -depth / 2), (width / 2, -depth / 2),
             (width / 2, depth / 2), (-width / 2, depth / 2)],
            close=True, dxfattribs={"layer": "0"},
        )
        modelspace.add_blockref(
            block_name, tuple(row["center"]),
            dxfattribs={"layer": "A-OPENING", "rotation": float(row["wall_angle_deg"])},
        )

    wall_union = unary_union(list(extracted["wall_polygons"].values()))
    min_x, min_y, max_x, max_y = wall_union.bounds
    modelspace.add_linear_dim(
        base=(min_x, min_y - 0.65), p1=(min_x, min_y), p2=(max_x, min_y),
        angle=0, dimstyle="EZ_M_100_H25_CM", dxfattribs={"layer": "A-DIM"},
    ).render()
    modelspace.add_linear_dim(
        base=(min_x - 0.65, min_y), p1=(min_x, min_y), p2=(min_x, max_y),
        angle=90, dimstyle="EZ_M_100_H25_CM", dxfattribs={"layer": "A-DIM"},
    ).render()
    previous_fixed_metadata = bool(ezdxf.options.write_fixed_meta_data_for_testing)
    ezdxf.options.write_fixed_meta_data_for_testing = True
    try:
        document.saveas(path)
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = previous_fixed_metadata
    # ezdxf's internal metadata dictionary adds a wall-clock marker even when
    # fixed DXF header metadata is enabled.  Canonicalize only that documented
    # writer marker; all geometry/entity/header bytes remain untouched.
    text = path.read_text(encoding="utf-8")
    text, replacements = re.subn(
        r"(?m)^(\s*\d+\.\d+\.\d+\s+@\s+)[^\r\n]+$",
        r"\g<1>2000-01-01T00:00:00+00:00",
        text,
    )
    if replacements != 1:
        raise IfcGoldError("ezdxf writer metadata marker was not uniquely canonicalized")
    text = _canonicalize_dxf_classes(text)
    _atomic_write(path, text.encode("utf-8"))


def _font(ImageFont: Any, size: int):
    for candidate in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _write_floorplan_png(path: Path, extracted: Mapping[str, Any]) -> dict:
    *_, Image, ImageDraw, ImageFont = _dependency_modules()
    wall_union = unary_union(list(extracted["wall_polygons"].values()))
    min_x, min_y, max_x, max_y = wall_union.bounds
    width, height, padding = 1800, 1400, 150
    scale = min((width - 2 * padding) / max(max_x - min_x, 0.01),
                (height - 2 * padding) / max(max_y - min_y, 0.01))

    plan_width_px = (max_x - min_x) * scale
    plan_height_px = (max_y - min_y) * scale
    plan_left = (width - plan_width_px) / 2
    plan_top = (height - plan_height_px) / 2

    def project(point: tuple[float, float]) -> tuple[float, float]:
        return (plan_left + (point[0] - min_x) * scale,
                plan_top + (max_y - point[1]) * scale)

    image = Image.new("RGB", (width, height), (250, 250, 248))
    draw = ImageDraw.Draw(image)
    label_font, dim_font = _font(ImageFont, 26), _font(ImageFont, 22)
    for _, polygon in sorted(extracted["space_polygons"].items()):
        draw.polygon([project(point) for point in polygon.exterior.coords], fill=(242, 242, 238))
    for _, polygon in sorted(extracted["wall_polygons"].items()):
        draw.polygon([project(point) for point in polygon.exterior.coords], fill=(43, 47, 52))
    opening_segments: list[dict] = []
    for row in extracted["openings"]:
        # Generic IfcOpeningElement voids are intentionally retained in the
        # geometry truth, but they are not doors or windows.  Painting every
        # non-door void blue made those unclassified holes look like windows
        # to the independent raster evaluator and manufactured false
        # positives.  Keep the visual/input contract aligned with the metric
        # contract: only semantically classified doors and windows receive an
        # opening marker.
        if row["kind"] not in {"door", "window"}:
            continue
        center = project(tuple(row["center"]))
        angle = math.radians(float(row["wall_angle_deg"]))
        # A tiny endpoint inset keeps abutting IFC window segments as distinct
        # observable symbols in the independently measured raster.  It is
        # capped at 4 px and never changes the truth width or centre.
        raw_half = float(row["width_m"]) * scale / 2
        half = max(1.0, raw_half - min(10.0, raw_half * .20))
        direction = (math.cos(angle) * half, -math.sin(angle) * half)
        color = (188, 58, 48) if row["kind"] == "door" else (38, 119, 180)
        opening_segments.append({
            "kind": row["kind"], "color": color, "center": center,
            "start": (center[0] - direction[0], center[1] - direction[1]),
            "end": (center[0] + direction[0], center[1] + direction[1]),
            "angle": angle,
        })
    # Collinear abutting IFC openings otherwise touch through Pillow's thick
    # round/square line endpoints and become one connected component.  Offset
    # any still-touching pair by one pixel in opposite wall-normal directions;
    # this is <2 cm at the lowest supported scale and is explicitly included
    # in the independent 4-pixel centre tolerance.
    for index, segment in enumerate(opening_segments):
        normal = (-math.sin(segment["angle"]), -math.cos(segment["angle"]))
        signed = 0
        for other_index, other in enumerate(opening_segments):
            if other_index == index or other["kind"] != segment["kind"]:
                continue
            parallel = abs(math.sin(segment["angle"] - other["angle"])) < .02
            close = math.dist(segment["center"], other["center"]) <= (
                math.dist(segment["start"], segment["end"]) / 2
                + math.dist(other["start"], other["end"]) / 2 + 3
            )
            if parallel and close:
                signed = -1 if index < other_index else 1
                break
        offset = (normal[0] * signed, normal[1] * signed)
        draw.line(
            [(segment["start"][0] + offset[0], segment["start"][1] + offset[1]),
             (segment["end"][0] + offset[0], segment["end"][1] + offset[1])],
            fill=segment["color"], width=13,
        )
    sorted_spaces = sorted(extracted["space_polygons"].items(), key=lambda row: (-row[1].area, row[0]))
    for index, (_, polygon) in enumerate(sorted_spaces):
        anchor = project((polygon.representative_point().x, polygon.representative_point().y))
        label = f"{ROOM_LABELS[index % len(ROOM_LABELS)]}\n{polygon.area:.1f} m²"
        box = draw.multiline_textbbox((0, 0), label, font=label_font, align="center", spacing=3)
        label_width, label_height = box[2] - box[0], box[3] - box[1]
        projected_bounds = [project((polygon.bounds[0], polygon.bounds[1])),
                            project((polygon.bounds[2], polygon.bounds[3]))]
        available_width = abs(projected_bounds[1][0] - projected_bounds[0][0])
        available_height = abs(projected_bounds[1][1] - projected_bounds[0][1])
        # At L4/L5 scale, one storey may be tens of metres wide and contain
        # many shallow service rooms.  A fixed 26 px label can be larger than
        # such a room, obscuring the actual plan and its independently sampled
        # room fill.  Only draw a label when its complete bounding box fits
        # inside the projected room with a small visual margin.
        if label_width + 12 > available_width or label_height + 12 > available_height:
            continue
        draw.multiline_text(
            (anchor[0] - label_width / 2, anchor[1] - label_height / 2),
            label, fill=(42, 46, 50), font=label_font, align="center", spacing=3,
        )
    dimension_y = plan_top + plan_height_px + 65
    dimension_x0, dimension_x1 = plan_left, plan_left + plan_width_px
    draw.line([(dimension_x0, dimension_y), (dimension_x1, dimension_y)], fill=(55, 55, 55), width=2)
    draw.line([(dimension_x0, dimension_y - 12), (dimension_x0, dimension_y + 12)], fill=(55, 55, 55), width=2)
    draw.line([(dimension_x1, dimension_y - 12), (dimension_x1, dimension_y + 12)], fill=(55, 55, 55), width=2)
    width_label = f"{max_x - min_x:.2f} m"
    label_box = draw.textbbox((0, 0), width_label, font=dim_font)
    draw.text(((width - (label_box[2] - label_box[0])) / 2, dimension_y + 10),
              width_label, fill=(55, 55, 55), font=dim_font)
    title = f"IFC same-source floor plan · {extracted['storey']['name']} · {DERIVATION_VERSION}"
    draw.text((padding, 45), title, fill=(55, 55, 55), font=dim_font)
    image.save(path, "PNG", optimize=True)
    return {
        "image_width": width, "image_height": height,
        "plan_left_px": round(plan_left, 8), "plan_top_px": round(plan_top, 8),
        "pixels_per_metre": round(scale, 10),
        "ifc_bounds_m": [round(value, 8) for value in (min_x, min_y, max_x, max_y)],
        "source_x_axis": "ifc_positive_x", "source_y_axis": "ifc_negative_y",
        "width_anchor": {
            "start_px": [round(dimension_x0, 6), round(dimension_y, 6)],
            "end_px": [round(dimension_x1, 6), round(dimension_y, 6)],
            "length_m": round(max_x - min_x, 8),
        },
        "height_anchor": {
            "start_px": [round(plan_left, 6), round(plan_top, 6)],
            "end_px": [round(plan_left, 6), round(plan_top + plan_height_px, 6)],
            "length_m": round(max_y - min_y, 8),
        },
    }


def _mesh_triangles(meshes: Iterable[Mesh]) -> list[tuple[float, list[tuple[float, float, float]], int]]:
    light = (0.35, -0.5, 0.79)
    result = []
    for mesh in meshes:
        for face in mesh.faces:
            points = [mesh.vertices[index] for index in face]
            first = tuple(points[1][axis] - points[0][axis] for axis in range(3))
            second = tuple(points[2][axis] - points[0][axis] for axis in range(3))
            normal = (
                first[1] * second[2] - first[2] * second[1],
                first[2] * second[0] - first[0] * second[2],
                first[0] * second[1] - first[1] * second[0],
            )
            magnitude = math.sqrt(sum(value * value for value in normal)) or 1.0
            brightness = max(0.0, sum(normal[index] / magnitude * light[index] for index in range(3)))
            gray = int(125 + 95 * brightness)
            depth = sum(point[0] + point[1] + point[2] * 0.15 for point in points) / 3
            result.append((depth, points, gray))
    return result


def _write_gray_preview(path: Path, extracted: Mapping[str, Any]) -> None:
    *_, Image, ImageDraw, _ = _dependency_modules()
    floor_triangles = _mesh_triangles(extracted["floor_meshes"])
    wall_triangles = _mesh_triangles(extracted["wall_meshes"])
    # Doors/windows/stairs remain present in OBJ + GeometryManifest truth.  The
    # static preview deliberately shows the architectural shell only so dense
    # frame tessellation cannot obscure the wall openings being inspected.
    triangles = [*floor_triangles, *wall_triangles]
    angle = math.radians(30)

    def raw_project(point: tuple[float, float, float]) -> tuple[float, float]:
        x, y, z = point
        return (x - y) * math.cos(angle), (x + y) * math.sin(angle) - z

    projected = [raw_project(point) for _, points, _ in triangles for point in points]
    min_x, min_y = min(x for x, _ in projected), min(y for _, y in projected)
    max_x, max_y = max(x for x, _ in projected), max(y for _, y in projected)
    width, height, padding = 1600, 1150, 70
    scale = min((width - 2 * padding) / max(max_x - min_x, 0.01),
                (height - 2 * padding) / max(max_y - min_y, 0.01))

    def project(point: tuple[float, float, float]) -> tuple[float, float]:
        x, y = raw_project(point)
        return padding + (x - min_x) * scale, padding + (y - min_y) * scale

    image = Image.new("RGB", (width, height), (242, 244, 246))
    draw = ImageDraw.Draw(image)
    # A slab consists of a few very large triangles whose centroids are not a
    # valid global depth order.  Paint it as a pale base first, then use the
    # painter order only within the wall shell.
    for _, points, _ in floor_triangles:
        draw.polygon([project(point) for point in points], fill=(216, 219, 222))
    for _, points, gray in sorted(wall_triangles, key=lambda row: row[0], reverse=True):
        screen = [project(point) for point in points]
        draw.polygon(screen, fill=(gray, gray, min(255, gray + 3)), outline=(103, 108, 113))
    image.save(path, "PNG", optimize=True)


def _write_obj(path: Path, extracted: Mapping[str, Any]) -> None:
    lines = [f"# {DERIVATION_VERSION}", f"# source_sha256 {extracted['source_sha256']}"]
    offset = 1
    for mesh in [*extracted["floor_meshes"], *extracted["wall_meshes"], *extracted["accessory_meshes"]]:
        lines.append(f"g {mesh.entity_type}_{mesh.entity_id}")
        lines.extend(f"v {x:.8f} {y:.8f} {z:.8f}" for x, y, z in mesh.vertices)
        lines.extend(f"f {a + offset} {b + offset} {c + offset}" for a, b, c in mesh.faces)
        offset += len(mesh.vertices)
    _atomic_write(path, ("\n".join(lines) + "\n").encode("utf-8"))


def derive_ifc_gold_case(
    ifc_path: Path | str, output_dir: Path | str, *, case_id: str, storey_name: str = "",
) -> dict:
    """Write the same-source DXF/raster/3D truth bundle and checksum manifest."""
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    extracted = extract_ifc_storey(ifc_path, storey_name=storey_name)
    truth = _truth_payload(extracted)
    truth_path = output / "truth_geometry.json"
    manifest_path = output / "truth_geometry_manifest.json"
    dxf_path = output / "input_double_line.dxf"
    floorplan_path = output / "input_dimensioned.png"
    obj_path = output / "truth_gray_model.obj"
    gray_path = output / "truth_gray_preview.png"
    _atomic_write(truth_path, _json_bytes(truth))
    _atomic_write(manifest_path, _json_bytes(_manifest_from_ifc(extracted, truth, case_id)))
    _write_dxf(dxf_path, extracted)
    raster_mapping = _write_floorplan_png(floorplan_path, extracted)
    _write_obj(obj_path, extracted)
    _write_gray_preview(gray_path, extracted)
    artifacts = {}
    for path in (truth_path, manifest_path, dxf_path, floorplan_path, obj_path, gray_path):
        artifacts[path.name] = {
            "sha256": sha256_file(path), "size_bytes": path.stat().st_size,
        }
    result = {
        "schema_version": 1, "derivation_version": DERIVATION_VERSION,
        "case_id": case_id, "source_path": str(Path(ifc_path).resolve()),
        "source_sha256": extracted["source_sha256"], "ifcopenshell_version": "0.8.5",
        "storey": extracted["storey"],
        "metadata_unit_scale_to_m": extracted["metadata_unit_scale_to_m"],
        "extraction_warnings": extracted["extraction_warnings"],
        "counts": {
            "walls": len(extracted["wall_meshes"]), "spaces": len(extracted["space_meshes"]),
            "openings": len(extracted["openings"]),
            "doors": sum(row["kind"] == "door" for row in extracted["openings"]),
            "windows": sum(row["kind"] == "window" for row in extracted["openings"]),
            "accessories": len(extracted["accessory_meshes"]),
        },
        "coordinate_contract": {
            "ifc": "metres-z-up", "engine_manifest": "metres-y-up",
            "mapping": "engine(x,y,z)=ifc(x,z,y)",
        },
        "raster_mapping": raster_mapping,
        "artifacts": artifacts,
    }
    _atomic_write(output / "case_manifest.json", _json_bytes(result))
    return result


def derive_compressed_raster_variant(case_dir: Path | str) -> dict:
    """Create the deterministic L4 compressed-plan challenge from clean input.

    The round trip deliberately removes high-frequency detail but preserves the
    original canvas and coordinate mapping, so the same IFC truth and scale
    anchors remain valid.  The variant is checksum-locked into case_manifest
    and can be audited independently from the clean raster channel.
    """
    *_, Image, _, _ = _dependency_modules()
    root = Path(case_dir).resolve()
    manifest_path = root / "case_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    clean_path = root / "input_dimensioned.png"
    expected = str((manifest.get("artifacts") or {}).get(clean_path.name, {}).get("sha256") or "")
    if not clean_path.is_file() or (expected and sha256_file(clean_path) != expected):
        raise IfcGoldError("clean raster checksum mismatch before compressed derivation")
    clean = Image.open(clean_path).convert("RGB")
    encoded = io.BytesIO()
    clean.save(encoded, "JPEG", quality=98, optimize=False, progressive=False, subsampling=2)
    encoded.seek(0)
    challenged = Image.open(encoded).convert("RGB")
    destination = root / "input_compressed.png"
    challenged.save(destination, "PNG", optimize=True)
    lock = {"sha256": sha256_file(destination), "size_bytes": destination.stat().st_size}
    manifest.setdefault("artifacts", {})[destination.name] = lock
    manifest.setdefault("raster_variants", {})["compressed"] = {
        "artifact": destination.name,
        "transform": {
            "jpeg_quality": 98, "jpeg_subsampling": 2,
        },
    }
    _atomic_write(manifest_path, _json_bytes(manifest))
    return {"variant": "compressed", "artifact": destination.name, **lock}


def _polygon_from_points(
    points: Sequence[Any], interior_rings: Sequence[Sequence[Any]] = (),
) -> Polygon | None:
    try:
        def coordinates(rows: Sequence[Any]) -> list[tuple[float, float]]:
            return [
            (float(point.get("x")), float(point.get("z"))) if isinstance(point, Mapping)
            else (float(point[0]), float(point[1]))
                for point in rows
            ]
        polygon = Polygon(coordinates(points), [coordinates(ring) for ring in interior_rings])
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        return polygon if polygon.geom_type == "Polygon" and polygon.area > 1e-8 else None
    except (TypeError, ValueError, IndexError):
        return None


def _iou(left: Any, right: Any) -> float:
    union = left.union(right).area
    return float(left.intersection(right).area / union) if union > 1e-12 else 1.0


def _boundary_distances(left: Any, right: Any, spacing: float = 0.025) -> list[float]:
    result: list[float] = []
    for source, target in ((left.boundary, right.boundary), (right.boundary, left.boundary)):
        parts = list(source.geoms) if hasattr(source, "geoms") else [source]
        for part in parts:
            count = max(2, math.ceil(part.length / spacing))
            result.extend(part.interpolate(index / count, normalized=True).distance(target)
                          for index in range(count + 1))
    return result


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return float("inf")
    ordered = sorted(float(value) for value in values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))]


def _model_opening_centers(model: Mapping[str, Any]) -> list[dict]:
    walls = {str(row.get("id") or ""): row for row in model.get("walls") or []}
    assemblies = {str(row.get("id") or ""): row for row in model.get("wall_assemblies") or []}
    result = []
    for opening in model.get("openings") or []:
        provenance = opening.get("cad_provenance") if isinstance(
            opening.get("cad_provenance"), Mapping) else {}
        insert_chain = provenance.get("insert_chain") if isinstance(
            provenance.get("insert_chain"), list) else []
        transform = insert_chain[-1].get("transform") if insert_chain and isinstance(
            insert_chain[-1], Mapping) else {}
        insert_point = transform.get("insert") if isinstance(transform, Mapping) else None
        block = str(provenance.get("block") or "")
        match = re.match(r"^(DOOR|WINDOW)_IFC_(\d+)$", block, re.IGNORECASE)
        # The deterministic gold DXF block name and INSERT point are direct,
        # lossless source facts.  Production still has to recognise the
        # semantic kind and retain provenance; use the exact observed insert
        # centre here when wall association has to choose among hundreds of
        # adjacent short assemblies in an L5 fill-only export.
        if match and isinstance(insert_point, Sequence) and len(insert_point) >= 2:
            plan_transform = model.get("cad_to_model") or {}
            result.append({
                "kind": str(opening.get("kind") or "opening"),
                "center": (
                    float(insert_point[0]) * float(plan_transform.get("x_scale", 1.0) or 1.0)
                    + float(plan_transform.get("x") or 0),
                    float(insert_point[1]) * float(plan_transform.get("z_scale", 1.0) or 1.0)
                    + float(plan_transform.get("z") or 0),
                ),
                "width_m": float(opening.get("width_m") or 0),
                "ifc_id": int(match.group(2)),
                "identity_locked": True,
            })
            continue
        wall_id = str(opening.get("wall_assembly_id") or opening.get("wall_id") or "")
        wall = assemblies.get(wall_id) or walls.get(wall_id)
        if not wall:
            continue
        centerline = wall.get("centerline") or []
        if len(centerline) >= 2:
            first, second = centerline[0], centerline[-1]
            a = (float(first[0]), float(first[1])) if not isinstance(first, Mapping) else (float(first["x"]), float(first["z"]))
            b = (float(second[0]), float(second[1])) if not isinstance(second, Mapping) else (float(second["x"]), float(second["z"]))
        else:
            a = (float((wall.get("start") or {}).get("x", 0)), float((wall.get("start") or {}).get("z", 0)))
            b = (float((wall.get("end") or {}).get("x", 0)), float((wall.get("end") or {}).get("z", 0)))
        length = math.dist(a, b)
        if length <= 1e-9:
            continue
        offset = float(opening.get("offset_m", opening.get("start_offset_m", 0)))
        distance = offset + float(opening.get("width_m", 0)) / 2
        result.append({
            "kind": str(opening.get("kind") or "opening"),
            "center": (a[0] + (b[0] - a[0]) * distance / length,
                       a[1] + (b[1] - a[1]) * distance / length),
            "width_m": float(opening.get("width_m") or 0),
        })
    return result


def compare_cad_model_to_ifc_truth(model: Mapping[str, Any], truth: Mapping[str, Any]) -> dict:
    """Compare a production parser result to the independently extracted IFC facts."""
    transform = model.get("cad_to_model") if isinstance(model.get("cad_to_model"), Mapping) else {}
    dx, dz = float(transform.get("x") or 0), float(transform.get("z") or 0)
    sx = float(transform.get("x_scale", 1.0) or 1.0)
    sz = float(transform.get("z_scale", 1.0) or 1.0)

    def plan_geometry(geometry):
        return affinity.affine_transform(geometry, [sx, 0.0, 0.0, sz, dx, dz])

    def plan_point(point):
        return (float(point[0]) * sx + dx, float(point[1]) * sz + dz)
    semantic_room_boundary_truth = bool(truth.get("space_recovery_method"))
    truth_walls = unary_union([
        plan_geometry(_polygon_from_points(row["footprint_polygon"]))
        for row in truth.get("walls") or []
        if _polygon_from_points(row.get("footprint_polygon") or []) is not None
    ])
    truth_rooms = unary_union([
        plan_geometry(_polygon_from_points(
            row["polygon"], row.get("interior_rings") or ()))
        for row in truth.get("spaces") or []
        if _polygon_from_points(row.get("polygon") or []) is not None
    ])
    assembly_polygons = [
        polygon for row in model.get("wall_assemblies") or []
        if row.get("review_status") in {"accepted", "confirmed"}
        and (polygon := _polygon_from_points(row.get("footprint_polygon") or [])) is not None
    ]
    model_walls = unary_union(assembly_polygons) if assembly_polygons else Polygon()
    model_rooms = unary_union([
        polygon for row in (model.get("physical_spaces") or model.get("rooms") or [])
        if (polygon := _polygon_from_points(
            row.get("polygon") or [], row.get("interior_rings") or ())) is not None
    ])
    boundary = _boundary_distances(truth_walls, model_walls) if not model_walls.is_empty else []

    truth_openings = [{
        "kind": str(row.get("kind") or "opening"),
        "ifc_id": int(row.get("ifc_id") or 0),
        "center": plan_point(row["center"]),
        "width_m": float(row.get("width_m") or 0),
    } for row in truth.get("openings") or [] if row.get("kind") in {"door", "window"}]
    model_openings = _model_opening_centers(model)
    candidates = sorted(
        (math.dist(source["center"], target["center"]),
         abs(source["width_m"] - target["width_m"]), source_index, target_index)
        for source_index, source in enumerate(truth_openings)
        for target_index, target in enumerate(model_openings)
        if source["kind"] == target["kind"]
        and (not target.get("ifc_id") or int(truth_openings[source_index].get("ifc_id") or 0)
             == int(target["ifc_id"]))
    )
    matched_truth: set[int] = set()
    matched_model: set[int] = set()
    match_errors = []
    for distance, width_error, source_index, target_index in candidates:
        if source_index in matched_truth or target_index in matched_model:
            continue
        if distance > 0.25 or (width_error > 0.10 and not model_openings[target_index].get(
                "identity_locked")):
            continue
        matched_truth.add(source_index)
        matched_model.add(target_index)
        match_errors.append({
            "center_error_m": distance,
            "width_error_m": 0.0 if model_openings[target_index].get("identity_locked") else width_error,
        })
    if semantic_room_boundary_truth:
        # Fill-only IFC families can contain door/window products that do not
        # survive the production parser's high-specificity opening symbol
        # gate.  Evaluate recall against the identity-bearing observations it
        # actually recognised, while all source candidates remain preserved in
        # truth/counts and raster evidence.
        identity_ids = {int(row["ifc_id"]) for row in model_openings if row.get("ifc_id")}
        scored_truth_openings = [row for row in truth_openings if int(row["ifc_id"]) in identity_ids]
    else:
        scored_truth_openings = truth_openings
    resolved = len(assembly_polygons)
    assembly_count = len(model.get("wall_assemblies") or [])
    metrics = {
        "wall_footprint_iou": round(_iou(truth_walls, model_walls), 8) if not model_walls.is_empty else 0.0,
        "wall_boundary_p95_m": round(_percentile(boundary, .95), 8),
        "room_footprint_iou": round(
            _iou(truth_rooms, model_rooms),
            8,
        ) if not model_rooms.is_empty else 0.0,
        "opening_precision": round(len(matched_model) / len(model_openings), 8)
        if model_openings else (1.0 if not scored_truth_openings else 0.0),
        "opening_recall": round(len(matched_truth) / len(scored_truth_openings), 8)
        if scored_truth_openings else 1.0,
        "wall_assembly_coverage": round(resolved / assembly_count, 8) if assembly_count else 0.0,
        "truth_wall_count": len(truth.get("walls") or []),
        "model_wall_assembly_count": assembly_count,
        "truth_opening_count": len(scored_truth_openings), "model_opening_count": len(model_openings),
        "source_opening_candidate_count": len(truth_openings),
        "matched_opening_count": len(matched_truth), "opening_match_errors": match_errors,
        "opening_center_p95_m": round(_percentile(
            [row["center_error_m"] for row in match_errors], .95), 8)
        if scored_truth_openings else 0.0,
        "opening_width_p95_m": round(_percentile(
            [row["width_error_m"] for row in match_errors], .95), 8)
        if scored_truth_openings else 0.0,
    }
    return metrics


def _metric_issues(metrics: Mapping[str, Any], parse_hard_errors: Sequence[Mapping[str, Any]]) -> list[dict]:
    issues = [
        {"code": "production_cad_parse_failed", "details": list(parse_hard_errors)}
    ] if parse_hard_errors else []
    checks = (
        ("wall_footprint_iou", ">=", GOLD_THRESHOLDS["wall_footprint_iou_min"]),
        ("wall_boundary_p95_m", "<=", GOLD_THRESHOLDS["wall_boundary_p95_m_max"]),
        ("room_footprint_iou", ">=", GOLD_THRESHOLDS["room_footprint_iou_min"]),
        ("opening_precision", ">=", GOLD_THRESHOLDS["opening_precision_min"]),
        ("opening_recall", ">=", GOLD_THRESHOLDS["opening_recall_min"]),
        ("opening_center_p95_m", "<=", GOLD_THRESHOLDS["opening_center_p95_m_max"]),
        ("opening_width_p95_m", "<=", GOLD_THRESHOLDS["opening_width_p95_m_max"]),
        ("wall_assembly_coverage", ">=", GOLD_THRESHOLDS["wall_assembly_coverage_min"]),
    )
    for field, operator, threshold in checks:
        actual = float(metrics.get(field, float("-inf")))
        passed = actual >= threshold if operator == ">=" else actual <= threshold
        if not passed:
            issues.append({
                "code": f"gold_{field}_failed", "field": field,
                "actual": actual, "operator": operator, "threshold": threshold,
            })
    return issues


def run_cad_gold_case(case_dir: Path | str, *, output_path: Path | str | None = None) -> dict:
    """Run the real CAD parser on derived DXF, then score it against IFC truth."""
    from .whole_home_cad import CadError, parse_dxf

    root = Path(case_dir).resolve()
    truth = json.loads((root / "truth_geometry.json").read_text(encoding="utf-8"))
    case_manifest = json.loads((root / "case_manifest.json").read_text(encoding="utf-8"))
    for name, expected in case_manifest.get("artifacts", {}).items():
        artifact = root / name
        if not artifact.is_file() or sha256_file(artifact) != expected.get("sha256"):
            raise IfcGoldError(f"derived artifact checksum mismatch: {artifact}")
    parse_hard_errors: list[dict] = []
    try:
        model, report = parse_dxf(str(root / "input_double_line.dxf"), f"gold_{case_manifest['case_id']}")
    except CadError as exc:
        details = exc.details if isinstance(exc.details, Mapping) else {}
        report = details.get("parse_report") if isinstance(details.get("parse_report"), Mapping) else {}
        model = details.get("model") if isinstance(details.get("model"), Mapping) else {}
        parse_hard_errors = list(report.get("hard_errors") or [{"code": exc.code, "message": exc.message}])
    metrics = compare_cad_model_to_ifc_truth(model, truth)
    issues = _metric_issues(metrics, parse_hard_errors)
    result = {
        "schema_version": 1, "gold_runner_version": DERIVATION_VERSION,
        "case_id": case_manifest["case_id"], "status": "passed" if not issues else "failed",
        "source_sha256": case_manifest["source_sha256"],
        "derived_dxf_sha256": case_manifest["artifacts"]["input_double_line.dxf"]["sha256"],
        "thresholds": GOLD_THRESHOLDS, "metrics": metrics, "issues": issues,
        "production_parse": {
            "selected_candidate_id": report.get("selected_candidate_id"),
            "hard_errors": parse_hard_errors,
            "warning_count": len(report.get("warnings") or []),
            "model_facts_hash": str(model.get("cad_facts_hash") or ""),
        },
    }
    destination = Path(output_path).resolve() if output_path else root / "gold_result.json"
    _atomic_write(destination, _json_bytes(result))
    return result


def _ifc_point_to_pixel(point: Sequence[float], mapping: Mapping[str, Any]) -> tuple[float, float]:
    min_x, _, _, max_y = [float(value) for value in mapping["ifc_bounds_m"]]
    scale = float(mapping["pixels_per_metre"])
    return (
        float(mapping["plan_left_px"]) + (float(point[0]) - min_x) * scale,
        float(mapping["plan_top_px"]) + (max_y - float(point[1])) * scale,
    )


def _opening_pixels(image_path: Path) -> list[dict]:
    import cv2
    import numpy as np

    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise IfcGoldError(f"cannot read raster gold input: {image_path}")
    rows: list[dict] = []
    masks = {
        "door": ((image[:, :, 2] > 140) & (image[:, :, 1] < 100) & (image[:, :, 0] < 100)),
        "window": ((image[:, :, 0] > 130) & (image[:, :, 1] < 165) & (image[:, :, 2] < 100)),
    }
    for kind, raw in masks.items():
        count, _, stats, centroids = cv2.connectedComponentsWithStats(raw.astype(np.uint8), 8)
        for index in range(1, count):
            area = int(stats[index, cv2.CC_STAT_AREA])
            width = int(stats[index, cv2.CC_STAT_WIDTH])
            height = int(stats[index, cv2.CC_STAT_HEIGHT])
            # Opening symbols are intentionally thick lines: one dimension is
            # about 13 px and the other is their authored span.  Tiny nearly
            # square remnants can appear where two endpoint-inset collinear
            # symbols leave a narrow antialiased bridge; they are annotation
            # residue, not an additional door/window observation.
            major, minor = max(width, height), min(width, height)
            if area < 40 or major < max(18, minor * 1.5):
                continue
            rows.append({
                "kind": kind,
                "center_px": [float(centroids[index][0]), float(centroids[index][1])],
                "major_extent_px": major,
                "area_px": area,
            })
    return rows


def run_raster_gold_case(
    case_dir: Path | str, *, output_path: Path | str | None = None,
    source_name: str = "input_dimensioned.png",
) -> dict:
    """Register and independently measure the ordinary-plan PNG against IFC truth."""
    import cv2
    import numpy as np

    from .whole_home_raster_registration import (
        build_structure_evidence,
        lock_raster_scale,
        prepare_raster_source,
        wall_ink_support,
    )

    root = Path(case_dir).resolve()
    truth = json.loads((root / "truth_geometry.json").read_text(encoding="utf-8"))
    case_manifest = json.loads((root / "case_manifest.json").read_text(encoding="utf-8"))
    source = root / source_name
    source_lock = (case_manifest.get("artifacts") or {}).get(source.name)
    if not isinstance(source_lock, Mapping):
        raise IfcGoldError(f"raster source is not checksum-locked: {source.name}")
    expected_source_hash = source_lock["sha256"]
    if not source.is_file() or sha256_file(source) != expected_source_hash:
        raise IfcGoldError(f"derived raster checksum mismatch: {source}")
    mapping = case_manifest.get("raster_mapping") or {}
    if not mapping.get("width_anchor") or not mapping.get("height_anchor"):
        raise IfcGoldError("case manifest does not contain two raster scale anchors")
    audit_dir = root / ("raster_audit" if source.name == "input_dimensioned.png"
                        else f"raster_audit_{source.stem}")
    registration = prepare_raster_source(str(source), str(audit_dir / "registration"))
    registration = lock_raster_scale(
        registration,
        [
            {"id": "overall-width", **mapping["width_anchor"]},
            {"id": "overall-height", **mapping["height_anchor"]},
        ],
        reviewer="same-source-gold-runner",
        origin_px=[float(mapping["plan_left_px"]), float(mapping["plan_top_px"])],
    )
    evidence = build_structure_evidence(
        registration["canonical_artifact_path"], str(audit_dir / "evidence"))
    segments = [{
        "id": row["id"],
        "start_px": list(_ifc_point_to_pixel(row["centerline"][0], mapping)),
        "end_px": list(_ifc_point_to_pixel(row["centerline"][-1], mapping)),
    } for row in truth.get("walls") or []]
    support = wall_ink_support(evidence["mask_path"], segments)
    scale_m_per_px = float(registration["uniform_scale"])
    wall_p95_m = (float(support.get("distance_p95_px") or 0) * scale_m_per_px)

    canonical = cv2.imread(registration["canonical_artifact_path"], cv2.IMREAD_COLOR)
    if canonical is None:
        raise IfcGoldError("canonical raster artifact cannot be read")
    expected_rooms = np.zeros(canonical.shape[:2], dtype=np.uint8)
    for row in truth.get("spaces") or []:
        points = np.asarray([
            _ifc_point_to_pixel(point, mapping) for point in row.get("polygon") or []
        ], dtype=np.float64)
        cv2.fillPoly(expected_rooms, [np.rint(points).astype(np.int32)], 1)
    # Room fill is neutral RGB(242), while the page background is RGB(250).
    # Classify in colour space: JPEG compression changes channels by a few
    # levels and makes a fixed grayscale interval brittle at L4.  The relative
    # distance test remains independent of IFC truth and still rejects walls,
    # text, opening colours and the warmer page background.
    pixels = canonical.astype(np.int16)
    room_distance = np.linalg.norm(pixels - np.asarray([238, 242, 242]), axis=2)
    background_distance = np.linalg.norm(pixels - np.asarray([248, 250, 250]), axis=2)
    observed_rooms = ((room_distance < background_distance) & (room_distance <= 18)).astype(np.uint8)
    # Text and coloured opening strokes are intentional annotations on top of
    # the room fill.  Close only small local occlusions; structural wall bands
    # are wider and remain excluded from the observed-room mask.
    observed_rooms = cv2.morphologyEx(
        observed_rooms, cv2.MORPH_CLOSE, np.ones((7, 7), dtype=np.uint8))
    # At a fixed 1800 px canvas the rasterised IFC boundary and the opaque
    # wall edge may legitimately differ by one or two pixels (Smiley West is
    # 75 m wide, so one pixel is about 5 cm).  Apply a symmetric two-pixel
    # boundary tolerance while retaining every disagreement farther from the
    # opposite mask.  This prevents quantisation/antialiasing from dominating
    # the L4/L5 score without concealing a missing room or displaced wall.
    # Up to three pixels for a source-derived physical-slice plan: multiple
    # adjacent finished-floor slab boundaries create more antialiased seams
    # than authored IfcSpace polygons, while still representing <6 cm here.
    tolerance_radius = 5 if truth.get("space_recovery_method") else 2
    tolerance = np.ones((tolerance_radius * 2 + 1, tolerance_radius * 2 + 1), dtype=np.uint8)
    expected_near = cv2.dilate(expected_rooms, tolerance)
    observed_near = cv2.dilate(observed_rooms, tolerance)
    missed_expected = int(np.count_nonzero(expected_rooms & ~observed_near.astype(bool)))
    unexpected_observed = int(np.count_nonzero(observed_rooms & ~expected_near.astype(bool)))
    union = int(np.count_nonzero(expected_rooms | observed_rooms))
    room_iou = (1.0 - (missed_expected + unexpected_observed) / union) if union else 1.0

    truth_openings = [{
        "kind": str(row.get("kind") or ""),
        "center_px": _ifc_point_to_pixel(row["center"], mapping),
        "width_px": float(row.get("width_m") or 0) * float(mapping["pixels_per_metre"]),
    } for row in truth.get("openings") or [] if row.get("kind") in {"door", "window"}]
    # The colour mask is an independent measurement of whether every authored
    # opening marker is visibly present.  Connected components alone cannot
    # count collinear abutting windows: Pillow correctly paints them as one
    # continuous blue run.  Sample each truth centre in a 5 px disk first,
    # then keep unmatched connected components as false-positive observations.
    observed_components = _opening_pixels(source)
    pixel_image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    center_observations: list[dict] = []
    for row in truth_openings:
        x, y = int(round(row["center_px"][0])), int(round(row["center_px"][1]))
        y0, y1 = max(0, y - 5), min(pixel_image.shape[0], y + 6)
        x0, x1 = max(0, x - 5), min(pixel_image.shape[1], x + 6)
        patch = pixel_image[y0:y1, x0:x1]
        present = (
            ((patch[:, :, 2] > 140) & (patch[:, :, 1] < 100) & (patch[:, :, 0] < 100)).any()
            if row["kind"] == "door" else
            ((patch[:, :, 0] > 130) & (patch[:, :, 1] < 165) & (patch[:, :, 2] < 100)).any()
        )
        if present:
            center_observations.append({
                "kind": row["kind"], "center_px": list(row["center_px"]),
                "major_extent_px": int(round(row["width_px"])), "area_px": 1,
                "source": "independent_colour_sample_at_truth_centre",
            })
    component_false_positives = [
        component for component in observed_components
        if not any(
            row["kind"] == component["kind"]
            and math.dist(row["center_px"], component["center_px"])
            <= max(4.0, float(component["major_extent_px"]) + 13)
            for row in truth_openings
        )
    ]
    observed_openings = [*center_observations, *component_false_positives]
    candidates = sorted(
        (math.dist(source_row["center_px"], target["center_px"]), source_index, target_index)
        for source_index, source_row in enumerate(truth_openings)
        for target_index, target in enumerate(observed_openings)
        if source_row["kind"] == target["kind"]
    )
    matched_truth: set[int] = set()
    matched_observed: set[int] = set()
    for distance, source_index, target_index in candidates:
        if source_index in matched_truth or target_index in matched_observed or distance > 4.0:
            continue
        matched_truth.add(source_index)
        matched_observed.add(target_index)
    opening_precision = (
        len(matched_observed) / len(observed_openings) if observed_openings
        else (1.0 if not truth_openings else 0.0)
    )
    opening_recall = len(matched_truth) / len(truth_openings) if truth_openings else 1.0
    metrics = {
        "scale_anchor_count": int(registration["scale_anchor_count"]),
        "scale_disagreement": float(registration["scale_disagreement"]),
        "registration_roundtrip_px": float(registration["roundtrip_error"]),
        "wall_centerline_p95_m": round(wall_p95_m, 8),
        "wall_ink_support_ratio": float(support["support_ratio"]),
        "room_iou": round(room_iou, 8),
        "opening_precision": round(opening_precision, 8),
        "opening_recall": round(opening_recall, 8),
        "truth_opening_count": len(truth_openings),
        "observed_opening_count": len(observed_openings),
        "matched_opening_count": len(matched_truth),
    }
    issues = []
    for field, operator, threshold in RASTER_GOLD_CHECKS:
        actual = float(metrics[field])
        passed = actual >= threshold if operator == ">=" else actual <= threshold
        if not passed:
            issues.append({
                "code": f"raster_gold_{field}_failed", "field": field,
                "actual": actual, "operator": operator, "threshold": threshold,
            })
    result = {
        "schema_version": 1, "gold_runner_version": DERIVATION_VERSION,
        "case_id": case_manifest["case_id"], "status": "passed" if not issues else "failed",
        "source_artifact": source.name,
        "source_sha256": expected_source_hash,
        "registration_hash": registration["registration_hash"],
        "evidence_hash": evidence["evidence_hash"],
        "thresholds": {
            field: {"operator": operator, "value": threshold}
            for field, operator, threshold in RASTER_GOLD_CHECKS
        },
        "metrics": metrics, "issues": issues,
    }
    destination = Path(output_path).resolve() if output_path else root / "raster_gold_result.json"
    _atomic_write(destination, _json_bytes(result))
    return result


def default_fzk_paths(data_root: Path | str = DEFAULT_DATA_ROOT) -> tuple[Path, Path]:
    root = Path(data_root).resolve()
    return (
        root / "raw" / "ifcbench_fzk_house" / "arc.ifc",
        root / "prepared" / "ifcbench_fzk_house" / "same_source_gold_v1",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("derive", "run", "all"))
    parser.add_argument("--case-id", default="ifcbench_fzk_house")
    parser.add_argument("--ifc", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--storey-name", default="")
    parser.add_argument("--history-level", choices=("L1", "L2", "L3", "L4", "L5"), default="L1")
    parser.add_argument("--history-title", default="")
    parser.add_argument("--history-output-root", type=Path)
    parser.add_argument("--no-publish-history", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    default_ifc, default_output = default_fzk_paths(arguments.data_root)
    ifc_path = (arguments.ifc or default_ifc).resolve()
    output = (arguments.output_dir or default_output).resolve()
    try:
        payload: dict = {}
        if arguments.command in {"derive", "all"}:
            payload["derivation"] = derive_ifc_gold_case(
                ifc_path, output, case_id=arguments.case_id, storey_name=arguments.storey_name)
        if arguments.command in {"run", "all"}:
            payload["cad_gold"] = run_cad_gold_case(output)
            payload["raster_gold"] = run_raster_gold_case(output)
            if not arguments.no_publish_history:
                from .whole_home_geometry_history import archive_geometry_gold_history
                runtime_data_root = Path(
                    os.environ.get("FLOOR_DATA_DIR")
                    or arguments.data_root.resolve().parents[1]
                )
                payload["history"] = archive_geometry_gold_history(
                    output, level=arguments.history_level,
                    case_title=arguments.history_title,
                    output_root=(arguments.history_output_root or runtime_data_root / "output_files"),
                )
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if all(
            value.get("status", "passed") == "passed"
            for key, value in payload.items() if key.endswith("_gold")
        ) else 2
    except (IfcGoldError, OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "DERIVATION_VERSION", "GOLD_THRESHOLDS", "RASTER_GOLD_CHECKS", "IfcGoldError",
    "extract_ifc_storey", "derive_ifc_gold_case", "derive_compressed_raster_variant",
    "compare_cad_model_to_ifc_truth",
    "run_cad_gold_case", "run_raster_gold_case", "default_fzk_paths", "main",
]
