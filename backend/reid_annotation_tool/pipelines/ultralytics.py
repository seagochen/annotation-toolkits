"""Reference pipeline: Ultralytics detector + the bundled IoU tracker.

This is the turnkey path for someone who has a detector weight and no tracking
pipeline yet, and it is what this tool used to do internally before detection
and tracking became the user's job. It is a *reference*, not a default: the
config always names a pipeline explicitly, and a deployment that already runs
its own tracker should point at its own script instead — copy this file, keep
``process_frame``'s signature, and swap the middle.

Every knob lives in the ``pipeline:`` section of the project config, because
they belong to the model, not to the dataset contract::

    pipeline:
      script: ultralytics
      detector: /path/to/yolo11n.pt
      class_ids: [0]              # which class becomes an identity
      neighbour_class_ids: [0]    # which classes may veto a contaminated crop
      device: cuda:0
      imgsz: 1280
      conf: 0.35
      reid_onnx: /path/to/reid.onnx     # optional: tightens association only
      appearance_weight: 0.3

``detector`` may also be a bare name declared under the project's ``models:``
section instead of a literal path::

    models:
      head_detector: {path: /path/to/yolo11n.pt}

    pipeline:
      script: ultralytics
      detector: head_detector

This is the reference for wiring a pipeline to the named-model registry
(``reid_annotation_tool.registry.ModelRegistry``): every hook here declares
the optional fourth/third ``registry`` parameter and resolves ``detector``
through it when the name matches an entry, falling back to the literal-path
behaviour otherwise.
"""

from __future__ import annotations

import numpy as np

from reid_annotation_tool.contract import Observation
from reid_annotation_tool.detector import Detector, letterbox_canvas
from reid_annotation_tool.geometry import crop_image
from reid_annotation_tool.tracker import Tracker

# One detector and one tracker per run; the tracker is rebuilt per recording so
# track ids never bleed across videos (the host asks for that through
# open_source, which is exactly why the hook exists).
_state: dict = {}


def _int_set(config: dict, key: str, fallback) -> set[int]:
    values = config.get(key)
    if values is None:
        return set(fallback)
    return {int(value) for value in values}


def _resolve_detector(weights: str, registry) -> str:
    """A literal path, unchanged; or a name declared under `models:`, resolved
    through the registry. Membership decides which -- a name that happens to
    collide with a real file on disk still means the registry entry, since
    declaring it under `models:` is what made it a name in the first place."""
    if registry is not None and weights in registry.entries:
        return str(registry.resolve_path(weights))
    return weights


def _setup(config: dict, registry=None) -> dict:
    if "detector" in _state:
        return _state
    weights = config.get("detector")
    if not weights:
        raise SystemExit("pipeline.detector is required by the ultralytics pipeline")
    weights = _resolve_detector(str(weights), registry)
    targets = _int_set(config, "class_ids", {0})
    neighbours = _int_set(config, "neighbour_class_ids", targets)
    _state["targets"], _state["neighbours"] = targets, neighbours
    _state["conf"] = float(config.get("conf", 0.35))
    _state["neighbour_conf"] = float(config.get("neighbour_conf", 0.10))
    _state["crop_conf"] = float(config.get("crop_conf", 0.02))
    _state["crop_check_imgsz"] = int(config.get("crop_check_imgsz", 640))
    _state["crop_check_batch"] = int(config.get("crop_check_batch", 32))
    _state["n_init"] = int(config.get("n_init", 3))
    _state["detector"] = Detector(
        weights, imgsz=int(config.get("imgsz", 640)),
        conf=min(_state["conf"], _state["neighbour_conf"]),
        nms_iou=float(config.get("nms_iou", 0.45)),
        device=str(config.get("device", "cpu")),
        classes=sorted(targets | neighbours))
    _state["embedder"] = _embedder(config)
    return _state


def _embedder(config: dict):
    """The rough ReID model, only when it is actually asked to do something.

    It never creates a label — it can only tighten association, so loading it
    without appearance_weight or appearance_recovery would cost startup time
    and change nothing.
    """
    model = config.get("reid_onnx")
    wanted = (float(config.get("appearance_weight", 0.0)) > 0.0
              or bool(config.get("appearance_recovery", False)))
    if not model or not wanted:
        return None
    from reid_annotation_tool.embed import ReidEmbedder

    return ReidEmbedder(model, size=int(config.get("reid_input_size", 224)),
                        preprocess=str(config.get("reid_preprocess", "letterbox-bgr")),
                        provider=str(config.get("reid_provider", "auto")),
                        batch_size=int(config.get("reid_batch_size", 64)))


def open_source(source, config: dict, registry=None) -> None:
    """A fresh tracker per recording: ids must not survive across videos."""
    state = _setup(config, registry)
    state["tracker"] = Tracker(
        max_iou_distance=float(config.get("max_iou_distance", 0.5)),
        max_age=int(config.get("max_age", 15)),
        n_init=state["n_init"],
        appearance_weight=float(config.get("appearance_weight", 0.0)),
        max_cosine_distance=float(config.get("max_cosine_distance", 0.35)),
        appearance_recovery=bool(config.get("appearance_recovery", False)))


def process_frame(image: np.ndarray, source, config: dict, registry=None) -> list[Observation]:
    state = _setup(config, registry)
    if "tracker" not in state:
        open_source(source, config, registry)
    tracker = state["tracker"]

    detections = state["detector"].predict([image])[0]
    targets = [item for item in detections
               if item.cls in state["targets"] and item.conf >= state["conf"]]
    neighbours = [item for item in detections
                  if item.cls in state["neighbours"]
                  and item.conf >= state["neighbour_conf"]]

    embeddings = None
    if state["embedder"] is not None and targets:
        crops = [crop_image(image, item.box) for item in targets]
        usable = [crop if crop is not None else np.zeros((8, 8, 3), np.uint8)
                  for crop in crops]
        embeddings = list(state["embedder"].embed_images(usable))

    active = tracker.update(targets, embeddings)
    confirmed = [track for track in active if track.confirmed(state["n_init"])]
    found = [Observation(track.box, track_id=track.track_id, class_id=track.cls,
                         confidence=track.conf)
             for track in confirmed]

    # Every other detected object goes back untracked. The host needs them to
    # judge occlusion and co-visibility; without them a crop with a second
    # person in it looks clean.
    claimed = {id(track.detection) for track in confirmed if track.detection is not None}
    found += [Observation(item.box, class_id=item.cls, confidence=item.conf)
              for item in neighbours if id(item) not in claimed]
    return found


def detect_crops(images: list[np.ndarray], config: dict, registry=None) -> list[list[Observation]]:
    """Crop firewall, second pass: re-detect inside each finished crop.

    Only the detections are returned — whether two boxes in one crop mean the
    crop is contaminated is the host's rule, so that it cannot drift between
    pipelines.
    """
    state = _setup(config, registry)
    canvases = [letterbox_canvas(image, state["crop_check_imgsz"]) for image in images]
    found: list[list[Observation]] = []
    for start in range(0, len(canvases), state["crop_check_batch"]):
        results = state["detector"].predict(
            canvases[start:start + state["crop_check_batch"]],
            imgsz=state["crop_check_imgsz"], conf=state["crop_conf"])
        found.extend([Observation(item.box, class_id=item.cls, confidence=item.conf)
                      for item in detections] for detections in results)
    return found
