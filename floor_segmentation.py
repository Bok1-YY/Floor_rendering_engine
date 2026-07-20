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
SAM_INPUT_SIDE = 1024
MAX_PROMPT_POINTS = 32


def _resize_working(image: Image.Image) -> Image.Image:
    out = ImageOps.exif_transpose(image).convert("RGB")
    out.thumbnail((SEGMENT_MAX_SIDE, SEGMENT_MAX_SIDE), Image.Resampling.LANCZOS)
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

    def predict(self, image: Image.Image, cache_key: str, points: np.ndarray,
                labels: np.ndarray, previous: np.ndarray | None = None) -> tuple[list[np.ndarray], list[float]]:
        self._ensure_loaded()
        if not self.available:
            return [], []
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
        masks = outputs[0][0]
        scores = outputs[1][0]
        return [(mask > 0) for mask in masks], [float(score) for score in scores]


_RUNTIME = _MobileSAMRuntime()
_INFERENCE_LOCK = threading.Lock()


@dataclass
class SegmentResult:
    mask: np.ndarray | None
    confidence: float
    status: str
    warnings: list[str]
    model: str


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


__all__ = ["segment_floor", "encode_mask_png", "SEGMENT_MAX_SIDE", "SegmentResult"]
