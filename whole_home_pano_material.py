from __future__ import annotations

"""Deterministic geometry-locked material pass for 2:1 ERP panoramas.

This is intentionally not a generative model.  It decodes the renderer's
metric depth, world normals and semantic IDs, evaluates low-frequency PBR-like
materials in world coordinates, and then restores every protected structure
pixel from the authoritative clay ERP.  It is the fail-safe delivery path when
an image model cannot satisfy the structural gate.
"""

import math

import cv2
import numpy as np
from PIL import Image

try:
    from .whole_home_software_renderer import SEMANTIC_COLORS
except ImportError:  # pragma: no cover
    from whole_home_software_renderer import SEMANTIC_COLORS


MATERIAL_ENGINE_VERSION = 'geometry-material-v1'

_ROLE_BASE = {
    'wall': (235, 231, 221),
    'floor': (190, 151, 105),
    'kitchen_run': (176, 149, 116),
    'sink': (178, 190, 192),
    'hob': (43, 45, 47),
    'fridge': (118, 124, 127),
    'basin': (226, 229, 225),
    'toilet': (232, 233, 229),
    'shower_zone': (173, 202, 207),
    'bed': (218, 207, 190),
    'wardrobe': (151, 119, 84),
    'sofa': (174, 158, 145),
    'tv': (36, 38, 40),
    'dining_table': (139, 103, 72),
    'entry_storage': (164, 132, 96),
    'balcony_rail': (83, 89, 87),
    'washing_machine': (199, 201, 199),
    'other': (153, 146, 137),
}


def _arrays_same_size(*images: Image.Image) -> tuple[int, int]:
    sizes = {image.size for image in images}
    if len(sizes) != 1:
        raise ValueError(f'geometry_material_channel_size_mismatch:{sorted(sizes)}')
    width, height = next(iter(sizes))
    if width != height * 2:
        raise ValueError('geometry_material_erp_must_be_2_1')
    return width, height


