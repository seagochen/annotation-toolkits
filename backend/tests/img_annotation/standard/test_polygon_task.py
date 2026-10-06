import json
from pathlib import Path

import pytest

from annotation_platform.img_annotation.standard.polygon_task import (
    PolygonTaskType,
    bounding_box,
    shoelace_area,
)
from annotation_platform.export_contracts import validate_files
from annotation_platform.task_types import (
    ExportRequest,
    QueueRequest,
    Submission,
    TaskConflictError,
    TaskOperationError,
)

IMAGES = ("a.jpg", "b.jpg", "c.jpg", "d.jpg", "nested/e.png")
SQUARE = [[10.0, 10.0], [50.0, 10.0], [50.0, 50.0], [10.0, 50.0]]


def ring(points):
    return [coordinate for point in points for coordinate in point]


def coco(**overrides) -> dict:
    """A COCO prelabel file: a.jpg and b.jpg valid, c.jpg partly invalid, plus junk."""
    document = {
        "images": [
            {"id": 1, "file_name": "a.jpg", "width": 100, "height": 80, "capture": "cam-1"},
            {"id": 2, "file_name": "b.jpg", "width": 100, "height": 80},
            {"id": 3, "file_name": "c.jpg", "width": 100, "height": 80, "capture": "cam-2"},
            {"id": 4, "file_name": "missing.jpg", "width": 100, "height": 80},
            {"id": 5, "file_name": "d.jpg"},
        ],
        "categories": [{"id": 7, "name": "material"}, {"id": 8, "name": "crack"},
                       {"id": 9, "name": "unconfigured"}],
        "annotations": [
            {"id": 11, "image_id": 1, "category_id": 7, "iscrowd": 0,
             "segmentation": [ring(SQUARE), ring([[60, 10], [90, 10], [75, 40]])]},
            {"id": 12, "image_id": 1, "category_id": 8, "segmentation": [ring([[0, 0], [100, 0], [100, 80]])]},
            # b.jpg: an image entry with no annotations is a valid "nothing here".
            {"id": 31, "image_id": 3, "category_id": 7, "iscrowd": 0, "segmentation": [ring(SQUARE)]},
            {"id": 32, "image_id": 3, "category_id": 7, "iscrowd": 1,
             "segmentation": {"counts": "abc", "size": [80, 100]}},
            {"id": 33, "image_id": 3, "category_id": 9, "segmentation": [ring(SQUARE)]},
            {"id": 34, "image_id": 3, "category_id": 7, "segmentation": [ring([[0, 0], [101, 0], [50, 50]])]},
            {"id": 35, "image_id": 3, "category_id": 7, "segmentation": [[0, 0, 10, 10]]},
            {"id": 36, "image_id": 3, "category_id": 7, "segmentation": {"counts": [1, 2], "size": [80, 100]}},
            {"id": 41, "image_id": 4, "category_id": 7, "segmentation": [ring(SQUARE)]},
            {"id": 51, "image_id": 5, "category_id": 7, "segmentation": [ring(SQUARE)]},
            {"id": 99, "image_id": 404, "category_id": 7, "segmentation": [ring(SQUARE)]},
        ],
    }
    document.update(overrides)
    return document


def project_config(tmp_path: Path, prelabels: dict | str | None = None, extra: str = "") -> Path:
    dataset = tmp_path / "images"
    for name in IMAGES:
        path = dataset / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")
    config = tmp_path / "polygon.yaml"
    body = "dataset: ./images\ncategories: [material, crack]\n" + extra
    if prelabels is not None:
        text = prelabels if isinstance(prelabels, str) else json.dumps(prelabels)
        (dataset / "prelabels.coco.json").write_text(text, encoding="utf-8")
        body += "prelabels: prelabels.coco.json\n"
    config.write_text(body, encoding="utf-8")
    return config


def items(module, project, **filters) -> dict:
    page = module.queue(project, QueueRequest(filters=filters))
    return {item["image_path"]: item for item in page.items}


def result(polygons, base_revision=0, width=100, height=80, boxes=None, points=None):
    submitted = {
        "image_size": {"width": width, "height": height},
        "polygons": polygons,
        "base_revision": base_revision,
    }
    if boxes is not None:
        submitted["boxes"] = boxes
    if points is not None:
        submitted["points"] = points
    return submitted


