"""Web-driven revision of baked-in human verdicts.

The dataset's correction rule: on one relation, the newest human judgement
supersedes every earlier human judgement — and only human judgements. Rows
whose evidence starts with ``reviewed_`` are earlier review rounds that
finalize baked into pairs.csv; physical evidence (``covisible_*``,
``same_continuous_track``) is machine-derived fact and can never be overruled
from the web — if a new verdict contradicts it, the conflict checks flag it
immediately, which is the desired outcome.

A revision therefore (1) removes the superseded ``reviewed_*`` rows — archived,
backed up once, hash-logged, the same provenance discipline as purge-domain —
and (2) records the new verdict as a labelled candidate in the live round, so
it flows through the normal review → finalize pipeline instead of being
written into pairs.csv directly.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from .core import (atomic_write_csv, atomic_write_json, candidate_id, read_csv,
                   relation, sha256)
from .domain import archive_rows, backup_once, csv_fields

REVISABLE_PREFIX = "reviewed_"
WEB_SOURCE = "web_base_revision"
VERDICTS = ("same", "different", "unclear")


def revise(candidates: Path, base_pairs: Path, left: str, right: str,
           verdict: str, notes: str, injected_defaults: dict) -> dict:
    """Supersede the baked human rows for one relation and file the new verdict.

    ``injected_defaults`` carries split/img1/img2 for the case where the pair
    has no candidate in the live queue and one must be injected. The caller is
    responsible for serialising writers (the Store lock does this in the app).
    """
    if verdict not in VERDICTS:
        raise ValueError(f"unsupported verdict: {verdict}")
    key = relation(left, right)
    fields = csv_fields(base_pairs)
    kept, removed = [], []
    for line, row in enumerate(read_csv(base_pairs), start=2):  # header is line 1
        if (relation(row["person_id1"], row["person_id2"]) == key
                and row.get("evidence", "").startswith(REVISABLE_PREFIX)):
            removed.append({"line": line, **row})
        else:
            kept.append(row)
    if not removed:
        raise ValueError(
            f"no {REVISABLE_PREFIX}* rows to supersede for {key[0]} vs {key[1]}: "
            "physical evidence cannot be overruled from the web")

    round_dir = candidates.parent
    event = {
        "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "rule": "the newest human judgement supersedes earlier ones on the same relation",
        "pair": list(key), "verdict": verdict, "notes": notes, "removed": removed,
        "pairs_sha256_before": sha256(base_pairs),
        "backup_created": backup_once(base_pairs,
                                      round_dir / "pairs.before_web_revision.csv"),
    }
    archive_rows(round_dir / "pairs.overturned.csv",
                 [{key_: value for key_, value in row.items() if key_ != "line"}
                  for row in removed], fields)
    atomic_write_csv(base_pairs, kept, fields)
    event["pairs_sha256_after"] = sha256(base_pairs)
    event.update(file_verdict(candidates, key, verdict, notes, injected_defaults))

    log_path = round_dir / "revisions.json"
    log = (json.loads(log_path.read_text(encoding="utf-8"))
           if log_path.is_file() else {"schema": 1, "events": []})
    log["events"].append(event)
    atomic_write_json(log_path, log)
    return event


def file_verdict(candidates: Path, key: tuple[str, str], verdict: str, notes: str,
                 injected_defaults: dict) -> dict:
    """Record the new verdict in the live round.

    The pair's existing candidates are relabelled when there are any —
    *every* one of them, because the answer is about the relation and not
    about a row: older mining generations can leave two ids on one pair, and
    relabelling only the first would leave the queue self-contradictory with
    no way to fix it. Otherwise a labelled candidate is injected with a
    content-addressed id, and its origin is recorded in candidate_source.csv
    the way the miner would.
    """
    rows = read_csv(candidates)
    fields = csv_fields(candidates)
    matched = [row for row in rows
               if row.get("kind", "cross_track") != "track_purity"
               and relation(row["person_id1"], row["person_id2"]) == key]
    if matched:
        for row in matched:
            row["review_label"] = verdict
            if notes:
                row["review_notes"] = notes
        atomic_write_csv(candidates, rows, fields)
        return {"candidate_id": matched[0]["candidate_id"], "injected": False,
                "candidate_ids": [row["candidate_id"] for row in matched]}

    injected = {field: "" for field in fields}
    injected.update({
        "candidate_id": candidate_id("cross_track", *key), "kind": "cross_track",
        "person_id1": key[0], "person_id2": key[1],
        "review_label": verdict, "review_notes": notes or "web base revision",
        **injected_defaults,
    })
    atomic_write_csv(candidates, rows + [injected], fields)
    source_file = candidates.parent / "candidate_source.csv"
    sources = read_csv(source_file) if source_file.is_file() else []
    sources.append({"candidate_id": injected["candidate_id"], "source": WEB_SOURCE,
                    "selection_basis": notes or "revised from the conflict detail page"})
    atomic_write_csv(source_file, sources, ("candidate_id", "source", "selection_basis"))
    return {"candidate_id": injected["candidate_id"], "injected": True,
            "candidate_ids": [injected["candidate_id"]]}
