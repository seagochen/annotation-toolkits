"""Where every relation answer came from, and what the web may do about it.

The conflict page does not just list contradictions: it expands the whole
same-chain behind one, shows each edge's crops and every record that asserts
it, and lets the reviewer answer the relation in place. That needs an index no
other part of the app wants — every review round ever written, indexed by pair,
with each answer's round, source, similarity metric and the model that measured
it, joined against the pair manifest by exact line number.

Keeping it beside the HTTP layer made ``server.py`` three unrelated jobs in one
file. Here it is one: read the provenance, decide what it means, and hand the
web app a decorated report. Nothing in this module writes.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from . import revision
from .core import read_csv, relation

# Similarity columns the different mining generations wrote, most recent first.
# Direction matters when ranking weak edges: a SMALL cosine is a weak same-edge,
# but a SMALL distance is a strong one.
METRIC_COLUMNS = (
    ("cosine", "lower_is_weaker"),
    ("cosine_similarity", "lower_is_weaker"),
    ("distance", "higher_is_weaker"),
    ("centroid_distance", "higher_is_weaker"),
)


def candidate_metric(row: dict) -> dict | None:
    """Extract whichever similarity column this round's schema recorded."""
    for name, direction in METRIC_COLUMNS:
        raw = (row.get(name) or "").strip()
        if not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        return {"name": name, "value": value, "direction": direction}
    return None


def weakness(answer: dict) -> float:
    """Monotone weakness score: larger always means a weaker same-edge."""
    value = answer["metric_value"]
    return -value if answer["metric_direction"] == "lower_is_weaker" else value


def weakest_edge_index(edges: list[dict]) -> tuple[int | None, str, dict | None]:
    """Rank the weakest same-edge only when the whole chain is comparable.

    Review rounds recorded different similarity columns (cosine vs distance
    flavours) measured by different models, and those scales are mutually
    meaningless: a cosine of 0.62 says nothing against a Euclidean distance of
    0.62. An edge may carry several measurements (a pair can be re-asked in a
    later round), so comparability means one (metric, model) key is shared by
    every edge. Anything else refuses to rank and reports why, instead of
    pretending an ordering exists. Only answers that actually support the edge
    (label == "same") are ranked: a different/unclear verdict's measurement is
    not a witness of the same-edge and must not move the "review first" badge.
    Returns (index or None, status, shared key or None); status is sortable /
    missing_metrics / mixed_metrics / ambiguous_metrics.
    """
    per_edge: list[dict] = []
    for edge in edges:
        keyed: dict[tuple, float] = {}
        for answer in edge["answers"]:
            if "metric_value" not in answer or answer.get("label") != "same":
                continue
            key = (answer["metric_name"], answer["model_or_source"])
            score = weakness(answer)
            # keep the weakest measurement per key so a re-asked pair does not
            # hide a weak answer behind a later strong one
            if key not in keyed or score > keyed[key]:
                keyed[key] = score
        per_edge.append(keyed)
    if not per_edge or any(not keyed for keyed in per_edge):
        return None, "missing_metrics", None
    shared = set(per_edge[0])
    for keyed in per_edge[1:]:
        shared &= set(keyed)
    if not shared:
        return None, "mixed_metrics", None
    if len(shared) > 1:
        return None, "ambiguous_metrics", None
    key = shared.pop()
    scores = [keyed[key] for keyed in per_edge]
    return scores.index(max(scores)), "sortable", {"name": key[0], "model": key[1]}



