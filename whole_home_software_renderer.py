from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass
from typing import Iterable

import cv2
import numpy as np
from PIL import Image

try:
    from .whole_home_reference_camera import evaluate_subject_id_pixels, split_reference_contract
    from .whole_home_geometry_kernel import manifest_triangles
except ImportError:  # pragma: no cover - standalone development import
    from whole_home_reference_camera import evaluate_subject_id_pixels, split_reference_contract
    from whole_home_geometry_kernel import manifest_triangles


SEMANTIC_COLORS = {
    'wall': (216, 212, 202), 'floor': (188, 182, 170),
    'kitchen_run': (217, 119, 6), 'sink': (14, 165, 233),
    'hob': (239, 68, 68), 'fridge': (100, 116, 139),
    'basin': (6, 182, 212), 'toilet': (248, 250, 252),
    'shower_zone': (103, 232, 249), 'bed': (139, 92, 246),
    'wardrobe': (161, 98, 7), 'sofa': (236, 72, 153),
    'tv': (17, 24, 39), 'dining_table': (132, 204, 22),
    'entry_storage': (245, 158, 11), 'balcony_rail': (34, 197, 94),
    'washing_machine': (59, 130, 246), 'other': (148, 163, 184),
}

RGB_COLORS = {
    'wall': (218, 215, 207), 'floor': (205, 198, 184),
    'kitchen_run': (145, 116, 84), 'sink': (164, 183, 188),
    'hob': (54, 57, 61), 'fridge': (104, 108, 112),
    'basin': (211, 220, 219), 'toilet': (224, 226, 224),
    'shower_zone': (151, 179, 184), 'bed': (151, 137, 125),
    'wardrobe': (119, 101, 83), 'sofa': (135, 121, 111),
    'tv': (43, 45, 48), 'dining_table': (120, 99, 78),
    'entry_storage': (128, 103, 78), 'balcony_rail': (92, 100, 93),
    'washing_machine': (174, 179, 180), 'other': (140, 145, 145),
}


@dataclass
class Triangle:
    points: np.ndarray
    normal: np.ndarray
    role: str
    anchor_id: str = ''
    subject_only: bool = False


def _normalized(value: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(value))
    return value / length if length > 1e-9 else value


def _box_triangles(center: tuple[float, float, float], size: tuple[float, float, float],
                   rotation_y_deg: float, role: str, anchor_id: str = '',
                   subject_only: bool = False) -> list[Triangle]:
    sx, sy, sz = (max(.001, float(item)) for item in size)
    cx, cy, cz = center
    vertices = np.array([
        [-sx / 2, -sy / 2, -sz / 2], [sx / 2, -sy / 2, -sz / 2],
        [sx / 2, sy / 2, -sz / 2], [-sx / 2, sy / 2, -sz / 2],
        [-sx / 2, -sy / 2, sz / 2], [sx / 2, -sy / 2, sz / 2],
        [sx / 2, sy / 2, sz / 2], [-sx / 2, sy / 2, sz / 2],
    ], dtype=np.float64)
    angle = math.radians(float(rotation_y_deg or 0))
    rotation = np.array([
        [math.cos(angle), 0, math.sin(angle)],
        [0, 1, 0],
        [-math.sin(angle), 0, math.cos(angle)],
    ], dtype=np.float64)
    vertices = vertices @ rotation.T + np.array([cx, cy, cz], dtype=np.float64)
    faces = [
        (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4), (3, 7, 6), (3, 6, 2),
        (0, 4, 7), (0, 7, 3), (1, 2, 6), (1, 6, 5),
    ]
    rows = []
    for face in faces:
        points = vertices[list(face)]
        normal = _normalized(np.cross(points[1] - points[0], points[2] - points[0]))
        rows.append(Triangle(points, normal, role, anchor_id, subject_only))
    return rows


