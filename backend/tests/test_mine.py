import numpy as np

from reid_annotation_tool.embed import centroid, cosine, letterbox_bgr, normalize_rows
from reid_annotation_tool.mine import (candidate_id, closest_pair, farthest_pair,
                                       known_relations, time_gap)

from conftest import CANDIDATE_FIELDS, COVISIBLE_FIELDS, candidate, pair, write_csv
from reid_annotation_tool.core import PAIR_FIELDS


def rows(*timestamps):
    return [{"img_path": f"{value}.jpg", "timestamp": str(value)} for value in timestamps]


def test_candidate_ids_are_content_addressed_so_labels_survive_remining():
    assert candidate_id("cross_track", "a", "b") == candidate_id("cross_track", "a", "b")
    assert candidate_id("cross_track", "a", "b") != candidate_id("cross_track", "b", "a")
    assert candidate_id("cross_track", "a", "b") != candidate_id("track_purity", "a", "b")
    assert candidate_id("track_purity", "a", "a").startswith("t")


def test_time_gap_is_zero_for_overlapping_tracks():
    assert time_gap(rows(0, 10), rows(20, 30)) == 10.0
    assert time_gap(rows(20, 30), rows(0, 10)) == 10.0
    assert time_gap(rows(0, 25), rows(20, 30)) == 0.0


def test_known_relations_exclude_covisible_and_merged_tracks(dataset):
    write_csv(dataset / "pairs.csv", PAIR_FIELDS, [pair("a", "b", 1), pair("c", "d", 0)])
    write_csv(dataset / "covisibility.csv", COVISIBLE_FIELDS,
              [{"person_id1": "a", "person_id2": "c", "video": "v0.mp4", "split": "train",
                "frames": 7, "first_timestamp": "1.0"}])
    write_csv(dataset / "review.csv", CANDIDATE_FIELDS,
              [candidate("c1", "b", "c", "same"), candidate("c2", "a", "d", "different")])
    graph, negatives, decided = known_relations(dataset, dataset / "pairs.csv",
                                                [dataset / "review.csv"])
    assert graph.same("a", "c")
    assert ("a", "c") in negatives and ("c", "d") in negatives and ("a", "d") in negatives
    assert decided["c1"]["review_label"] == "same"


def test_track_purity_answers_never_merge_identities(dataset):
    write_csv(dataset / "pairs.csv", PAIR_FIELDS, [pair("a", "a", 1)])
    write_csv(dataset / "review.csv", CANDIDATE_FIELDS,
              [candidate("t1", "a", "a", "different", kind="track_purity")])
    graph, negatives, _ = known_relations(dataset, dataset / "pairs.csv",
                                          [dataset / "review.csv"])
    assert ("a", "a") not in negatives


def test_representative_pairs_pick_the_strongest_and_weakest_evidence():
    features = {"a0.jpg": np.asarray([1.0, 0.0]), "a1.jpg": np.asarray([0.0, 1.0]),
                "b0.jpg": np.asarray([0.99, 0.14])}
    left = [{"img_path": "a0.jpg"}, {"img_path": "a1.jpg"}]
    right = [{"img_path": "b0.jpg"}]
    assert closest_pair(left, right, features)[:2] == ("a0.jpg", "b0.jpg")
    assert farthest_pair(left, features)[2] < 0.01


def test_letterbox_preprocessing_matches_the_engine_convention():
    image = np.full((40, 20, 3), 200, np.uint8)
    tensor = letterbox_bgr(image, 8)
    assert tensor.shape == (3, 8, 8)
    assert tensor.max() <= 1.0
    assert np.isclose(tensor[0, 0, 0], 114 / 255.0)


def test_normalisation_helpers():
    features = normalize_rows(np.asarray([[3.0, 4.0]], np.float32))
    assert np.isclose(np.linalg.norm(features[0]), 1.0)
    assert np.isclose(cosine(centroid([np.asarray([1.0, 0.0]), np.asarray([0.0, 1.0])]),
                             np.asarray([1.0, 1.0]) / np.sqrt(2)), 1.0)
