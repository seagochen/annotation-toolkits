"""ReID review queue access and atomic CSV label persistence.

This module is transport-neutral: both the platform API and domain tests use the
same store, while labels remain readable by the existing CLI pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

from local_files import file_lock

from .core import REVIEW_LABELS, atomic_write_csv, read_csv
from .domain import csv_fields

GALLERY_LIMIT = 8


class LabelConflictError(RuntimeError):
    """A write-once review request disagrees with an existing decision."""


def spread(values: list, maximum: int) -> list:
    """Evenly sample a list so a gallery shows the whole track, not its start."""
    if len(values) <= maximum:
        return values
    step = (len(values) - 1) / (maximum - 1)
    return [values[round(index * step)] for index in range(maximum)]


class Store:
    """Thread-safe view over one review round and the identity galleries.

    ``candidates.csv`` and ``identities.csv`` are re-parsed only when their
    mtime changes, so one Store is meant to be kept per round and reused
    across requests (the platform adapter caches one per round file).
    """

    def __init__(self, root: Path, candidates: Path | None):
        self.root, self.candidates = root, candidates
        # The same per-path lock the platform modules use, shared across
        # Store instances created for HTTP requests.
        self.lock = file_lock(candidates or root)
        self._rows: list[dict] = []
        self._stamp = -1.0
        self._gallery: dict[str, list[str]] = {}
        self._identity_meta: dict[str, dict] = {}
        self._identity_stamp = -1.0

    def _reload_candidates(self) -> None:
        """No candidates.csv yet is not an error -- a brand-new project has no
        review items until `extract` and `mine` have run."""
        if self.candidates is None or not self.candidates.is_file():
            self._rows, self._stamp = [], -1.0
            return
        stamp = self.candidates.stat().st_mtime_ns
        if stamp == self._stamp:
            return
        self._rows = read_csv(self.candidates)
        self._stamp = stamp

    def _reload_identities(self) -> None:
        path = self.root / "identities.csv"
        if not path.is_file():
            return
        stamp = path.stat().st_mtime_ns
        if stamp == self._identity_stamp:
            return
        gallery: dict[str, list[dict]] = {}
        for row in read_csv(path):
            gallery.setdefault(row["person_id"], []).append(row)
        self._gallery = {}
        self._identity_meta = {}
        for identity, rows in gallery.items():
            rows.sort(key=lambda row: (float(row.get("timestamp", 0) or 0), row["img_path"]))
            self._gallery[identity] = [row["img_path"] for row in rows]
            self._identity_meta[identity] = {
                "split": rows[0].get("split", ""), "video": rows[0].get("video", ""),
                "track_id": rows[0].get("track_id", ""), "crops": len(rows),
                "start": rows[0].get("timestamp", ""), "end": rows[-1].get("timestamp", ""),
                "class_id": rows[0].get("class_id", ""),
            }
        self._identity_stamp = stamp

    def rows(self) -> list[dict]:
        with self.lock:
            self._reload_candidates()
            return self._rows

    def decorate(self, row: dict) -> dict:
        with self.lock:
            self._reload_identities()
            left, right = row.get("person_id1", ""), row.get("person_id2", "")
            gallery1 = self._gallery.get(left, [])
            gallery2 = self._gallery.get(right, [])
            value = dict(row)
            value.setdefault("kind", "cross_track")
            value["gallery1"] = spread(gallery1, GALLERY_LIMIT)
            value["gallery2"] = spread(gallery2, GALLERY_LIMIT)
            value["meta1"] = self._identity_meta.get(left, {})
            value["meta2"] = self._identity_meta.get(right, {})
            value["img1"] = row.get("img1") or (gallery1[0] if gallery1 else "")
            value["img2"] = row.get("img2") or (gallery2[-1] if gallery2 else "")
            return value

    def set_label(
        self,
        candidate_id: str,
        label: str,
        notes: str | None,
        *,
        overwrite: bool = True,
    ) -> dict | None:
        with self.lock:
            rows = read_csv(self.candidates)
            fields = csv_fields(self.candidates)
            if notes is not None and "review_notes" not in fields:
                fields.append("review_notes")
            target = None
            for row in rows:
                if row.get("candidate_id") == candidate_id:
                    current_label = row.get("review_label", "")
                    current_notes = row.get("review_notes", "")
                    if not overwrite and current_label:
                        same_notes = notes is None or current_notes == notes
                        if current_label == label and same_notes:
                            return self.decorate(row)
                        raise LabelConflictError(
                            f"candidate {candidate_id!r} is already labelled "
                            f"{current_label!r}"
                        )
                    row["review_label"] = label
                    if notes is not None:
                        row["review_notes"] = notes
                    target = row
                    break
            if target is None:
                return None
            atomic_write_csv(self.candidates, fields, rows)
            self._stamp = -1.0
            return self.decorate(target)

    def queue(self, *, kind: str = "", split: str = "", status: str = "",
              search: str = "", offset: int = 0, limit: int = 60) -> dict:
        """Return one filtered page of the round, decorated with galleries."""
        selected = []
        for row in self.rows():
            label = row.get("review_label", "")
            if kind and row.get("kind", "cross_track") != kind:
                continue
            if split and row.get("split") != split:
                continue
            if status == "pending" and label:
                continue
            if status in REVIEW_LABELS and status and label != status:
                continue
            if search and search.lower() not in json.dumps(
                row, ensure_ascii=False
            ).lower():
                continue
            selected.append(row)
        page = selected[offset:offset + limit]
        return {
            "total": len(selected), "offset": offset, "limit": limit,
            "rows": [self.decorate(row) for row in page],
        }
