"""Manifest, review-label and identity-constraint primitives."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path


PAIR_FIELDS = ("img1", "img2", "label", "split", "evidence",
               "person_id1", "person_id2", "gap_sec")
REVIEW_LABELS = {"same", "different", "unclear", ""}
REVIEW_KINDS = {"cross_track", "track_purity", ""}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def atomic_write_csv(path: Path, rows: list[dict], fields) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def atomic_write_json(path: Path, value: object) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def candidate_id(kind: str, left: str, right: str) -> str:
    """Content-addressed candidate id: the same pair always gets the same id,
    so labels survive re-mining and a web revision collides with nothing."""
    digest = hashlib.sha256(f"{kind}:{left}:{right}".encode()).hexdigest()
    return f"{kind[0]}{digest[:12]}"


def relation(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))


# Canonical track ids carry their recording domain, camera being optional in
# batch-01 ids: cam36_d20260829_v035_t00054, d20260823_v038_t00018.
IDENTITY_DOMAIN = re.compile(r"^(?:(cam\d+)_)?d(\d{8})_")
# Compatibility for datasets produced before canonical domain ids were added:
# cam16-2026-08-30-210002_t00001 / seg-20260823-225003_t00001.
LEGACY_IDENTITY_DOMAIN = re.compile(
    r"^(?:(cam\d+)-)?(?:seg-)?(20\d{2})-?([01]\d)-?([0-3]\d)(?:[-_])")


def identity_domain(person_id: str) -> tuple[str, str] | None:
    """(camera, day) of a canonical track id, or None when the id carries neither.

    Appearance comparison is only meaningful inside one business day and one
    camera: clothes change between days, and production keeps an independent
    gallery per camera, so cross-domain relations never occur online. Miners
    and the conflict checks gate on this; foreign naming schemes return None
    and stay ungated rather than being guessed at.
    """
    match = IDENTITY_DOMAIN.match(person_id)
    if match is not None:
        return (match.group(1) or "", match.group(2))
    legacy = LEGACY_IDENTITY_DOMAIN.match(person_id)
    if legacy is None:
        return None
    return (legacy.group(1) or "", "".join(legacy.groups()[1:]))


def domain_allows(left: str, right: str, allow_cross_day: bool,
                  allow_cross_camera: bool) -> bool:
    """Keep appearance comparison inside one business day and one camera.

    Cross-day pairs are unusable in either direction (clothes change, and
    without covisibility evidence the two tracks might even be the same
    person), and production never queries across cameras. Ids that carry no
    parseable domain are not gated.
    """
    left_domain, right_domain = identity_domain(left), identity_domain(right)
    if left_domain is None or right_domain is None:
        return True
    if not allow_cross_day and left_domain[1] != right_domain[1]:
        return False
    if not allow_cross_camera and left_domain[0] != right_domain[0]:
        return False
    return True


class IdentityGraph:
    """Positive edges merge identities; negative edges are hard constraints."""

    def __init__(self):
        self.parent: dict[str, str] = {}

    def add(self, value: str) -> None:
        self.parent.setdefault(value, value)

    def find(self, value: str) -> str:
        self.add(value)
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: str, right: str) -> None:
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[right] = left

    def same(self, left: str, right: str) -> bool:
        return self.find(left) == self.find(right)


def is_identity_review(row: dict) -> bool:
    """Track-purity answers judge one track's crops, not a relation between two."""
    return row.get("kind", "cross_track") != "track_purity"


def answer_key(row: dict) -> tuple:
    """What question a review row answers.

    A relation, or — for track_purity — one track. Candidate ids are content
    addressed, so re-mining asks the same question under the same id; but an
    older round may have asked it under a different id scheme entirely, which
    is why the question, not the id, is the key.
    """
    # Legacy queues have no kind column. After an in-place schema migration those same
    # rows carry an empty value, which must remain semantically equivalent to cross_track;
    # otherwise an older explicit "cross_track" answer and the newer correction get two
    # different keys and falsely contradict each other.
    kind = row.get("kind") or "cross_track"
    if kind == "track_purity":
        return (kind, row.get("person_id1", ""))
    return (kind, relation(row.get("person_id1", ""), row.get("person_id2", "")))


