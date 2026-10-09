import copy
import hashlib
import json

import pytest
from annotation_platform.hosted import hosted_task_types, user_workspace
from annotation_platform.import_legacy import import_legacy
from annotation_platform.task_types import QueueRequest, Submission
from annotation_platform.workspace import Workspace


def export_fixture(tmp_path):
    source = tmp_path / "export"
    (source / "objects").mkdir(parents=True)
    image = b"test-image"
    checksum = hashlib.sha256(image).hexdigest()
    (source / "objects" / checksum).write_bytes(image)
    user = {"userId": "alice", "datasets": [{"id": "old-project", "user_id": "alice", "title": "旧项目", "deleted_at": None, "status": "active"}],
        "labels": [{"id": "cat", "user_id": "alice", "dataset_id": "old-project", "name": "猫", "color": "#fff"}],
        "assets": [{"id": "photo", "dataset_id": "old-project", "user_id": "alice", "mime_type": "image/png", "size_bytes": len(image),
                    "sha256": checksum, "width": 100, "height": 80, "filename": "original.png"}],
        "tasks": [{"id": "task-a", "asset_id": "photo", "dataset_id": "old-project", "user_id": "alice", "status": "completed", "revision": 7,
            "updated_at": "2026-01-01T00:00:00Z", "annotation_json": json.dumps({"version": 1, "assetId": "photo", "image": {"width": 100, "height": 80},
                "objects": [{"id": "box", "labelId": "cat", "geometry": {"kind": "bbox", "x": 1, "y": 2, "width": 10, "height": 20}}]})}],
        "suggestion_jobs": [{"id": "job", "task_id": "task-a", "dataset_id": "old-project", "user_id": "alice", "status": "success", "backend_run_id": "run-old"}],
        "suggestions": [{"id": "suggestion", "job_id": "job", "task_id": "task-a", "dataset_id": "old-project", "user_id": "alice", "status": "rejected"}]}
    bundle = {"schemaVersion": 1, "moduleId": "annotation", "users": [user], "counts": {key: len(user[key]) for key in user if key != "userId"}}
    (source / "export.json").write_text(json.dumps(bundle), encoding="utf8")
    return source, bundle


def test_conversion_preserves_revision_history_files_and_replays(tmp_path):
    source, bundle = export_fixture(tmp_path)
    target = tmp_path / "owned"
    report = import_legacy(source, target)
    assert report["projects"] == report["tasks"] == report["assets"] == 1
    root = user_workspace(target, "alice")
    original = json.loads((root / "legacy-records.json").read_text())
    assert original == bundle["users"][0]
    workspace = Workspace(root, hosted_task_types(), import_roots=[])
    entry = workspace.load_registry().get_entry("old-project")
    queue = entry.module.queue(entry.project, QueueRequest())
    item = queue.items[0]
    assert item["revision"] == 7
    assert item["polygons"] == [{"category": "猫", "points": [[1.0, 2.0], [11.0, 2.0], [11.0, 22.0], [1.0, 22.0]]}]
    assert (entry.project.root / "photo.png").read_bytes() == b"test-image"
    result = {"base_revision": 7, "image_size": {"width": 100, "height": 80}, "polygons": []}
    entry.module.submit(entry.project, Submission(item["item_id"], result))
    assert import_legacy(source, target)["replay"]
    assert entry.module.queue(entry.project, QueueRequest()).items[0]["revision"] == 8
    assert not user_workspace(target, "bob").exists()


def test_pending_objects_become_editable_prelabels(tmp_path):
    source, bundle = export_fixture(tmp_path)
    bundle["users"][0]["tasks"][0]["status"] = "in_progress"
    (source / "export.json").write_text(json.dumps(bundle))
    target = tmp_path / "owned"
    import_legacy(source, target)
    entry = Workspace(user_workspace(target, "alice"), hosted_task_types(), import_roots=[]).load_registry().get_entry("old-project")
    item = entry.module.queue(entry.project, QueueRequest()).items[0]
    assert item["source"] == "prelabel"
    assert item["revision"] == 0 and len(item["polygons"]) == 1
    assert entry.module.status(entry.project).details["pending"] == 1


@pytest.mark.parametrize("failure", ["foreign", "running", "checksum", "unsupported", "counts", "symlink"])
def test_rejected_input_never_creates_destination(tmp_path, failure):
    source, bundle = export_fixture(tmp_path)
    user = bundle["users"][0]
    if failure == "foreign": user["tasks"][0]["user_id"] = "bob"
    if failure == "running": user["suggestion_jobs"][0]["status"] = "running"
    if failure == "checksum": user["assets"][0]["size_bytes"] = 1000
    if failure == "counts": bundle["counts"]["tasks"] = 2
    if failure == "unsupported":
        doc = json.loads(user["tasks"][0]["annotation_json"])
        doc["objects"][0]["geometry"] = {"kind": "keypoints"}
        user["tasks"][0]["annotation_json"] = json.dumps(doc)
    if failure == "symlink":
        (source / "objects").rename(tmp_path / "elsewhere")
        (source / "objects").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    (source / "export.json").write_text(json.dumps(bundle))
    target = tmp_path / "owned"
    with pytest.raises(ValueError): import_legacy(source, target)
    assert not target.exists()
