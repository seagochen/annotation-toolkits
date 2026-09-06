"""Portable example: Ultralytics YOLO + motion/IoU tracking + optional ONNX ReID.

Point ``pipeline.script`` at this file. The detector must produce full-body
boxes; a head detector needs a scene-specific head-to-body projection before
the ReID crop is cut (see the local, gitignored ``pipeline/mitocity.py``).

ReID deliberately only *tightens* a geometrically valid association here.
``appearance_recovery`` is refused because reconnecting a track from appearance
alone would turn a model guess into an automatic same-identity training label.
"""

from __future__ import annotations

import numpy as np

from reid_annotation_tool.contract import Observation
from reid_annotation_tool.detector import Detection, letterbox_canvas
from reid_annotation_tool.embed import ReidEmbedder
from reid_annotation_tool.geometry import crop_image
from reid_annotation_tool.tracker import Tracker

_state: dict = {}


def _setup(config: dict) -> dict:
    if "model" in _state:
        return _state
    try:
        from ultralytics import YOLO
    except ImportError as error:  # pragma: no cover - environment dependency
        raise SystemExit("install ultralytics: pip install 'reid-annotation-tool[ultralytics]'") from error
    if config.get("appearance_recovery", False):
        raise ValueError("appearance_recovery must stay false when generating training tracks")
    weights = config.get("detector")
    if not weights:
        raise ValueError("pipeline.detector is required")
    _state.update({
        "model": YOLO(str(weights)),
        "classes": {int(value) for value in config.get("class_ids", [0])},
        "conf": float(config.get("conf", 0.35)),
        "neighbour_conf": float(config.get("neighbour_conf", 0.10)),
        "imgsz": int(config.get("imgsz", 1280)),
        "device": str(config.get("device", "cuda:0")),
        "n_init": int(config.get("n_init", 3)),
    })
    model = config.get("reid_onnx")
    _state["embedder"] = (ReidEmbedder(
        model, size=int(config.get("reid_input_size", 224)),
        preprocess=str(config.get("reid_preprocess", "letterbox-bgr")),
        provider=str(config.get("reid_provider", "auto")),
        batch_size=int(config.get("reid_batch_size", 64))) if model else None)
    return _state


def _detections(image: np.ndarray, config: dict, conf: float) -> list[Detection]:
    state = _setup(config)
    result = state["model"].predict(
        image, imgsz=state["imgsz"], conf=conf, classes=sorted(state["classes"]),
        device=state["device"], verbose=False)[0]
    boxes = result.boxes.xyxy.cpu().numpy().astype(np.float32)
    scores = result.boxes.conf.cpu().numpy().astype(np.float32)
    labels = result.boxes.cls.cpu().numpy().astype(int)
    return [Detection(box, float(score), int(label))
            for box, score, label in zip(boxes, scores, labels)]


def open_source(source, config: dict) -> None:
    state = _setup(config)
    state["tracker"] = Tracker(
        max_iou_distance=float(config.get("max_iou_distance", 0.5)),
        max_age=int(config.get("max_age", 15)), n_init=state["n_init"],
        appearance_weight=float(config.get("appearance_weight", 0.30)),
        max_cosine_distance=float(config.get("max_cosine_distance", 0.30)),
        appearance_recovery=False)


def process_frame(image: np.ndarray, source, config: dict) -> list[Observation]:
    state = _setup(config)
    if "tracker" not in state:
        open_source(source, config)
    detections = _detections(image, config, state["neighbour_conf"])
    targets = [item for item in detections if item.conf >= state["conf"]]
    embeddings = None
    if state["embedder"] is not None and targets:
        crops = [crop_image(image, item.box) for item in targets]
        usable = [crop if crop is not None else np.zeros((8, 8, 3), np.uint8)
                  for crop in crops]
        embeddings = list(state["embedder"].embed_images(usable))
    active = state["tracker"].update(targets, embeddings)
    confirmed = [track for track in active if track.confirmed(state["n_init"])]
    found = [Observation(track.box, track_id=track.track_id, class_id=track.cls,
                         confidence=track.conf) for track in confirmed]
    claimed = {id(track.detection) for track in confirmed if track.detection is not None}
    found.extend(Observation(item.box, class_id=item.cls, confidence=item.conf)
                 for item in detections if id(item) not in claimed)
    return found


def detect_crops(images: list[np.ndarray], config: dict) -> list[list[Observation]]:
    """Second detector pass used by the crop-contamination firewall."""
    size = int(config.get("crop_check_imgsz", 640))
    return [[Observation(item.box, class_id=item.cls, confidence=item.conf)
             for item in _detections(letterbox_canvas(image, size), config,
                                     float(config.get("crop_conf", 0.02)))]
            for image in images]