def materialize_geometry_locked_erp(
        rgb_erp: Image.Image, depth_erp: Image.Image, normal_erp: Image.Image,
        semantic_erp: Image.Image, manifest: dict, *,
        holdout_mask: Image.Image | None = None,
        preset: str = 'warm-contemporary') -> Image.Image:
    """Return a styled ERP whose protected structure pixels remain byte-exact."""
    if preset != 'warm-contemporary':
        raise ValueError(f'geometry_material_preset_unknown:{preset}')
    width, height = _arrays_same_size(rgb_erp, depth_erp, normal_erp, semantic_erp)
    if holdout_mask is not None and holdout_mask.size != (width, height):
        raise ValueError('geometry_material_holdout_size_mismatch')
    source = np.asarray(rgb_erp.convert('RGB'), dtype=np.uint8)
    depth = np.asarray(depth_erp.convert('L'), dtype=np.uint8)
    normal = np.asarray(normal_erp.convert('RGB'), dtype=np.uint8)
    semantic = np.asarray(semantic_erp.convert('RGB'), dtype=np.uint8)
    output = np.empty_like(source, dtype=np.float32)

    camera = manifest.get('camera_center_m') or {}
    camera_xyz = np.array([
        float(camera.get('x') or 0), float(camera.get('y') or 1.55),
        float(camera.get('z') or 0),
    ], dtype=np.float32)
    near = max(.001, float(manifest.get('near_m') or .05))
    far = max(near + .1, float(manifest.get('far_m') or 30.0))
    role_names = tuple(SEMANTIC_COLORS)
    semantic_palette = np.asarray([SEMANTIC_COLORS[name] for name in role_names], dtype=np.int16)
    material_palette = np.asarray([_ROLE_BASE[name] for name in role_names], dtype=np.float32)
    wall_index = role_names.index('wall')
    floor_index = role_names.index('floor')
    bed_index = role_names.index('bed')

    u = (np.arange(width, dtype=np.float32) + .5) / width
    lam = 2.0 * np.pi * (u - .5)
    sin_lam = np.sin(lam)
    cos_lam = np.cos(lam)
    chunk_rows = 96
    for start in range(0, height, chunk_rows):
        stop = min(height, start + chunk_rows)
        rows = stop - start
        v = (np.arange(start, stop, dtype=np.float32) + .5) / height
        phi = np.pi * (.5 - v)
        cos_phi = np.cos(phi)[:, None]
        dx = cos_phi * sin_lam[None, :]
        dy = np.broadcast_to(np.sin(phi)[:, None], (rows, width))
        dz = cos_phi * cos_lam[None, :]

        semantic_chunk = semantic[start:stop].astype(np.int16)
        geometry = np.any(semantic_chunk != 0, axis=2)
        best_distance = np.full((rows, width), np.iinfo(np.int32).max, dtype=np.int32)
        role_index = np.zeros((rows, width), dtype=np.int16)
        for index, color in enumerate(semantic_palette):
            # Promote *before* squaring; int16 multiplication wraps for color
            # deltas above ~181 and can classify an ivory wall as a black TV.
            delta = (semantic_chunk.astype(np.int32)
                     - color.astype(np.int32)[None, None, :])
            distance = np.sum(delta * delta, axis=2, dtype=np.int32)
            update = distance < best_distance
            best_distance[update] = distance[update]
            role_index[update] = index

        depth_norm = depth[start:stop].astype(np.float32) / 255.0
        distance_m = near + (1.0 - depth_norm) * (far - near)
        # Depth=0 is ambiguous at the far plane; semantic presence is the
        # authoritative geometry mask.  Background floor rays intersect y=0.
        safe_dy = np.where(np.abs(dy) > 1e-4, dy, -1e-4)
        floor_t = np.maximum(0.0, (0.0 - camera_xyz[1]) / safe_dy)
        ray_t = np.where(geometry, distance_m, floor_t)
        world_x = camera_xyz[0] + dx * ray_t
        world_y = camera_xyz[1] + dy * ray_t
        world_z = camera_xyz[2] + dz * ray_t

        # Background in the renderer represents a ceiling/floor environment;
        # the narrow horizontal band is kept warm-neutral instead of invented
        # outdoor scenery, so openings are never reinterpreted.
        base = np.empty((rows, width, 3), dtype=np.float32)
        upper = dy >= -.02
        base[upper] = (242, 239, 231)
        base[~upper] = (190, 151, 105)
        base[geometry] = material_palette[role_index[geometry]]

        floor_pixels = ((role_index == floor_index) & geometry) | (~geometry & ~upper)
        # Smooth world-space oak grain: deliberately no hard plank seams, which
        # would create false structural lines in the P0 edge detector.
        grain = (6.0 * np.sin(world_z * 16.0 + .9 * np.sin(world_x * 3.1))
                 + 2.5 * np.sin(world_x * 5.3 + world_z * 2.2))
        base[..., 0][floor_pixels] += grain[floor_pixels]
        base[..., 1][floor_pixels] += grain[floor_pixels] * .72
        base[..., 2][floor_pixels] += grain[floor_pixels] * .38

        normal_chunk = normal[start:stop].astype(np.float32) / 127.5 - 1.0
        wall_pixels = (role_index == wall_index) & geometry
        # Ceiling/top caps are an orientation fact, not a height guess.  The
        # 8-bit depth channel is deliberately compact and its quantisation can
        # move reconstructed world_y across a hard threshold, creating repeated
        # triangular patches at cube-face boundaries.  A near-vertical world
        # normal is stable across all faces and identifies horizontal wall caps.
        ceiling_pixels = wall_pixels & (np.abs(normal_chunk[..., 1]) > .65)
        base[ceiling_pixels] = (244, 241, 234)
        # Wall paint stays spatially uniform.  Even low-amplitude world-space
        # noise can expose 8-bit depth interpolation at cube-face boundaries.

        # Bedding top surfaces read as fabric rather than an unlabelled box.
        bed_top = (role_index == bed_index) & geometry & (normal_chunk[..., 1] > .65)
        base[bed_top] = (232, 224, 211)
        fabric = 2.0 * np.sin(world_x * 18.0 + world_z * 13.0)
        for channel in range(3):
            channel_data = base[..., channel]
            channel_data[bed_top] += fabric[bed_top]

        normal_length = np.linalg.norm(normal_chunk, axis=2)
        valid_normal = geometry & (normal_length > .2)
        normalized = normal_chunk / np.maximum(normal_length[..., None], 1e-5)
        # Use the vertical component only.  X/Z directional Lambert terms can
        # expose tiny normal interpolation differences at cubemap boundaries;
        # vertical orientation is continuous and still distinguishes horizontal
        # tops/floors from vertical faces.
        shade = .92 + .08 * np.abs(normalized[..., 1])
        # Very gentle metric aerial perspective avoids flat CAD blocks without
        # moving a single edge or opening.
        fog = np.where(geometry, np.clip(distance_m / far, 0, 1), 0) * .08
        shaded = base * shade[..., None]
        shaded = shaded * (1.0 - fog[..., None]) + np.array(
            [236, 234, 229], dtype=np.float32)[None, None, :] * fog[..., None]
        shaded[~valid_normal] = base[~valid_normal]
        output[start:stop] = np.clip(shaded, 0, 255)

    styled = output.astype(np.uint8)
    if holdout_mask is not None:
        editable = np.asarray(holdout_mask.convert('L'), dtype=np.uint8) > 127
        weight = cv2.GaussianBlur(editable.astype(np.float32), (0, 0), 2.0)
        # Architectural samples are byte-exact, while a small outer feather
        # hides the otherwise visible clay/material transition.
        weight[~editable] = 0.0
        styled = np.clip(
            styled.astype(np.float32) * weight[..., None]
            + source.astype(np.float32) * (1.0 - weight[..., None]), 0, 255).astype(np.uint8)
        styled[~editable] = source[~editable]
    return Image.fromarray(styled, 'RGB')