def _wall_parts(wall: dict, openings: Iterable[dict]):
    start, end = wall.get('start') or {}, wall.get('end') or {}
    dx = float(end.get('x') or 0) - float(start.get('x') or 0)
    dz = float(end.get('z') or 0) - float(start.get('z') or 0)
    length = math.hypot(dx, dz)
    if length <= .01:
        return
    cuts = sorted([
        row for row in openings
        if row.get('wall_id') == wall.get('id') and row.get('review_status') != 'rejected'
        and float(row.get('width_m') or 0) > .02
    ], key=lambda row: float(row.get('offset_m') or 0))
    height = max(.1, float(wall.get('height_m') or 2.8))
    cursor = 0.0
    for opening in cuts:
        left = max(cursor, min(length, float(opening.get('offset_m') or 0)))
        right = max(left, min(length, left + float(opening.get('width_m') or 0)))
        if left > cursor + .005:
            yield cursor, left, 0.0, height
        sill = max(0.0, float(opening.get('sill_height_m') or 0))
        top = min(height, sill + max(.05, float(opening.get('height_m') or 2.1)))
        if sill > .02:
            yield left, right, 0.0, sill
        if top < height - .02:
            yield left, right, top, height
        cursor = max(cursor, right)
    if cursor < length - .005:
        yield cursor, length, 0.0, height


def _polygon_triangles(points: list[dict], y: float, normal_y: float, role: str):
    if len(points) < 3:
        return []
    try:
        from shapely.geometry import Polygon
        from shapely.ops import triangulate

        polygon = Polygon([(float(row.get('x') or 0), float(row.get('z') or 0)) for row in points])
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        rows = []
        for item in triangulate(polygon):
            if not polygon.covers(item.representative_point()):
                continue
            coordinates = list(item.exterior.coords)[:3]
            vertices = np.array([[x, y, z] for x, z in coordinates], dtype=np.float64)
            if normal_y > 0:
                vertices = vertices[[0, 2, 1]]
            normal = np.array([0.0, normal_y, 0.0], dtype=np.float64)
            rows.append(Triangle(vertices, normal, role))
        return rows
    except Exception:
        origin = points[0]
        rows = []
        for index in range(1, len(points) - 1):
            vertices = np.array([
                [float(origin.get('x') or 0), y, float(origin.get('z') or 0)],
                [float(points[index].get('x') or 0), y, float(points[index].get('z') or 0)],
                [float(points[index + 1].get('x') or 0), y, float(points[index + 1].get('z') or 0)],
            ], dtype=np.float64)
            if normal_y > 0:
                vertices = vertices[[0, 2, 1]]
            rows.append(Triangle(vertices, np.array([0.0, normal_y, 0.0]), role))
        return rows


def _footprint_prism_triangles(footprint: dict, default_height: float) -> list[Triangle]:
    points = footprint.get('points') or []
    if len(points) < 3:
        return []
    try:
        from shapely.geometry import Polygon
        from shapely.ops import triangulate

        exterior = [(float(row.get('x') or 0), float(row.get('z') or 0))
                    for row in points]
        holes = [[(float(row.get('x') or 0), float(row.get('z') or 0))
                  for row in ring]
                 for ring in footprint.get('interior_rings') or [] if len(ring) >= 3]
        polygon = Polygon(exterior, holes=holes)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.geom_type != 'Polygon' or polygon.is_empty:
            return []
        bottom = float(footprint.get('floor_elevation_m') or 0)
        top = bottom + max(.1, float(footprint.get('height_m') or default_height))
        rows: list[Triangle] = []
        for item in triangulate(polygon):
            if not polygon.covers(item.representative_point()):
                continue
            coordinates = list(item.exterior.coords)[:3]
            lower = np.array([[x, bottom, z] for x, z in coordinates], dtype=np.float64)
            upper = np.array([[x, top, z] for x, z in coordinates], dtype=np.float64)
            rows.append(Triangle(lower, np.array([0.0, -1.0, 0.0]), 'wall'))
            rows.append(Triangle(upper[[0, 2, 1]], np.array([0.0, 1.0, 0.0]), 'wall'))
        for ring in [polygon.exterior, *polygon.interiors]:
            coordinates = list(ring.coords)
            for first, second in zip(coordinates, coordinates[1:]):
                x0, z0 = first
                x1, z1 = second
                for vertices in (
                    np.array([[x0, bottom, z0], [x1, bottom, z1], [x1, top, z1]], dtype=np.float64),
                    np.array([[x0, bottom, z0], [x1, top, z1], [x0, top, z0]], dtype=np.float64),
                ):
                    normal = _normalized(np.cross(vertices[1] - vertices[0], vertices[2] - vertices[0]))
                    rows.append(Triangle(vertices, normal, 'wall'))
        return rows
    except Exception:
        return []


