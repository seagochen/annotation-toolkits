"""Box geometry and crop-quality primitives — the host's half of a crop.

These decide whether a rectangle is a trustworthy ReID sample: how much of a
neighbour it swallows, whether the pixels are sharp enough, whether the
exposure destroyed the texture. They belong here and not to whoever supplied
the box, because they are the dataset's contract and must not vary with the
pipeline. Nothing in this module knows what a model is.
"""

from __future__ import annotations

import cv2
import numpy as np

def area(box: np.ndarray) -> float:
    return max(float(box[2] - box[0]), 0.0) * max(float(box[3] - box[1]), 0.0)


def intersection(left: np.ndarray, right: np.ndarray) -> float:
    width = min(left[2], right[2]) - max(left[0], right[0])
    height = min(left[3], right[3]) - max(left[1], right[1])
    return max(float(width), 0.0) * max(float(height), 0.0)


def iou(left: np.ndarray, right: np.ndarray) -> float:
    overlap = intersection(left, right)
    return overlap / max(area(left) + area(right) - overlap, 1e-9)


def contained_fraction(box: np.ndarray, other: np.ndarray) -> float:
    """Fraction of ``other`` that falls inside ``box``.

    A bystander much smaller than the target can sit almost entirely inside the
    crop while scoring a low IoU, so containment is checked separately.
    """
    return intersection(box, other) / max(area(other), 1e-9)


def clip_box(box: np.ndarray, width: int, height: int) -> np.ndarray:
    value = np.asarray(box, np.float32).copy()
    value[0] = min(max(value[0], 0.0), width - 1.0)
    value[1] = min(max(value[1], 0.0), height - 1.0)
    value[2] = min(max(value[2], 0.0), width - 1.0)
    value[3] = min(max(value[3], 0.0), height - 1.0)
    return value


def expand_box(box: np.ndarray, margin: float, width: int, height: int) -> np.ndarray:
    """Grow a box by a relative margin, then clip it to the frame."""
    if margin <= 0.0:
        return clip_box(box, width, height)
    dx = (box[2] - box[0]) * margin / 2.0
    dy = (box[3] - box[1]) * margin / 2.0
    return clip_box(np.asarray([box[0] - dx, box[1] - dy,
                                box[2] + dx, box[3] + dy], np.float32), width, height)


def crop_image(frame: np.ndarray, box: np.ndarray) -> np.ndarray | None:
    x1, y1, x2, y2 = (int(round(float(value))) for value in box)
    x1, y1 = max(x1, 0), max(y1, 0)
    x2, y2 = min(x2, frame.shape[1]), min(y2, frame.shape[0])
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    return frame[y1:y2, x1:x2].copy()


def blur_laplacian_var(image: np.ndarray) -> float:
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def brightness_mean(image: np.ndarray) -> float:
    return float(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).mean())


def exposure_clip(image: np.ndarray, low: int = 8, high: int = 247) -> tuple[float, float]:
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    total = max(grey.size, 1)
    return float((grey >= high).sum()) / total, float((grey <= low).sum()) / total
