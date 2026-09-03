"""Rank human-review candidates with a rough ReID model.

Two questions are mined, both of which a model may only *ask*, never answer:

``cross_track``   two continuous tracks that look alike but were never proven
                  related — are they the same object?
``track_purity``  one track whose own crops disagree — did the tracker switch
                  onto a different object mid-track?

Candidate ids are content-addressed, so re-mining a dataset keeps every label a
reviewer already gave to the same question.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .core import (atomic_write_csv, atomic_write_json, build_constraints, candidate_id,
                   domain_allows, load_reviews, read_csv, relation)
from .embed import ReidEmbedder, centroid, cosine

CANDIDATE_FIELDS = ("candidate_id", "kind", "split", "person_id1", "person_id2",
                    "img1", "img2", "time_gap_sec", "cosine", "rank_score",
                    "review_label", "review_notes")


def load_tracks(root: Path) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    images: defaultdict[str, list[dict]] = defaultdict(list)
    for row in read_csv(root / "identities.csv"):
        images[row["person_id"]].append(row)
    for rows in images.values():
        rows.sort(key=lambda row: (float(row["timestamp"]), row["img_path"]))
    meta = {}
    tracks_csv = root / "tracks.csv"
    if tracks_csv.is_file():
        meta = {row["person_id"]: row for row in read_csv(tracks_csv)
                if row.get("status", "accepted") == "accepted"}
    return dict(images), meta


def time_gap(left: list[dict], right: list[dict]) -> float:
    left_start, left_end = float(left[0]["timestamp"]), float(left[-1]["timestamp"])
    right_start, right_end = float(right[0]["timestamp"]), float(right[-1]["timestamp"])
    if left_end < right_start:
        return right_start - left_end
    if right_end < left_start:
        return left_start - right_end
    return 0.0


def known_relations(root: Path, base_pairs: Path, reviews: list[Path]):
    """Everything already decided: merged identities and proven-different pairs."""
    base_rows = read_csv(base_pairs)
    existing = list(dict.fromkeys(path for path in reviews if path.is_file()))
    review_rows = load_reviews(existing)
    graph, negative_sources, _ = build_constraints(base_rows, review_rows)
    negatives: set[tuple[str, str]] = set(negative_sources)
    covisibility = root / "covisibility.csv"
    if covisibility.is_file():
        for row in read_csv(covisibility):
            negatives.add(relation(row["person_id1"], row["person_id2"]))
    decided: dict[str, dict[str, str]] = {}
    for path in existing:
        for row in read_csv(path):
            label = row.get("review_label", "")
            decided[row["candidate_id"]] = {"review_label": label,
                                            "review_notes": row.get("review_notes", "")}
    return graph, negatives, decided


def closest_pair(left_rows: list[dict], right_rows: list[dict],
                 features: dict[str, np.ndarray]) -> tuple[str, str, float]:
    best = (-2.0, left_rows[0]["img_path"], right_rows[0]["img_path"])
    for first in left_rows:
        for second in right_rows:
            score = cosine(features[first["img_path"]], features[second["img_path"]])
            if score > best[0]:
                best = (score, first["img_path"], second["img_path"])
    return best[1], best[2], best[0]


def farthest_pair(rows: list[dict], features: dict[str, np.ndarray]) -> tuple[str, str, float]:
    best = (2.0, rows[0]["img_path"], rows[0]["img_path"])
    for index, first in enumerate(rows):
        for second in rows[index + 1:]:
            score = cosine(features[first["img_path"]], features[second["img_path"]])
            if score < best[0]:
                best = (score, first["img_path"], second["img_path"])
    return best[1], best[2], best[0]


def mine(root: Path, args) -> dict:
    images, meta = load_tracks(root)
    if meta:
        images = {key: rows for key, rows in images.items() if key in meta}
    splits = set(args.splits)
    identities = {key: rows for key, rows in images.items() if rows[0]["split"] in splits}
    if not identities:
        raise SystemExit(f"no accepted identities in splits={sorted(splits)}")

    destination = root / args.output_dir
    destination.mkdir(parents=True, exist_ok=True)
    reviews = [root / path for path in args.reviews] + [destination / "candidates.csv"]
    graph, negatives, decided = known_relations(root, root / args.base_pairs, reviews)

    embedder = ReidEmbedder(args.reid_onnx, size=args.reid_input_size,
                            preprocess=args.reid_preprocess, provider=args.reid_provider,
                            batch_size=args.reid_batch_size)
    paths = sorted(row["img_path"] for rows in identities.values() for row in rows)
    features = embedder.embed_paths(root, paths,
                                    cache=root / ".cache" / f"embeddings-{embedder.signature()}.npz")
    centroids = {key: centroid([features[row["img_path"]] for row in rows])
                 for key, rows in identities.items()}
    split_of = {key: rows[0]["split"] for key, rows in identities.items()}
    class_of = {key: rows[0].get("class_id", "0") for key, rows in identities.items()}

    rows: list[dict] = []
    report: dict = {"model": str(args.reid_onnx), "preprocess": args.reid_preprocess,
                    "min_cosine": args.min_cosine, "min_gap_sec": args.min_gap_sec,
                    "allow_cross_day": args.allow_cross_day,
                    "allow_cross_camera": args.allow_cross_camera,
                    "splits": {}, "kinds": {}}
    for split in args.splits:
        keys = sorted(key for key, value in split_of.items() if value == split)
        scored = []
        domain_skipped = 0
        for index, left in enumerate(keys):
            for right in keys[index + 1:]:
                if class_of[left] != class_of[right]:
                    continue
                if not domain_allows(left, right, args.allow_cross_day,
                                     args.allow_cross_camera):
                    domain_skipped += 1
                    continue
                if relation(left, right) in negatives or graph.same(left, right):
                    continue
                gap = time_gap(identities[left], identities[right])
                if gap < args.min_gap_sec:
                    continue
                score = cosine(centroids[left], centroids[right])
                if score < args.min_cosine:
                    continue
                scored.append((score, gap, left, right))
        scored.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))
        counts: Counter[str] = Counter()
        chosen = []
        for score, gap, left, right in scored:
            if (counts[left] >= args.max_per_identity
                    or counts[right] >= args.max_per_identity):
                continue
            counts[left] += 1
            counts[right] += 1
            chosen.append((score, gap, left, right))
            if len(chosen) >= args.per_split:
                break
        for score, gap, left, right in chosen:
            img1, img2, best = closest_pair(identities[left], identities[right], features)
            rows.append({
                "candidate_id": candidate_id("cross_track", *relation(left, right)),
                "kind": "cross_track", "split": split,
                "person_id1": left, "person_id2": right, "img1": img1, "img2": img2,
                "time_gap_sec": f"{gap:.1f}", "cosine": f"{best:.6f}",
                "rank_score": f"{score:.6f}", "review_label": "", "review_notes": "",
            })
        report["splits"][split] = {"identities": len(keys), "eligible": len(scored),
                                   "selected": len(chosen),
                                   "domain_skipped": domain_skipped}

    if args.purity_per_split:
        for split in args.splits:
            suspects = []
            for key in sorted(key for key, value in split_of.items() if value == split):
                rows_for_key = identities[key]
                if len(rows_for_key) < 2:
                    continue
                img1, img2, worst = farthest_pair(rows_for_key, features)
                if worst <= args.purity_max_cosine:
                    suspects.append((worst, key, img1, img2))
            suspects.sort()
            for worst, key, img1, img2 in suspects[:args.purity_per_split]:
                rows.append({
                    "candidate_id": candidate_id("track_purity", key, key),
                    "kind": "track_purity", "split": split,
                    "person_id1": key, "person_id2": key, "img1": img1, "img2": img2,
                    "time_gap_sec": "0.0", "cosine": f"{worst:.6f}",
                    "rank_score": f"{worst:.6f}", "review_label": "", "review_notes": "",
                })

    for row in rows:
        previous = decided.get(row["candidate_id"])
        if previous:
            row["review_label"] = previous["review_label"]
            row["review_notes"] = previous["review_notes"]
    rows.sort(key=lambda row: (row["kind"], row["split"], -float(row["rank_score"]),
                               row["candidate_id"]))
    report["kinds"] = dict(Counter(row["kind"] for row in rows))
    report["preserved_labels"] = sum(1 for row in rows if row["review_label"])
    atomic_write_csv(destination / "candidates.csv", rows, CANDIDATE_FIELDS)
    atomic_write_json(destination / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print(f"candidates: {destination / 'candidates.csv'}", flush=True)
    return report
