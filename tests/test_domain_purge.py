"""purge-domain: archive cross-domain rows with backups, hashes and an event log."""

import json

from reid_annotation_tool import conflicts as conflict_engine
from reid_annotation_tool.domain import purge

from test_domain_guard import domain_dataset


def test_dry_run_reports_but_changes_nothing(tmp_path):
    root = domain_dataset(tmp_path)
    queue, pairs = root / "review.csv", root / "pairs.csv"
    before = (queue.read_bytes(), pairs.read_bytes())
    event = purge(root, queue, pairs, apply=False)
    assert event["queues"][0]["removed"] == 1            # r_unclear (cross-day)
    assert event["queues"][0]["labels_removed"] == {"unclear": 1}
    assert event["pairs"]["removed"] == 2            # A-B label=0, A-C label=1
    assert event["pairs"]["labels_removed"] == {"label=0": 1, "label=1": 1}
    assert (queue.read_bytes(), pairs.read_bytes()) == before
    assert not (root / "domain_purge.json").exists()


def test_apply_archives_and_rewrites_with_provenance(tmp_path):
    root = domain_dataset(tmp_path)
    queue, pairs = root / "review.csv", root / "pairs.csv"
    event = purge(root, queue, pairs, apply=True)

    # queue: only the cross-domain row left, label preserved in the archive
    kept_ids = [row["candidate_id"] for row in
                __import__("csv").DictReader(queue.open(newline=""))]
    assert kept_ids == ["r_foreign", "r_purity"]
    archived = list(__import__("csv").DictReader((root / "out_of_scope.csv").open(newline="")))
    assert [(row["candidate_id"], row["review_label"]) for row in archived] \
        == [("r_unclear", "unclear")]

    # pairs: both cross-domain rows gone whatever their label
    assert (root / "pairs.out_of_domain.csv").is_file()
    assert event["pairs"]["rows_after"] == 0
    assert pairs.read_text().count("\n") == 1        # header only

    # provenance: one-time backups, before/after hashes, event log
    assert (root / "review.before_domain_purge.csv").exists() is False  # named after source
    assert (root / "candidates.before_domain_purge.csv").is_file()
    assert (root / "pairs.before_domain_purge.csv").is_file()
    assert event["queues"][0]["sha256_before"] != event["queues"][0]["sha256_after"]
    log = json.loads((root / "domain_purge.json").read_text())
    assert len(log["events"]) == 1 and log["events"][0]["apply"] is True

    # and the conflict guard is silent afterwards
    report = conflict_engine.report(root, pairs, [queue])
    assert not [item for item in report["conflicts"]
                if item["kind"] in {"cross_day_pair", "cross_camera_pair"}]


def test_second_apply_is_a_noop(tmp_path):
    root = domain_dataset(tmp_path)
    queue, pairs = root / "review.csv", root / "pairs.csv"
    purge(root, queue, pairs, apply=True)
    backup = (root / "candidates.before_domain_purge.csv").read_bytes()
    event = purge(root, queue, pairs, apply=True)
    assert event["queues"][0]["removed"] == 0 and event["pairs"]["removed"] == 0
    # the original-state backup and the event log are untouched
    assert (root / "candidates.before_domain_purge.csv").read_bytes() == backup
    assert len(json.loads((root / "domain_purge.json").read_text())["events"]) == 1


def test_every_round_is_purged_not_only_the_live_one(tmp_path):
    """An out-of-domain answer given two batches ago still asserts something
    production can never observe, and no other route can retract it — a
    historical round is not editable from the web either."""
    import csv

    root = domain_dataset(tmp_path)
    live, pairs = root / "review.csv", root / "pairs.csv"
    old_round = root / "review" / "batch-01" / "candidates.csv"
    old_round.parent.mkdir(parents=True)
    rows = list(csv.DictReader(live.open(newline="")))
    with old_round.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    event = purge(root, [old_round, live], pairs, apply=True)
    assert [record["removed"] for record in event["queues"]] == [1, 1]
    # each round keeps its own archive and backup, beside itself
    assert (old_round.parent / "out_of_scope.csv").is_file()
    assert (old_round.parent / "candidates.before_domain_purge.csv").is_file()
    assert (root / "out_of_scope.csv").is_file()
    # the shared artefacts sit beside the newest round
    assert (root / "domain_purge.json").is_file()
    report = conflict_engine.report(root, pairs, [old_round, live])
    assert not [item for item in report["conflicts"]
                if item["kind"] in {"cross_day_pair", "cross_camera_pair"}]