def edge_decision(answers: list[dict], base_rows: list[dict]) -> dict:
    """What one relation currently says, and what a web verdict would do to it.

    The conflict page judges relations in place, so every chain card needs two
    things raw provenance does not give: which of the three buttons is in
    force right now (so it can be highlighted), and which write path a click
    would take. Precedence follows the dataset's correction rule — the newest
    human judgement wins — so a live-round answer outranks a baked pairs.csv
    row, which outranks a historical round the web cannot rewrite. Sources
    that disagree highlight nothing and say so: picking a winner there would
    hide exactly the contradiction the page exists to surface.
    """
    archives = [row for row in base_rows
                if row["evidence"].startswith(revision.REVISABLE_PREFIX)]
    physical = [row for row in base_rows
                if not row["evidence"].startswith(revision.REVISABLE_PREFIX)]
    positives = [row for row in base_rows if row["label"] == 1]
    negatives = [row for row in base_rows if row["label"] == 0]
    live = [answer for answer in answers if answer["editable"]]
    live_labels = sorted({answer["label"] for answer in live})
    history = sorted({answer["label"] for answer in answers if not answer["editable"]})

    verdict, conflicting = "", False
    if len(live_labels) > 1:
        origin = ("当前轮多个候选给出不同判定："
                  + "、".join(f"{answer['candidate_id']}→{answer['label']}"
                              for answer in live))
        conflicting = True
    elif live:
        origin = f"当前轮候选 {live[0]['candidate_id']}"
        verdict = live[0]["label"]
    elif positives and negatives:
        origin = (f"pairs.csv 同一关系上同时有 {len(positives)} 行 label=1 "
                  f"和 {len(negatives)} 行 label=0")
        conflicting = True
    elif positives:
        origin, verdict = f"pairs.csv {len(positives)} 行 label=1", "same"
    elif negatives:
        origin, verdict = f"pairs.csv {len(negatives)} 行 label=0", "different"
    elif len(history) == 1:
        rounds = sorted({answer["round"] for answer in answers})
        origin, verdict = f"历史轮次 {'、'.join(rounds)}", history[0]
    elif history:
        origin = f"历史轮次结论不一致（{'、'.join(history)}）"
        conflicting = True
    else:
        origin = "尚无任何判定"
    return {
        "verdict": verdict, "origin": origin, "conflicting": conflicting,
        "candidate_id": live[0]["candidate_id"] if live else "",
        "action": "revise" if archives else "label",
        "archives": archives, "physical": physical,
    }



