"""A small, deterministic IoU tracker with optional appearance gating.

Association only links a detection to a track when the geometry already agrees;
appearance can further *restrict* a match but, by default, never creates one.
An identity produced by this tracker is therefore one continuous observation,
which is what makes same-track positives trustworthy evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .detector import Detection
from .geometry import iou


@dataclass
class Track:
    track_id: int
    box: np.ndarray
    conf: float
    cls: int
    hits: int = 1
    time_since_update: int = 0
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(4, np.float32))
    embedding: np.ndarray | None = None
    recovered: int = 0
    detection: Detection | None = None

    def confirmed(self, n_init: int) -> bool:
        return self.hits >= n_init

    def predicted_box(self) -> np.ndarray:
        return self.box + self.velocity * float(self.time_since_update + 1)


def cosine_distance(left: np.ndarray | None, right: np.ndarray | None) -> float:
    if left is None or right is None:
        return 0.0
    return float(1.0 - np.dot(left, right)
                 / max(float(np.linalg.norm(left) * np.linalg.norm(right)), 1e-12))


class Tracker:
    """Greedy IoU/appearance association; no Kalman filter, no learned motion."""

    def __init__(self, *, max_iou_distance: float = 0.7, max_age: int = 30,
                 n_init: int = 3, appearance_weight: float = 0.0,
                 max_cosine_distance: float = 0.35,
                 appearance_recovery: bool = False,
                 embedding_momentum: float = 0.9):
        self.max_iou_distance = max_iou_distance
        self.max_age, self.n_init = max_age, n_init
        self.appearance_weight = appearance_weight
        self.max_cosine_distance = max_cosine_distance
        self.appearance_recovery = appearance_recovery
        self.embedding_momentum = embedding_momentum
        self.tracks: list[Track] = []
        self.removed: list[Track] = []
        self._next_id = 1

    def _cost(self, track: Track, detection: Detection,
              embedding: np.ndarray | None) -> float | None:
        overlap = iou(track.predicted_box(), detection.box)
        geometry_ok = overlap >= 1.0 - self.max_iou_distance
        appearance = cosine_distance(track.embedding, embedding)
        appearance_ok = (embedding is None or track.embedding is None
                         or appearance <= self.max_cosine_distance)
        if not appearance_ok:
            return None
        if not geometry_ok:
            recoverable = (self.appearance_recovery and embedding is not None
                           and track.embedding is not None
                           and track.time_since_update > 0)
            if not recoverable:
                return None
            return 1.0 + appearance
        return (1.0 - overlap) * (1.0 - self.appearance_weight) + appearance * self.appearance_weight

    def update(self, detections: list[Detection],
               embeddings: list[np.ndarray] | None = None) -> list[Track]:
        """Advance one frame and return the tracks updated by this frame."""
        embeddings = embeddings if embeddings is not None else [None] * len(detections)
        for track in self.tracks:
            track.time_since_update += 1
            track.detection = None

        pairs = []
        for track_index, track in enumerate(self.tracks):
            for detection_index, detection in enumerate(detections):
                if detection.cls != track.cls:
                    continue
                cost = self._cost(track, detection, embeddings[detection_index])
                if cost is not None:
                    pairs.append((cost, track_index, detection_index))
        pairs.sort(key=lambda item: (item[0], item[1], item[2]))

        used_tracks: set[int] = set()
        used_detections: set[int] = set()
        for cost, track_index, detection_index in pairs:
            if track_index in used_tracks or detection_index in used_detections:
                continue
            used_tracks.add(track_index)
            used_detections.add(detection_index)
            track, detection = self.tracks[track_index], detections[detection_index]
            if track.time_since_update > 1:
                track.recovered += 1
            gap = float(track.time_since_update)
            track.velocity = (detection.box - track.box) / max(gap, 1.0)
            track.box, track.conf, track.detection = detection.box, detection.conf, detection
            track.hits += 1
            track.time_since_update = 0
            self._merge_embedding(track, embeddings[detection_index])

        for detection_index, detection in enumerate(detections):
            if detection_index in used_detections:
                continue
            track = Track(self._next_id, detection.box, detection.conf, detection.cls,
                          detection=detection)
            self._merge_embedding(track, embeddings[detection_index])
            self._next_id += 1
            self.tracks.append(track)

        alive, self.removed = [], []
        for track in self.tracks:
            if track.time_since_update > self.max_age:
                self.removed.append(track)
            else:
                alive.append(track)
        self.tracks = alive
        return [track for track in self.tracks if track.time_since_update == 0]

    def _merge_embedding(self, track: Track, embedding: np.ndarray | None) -> None:
        if embedding is None:
            return
        if track.embedding is None:
            track.embedding = embedding.astype(np.float32).copy()
            return
        momentum = self.embedding_momentum
        merged = track.embedding * momentum + embedding * (1.0 - momentum)
        track.embedding = merged / max(float(np.linalg.norm(merged)), 1e-12)

    def flush(self) -> list[Track]:
        """Close the video: every live track becomes a finished track."""
        finished, self.tracks = self.tracks, []
        self.removed = []
        return finished
