"""Ultralytics wrapper for the bundled reference pipeline.

Detection is the user's job (see contract.py); this exists so the shipped
`pipelines/ultralytics.py` reference has something to call, and so that a
script copied from it has a working starting point. Nothing in the dataset
path imports this — the host reaches only for geometry.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(slots=True)
class Detection:
    """One detector output in absolute frame pixels."""

    box: np.ndarray  # xyxy, float32
    conf: float
    cls: int


def letterbox_canvas(image: np.ndarray, size: int, grey: int = 114) -> np.ndarray:
    """Place a crop on a square detector canvas without upscaling its texture."""
    height, width = image.shape[:2]
    scale = min(1.0, size / max(width, 1), size / max(height, 1))
    if scale < 1.0:
        image = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))),
                           interpolation=cv2.INTER_AREA)
        height, width = image.shape[:2]
    canvas = np.full((size, size, 3), grey, np.uint8)
    left, top = (size - width) // 2, (size - height) // 2
    canvas[top:top + height, left:left + width] = image
    return canvas


class Detector:
    """Thin Ultralytics wrapper returning plain numpy detections."""

    def __init__(self, weights: Path, *, imgsz: int = 640, conf: float = 0.25,
                 nms_iou: float = 0.45, device: str = "cpu",
                 classes: list[int] | None = None):
        try:
            from ultralytics import YOLO
        except ImportError as error:  # pragma: no cover - depends on environment
            raise SystemExit(
                "ultralytics is required for this pipeline: "
                "pip install 'reid-annotation-tool[extract,ultralytics]'"
            ) from error
        self.model = YOLO(str(weights))
        self.weights = Path(weights)
        self.imgsz, self.conf, self.nms_iou = imgsz, conf, nms_iou
        self.device, self.classes = device, classes

    def predict(self, frames: list[np.ndarray], *, imgsz: int | None = None,
                conf: float | None = None) -> list[list[Detection]]:
        if not frames:
            return []
        results = self.model.predict(
            frames, imgsz=imgsz or self.imgsz, conf=self.conf if conf is None else conf,
            iou=self.nms_iou, classes=self.classes, device=self.device, verbose=False)
        output = []
        for result in results:
            boxes = result.boxes.xyxy.cpu().numpy().astype(np.float32)
            confs = result.boxes.conf.cpu().numpy().astype(np.float32)
            classes = result.boxes.cls.cpu().numpy().astype(int)
            output.append([Detection(box, float(score), int(label))
                           for box, score, label in zip(boxes, confs, classes)])
        return output

    def class_names(self) -> dict[int, str]:
        names = getattr(self.model, "names", {}) or {}
        return {int(key): str(value) for key, value in names.items()}