class Provenance:
    """Read-only provenance index over one dataset's review history.

    Thread-safe and self-invalidating: the index is keyed by an mtime/size
    snapshot of every file it derives from, so a saved label, an explicit
    refresh and the background recompute thread all see the same rebuild rule.
    """

    def __init__(self, root: Path, candidates: Path, base_pairs: Path,
                 reviews: list[Path]):
        self.root, self.candidates = root, candidates
        self.base_pairs, self.reviews = base_pairs, reviews
        self.lock = threading.RLock()
        # (mtime snapshot, provenance index) — see context()
        self._detail_cache: tuple | None = None
        # sha256 per (model path, mtime, size) — see _model_key
        self._model_hash_cache: dict[tuple, str] = {}

    def context(self) -> dict:
        """Provenance index for conflict decoration, keyed by an mtime snapshot.

        A permanent cache would go stale the moment the reviewer saves a label
        (candidates.csv is rewritten atomically), so every call stats the files
        the index is derived from and rebuilds only when any of them changed.
        That covers all three refresh triggers the same way: a saved label, an
        explicit refresh=1, and the background recompute thread.
        """
        review_dir = self.root / "review"
        discovered = sorted(review_dir.rglob("candidates.csv")) if review_dir.is_dir() else []
        # The CLI accepts arbitrary --review paths; those relations join the
        # conflict computation, so the detail page must resolve their
        # provenance too, not only what lives under <root>/review/**.
        files: list[Path] = []
        seen: set[Path] = set()
        for path in discovered + list(self.reviews) + [self.candidates]:
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(path)
        watched = [self.base_pairs, self.root / "tracks.csv", self.root / "covisibility.csv"]
        for path in files:
            watched += [path, path.parent / "candidate_source.csv",
                        path.parent / "report.json", path.parent / "mine.report.json"]
        # mtime alone can miss two writes inside one filesystem timestamp
        # tick, so the size joins the key; equal-size same-tick edits remain
        # theoretically invisible, the same caveat _reload_candidates accepts.
        def stamp(path: Path) -> tuple:
            try:
                stat = path.stat()
            except OSError:
                return (-1, -1)
            return (stat.st_mtime_ns, stat.st_size)

        snapshot = tuple((str(path), stamp(path)) for path in watched)
        with self.lock:
            if self._detail_cache is not None and self._detail_cache[0] == snapshot:
                return self._detail_cache[1]
        context = self._build_context(files)
        with self.lock:
            self._detail_cache = (snapshot, context)
        return context

    def _model_key(self, recorded: str) -> tuple[str, str]:
        """Comparison key for "same model", plus the recorded path for display.

        Two rounds recording the same path do not prove they used the same
        weights, and two different paths may hold identical weights — so the
        key is the file content's SHA-256 whenever the recorded file is still
        readable, and the bare path (marked as such) only as a fallback.
        """
        if not recorded:
            return "", ""
        model = Path(recorded)
        try:
            stat = model.stat()
        except OSError:
            return f"path:{recorded}", recorded
        cache_key = (recorded, stat.st_mtime_ns, stat.st_size)
        digest = self._model_hash_cache.get(cache_key)
        if digest is None:
            hasher = hashlib.sha256()
            with model.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    hasher.update(chunk)
            digest = hasher.hexdigest()
            self._model_hash_cache[cache_key] = digest
        return f"sha256:{digest}", recorded

    def _build_context(self, files: list[Path]) -> dict:
        """Read every provenance file once and index it for edge lookups."""
        answers_by_pair: dict[tuple, list[dict]] = {}
        answers_by_candidate: dict[str, list[dict]] = {}
        live_pairs: dict[str, tuple] = {}
        current = self.candidates.resolve()
        for path in files:
            if not path.is_file():
                continue
            try:
                round_name = path.relative_to(self.root / "review").parts[0]
            except ValueError:
                round_name = path.parent.name or path.stem
            try:
                file_name = str(path.relative_to(self.root))
            except ValueError:
                file_name = str(path)
            # candidate_source.csv is written only by the newest mining runs;
            # older rounds fall back to the candidate row and its file path.
            source_file = path.parent / "candidate_source.csv"
            sources = ({row.get("candidate_id", ""): row.get("source", "")
                        for row in read_csv(source_file)} if source_file.is_file() else {})
            # The mining report records which model ranked this round. Without a
            # recorded model the round name stands in, which by construction can
            # never equal another round's model string, so cross-round metric
            # comparisons stay disabled unless both rounds prove the same model.
            model = ""
            for report_name in ("report.json", "mine.report.json"):
                report = path.parent / report_name
                if report.is_file():
                    try:
                        model = str(json.loads(report.read_text(encoding="utf-8"))
                                    .get("model") or "")
                    except (OSError, json.JSONDecodeError):
                        model = ""
                    if model:
                        break
            model_key, model_path = self._model_key(model)
            editable_file = path.resolve() == current
            for row in read_csv(path):
                if editable_file:
                    # Every live row, labelled or not: this is the set /api/label
                    # can actually rewrite, and a chain card may only offer a
                    # button for a row that is still in the queue on this pair.
                    live_pairs[row.get("candidate_id", "")] = relation(
                        row.get("person_id1", ""), row.get("person_id2", ""))
                label = row.get("review_label", "")
                if not label:
                    continue
                answer = {
                    "round": round_name, "file": file_name,
                    "candidate_id": row.get("candidate_id", ""),
                    "kind": row.get("kind", "cross_track"), "label": label,
                    "source": sources.get(row.get("candidate_id", ""), ""),
                    "notes": row.get("review_notes", ""),
                    "person_id1": row.get("person_id1", ""),
                    "person_id2": row.get("person_id2", ""),
                    "model_or_source": model_key or round_name,
                    "model_path": model_path,
                    "editable": editable_file,
                }
                metric = candidate_metric(row)
                if metric:
                    answer["metric_name"] = metric["name"]
                    answer["metric_value"] = metric["value"]
                    answer["metric_direction"] = metric["direction"]
                key = relation(answer["person_id1"], answer["person_id2"])
                answers_by_pair.setdefault(key, []).append(answer)
                answers_by_candidate.setdefault(answer["candidate_id"], []).append(answer)

        # pairs.csv indexed with real line numbers (header occupies line 1), so
        # a base edge can be pointed at exactly, per (pair, label, evidence) row
        # — the same evidence name stands on many unrelated relations.
        base_by_pair: dict[tuple, list[dict]] = {}
        if self.base_pairs.is_file():
            for index, row in enumerate(read_csv(self.base_pairs)):
                entry = {"line": index + 2, "label": int(row["label"]),
                         "evidence": row.get("evidence", ""),
                         "batch": row.get("batch", ""), "split": row.get("split", "")}
                base_by_pair.setdefault(
                    relation(row["person_id1"], row["person_id2"]), []).append(entry)

        track_of: dict[str, dict] = {}
        tracks = self.root / "tracks.csv"
        if tracks.is_file():
            for row in read_csv(tracks):
                if row.get("status", "accepted") == "accepted":
                    track_of[row["person_id"]] = row

        covisible_by_pair: dict[tuple, dict] = {}
        covisibility = self.root / "covisibility.csv"
        if covisibility.is_file():
            for row in read_csv(covisibility):
                covisible_by_pair[relation(row["person_id1"], row["person_id2"])] = row

        return {"answers_by_pair": answers_by_pair,
                "answers_by_candidate": answers_by_candidate,
                "base_by_pair": base_by_pair, "track_of": track_of,
                "covisible_by_pair": covisible_by_pair, "live_pairs": live_pairs}

    def pair_answers(self, key: tuple, context: dict) -> list[dict]:
        """Every recorded relation answer for one pair, with editability resolved.

        ``editable`` in the index only means "this row lives in the live file";
        a chain card may only offer to rewrite a row /api/label can actually
        reach, so the id must also still be in the queue naming this same pair.
        A candidate from a past round must not pretend to be editable.
        """
        answers = []
        for stored in context["answers_by_pair"].get(key, []):
            if stored.get("kind", "cross_track") == "track_purity":
                continue
            answer = dict(stored)
            answer["editable"] = (answer["editable"]
                                  and context["live_pairs"].get(answer["candidate_id"]) == key)
            answers.append(answer)
        return answers

    def _conflict_detail(self, conflict: dict, context: dict) -> dict | None:
        """Edge-by-edge expansion of the same-chain behind a conflict.

        Strictly walks adjacent pairs of detail.same_path; provenance is
        resolved per pair from the indexed rows, never by aggregating the
        conflict's witness names, because one base evidence name stands on many
        unrelated relations.
        """
        detail = conflict.get("detail") or {}
        paths = [list(item.get("path") or []) for item in detail.get("same_paths", [])]
        if not paths:
            path = list(detail.get("same_path") or [])
            if not path and conflict.get("kind") == "direct_contradiction":
                path = list(conflict.get("identities") or [])
            paths = [path] if path else []
        paths = [path for path in paths if len(path) >= 2]
        if not paths:
            return None
        path = list(dict.fromkeys(identity for one_path in paths for identity in one_path))
        edges = []
        seen_edges = set()
        for one_path in paths:
            for left, right in zip(one_path, one_path[1:]):
                key = relation(left, right)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                answers = self.pair_answers(key, context)
                base_rows = context["base_by_pair"].get(key, [])
                edges.append({"left": left, "right": right, "base_rows": base_rows,
                              "answers": answers,
                              "decision": edge_decision(answers, base_rows)})
        weakest, metric_status, metric_key = weakest_edge_index(edges)
        if metric_key:
            # the key may be a content hash; surface the recorded path for display
            metric_key["model_path"] = next(
                (answer.get("model_path", "") for edge in edges
                 for answer in edge["answers"]
                 if answer.get("model_or_source") == metric_key["model"]), "")

        endpoint_pairs = [list(item.get("endpoints") or [one_path[0], one_path[-1]])
                          for item, one_path in zip(detail.get("same_paths", []), paths)]
        if not endpoint_pairs:
            endpoint_pairs = [[paths[0][0], paths[0][-1]]]
        contradictions = [self._endpoint_contradiction(left, right, context)
                          for left, right in endpoint_pairs]
        return {
            "path": path, "endpoints": endpoint_pairs[0],
            "endpoint_pairs": endpoint_pairs, "edges": edges,
            "group_key": ">".join(sorted(path)),
            "metric_status": metric_status, "metric_key": metric_key,
            "weakest_index": weakest,
            "contradiction": contradictions[0], "contradictions": contradictions,
        }

    def _endpoint_contradiction(self, left: str, right: str, context: dict) -> dict:
        """Merge all evidence saying one pair of chain endpoints differs."""
        key = relation(left, right)
        endpoint_base = context["base_by_pair"].get(key, [])
        endpoint_answers = self.pair_answers(key, context)
        negatives = [row for row in endpoint_base if row["label"] == 0]
        different_answers = [answer for answer in endpoint_answers
                             if answer["label"] == "different"]
        first, second = context["track_of"].get(left), context["track_of"].get(right)
        temporal = None
        if (first and second and first.get("video")
                and first.get("video") == second.get("video")):
            overlap = (min(float(first["end"]), float(second["end"]))
                       - max(float(first["start"]), float(second["start"])))
            if overlap > 0:
                temporal = {"video": first.get("video", ""),
                            "overlap_sec": round(overlap, 3)}
        covisible_row = context["covisible_by_pair"].get(key)
        covisible = ({"video": covisible_row.get("video", ""),
                      "frames": int(covisible_row.get("frames", 0) or 0)}
                     if covisible_row else None)
        return {
            "endpoints": [left, right],
            "negative_rows": negatives[:50],
            "negative_row_count": len(negatives),
            "different_answers": different_answers,
            "temporal": temporal, "covisible": covisible,
            # The endpoints are a relation too: sometimes the chain is right
            # and the negative answer is the judgement that needs changing.
            "decision": edge_decision(endpoint_answers, endpoint_base),
        }

    def _witness_chain(self, conflict: dict, context: dict) -> list[dict]:
        """Expand each witness into something a reviewer can act on.

        A witness reading `base:<evidence>` is not a candidate and cannot be
        clicked: it is one or more rows of the pair manifest, usually written
        there by an earlier review round that finalize has already baked in.
        Showing only its name tells a reviewer that a contradiction exists while
        hiding both who decided it and what would have to change, so the round
        of origin and the affected row count are resolved here. Edge-level
        provenance (exact pairs.csv line numbers per adjacent pair) lives in
        _conflict_detail instead.
        """
        identities = conflict.get("identities") or []
        if len(identities) < 2:
            return []
        # `identities` is the union of every grouped constraint's path, so its
        # first/last entries no longer name a real relation once a conflict
        # holds more than one endpoint pair. Look up each pair's own evidence
        # instead of guessing one from the merged identity list.
        detail = conflict.get("detail") or {}
        endpoint_pairs = [tuple(item["endpoints"]) for item in detail.get("same_paths", [])
                          if item.get("endpoints")]
        if not endpoint_pairs:
            endpoint_pairs = [(identities[0], identities[-1])]
        keys = [relation(left, right) for left, right in endpoint_pairs]
        endpoint_rows = [row for key in keys for row in context["base_by_pair"].get(key, [])]
        answers = context["answers_by_candidate"]

        # Which round originally said "different" about any of these relations.
        origin_rounds = sorted({entry["round"] for entries in answers.values()
                                for entry in entries if entry["label"] == "different"
                                and relation(entry["person_id1"],
                                             entry["person_id2"]) in keys})

        chain = []
        for witness in dict.fromkeys(conflict.get("witnesses") or []):
            if witness.startswith("base:"):
                evidence = witness[len("base:"):]
                matched = [row for row in endpoint_rows if row["evidence"] == evidence]
                chain.append({
                    "witness": witness, "type": "base", "editable": False,
                    "evidence": evidence, "rows": len(matched),
                    "batches": sorted({row["batch"] for row in matched if row["batch"]}),
                    "origin": origin_rounds,
                    "detail": (f"{len(matched)} 行在 pairs.csv 中"
                               + (f"；原判来自 {'、'.join(origin_rounds)}" if origin_rounds else "")
                               + "；当前版本不支持网页推翻基础边"),
                })
            else:
                entries = answers.get(witness, [])
                chain.append({
                    "witness": witness, "type": "candidate",
                    "editable": any(entry["editable"] for entry in entries),
                    "answers": [{"round": entry["round"], "label": entry["label"],
                                 "source": entry["source"] or "unrecorded",
                                 "notes": entry["notes"]} for entry in entries],
                    "detail": "；".join(
                        f"{entry['round']} → {entry['label']}（{entry['source'] or 'unrecorded'}）"
                        for entry in entries) or "尚未回答",
                })
        return chain

    def decorate(self, value: dict) -> dict:
        """Attach the actionable expansions to a raw engine report.

        Both the synchronous and the background refresh MUST come through here:
        the background path used to store the raw report, so one saved label
        made every previously decorated field vanish from /api/conflicts.
        """
        context = self.context()
        for conflict in value.get("conflicts", []):
            conflict["chain"] = self._witness_chain(conflict, context)
            conflict["conflict_detail"] = self._conflict_detail(conflict, context)
        return value
