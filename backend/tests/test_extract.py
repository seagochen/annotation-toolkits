from datetime import datetime
from pathlib import Path

import numpy as np
import pytest


from reid_annotation_tool.contract import Observation
from reid_annotation_tool.geometry import contained_fraction, iou
from reid_annotation_tool.extract import (CropSample, TrackRecord, assign_split,
                                          build_pairs, build_sample,
                                          crop_boxes_of_frame, crop_is_contaminated,
                                          finalize_track, identity_rows,
                                          identity_prefix,
                                          is_isolated, motion_is_sane,
                                          parse_day_splits, parse_start_time,
                                          slug, thin_samples, video_identifier,
                                          video_split)
from reid_annotation_tool.projection import BodyProjection


class Args:
    def __init__(self, **values):
        self.__dict__.update(values)


def box(x, y, w=20, h=40):
    return np.asarray([x, y, x + w, y + h], np.float32)


def sample(timestamp, path):
    return CropSample(timestamp, int(timestamp), np.zeros((4, 4, 3), np.uint8),
                      box(0, 0), 0.9, 100.0, 100.0, 0.0, 0.0, path)


def track(name, split="train", times=(0.0, 2.0, 4.0)):
    record = TrackRecord(name, split, "v0.mp4", 1, 0, times[0], times[-1])
    record.samples = [sample(value, f"images/{split}/{name}/{index:02d}.jpg")
                      for index, value in enumerate(times)]
    return record


def test_split_assignment_is_stable_and_video_wide():
    ratios = {"train": 0.7, "val": 0.15, "test": 0.15}
    first = assign_split("cam1-0001.mp4", ratios, 7)
    assert first == assign_split("cam1-0001.mp4", ratios, 7)
    assert first in {"train", "val", "test"}
    assigned = [assign_split(f"cam-{index:04d}.mp4", ratios, 7) for index in range(200)]
    assert set(assigned) == {"train", "val", "test"}
    assert assigned.count("train") > assigned.count("val")


def test_forcing_a_split_never_mixes_videos():
    ratios = {"train": 1.0, "val": 0.0, "test": 0.0}
    assert {assign_split(f"v{index}.mp4", ratios, 1) for index in range(50)} == {"train"}


def test_slug_is_filesystem_safe():
    assert slug("seg-2026 08/21.mp4") == "seg-2026-08-21-mp4"


def test_generated_identity_prefix_is_domain_aware_and_collision_resistant():
    from reid_annotation_tool.core import identity_domain

    epoch = datetime.strptime("20260830210002", "%Y%m%d%H%M%S").timestamp()
    first = identity_prefix("cam16-2026-08-30-210002", epoch, "/cam16/a/210002.mp4")
    second = identity_prefix("cam16-2026-08-30-210002", epoch, "/cam16/b/210002.mp4")
    assert identity_domain(first + "_t00001") == ("cam16", "20260830")
    assert first != second
    nested = identity_prefix("210002", None,
                             "/recording/cam17/2026-08-31/210002.mp4")
    assert identity_domain(nested + "_t00001") == ("cam17", "20260831")


def test_nested_recording_path_supplies_timestamp_and_unique_video_id():
    path = Path("/recording/cam16/2026-08-30/210002.mp4")
    timestamp = parse_start_time(
        path, r"(\d{4})-(\d{2})-(\d{2})/(\d{6})\.mp4$",
        "%Y%m%d%H%M%S", source="path")
    assert datetime.fromtimestamp(timestamp).strftime("%Y%m%d%H%M%S") == "20260830210002"
    assert video_identifier(
        path, r"(cam\d+)/(\d{4}-\d{2}-\d{2})/(\d{6})\.mp4$") \
        == "cam16-2026-08-30-210002"


def test_legacy_flat_recording_timestamp_stays_supported():
    path = Path("recording/seg-20260823-225003.mp4")
    timestamp = parse_start_time(
        path, r"seg-(\d{8})-(\d{6})\.mp4$", "%Y%m%d%H%M%S")
    assert datetime.fromtimestamp(timestamp).strftime("%Y%m%d%H%M%S") == "20260823225003"