def build_scene_triangles(model: dict, subjects: list[dict] | None = None) -> list[Triangle]:
    locked_manifest = model.get('geometry_manifest')
    triangles: list[Triangle] = []
    if isinstance(locked_manifest, dict) and locked_manifest.get('manifest_hash'):
        triangles = [
            Triangle(
                points=row['points'], normal=row['normal'], role=(
                    row['role'] if row['role'] in SEMANTIC_COLORS else 'other'),
                anchor_id=row['anchor_id'], subject_only=False,
            )
            for row in manifest_triangles(locked_manifest)
        ]
    openings = model.get('openings') or []
    if not triangles:
        default_height = max(.5, float(
            model.get('wall_height_m') or model.get('default_wall_height_m') or 2.8))
        floor_spaces = model.get('physical_spaces') or model.get('rooms') or []
        for room in floor_spaces:
            polygon = room.get('polygon') or []
            triangles.extend(_polygon_triangles(polygon, 0.0, 1.0, 'floor'))
            triangles.extend(_polygon_triangles(polygon, default_height, -1.0, 'wall'))
        global_footprints = [
            row for row in model.get('global_wall_footprints') or []
            if isinstance(row, dict) and len(row.get('points') or []) >= 3
        ]
        if global_footprints:
            for footprint in global_footprints:
                triangles.extend(_footprint_prism_triangles(footprint, default_height))
            walls = {str(row.get('id') or ''): row for row in model.get('walls') or []}
            assembly_walls = {
                str(row.get('wall_assembly_id') or ''): row
                for row in model.get('walls') or [] if row.get('wall_assembly_id')
            }
            for opening in openings:
                if opening.get('review_status') == 'rejected':
                    continue
                wall = walls.get(str(opening.get('wall_id') or '')) or assembly_walls.get(
                    str(opening.get('wall_assembly_id') or ''))
                if not wall:
                    continue
                start, end = wall.get('start') or {}, wall.get('end') or {}
                x0, z0 = float(start.get('x') or 0), float(start.get('z') or 0)
                x1, z1 = float(end.get('x') or 0), float(end.get('z') or 0)
                wall_length = math.hypot(x1 - x0, z1 - z0)
                if wall_length <= .01:
                    continue
                ux, uz = (x1 - x0) / wall_length, (z1 - z0) / wall_length
                width = max(.02, float(opening.get('width_m') or 0))
                along = float(opening.get('offset_m') or 0) + width / 2
                sill = max(0.0, float(opening.get('sill_height_m') or 0))
                opening_top = min(default_height, sill + max(
                    .05, float(opening.get('height_m') or 2.1)))
                vertical_parts = []
                if sill > .02:
                    vertical_parts.append((0.0, sill))
                if opening_top < default_height - .02:
                    vertical_parts.append((opening_top, default_height))
                for bottom, top in vertical_parts:
                    triangles.extend(_box_triangles(
                        (x0 + ux * along, (bottom + top) / 2,
                         z0 + uz * along),
                        (width, top - bottom, max(
                            .02, float(wall.get('thickness_m') or .12))),
                        -math.degrees(math.atan2(uz, ux)), 'wall'))
        else:
            for wall in model.get('walls') or []:
                start, end = wall.get('start') or {}, wall.get('end') or {}
                x0, z0 = float(start.get('x') or 0), float(start.get('z') or 0)
                x1, z1 = float(end.get('x') or 0), float(end.get('z') or 0)
                length = math.hypot(x1 - x0, z1 - z0)
                if length <= .01:
                    continue
                ux, uz = (x1 - x0) / length, (z1 - z0) / length
                angle = -math.degrees(math.atan2(uz, ux))
                for left, right, bottom, top in _wall_parts(wall, openings):
                    segment = right - left
                    center_along = (left + right) / 2
                    center = (x0 + ux * center_along, (bottom + top) / 2, z0 + uz * center_along)
                    triangles.extend(_box_triangles(
                        center, (segment, top - bottom, max(.02, float(wall.get('thickness_m') or .12))),
                        angle, 'wall'))

    # GeometryManifest locks the structural shell, but it intentionally does
    # not contain movable/fixed furnishing proxies.  Those audited CAD anchors
    # must still be rendered into RGB/semantic/subject channels; otherwise the
    # strict-manifest path silently drops beds, sanitary fixtures, and kitchen
    # equipment and gives the image model an under-constrained empty room.
    for item in model.get('fixed_objects') or []:
        if item.get('review_status') == 'rejected':
            continue
        position, size = item.get('position') or {}, item.get('size') or {}
        sx, sy, sz = (max(.02, float(size.get(axis) or 0)) for axis in ('x', 'y', 'z'))
        role = str(item.get('semantic_role') or item.get('kind') or 'other')
        bottom_y = float(position.get('y') or 0)
        # A 2D DWG proves footprint/identity but not Z.  Put flat cooktop/sink
        # symbols on the local 900mm worktop instead of burying them at floor
        # level.  This changes render proxies only, never the CAD/model facts.
        if role in {'hob', 'sink'} and bottom_y <= .05:
            bottom_y = .9
        center = (float(position.get('x') or 0), bottom_y + sy / 2,
                  float(position.get('z') or 0))
        if role not in SEMANTIC_COLORS:
            role = 'other'
        triangles.extend(_box_triangles(
            center, (sx, sy, sz), float(item.get('rotation_y_deg') or 0), role,
            str(item.get('id') or '')))

    subject_rows = subjects or []
    walls = {str(row.get('id') or ''): row for row in model.get('walls') or []}
    openings_by_id = {str(row.get('id') or ''): row for row in openings}
    for subject in subject_rows:
        anchor_id = str(subject.get('anchor_id') or '')
        if subject.get('anchor_kind') == 'derived_fixture_component':
            source_id = str(subject.get('source_anchor_id') or '')
            source = next((row for row in model.get('fixed_objects') or []
                           if str(row.get('id') or '') == source_id), None)
            if not source:
                continue
            position, size = source.get('position') or {}, source.get('size') or {}
            sx, sy, sz = (max(.02, float(size.get(axis) or 0)) for axis in ('x', 'y', 'z'))
            role = str(subject.get('role') or '')
            if role == 'hood':
                render_size, bottom = (max(.35, sx * 1.05), .45, max(.25, sz * .65)), 1.75
            elif role == 'faucet':
                render_size, bottom = (max(.12, sx * .18), .28, max(.08, sz * .18)), float(position.get('y') or 0) + sy
            elif role == 'mirror':
                render_size, bottom = (max(.35, sx * .9), .75, .035), 1.15
            else:
                render_size, bottom = (sx, sy, sz), float(position.get('y') or 0)
            triangles.extend(_box_triangles(
                (float(position.get('x') or 0), bottom + render_size[1] / 2,
                 float(position.get('z') or 0)), render_size,
                float(source.get('rotation_y_deg') or 0), 'other', anchor_id, True))
        elif subject.get('anchor_kind') in ('opening', 'door', 'window', 'open_connection') or anchor_id in openings_by_id:
            opening = openings_by_id.get(anchor_id)
            wall = walls.get(str((opening or {}).get('wall_id') or ''))
            if not opening or not wall:
                continue
            start, end = wall.get('start') or {}, wall.get('end') or {}
            x0, z0 = float(start.get('x') or 0), float(start.get('z') or 0)
            x1, z1 = float(end.get('x') or 0), float(end.get('z') or 0)
            length = max(.001, math.hypot(x1 - x0, z1 - z0))
            ux, uz = (x1 - x0) / length, (z1 - z0) / length
            along = float(opening.get('offset_m') or 0) + float(opening.get('width_m') or .8) / 2
            center = (x0 + ux * along,
                      float(opening.get('sill_height_m') or 0) + float(opening.get('height_m') or 2.1) / 2,
                      z0 + uz * along)
            triangles.extend(_box_triangles(
                center, (float(opening.get('width_m') or .8), float(opening.get('height_m') or 2.1), .018),
                -math.degrees(math.atan2(uz, ux)), 'other', anchor_id, True))
        elif subject.get('anchor_kind') == 'cad_open_semantic_boundary' and subject.get('position'):
            position = subject['position']
            triangles.extend(_box_triangles(
                (float(position.get('x') or 0), .9, float(position.get('z') or 0)),
                (.42, 1.8, .035), 0, 'other', anchor_id, True))
    return triangles


