import csv
import json
from pathlib import Path

import pytest

from annotation_platform.classification_task import ClassificationTaskType
from annotation_platform.task_types import (
    ExportRequest,
    QueueRequest,
    Submission,
    TaskConflictError,
    TaskOperationError,
)


def project_config(tmp_path: Path, mode: str = "single") -> Path:
    dataset = tmp_path / "images"
    dataset.mkdir()
    for name in ("b.jpg", "nested/a.png"):
        path = dataset / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")
    config = tmp_path / "classification.yaml"
    config.write_text(
        "dataset: ./images\n"
        "labels: [cat, dog, 室外]\n"
        f"mode: {mode}\n",
        encoding="utf-8",
    )
    return config


def test_single_label_queue_submission_reload_and_exports(tmp_path):
    module = ClassificationTaskType()
    project = module.load(project_config(tmp_path))
    page = module.queue(project, QueueRequest(filters={"status": "pending"}))
    assert page.total == 2
    assert [item["image_path"] for item in page.items] == ["b.jpg", "nested/a.png"]

    item_id = str(page.items[0]["item_id"])
    saved = module.submit(project, Submission(item_id, {"labels": ["cat"]}))
    assert saved.item["labels"] == ["cat"]
    assert saved.status.details["pending"] == 1

    # A fresh module reads the same local document after a page/process reload.
    reloaded = ClassificationTaskType()
    reopened = reloaded.load(tmp_path / "classification.yaml")
    labelled = reloaded.queue(reopened, QueueRequest(filters={"status": "labelled"}))
    assert labelled.items[0]["labels"] == ["cat"]
    document = tmp_path / "images" / ".annotations" / "classification.json"
    state = json.loads(document.read_text(encoding="utf-8"))
    assert state["schema"] == 1
    assert state["history"][0]["item_id"] == item_id

    native = reloaded.export(reopened, ExportRequest(format="json"))
    assert native.artifacts == (document,)
    exported = reloaded.export(reopened, ExportRequest(format="csv"))
    with exported.artifacts[0].open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert json.loads(rows[0]["labels"]) == ["cat"]


def test_multi_label_is_normalized_and_submission_is_idempotent(tmp_path):
    module = ClassificationTaskType()
    project = module.load(project_config(tmp_path, "multi"))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    submission = Submission(item_id, {"labels": ["室外", "cat"]})
    assert module.submit(project, submission).item["labels"] == ["cat", "室外"]
    assert module.submit(project, submission).item["labels"] == ["cat", "室外"]

    with pytest.raises(TaskConflictError, match="already labelled"):
        module.submit(project, Submission(item_id, {"labels": ["dog"]}))


@pytest.mark.parametrize(
    ("mode", "labels", "message"),
    [
        ("single", [], "exactly one"),
        ("single", ["cat", "dog"], "exactly one"),
        ("multi", [], "at least one"),
        ("multi", ["cat", "cat"], "duplicates"),
        ("multi", ["unknown"], "unknown classification labels"),
    ],
)
def test_label_constraints_are_enforced(tmp_path, mode, labels, message):
    module = ClassificationTaskType()
    project = module.load(project_config(tmp_path, mode))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    with pytest.raises(TaskOperationError, match=message):
        module.submit(project, Submission(item_id, {"labels": labels}))


def test_failed_atomic_write_leaves_no_decision(tmp_path, monkeypatch):
    module = ClassificationTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])

    def fail(path, value):
        raise OSError("disk full")

    monkeypatch.setattr("annotation_platform.classification_task.atomic_write_json", fail)
    with pytest.raises(TaskOperationError, match="disk full"):
        module.submit(project, Submission(item_id, {"labels": ["cat"]}))
    assert not (tmp_path / "images" / ".annotations" / "classification.json").exists()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("dataset: ./images\nlabels: [cat]\nmode: other\n", "mode"),
        ("dataset: ./images\nlabels: [cat, cat]\nmode: single\n", "unique"),
        (
            "dataset: ./images\nlabels: [cat]\nmode: single\nannotations: ../out.json\n",
            "stay inside dataset",
        ),
        ("dataset: ./images\nlabels: [cat]\nmode: single\nextra: true\n", "unknown"),
        (
            "dataset: ./images\nlabels: [cat]\nmode: single\npatterns: [../*.jpg]\n",
            "stay inside dataset",
        ),
    ],
)
def test_invalid_config_is_rejected(tmp_path, body, message):
    (tmp_path / "images").mkdir()
    config = tmp_path / "classification.yaml"
    config.write_text(body, encoding="utf-8")
    with pytest.raises(TaskOperationError, match=message):
        ClassificationTaskType().load(config)