def test_day_split_mapping_is_closed_and_validated():
    mapping = parse_day_splits([
        "20260828=train", "20260829=val", "20260830=test",
    ])
    epoch = datetime.strptime("20260830210002", "%Y%m%d%H%M%S").timestamp()
    assert video_split("v", epoch, None, mapping, {}, 0) == "test"
    with pytest.raises(SystemExit, match="absent"):
        video_split("v", datetime.strptime("20260831", "%Y%m%d").timestamp(),
                    None, mapping, {}, 0)
    with pytest.raises(ValueError, match="duplicate"):
        parse_day_splits(["20260828=train", "20260828=test"])
    with pytest.raises(ValueError, match="expected"):
        parse_day_splits(["2026-08-28=train"])


def test_positives_come_only_from_one_track_and_respect_the_gap():
    args = Args(min_positive_gap=1.5, max_positive_pairs=10, negative_ratio=1.0, seed=1,
                min_covisible_frames=5)
    rows = build_pairs({"a": track("a"), "b": track("b")}, {}, args)
    assert rows and all(int(row["label"]) == 1 for row in rows)
    assert all(row["person_id1"] == row["person_id2"] for row in rows)
    assert all(float(row["gap_sec"]) >= 1.5 for row in rows)


def test_negatives_need_enough_covisible_witness_frames():
    args = Args(min_positive_gap=1.0, max_positive_pairs=10, negative_ratio=1.0, seed=1,
                min_covisible_frames=5)
    finished = {"a": track("a"), "b": track("b")}
    weak = build_pairs(finished, {("a", "b"): {"frames": 2}}, args)
    strong = build_pairs(finished, {("a", "b"): {"frames": 9}}, args)
    assert not [row for row in weak if int(row["label"]) == 0]
    assert [row for row in strong if int(row["label"]) == 0]


def test_negatives_never_cross_a_split_boundary():
    args = Args(min_positive_gap=1.0, max_positive_pairs=10, negative_ratio=1.0, seed=1,
                min_covisible_frames=1)
    finished = {"a": track("a", "train"), "b": track("b", "val")}
    rows = build_pairs(finished, {("a", "b"): {"frames": 9}}, args)
    assert not [row for row in rows if int(row["label"]) == 0]


def test_rejected_tracks_contribute_no_pairs():
    args = Args(min_positive_gap=1.0, max_positive_pairs=10, negative_ratio=1.0, seed=1,
                min_covisible_frames=1)
    record = track("a")
    record.rejected = "occluded_by_neighbour"
    assert build_pairs({"a": record}, {}, args) == []


def test_thinning_keeps_both_ends_of_a_track():
    samples = [sample(float(index), f"{index}.jpg") for index in range(10)]
    thin_samples(samples, 4)
    assert len(samples) == 4
    assert samples[0].timestamp == 0.0 and samples[-1].timestamp == 9.0


def test_isolation_rejects_overlap_and_containment():
    """Isolation now takes BODY boxes (arrays), not Detection objects."""
    args = Args(max_neighbour_iou=0.10, max_neighbour_contained=0.35)
    target = box(0, 0, 100, 200)
    assert is_isolated(target, [box(500, 500)], args)
    assert not is_isolated(target, [box(10, 10, 90, 180)], args)
    small = box(10, 10, 20, 20)
    assert iou(target, small) < 0.10 < contained_fraction(target, small)
    assert not is_isolated(target, [small], args)


def test_motion_sanity_rejects_teleports_and_scale_jumps():
    previous = box(0, 0, 40, 80)
    assert motion_is_sane(previous, box(5, 5, 40, 80), 0.75, 0.5, 2.0)
    assert not motion_is_sane(previous, box(300, 0, 40, 80), 0.75, 0.5, 2.0)
    assert not motion_is_sane(previous, box(0, 0, 200, 400), 0.75, 0.5, 2.0)


