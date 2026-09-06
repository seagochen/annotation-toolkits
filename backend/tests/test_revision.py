"""Web revision of baked human verdicts: supersede reviewed_* rows only, file
the new verdict as a live-round candidate, full provenance trail."""

import csv
import json

import pytest

from reid_annotation_tool.core import PAIR_FIELDS, supersede
from reid_annotation_tool.server import Store

from conftest import CANDIDATE_FIELDS, IDENTITY_FIELDS, TRACK_FIELDS, write_csv

PAIR_FIELDS_WITH_BATCH = PAIR_FIELDS + ("batch",)
# all same day / same camera so the domain guard stays silent
A, B, C, D, E = (f"cam16_d20260830_v{index:03d}_t{index:05d}" for index in range(1, 6))


def base(left, right, label, evidence):
    return {"img1": f"images/train/{left}/00.jpg", "img2": f"images/train/{right}/00.jpg",
            "label": label, "split": "train", "evidence": evidence,
            "person_id1": left, "person_id2": right, "gap_sec": "1", "batch": "b2"}


@pytest.fixture
def revision_store(tmp_path):
    identities, tracks = [], []
    for index, name in enumerate([A, B, C, D, E]):
        for crop in range(2):
            path = f"images/train/{name}/{crop:02d}.jpg"
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_bytes(b"jpeg")
            identities.append({"img_path": path, "person_id": name, "split": "train",
                               "video": f"v_{index}.mp4", "track_id": index, "class_id": 0,
                               "timestamp": f"{100 * index + crop:.3f}"})
        tracks.append({"person_id": name, "split": "train", "video": f"v_{index}.mp4",
                       "track_id": index, "class_id": 0, "start": f"{100 * index:.3f}",
                       "end": f"{100 * index + 5:.3f}", "frames": 10, "crops": 2,
                       "recovered": 0, "status": "accepted", "reason": ""})
    write_csv(tmp_path / "identities.csv", IDENTITY_FIELDS, identities)
    write_csv(tmp_path / "tracks.csv", TRACK_FIELDS, tracks)
    write_csv(tmp_path / "pairs.csv", PAIR_FIELDS_WITH_BATCH, [
        base(A, B, 1, "reviewed_model_mined_same"),        # line 2
        base(B, C, 0, "covisible_tracks_cross_crops"),     # line 3: physical
        base(D, E, 1, "reviewed_cross_track_same"),        # line 4
        base(A, B, 0, "reviewed_model_mined_different"),   # line 5: both labels fall
    ])
    queue = tmp_path / "review" / "cur" / "candidates.csv"
    write_csv(queue, CANDIDATE_FIELDS, [{
        "candidate_id": "q1", "kind": "cross_track", "split": "train",
        "person_id1": A, "person_id2": B,
        "img1": f"images/train/{A}/00.jpg", "img2": f"images/train/{B}/00.jpg",
        "time_gap_sec": "10", "cosine": "0.9", "rank_score": "0.9",
        "review_label": "", "review_notes": ""}])
    return Store(tmp_path, queue, tmp_path / "pairs.csv", [queue])


