# -*- coding: utf-8 -*-
"""Offline relative-depth helper used by panorama floor/geometry analysis.

The runtime intentionally exposes only normalized relative depth and edge
confidence.  Depth Anything V2 Small is not a metric sensor: callers must not
turn its values into room dimensions.  The model is loaded lazily through the
same CPU-only ONNXRuntime stack already used by MobileSAM.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps


MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "depth_anything_v2")
MODEL_PATH = os.path.join(MODEL_DIR, "depth_anything_v2_small.onnx")
MODEL_VERSION = "depth-anything-v2-small-onnx-v1"
MODEL_INPUT_SIDE = 518


@dataclass(frozen=True)
class RelativeDepthResult:
    depth: np.ndarray | None
    edge: np.ndarray | None
    status: str
    model: str
    error: str = ""


class _DepthAnythingRuntime:
    """Lazy, process-wide ONNX session with serialized CPU inference."""

    def __init__(self) -> None:
        self._session = None
        self._error = ""
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    @property
    def available(self) -> bool:
        self._ensure_loaded()
        return self._session is not None

    @property
    def error(self) -> str:
        self._ensure_loaded()
        return self._error

    def _ensure_loaded(self) -> None:
        if self._session is not None or self._error:
            return
        with self._load_lock:
            if self._session is not None or self._error:
                return
            if not os.path.isfile(MODEL_PATH):
                self._error = "Depth Anything V2 Small 模型文件未安装"
                return
            try:
                import onnxruntime as ort

                options = ort.SessionOptions()
                options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
                options.inter_op_num_threads = 1
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._session = ort.InferenceSession(
                    MODEL_PATH, sess_options=options, providers=["CPUExecutionProvider"])
            except Exception as exc:  # pragma: no cover - depends on host ORT/model
                self._error = f"Depth Anything V2 Small 加载失败：{exc}"

    @staticmethod
    def _preprocess(image: Image.Image) -> np.ndarray:
        rgb = np.asarray(ImageOps.exif_transpose(image).convert("RGB"), dtype=np.uint8)
        resized = cv2.resize(
            rgb, (MODEL_INPUT_SIDE, MODEL_INPUT_SIDE), interpolation=cv2.INTER_CUBIC)
        value = resized.astype(np.float32) / 255.0
        value = (value - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array(
            [0.229, 0.224, 0.225], dtype=np.float32)
        return np.transpose(value, (2, 0, 1))[None].astype(np.float32)

    def predict(self, image: Image.Image) -> RelativeDepthResult:
        self._ensure_loaded()
        if self._session is None:
            return RelativeDepthResult(None, None, "unavailable", MODEL_VERSION, self._error)
        tensor = self._preprocess(image)
        try:
            input_name = self._session.get_inputs()[0].name
            with self._inference_lock:
                raw = self._session.run(None, {input_name: tensor})[0]
            depth = np.asarray(raw, dtype=np.float32).squeeze()
            if depth.ndim != 2 or not np.isfinite(depth).any():
                raise ValueError(f"模型输出形状无效：{tuple(np.asarray(raw).shape)}")
            finite = depth[np.isfinite(depth)]
            low, high = np.percentile(finite, (2.0, 98.0))
            if high - low < 1e-6:
                raise ValueError("模型输出没有有效深度层次")
            depth = np.nan_to_num((depth - low) / (high - low), nan=0.0, posinf=1.0, neginf=0.0)
            depth = np.clip(depth, 0.0, 1.0)
            depth = np.clip(
                cv2.resize(depth, image.size, interpolation=cv2.INTER_CUBIC), 0.0, 1.0
            ).astype(np.float32)
            gx = cv2.Sobel(depth, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(depth, cv2.CV_32F, 0, 1, ksize=3)
            magnitude = cv2.magnitude(gx, gy)
            scale = float(np.percentile(magnitude, 95.0))
            edge = np.clip(magnitude / max(scale, 1e-6), 0.0, 1.0).astype(np.float32)
            return RelativeDepthResult(depth, edge, "ok", MODEL_VERSION)
        except Exception as exc:
            return RelativeDepthResult(None, None, "failed", MODEL_VERSION, str(exc))


_RUNTIME = _DepthAnythingRuntime()


def predict_relative_depth(image: Image.Image) -> RelativeDepthResult:
    return _RUNTIME.predict(image)


def depth_model_status() -> dict:
    return {
        "model": MODEL_VERSION,
        "path": MODEL_PATH,
        "available": _RUNTIME.available,
        "error": _RUNTIME.error,
    }


__all__ = [
    "MODEL_PATH", "MODEL_VERSION", "RelativeDepthResult",
    "depth_model_status", "predict_relative_depth",
]
