"""Behaviour the five image task types now share through image_dataset."""

import hashlib
import json

import pytest

from annotation_platform.caption_task import CaptionTaskType
from annotation_platform.detection_task import DetectionTaskType
from annotation_platform.image_dataset import item_id
from annotation_platform.task_types import QueueRequest, TaskOperationError


def detection_project(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    (images / "a.jpg").write_bytes(b"jpeg")
    config = tmp_path / "detection.yaml"
    config.write_text("dataset: ./images\ncategories: [person]\n", encoding="utf-8")
    module = DetectionTaskType()
    return module, module.load(config), images


def test_item_id_is_the_persisted_path_hash():
    # Stored in every sidecar: changing this rule orphans saved annotations.
    expected = "i" + hashlib.sha256(b"sub/b.png").hexdigest()[:16]
    assert item_id("sub/b.png") == expected


@pytest.mark.parametrize("status", ["labelled", "done"])
def test_unknown_status_filter_is_rejected(tmp_path, status):
    module, project, _ = detection_project(tmp_path)
    with pytest.raises(TaskOperationError, match="unsupported detection status filter"):
        module.queue(project, QueueRequest(filters={"status": status}))


def test_annotated_and_pending_filters_partition_the_queue(tmp_path):
    module, project, images = detection_project(tmp_path)
    (images / "b.jpg").write_bytes(b"jpeg")
    sidecar = images / ".annotations" / "detection.json"
    sidecar.parent.mkdir()
    sidecar.write_text(json.dumps({"schema": 1, "items": {item_id("a.jpg"): {
        "image_path": "a.jpg", "image_size": {"width": 2, "height": 2}, "boxes": []}},
        "history": []}), encoding="utf-8")
    annotated = module.queue(project, QueueRequest(filters={"status": "annotated"}))
    pending = module.queue(project, QueueRequest(filters={"status": "pending"}))
    assert [item["image_path"] for item in annotated.items] == ["a.jpg"]
    assert [item["image_path"] for item in pending.items] == ["b.jpg"]


def test_a_malformed_stored_item_is_reported_not_a_key_error(tmp_path):
    module, project, images = detection_project(tmp_path)
    sidecar = images / ".annotations" / "detection.json"
    sidecar.parent.mkdir()
    sidecar.write_text(json.dumps({"schema": 1, "items": {item_id("a.jpg"): {
        "image_path": "a.jpg"}}, "history": []}), encoding="utf-8")
    with pytest.raises(TaskOperationError, match="invalid detection annotation item"):
        module.queue(project, QueueRequest())


def test_every_image_task_rejects_duplicate_patterns(tmp_path):
    (tmp_path / "images").mkdir()
    config = tmp_path / "caption.yaml"
    config.write_text("dataset: ./images\npatterns: ['*.jpg', '*.jpg']\n", encoding="utf-8")
    with pytest.raises(TaskOperationError, match="caption `patterns` entries must be unique"):
        CaptionTaskType().load(config)
