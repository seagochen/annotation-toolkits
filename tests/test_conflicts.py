
from reid_annotation_tool.conflicts import DatasetView, detect, report

from conftest import (CANDIDATE_FIELDS, COVISIBLE_FIELDS, IDENTITY_FIELDS,
                      TRACK_FIELDS, candidate, pair, write_csv)
from reid_annotation_tool.core import PAIR_FIELDS


def kinds(conflicts):
    return sorted({item.kind for item in conflicts})


def view(root, reviews=("review.csv",)):
    return DatasetView.load(root, root / "pairs.csv",
                            [root / name for name in reviews if (root / name).is_file()])


def test_clean_dataset_has_no_conflicts(dataset):
    write_csv(dataset / "review.csv", CANDIDATE_FIELDS,
              [candidate("c1", "a", "b", "same"), candidate("c2", "c", "d", "different")])
    assert detect(view(dataset)) == []


def test_transitive_chain_against_a_negative_names_its_witnesses(dataset):
    write_csv(dataset / "pairs.csv", PAIR_FIELDS,
              [pair("a", "a", 1), pair("a", "d", 0, evidence="covisible")])
    write_csv(dataset / "review.csv", CANDIDATE_FIELDS,
              [candidate("c1", "a", "b", "same"), candidate("c2", "b", "c", "same"),
               candidate("c3", "c", "d", "same")])
    conflicts = [item for item in detect(view(dataset))
                 if item.kind == "transitive_negative"]
    assert len(conflicts) == 1
    assert conflicts[0].detail["same_path"] == ["a", "b", "c", "d"]
    assert conflicts[0].witnesses == ["base:covisible", "c1", "c2", "c3"]


def test_covisible_tracks_may_not_be_merged(dataset):
    write_csv(dataset / "covisibility.csv", COVISIBLE_FIELDS,
              [{"person_id1": "a", "person_id2": "b", "video": "v0.mp4", "split": "train",
                "frames": 9, "first_timestamp": "12.0"}])
    write_csv(dataset / "review.csv", CANDIDATE_FIELDS, [candidate("c1", "a", "b", "same")])
    conflicts = [item for item in detect(view(dataset)) if item.kind == "covisible_merge"]
    assert len(conflicts) == 1
    assert conflicts[0].witnesses == ["c1"]
    assert conflicts[0].detail["covisible_frames"] == 9


def test_one_identity_cannot_exist_twice_at_once(dataset):
    rows = [{"person_id": name, "split": "train", "video": "v0.mp4", "track_id": index,
             "class_id": 0, "start": "0.000", "end": "50.000", "frames": 30, "crops": 2,
             "recovered": 0, "status": "accepted", "reason": ""}
            for index, name in enumerate(("a", "b"))]
    write_csv(dataset / "tracks.csv", TRACK_FIELDS, rows)
    write_csv(dataset / "review.csv", CANDIDATE_FIELDS, [candidate("c1", "a", "b", "same")])
    conflicts = [item for item in detect(view(dataset)) if item.kind == "temporal_overlap"]
    assert len(conflicts) == 1
    assert conflicts[0].detail["overlap_sec"] == 50.0


def test_merging_across_splits_is_leakage(dataset):
    identities = []
    for index, (name, split) in enumerate([("a", "train"), ("b", "val")]):
        identities.append({"img_path": f"images/{split}/{name}/00.jpg", "person_id": name,
                           "split": split, "video": "v0.mp4", "track_id": index,
                           "class_id": 0, "timestamp": "1.0"})
        (dataset / identities[-1]["img_path"]).parent.mkdir(parents=True, exist_ok=True)
        (dataset / identities[-1]["img_path"]).write_bytes(b"jpeg")
    write_csv(dataset / "identities.csv", IDENTITY_FIELDS, identities)
    write_csv(dataset / "pairs.csv", PAIR_FIELDS, [pair("a", "a", 1)])
    write_csv(dataset / "review.csv", CANDIDATE_FIELDS, [candidate("c1", "a", "b", "same")])
    conflicts = [item for item in detect(view(dataset)) if item.kind == "split_leakage"]
    assert len(conflicts) == 1
    assert conflicts[0].detail["splits"] == {"train": ["a"], "val": ["b"]}


def test_pair_filed_under_the_wrong_split_is_detected(dataset):
    write_csv(dataset / "pairs.csv", PAIR_FIELDS, [pair("a", "a", 1, split="val")])
    conflicts = [item for item in detect(view(dataset)) if item.kind == "split_mismatch"]
    assert [item.detail["pair_split"] for item in conflicts] == ["val"]


def test_track_purity_answers_do_not_become_relations(dataset):
    write_csv(dataset / "review.csv", CANDIDATE_FIELDS,
              [candidate("t1", "a", "a", "different", kind="track_purity")])
    found = detect(view(dataset))
    assert "self_negative" not in kinds(found)
    assert [item.kind for item in found] == ["contaminated_track"]
    assert found[0].severity == "warning"


def test_missing_files_and_unknown_identities_are_reported(dataset):
    write_csv(dataset / "pairs.csv", PAIR_FIELDS, [pair("a", "ghost", 1)])
    (dataset / "images/train/a/00.jpg").unlink()
    found = kinds(detect(view(dataset, reviews=())))
    assert "unknown_identity" in found and "missing_image" in found


def test_report_counts_and_exit_state(dataset):
    write_csv(dataset / "review.csv", CANDIDATE_FIELDS,
              [candidate("c1", "a", "b", ""), candidate("c2", "c", "d", "same")])
    value = report(dataset, dataset / "pairs.csv", [dataset / "review.csv"],
                   dataset / "conflicts.json")
    assert value["errors"] == 0 and value["pending_reviews"] == 1
    assert (dataset / "conflicts.json").is_file()
