import csv
from pathlib import Path

import pytest

from reid_annotation_tool.core import (
    PAIR_FIELDS, build_constraints, dataset_status, finalize_reviews,
    load_reviews, read_csv,
)


def write_csv(path: Path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def pair(left, right, label, split="train"):
    return {"img1": f"{left}.jpg", "img2": f"{right}.jpg", "label": label,
            "split": split, "evidence": "test", "person_id1": left,
            "person_id2": right, "gap_sec": "1"}


def review(candidate, left, right, label, split="train"):
    return {"candidate_id": candidate, "split": split, "person_id1": left,
            "person_id2": right, "time_gap_sec": "10", "review_label": label,
            "review_notes": ""}


def test_transitive_positive_conflicts_with_negative():
    _, _, conflicts = build_constraints(
        [pair("a", "b", 1), pair("a", "d", 0)],
        [review("c1", "b", "c", "same"), review("c2", "c", "d", "same")],
    )
    assert [(row["person_id1"], row["person_id2"]) for row in conflicts] == [("a", "d")]


def make_dataset(tmp_path):
    identities = []
    for identity in ("a", "b", "c", "d"):
        identities.append({"img_path": f"images/train/{identity}/00.jpg",
                           "person_id": identity, "split": "train", "timestamp": "1"})
    write_csv(tmp_path / "identities.csv", identities[0], identities)
    base = [pair("a", "a", 1), pair("c", "c", 1),
            pair("v", "v", 1, "val"), pair("t", "t", 1, "test")]
    write_csv(tmp_path / "pairs.csv", PAIR_FIELDS, base)
    return base


def test_finalize_preserves_protected_splits(tmp_path):
    base = make_dataset(tmp_path)
    fields = ("candidate_id", "split", "person_id1", "person_id2",
              "time_gap_sec", "review_label", "review_notes")
    rows = [review("c1", "a", "b", "same"),
            review("c2", "c", "d", "different"),
            review("c3", "b", "c", "unclear")]
    write_csv(tmp_path / "review.csv", fields, rows)
    report = finalize_reviews(tmp_path, tmp_path / "pairs.csv",
                              [tmp_path / "review.csv"], tmp_path / "v2.csv",
                              tmp_path / "v2.json")
    assert report["additions"] == {"positive": 1, "negative": 1}
    with (tmp_path / "v2.csv").open(newline="", encoding="utf-8") as handle:
        output = list(csv.DictReader(handle))
    assert [tuple(row[field] for field in PAIR_FIELDS)
            for row in output if row["split"] == "val"] == [
                tuple(str(base[2][field]) for field in PAIR_FIELDS)]
    assert [tuple(row[field] for field in PAIR_FIELDS)
            for row in output if row["split"] == "test"] == [
                tuple(str(base[3][field]) for field in PAIR_FIELDS)]


def test_train_pending_is_blocked_and_protected_review_is_skipped(tmp_path):
    make_dataset(tmp_path)
    fields = ("candidate_id", "split", "person_id1", "person_id2",
              "time_gap_sec", "review_label", "review_notes")
    write_csv(tmp_path / "pending.csv", fields, [review("c1", "a", "b", "")])
    with pytest.raises(ValueError, match="pending"):
        finalize_reviews(tmp_path, tmp_path / "pairs.csv", [tmp_path / "pending.csv"],
                         tmp_path / "out.csv", tmp_path / "out.json")
    write_csv(tmp_path / "protected.csv", fields,
              [review("c2", "a", "b", "same", split="val")])
    report = finalize_reviews(tmp_path, tmp_path / "pairs.csv", [tmp_path / "protected.csv"],
                              tmp_path / "out.csv", tmp_path / "out.json")
    assert report["skipped"] == {"protected_split_review": 1}
    assert read_csv(tmp_path / "out.csv") == read_csv(tmp_path / "pairs.csv")

    write_csv(tmp_path / "protected.csv", fields,
              [review("c3", "a", "b", "", split="test")])
    finalize_reviews(tmp_path, tmp_path / "pairs.csv", [tmp_path / "protected.csv"],
                     tmp_path / "out.csv", tmp_path / "out.json")


def test_status_counts_labels_and_conflicts(tmp_path):
    make_dataset(tmp_path)
    fields = ("candidate_id", "split", "person_id1", "person_id2",
              "time_gap_sec", "review_label", "review_notes")
    write_csv(tmp_path / "review.csv", fields,
              [review("c1", "a", "b", "same"), review("c2", "b", "c", "unclear")])
    value = dataset_status(tmp_path, tmp_path / "pairs.csv", [tmp_path / "review.csv"])
    assert value["review_labels"] == {"same": 1, "unclear": 1}
    assert value["graph_conflicts"] == 0


def purity(candidate, identity, label, split="train"):
    return {"candidate_id": candidate, "kind": "track_purity", "split": split,
            "person_id1": identity, "person_id2": identity, "time_gap_sec": "0",
            "review_label": label, "review_notes": ""}


def review_fields():
    return ("candidate_id", "kind", "split", "person_id1", "person_id2",
            "time_gap_sec", "review_label", "review_notes")


def kinded(candidate, left, right, label, split="train"):
    return dict(review(candidate, left, right, label, split), kind="cross_track")


def test_contaminated_track_is_dropped_from_the_finalized_manifest(tmp_path):
    make_dataset(tmp_path)
    write_csv(tmp_path / "pairs.csv", PAIR_FIELDS,
              [pair("a", "a", 1), pair("c", "c", 1), pair("a", "c", 0),
               pair("v", "v", 1, "val"), pair("t", "t", 1, "test")])
    write_csv(tmp_path / "review.csv", review_fields(),
              [purity("t1", "a", "different"), kinded("c1", "c", "d", "same")])
    report = finalize_reviews(tmp_path, tmp_path / "pairs.csv", [tmp_path / "review.csv"],
                              tmp_path / "v2.csv", tmp_path / "v2.json")
    assert report["rejected_tracks"] == {"a": "t1"}
    assert report["dropped_pairs"] == 2
    with (tmp_path / "v2.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert not [row for row in rows if "a" in (row["person_id1"], row["person_id2"])]
    assert [row["evidence"] for row in rows if row["evidence"].startswith("reviewed")] == [
        "reviewed_model_mined_same"]


def test_a_clean_track_review_changes_nothing(tmp_path):
    make_dataset(tmp_path)
    write_csv(tmp_path / "review.csv", review_fields(), [purity("t1", "a", "same")])
    report = finalize_reviews(tmp_path, tmp_path / "pairs.csv", [tmp_path / "review.csv"],
                              tmp_path / "v2.csv", tmp_path / "v2.json")
    assert report["rejected_tracks"] == {} and report["dropped_pairs"] == 0
    assert report["additions"] == {}


def test_track_purity_answers_are_not_identity_constraints(tmp_path):
    _, _, conflicts = build_constraints(
        [pair("a", "a", 1)], [purity("t1", "a", "different")])
    assert conflicts == []


def test_contaminated_track_review_cannot_change_a_protected_split(tmp_path):
    make_dataset(tmp_path)
    write_csv(tmp_path / "pairs.csv", PAIR_FIELDS,
              [pair("a", "a", 1), pair("v", "v", 1, "val")])
    write_csv(tmp_path / "review.csv", review_fields(),
              [purity("t1", "v", "different", split="val")])
    report = finalize_reviews(tmp_path, tmp_path / "pairs.csv", [tmp_path / "review.csv"],
                              tmp_path / "v2.csv", tmp_path / "v2.json")
    assert report["rejected_tracks"] == {}
    assert read_csv(tmp_path / "v2.csv") == read_csv(tmp_path / "pairs.csv")


# ------------------------------- one answer per question, newest round wins

def review_row(left, right, label, kind="cross_track", candidate="c"):
    return {"candidate_id": candidate, "kind": kind, "split": "train",
            "person_id1": left, "person_id2": right, "review_label": label,
            "review_notes": "", "img1": "", "img2": "", "time_gap_sec": "1",
            "cosine": "0.9", "rank_score": "0.9"}


def test_a_reviewer_changing_their_mind_corrects_rather_than_contradicts(tmp_path):
    """Re-answering an old question is the dataset's correction rule, not a
    contradiction — the round files are read oldest first and the newest wins."""
    old = tmp_path / "v1.csv"
    new = tmp_path / "v2.csv"
    fields = list(review_row("a", "b", "").keys())
    write_csv(old, fields, [review_row("a", "b", "same", candidate="c1"),
                            review_row("x", "x", "different", kind="track_purity",
                                       candidate="t1")])
    write_csv(new, fields, [review_row("b", "a", "different", candidate="c2"),
                            review_row("x", "x", "same", kind="track_purity",
                                       candidate="t2")])
    merged = load_reviews([old, new])
    # keyed by the question, not the candidate id: the two rounds used
    # different id schemes and stated the pair in opposite order
    assert [(row["candidate_id"], row["review_label"]) for row in merged] \
        == [("c2", "different"), ("t2", "same")]
    _, negatives, conflicts = build_constraints([], merged)
    assert conflicts == [] and ("a", "b") in negatives


def test_an_unanswered_re_ask_never_erases_the_answer_it_re_asks(tmp_path):
    """Re-mining enqueues an answered pair again as pending; a pending row is a
    question, not a retraction."""
    old, new = tmp_path / "v1.csv", tmp_path / "v2.csv"
    fields = list(review_row("a", "b", "").keys())
    write_csv(old, fields, [review_row("a", "b", "same", candidate="c1")])
    write_csv(new, fields, [review_row("a", "b", "", candidate="c1")])
    merged = load_reviews([old, new])
    assert [row["review_label"] for row in merged] == ["same"]