BOX = {"category": "crack", "x": 5.0, "y": 6.0, "width": 20.0, "height": 10.0}
POINT = {"category": "material", "x": 30.0, "y": 40.0}


def test_geometry_helpers():
    assert shoelace_area(SQUARE) == 1600
    assert shoelace_area(list(reversed(SQUARE))) == 1600
    assert shoelace_area([[0, 0], [4, 0], [0, 3]]) == 6
    assert bounding_box([[5, 7], [1, 9], [3, 2]]) == [1, 2, 4, 7]


def test_without_prelabels_every_image_starts_blank(tmp_path):
    module = PolygonTaskType()
    project = module.load(project_config(tmp_path))
    queued = items(module, project)
    assert sorted(queued) == sorted(IMAGES)
    assert queued["a.jpg"] == {
        "item_id": queued["a.jpg"]["item_id"], "image_path": "a.jpg", "revision": 0,
        "source": "none", "image_size": None, "polygons": [], "boxes": [], "points": [],
        "draft": False, "annotated": False,
    }
    status = module.status(project)
    assert status.state == "reviewing"
    assert status.details["prelabels"] is None
    assert "prelabel_issues" not in status.details


def test_prelabels_load_per_image_and_report_everything_not_loaded(tmp_path):
    module = PolygonTaskType()
    project = module.load(project_config(tmp_path, coco()))
    queued = items(module, project)
    assert queued["a.jpg"]["source"] == "prelabel"
    assert queued["a.jpg"]["image_size"] == {"width": 100, "height": 80}
    assert queued["a.jpg"]["polygons"] == [
        {"category": "material", "points": SQUARE},
        {"category": "material", "points": [[60.0, 10.0], [90.0, 10.0], [75.0, 40.0]]},
        {"category": "crack", "points": [[0.0, 0.0], [100.0, 0.0], [100.0, 80.0]]},
    ]
    assert queued["b.jpg"]["source"] == "prelabel" and queued["b.jpg"]["polygons"] == []
    # c.jpg has one valid and several invalid annotations: nothing is loaded.
    assert queued["c.jpg"]["source"] == "none" and queued["c.jpg"]["polygons"] == []
    assert queued["d.jpg"]["source"] == "none"
    assert queued["nested/e.png"]["source"] == "none"
    # The prelabel file is never a queue item, even if a pattern matches it.
    assert "prelabels.coco.json" not in queued

    status = module.status(project)
    details = status.details
    assert status.state == "reviewing"
    assert details["prelabel_images"] == 2
    issues = details["prelabel_issues"]
    assert details["prelabel_issue_count"] == len(issues)
    reasons = {(issue["image_id"], issue["annotation_id"]): issue["reason"] for issue in issues}
    assert "iscrowd must be 0" in reasons[(3, 32)]
    assert "not configured" in reasons[(3, 33)]
    assert "outside the image" in reasons[(3, 34)]
    assert "fewer than 3 points" in reasons[(3, 35)]
    assert "RLE" in reasons[(3, 36)]
    assert "starts blank" in reasons[(3, None)]
    assert "does not match any dataset image" in reasons[(4, None)]
    assert "1 annotation(s) were not loaded" in reasons[(4, None)]
    assert "width and height" in reasons[(5, None)]
    assert "does not refer to an `images` entry" in reasons[(404, 99)]
    assert (3, 31) not in reasons
    assert all(issue["file_name"] == "c.jpg" for issue in issues if issue["image_id"] == 3)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("{not json", "not valid UTF-8 JSON"),
        ("[]", "not a COCO document"),
        (json.dumps({"images": [], "annotations": []}), "not a COCO document"),
        (json.dumps(coco(categories=[{"id": 1, "name": "a"}, {"id": 1, "name": "b"}])), "listed twice"),
        (json.dumps(coco(categories=[{"name": "a"}])), "integer id and a name"),
    ],
)
def test_unparseable_prelabels_fail_the_project_but_not_load(tmp_path, document, message):
    module = PolygonTaskType()
    project = module.load(project_config(tmp_path, document))
    with pytest.raises(TaskOperationError, match=message):
        module.queue(project, QueueRequest())
    status = module.status(project)
    assert status.state == "invalid"
    assert message.split()[0] in status.details["prelabel_error"]
    item_id = next(iter(module.store_type(project.value).images()))[0]
    with pytest.raises(TaskOperationError, match=message):
        module.submit(project, Submission(item_id, result([])))