def _clip_near(points: np.ndarray, near: float = .05) -> list[np.ndarray]:
    polygon = [row for row in points]
    output = []
    for index, current in enumerate(polygon):
        previous = polygon[index - 1]
        current_inside = current[2] >= near
        previous_inside = previous[2] >= near
        if current_inside != previous_inside:
            ratio = (near - previous[2]) / (current[2] - previous[2])
            output.append(previous + (current - previous) * ratio)
        if current_inside:
            output.append(current)
    if len(output) < 3:
        return []
    return [np.array([output[0], output[index], output[index + 1]])
            for index in range(1, len(output) - 1)]


def _camera_basis(camera: dict):
    position = np.array([float((camera.get('position') or {}).get(axis) or 0)
                         for axis in ('x', 'y', 'z')], dtype=np.float64)
    target = np.array([float((camera.get('target') or {}).get(axis) or 0)
                       for axis in ('x', 'y', 'z')], dtype=np.float64)
    forward = _normalized(target - position)
    right = _normalized(np.cross(forward, np.array([0.0, 1.0, 0.0])))
    up = _normalized(np.cross(right, forward))
    return position, np.stack([right, up, forward])


def _rasterize(triangles: list[Triangle], camera: dict, width: int, height: int,
               subject_colors: dict[str, tuple[int, int, int]] | None = None,
               *, metric_depth_range: tuple[float, float] | None = None,
               world_normal: bool = False,
               basis_override: np.ndarray | None = None):
    """软件光栅化。

    metric_depth_range=(near, far):整组六面统一 metric near/far(文档 §5.2-4),
    各面不再按自身 1%/99% 分位归一;None 保持单张透视图的旧行为。
    world_normal=True:输出 world-space XYZ→RGB;否则 camera-space(旧行为)。
    basis_override:立方体面渲染时传入该面 (right, up, forward) 基;±Y 面
    不能从 camera.target 推导,必须显式传入。
    """
    position, basis = _camera_basis(camera)
    if basis_override is not None:
        position = np.array([float((camera.get('position') or {}).get(axis) or 0)
                             for axis in ('x', 'y', 'z')], dtype=np.float64)
        basis = basis_override
    focal = max(1.0, float(camera.get('focal_length_mm') or 28))
    scale = height * focal / 24.0
    # Keep the deliverable buffers and the subject-ID proof on separate depth
    # surfaces.  Synthetic subject markers (hood/faucet/mirror/open-boundary)
    # are evidence-only geometry: they must participate in subject occlusion,
    # but must never paint black proxy boxes into RGB/semantic/depth.
    zbuffer = np.full((height, width), np.inf, dtype=np.float32)
    subject_zbuffer = np.full((height, width), np.inf, dtype=np.float32)
    rgb = np.full((height, width, 3), (233, 230, 223), dtype=np.uint8)
    semantic = np.zeros((height, width, 3), dtype=np.uint8)
    normal_image = np.zeros((height, width, 3), dtype=np.uint8)
    subject_image = np.zeros((height, width, 3), dtype=np.uint8)
    light = _normalized(np.array([-.35, .8, -.45], dtype=np.float64))

    for triangle in triangles:
        camera_points = (triangle.points - position) @ basis.T
        clipped_rows = _clip_near(camera_points)
        for camera_triangle in clipped_rows:
            screen = np.empty((3, 2), dtype=np.float64)
            screen[:, 0] = width / 2 + scale * camera_triangle[:, 0] / camera_triangle[:, 2]
            screen[:, 1] = height / 2 - scale * camera_triangle[:, 1] / camera_triangle[:, 2]
            min_x = max(0, int(math.floor(float(screen[:, 0].min()))))
            max_x = min(width - 1, int(math.ceil(float(screen[:, 0].max()))))
            min_y = max(0, int(math.floor(float(screen[:, 1].min()))))
            max_y = min(height - 1, int(math.ceil(float(screen[:, 1].max()))))
            if min_x > max_x or min_y > max_y:
                continue
            x0, y0 = screen[0]
            x1, y1 = screen[1]
            x2, y2 = screen[2]
            denominator = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            if abs(denominator) < 1e-9:
                continue
            xs, ys = np.meshgrid(
                np.arange(min_x, max_x + 1, dtype=np.float64) + .5,
                np.arange(min_y, max_y + 1, dtype=np.float64) + .5,
            )
            w0 = ((y1 - y2) * (xs - x2) + (x2 - x1) * (ys - y2)) / denominator
            w1 = ((y2 - y0) * (xs - x2) + (x0 - x2) * (ys - y2)) / denominator
            w2 = 1 - w0 - w1
            inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not np.any(inside):
                continue
            inverse_depth = (w0 / camera_triangle[0, 2] + w1 / camera_triangle[1, 2]
                             + w2 / camera_triangle[2, 2])
            depth = np.where(inverse_depth > 1e-9, 1 / inverse_depth, np.inf)
            subject_depth = subject_zbuffer[min_y:max_y + 1, min_x:max_x + 1]
            marker_color = (subject_colors or {}).get(triangle.anchor_id)
            # Expected opening/derived-fixture markers may be coplanar with a
            # wall or counter.  Only that exact expected marker may win a 3cm
            # equality band; ordinary objects retain strict depth occlusion.
            if triangle.subject_only and marker_color:
                # Double-line architectural walls can put the second CAD face
                # up to ~40cm in front of an opening marker.  The camera
                # generator has already proved the ray uses this exact opening
                # assembly, so a 45cm marker-only band is safe and prevents the
                # paired wall face from erasing a real door/window.
                subject_update = inside & (depth <= subject_depth + .45)
            else:
                subject_update = inside & (depth < subject_depth)
            if np.any(subject_update):
                subject_depth[subject_update] = depth[subject_update]
                subject_slice = subject_image[min_y:max_y + 1, min_x:max_x + 1]
                subject_slice[subject_update] = marker_color or (0, 0, 0)

            if not triangle.subject_only:
                target_depth = zbuffer[min_y:max_y + 1, min_x:max_x + 1]
                update = inside & (depth < target_depth)
                if not np.any(update):
                    continue
                target_depth[update] = depth[update]
                base = np.array(RGB_COLORS.get(triangle.role, RGB_COLORS['other']), dtype=np.float64)
                shade = .58 + .42 * abs(float(np.dot(triangle.normal, light)))
                role_rgb = tuple(np.clip(base * shade, 0, 255).astype(np.uint8).tolist())
                semantic_rgb = SEMANTIC_COLORS.get(triangle.role, SEMANTIC_COLORS['other'])
                rgb_slice = rgb[min_y:max_y + 1, min_x:max_x + 1]
                semantic_slice = semantic[min_y:max_y + 1, min_x:max_x + 1]
                normal_slice = normal_image[min_y:max_y + 1, min_x:max_x + 1]
                rgb_slice[update] = role_rgb
                semantic_slice[update] = semantic_rgb
                camera_normal = basis @ triangle.normal
                if world_normal:
                    normal_rgb = tuple(np.clip((triangle.normal * .5 + .5) * 255, 0, 255).astype(np.uint8).tolist())
                else:
                    normal_rgb = tuple(np.clip((camera_normal * .5 + .5) * 255, 0, 255).astype(np.uint8).tolist())
                normal_slice[update] = normal_rgb

    finite = np.isfinite(zbuffer)
    depth_image = np.zeros((height, width), dtype=np.uint8)
    if np.any(finite):
        distances = zbuffer[finite]
        if metric_depth_range is not None:
            near, far = metric_depth_range
        else:
            near = max(.05, float(np.percentile(distances, 1)))
            far = max(near + .1, float(np.percentile(distances, 99)))
        normalized = 1 - np.clip((zbuffer - near) / (far - near), 0, 1)
        depth_image[finite] = np.clip(normalized[finite] * 255, 0, 255).astype(np.uint8)
    edge = np.full((height, width), 255, dtype=np.uint8)
    semantic_gray = cv2.cvtColor(semantic, cv2.COLOR_RGB2GRAY)
    depth_edges = cv2.Canny(depth_image, 18, 42)
    semantic_edges = cv2.Canny(semantic_gray, 8, 24)
    combined = cv2.dilate(cv2.bitwise_or(depth_edges, semantic_edges), np.ones((2, 2), np.uint8))
    edge[combined > 0] = 0
    return {
        'rgb': Image.fromarray(rgb, 'RGB'),
        'depth': Image.fromarray(depth_image, 'L').convert('RGB'),
        'normal': Image.fromarray(normal_image, 'RGB'),
        'edge': Image.fromarray(edge, 'L').convert('RGB'),
        'semantic': Image.fromarray(semantic, 'RGB'),
        'subject_id': Image.fromarray(subject_image, 'RGB'),
    }


