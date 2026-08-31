"""Low-level managed image and thumbnail storage helpers."""

from __future__ import annotations

import glob
import hashlib
import io
import os
import tempfile
import threading
from typing import Optional

from PIL import Image


SAMPLE_DIR_NAME = "_samples"
THUMB_CACHE_VERSION = "v2"
_asset_write_lock = threading.Lock()
_asset_lifecycle_lock = threading.RLock()


def asset_lifecycle_lock() -> threading.RLock:
    return _asset_lifecycle_lock


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.isfile(path) and sha256_file(path) == sha256_bytes(data):
        return
    fd, tmp = tempfile.mkstemp(prefix=".asset_", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        tmp = ""
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def sample_jpeg_bytes(image_or_path, *, max_width: int = 400) -> bytes:
    if isinstance(image_or_path, str):
        with Image.open(image_or_path) as opened:
            image = opened.copy()
    else:
        image = image_or_path.copy()
    try:
        if max_width and image.width > max_width:
            height = max(1, int(image.height * max_width / image.width))
            image = image.resize((max_width, height), Image.Resampling.LANCZOS)
        if image.mode != "RGB":
            image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()
    finally:
        try:
            image.close()
        except Exception:
            pass


def normalize_sample_bytes(data: bytes) -> bytes:
    """Validate legacy bytes and normalize non-JPEG input to the sample contract."""
    with Image.open(io.BytesIO(data)) as opened:
        opened.verify()
    with Image.open(io.BytesIO(data)) as opened:
        if (opened.format or "").upper() == "JPEG" and opened.width <= 400:
            return data
        return sample_jpeg_bytes(opened, max_width=400)


def store_sample_bytes(data: bytes, output_dir: str) -> str:
    normalized = normalize_sample_bytes(data)
    digest = sha256_bytes(normalized)
    rel = f"{SAMPLE_DIR_NAME}/{digest}.jpg"
    target = os.path.join(output_dir, *rel.split("/"))
    with _asset_write_lock:
        _atomic_write_bytes(target, normalized)
        if sha256_file(target) != digest:
            raise OSError(f"sample asset hash verification failed: {target}")
    return rel


def store_sample_image(image_or_path, output_dir: str) -> str:
    return store_sample_bytes(sample_jpeg_bytes(image_or_path), output_dir)


def output_thumb_cache_path(source: str, size: int, thumb_dir: str) -> str:
    stat = os.stat(source)
    source_key = hashlib.sha256(os.path.normcase(os.path.realpath(source)).encode("utf-8")).hexdigest()[:20]
    version = hashlib.sha256(
        f"{stat.st_mtime_ns}:{stat.st_size}:{THUMB_CACHE_VERSION}".encode("ascii")
    ).hexdigest()[:16]
    return os.path.join(thumb_dir, f"outv2_{source_key}_{version}_{int(size)}.jpg")


def output_thumb_prefix(source: str) -> str:
    return hashlib.sha256(os.path.normcase(os.path.realpath(source)).encode("utf-8")).hexdigest()[:20]


def purge_output_thumbnails(source: str, thumb_dir: str, *, keep: Optional[str] = None) -> tuple[int, int]:
    count = 0
    freed = 0
    pattern = os.path.join(thumb_dir, f"outv2_{output_thumb_prefix(source)}_*.jpg")
    keep_real = os.path.realpath(keep) if keep else ""
    for path in glob.glob(pattern):
        if keep_real and os.path.realpath(path) == keep_real:
            continue
        try:
            size = os.path.getsize(path)
            os.remove(path)
            count += 1
            freed += size
        except OSError:
            continue
    return count, freed


def clear_thumbnail_cache(thumb_dir: str) -> tuple[int, int]:
    """Delete only regular cache files inside the exact configured cache directory."""
    count = 0
    freed = 0
    base = os.path.realpath(thumb_dir)
    if not os.path.isdir(base):
        return count, freed
    for entry in os.scandir(base):
        try:
            path = os.path.realpath(entry.path)
            if os.path.commonpath([base, path]) != base or not entry.is_file(follow_symlinks=False):
                continue
            size = entry.stat(follow_symlinks=False).st_size
            os.remove(path)
            count += 1
            freed += size
        except (OSError, ValueError):
            continue
    return count, freed


__all__ = [
    "SAMPLE_DIR_NAME", "THUMB_CACHE_VERSION", "asset_lifecycle_lock", "clear_thumbnail_cache",
    "normalize_sample_bytes", "output_thumb_cache_path", "purge_output_thumbnails",
    "sample_jpeg_bytes", "sha256_bytes", "sha256_file", "store_sample_bytes",
    "store_sample_image",
]