def test_missing_prelabel_file_is_reported(tmp_path):
    module = PolygonTaskType()
    config = project_config(tmp_path, extra="prelabels: later.coco.json\n")
    project = module.load(config)
    assert module.status(project).state == "invalid"
    with pytest.raises(TaskOperationError, match="prelabels file not found"):
        module.queue(project, QueueRequest())


def test_duplicate_image_entries_are_not_loaded(tmp_path):
    document = coco(images=[
        {"id": 1, "file_name": "a.jpg", "width": 100, "height": 80},
        {"id": 2, "file_name": "a.jpg", "width": 100, "height": 80},
        {"id": 3, "file_name": "b.jpg", "width": 100, "height": 80},
        {"id": 3, "file_name": "c.jpg", "width": 100, "height": 80},
    ], annotations=[])
    module = PolygonTaskType()
    project = module.load(project_config(tmp_path, document))
    queued = items(module, project)
    assert {path: item["source"] for path, item in queued.items() if path < "d"} == {
        "a.jpg": "none", "b.jpg": "none", "c.jpg": "none",
    }
    reasons = [issue["reason"] for issue in module.status(project).details["prelabel_issues"]]
    assert sum("more than one `images` entry" in reason for reason in reasons) == 3


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r.update(polygons=[{"category": "material", "points": SQUARE[:2]}]), "at least 3 points"),
        (lambda r: r.update(polygons=[{"category": "material", "points": [[0, 0], [101, 0], [0, 5]]}]), "out of image bounds"),
        (lambda r: r.update(polygons=[{"category": "material", "points": [[0, 0], [-1, 0], [0, 5]]}]), "out of image bounds"),
        (lambda r: r.update(polygons=[{"category": "material", "points": [[0, 0], [float("nan"), 0], [0, 5]]}]), "finite"),
        (lambda r: r.update(polygons=[{"category": "material", "points": [[0, 0], [float("inf"), 0], [0, 5]]}]), "finite"),
        (lambda r: r.update(polygons=[{"category": "material", "points": [[0, 0], ["1", 0], [0, 5]]}]), "number"),
        (lambda r: r.update(polygons=[{"category": "material", "points": [[0, 0], [1, 0, 2], [0, 5]]}]), r"\[x, y\]"),
        (lambda r: r.update(polygons=[{"category": "unknown", "points": SQUARE}]), "unknown polygon category"),
        (lambda r: r.update(polygons=[{"category": "material", "points": SQUARE, "id": 1}]), "exactly category and points"),
        (lambda r: r.update(polygons=None), "`polygons` list"),
        (lambda r: r.update(boxes={}), "`boxes` must be a list"),
        (lambda r: r.update(boxes=[{**BOX, "width": 0}]), "positive size"),
        (lambda r: r.update(boxes=[{**BOX, "x": 90.0}]), "box 0 is out of image bounds"),
        (lambda r: r.update(boxes=[{**BOX, "category": "unknown"}]), "unknown polygon box category"),
        (lambda r: r.update(boxes=[{**BOX, "id": 1}]), "exactly category, x, y, width and height"),
        (lambda r: r.update(points=[{**POINT, "x": 100.5}]), "point 0 is out of image bounds"),
        (lambda r: r.update(points=[{**POINT, "y": float("nan")}]), "finite"),
        (lambda r: r.update(points=[{"category": "material", "x": 1.0}]), "exactly category, x and y"),
        (lambda r: r.pop("base_revision"), "base_revision"),
        (lambda r: r.update(base_revision=-1), "base_revision"),
        (lambda r: r.update(base_revision=True), "base_revision"),
        (lambda r: r.update(image_size={"width": 0, "height": 80}), "positive whole numbers"),
        # a.jpg's prelabels were made for 100x80.
        (lambda r: r.update(image_size={"width": 200, "height": 160}), "does not match"),
    ],
)
def test_invalid_submissions_are_rejected(tmp_path, change, message):
    module = PolygonTaskType()
    project = module.load(project_config(tmp_path, coco()))
    item_id = items(module, project)["a.jpg"]["item_id"]
    submitted = result([{"category": "material", "points": SQUARE}])
    change(submitted)
    with pytest.raises(TaskOperationError, match=message):
        module.submit(project, Submission(item_id, submitted))
    assert not (tmp_path / "images" / ".annotations" / "polygon.json").exists()


