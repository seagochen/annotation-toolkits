import csv
import json
from pathlib import Path

import pytest

from annotation_platform.caption_task import CaptionTaskType, MAX_CAPTION_LENGTH
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
    config = tmp_path / "caption.yaml"
    config.write_text("dataset: ./images\n", encoding="utf-8")
    return config


def test_queue_submission_reload_and_exports(tmp_path):
    module = CaptionTaskType()
    project = module.load(project_config(tmp_path))
    page = module.queue(project, QueueRequest(filters={"status": "pending"}))
    assert page.total == 2
    assert [item["image_path"] for item in page.items] == ["b.jpg", "nested/a.png"]

    item_id = str(page.items[0]["item_id"])
    saved = module.submit(project, Submission(item_id, {"caption": "  一只猫在窗边。\n睡觉。  "}))
    assert saved.item["caption"] == "一只猫在窗边。\n睡觉。"
    assert saved.status.details["pending"] == 1

    reloaded = CaptionTaskType()
    reopened = reloaded.load(tmp_path / "caption.yaml")
    captioned = reloaded.queue(reopened, QueueRequest(filters={"status": "captioned"}))
    assert captioned.items[0]["caption"] == "一只猫在窗边。\n睡觉。"
    document = tmp_path / "images" / ".annotations" / "caption.json"
    state = json.loads(document.read_text(encoding="utf-8"))
    assert state["schema"] == 1
    assert state["history"][0]["item_id"] == item_id

    native = reloaded.export(reopened, ExportRequest(format="json"))
    assert native.artifacts == (document,)
    exported = reloaded.export(reopened, ExportRequest(format="csv"))
    with exported.artifacts[0].open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["caption"] == "一只猫在窗边。\n睡觉。"


def test_submission_is_idempotent_and_rejects_stale_overwrite(tmp_path):
    module = CaptionTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    submission = Submission(item_id, {"caption": "A cat."})
    assert module.submit(project, submission).item["caption"] == "A cat."
    assert module.submit(project, submission).item["caption"] == "A cat."

    with pytest.raises(TaskConflictError, match="already captioned"):
        module.submit(project, Submission(item_id, {"caption": "A dog."}))


@pytest.mark.parametrize(
    ("caption", "message"),
    [
        ("", "empty"),
        ("   \n\t  ", "empty"),
        ("x" * (MAX_CAPTION_LENGTH + 1), "exceeds"),
        (123, "text"),
        (None, "text"),
    ],
)
def test_caption_constraints_are_enforced(tmp_path, caption, message):
    module = CaptionTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    with pytest.raises(TaskOperationError, match=message):
        module.submit(project, Submission(item_id, {"caption": caption}))


def test_failed_atomic_write_leaves_no_decision(tmp_path, monkeypatch):
    module = CaptionTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])

    def fail(path, value):
        raise OSError("disk full")

    monkeypatch.setattr("annotation_platform.caption_task.atomic_write_json", fail)
    with pytest.raises(TaskOperationError, match="disk full"):
        module.submit(project, Submission(item_id, {"caption": "A cat."}))
    assert not (tmp_path / "images" / ".annotations" / "caption.json").exists()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("dataset: ./images\nannotations: ../out.json\n", "stay inside dataset"),
        ("dataset: ./images\nextra: true\n", "unknown"),
        ("dataset: ./images\npatterns: [../*.jpg]\n", "stay inside dataset"),
    ],
)
def test_invalid_config_is_rejected(tmp_path, body, message):
    (tmp_path / "images").mkdir()
    config = tmp_path / "caption.yaml"
    config.write_text(body, encoding="utf-8")
    with pytest.raises(TaskOperationError, match=message):
        CaptionTaskType().load(config)
