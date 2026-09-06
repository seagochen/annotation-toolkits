"""ReID review queue access and atomic CSV label persistence.

This module is transport-neutral: both the platform API and domain tests use the
same store, while labels remain readable by the existing CLI pipeline.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from pathlib import Path

from . import conflicts as conflict_engine
from . import revision
from .core import REVIEW_LABELS, atomic_write_csv, read_csv, relation
from .domain import csv_fields
from .provenance import Provenance

GALLERY_LIMIT = 8
_STORE_LOCKS: dict[Path, threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


class LabelConflictError(RuntimeError):
    """A write-once review request disagrees with an existing decision."""


def _store_lock(path: Path) -> threading.RLock:
    """Share the CSV lock across Store instances created for HTTP requests."""
    key = path.resolve()
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, threading.RLock())

# Similarity columns the different mining generations wrote, most recent first.
def spread(values: list, maximum: int) -> list:
    """Evenly sample a list so a gallery shows the whole track, not its start."""
    if len(values) <= maximum:
        return values
    step = (len(values) - 1) / (maximum - 1)
    return [values[round(index * step)] for index in range(maximum)]


class Store:
    """Thread-safe view over identities, candidates and derived conflicts."""

    def __init__(self, root: Path, candidates: Path | None, base_pairs: Path,
                 reviews: list[Path]):
        self.root, self.candidates = root, candidates
        self.base_pairs = base_pairs
        self.reviews = reviews or ([candidates] if candidates else [])
        self.lock = _store_lock(candidates or root)
        self._rows: list[dict] = []
        self._stamp = -1.0
        self._gallery: dict[str, list[str]] = {}
        self._identity_meta: dict[str, dict] = {}
        self._identity_stamp = -1.0
        self._conflicts: dict | None = None
        self._conflicts_stale = False
        self._conflicts_running = False
        self.provenance = Provenance(root, candidates, base_pairs, self.reviews)

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

    def identity(self, person_id: str) -> dict:
        with self.lock:
            self._reload_identities()
            return {"person_id": person_id,
                    "images": self._gallery.get(person_id, []),
                    "meta": self._identity_meta.get(person_id, {})}

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
            atomic_write_csv(self.candidates, rows, fields)
            self._stamp = -1.0
            self.invalidate_conflicts()
            return self.decorate(target)

    def _injection_defaults(self, left: str, right: str) -> dict:
        """Split and evidence crops for a candidate the review workflow has to inject."""
        with self.lock:
            self._reload_identities()
            gallery1 = self._gallery.get(left, [])
            gallery2 = self._gallery.get(right, [])
            return {
                "split": self._identity_meta.get(left, {}).get("split", ""),
                # middle crops as representative evidence images for an
                # injected candidate; the miner would have picked its own
                "img1": gallery1[len(gallery1) // 2] if gallery1 else "",
                "img2": gallery2[len(gallery2) // 2] if gallery2 else "",
            }

    def revise_base(self, left: str, right: str, verdict: str, notes: str) -> dict:
        """Supersede baked reviewed_* rows for one pair from an annotation client.

        Runs under the store lock so no label save can interleave with the
        read-modify-write of either CSV; see revision.py for the rule and the
        provenance trail it writes.
        """
        with self.lock:
            event = revision.revise(self.candidates, self.base_pairs, left, right,
                                    verdict, notes,
                                    self._injection_defaults(left, right))
            self._stamp = -1.0  # candidates.csv was rewritten behind rows()
            self.invalidate_conflicts()
            return event

    def set_relation(self, left: str, right: str, verdict: str, notes: str) -> dict:
        """Judge one relation straight from the conflict chain.

        The chain cards answer a relation, not a queue row, so this is the one
        entry point for both writes such an answer can need: baked
        ``reviewed_*`` rows standing in the way are superseded first (archived
        and logged, see revision.py), and the verdict itself is always filed
        into the live round — relabelling the pair's candidate, or injecting
        one when the pair was never queued. Physical evidence is deliberately
        left alone: a verdict contradicting it stays flagged by the conflict
        checks instead of silently deleting a machine-derived fact.
        """
        if verdict not in revision.VERDICTS:
            raise ValueError(f"unsupported verdict: {verdict}")
        with self.lock:
            key = relation(left, right)
            if any(relation(row["person_id1"], row["person_id2"]) == key
                   and row.get("evidence", "").startswith(revision.REVISABLE_PREFIX)
                   for row in (read_csv(self.base_pairs)
                               if self.base_pairs.is_file() else [])):
                return self.revise_base(left, right, verdict, notes)
            event = {"pair": list(key), "verdict": verdict, "notes": notes,
                     "removed": [],
                     **revision.file_verdict(self.candidates, key, verdict, notes,
                                             self._injection_defaults(left, right))}
            self._stamp = -1.0  # candidates.csv was rewritten behind rows()
            self.invalidate_conflicts()
            return event

    def state(self) -> dict:
        rows = self.rows()
        by_kind: dict[str, Counter] = {}
        for row in rows:
            kind = row.get("kind", "cross_track")
            by_kind.setdefault(kind, Counter())[row.get("review_label") or "pending"] += 1
        splits = sorted({row.get("split", "") for row in rows})
        labelled = sum(1 for row in rows if row.get("review_label"))
        return {
            "dataset_root": str(self.root),
            "candidates": str(self.candidates) if self.candidates else "",
            "base_pairs": str(self.base_pairs),
            "total": len(rows), "labelled": labelled, "pending": len(rows) - labelled,
            "kinds": {kind: dict(counter) for kind, counter in sorted(by_kind.items())},
            "splits": [split for split in splits if split],
            "conflicts": self.conflict_summary(),
        }

    def queue(self, *, kind: str = "", split: str = "", status: str = "",
              search: str = "", offset: int = 0, limit: int = 60) -> dict:
        """Return one filtered page; shared by the legacy and platform adapters."""
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

    def invalidate_conflicts(self) -> None:
        """Mark the cached report stale and refresh it off the request thread.

        Conflict detection walks the whole identity graph, so doing it inline
        would make every keystroke wait on the size of the dataset. The reviewer
        sees the updated badge on the next answer instead of the current one.
        """
        self._conflicts_stale = True
        if self._conflicts is None or self._conflicts_running:
            return
        self._conflicts_running = True
        threading.Thread(target=self._recompute_conflicts, daemon=True).start()

    def _recompute_conflicts(self) -> None:
        try:
            value = self.provenance.decorate(
                conflict_engine.report(self.root, self.base_pairs, self.reviews))
            with self.lock:
                self._conflicts = value
                self._conflicts_stale = False
        except Exception as error:  # pragma: no cover - reported through the API
            print(f"conflict detection failed: {error}", flush=True)
        finally:
            self._conflicts_running = False

    def conflict_report(self, refresh: bool = False) -> dict:
        with self.lock:
            if self._conflicts is None or refresh or self._conflicts_stale:
                self._conflicts = self.provenance.decorate(
                    conflict_engine.report(self.root, self.base_pairs, self.reviews))
                self._conflicts_stale = False
            return self._conflicts

    def conflict_summary(self) -> dict:
        if not self.base_pairs.is_file():
            return {"available": False, "errors": 0, "warnings": 0, "stale": False}
        with self.lock:
            cached = self._conflicts
            stale = self._conflicts_stale
        value = cached if cached is not None else self.conflict_report()
        return {"available": True, "errors": value["errors"],
                "warnings": value["warnings"], "by_kind": value["by_kind"],
                "stale": stale}