def test_revisions_overwrite_from_current_and_conflict_from_stale(tmp_path):
    module = PolygonTaskType()
    project = module.load(project_config(tmp_path, coco()))
    item_id = items(module, project)["a.jpg"]["item_id"]
    first = [{"category": "material", "points": SQUARE}]
    saved = module.submit(project, Submission(item_id, result(first))).item
    assert saved["revision"] == 1
    # Equivalent resubmission: idempotent, whatever revision it names.
    assert module.submit(project, Submission(item_id, result(first))).item["revision"] == 1
    assert module.submit(project, Submission(item_id, result(first, 1))).item["revision"] == 1

    second = [{"category": "crack", "points": SQUARE}]
    saved = module.submit(project, Submission(item_id, result(second, base_revision=1))).item
    assert saved["revision"] == 2 and saved["polygons"] == second

    # A page still showing revision 1 (or nothing saved) must not overwrite revision 2.
    for stale in (0, 1):
        with pytest.raises(TaskConflictError, match="at revision 2"):
            module.submit(project, Submission(item_id, result([], base_revision=stale)))
    # Neither may a page that claims a revision that does not exist yet.
    with pytest.raises(TaskConflictError):
        module.submit(project, Submission(item_id, result([], base_revision=5)))
    # The stored image size is fixed by the first save.
    with pytest.raises(TaskOperationError, match="does not match"):
        module.submit(project, Submission(item_id, result([], 2, width=99)))

    emptied = module.submit(project, Submission(item_id, result([], base_revision=2))).item
    assert emptied["revision"] == 3 and emptied["polygons"] == []

    document = json.loads((tmp_path / "images" / ".annotations" / "polygon.json").read_text())
    assert document["items"][item_id]["revision"] == 3
    assert [(event["revision"], event["base_revision"]) for event in document["history"]] == [
        (1, 0), (2, 1), (3, 2),
    ]
    assert document["history"][0]["polygons"] == first

    reopened = PolygonTaskType()
    project = reopened.load(tmp_path / "polygon.yaml")
    view = items(reopened, project, status="annotated")["a.jpg"]
    assert view["source"] == "annotation" and view["revision"] == 3 and view["polygons"] == []
    assert reopened.status(project).details["annotated"] == 1


def test_first_submission_must_start_from_revision_zero(tmp_path):
    module = PolygonTaskType()
    project = module.load(project_config(tmp_path))
    item_id = items(module, project)["d.jpg"]["item_id"]
    with pytest.raises(TaskConflictError, match="at revision 0"):
        module.submit(project, Submission(item_id, result([], base_revision=1)))
    assert module.submit(project, Submission(item_id, result([], width=640, height=480))).item["revision"] == 1


def validate_coco(document: dict) -> None:
    """Field-and-type check of the COCO object detection/segmentation format."""
    assert set(document) >= {"images", "annotations", "categories"}
    image_ids = set()
    for image in document["images"]:
        assert isinstance(image["id"], int) and isinstance(image["file_name"], str)
        assert isinstance(image["width"], int) and isinstance(image["height"], int)
        image_ids.add(image["id"])
    assert len(image_ids) == len(document["images"])
    category_ids = {category["id"] for category in document["categories"]}
    assert all(isinstance(category["name"], str) for category in document["categories"])
    annotation_ids = set()
    for annotation in document["annotations"]:
        assert isinstance(annotation["id"], int) and annotation["id"] not in annotation_ids
        annotation_ids.add(annotation["id"])
        assert annotation["image_id"] in image_ids
        assert annotation["category_id"] in category_ids
        assert annotation["iscrowd"] == 0
        segmentation = annotation["segmentation"]
        assert isinstance(segmentation, list) and segmentation
        for polygon in segmentation:
            assert len(polygon) % 2 == 0 and len(polygon) >= 6
            assert all(isinstance(value, float) for value in polygon)
        assert isinstance(annotation["area"], float)
        assert len(annotation["bbox"]) == 4 and all(isinstance(v, float) for v in annotation["bbox"])