def supersede(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """One answer per question: on one relation the newest round wins.

    This is the dataset's correction rule (see revision.py), and it has to be
    applied wherever review rounds are merged. Without it a reviewer who
    revisits an old question and changes their mind does not correct the
    dataset — they contradict it, and the checks report a `direct_contradiction`
    against the reviewer's own newer judgement.

    ``paths`` order is the round order, oldest first. An unanswered re-ask
    never erases the answer it re-asks: re-mining a pair enqueues it again as
    pending, and a pending row is a question, not a retraction.
    """
    newest: dict[tuple, dict] = {}
    for row in rows:
        key = answer_key(row)
        held = newest.get(key)
        if held is not None and not row.get("review_label") and held.get("review_label"):
            continue
        newest[key] = row
    return list(newest.values())


def load_reviews(paths: list[Path]) -> list[dict[str, str]]:
    """Read review rounds, oldest first, and keep one answer per question.

    The conflict detail page deliberately does NOT come through here: "who
    said what, when" is exactly the history this collapses, so provenance.py
    reads the round files itself.
    """
    rows = []
    for path in paths:
        candidate_ids = set()
        for source in read_csv(path):
            row = dict(source)
            candidate_id = row.get("candidate_id", "")
            if candidate_id in candidate_ids:
                raise ValueError(f"Duplicate candidate ID {candidate_id!r} in {path}")
            candidate_ids.add(candidate_id)
            label = row.get("review_label", "")
            if label not in REVIEW_LABELS:
                raise ValueError(f"Unsupported review label {label!r} in {path}")
            kind = row.get("kind", "")
            if kind not in REVIEW_KINDS:
                raise ValueError(f"Unsupported candidate kind {kind!r} in {path}")
            row["review_source"] = str(path)
            rows.append(row)
    return supersede(rows)


def contaminated_tracks(review_rows: list[dict]) -> dict[str, str]:
    """Identities a reviewer proved to contain more than one object."""
    return {row["person_id1"]: row.get("candidate_id", "review")
            for row in review_rows
            if row.get("kind") == "track_purity" and row["review_label"] == "different"}


def build_constraints(base_rows: list[dict], review_rows: list[dict]):
    graph = IdentityGraph()
    negatives: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for row in base_rows:
        if int(row["label"]) == 1:
            graph.union(row["person_id1"], row["person_id2"])
        else:
            negatives[relation(row["person_id1"], row["person_id2"])].add(
                "base:" + row.get("evidence", "negative"))
    for row in review_rows:
        if not is_identity_review(row):
            continue
        if row["review_label"] == "same":
            graph.union(row["person_id1"], row["person_id2"])
        elif row["review_label"] == "different":
            negatives[relation(row["person_id1"], row["person_id2"])].add(
                "review:" + row.get("candidate_id", ""))
    conflicts = [
        {"person_id1": left, "person_id2": right,
         "negative_sources": sorted(sources)}
        for (left, right), sources in sorted(negatives.items())
        if graph.same(left, right)
    ]
    return graph, negatives, conflicts


def dataset_status(root: Path, base_pairs: Path, reviews: list[Path]) -> dict:
    base_rows = read_csv(base_pairs)
    review_rows = load_reviews(reviews)
    _, _, conflicts = build_constraints(base_rows, review_rows)
    labels = Counter(row["review_label"] or "pending" for row in review_rows)
    kinds = Counter(f"{row.get('kind', 'cross_track')}:"
                    f"{row['review_label'] or 'pending'}" for row in review_rows)
    pairs = Counter((row["split"], int(row["label"])) for row in base_rows)
    return {
        "schema": 1,
        "dataset_root": str(root),
        "base_pairs": str(base_pairs),
        "base_pairs_sha256": sha256(base_pairs),
        "review_files": [str(path) for path in reviews],
        "review_labels": dict(sorted(labels.items())),
        "review_kinds": dict(sorted(kinds.items())),
        "pending_reviews": labels.get("pending", 0),
        "graph_conflicts": len(conflicts),
        "conflicts": conflicts,
        "contaminated_tracks": sorted(contaminated_tracks(review_rows)),
        "pair_counts": {
            split: {"positive": pairs[(split, 1)], "negative": pairs[(split, 0)]}
            for split in ("train", "val", "test")
        },
    }


def representative_images(root: Path) -> dict[str, str]:
    grouped: defaultdict[str, list[dict]] = defaultdict(list)
    for row in read_csv(root / "identities.csv"):
        grouped[row["person_id"]].append(row)
    output = {}
    for identity, rows in grouped.items():
        rows.sort(key=lambda row: (float(row["timestamp"]), row["img_path"]))
        output[identity] = rows[len(rows) // 2]["img_path"]
    return output


def canonical_split(rows: list[dict], split: str) -> list[tuple[str, ...]]:
    return [tuple(str(row[field]) for field in PAIR_FIELDS)
            for row in rows if row["split"] == split]


def finalize_reviews(root: Path, base_pairs: Path, reviews: list[Path],
                     output: Path, report_path: Path,
                     allowed_splits=frozenset({"train"})) -> dict:
    """Add reviewed evidence while proving protected splits are unchanged."""
    base_rows = read_csv(base_pairs)
    review_rows = load_reviews(reviews)
    pending = [row for row in review_rows
               if row.get("split") in allowed_splits and not row["review_label"]]
    if pending:
        raise ValueError(f"{len(pending)} review candidates are still pending")
    _, _, conflicts = build_constraints(base_rows, review_rows)
    if conflicts:
        raise ValueError(f"Identity graph has {len(conflicts)} conflicts: {conflicts[:3]}")

    images = representative_images(root)
    base_graph, base_negatives, _ = build_constraints(base_rows, [])
    # Protected-split reviews are useful audit evidence, but they must never
    # change the immutable val/test manifests (including track-purity drops).
    writable_reviews = [row for row in review_rows if row.get("split") in allowed_splits]
    rejected = contaminated_tracks(writable_reviews)
    additions = []
    skipped = Counter()
    for row in review_rows:
        label = row["review_label"]
        if row.get("split") not in allowed_splits:
            skipped["protected_split_review"] += 1
            continue
        if not is_identity_review(row):
            continue
        if label == "unclear":
            skipped["unclear"] += 1
            continue
        left, right = row["person_id1"], row["person_id2"]
        pair = relation(left, right)
        if left in rejected or right in rejected:
            skipped["contaminated_track"] += 1
            continue
        if label == "same" and base_graph.same(left, right):
            skipped["already_same"] += 1
            continue
        if label == "different" and pair in base_negatives:
            skipped["already_different"] += 1
            continue
        additions.append({
            "img1": row.get("img1") or images[left],
            "img2": row.get("img2") or images[right],
            "label": 1 if label == "same" else 0,
            "split": row["split"],
            "evidence": ("reviewed_model_mined_same" if label == "same"
                         else "reviewed_model_mined_different"),
            "person_id1": left, "person_id2": right,
            "gap_sec": row.get("time_gap_sec", ""),
        })
        if label == "same":
            base_graph.union(left, right)
        else:
            base_negatives[pair].add("new")

    output_rows = [{field: row[field] for field in PAIR_FIELDS} for row in base_rows]
    dropped = [row for row in output_rows
               if row["person_id1"] in rejected or row["person_id2"] in rejected]
    protected_drops = sorted({row["split"] for row in dropped} - set(allowed_splits))
    if protected_drops:
        raise ValueError("Contaminated tracks appear in protected splits "
                         f"{protected_drops}; rebuild those splits explicitly")
    output_rows = [row for row in output_rows
                   if row["person_id1"] not in rejected and row["person_id2"] not in rejected]
    output_rows.extend(additions)
    protected = {"val", "test"} - set(allowed_splits)
    for split in protected:
        if canonical_split(output_rows, split) != canonical_split(base_rows, split):
            raise AssertionError(f"Protected split changed: {split}")
    atomic_write_csv(output, output_rows, PAIR_FIELDS)
    report = {
        "schema": 1, "base_pairs": str(base_pairs),
        "base_pairs_sha256": sha256(base_pairs), "output": str(output),
        "output_sha256": sha256(output),
        "review_labels": dict(Counter(row["review_label"] for row in review_rows)),
        "additions": dict(Counter(
            "positive" if int(row["label"]) else "negative" for row in additions)),
        "skipped": dict(skipped), "allowed_splits": sorted(allowed_splits),
        "protected_splits_unchanged": sorted(protected), "graph_conflicts": 0,
        "rejected_tracks": dict(sorted(rejected.items())),
        "dropped_pairs": len(dropped),
    }
    atomic_write_json(report_path, report)
    return report
