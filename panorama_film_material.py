# -*- coding: utf-8 -*-
"""Automatic exact manufacturer-film material pass for generated ERP panoramas."""
from __future__ import annotations

from PIL import Image

from .film_repeat_floor import analyze_film_path, parse_plank_dimensions
from .floor_renderer import image_sha256
from .panorama_local_geometry import refine_erp_floor_mask, validate_geometry_contract
from .spherical_floor_renderer import (
    SphericalFloorRecipe,
    combine_view_masks,
    prepare_floor_mask_views,
    render_spherical_floor,
)


def apply_manufacturer_film(image: Image.Image, film_path: str, params: dict,
                            *, manifest: dict | None = None,
                            geometry_contract: dict | None = None,
                            geometry_source_path: str = "") -> tuple[Image.Image, dict]:
    film, current_manifest = analyze_film_path(film_path, params)
    if manifest and manifest.get("manifest_hash") != current_manifest.get("manifest_hash"):
        raise ValueError("彩膜文件或物理参数已经变化，请重新预览确认")
    manifest = current_manifest
    if geometry_contract:
        validate_geometry_contract(geometry_contract, geometry_source_path)
    views = prepare_floor_mask_views(
        image.convert("RGB"), cache_key=f"film:{image_sha256(image)[:24]}")
    masks = [{"id": row["id"], "mask_b64": row["mask_b64"]} for row in views]
    mask = combine_view_masks(masks, image.width, image.height)
    mask, mask_quality = refine_erp_floor_mask(image.convert("RGB"), mask, views)
    if not mask.getbbox():
        raise ValueError("本地地板蒙版为空，需要重新校准")
    plank_width, plank_length = parse_plank_dimensions(str(params.get("floor_size") or ""))
    camera = dict((geometry_contract or {}).get("camera") or {})
    floor_frame = dict((geometry_contract or {}).get("floor_frame") or {})
    recipe = SphericalFloorRecipe(
        camera_height_m=float(camera.get("camera_height_m") or 1.55),
        rotation_deg=float(floor_frame.get("plank_direction_deg") or 90.0),
        scale=1.0,
        offset_x=float(floor_frame.get("origin_x_m") or 0.0),
        offset_z=float(floor_frame.get("origin_z_m") or 0.0),
        texture_width_mm=float(plank_length or manifest["repeat_length_mm"]),
        texture_height_mm=float(manifest["film_width_mm"]),
        plank_width_mm=plank_width,
        plank_length_mm=plank_length,
        illumination_strength=0.50,
        shadow_strength=0.62,
        contact_shadow_strength=0.48,
        feather=0.004,
    )
    output, metadata = render_spherical_floor(
        image.convert("RGB"), film, mask, recipe,
        film_image=film, film_manifest=manifest)
    metadata.update({
        "status": ("applied" if mask_quality.get("status") == "ready" else "needs_calibration"),
        "delivery_mode": "local_exact_film_v4",
        "mask_source": "clipseg_plus_mobile_sam_plus_local_depth_five_rectilinear_views",
        "mask_quality": mask_quality,
        "fixed_nadir_fill": False,
        "geometry_contract_hash": str((geometry_contract or {}).get("contract_hash") or ""),
        "geometry_locked": bool(geometry_contract and geometry_contract.get("status") == "ready"),
        "mask_views": [{
            "id": row["id"], "confidence": row["confidence"],
            "status": row["status"], "warnings": row["warnings"],
        } for row in views],
    })
    return output, metadata


__all__ = ["apply_manufacturer_film"]