def test_coco_export_is_standard_and_passes_image_metadata_through(tmp_path):
    module = PolygonTaskType()
    project = module.load(project_config(tmp_path, coco()))
    queued = items(module, project)
    triangle = [[60.0, 10.0], [90.0, 10.0], [75.0, 40.0]]
    module.submit(project, Submission(queued["c.jpg"]["item_id"], result(
        [{"category": "crack", "points": triangle}])))
    module.submit(project, Submission(queued["a.jpg"]["item_id"], result([
        {"category": "material", "points": SQUARE},
        {"category": "crack", "points": [[0, 0], [100, 0], [100, 80]]},
    ])))
    module.submit(project, Submission(queued["d.jpg"]["item_id"], result([], width=640, height=480)))

    exported = module.export(project, ExportRequest(format="coco"))
    assert exported.artifacts == (tmp_path / "images" / ".annotations" / "polygon.coco.json",)
    assert exported.metadata == {"images": 3, "annotations": 3}
    document = json.loads(exported.artifacts[0].read_text(encoding="utf-8"))
    validate_coco(document)
    assert document["categories"] == [
        {"id": 1, "name": "material", "keypoints": ["point"], "skeleton": []},
        {"id": 2, "name": "crack", "keypoints": ["point"], "skeleton": []},
    ]
    assert document["images"] == [
        {"capture": "cam-1", "id": 1, "file_name": "a.jpg", "width": 100, "height": 80},
        # c.jpg's prelabels were not loaded, but its image metadata is still the user's.
        {"capture": "cam-2", "id": 2, "file_name": "c.jpg", "width": 100, "height": 80},
        {"id": 3, "file_name": "d.jpg", "width": 640, "height": 480},
    ]
    first, second, third = document["annotations"]
    assert first == {
        "id": 1, "image_id": 1, "category_id": 1,
        "segmentation": [[10.0, 10.0, 50.0, 10.0, 50.0, 50.0, 10.0, 50.0]],
        "area": 1600.0, "bbox": [10.0, 10.0, 40.0, 40.0], "iscrowd": 0,
    }
    assert second["area"] == 4000.0 and second["bbox"] == [0.0, 0.0, 100.0, 80.0]
    assert third["image_id"] == 2 and third["category_id"] == 2
    assert third["area"] == shoelace_area(triangle) == 450.0

    native = module.export(project, ExportRequest(format="native"))
    assert native.artifacts == (tmp_path / "images" / ".annotations" / "polygon.json",)
    with pytest.raises(TaskOperationError, match="does not support"):
        module.export(project, ExportRequest(format="voc"))


def test_boxes_and_points_are_saved_viewed_and_exported(tmp_path):
    module = PolygonTaskType()
    project = module.load(project_config(tmp_path))
    item_id = items(module, project)["b.jpg"]["item_id"]
    submitted = result([{"category": "material", "points": SQUARE}], boxes=[BOX], points=[POINT])
    saved = module.submit(project, Submission(item_id, submitted)).item
    assert saved["revision"] == 1
    item = items(module, project)["b.jpg"]
    assert (item["polygons"], item["boxes"], item["points"]) == (
        [{"category": "material", "points": SQUARE}], [BOX], [POINT])
    # The same shapes again are idempotent; a changed point is a new revision.
    assert module.submit(project, Submission(item_id, {**submitted, "base_revision": 0})).item["revision"] == 1
    moved = result([{"category": "material", "points": SQUARE}], base_revision=1,
                   boxes=[BOX], points=[{**POINT, "x": 31.0}])
    assert module.submit(project, Submission(item_id, moved)).item["revision"] == 2

    coco_file = module.export(project, ExportRequest(format="coco")).artifacts[0]
    validate_files("polygon-coco/v2", [coco_file])
    polygon, box, point = json.loads(coco_file.read_text(encoding="utf-8"))["annotations"]
    assert polygon["segmentation"] and "keypoints" not in polygon
    assert box == {"id": 2, "image_id": 1, "category_id": 2, "segmentation": [],
                   "area": 200.0, "bbox": [5.0, 6.0, 20.0, 10.0], "iscrowd": 0}
    assert point == {"id": 3, "image_id": 1, "category_id": 1, "segmentation": [], "area": 0.0,
                     "bbox": [31.0, 40.0, 0.0, 0.0], "keypoints": [31.0, 40.0, 2],
                     "num_keypoints": 1, "iscrowd": 0}
    native = module.export(project, ExportRequest(format="native")).artifacts
    validate_files("polygon-json/v2", native)


