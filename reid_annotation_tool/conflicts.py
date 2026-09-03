"""Logic-conflict detection over the identity graph and its physical evidence.

A ReID dataset can be internally impossible in more ways than one, and every one
of them silently teaches the model something false:

* ``transitive_negative``  a chain of "same" links two identities a "different"
                           edge says can never be the same;
* ``direct_contradiction`` one pair carries both answers;
* ``covisible_merge``      two tracks seen in the same frame were merged;
* ``temporal_overlap``     one identity exists twice at the same instant;
* ``cross_day_pair`` /
  ``cross_camera_pair``    a relation crosses the operating domain (appearance
                           comparison is same-day, same-camera only);
* ``split_leakage``        one identity straddles train/val/test;
* ``split_mismatch``       a pair is filed under a split its crops do not belong to;
* ``self_negative``        a track is declared different from itself;
* ``unknown_identity`` /
  ``missing_image``        the manifest points at data that is not there.

Every conflict names the witnesses that produced it, so a reviewer can reopen
exactly the questions that have to change.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from functools import cached_property
from pathlib import Path

from .core import (IdentityGraph, atomic_write_json, identity_domain, load_reviews,
                   read_csv, relation)


@dataclass(slots=True)
class Conflict:
    kind: str
    severity: str
    message: str
    identities: list[str] = field(default_factory=list)
    witnesses: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


@dataclass
class DatasetView:
    root: Path
    base_rows: list[dict]
    review_rows: list[dict]
    identity_rows: list[dict]
    track_rows: list[dict]
    covisible_rows: list[dict]

    @classmethod
    def load(cls, root: Path, base_pairs: Path, reviews: list[Path]) -> "DatasetView":
        def optional(name: str) -> list[dict]:
            path = root / name
            return read_csv(path) if path.is_file() else []

        return cls(root, read_csv(base_pairs), load_reviews(reviews),
                   optional("identities.csv"), optional("tracks.csv"),
                   optional("covisibility.csv"))

    @cached_property
    def split_of_identity(self) -> dict[str, str]:
        return {row["person_id"]: row["split"] for row in self.identity_rows}

    @cached_property
    def identity_edges(self) -> list[tuple[str, str, str]]:
        """Every asserted ``same`` edge with the witness that asserted it."""
        edges = [(row["person_id1"], row["person_id2"], f"base:{row.get('evidence', 'positive')}")
                 for row in self.base_rows if int(row["label"]) == 1
                 and row["person_id1"] != row["person_id2"]]
        edges += [(row["person_id1"], row["person_id2"], row.get("candidate_id", "review"))
                  for row in self.review_rows if row.get("review_label") == "same"
                  and row.get("kind", "cross_track") != "track_purity"
                  and row["person_id1"] != row["person_id2"]]
        return edges

    @cached_property
    def adjacency(self) -> dict[str, list[tuple[str, str]]]:
        """Undirected same-edge adjacency, derived once for the whole report."""
        adjacency: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)
        for left, right, witness in self.identity_edges:
            adjacency[left].append((right, witness))
            adjacency[right].append((left, witness))
        return dict(adjacency)

    @cached_property
    def graph(self) -> IdentityGraph:
        """Merged identities. Four checks need it; building it four times made
        the report quadratic in the edge count for no gain."""
        graph = IdentityGraph()
        for left, right, _ in self.identity_edges:
            graph.union(left, right)
        return graph

    @cached_property
    def negative_edges(self) -> dict[tuple[str, str], list[str]]:
        negatives: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
        for row in self.base_rows:
            if int(row["label"]) == 0:
                negatives[relation(row["person_id1"], row["person_id2"])].append(
                    f"base:{row.get('evidence', 'negative')}")
        for row in self.review_rows:
            if (row.get("review_label") == "different"
                    and row.get("kind", "cross_track") != "track_purity"):
                negatives[relation(row["person_id1"], row["person_id2"])].append(
                    row.get("candidate_id", "review"))
        return dict(negatives)

    @cached_property
    def contaminated_tracks(self) -> dict[str, str]:
        """Tracks a reviewer marked as containing more than one object."""
        return {row["person_id1"]: row.get("candidate_id", "review")
                for row in self.review_rows
                if row.get("kind") == "track_purity" and row.get("review_label") == "different"}


def shortest_same_path(adjacency: dict[str, list[tuple[str, str]]], source: str,
                       target: str) -> tuple[list[str], list[str]] | None:
    """BFS a ``same`` chain, returning the identity path and its witnesses.

    Takes a prebuilt adjacency: this runs once per conflict, and rebuilding the
    whole graph on each call made ``check_split_leakage`` cubic in the dataset
    (measured 82s on 800 identities, 98% of it here).
    """
    if source not in adjacency or target not in adjacency:
        return None
    previous: dict[str, tuple[str, str]] = {source: ("", "")}
    queue = [source]
    while queue:
        current = queue.pop(0)
        if current == target:
            path, witnesses = [target], []
            while path[-1] != source:
                parent, witness = previous[path[-1]]
                witnesses.append(witness)
                path.append(parent)
            return list(reversed(path)), list(reversed(witnesses))
        for neighbour, witness in adjacency.get(current, ()):
            if neighbour not in previous:
                previous[neighbour] = (current, witness)
                queue.append(neighbour)
    return None


def cluster_witnesses(adjacency: dict[str, list[tuple[str, str]]],
                      members: list[str]) -> list[str]:
    """The same-edges that hold one merged cluster together.

    A spanning tree answers the reviewer's question — "which links merged
    these identities, so which one do I break?" — with one BFS. The previous
    version unioned the shortest paths of every cross-split member pair, which
    is the same set of links on any real cluster shape but costs O(members^2)
    BFS runs to say it.
    """
    seen = set()
    witnesses: list[str] = []
    for start in members:
        if start in seen:
            continue
        seen.add(start)
        queue = [start]
        while queue:
            current = queue.pop(0)
            for neighbour, witness in adjacency.get(current, ()):
                if neighbour in seen:
                    continue
                seen.add(neighbour)
                witnesses.append(witness)
                queue.append(neighbour)
    return witnesses


def check_transitive(view: DatasetView) -> list[Conflict]:
    # One impossible merged identity can violate dozens of negative relations.
    # Reporting every relation as a separate conflict made the reviewer reopen
    # the same prefix of same-edges over and over. Group intersecting negative
    # paths while retaining every endpoint pair and its shortest explanation.
    candidates = []
    for (left, right), sources in sorted(view.negative_edges.items()):
        if left == right or not view.graph.same(left, right):
            continue
        found = shortest_same_path(view.adjacency, left, right)
        path, witnesses = found if found else ([left, right], [])
        candidates.append({
            "endpoints": [left, right], "path": path,
            "same_witnesses": witnesses, "different_witnesses": sources,
        })

    # A positive component can be very large and contain two unrelated bad
    # merges. Only coalesce constraints whose explanatory paths touch (with
    # transitive closure), which is the review surface they genuinely share.
    parents = list(range(len(candidates)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first: int, second: int) -> None:
        first, second = find(first), find(second)
        if first != second:
            parents[second] = first

    owner: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        for identity in candidate["path"]:
            if identity in owner:
                union(index, owner[identity])
            else:
                owner[identity] = index
    grouped: defaultdict[int, list[dict]] = defaultdict(list)
    for index, candidate in enumerate(candidates):
        grouped[find(index)].append(candidate)

    conflicts = []
    for constraints in grouped.values():
        identities = list(dict.fromkeys(
            identity for constraint in constraints for identity in constraint["path"]))
        witnesses = sorted({witness for constraint in constraints
                            for witness in (constraint["same_witnesses"]
                                            + constraint["different_witnesses"])})
        first = constraints[0]
        if len(constraints) == 1:
            left, right = first["endpoints"]
            message = f"{left} and {right} are proven different but a same-chain merges them"
        else:
            message = (f"one connected same-chain merges {len(constraints)} proven-different "
                       f"pairs across {len(identities)} identities")
        detail = {
            "same_paths": constraints,
            "negative_constraints": len(constraints),
        }
        # Preserve the original JSON shape for single-pair consumers.
        if len(constraints) == 1:
            detail.update({"same_path": first["path"],
                           "same_witnesses": first["same_witnesses"],
                           "different_witnesses": first["different_witnesses"]})
        conflicts.append(Conflict(
            "transitive_negative", "error",
            message, identities=identities, witnesses=witnesses, detail=detail))
    return conflicts


def check_direct_contradiction(view: DatasetView) -> list[Conflict]:
    votes: defaultdict[tuple[str, str], defaultdict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list))
    for row in view.base_rows:
        key = relation(row["person_id1"], row["person_id2"])
        votes[key]["same" if int(row["label"]) == 1 else "different"].append(
            f"base:{row.get('evidence', '')}")
    for row in view.review_rows:
        if row.get("kind", "cross_track") == "track_purity":
            continue
        label = row.get("review_label", "")
        if label in {"same", "different"}:
            key = relation(row["person_id1"], row["person_id2"])
            votes[key][label].append(row.get("candidate_id", "review"))
    return [Conflict("direct_contradiction", "error",
                     f"{left} vs {right} is labelled both same and different",
                     identities=[left, right],
                     witnesses=sorted(set(sides["same"] + sides["different"])),
                     detail={"same": sorted(set(sides["same"])),
                             "different": sorted(set(sides["different"]))})
            for (left, right), sides in sorted(votes.items())
            if left != right and sides["same"] and sides["different"]]


def check_covisible_merge(view: DatasetView) -> list[Conflict]:
    conflicts = []
    for row in view.covisible_rows:
        left, right = row["person_id1"], row["person_id2"]
        if not view.graph.same(left, right):
            continue
        found = shortest_same_path(view.adjacency, left, right)
        path, witnesses = found if found else ([left, right], [])
        conflicts.append(Conflict(
            "covisible_merge", "error",
            f"{left} and {right} were seen together in {row['frames']} frames "
            f"of {row.get('video', '?')} but are merged into one identity",
            identities=path, witnesses=sorted(set(witnesses)),
            detail={"same_path": path, "covisible_frames": int(row["frames"]),
                    "video": row.get("video", ""),
                    "first_timestamp": row.get("first_timestamp", "")}))
    return conflicts


def check_temporal_overlap(view: DatasetView) -> list[Conflict]:
    if not view.track_rows:
        return []
    spans: defaultdict[str, list[dict]] = defaultdict(list)
    for row in view.track_rows:
        if row.get("status", "accepted") != "accepted":
            continue
        spans[view.graph.find(row["person_id"])].append(row)
    conflicts = []
    for cluster, rows in sorted(spans.items()):
        rows.sort(key=lambda row: float(row["start"]))
        for index, first in enumerate(rows):
            for second in rows[index + 1:]:
                if first.get("video") != second.get("video"):
                    continue
                overlap = min(float(first["end"]), float(second["end"])) - float(second["start"])
                if overlap <= 0:
                    continue
                found = shortest_same_path(view.adjacency, first["person_id"],
                                           second["person_id"])
                path, witnesses = found if found else ([first["person_id"],
                                                        second["person_id"]], [])
                conflicts.append(Conflict(
                    "temporal_overlap", "error",
                    f"identity {cluster} exists twice for {overlap:.1f}s in "
                    f"{first.get('video', '?')} ({first['person_id']} and "
                    f"{second['person_id']})",
                    identities=path, witnesses=sorted(set(witnesses)),
                    detail={"cluster": cluster, "overlap_sec": round(overlap, 3),
                            "video": first.get("video", ""), "same_path": path}))
    return conflicts


def check_split_leakage(view: DatasetView) -> list[Conflict]:
    split_of = view.split_of_identity
    clusters: defaultdict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for identity, split in split_of.items():
        clusters[view.graph.find(identity)][split].add(identity)
    conflicts = []
    for cluster, by_split in sorted(clusters.items()):
        if len(by_split) < 2:
            continue
        members = sorted(identity for group in by_split.values() for identity in group)
        witnesses = cluster_witnesses(view.adjacency, members)
        conflicts.append(Conflict(
            "split_leakage", "error",
            f"identity {cluster} spans splits {sorted(by_split)}",
            identities=members, witnesses=sorted(set(witnesses)),
            detail={"cluster": cluster,
                    "splits": {split: sorted(group) for split, group in sorted(by_split.items())}}))

    seen: defaultdict[str, set[str]] = defaultdict(set)
    for row in view.identity_rows:
        seen[row["img_path"]].add(row["split"])
    conflicts.extend(Conflict("split_leakage", "error",
                              f"image {path} appears in splits {sorted(splits)}",
                              detail={"img_path": path, "splits": sorted(splits)})
                     for path, splits in sorted(seen.items()) if len(splits) > 1)
    return conflicts


def check_split_mismatch(view: DatasetView) -> list[Conflict]:
    """A pair row whose split disagrees with the split its crops were extracted into."""
    split_of = view.split_of_identity
    if not split_of:
        return []
    offenders: defaultdict[tuple[str, str, str], list[str]] = defaultdict(list)
    for row in view.base_rows:
        for key in ("person_id1", "person_id2"):
            identity = row[key]
            declared = split_of.get(identity)
            if declared is not None and declared != row["split"]:
                offenders[(identity, declared, row["split"])].append(
                    f"base:{row.get('evidence', '')}")
    for row in view.review_rows:
        identity = row.get("person_id1", "")
        declared = split_of.get(identity)
        if declared is not None and row.get("split") and declared != row["split"]:
            offenders[(identity, declared, row["split"])].append(
                row.get("candidate_id", "review"))
    return [Conflict("split_mismatch", "error",
                     f"{identity} lives in split {declared} but is used in {used} pairs",
                     identities=[identity], witnesses=sorted(set(witnesses))[:8],
                     detail={"identity_split": declared, "pair_split": used,
                             "rows": len(witnesses)})
            for (identity, declared, used), witnesses in sorted(offenders.items())]


def check_self_negative(view: DatasetView) -> list[Conflict]:
    conflicts = []
    for row in view.base_rows:
        if int(row["label"]) == 0 and row["person_id1"] == row["person_id2"]:
            conflicts.append(Conflict(
                "self_negative", "error",
                f"{row['person_id1']} is labelled different from itself",
                identities=[row["person_id1"]],
                witnesses=[f"base:{row.get('evidence', '')}"],
                detail={"img1": row.get("img1", ""), "img2": row.get("img2", "")}))
    for row in view.review_rows:
        if (row.get("review_label") == "different"
                and row["person_id1"] == row["person_id2"]
                and row.get("kind", "cross_track") != "track_purity"):
            conflicts.append(Conflict(
                "self_negative", "error",
                f"{row['person_id1']} is labelled different from itself "
                "(use a track_purity candidate to reject a mixed track)",
                identities=[row["person_id1"]],
                witnesses=[row.get("candidate_id", "review")], detail={}))
    return conflicts


def check_references(view: DatasetView) -> list[Conflict]:
    known = {row["person_id"] for row in view.identity_rows}
    images = {row["img_path"] for row in view.identity_rows}
    conflicts: list[Conflict] = []
    if not known:
        return conflicts
    missing_identity: dict[str, str] = {}
    missing_image: dict[str, str] = {}
    for row in view.base_rows:
        for key in ("person_id1", "person_id2"):
            if row[key] not in known:
                missing_identity.setdefault(row[key], f"base:{row.get('evidence', '')}")
        for key in ("img1", "img2"):
            if row.get(key) and row[key] not in images:
                missing_image.setdefault(row[key], f"base:{row.get('evidence', '')}")
    for row in view.review_rows:
        for key in ("person_id1", "person_id2"):
            if row.get(key) and row[key] not in known:
                missing_identity.setdefault(row[key], row.get("candidate_id", "review"))
    conflicts.extend(Conflict("unknown_identity", "error",
                              f"{identity} is referenced but absent from identities.csv",
                              identities=[identity], witnesses=[witness])
                     for identity, witness in sorted(missing_identity.items()))
    conflicts.extend(Conflict("missing_image", "error",
                              f"{path} is referenced but absent from identities.csv",
                              witnesses=[witness], detail={"img_path": path})
                     for path, witness in sorted(missing_image.items()))
    absent = sorted(row["img_path"] for row in view.identity_rows
                    if not (view.root / row["img_path"]).is_file())
    conflicts.extend(Conflict("missing_image", "error",
                              f"{path} is listed in identities.csv but not on disk",
                              detail={"img_path": path})
                     for path in absent[:200])
    return conflicts


def check_domain(view: DatasetView) -> list[Conflict]:
    """Cross-day / cross-camera relations are outside the operating domain.

    Clothes change between days, and without covisibility evidence a cross-day
    pair cannot even be trusted as a negative — the two tracks might be the
    same person. Production keeps an independent gallery per camera, so
    cross-camera relations never occur online either. Hence any labelled base
    row is an error whatever its label, a reviewed same/different is an error,
    and an unclear only a warning (it never enters training). track_purity
    rows ask about a single track and are exempt; ids without a parseable
    domain are not judged.
    """
    grouped: dict[tuple[str, str], dict] = {}

    def offend(left: str, right: str, witness: str, severity: str, note: str) -> None:
        if left == right:
            return
        first, second = relation(left, right)
        first_domain, second_domain = identity_domain(first), identity_domain(second)
        if first_domain is None or second_domain is None:
            return
        if first_domain[1] != second_domain[1]:
            kind = "cross_day_pair"
        elif first_domain[0] != second_domain[0]:
            kind = "cross_camera_pair"
        else:
            return
        entry = grouped.setdefault((first, second), {
            "kind": kind, "witnesses": set(), "notes": set(), "rows": 0,
            "severity": "warning",
            "domains": [f"{domain[0] or 'cam?'}/{domain[1]}"
                        for domain in (first_domain, second_domain)]})
        entry["witnesses"].add(witness)
        entry["notes"].add(note)
        entry["rows"] += 1
        if severity == "error":
            entry["severity"] = "error"

    for row in view.base_rows:
        offend(row["person_id1"], row["person_id2"],
               f"base:{row.get('evidence', '')}", "error",
               f"pairs.csv label={row['label']}")
    for row in view.review_rows:
        if row.get("kind", "cross_track") == "track_purity":
            continue
        label = row.get("review_label", "")
        if label not in {"same", "different", "unclear"}:
            continue
        offend(row["person_id1"], row["person_id2"],
               row.get("candidate_id", "review"),
               "warning" if label == "unclear" else "error", f"review {label}")

    return [Conflict(entry["kind"], entry["severity"],
                     f"{left} and {right} cross the operating domain "
                     f"({entry['domains'][0]} vs {entry['domains'][1]}); appearance "
                     "comparison is same-day, same-camera only",
                     identities=[left, right],
                     witnesses=sorted(entry["witnesses"])[:8],
                     detail={"rows": entry["rows"], "labels": sorted(entry["notes"]),
                             "domains": entry["domains"]})
            for (left, right), entry in sorted(grouped.items())]


def check_contaminated_tracks(view: DatasetView) -> list[Conflict]:
    contaminated = view.contaminated_tracks
    if not contaminated:
        return []
    active = {row["person_id1"] for row in view.base_rows if int(row["label"]) == 1}
    active |= {row["person_id2"] for row in view.base_rows if int(row["label"]) == 1}
    return [Conflict("contaminated_track", "warning",
                     f"{identity} was reviewed as a mixed track but still carries "
                     "positive pairs; finalize will drop it",
                     identities=[identity], witnesses=[witness])
            for identity, witness in sorted(contaminated.items()) if identity in active]


CHECKS = (check_transitive, check_direct_contradiction, check_covisible_merge,
          check_temporal_overlap, check_domain, check_split_leakage,
          check_split_mismatch, check_self_negative, check_references,
          check_contaminated_tracks)


def detect(view: DatasetView) -> list[Conflict]:
    conflicts: list[Conflict] = []
    for check in CHECKS:
        conflicts.extend(check(view))
    order = {"error": 0, "warning": 1}
    conflicts.sort(key=lambda item: (order[item.severity], item.kind, item.message))
    return conflicts


def report(root: Path, base_pairs: Path, reviews: list[Path],
           output: Path | None = None) -> dict:
    view = DatasetView.load(root, base_pairs, reviews)
    conflicts = detect(view)
    pending = sum(1 for row in view.review_rows if not row.get("review_label"))
    value = {
        "schema": 1, "dataset_root": str(root), "base_pairs": str(base_pairs),
        "review_files": [str(path) for path in reviews],
        "errors": sum(1 for item in conflicts if item.severity == "error"),
        "warnings": sum(1 for item in conflicts if item.severity == "warning"),
        "pending_reviews": pending,
        "by_kind": {kind: sum(1 for item in conflicts if item.kind == kind)
                    for kind in sorted({item.kind for item in conflicts})},
        "conflicts": [asdict(item) for item in conflicts],
    }
    if output is not None:
        atomic_write_json(output, value)
    return value


def main_text(value: dict) -> str:
    lines = [f"errors={value['errors']} warnings={value['warnings']} "
             f"pending_reviews={value['pending_reviews']}"]
    for item in value["conflicts"][:50]:
        witnesses = ", ".join(item["witnesses"][:6])
        lines.append(f"[{item['severity']}] {item['kind']}: {item['message']}"
                     + (f"  <- {witnesses}" if witnesses else ""))
    if len(value["conflicts"]) > 50:
        lines.append(f"... {len(value['conflicts']) - 50} more (see JSON report)")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    import sys
    print(json.dumps(report(Path(sys.argv[1]), Path(sys.argv[2]),
                            [Path(path) for path in sys.argv[3:]]), indent=2))
