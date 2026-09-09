import json
from pathlib import Path

import pytest

from annotation_platform.detection_task import DetectionTaskType
from annotation_platform.task_types import (
    ExportRequest,
    QueueRequest,
    Submission,
    TaskConflictError,
    TaskOperationError,
)


def project_config(tmp_path: Path) -> Path:
    dataset = tmp_path / "images"
    dataset.mkdir()
    for name in ("b.jpg", "nested/a.png"):
        path = dataset / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")
    config = tmp_path / "detection.yaml"
    config.write_text(
        "dataset: ./images\ncategories: [cat, dog]\n",
        encoding="utf-8",
    )
    return config


def box(category="cat", x=0, y=0, width=10, height=10):
    return {"category": category, "x": x, "y": y, "width": width, "height": height}


def test_queue_submission_reload_and_json_export(tmp_path):
    module = DetectionTaskType()
    project = module.load(project_config(tmp_path))
    page = module.queue(project, QueueRequest(filters={"status": "pending"}))
    assert page.total == 2

    item_id = str(page.items[0]["item_id"])
    result = {"image_size": {"width": 100, "height": 50}, "boxes": [box()]}
    saved = module.submit(project, Submission(item_id, result))
    assert saved.item["boxes"] == [box()]
    assert saved.status.details["pending"] == 1

    reloaded = DetectionTaskType()
    reopened = reloaded.load(tmp_path / "detection.yaml")
    annotated = reloaded.queue(reopened, QueueRequest(filters={"status": "annotated"}))
    assert annotated.items[0]["boxes"] == [box()]

    document = tmp_path / "images" / ".annotations" / "detection.json"
    state = json.loads(document.read_text(encoding="utf-8"))
    assert state["schema"] == 1

    native = reloaded.export(reopened, ExportRequest(format="json"))
    assert native.artifacts == (document,)


def test_coco_export_is_well_formed(tmp_path):
    module = DetectionTaskType()
    project = module.load(project_config(tmp_path))
    items = module.queue(project, QueueRequest()).items
    module.submit(
        project,
        Submission(
            str(items[0]["item_id"]),
            {"image_size": {"width": 100, "height": 50}, "boxes": [box("dog", 5, 5, 20, 20)]},
        ),
    )
    exported = module.export(project, ExportRequest(format="coco"))
    assert exported.format == "detection-coco"
    document = json.loads(exported.artifacts[0].read_text(encoding="utf-8"))
    assert {category["name"] for category in document["categories"]} == {"cat", "dog"}
    assert len(document["images"]) == 1
    assert document["images"][0]["width"] == 100
    assert document["annotations"][0]["bbox"] == [5, 5, 20, 20]
    assert document["annotations"][0]["category_id"] == document["categories"][1]["id"]


def test_submission_is_idempotent_and_rejects_stale_overwrite(tmp_path):
    module = DetectionTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    result = {"image_size": {"width": 100, "height": 50}, "boxes": [box()]}
    assert module.submit(project, Submission(item_id, result)).item["boxes"] == [box()]
    assert module.submit(project, Submission(item_id, result)).item["boxes"] == [box()]

    with pytest.raises(TaskConflictError, match="already annotated"):
        module.submit(
            project,
            Submission(item_id, {"image_size": {"width": 100, "height": 50}, "boxes": []}),
        )


def test_empty_boxes_list_is_a_valid_submission(tmp_path):
    module = DetectionTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    result = {"image_size": {"width": 100, "height": 50}, "boxes": []}
    assert module.submit(project, Submission(item_id, result)).item["boxes"] == []


@pytest.mark.parametrize(
    ("result", "message"),
    [
        ({"image_size": {"width": 0, "height": 50}, "boxes": []}, "positive"),
        ({"image_size": {"width": 100, "height": 50}, "boxes": [box(category="bird")]}, "unknown detection category"),
        (
            {"image_size": {"width": 100, "height": 50}, "boxes": [box(width=-1)]},
            "positive size",
        ),
        (
            {"image_size": {"width": 100, "height": 50}, "boxes": [box(x=95, width=10)]},
            "out of image bounds",
        ),
        (
            {"image_size": {"width": 100, "height": 50}, "boxes": [box(y=-1)]},
            "out of image bounds",
        ),
    ],
)
def test_box_constraints_are_enforced(tmp_path, result, message):
    module = DetectionTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    with pytest.raises(TaskOperationError, match=message):
        module.submit(project, Submission(item_id, result))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("dataset: ./images\n", "categories"),
        ("dataset: ./images\ncategories: [cat, cat]\n", "unique"),
        ("dataset: ./images\ncategories: [cat]\nextra: true\n", "unknown"),
        (
            "dataset: ./images\ncategories: [cat]\nannotations: ../out.json\n",
            "stay inside dataset",
        ),
    ],
)
def test_invalid_config_is_rejected(tmp_path, body, message):
    (tmp_path / "images").mkdir()
    config = tmp_path / "detection.yaml"
    config.write_text(body, encoding="utf-8")
    with pytest.raises(TaskOperationError, match=message):
        DetectionTaskType().load(config)