def _subject_color(index: int) -> tuple[int, int, int]:
    value = max(1, min(0xFFFFFF, int(index) + 1))
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def _reference_render_gate(semantic: Image.Image, room_profile: str):
    array = np.asarray(semantic.convert('RGB'))
    denominator = int(array.shape[0] * array.shape[1])
    fractions = {}
    matched = 0
    for role, color in SEMANTIC_COLORS.items():
        count = int(np.all(array == np.asarray(color, dtype=np.uint8), axis=2).sum())
        if count:
            fractions[role] = count / denominator
            matched += count
    floor_fraction = float(fractions.get('floor') or 0)
    wall_fraction = float(fractions.get('wall') or 0)
    peaks = sorted([(role, value) for role, value in fractions.items()
                    if role not in ('floor', 'wall', 'other')], key=lambda row: -row[1])
    peak_role, peak_fraction = peaks[0] if peaks else ('', 0.0)
    # Reference slots have their own CAD-derived must-show proof below.  The
    # generic room gate used by the old image workflow rejected valid frontal
    # kitchen/bed/bath elevations simply because little floor was visible.
    # Here only an empty frame or one proxy consuming almost the whole image is
    # a hard error; floor/wall ratios remain recorded as useful diagnostics.
    reasons = []
    if matched / max(1, denominator) < .20:
        reasons.append(f'可识别 CAD 灰模仅 {matched / max(1, denominator):.2%}，低于 20.00%')
    if peak_role and peak_fraction > .32:
        reasons.append(f'{peak_role} 占画面 {peak_fraction:.2%}，高于 32.00%')
    return {
        'version': 'whole-home-reference-render-gate-v3-software',
        'pass': not reasons, 'status': 'pass' if not reasons else 'blocked',
        'profile': room_profile or 'other', 'denominator_pixels': denominator,
        'matched_pixels': matched, 'unmatched_pixels': denominator - matched,
        'floor_fraction': floor_fraction, 'wall_fraction': wall_fraction,
        'peak_semantic_role': peak_role,
        'peak_semantic_role_fraction': peak_fraction,
        'semantic_role_fractions': fractions, 'required_groups': [], 'reasons': reasons,
        'renderer': 'numpy_zbuffer_v1',
    }


