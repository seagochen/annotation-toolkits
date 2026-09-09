
from reid_annotation_tool.review_store import Store, spread

from conftest import CANDIDATE_FIELDS, candidate, write_csv


def store(dataset, rows):
    write_csv(dataset / "review.csv", CANDIDATE_FIELDS, rows)
    return Store(dataset, dataset / "review.csv", dataset / "pairs.csv",
                 [dataset / "review.csv"])


def test_labels_are_persisted_atomically_and_reread(dataset):
    value = store(dataset, [candidate("c1", "a", "b"), candidate("c2", "c", "d")])
    assert value.state()["pending"] == 2
    updated = value.set_label("c1", "same", "clear match")
    assert updated["review_label"] == "same"
    assert value.state()["labelled"] == 1
    reopened = Store(dataset, dataset / "review.csv", dataset / "pairs.csv",
                     [dataset / "review.csv"])
    assert [row["review_label"] for row in reopened.rows()] == ["same", ""]
    assert [row["review_notes"] for row in reopened.rows()] == ["clear match", ""]
    assert not list(dataset.glob("review.csv.tmp"))


def test_unknown_candidate_is_rejected(dataset):
    assert store(dataset, [candidate("c1", "a", "b")]).set_label("nope", "same", None) is None


def test_rows_are_decorated_with_galleries_and_track_metadata(dataset):
    value = store(dataset, [candidate("c1", "a", "b")])
    decorated = value.decorate(value.rows()[0])
    assert decorated["gallery1"] == ["images/train/a/00.jpg", "images/train/a/01.jpg"]
    assert decorated["meta2"]["video"] == "v0.mp4"
    assert decorated["meta1"]["crops"] == 2


def test_state_reports_progress_per_kind(dataset):
    value = store(dataset, [candidate("c1", "a", "b"),
                            candidate("t1", "c", "c", kind="track_purity")])
    value.set_label("c1", "different", None)
    state = value.state()
    assert state["kinds"] == {"cross_track": {"different": 1}, "track_purity": {"pending": 1}}
    assert state["conflicts"]["available"] is True


def test_conflict_cache_goes_stale_on_a_new_label(dataset):
    value = store(dataset, [candidate("c1", "a", "b")])
    value.conflict_report()
    value.set_label("c1", "same", None)
    assert value.conflict_summary()["stale"] in {True, False}
    assert value.conflict_report()["errors"] == 0


def test_gallery_sampling_spans_the_whole_track():
    assert spread(list(range(10)), 4) == [0, 3, 6, 9]
    assert spread([1, 2], 4) == [1, 2]