def read(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_revise_supersedes_all_reviewed_rows_and_relabels_the_live_candidate(revision_store):
    store = revision_store
    root, queue = store.root, store.candidates
    event = store.revise_base(A, B, "different", "clearly two people")

    # both reviewed_* rows fell (lines 2 and 5), whatever their label
    assert [row["line"] for row in event["removed"]] == [2, 5]
    remaining = read(root / "pairs.csv")
    assert [row["evidence"] for row in remaining] \
        == ["covisible_tracks_cross_crops", "reviewed_cross_track_same"]

    # the pair already had a live candidate: relabelled, nothing injected
    assert event["injected"] is False and event["candidate_id"] == "q1"
    assert [(row["review_label"], row["review_notes"]) for row in store.rows()
            if row["candidate_id"] == "q1"] == [("different", "clearly two people")]

    # provenance: archive, one-time backup, hash chain, event log
    archived = read(root / "review" / "cur" / "pairs.overturned.csv")
    assert {row["evidence"] for row in archived} \
        == {"reviewed_model_mined_same", "reviewed_model_mined_different"}
    assert (root / "review" / "cur" / "pairs.before_web_revision.csv").is_file()
    assert event["pairs_sha256_before"] != event["pairs_sha256_after"]
    log = json.loads((root / "review" / "cur" / "revisions.json").read_text())
    assert len(log["events"]) == 1

    # and the dataset stays conflict-free: the new different verdict no longer
    # meets any baked same edge
    report = store.conflict_report(refresh=True)
    assert report["errors"] == 0


def test_revise_injects_a_candidate_when_the_pair_is_not_in_the_queue(revision_store):
    store = revision_store
    event = store.revise_base(D, E, "unclear", "")
    assert event["injected"] is True
    rows = [row for row in store.rows() if row["person_id1"] == D]
    assert [(row["review_label"], row["kind"]) for row in rows] == [("unclear", "cross_track")]
    assert rows[0]["candidate_id"].startswith("c")     # content-addressed cross_track id
    assert rows[0]["split"] == "train" and rows[0]["img1"]
    sources = read(store.root / "review" / "cur" / "candidate_source.csv")
    assert sources[-1]["candidate_id"] == rows[0]["candidate_id"]
    assert sources[-1]["source"] == "web_base_revision"


def test_injection_migrates_a_legacy_candidate_header(revision_store):
    store = revision_store
    legacy = ("candidate_id", "split", "person_id1", "person_id2", "time_gap_sec",
              "cosine_similarity", "review_label", "review_notes")
    write_csv(store.candidates, legacy, [{
        "candidate_id": "old", "split": "train", "person_id1": A, "person_id2": B,
        "time_gap_sec": "1", "cosine_similarity": "0.5", "review_label": "same",
        "review_notes": "",
    }])
    event = store.set_relation(A, C, "different", "legacy queue")
    assert event["injected"] is True
    with store.candidates.open(newline="", encoding="utf-8") as handle:
        fields = csv.DictReader(handle).fieldnames
    assert {"kind", "img1", "img2"}.issubset(fields)


def test_legacy_blank_kind_and_cross_track_share_revision_precedence():
    old = {"kind": "cross_track", "person_id1": A, "person_id2": C,
           "review_label": "same", "candidate_id": "old"}
    corrected = {"kind": "", "person_id1": A, "person_id2": C,
                 "review_label": "different", "candidate_id": "corrected"}
    assert supersede([old, corrected]) == [corrected]


def test_physical_evidence_cannot_be_overruled(revision_store):
    with pytest.raises(ValueError, match="physical evidence"):
        revision_store.revise_base(B, C, "same", "")
    # and a pair with no baked rows at all is refused the same way
    with pytest.raises(ValueError, match="no reviewed_"):
        revision_store.revise_base(A, C, "different", "")


def test_second_revision_of_the_same_pair_is_refused(revision_store):
    store = revision_store
    store.revise_base(A, B, "different", "")
    with pytest.raises(ValueError, match="no reviewed_"):
        store.revise_base(A, B, "same", "")
    # the one-time backup still holds the ORIGINAL pre-revision state
    backup = read(store.root / "review" / "cur" / "pairs.before_web_revision.csv")
    assert len(backup) == 4


def test_unsupported_verdict_is_rejected(revision_store):
    with pytest.raises(ValueError, match="unsupported verdict"):
        revision_store.revise_base(A, B, "maybe", "")


# ------------------------------------------- judging a relation from the chain

def test_set_relation_supersedes_baked_rows_only_while_they_stand_in_the_way(revision_store):
    """One entry point, two writes: the conflict chain answers relations, and
    whether that costs an archive is a property of the data, not of the click."""
    store = revision_store
    event = store.set_relation(A, B, "different", "clearly two people")
    assert [row["line"] for row in event["removed"]] == [2, 5]
    assert (event["candidate_id"], event["injected"]) == ("q1", False)

    # answering the same relation again is now an ordinary relabel — the old
    # revise-only path raised here, which made a corrected verdict unfixable
    again = store.set_relation(A, B, "unclear", "")
    assert again["removed"] == [] and again["candidate_id"] == "q1"
    assert [row["review_label"] for row in store.rows()
            if row["candidate_id"] == "q1"] == ["unclear"]
    assert len(read(store.root / "pairs.csv")) == 2      # untouched by the relabel


def test_set_relation_files_a_verdict_that_fights_physical_evidence(revision_store):
    """Machine-derived rows are never removed from the web: the verdict is
    recorded and the contradiction it creates is left for the checks to report."""
    store = revision_store
    event = store.set_relation(B, C, "same", "looks like one person")
    assert event["removed"] == [] and event["injected"] is True
    assert [row["evidence"] for row in read(store.root / "pairs.csv")] == [
        "reviewed_model_mined_same", "covisible_tracks_cross_crops",
        "reviewed_cross_track_same", "reviewed_model_mined_different"]
    flagged = [item for item in store.conflict_report(refresh=True)["conflicts"]
               if sorted(item["identities"]) == sorted([B, C])]
    assert flagged and flagged[0]["severity"] == "error"


def test_set_relation_enqueues_a_pair_that_was_never_mined(revision_store):
    store = revision_store
    event = store.set_relation(A, C, "different", "")
    assert event["injected"] is True and event["removed"] == []
    rows = [row for row in store.rows() if row["candidate_id"] == event["candidate_id"]]
    assert [(row["review_label"], row["kind"]) for row in rows] == [("different", "cross_track")]
    assert len(read(store.root / "pairs.csv")) == 4      # nothing baked was touched


def test_set_relation_rejects_an_unsupported_verdict(revision_store):
    with pytest.raises(ValueError, match="unsupported verdict"):
        revision_store.set_relation(A, B, "maybe", "")


def test_a_relation_answer_reaches_every_live_row_of_that_relation(revision_store):
    """Older mining generations can leave two ids on one pair. Relabelling only
    the first would leave the queue contradicting itself with no way out — and
    the chain card, which highlights one verdict per relation, unable to say
    which one is in force."""
    store = revision_store
    queue = store.candidates
    rows = read(queue)
    twin = dict(rows[0], candidate_id="q1_old", review_label="same")
    write_csv(queue, list(rows[0]), rows + [twin])
    store._stamp = -1.0

    detail_pair = [row for row in store.rows() if row["person_id1"] == A]
    assert {row["review_label"] for row in detail_pair} == {"", "same"}   # contradictory

    event = store.set_relation(A, B, "different", "")
    assert sorted(event["candidate_ids"]) == ["q1", "q1_old"]
    assert {row["review_label"] for row in store.rows()
            if row["person_id1"] == A} == {"different"}
