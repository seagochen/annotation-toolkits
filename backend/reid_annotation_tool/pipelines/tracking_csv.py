"""Reference pipeline: replay tracking results a user's own system already wrote.

Nothing is detected or tracked here — the boxes come from a file the user's
pipeline produced (a Jetson engine run, an offline batch job, a labelling tool
export). This is the case this tool is built around: the models, the tracker
and the identity assignment are already someone else's solved problem, and
what is missing is turning their output into a reviewable ReID dataset.

Config::

    pipeline:
      script: tracking_csv
      file: tracks_from_my_system.csv
      class_ids: [0]          # optional; omit to accept every class

The CSV needs a header with at least ``source,frame,x1,y1,x2,y2``. ``track_id``
is what makes a row an identity — leave it empty for a bystander that should
only veto contaminated crops. ``class_id`` and ``conf`` are optional.

``source`` must match the recording id the host derives from the video path
(the file stem, or the ``video_id_regex`` capture groups when the config sets
one), because that is what joins a row to a frame.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import numpy as np

from reid_annotation_tool.contract import Observation

REQUIRED = ("source", "frame", "x1", "y1", "x2", "y2")

# (source, frame) -> observations, loaded once for the whole run.
_rows: dict | None = None


def _load(config: dict) -> dict:
    global _rows
    if _rows is not None:
        return _rows
    reference = config.get("file")
    if not reference:
        raise SystemExit("pipeline.file is required by the tracking_csv pipeline")
    path = Path(reference).expanduser()
    if not path.is_file():
        raise SystemExit(f"tracking file not found: {path}")
    wanted = config.get("class_ids")
    classes = None if wanted is None else {int(value) for value in wanted}

    grouped: dict = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [name for name in REQUIRED if name not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"{path} is missing columns: {missing}")
        for line, row in enumerate(reader, start=2):
            try:
                box = np.asarray([row["x1"], row["y1"], row["x2"], row["y2"]], np.float32)
            except ValueError as error:
                raise SystemExit(f"{path} line {line}: unreadable box ({error})") from error
            class_id = int(float(row.get("class_id") or 0))
            if classes is not None and class_id not in classes:
                continue
            raw = (row.get("track_id") or "").strip()
            grouped[(row["source"], int(float(row["frame"])))].append(Observation(
                box, track_id=int(float(raw)) if raw else None, class_id=class_id,
                confidence=float(row.get("conf") or 1.0)))
    _rows = dict(grouped)
    print(f"tracking_csv: {len(_rows)} frames from {path}", flush=True)
    return _rows


def process_frame(image, source, config: dict) -> list[Observation]:
    # The image is ignored on purpose: this pipeline answers from the file, and
    # the host still cuts the pixels itself.
    return _load(config).get((source.name, source.index), [])
