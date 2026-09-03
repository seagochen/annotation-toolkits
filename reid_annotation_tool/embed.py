"""ONNX ReID embedding with deployment-consistent preprocessing.

``letterbox-bgr`` reproduces the Jetson engine's GPU preprocessing (letterbox on
grey 114, /255, BGR channel order, no ImageNet normalisation, bilinear sampling
without the half-pixel offset), so a candidate ranked here is ranked with the
same pixels the deployed model will see. ``imagenet-rgb`` is provided for
third-party checkpoints that were trained the conventional way.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np

PREPROCESS = ("letterbox-bgr", "imagenet-rgb")
LETTERBOX_GREY = 114
_MEAN = np.asarray([0.485, 0.456, 0.406], np.float32).reshape(3, 1, 1)
_STD = np.asarray([0.229, 0.224, 0.225], np.float32).reshape(3, 1, 1)
_F32 = np.float32


def letterbox_bgr(image: np.ndarray, size: int) -> np.ndarray:
    """BGR uint8 HWC -> CHW float32, bit-compatible with the engine kernel."""
    height, width = image.shape[:2]
    scale = min(_F32(size) / _F32(width), _F32(size) / _F32(height))
    scaled_w, scaled_h = int(_F32(width) * scale), int(_F32(height) * scale)
    pad_x, pad_y = (size - scaled_w) // 2, (size - scaled_h) // 2
    canvas = np.full((size, size, 3), LETTERBOX_GREY / 255.0, _F32)
    if scaled_w > 0 and scaled_h > 0:
        source = image.astype(_F32) / 255.0
        ys = np.arange(scaled_h, dtype=_F32) / scale
        xs = np.arange(scaled_w, dtype=_F32) / scale
        y0, x0 = ys.astype(np.int32), xs.astype(np.int32)
        y1 = np.minimum(y0 + 1, height - 1)
        x1 = np.minimum(x0 + 1, width - 1)
        dy, dx = (ys - y0)[:, None, None], (xs - x0)[None, :, None]
        top = source[y0][:, x0] * (1 - dx) + source[y0][:, x1] * dx
        bottom = source[y1][:, x0] * (1 - dx) + source[y1][:, x1] * dx
        canvas[pad_y:pad_y + scaled_h, pad_x:pad_x + scaled_w] = top * (1 - dy) + bottom * dy
    return np.ascontiguousarray(canvas.transpose(2, 0, 1))


def imagenet_rgb(image: np.ndarray, size: int) -> np.ndarray:
    resized = cv2.resize(image, (size, size), interpolation=cv2.INTER_LINEAR)
    tensor = resized[:, :, ::-1].astype(_F32).transpose(2, 0, 1) / 255.0
    return np.ascontiguousarray((tensor - _MEAN) / _STD)


def preprocess_image(image: np.ndarray, size: int, mode: str) -> np.ndarray:
    if mode == "letterbox-bgr":
        return letterbox_bgr(image, size)
    if mode == "imagenet-rgb":
        return imagenet_rgb(image, size)
    raise ValueError(f"Unsupported preprocess mode: {mode!r}")


def normalize_rows(features: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(features, axis=1, keepdims=True)
    return features / np.maximum(norms, 1e-12)


class ReidEmbedder:
    """Batched ONNX Runtime embedder with an on-disk cache keyed by model+preprocess."""

    def __init__(self, model: Path, *, size: int = 224, preprocess: str = "letterbox-bgr",
                 provider: str = "auto", batch_size: int = 64):
        try:
            import onnxruntime as ort
        except ImportError as error:  # pragma: no cover - depends on environment
            raise SystemExit(
                "onnxruntime is required for ReID mining: pip install 'reid-annotation-tool[extract]'"
            ) from error
        if preprocess not in PREPROCESS:
            raise ValueError(f"preprocess must be one of {PREPROCESS}")
        available = ort.get_available_providers()
        wanted = {"auto": ["CUDAExecutionProvider", "CPUExecutionProvider"],
                  "cuda": ["CUDAExecutionProvider", "CPUExecutionProvider"],
                  "cpu": ["CPUExecutionProvider"]}[provider]
        self.session = ort.InferenceSession(
            str(model), providers=[name for name in wanted if name in available])
        self.input_name = self.session.get_inputs()[0].name
        self.model, self.size = Path(model), size
        self.preprocess, self.batch_size = preprocess, batch_size
        self.model_digest = hashlib.sha256(self.model.read_bytes()).hexdigest()[:16]

    def signature(self) -> str:
        return f"{self.model_digest}-{self.preprocess}-{self.size}"

    def embed_images(self, images: list[np.ndarray]) -> np.ndarray:
        """Embed in-memory BGR crops (used during tracking)."""
        if not images:
            return np.zeros((0, 0), np.float32)
        output = []
        for start in range(0, len(images), self.batch_size):
            batch = np.stack([preprocess_image(image, self.size, self.preprocess)
                              for image in images[start:start + self.batch_size]])
            output.append(self.session.run(None, {self.input_name: batch})[0])
        return normalize_rows(np.concatenate(output).astype(np.float32))

    def embed_paths(self, root: Path, paths: list[str], *, cache: Path | None = None,
                    progress: bool = True) -> dict[str, np.ndarray]:
        stored: dict[str, np.ndarray] = {}
        if cache is not None and cache.is_file():
            with np.load(cache, allow_pickle=False) as data:
                if str(data["signature"]) == self.signature():
                    stored = dict(zip(data["paths"].tolist(), data["features"]))
        missing = [path for path in paths if path not in stored]
        for start in range(0, len(missing), self.batch_size):
            names = missing[start:start + self.batch_size]
            images = []
            for name in names:
                image = cv2.imread(str(root / name), cv2.IMREAD_COLOR)
                if image is None:
                    raise FileNotFoundError(f"Unreadable crop: {root / name}")
                images.append(image)
            features = self.embed_images(images)
            stored.update(zip(names, features))
            if progress:
                print(f"embedded {min(start + len(names), len(missing))}/{len(missing)}",
                      flush=True)
        if cache is not None and missing:
            cache.parent.mkdir(parents=True, exist_ok=True)
            ordered = sorted(stored)
            temporary = cache.with_suffix(cache.suffix + ".tmp")
            with temporary.open("wb") as handle:  # a file object stops np.savez appending .npz
                np.savez(handle, signature=self.signature(), paths=np.asarray(ordered),
                         features=np.stack([stored[name] for name in ordered]))
            temporary.replace(cache)
        return {path: stored[path] for path in paths}


def centroid(features: list[np.ndarray]) -> np.ndarray:
    value = np.mean(np.stack(features), axis=0)
    return value / max(float(np.linalg.norm(value)), 1e-12)


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(left, right))
