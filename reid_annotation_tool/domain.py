"""Archive out-of-domain relations: appearance comparison is same-day, same-camera only.

Clothes change between business days, and without covisibility evidence a
cross-day pair cannot even be trusted as a negative; production keeps an
independent gallery per camera, so cross-camera relations never occur online
(user decision, 2026-09-02). The purge therefore removes cross-domain rows
from both the live review queue and the pair manifest — but never deletes
information: removed rows go to archive CSVs beside the review round, the
first run backs up the original files, and every applied run appends an event
with before/after hashes to ``domain_purge.json``.
"""

from __future__ import annotations

import csv
import datetime
import json
from collections import Counter
from pathlib import Path

from .core import atomic_write_csv, atomic_write_json, domain_allows, read_csv, sha256

RULE = ("appearance comparison is same-day, same-camera only; "
        "cross-domain rows are archived, not deleted")


def split_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(kept, removed) — removed rows are those whose pair crosses the domain.

    Self pairs (track_purity) and ids without a parseable domain always stay:
    ``domain_allows`` already treats them as in-domain.
    """
    kept, removed = [], []
    for row in rows:
        target = kept if domain_allows(row.get("person_id1", ""), row.get("person_id2", ""),
                                       False, False) else removed
        target.append(row)
    return kept, removed


def csv_fields(path: Path) -> list[str]:
    """The file's own header order — real manifests carry columns (e.g. batch)
    that the historical field constants do not."""
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle).fieldnames or [])


def archive_rows(path: Path, rows: list[dict], fields: list[str]) -> None:
    """Append to an archive CSV, keeping rows from earlier purge runs."""
    existing = read_csv(path) if path.is_file() else []
    atomic_write_csv(path, existing + rows, fields)


def backup_once(source: Path, backup: Path) -> bool:
    """Preserve the pre-purge state; a second run must not overwrite it."""
    if backup.exists():
        return False
    backup.write_bytes(source.read_bytes())
    return True


def purge_file(source: Path, archive: Path, backup: Path, apply: bool) -> dict:
    """Dry-run or apply the purge of one CSV, returning its event record."""
    rows = read_csv(source)
    fields = csv_fields(source)
    kept, removed = split_rows(rows)
    record = {
        "file": str(source), "rows_before": len(rows), "removed": len(removed),
        "rows_after": len(kept),
        # which verdicts are being retired matters for the audit trail: a
        # cross-domain "same" was dirty data, a "pending" merely wasted effort
        "labels_removed": dict(Counter((row.get("review_label") or "pending")
                                       if "review_label" in fields else f"label={row['label']}"
                                       for row in removed)),
    }
    if not apply or not removed:
        return record
    record["sha256_before"] = sha256(source)
    record["backup"] = str(backup)
    record["backup_created"] = backup_once(source, backup)
    archive_rows(archive, removed, fields)
    record["archive"] = str(archive)
    atomic_write_csv(source, kept, fields)
    record["sha256_after"] = sha256(source)
    return record


def purge(root: Path, candidates: Path | list[Path], base_pairs: Path,
          apply: bool) -> dict:
    """Archive cross-domain rows from every review round and the pair manifest.

    All rounds, not just the live one: an out-of-domain relation answered two
    batches ago still asserts something production can never observe, and the
    conflict checks keep reporting it. Purging only the live queue left those
    permanently unreachable — there is no other way to retract them, because a
    historical round is not editable from the web either.

    Each round's artefacts land beside it (``out_of_scope.csv`` plus a one-time
    ``candidates.before_domain_purge.csv``); the manifest archive and the
    ``domain_purge.json`` event log live beside the newest round, which is
    where the rest of that round's provenance already is.
    """
    queues = [candidates] if isinstance(candidates, Path) else list(candidates)
    if not queues:
        raise ValueError("purge needs at least one review round")
    round_dir = queues[-1].parent
    event = {
        "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "rule": RULE, "apply": apply,
        "queues": [purge_file(path, path.parent / "out_of_scope.csv",
                              path.parent / "candidates.before_domain_purge.csv", apply)
                   for path in queues],
        "pairs": purge_file(base_pairs, round_dir / "pairs.out_of_domain.csv",
                            round_dir / "pairs.before_domain_purge.csv", apply),
    }
    changed = sum(record["removed"] for record in event["queues"]) + event["pairs"]["removed"]
    if apply and changed:
        log_path = round_dir / "domain_purge.json"
        log = {"schema": 1, "events": []}
        if log_path.is_file():
            log = json.loads(log_path.read_text(encoding="utf-8"))
        log["events"].append(event)
        atomic_write_json(log_path, log)
        event["log"] = str(log_path)
    return event