def verify_geometry_locked_replay(
        candidate_path: str, channels: dict, manifest: dict, *,
        holdout_mask_path: str, preset: str = 'warm-contemporary',
        expected_output_sha256: str = '') -> dict:
    """Recompute the local material pass and demand pixel-exact identity."""
    import hashlib
    import os

    required = ('rgb_erp', 'depth_erp', 'normal_erp', 'semantic_erp')
    paths = {key: str(channels.get(key) or '') for key in required}
    if (not candidate_path or not os.path.isfile(candidate_path)
            or not holdout_mask_path or not os.path.isfile(holdout_mask_path)
            or any(not path or not os.path.isfile(path) for path in paths.values())):
        return {
            'check_id': 'geometry_locked_replay', 'status': 'fail',
            'metric': 'required_artifacts', 'value': 0, 'threshold': 1,
            'detail': 'candidate/channel/holdout artifact missing',
        }
    if expected_output_sha256:
        digest = hashlib.sha256()
        with open(candidate_path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != str(expected_output_sha256):
            return {
                'check_id': 'geometry_locked_replay', 'status': 'fail',
                'metric': 'candidate_file_sha256', 'value': digest.hexdigest(),
                'threshold': str(expected_output_sha256),
                'detail': 'candidate file differs from the audited local call ledger',
            }
    try:
        with Image.open(paths['rgb_erp']) as rgb, Image.open(paths['depth_erp']) as depth, \
                Image.open(paths['normal_erp']) as normal, \
                Image.open(paths['semantic_erp']) as semantic, \
                Image.open(holdout_mask_path) as holdout:
            replay = materialize_geometry_locked_erp(
                rgb, depth, normal, semantic, manifest,
                holdout_mask=holdout, preset=preset)
        with Image.open(candidate_path) as candidate_image:
            candidate = np.asarray(candidate_image.convert('RGB'), dtype=np.int16)
        expected = np.asarray(replay.convert('RGB'), dtype=np.int16)
    except Exception as ex:
        return {
            'check_id': 'geometry_locked_replay', 'status': 'fail',
            'metric': 'replay_exception', 'value': type(ex).__name__, 'threshold': 'none',
            'detail': str(ex)[:500],
        }
    if candidate.shape != expected.shape:
        return {
            'check_id': 'geometry_locked_replay', 'status': 'fail',
            'metric': 'pixel_shape', 'value': list(candidate.shape),
            'threshold': list(expected.shape), 'detail': 'replay shape mismatch',
        }
    delta = np.max(np.abs(candidate - expected), axis=2)
    mismatched = int(np.count_nonzero(delta))
    return {
        'check_id': 'geometry_locked_replay',
        'status': 'pass' if mismatched == 0 else 'fail',
        'metric': 'mismatched_pixels', 'value': mismatched, 'threshold': 0,
        'max_rgb_delta': int(delta.max()) if delta.size else 0,
        'pixel_count': int(delta.size),
        'engine_version': MATERIAL_ENGINE_VERSION,
        'coordinate_transform': 'identity_pixel_grid',
        'spatial_operations': [],
        'input_source_hash': str(manifest.get('source_hash') or ''),
        'detail': ('pixel-exact deterministic replay; no warp, crop, resample, inpaint or '
                   'geometry-generating operation'),
    }


__all__ = [
    'MATERIAL_ENGINE_VERSION', 'materialize_geometry_locked_erp',
    'verify_geometry_locked_replay',
]
