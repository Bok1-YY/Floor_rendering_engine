"""Offline floor-mask segmentation for color correction.

MobileSAM/ONNX supplies the semantic boundary proposal.  User foreground and
background strokes are hard constraints, and OpenCV GrabCut performs a final
edge-aware refinement.  The module remains usable without the model: painted
foreground is then treated as an exact/manual mask instead of silently
falling back to global color correction.
"""
from __future__ import annotations

import base64
import io
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps


MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "mobile_sam")
ENCODER_PATH = os.path.join(MODEL_DIR, "mobile_sam_encoder.onnx")
DECODER_PATH = os.path.join(MODEL_DIR, "mobile_sam_decoder.onnx")
SEGMENT_MAX_SIDE = 1600
OBJECT_SEGMENT_MAX_SIDE = 1280
SAM_INPUT_SIDE = 1024
MAX_PROMPT_POINTS = 32
OBJECT_SCAN_COLS = 8
OBJECT_SCAN_ROWS = 6
OBJECT_SCAN_MAX_CANDIDATES = 24


def _resize_working(image: Image.Image, max_side: int = SEGMENT_MAX_SIDE) -> Image.Image:
    out = ImageOps.exif_transpose(image).convert("RGB")
    out.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return out.copy()


def _decode_optional_mask(value: str, size: tuple[int, int]) -> np.ndarray | None:
    if not (value or "").strip():
        return None
    raw_value = value.split(",", 1)[-1]
    try:
        raw = base64.b64decode(raw_value, validate=True)
        if not raw or len(raw) > 12 * 1024 * 1024:
            return None
        with Image.open(io.BytesIO(raw)) as image:
            mask = image.convert("L")
            if mask.size != size:
                mask = mask.resize(size, Image.Resampling.NEAREST)
            return np.asarray(mask, dtype=np.uint8) >= 128
    except Exception:
        return None


def encode_mask_png(mask: np.ndarray) -> str:
    image = Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), mode="L")
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _sample_prompt_points(mask: np.ndarray | None, limit: int) -> np.ndarray:
    if mask is None or not mask.any():
        return np.empty((0, 2), dtype=np.float32)
    ys, xs = np.nonzero(mask)
    if len(xs) > limit:
        indexes = np.linspace(0, len(xs) - 1, limit, dtype=np.int64)
        xs, ys = xs[indexes], ys[indexes]
    return np.stack((xs, ys), axis=1).astype(np.float32)


