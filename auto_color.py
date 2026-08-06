"""Automatic post-generation floor color correction.

This module keeps the expensive, model-independent post-process separate from
the job orchestrator: MobileSAM finds the floor, then the existing masked LAB
engine aligns only floor chroma to the uploaded swatch.  Automatic runs are
serialized so concurrent 4K model results do not multiply the peak memory use.
"""
from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .color_match import match_color_masked
from .floor_segmentation import segment_floor


AUTO_COLOR_STRENGTH = 0.7
AUTO_COLOR_MASK_FEATHER = 0.003
_AUTO_COLOR_LOCK = threading.Lock()


@dataclass
class AutoColorResult:
    image: Image.Image | None
    mask: Image.Image | None
    metadata: dict


def auto_color_match_generated(source: Image.Image, ref_path: str, cache_key: str,
                               *, strength: float = AUTO_COLOR_STRENGTH,
                               mask_feather: float = AUTO_COLOR_MASK_FEATHER) -> AutoColorResult:
    """Return an automatically corrected generated image, or a safe skip result.

    A missing/uncertain floor mask is deliberately not replaced by global color
    correction: preserving walls and furniture is more important than forcing a
    result when segmentation has insufficient confidence.
    """
    metadata = {
        'operation': 'auto_color_match',
        'scope': 'floor_mask',
        'adjustment_mode': 'auto',
        'strength': float(strength),
        'mask_feather': float(mask_feather),
        'status': 'started',
        'warnings': [],
    }
    with _AUTO_COLOR_LOCK:
        working, segmented = segment_floor(source, cache_key, auto_seed=True)
        metadata.update({
            'status': segmented.status,
            'confidence': round(float(segmented.confidence), 4),
            'segmentation_model': segmented.model,
            'warnings': list(segmented.warnings or []),
            'mask_width': working.width,
            'mask_height': working.height,
        })
        if segmented.mask is None:
            return AutoColorResult(None, None, metadata)

        mask = Image.fromarray(
            np.where(segmented.mask, 255, 0).astype(np.uint8), mode='L')
        with Image.open(ref_path) as ref_file:
            ref = ref_file.convert('RGB')
            ref.load()
        corrected = match_color_masked(
            source, ref, mask,
            strength=strength,
            adjustment_mode='auto',
            mask_feather=mask_feather,
        )
        metadata['status'] = 'ok'
        return AutoColorResult(corrected, mask, metadata)


def save_auto_color_mask(mask: Image.Image, result_path: str) -> str:
    """Save the exact automatic mask beside its corrected PNG, atomically."""
    root, _ = os.path.splitext(result_path)
    target = root + '_mask.png'
    fd, temp_path = tempfile.mkstemp(
        prefix='.auto_color_mask_', suffix='.png', dir=os.path.dirname(target))
    os.close(fd)
    try:
        mask.convert('L').save(temp_path, format='PNG', optimize=True)
        os.replace(temp_path, target)
        return os.path.basename(target)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


__all__ = [
    'AUTO_COLOR_STRENGTH',
    'AUTO_COLOR_MASK_FEATHER',
    'AutoColorResult',
    'auto_color_match_generated',
    'save_auto_color_mask',
]