def test_crop_firewall_flags_two_independent_objects():
    args = Args(crop_conf=0.02, crop_primary_conf=0.30, max_crop_overlap=0.30,
                min_crop_relative_area=0.15)
    single = [Observation(box(0, 0, 100, 200), class_id=0, confidence=0.9)]
    assert not crop_is_contaminated(single, 0, args)
    two = single + [Observation(box(150, 0, 90, 190), class_id=0, confidence=0.5)]
    assert crop_is_contaminated(two, 0, args)
    tiny = single + [Observation(box(150, 0, 5, 5), class_id=0, confidence=0.5)]
    assert not crop_is_contaminated(tiny, 0, args)
    other_class = single + [Observation(box(150, 0, 90, 190), class_id=1, confidence=0.5)]
    assert not crop_is_contaminated(other_class, 0, args)


def test_a_track_rejected_mid_video_leaves_nothing_in_the_manifest(tmp_path):
    """Its crops were never written to disk, so they must not be listed either."""
    record = track("a")
    record.rejected = "occluded_by_neighbour"
    for sample in record.samples:
        sample.path = ""
    finished = {}
    finalize_track(record, tmp_path, None, Args(), finished)
    assert finished["a"].samples == []
    assert identity_rows(finished) == []


def test_accepted_tracks_report_every_crop():
    rows = identity_rows({"a": track("a")})
    assert [row["img_path"] for row in rows] == [
        f"images/train/a/{index:02d}.jpg" for index in range(3)]
    assert {row["person_id"] for row in rows} == {"a"}


def test_the_default_policy_flags_even_a_tiny_second_object():
    """Contamination policy: any second object counts, however small it looks."""
    args = Args(crop_conf=0.02, crop_primary_conf=0.30, max_crop_overlap=0.30,
                min_crop_relative_area=0.0)
    pair = [Observation(box(0, 0, 100, 200), class_id=0, confidence=0.9), Observation(box(150, 0, 5, 5), class_id=0, confidence=0.5)]
    assert crop_is_contaminated(pair, 0, args)


def test_crop_boxes_grow_with_the_margin_and_stay_inside_the_frame():
    frame = np.zeros((538, 1200, 3), np.uint8)
    projection = BodyProjection()
    head = np.asarray([300.0, 200.0, 340.0, 245.0], np.float32)
    tight = crop_boxes_of_frame([head], frame, projection, Args(crop_margin=0.0))[0]
    loose = crop_boxes_of_frame([head], frame, projection, Args(crop_margin=0.4))[0]
    assert loose[0] < tight[0] and loose[2] > tight[2]
    corner = crop_boxes_of_frame([np.asarray([1150.0, 480.0, 1195.0, 530.0], np.float32)],
                                 frame, projection, Args(crop_margin=0.6))[0]
    assert corner[0] >= 0.0 and corner[2] <= 1200.0 and corner[3] <= 538.0


def test_purity_is_judged_on_the_box_that_becomes_pixels():
    """A neighbour hiding in the crop margin must still reject the sample.

    Vetting the pre-margin body box while cropping the expanded one is exactly how a
    second person slips into the gallery, so the two must be the same rectangle.
    """
    frame = np.zeros((538, 1200, 3), np.uint8)
    projection = BodyProjection()
    args = Args(crop_margin=0.4, max_neighbour_iou=0.10, max_neighbour_contained=0.35,
                min_crop_side=1, min_blur=0.0, min_brightness=-1.0, max_brightness=256.0,
                max_exposure_clip=1.1)
    head = np.asarray([300.0, 200.0, 340.0, 245.0], np.float32)
    tight = crop_boxes_of_frame([head], frame, projection, Args(crop_margin=0.0))[0]
    crop_box = crop_boxes_of_frame([head], frame, projection, args)[0]
    intruder = np.asarray([tight[2] + 1.0, tight[1], crop_box[2], crop_box[3]], np.float32)
    assert is_isolated(tight, [intruder], args)      # invisible to the pre-margin box
    assert build_sample(frame, crop_box, [], 0.0, 0, 0.9, args) is not None
    assert build_sample(frame, crop_box, [intruder], 0.0, 0, 0.9, args) is None