def image_data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format='PNG', optimize=True)
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def render_reference_candidate(model: dict, candidate: dict, contract: dict,
                               width: int = 384, height: int = 288):
    contract = split_reference_contract(contract)
    camera = candidate.get('camera') or {}
    validation = camera.get('reference_contract_validation') or {}
    subjects = validation.get('must_show_subjects') or []
    legend = {
        'version': 'whole-home-subject-id-v1', 'pixel_origin': 'top-left',
        'renderer': 'numpy_zbuffer_v1',
        'subjects': [
            {
                'subject': str(row.get('subject') or ''),
                'anchor_id': str(row.get('anchor_id') or ''),
                'anchor_kind': str(row.get('anchor_kind') or ''),
                'role': str(row.get('role') or ''),
                'color': list(_subject_color(index)),
            }
            for index, row in enumerate(subjects)
        ],
    }
    colors = {row['anchor_id']: tuple(row['color']) for row in legend['subjects']}
    triangles = build_scene_triangles(model, subjects)
    images = _rasterize(triangles, camera, width, height, colors)
    safe_frame = dict((contract.get('camera') or {}).get('safe_frame') or {})
    slot_id = str(candidate.get('slot_id') or candidate.get('reference_slot_id') or '')
    slot = next((row for row in contract.get('slots') or []
                 if str(row.get('slot_id') or '') == slot_id), {})
    safe_frame['subject_overrides'] = dict(slot.get('subject_safe_frame_overrides') or {})
    subject_evidence = evaluate_subject_id_pixels(
        images['subject_id'], legend, [str(row.get('subject') or '') for row in subjects], safe_frame)
    room_profile = str((candidate.get('metrics') or {}).get('room_profile') or 'other')
    render_gate = _reference_render_gate(images['semantic'], room_profile)
    return {
        'images': images, 'legend': legend, 'subject_evidence': subject_evidence,
        'render_gate': render_gate,
        'pass': bool(subject_evidence.get('pass') and render_gate.get('pass')),
        'renderer': 'numpy_zbuffer_v1', 'width': width, 'height': height,
    }