def test_schema_1_sidecars_are_read_and_upgraded_when_written(tmp_path):
    module = PolygonTaskType()
    project = module.load(project_config(tmp_path))
    queued = items(module, project)
    sidecar = tmp_path / "images" / ".annotations" / "polygon.json"
    sidecar.parent.mkdir()
    old_item = {"image_path": "a.jpg", "image_size": {"width": 100, "height": 80}, "revision": 1,
                "polygons": [{"category": "material", "points": SQUARE}]}
    sidecar.write_text(json.dumps({"schema": 1, "items": {queued["a.jpg"]["item_id"]: old_item},
                                   "history": []}), encoding="utf-8")
    validate_files("polygon-json/v1", [sidecar])
    item = items(module, project)["a.jpg"]
    assert (item["source"], item["boxes"], item["points"]) == ("annotation", [], [])
    assert json.loads(sidecar.read_text(encoding="utf-8"))["schema"] == 1  # reading never writes

    # Exporting rewrites it as the current contract, every item with all three lists.
    module.export(project, ExportRequest(format="native"))
    upgraded = json.loads(sidecar.read_text(encoding="utf-8"))
    assert upgraded["schema"] == 2
    assert upgraded["items"][queued["a.jpg"]["item_id"]] == {**old_item, "boxes": [], "points": []}
    validate_files("polygon-json/v2", [sidecar])
    # Re-saving the unchanged shapes is still idempotent after the upgrade.
    same = result([{"category": "material", "points": SQUARE}], base_revision=1, boxes=[], points=[])
    assert module.submit(project, Submission(queued["a.jpg"]["item_id"], same)).item["revision"] == 1


@pytest.mark.parametrize(
    ("annotation", "reason"),
    [
        ({"keypoints": [1, 2, 2, 3, 4, 2]}, "single-point keypoint"),
        ({"keypoints": [1, 2, 0]}, "visibility 1 or 2"),
        ({"keypoints": [101, 2, 2]}, "keypoint is outside"),
        ({"segmentation": [], "bbox": [0, 0, 0, 5]}, "positive size"),
        ({"segmentation": [], "bbox": [90, 0, 20, 5]}, "bbox is outside"),
        ({"segmentation": []}, "needs a bbox"),
    ],
)
def test_invalid_box_and_keypoint_prelabels_leave_the_image_blank(tmp_path, annotation, reason):
    document = coco(annotations=[{"id": 1, "image_id": 2, "category_id": 7, **annotation}])
    module = PolygonTaskType()
    project = module.load(project_config(tmp_path, document))
    assert items(module, project)["b.jpg"]["source"] == "none"
    reasons = [issue["reason"] for issue in module.status(project).details["prelabel_issues"]]
    assert any(reason in text for text in reasons), reasons


