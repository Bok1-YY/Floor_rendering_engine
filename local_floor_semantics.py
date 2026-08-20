# -*- coding: utf-8 -*-
"""Offline open-vocabulary floor semantics using quantized CLIPSeg ONNX."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps


MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "clipseg")
MODEL_PATH = os.path.join(MODEL_DIR, "clipseg_rd64_quantized.onnx")
TOKENIZER_PATH = os.path.join(MODEL_DIR, "tokenizer.json")
MODEL_VERSION = "clipseg-rd64-refined-quantized-floor-v1"
INPUT_SIDE = 352
FLOOR_PROMPTS = ("floor", "flooring")


@dataclass(frozen=True)
class FloorSemanticResult:
    probability: np.ndarray | None
    status: str
    model: str
    prompts: tuple[str, ...]
    error: str = ""
    generic_probability: np.ndarray | None = None
    context_probability: np.ndarray | None = None


class _ClipSegRuntime:
    def __init__(self) -> None:
        self._session = None
        self._tokenizer = None
        self._tokens = None
        self._error = ""
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._session is not None or self._error:
            return
        with self._load_lock:
            if self._session is not None or self._error:
                return
            if not os.path.isfile(MODEL_PATH) or not os.path.isfile(TOKENIZER_PATH):
                self._error = "CLIPSeg 本地地板语义模型未安装"
                return
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                options = ort.SessionOptions()
                options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
                options.inter_op_num_threads = 1
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._session = ort.InferenceSession(
                    MODEL_PATH, sess_options=options, providers=["CPUExecutionProvider"])
                self._tokenizer = Tokenizer.from_file(TOKENIZER_PATH)
                encoded = [self._tokenizer.encode(value).ids for value in FLOOR_PROMPTS]
                length = max(len(row) for row in encoded)
                input_ids = np.full((len(encoded), length), 49407, dtype=np.int64)
                attention = np.zeros_like(input_ids)
                for index, row in enumerate(encoded):
                    input_ids[index, :len(row)] = row
                    attention[index, :len(row)] = 1
                self._tokens = (input_ids, attention)
            except Exception as exc:  # pragma: no cover - host/model dependent
                self._error = f"CLIPSeg 加载失败：{exc}"

    @property
    def available(self) -> bool:
        self._ensure_loaded()
        return self._session is not None

    @property
    def error(self) -> str:
        self._ensure_loaded()
        return self._error

    @staticmethod
    def _pixels(image: Image.Image) -> np.ndarray:
        rgb = np.asarray(
            ImageOps.exif_transpose(image).convert("RGB").resize(
                (INPUT_SIDE, INPUT_SIDE), Image.Resampling.BICUBIC),
            dtype=np.float32) / 255.0
        mean = np.array((.48145466, .4578275, .40821073), dtype=np.float32)
        std = np.array((.26862954, .26130258, .27577711), dtype=np.float32)
        return np.transpose((rgb - mean) / std, (2, 0, 1))[None].astype(np.float32)

    def predict(self, image: Image.Image) -> FloorSemanticResult:
        self._ensure_loaded()
        if self._session is None or self._tokens is None:
            return FloorSemanticResult(None, "unavailable", MODEL_VERSION, FLOOR_PROMPTS, self._error)
        input_ids, attention = self._tokens
        try:
            with self._inference_lock:
                raw = self._session.run(None, {
                    "input_ids": input_ids,
                    "attention_mask": attention,
                    "pixel_values": self._pixels(image),
                })[0]
            logits = np.asarray(raw, dtype=np.float32)
            if logits.ndim == 2:
                logits = logits[None]
            if logits.ndim != 3 or logits.shape[0] != len(FLOOR_PROMPTS):
                raise ValueError(f"语义模型输出形状无效：{tuple(logits.shape)}")
            prompt_probability = 1.0 / (1.0 + np.exp(-np.clip(logits, -30.0, 30.0)))
            generic_floor = np.maximum(prompt_probability[0], prompt_probability[1])
            probability = generic_floor
            def resize(value: np.ndarray) -> np.ndarray:
                return np.clip(
                    cv2.resize(value, image.size, interpolation=cv2.INTER_CUBIC), 0.0, 1.0
                ).astype(np.float32)
            return FloorSemanticResult(
                resize(probability), "ok", MODEL_VERSION, FLOOR_PROMPTS,
                generic_probability=resize(generic_floor),
                context_probability=None,
            )
        except Exception as exc:
            return FloorSemanticResult(None, "failed", MODEL_VERSION, FLOOR_PROMPTS, str(exc))


_RUNTIME = _ClipSegRuntime()


def predict_floor_semantics(image: Image.Image) -> FloorSemanticResult:
    return _RUNTIME.predict(image)


def floor_semantic_model_status() -> dict:
    return {
        "model": MODEL_VERSION,
        "path": MODEL_PATH,
        "available": _RUNTIME.available,
        "error": _RUNTIME.error,
        "prompts": list(FLOOR_PROMPTS),
    }


__all__ = [
    "FLOOR_PROMPTS", "MODEL_PATH", "MODEL_VERSION", "FloorSemanticResult",
    "floor_semantic_model_status", "predict_floor_semantics",
]
