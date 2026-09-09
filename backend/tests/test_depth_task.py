import base64
import json
from pathlib import Path

import pytest
from PIL import Image

from annotation_platform.depth_task import DepthTaskType
from annotation_platform.task_types import (
    ExportRequest,
    QueueRequest,
    Submission,
    TaskConflictError,
    TaskOperationError,
)


def project_config(tmp_path: Path, with_baseline: bool = True) -> Path:
    dataset = tmp_path / "images"
    dataset.mkdir()
    for name in ("b.jpg", "nested/a.png"):
        path = dataset / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")
    if with_baseline:
        baseline_dir = dataset / ".depth-baseline"
        baseline = baseline_dir / "b.png"
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_bytes(b"not-decoded-by-backend")
    config = tmp_path / "depth.yaml"
    config.write_text("dataset: ./images\n", encoding="utf-8")
    return config


def pixels_b64(width: int, height: int, value: int) -> str:
    return base64.b64encode(bytes([value]) * (width * height)).decode("ascii")


def test_queue_reports_baseline_path_when_present(tmp_path):
    module = DepthTaskType()
    project = module.load(project_config(tmp_path))
    page = module.queue(project, QueueRequest(filters={"status": "pending"}))
    assert page.total == 2
    by_path = {item["image_path"]: item for item in page.items}
    assert by_path["b.jpg"]["baseline_path"] == ".depth-baseline/b.png"
    assert by_path["nested/a.png"]["baseline_path"] is None


def test_submission_reload_and_native_export(tmp_path):
    module = DepthTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    result = {"image_size": {"width": 4, "height": 3}, "pixels": pixels_b64(4, 3, 200)}
    saved = module.submit(project, Submission(item_id, result))
    assert saved.item["depth_path"] == f"{item_id}.png"
    assert saved.status.details["pending"] == 1

    depth_file = tmp_path / "images" / ".annotations" / "depth" / f"{item_id}.png"
    image = Image.open(depth_file)
    assert image.size == (4, 3)
    assert set(image.tobytes()) == {200}

    reloaded = DepthTaskType()
    reopened = reloaded.load(tmp_path / "depth.yaml")
    edited = reloaded.queue(reopened, QueueRequest(filters={"status": "edited"}))
    assert edited.items[0]["depth_path"] == f"{item_id}.png"

    index_path = tmp_path / "images" / ".annotations" / "depth" / "index.json"
    state = json.loads(index_path.read_text(encoding="utf-8"))
    assert state["schema"] == 1

    exported = reloaded.export(reopened, ExportRequest(format="native"))
    assert index_path in exported.artifacts
    assert depth_file in exported.artifacts


def test_baseline_and_edited_files_never_pollute_the_queue(tmp_path):
    module = DepthTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    module.submit(
        project,
        Submission(item_id, {"image_size": {"width": 2, "height": 2}, "pixels": pixels_b64(2, 2, 5)}),
    )
    page = module.queue(project, QueueRequest())
    assert page.total == 2
    assert all(item["image_path"] in {"b.jpg", "nested/a.png"} for item in page.items)


def test_submission_is_idempotent_and_rejects_stale_overwrite(tmp_path):
    module = DepthTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    result = {"image_size": {"width": 2, "height": 2}, "pixels": pixels_b64(2, 2, 10)}
    assert module.submit(project, Submission(item_id, result)).item["pixel_hash"]
    assert module.submit(project, Submission(item_id, result)).item["pixel_hash"]

    different = {"image_size": {"width": 2, "height": 2}, "pixels": pixels_b64(2, 2, 20)}
    with pytest.raises(TaskConflictError, match="already edited"):
        module.submit(project, Submission(item_id, different))


def test_pixel_count_mismatch_is_rejected(tmp_path):
    module = DepthTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    bad = {"image_size": {"width": 2, "height": 2}, "pixels": pixels_b64(2, 3, 1)}
    with pytest.raises(TaskOperationError, match="must contain"):
        module.submit(project, Submission(item_id, bad))


def test_malformed_base64_is_rejected(tmp_path):
    module = DepthTaskType()
    project = module.load(project_config(tmp_path))
    item_id = str(module.queue(project, QueueRequest()).items[0]["item_id"])
    bad = {"image_size": {"width": 2, "height": 2}, "pixels": "***"}
    with pytest.raises(TaskOperationError, match="base64"):
        module.submit(project, Submission(item_id, bad))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("dataset: ./images\nextra: true\n", "unknown"),
        ("dataset: ./images\nannotations: ../out\n", "stay inside dataset"),
        ("dataset: ./images\ndepth_maps: ../baseline\n", "stay inside dataset"),
        (
            "dataset: ./images\ndepth_maps: .shared\nannotations: .shared\n",
            "must not overlap",
        ),
    ],
)
def test_invalid_config_is_rejected(tmp_path, body, message):
    (tmp_path / "images").mkdir()
    config = tmp_path / "depth.yaml"
    config.write_text(body, encoding="utf-8")
    with pytest.raises(TaskOperationError, match=message):
        DepthTaskType().load(config)
