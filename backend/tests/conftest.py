"""Shared dataset fixtures for the workbench tests."""

import csv
from pathlib import Path

import pytest

from reid_annotation_tool.core import PAIR_FIELDS

CANDIDATE_FIELDS = ("candidate_id", "kind", "split", "person_id1", "person_id2",
                    "img1", "img2", "time_gap_sec", "cosine", "rank_score",
                    "review_label", "review_notes")
IDENTITY_FIELDS = ("img_path", "person_id", "split", "video", "track_id", "class_id",
                   "timestamp")
TRACK_FIELDS = ("person_id", "split", "video", "track_id", "class_id", "start", "end",
                "frames", "crops", "recovered", "status", "reason")
COVISIBLE_FIELDS = ("person_id1", "person_id2", "video", "split", "frames",
                    "first_timestamp")


def write_csv(path: Path, fields, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def pair(left, right, label, split="train", evidence="test"):
    return {"img1": f"images/{split}/{left}/00.jpg", "img2": f"images/{split}/{right}/01.jpg",
            "label": label, "split": split, "evidence": evidence,
            "person_id1": left, "person_id2": right, "gap_sec": "1"}


def candidate(candidate_id, left, right, label="", kind="cross_track", split="train"):
    return {"candidate_id": candidate_id, "kind": kind, "split": split,
            "person_id1": left, "person_id2": right,
            "img1": f"images/{split}/{left}/00.jpg", "img2": f"images/{split}/{right}/01.jpg",
            "time_gap_sec": "10", "cosine": "0.9", "rank_score": "0.9",
            "review_label": label, "review_notes": ""}


@pytest.fixture
def dataset(tmp_path):
    """A four-track dataset with real crop files on disk."""
    identities, tracks = [], []
    for index, (identity, split) in enumerate(
            [("a", "train"), ("b", "train"), ("c", "train"), ("d", "train")]):
        for crop in range(2):
            path = f"images/{split}/{identity}/{crop:02d}.jpg"
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_bytes(b"jpeg")
            identities.append({"img_path": path, "person_id": identity, "split": split,
                               "video": "v0.mp4", "track_id": index, "class_id": 0,
                               "timestamp": f"{100 * index + crop:.3f}"})
        tracks.append({"person_id": identity, "split": split, "video": "v0.mp4",
                       "track_id": index, "class_id": 0,
                       "start": f"{100 * index:.3f}", "end": f"{100 * index + 1:.3f}",
                       "frames": 30, "crops": 2, "recovered": 0,
                       "status": "accepted", "reason": ""})
    write_csv(tmp_path / "identities.csv", IDENTITY_FIELDS, identities)
    write_csv(tmp_path / "tracks.csv", TRACK_FIELDS, tracks)
    write_csv(tmp_path / "covisibility.csv", COVISIBLE_FIELDS, [])
    write_csv(tmp_path / "pairs.csv", PAIR_FIELDS,
              [pair(name, name, 1, evidence="same_continuous_track")
               for name in ("a", "b", "c", "d")])
    return tmp_path