def test_coco_export_round_trips_as_prelabels(tmp_path):
    module = PolygonTaskType()
    project = module.load(project_config(tmp_path))
    queued = items(module, project)
    module.submit(project, Submission(queued["nested/e.png"]["item_id"], result(
        [{"category": "material", "points": SQUARE}], boxes=[BOX], points=[POINT])))
    exported = module.export(project, ExportRequest(format="coco")).artifacts[0]

    second = tmp_path / "second"
    second.mkdir()
    (second / "images").mkdir()
    (second / "images" / "nested").mkdir()
    (second / "images" / "nested" / "e.png").write_bytes(b"image")
    (second / "images" / "pre.json").write_bytes(exported.read_bytes())
    config = second / "polygon.yaml"
    config.write_text("dataset: ./images\ncategories: [material, crack]\nprelabels: pre.json\n")
    project = module.load(config)
    item = items(module, project)["nested/e.png"]
    assert item["source"] == "prelabel"
    assert item["polygons"] == [{"category": "material", "points": SQUARE}]
    assert (item["boxes"], item["points"]) == ([BOX], [POINT])
    assert module.status(project).details["prelabel_issue_count"] == 0


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("dataset: ./images\n", "`categories` must be a non-empty list"),
        ("dataset: ./images\ncategories: [a]\nprelabels: ../outside.json\n", "stay inside dataset"),
        ("dataset: ./images\ncategories: [a]\nprelabels: /abs.json\n", "dataset-relative"),
        ("dataset: ./images\ncategories: [a]\nprelabels: x.json\nannotations: x.json\n", "different files"),
        ("dataset: ./images\ncategories: [a]\nunknown: 1\n", "unknown polygon config keys"),
    ],
)
def test_invalid_config_is_rejected(tmp_path, body, message):
    (tmp_path / "images").mkdir()
    config = tmp_path / "polygon.yaml"
    config.write_text(body, encoding="utf-8")
    with pytest.raises(TaskOperationError, match=message):
        PolygonTaskType().load(config)


def test_a_draft_is_a_working_copy_that_submitting_replaces(tmp_path):
    module = PolygonTaskType()
    project = module.load(project_config(tmp_path, coco()))
    a = items(module, project)["a.jpg"]
    assert a["source"] == "prelabel" and a["draft"] is False

    saved = module.save_draft(project, Submission(a["item_id"], result([{"category": "crack", "points": SQUARE}])))
    assert saved["draft"] is True
    drafts = json.loads(project.value.annotations.with_name("polygon.drafts.json").read_text(encoding="utf-8"))
    assert list(drafts["items"]) == [a["item_id"]]
    # The queue shows the draft, but the item is still pending and nothing is exported.
    queued = items(module, project)["a.jpg"]
    assert (queued["source"], queued["draft"], queued["annotated"], queued["revision"]) == ("draft", True, False, 0)
    assert queued["polygons"] == [{"category": "crack", "points": SQUARE}]
    assert module.status(project).details["pending"] == len(IMAGES)
    assert not project.value.annotations.exists()

    # Submitting makes it a result and drops the draft (and the empty drafts file).
    module.submit(project, Submission(a["item_id"], result([{"category": "crack", "points": SQUARE}])))
    queued = items(module, project)["a.jpg"]
    assert (queued["source"], queued["draft"], queued["annotated"], queued["revision"]) == ("annotation", False, True, 1)
    assert not project.value.annotations.with_name("polygon.drafts.json").exists()


def test_a_draft_back_at_the_starting_point_is_dropped(tmp_path):
    module = PolygonTaskType()
    project = module.load(project_config(tmp_path))
    a = items(module, project)["a.jpg"]
    module.save_draft(project, Submission(a["item_id"], result([{"category": "crack", "points": SQUARE}])))
    # Undone back to the blank start: no draft is kept.
    assert module.save_draft(project, Submission(a["item_id"], result([]))) == {"item_id": a["item_id"], "draft": False}
    assert items(module, project)["a.jpg"]["source"] == "none"


def test_a_draft_is_validated_like_a_result(tmp_path):
    module = PolygonTaskType()
    project = module.load(project_config(tmp_path, coco()))
    a = items(module, project)["a.jpg"]
    with pytest.raises(TaskOperationError, match="unknown polygon category"):
        module.save_draft(project, Submission(a["item_id"], result([{"category": "", "points": SQUARE}])))
    with pytest.raises(TaskOperationError, match="does not match"):
        module.save_draft(project, Submission(a["item_id"], result([], width=640, height=480)))
    with pytest.raises(TaskOperationError, match="unknown polygon item"):
        module.save_draft(project, Submission("nope", result([])))
