import base64
import json
from pathlib import Path

import pytest
from PIL import Image

from annotation_platform.segmentation_task import SegmentationTaskType
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
    config = tmp_path / "segmentation.yaml"
    config.write_text(
        "dataset: ./images\ncategories: [background_object, foreground]\n",
        encoding="utf-8",
    )
    return config


def pixels_b64(width: int, height: int, value: int) -> str:
    return base64.b64encode(bytes([value]) * (width * height)).decode("ascii")


def test_queue_submission_reload_and_mask_export(tmp_path):
    module = SegmentationTaskType()
    project = module.load(project_config(tmp_path))
    page = module.queue(project, QueueRequest(filters={"status": "pending"}))
    assert page.total == 2

    item_id = str(page.items[0]["item_id"])
    result = {"image_size": {"width": 4, "height": 3}, "pixels": pixels_b64(4, 3, 2)}
    saved = module.submit(project, Submission(item_id, result))
    assert saved.item["mask_path"] == f"{item_id}.png"
    assert saved.status.details["pending"] == 1

    mask_file = tmp_path / "images" / ".annotations" / "segmentation" / f"{item_id}.png"
    assert mask_file.is_file()
    image = Image.open(mask_file)
    assert image.size == (4, 3)
    assert set(image.tobytes()) == {2}

    reloaded = SegmentationTaskType()
    reopened = reloaded.load(tmp_path / "segmentation.yaml")
    segmented = reloaded.queue(reopened, QueueRequest(filters={"status": "segmented"}))
    assert segmented.items[0]["mask_path"] == f"{item_id}.png"

    index_path = tmp_path / "images" / ".annotations" / "segmentation" / "index.json"
    state = json.loads(index_path.read_text(encoding="utf-8"))
    assert state["schema"] == 1

    exported = reloaded.export(reopened, ExportRequest(format="native"))
    assert index_path in exported.artifacts
    assert mask_file in exported.artifacts


def test_coco_export_references_mask_files(tmp_path):
    module = SegmentationTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    module.submit(
        project,
        Submission(item_id, {"image_size": {"width": 2, "height": 2}, "pixels": pixels_b64(2, 2, 1)}),
    )
    exported = module.export(project, ExportRequest(format="coco"))
    document = json.loads(exported.artifacts[0].read_text(encoding="utf-8"))
    assert {category["name"] for category in document["categories"]} == {
        "background_object",
        "foreground",
    }
    assert document["annotations"][0]["segmentation_mask"] == f"{item_id}.png"


def test_submission_is_idempotent_and_rejects_stale_overwrite(tmp_path):
    module = SegmentationTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    result = {"image_size": {"width": 2, "height": 2}, "pixels": pixels_b64(2, 2, 1)}
    assert module.submit(project, Submission(item_id, result)).item["pixel_hash"]
    first_hash = module.submit(project, Submission(item_id, result)).item["pixel_hash"]
    assert first_hash == module.submit(project, Submission(item_id, result)).item["pixel_hash"]

    different = {"image_size": {"width": 2, "height": 2}, "pixels": pixels_b64(2, 2, 2)}
    with pytest.raises(TaskConflictError, match="already segmented"):
        module.submit(project, Submission(item_id, different))


def test_pixel_count_mismatch_is_rejected(tmp_path):
    module = SegmentationTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    bad = {"image_size": {"width": 2, "height": 2}, "pixels": pixels_b64(2, 3, 1)}
    with pytest.raises(TaskOperationError, match="must contain"):
        module.submit(project, Submission(item_id, bad))


def test_unknown_category_index_is_rejected(tmp_path):
    module = SegmentationTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    bad = {"image_size": {"width": 2, "height": 2}, "pixels": pixels_b64(2, 2, 9)}
    with pytest.raises(TaskOperationError, match="unknown category"):
        module.submit(project, Submission(item_id, bad))


def test_malformed_base64_is_rejected(tmp_path):
    module = SegmentationTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    bad = {"image_size": {"width": 2, "height": 2}, "pixels": "not-base64!!"}
    with pytest.raises(TaskOperationError, match="base64"):
        module.submit(project, Submission(item_id, bad))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("dataset: ./images\n", "categories"),
        ("dataset: ./images\ncategories: [a, a]\n", "unique"),
        ("dataset: ./images\ncategories: [a]\nextra: true\n", "unknown"),
        (
            "dataset: ./images\ncategories: [a]\nannotations: ../out\n",
            "stay inside dataset",
        ),
    ],
)
def test_invalid_config_is_rejected(tmp_path, body, message):
    (tmp_path / "images").mkdir()
    config = tmp_path / "segmentation.yaml"
    config.write_text(body, encoding="utf-8")
    with pytest.raises(TaskOperationError, match=message):
        SegmentationTaskType().load(config)
