"""Fail-closed checks for pixel/metric segment registration.

The source contract owns the affine transform.  Evidence producers must not
silently reinterpret an opening axis: every pixel segment is projected through
the inverse transform and compared with the metric segment before it can be
used by a later layer.
"""
from __future__ import annotations

from typing import Sequence


def _apply(m: Sequence[Sequence[float]], p: Sequence[float]) -> tuple[float, float]:
    x, y = float(p[0]), float(p[1])
    w = float(m[2][0]) * x + float(m[2][1]) * y + float(m[2][2])
    if abs(w) < 1e-12:
        raise ValueError("registration maps point to infinity")
    return ((float(m[0][0]) * x + float(m[0][1]) * y + float(m[0][2])) / w,
            (float(m[1][0]) * x + float(m[1][1]) * y + float(m[1][2])) / w)


def _inverse(m: Sequence[Sequence[float]]) -> tuple[tuple[float, ...], ...]:
    # General 3x3 inverse, written explicitly to keep this gate dependency-free.
    a = [[float(m[r][c]) for c in range(3)] for r in range(3)]
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
           - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    if abs(det) < 1e-12:
        raise ValueError("registration matrix is singular")
    cof = [[
        a[(c + 1) % 3][(r + 1) % 3] * a[(c + 2) % 3][(r + 2) % 3]
        - a[(c + 1) % 3][(r + 2) % 3] * a[(c + 2) % 3][(r + 1) % 3]
        for c in range(3)] for r in range(3)]
    return tuple(tuple(cof[r][c] / det for c in range(3)) for r in range(3))


def validate_pixel_metric_segment(matrix: Sequence[Sequence[float]], pixel_segment: Sequence[Sequence[float]], metric_segment: Sequence[Sequence[float]], tolerance_px: float = 1.0) -> dict:
    """Return measured registration; raise if either endpoint is inconsistent."""
    if len(pixel_segment) != 2 or len(metric_segment) != 2:
        raise ValueError("segments must contain exactly two endpoints")
    inv = _inverse(matrix)
    expected = [_apply(inv, p) for p in metric_segment]
    distances = [((expected[i][0] - float(pixel_segment[i][0])) ** 2 + (expected[i][1] - float(pixel_segment[i][1])) ** 2) ** 0.5 for i in range(2)]
    if max(distances) > float(tolerance_px):
        raise ValueError(f"pixel/metric registration mismatch: max endpoint error {max(distances):.3f}px")
    return {"expected_pixel_segment": [list(p) for p in expected], "endpoint_error_px": distances, "max_endpoint_error_px": max(distances), "tolerance_px": float(tolerance_px)}