class _MobileSAMRuntime:
    """Lazy CPU ONNX runtime with a tiny embedding LRU."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._encoder = None
        self._decoder = None
        self._error = ""
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()

    @property
    def available(self) -> bool:
        self._ensure_loaded()
        return self._encoder is not None and self._decoder is not None

    @property
    def error(self) -> str:
        return self._error

    def _ensure_loaded(self) -> None:
        if self._encoder is not None or self._error:
            return
        with self._lock:
            if self._encoder is not None or self._error:
                return
            if not os.path.isfile(ENCODER_PATH) or not os.path.isfile(DECODER_PATH):
                self._error = "MobileSAM 模型文件未安装"
                return
            try:
                import onnxruntime as ort

                options = ort.SessionOptions()
                options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
                options.inter_op_num_threads = 1
                self._encoder = ort.InferenceSession(
                    ENCODER_PATH, sess_options=options, providers=["CPUExecutionProvider"])
                self._decoder = ort.InferenceSession(
                    DECODER_PATH, sess_options=options, providers=["CPUExecutionProvider"])
            except Exception as exc:
                self._error = f"MobileSAM 加载失败：{exc}"

    def _preprocess(self, image: Image.Image) -> tuple[np.ndarray, float]:
        rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
        height, width = rgb.shape[:2]
        scale = SAM_INPUT_SIDE / max(height, width)
        new_width = max(1, int(round(width * scale)))
        new_height = max(1, int(round(height * scale)))
        resized = cv2.resize(rgb, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
        encoder_input = self._encoder.get_inputs()[0]
        # The bundled Acly/MobileSAM encoder contains normalization, CHW
        # conversion and padding and therefore accepts resized HWC RGB. Keep
        # compatibility with a raw NCHW encoder exported from the official repo.
        if len(encoder_input.shape) == 3 and encoder_input.shape[-1] == 3:
            return resized.astype(np.float32), scale
        normalized = (resized.astype(np.float32) - np.array(
            [123.675, 116.28, 103.53], dtype=np.float32)) / np.array(
                [58.395, 57.12, 57.375], dtype=np.float32)
        padded = np.zeros((SAM_INPUT_SIDE, SAM_INPUT_SIDE, 3), dtype=np.float32)
        padded[:new_height, :new_width] = normalized
        return np.transpose(padded, (2, 0, 1))[None], scale

    def _embedding(self, image: Image.Image, cache_key: str) -> tuple[np.ndarray, float]:
        tensor, scale = self._preprocess(image)
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                return cached, scale
            input_name = self._encoder.get_inputs()[0].name
            embedding = self._encoder.run(None, {input_name: tensor})[0]
            self._cache[cache_key] = embedding
            while len(self._cache) > 3:
                self._cache.popitem(last=False)
            return embedding, scale

    def predict_raw(self, image: Image.Image, cache_key: str, points: np.ndarray,
                    labels: np.ndarray, previous: np.ndarray | None = None) -> tuple[np.ndarray, list[float]]:
        self._ensure_loaded()
        if not self.available:
            return np.empty((0, image.height, image.width), dtype=np.float32), []
        embedding, scale = self._embedding(image, cache_key)
        height, width = image.height, image.width
        point_coords = (points * scale)[None].astype(np.float32)
        point_labels = labels[None].astype(np.float32)
        if previous is None:
            mask_input = np.zeros((1, 1, 256, 256), dtype=np.float32)
            has_mask_input = np.zeros((1,), dtype=np.float32)
        else:
            small = cv2.resize(previous.astype(np.float32), (256, 256), interpolation=cv2.INTER_LINEAR)
            mask_input = ((small * 20.0) - 10.0)[None, None].astype(np.float32)
            has_mask_input = np.ones((1,), dtype=np.float32)
        feed = {
            "image_embeddings": embedding,
            "point_coords": point_coords,
            "point_labels": point_labels,
            "mask_input": mask_input,
            "has_mask_input": has_mask_input,
            "orig_im_size": np.array([height, width], dtype=np.float32),
        }
        outputs = self._decoder.run(None, feed)
        masks = np.asarray(outputs[0][0], dtype=np.float32)
        scores = outputs[1][0]
        return masks, [float(score) for score in scores]

    def predict(self, image: Image.Image, cache_key: str, points: np.ndarray,
                labels: np.ndarray, previous: np.ndarray | None = None) -> tuple[list[np.ndarray], list[float]]:
        masks, scores = self.predict_raw(image, cache_key, points, labels, previous)
        return [(mask > 0) for mask in masks], scores


_RUNTIME = _MobileSAMRuntime()
_INFERENCE_LOCK = threading.Lock()
_OBJECT_SCAN_CACHE_LOCK = threading.Lock()
_OBJECT_SCAN_CACHE: OrderedDict[str, tuple[tuple[int, int], list["MaskCandidate"]]] = OrderedDict()


@dataclass
class SegmentResult:
    mask: np.ndarray | None
    confidence: float
    status: str
    warnings: list[str]
    model: str


@dataclass
class MaskCandidate:
    """Compact, frontend-friendly binary mask candidate (row-major uncompressed RLE)."""

    id: str
    rle: list[int]
    bbox: tuple[int, int, int, int]
    area: int
    confidence: float
    stability: float


@dataclass
class SmartSegmentResult:
    size: tuple[int, int]
    candidates: list[MaskCandidate]
    status: str
    warnings: list[str]
    model: str = "mobile_sam"


def encode_mask_rle(mask: np.ndarray) -> list[int]:
    """Encode a boolean mask as alternating 0/1 run lengths, starting with zeros."""
    flat = np.asarray(mask, dtype=bool).reshape(-1)
    if flat.size == 0:
        return []
    changes = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    bounds = np.concatenate((np.array([0]), changes, np.array([flat.size])))
    runs = np.diff(bounds).astype(np.int64).tolist()
    if bool(flat[0]):
        runs.insert(0, 0)
    return runs


def decode_mask_rle(rle: list[int], size: tuple[int, int]) -> np.ndarray:
    """Decode row-major alternating RLE. Primarily used by tests and cache consumers."""
    width, height = size
    total = max(0, int(width) * int(height))
    flat = np.zeros(total, dtype=bool)
    offset = 0
    value = False
    for raw_count in rle:
        count = max(0, int(raw_count))
        end = min(total, offset + count)
        if value and end > offset:
            flat[offset:end] = True
        offset += count
        value = not value
        if offset >= total:
            break
    return flat.reshape((height, width))


def _mask_stability(logits: np.ndarray, offset: float = 1.0) -> float:
    inner = int(np.count_nonzero(logits > offset))
    outer = int(np.count_nonzero(logits > -offset))
    return float(inner / outer) if outer else 0.0


def _component_at_point(mask: np.ndarray, x: int, y: int) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    if count <= 1:
        return mask
    label = int(labels[max(0, min(mask.shape[0] - 1, y)), max(0, min(mask.shape[1] - 1, x))])
    if label <= 0:
        label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return labels == label


def _candidate_from_mask(candidate_id: str, mask: np.ndarray, confidence: float,
                         stability: float) -> MaskCandidate:
    ys, xs = np.nonzero(mask)
    left, top = int(xs.min()), int(ys.min())
    right, bottom = int(xs.max()) + 1, int(ys.max()) + 1
    return MaskCandidate(
        id=candidate_id,
        rle=encode_mask_rle(mask),
        bbox=(left, top, right - left, bottom - top),
        area=int(mask.sum()),
        confidence=max(0.0, min(1.0, float(confidence))),
        stability=max(0.0, min(1.0, float(stability))),
    )


def _overlaps_existing(mask: np.ndarray, area: int,
                       existing: list[tuple[np.ndarray, int, float, float]]) -> bool:
    for other, other_area, _, _ in existing:
        intersection = int(np.count_nonzero(mask & other))
        if not intersection:
            continue
        union = area + other_area - intersection
        iou = intersection / max(1, union)
        containment = intersection / max(1, min(area, other_area))
        if iou >= 0.80 or containment >= 0.92:
            return True
    return False


def scan_object_masks(image: Image.Image, cache_key: str) -> SmartSegmentResult:
    """Best-effort offline object proposal scan for inpaint selection."""
    working = _resize_working(image, OBJECT_SEGMENT_MAX_SIDE)
    size = working.size
    scoped_key = f"{cache_key}:objects:{size[0]}x{size[1]}"
    with _OBJECT_SCAN_CACHE_LOCK:
        cached = _OBJECT_SCAN_CACHE.get(scoped_key)
        if cached is not None:
            _OBJECT_SCAN_CACHE.move_to_end(scoped_key)
            cached_size, candidates = cached
            return SmartSegmentResult(cached_size, list(candidates), "ok", [])

    warnings: list[str] = []
    kept: list[tuple[np.ndarray, int, float, float]] = []
    with _INFERENCE_LOCK:
        available = _RUNTIME.available
    if not available:
        warnings.append(_RUNTIME.error or "AI 蒙版当前不可用")
    else:
        width, height = size
        min_area = max(64, int(width * height * 0.001))
        max_area = int(width * height * 0.60)
        for row in range(OBJECT_SCAN_ROWS):
            for col in range(OBJECT_SCAN_COLS):
                x = int(round((col + 0.5) * width / OBJECT_SCAN_COLS))
                y = int(round((row + 0.5) * height / OBJECT_SCAN_ROWS))
                # Yield the shared runtime lock between grid points so an
                # explicit user click can be served before the background scan ends.
                with _INFERENCE_LOCK:
                    logits, scores = _RUNTIME.predict_raw(
                        working, scoped_key,
                        np.array([[x, y]], dtype=np.float32),
                        np.ones((1,), dtype=np.float32),
                    )
                ranked = sorted(
                    zip(logits, scores),
                    key=lambda item: float(item[1]), reverse=True,
                )
                for raw_mask, score in ranked:
                    stability = _mask_stability(raw_mask)
                    if score < 0.70 or stability < 0.82:
                        continue
                    mask = _component_at_point(raw_mask > 0, x, y)
                    area = int(mask.sum())
                    if area < min_area or area > max_area:
                        continue
                    if _overlaps_existing(mask, area, kept):
                        continue
                    kept.append((mask, area, float(score), stability))

    if not kept:
        return SmartSegmentResult(size, [], "needs_guidance",
                                  warnings or ["未识别到可选物件，请使用画笔涂抹"], "mobile_sam")

    kept.sort(key=lambda item: (-item[2], -item[3], item[1]))
    kept = kept[:OBJECT_SCAN_MAX_CANDIDATES]
    candidates = [
        _candidate_from_mask(f"object-{index + 1}", mask, confidence, stability)
        for index, (mask, _, confidence, stability) in enumerate(kept)
    ]
    with _OBJECT_SCAN_CACHE_LOCK:
        _OBJECT_SCAN_CACHE[scoped_key] = (size, list(candidates))
        while len(_OBJECT_SCAN_CACHE) > 3:
            _OBJECT_SCAN_CACHE.popitem(last=False)
    return SmartSegmentResult(size, candidates, "ok", warnings, "mobile_sam")


def segment_mask_at_point(image: Image.Image, cache_key: str, x: float, y: float) -> SmartSegmentResult:
    """Return the best MobileSAM region containing one normalized click point."""
    working = _resize_working(image, OBJECT_SEGMENT_MAX_SIDE)
    width, height = working.size
    px = max(0, min(width - 1, int(round(float(x) * (width - 1)))))
    py = max(0, min(height - 1, int(round(float(y) * (height - 1)))))
    scoped_key = f"{cache_key}:objects:{width}x{height}"
    warnings: list[str] = []
    choices: list[tuple[np.ndarray, int, float, float]] = []
    with _INFERENCE_LOCK:
        if not _RUNTIME.available:
            warnings.append(_RUNTIME.error or "AI 蒙版当前不可用")
        else:
            logits, scores = _RUNTIME.predict_raw(
                working, scoped_key,
                np.array([[px, py]], dtype=np.float32),
                np.ones((1,), dtype=np.float32),
            )
            max_area = int(width * height * 0.95)
            min_area = max(16, int(width * height * 0.0002))
            for raw_mask, score in zip(logits, scores):
                mask = _component_at_point(raw_mask > 0, px, py)
                area = int(mask.sum())
                if min_area <= area <= max_area:
                    choices.append((mask, area, float(score), _mask_stability(raw_mask)))
    if not choices:
        return SmartSegmentResult((width, height), [], "needs_guidance",
                                  warnings or ["该位置未识别到有效区域，请换个位置或使用画笔"],
                                  "mobile_sam")
    choices.sort(key=lambda item: (item[2] * 0.75 + item[3] * 0.25), reverse=True)
    mask, _, confidence, stability = choices[0]
    return SmartSegmentResult(
        (width, height),
        [_candidate_from_mask("point-region", mask, confidence, stability)],
        "ok", warnings, "mobile_sam",
    )


def _auto_candidate(image: Image.Image, cache_key: str) -> tuple[np.ndarray | None, float]:
    height, width = image.height, image.width
    best_mask = None
    best_rank = -1.0
    best_confidence = 0.0
    for nx, ny in ((0.50, 0.86), (0.25, 0.82), (0.75, 0.82)):
        points = np.array([[nx * width, ny * height]], dtype=np.float32)
        masks, scores = _RUNTIME.predict(
            image, cache_key, points, np.ones((1,), dtype=np.float32))
        for mask, predicted_iou in zip(masks, scores):
            coverage = float(mask.mean())
            if coverage < 0.02 or coverage > 0.85:
                continue
            lower_fraction = float(mask[height // 2:].sum()) / max(1.0, float(mask.sum()))
            upper_fraction = float(mask[:height // 4].sum()) / max(1.0, float(mask.sum()))
            bottom_contact = float(mask[-max(1, height // 50):].mean())
            rank = predicted_iou + 0.15 * lower_fraction + 0.10 * bottom_contact - 0.25 * upper_fraction
            if rank > best_rank:
                best_rank, best_mask = rank, mask
                best_confidence = predicted_iou
    if best_rank < 0.65:
        return None, max(0.0, min(1.0, best_confidence))
    return best_mask, max(0.0, min(1.0, best_confidence))


def _refine_grabcut(image: Image.Image, proposal: np.ndarray,
                    positive: np.ndarray | None, negative: np.ndarray | None) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    seed = np.full(proposal.shape, cv2.GC_PR_BGD, dtype=np.uint8)
    seed[proposal] = cv2.GC_PR_FGD
    core = cv2.erode(proposal.astype(np.uint8), np.ones((5, 5), np.uint8), iterations=1) > 0
    seed[core] = cv2.GC_FGD
    if positive is not None:
        seed[positive] = cv2.GC_FGD
    if negative is not None:
        seed[negative] = cv2.GC_BGD
    bg_model = np.zeros((1, 65), dtype=np.float64)
    fg_model = np.zeros((1, 65), dtype=np.float64)
    try:
        cv2.grabCut(rgb, seed, None, bg_model, fg_model, 3, cv2.GC_INIT_WITH_MASK)
        refined = (seed == cv2.GC_FGD) | (seed == cv2.GC_PR_FGD)
    except cv2.error:
        refined = proposal.copy()
    if positive is not None:
        refined[positive] = True
    if negative is not None:
        refined[negative] = False
    return refined


def _remove_small_islands(mask: np.ndarray, positive: np.ndarray | None) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    minimum = max(16, int(mask.size * 0.0005))
    cleaned = np.zeros_like(mask, dtype=bool)
    for index in range(1, count):
        component = labels == index
        keep = int(stats[index, cv2.CC_STAT_AREA]) >= minimum
        if positive is not None and np.any(component & positive):
            keep = True
        if keep:
            cleaned |= component
    return cleaned


def segment_floor(image: Image.Image, cache_key: str, *, positive_b64: str = "",
                  negative_b64: str = "", previous_b64: str = "",
                  auto_seed: bool = True) -> tuple[Image.Image, SegmentResult]:
    working = _resize_working(image)
    size = working.size
    positive = _decode_optional_mask(positive_b64, size)
    negative = _decode_optional_mask(negative_b64, size)
    previous = _decode_optional_mask(previous_b64, size)
    warnings: list[str] = []
    proposal = None
    confidence = 0.0
    painted_fallback = False

    with _INFERENCE_LOCK:
        if _RUNTIME.available:
            pos_points = _sample_prompt_points(positive, MAX_PROMPT_POINTS)
            neg_points = _sample_prompt_points(negative, MAX_PROMPT_POINTS)
            if len(pos_points):
                points = np.concatenate((pos_points, neg_points), axis=0)
                labels = np.concatenate((np.ones(len(pos_points)), np.zeros(len(neg_points)))).astype(np.float32)
                masks, scores = _RUNTIME.predict(working, cache_key, points, labels, previous)
                if masks:
                    best = int(np.argmax(scores))
                    proposal, confidence = masks[best], max(0.0, min(1.0, scores[best]))
            elif auto_seed:
                proposal, confidence = _auto_candidate(working, cache_key)
        else:
            warnings.append(_RUNTIME.error or "AI 蒙版当前不可用")

    if proposal is None and previous is not None:
        proposal = previous.copy()
    if proposal is None and positive is not None and positive.any():
        proposal = positive.copy()
        painted_fallback = True
        warnings.append("AI 未生成有效结果，已使用手工涂抹区域")
    if proposal is None:
        empty = Image.new("L", size, 0)
        return working, SegmentResult(None, confidence, "needs_guidance", warnings, "mobile_sam")

    # Without a semantic proposal, colour-only GrabCut can flood a uniform room.
    # Keep the user's paint exact: strict locality is more important than guessing.
    refined = proposal.copy() if painted_fallback else _refine_grabcut(
        working, proposal, positive, negative)
    if negative is not None:
        refined[negative] = False
    refined = _remove_small_islands(refined, positive)
    if not refined.any():
        return working, SegmentResult(None, confidence, "needs_guidance",
                                      warnings + ["未识别到有效地板，请增加地板笔触"], "mobile_sam")
    return working, SegmentResult(refined, confidence, "ok", warnings, "mobile_sam")


__all__ = [
    "segment_floor", "encode_mask_png", "SEGMENT_MAX_SIDE", "SegmentResult",
    "scan_object_masks", "segment_mask_at_point", "encode_mask_rle", "decode_mask_rle",
    "MaskCandidate", "SmartSegmentResult", "OBJECT_SEGMENT_MAX_SIDE",
]
