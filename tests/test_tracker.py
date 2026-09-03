import numpy as np

from reid_annotation_tool.detector import Detection
from reid_annotation_tool.tracker import Tracker


def box(x, y, w=20, h=40):
    return np.asarray([x, y, x + w, y + h], np.float32)


def detection(x, y, conf=0.9, cls=0):
    return Detection(box(x, y), conf, cls)


def test_a_moving_object_keeps_one_track_id():
    tracker = Tracker(n_init=1)
    ids = [tracker.update([detection(10 + step * 4, 10)])[0].track_id for step in range(8)]
    assert len(set(ids)) == 1


def test_a_jump_starts_a_new_track_instead_of_switching_identity():
    tracker = Tracker(n_init=1, max_iou_distance=0.5)
    first = tracker.update([detection(10, 10)])[0].track_id
    second = tracker.update([detection(400, 300)])[0].track_id
    assert first != second


def crossing(tracker, embeddings=None):
    """Two objects walking through each other, 6px per step."""
    return [tuple(track.track_id for track in tracker.update(
        [detection(10 + step * 6, 10), detection(90 - step * 6, 12)], embeddings))
        for step in range(11)]


def test_crossing_objects_are_never_merged_into_one_track():
    """Geometry alone may fragment a crossing, but must never fuse two objects.

    A fragmented track becomes two identities a reviewer can merge; a fused one
    would silently teach the model that two people are the same.
    """
    seen = crossing(Tracker(n_init=1))
    assert all(len(set(step)) == 2 for step in seen)
    assert len({value for step in seen for value in step}) > 2


def test_appearance_gating_carries_identity_through_a_crossing():
    left = np.asarray([1.0, 0.0], np.float32)
    right = np.asarray([0.0, 1.0], np.float32)
    tracker = Tracker(n_init=1, appearance_weight=0.5, max_cosine_distance=0.5)
    assert set(crossing(tracker, [left, right])) == {(1, 2)}


def test_appearance_gate_blocks_a_geometric_but_unlike_match():
    left = np.asarray([1.0, 0.0], np.float32)
    right = np.asarray([0.0, 1.0], np.float32)
    tracker = Tracker(n_init=1, appearance_weight=0.5, max_cosine_distance=0.2)
    first = tracker.update([detection(10, 10)], [left])[0].track_id
    updated = tracker.update([detection(12, 10)], [right])
    assert updated[0].track_id != first


def test_a_track_is_closed_after_max_age():
    tracker = Tracker(n_init=1, max_age=2)
    tracker.update([detection(10, 10)])
    for _ in range(3):
        tracker.update([])
    assert tracker.tracks == []
    assert len(tracker.removed) == 1


def test_unconfirmed_tracks_need_n_init_hits():
    tracker = Tracker(n_init=3)
    for step in range(2):
        updated = tracker.update([detection(10 + step, 10)])
        assert not updated[0].confirmed(3)
    assert tracker.update([detection(12, 10)])[0].confirmed(3)
