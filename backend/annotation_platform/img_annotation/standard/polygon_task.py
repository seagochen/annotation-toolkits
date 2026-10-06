"""Editable vector annotation (polygons, boxes, keypoints) with COCO import/export.

Unlike segmentation (where polygons are only a way to paint a raster), the
shapes *are* the result here: each image gets lists of polygons
``{category, points}``, boxes ``{category, x, y, width, height}`` and
keypoints ``{category, x, y}`` in original-image pixel coordinates, which stay
editable after they are submitted. Boxes and keypoints were added in sidecar
schema 2; a schema 1 sidecar (polygons only) is still read, and is rewritten
as schema 2 the next time the project saves or exports.

Prelabels (``prelabels``, optional) are a read-only standard COCO polygon file
inside the dataset. Each dataset image whose COCO entry is entirely valid starts
from its prelabel polygons; an image with **any** invalid annotation starts
blank instead, so a partly loaded prelabel never looks complete. Everything not
loaded is reported with its reason in the project status. A file that cannot be
parsed at all makes the project unusable until it is fixed (queue and submit
fail; status reports ``invalid``) -- but never breaks ``load()``, because one
bad file must not take the whole project list down.

Revisions: every saved result carries a ``revision`` (1, 2, ...). A submission
names the ``base_revision`` it was edited from (0 for "nothing saved yet"):
when it matches the stored revision the result is replaced and a history entry
appended; otherwise it is a conflict (the page was stale). An equivalent
resubmission is idempotent and does not create a revision.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from local_files import atomic_write_json

from ...export_contracts import coco_info
from ..common.image_dataset import (
    ImageTaskStore,
    ImageTaskType,
    config_patterns,
    dataset_relative,
    finite_number,
    image_size,
    is_image_size,
    read_config,
    text_list,
)
from ...task_types import (
    STATUS_MISSING,
    ExportRequest,
    ExportResult,
    QueuePage,
    QueueRequest,
    Submission,
    TaskConflictError,
    TaskOperationError,
    TaskStatus,
)

CONFIG_KEYS = frozenset({"dataset", "categories", "patterns", "prelabels", "annotations"})
POLYGON_FIELDS = frozenset({"category", "points"})
BOX_FIELDS = frozenset({"category", "x", "y", "width", "height"})
POINT_FIELDS = frozenset({"category", "x", "y"})
# Sidecar `schema` written by this module; 1 (polygons only) is still read.
SIDECAR_SCHEMA = 2
COCO_VERSION = 2
COCO_IMAGE_FIELDS = frozenset({"id", "file_name", "width", "height"})
MIN_POINTS = 3
# The project cannot be used until its prelabel file is fixed.
STATUS_INVALID = "invalid"
# Issues listed in the status; the count always covers all of them.
MAX_REPORTED_ISSUES = 200


@dataclass(frozen=True)
class PolygonProject:
    config_path: Path
    dataset: Path
    categories: tuple[str, ...]
    patterns: tuple[str, ...]
    prelabels: Path | None
    annotations: Path


def load_config(path: Path) -> PolygonProject:
    raw, dataset = read_config(path, "polygon", CONFIG_KEYS)
    categories = text_list(raw.get("categories"), "polygon", "categories")
    patterns = config_patterns(raw, "polygon")
    prelabels = (
        None
        if raw.get("prelabels") in (None, "")
        else dataset_relative(dataset, raw.get("prelabels"), "polygon", "prelabels", "")
    )
    annotations = dataset_relative(
        dataset, raw.get("annotations"), "polygon", "annotations", ".annotations/polygon.json")
    if prelabels is not None and prelabels == annotations:
        raise TaskOperationError("polygon `prelabels` and `annotations` must be different files")
    return PolygonProject(path.resolve(), dataset, categories, patterns, prelabels, annotations)


# ------------------------------------------------------------- geometry


def shoelace_area(points: list[list[float]]) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def bounding_box(points: list[list[float]]) -> list[float]:
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


def _coordinate(value: object, name: str) -> float:
    # `finite_number` already rejects bools, strings, NaN and infinities.
    return finite_number(value, "polygon", name)


def _points(value: object, size: dict, name: str) -> list[list[float]]:
    if not isinstance(value, list) or len(value) < MIN_POINTS:
        raise TaskOperationError(f"polygon {name} needs at least {MIN_POINTS} points")
    points = []
    for index, point in enumerate(value):
        if not isinstance(point, list) or len(point) != 2:
            raise TaskOperationError(f"polygon {name} point {index} must be [x, y]")
        x = _coordinate(point[0], f"{name}.points[{index}].x")
        y = _coordinate(point[1], f"{name}.points[{index}].y")
        if not (0 <= x <= size["width"] and 0 <= y <= size["height"]):
            raise TaskOperationError(f"polygon {name} point {index} is out of image bounds")
        points.append([x, y])
    return points


def _polygons(value: object, categories: tuple[str, ...], size: dict) -> list[dict]:
    if not isinstance(value, list):
        raise TaskOperationError("polygon result requires a `polygons` list")
    normalized = []
    for index, polygon in enumerate(value):
        if not isinstance(polygon, dict) or set(polygon) != POLYGON_FIELDS:
            raise TaskOperationError(f"polygon {index} must have exactly category and points")
        category = polygon["category"]
        if category not in categories:
            raise TaskOperationError(f"unknown polygon category {category!r}")
        normalized.append(
            {"category": category, "points": _points(polygon["points"], size, f"polygon {index}")}
        )
    return normalized


def _category(value: object, categories: tuple[str, ...], kind: str) -> str:
    if value not in categories:
        raise TaskOperationError(f"unknown polygon {kind} category {value!r}")
    return value


def _boxes(value: object, categories: tuple[str, ...], size: dict) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TaskOperationError("polygon result `boxes` must be a list")
    normalized = []
    for index, box in enumerate(value):
        if not isinstance(box, dict) or set(box) != BOX_FIELDS:
            raise TaskOperationError(f"box {index} must have exactly category, x, y, width and height")
        x, y, width, height = (
            _coordinate(box[key], f"box {index}.{key}") for key in ("x", "y", "width", "height")
        )
        if width <= 0 or height <= 0:
            raise TaskOperationError(f"polygon box {index} must have positive size")
        if x < 0 or y < 0 or x + width > size["width"] or y + height > size["height"]:
            raise TaskOperationError(f"polygon box {index} is out of image bounds")
        normalized.append({
            "category": _category(box["category"], categories, "box"),
            "x": x, "y": y, "width": width, "height": height,
        })
    return normalized


def _keypoints(value: object, categories: tuple[str, ...], size: dict) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TaskOperationError("polygon result `points` must be a list")
    normalized = []
    for index, point in enumerate(value):
        if not isinstance(point, dict) or set(point) != POINT_FIELDS:
            raise TaskOperationError(f"point {index} must have exactly category, x and y")
        x = _coordinate(point["x"], f"point {index}.x")
        y = _coordinate(point["y"], f"point {index}.y")
        if not (0 <= x <= size["width"] and 0 <= y <= size["height"]):
            raise TaskOperationError(f"polygon point {index} is out of image bounds")
        normalized.append({"category": _category(point["category"], categories, "point"), "x": x, "y": y})
    return normalized


def _shapes(saved: dict) -> dict:
    """The three shape lists of a stored item (schema 1 items have only polygons)."""
    return {
        "polygons": saved["polygons"],
        "boxes": saved.get("boxes", []),
        "points": saved.get("points", []),
    }


def _revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TaskOperationError(
            "polygon result requires `base_revision`: the revision the edit started from "
            "(0 when nothing was saved yet)"
        )
    return value


# ------------------------------------------------------------ prelabels


class PrelabelFileError(TaskOperationError):
    """The prelabel file as a whole cannot be used (missing, not JSON, not COCO)."""


@dataclass
class Prelabels:
    """What a prelabel file contributes, per dataset item."""

    # item_id -> {"image_size", "polygons", "boxes", "points"} for fully valid images.
    polygons: dict[str, dict] = field(default_factory=dict)
    # item_id -> extra `images[]` fields (anything beyond id/file_name/width/height).
    extras: dict[str, dict] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)


def _issue(reason: str, *, file_name=None, image_id=None, annotation_id=None) -> dict:
    return {
        "file_name": file_name,
        "image_id": image_id,
        "annotation_id": annotation_id,
        "reason": reason,
    }


def _coco_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _coco_size(entry: dict) -> dict | None:
    width, height = entry.get("width"), entry.get("height")
    if isinstance(width, bool) or isinstance(height, bool):
        return None
    if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
        return None
    if not (math.isfinite(width) and math.isfinite(height)):
        return None
    if width <= 0 or height <= 0 or width != int(width) or height != int(height):
        return None
    return {"width": int(width), "height": int(height)}


def _ring(value: object, size: dict) -> list[list[float]]:
    """One COCO polygon ring ``[x1, y1, x2, y2, ...]`` as points; raises with a reason."""
    if not isinstance(value, list):
        raise ValueError("a segmentation ring is not a list of numbers")
    if len(value) % 2:
        raise ValueError("a segmentation ring has an odd number of coordinates")
    if len(value) < 2 * MIN_POINTS:
        raise ValueError(f"a segmentation ring has fewer than {MIN_POINTS} points")
    points = []
    for x, y in zip(value[::2], value[1::2]):
        for coordinate in (x, y):
            if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                raise ValueError("a segmentation coordinate is not a number")
            if not math.isfinite(coordinate):
                raise ValueError("a segmentation coordinate is not finite")
        if not (0 <= x <= size["width"] and 0 <= y <= size["height"]):
            raise ValueError("a segmentation point is outside the image width/height")
        points.append([float(x), float(y)])
    return points


def _number(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{what} is not a finite number")
    return float(value)


def _annotation_shapes(entry: dict, size: dict, category_names: dict[int, str],
                       categories: tuple[str, ...]) -> tuple[str, list[dict]]:
    """``(kind, shapes)`` of one valid COCO annotation; raises ValueError with a reason.

    An annotation with ``keypoints`` is one keypoint, one with polygon rings is
    one polygon per ring, and one with only a ``bbox`` (``segmentation``
    missing or empty) is a box -- the three shapes ``coco_document`` writes.
    """
    category_id = _coco_int(entry.get("category_id"))
    if category_id is None or category_id not in category_names:
        raise ValueError(f"category_id {entry.get('category_id')!r} is not in `categories`")
    name = category_names[category_id]
    if name not in categories:
        raise ValueError(f"category {name!r} is not configured for this project")
    iscrowd = entry.get("iscrowd", 0)
    if iscrowd != 0 or isinstance(iscrowd, bool):
        raise ValueError(f"iscrowd must be 0 (got {iscrowd!r}); crowd/RLE regions are not supported")
    if "keypoints" in entry:
        keypoints = entry["keypoints"]
        if not isinstance(keypoints, list) or len(keypoints) != 3:
            raise ValueError("only single-point keypoint annotations ([x, y, v]) are supported")
        x, y = (_number(value, "a keypoint coordinate") for value in keypoints[:2])
        if keypoints[2] not in (1, 2) or isinstance(keypoints[2], bool):
            raise ValueError("a keypoint must be labelled (visibility 1 or 2)")
        if not (0 <= x <= size["width"] and 0 <= y <= size["height"]):
            raise ValueError("a keypoint is outside the image width/height")
        return "points", [{"category": name, "x": x, "y": y}]
    segmentation = entry.get("segmentation")
    if isinstance(segmentation, dict):
        raise ValueError("RLE segmentation is not supported; use polygon rings")
    if segmentation is None or segmentation == []:
        bbox = entry.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError("an annotation without segmentation needs a bbox [x, y, w, h]")
        x, y, width, height = (_number(value, "a bbox value") for value in bbox)
        if width <= 0 or height <= 0:
            raise ValueError("a bbox must have a positive size")
        if x < 0 or y < 0 or x + width > size["width"] or y + height > size["height"]:
            raise ValueError("a bbox is outside the image width/height")
        return "boxes", [{"category": name, "x": x, "y": y, "width": width, "height": height}]
    if not isinstance(segmentation, list):
        raise ValueError("segmentation must be a list of polygon rings")
    return "polygons", [{"category": name, "points": _ring(ring, size)} for ring in segmentation]


def _read_coco(path: Path) -> dict:
    try:
        document = json.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError as error:
        raise PrelabelFileError(f"polygon prelabels file not found: {path}") from error
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise PrelabelFileError(f"polygon prelabels {path} is not valid UTF-8 JSON: {error}") from error
    if not isinstance(document, dict) or not all(
        isinstance(document.get(key), list) for key in ("images", "annotations", "categories")
    ):
        raise PrelabelFileError(
            f"polygon prelabels {path} is not a COCO document: `images`, `annotations` "
            "and `categories` must be lists"
        )
    return document


def _category_names(document: dict, path: Path) -> dict[int, str]:
    names: dict[int, str] = {}
    for entry in document["categories"]:
        category_id = _coco_int(entry.get("id")) if isinstance(entry, dict) else None
        name = entry.get("name") if isinstance(entry, dict) else None
        if category_id is None or not isinstance(name, str):
            raise PrelabelFileError(
                f"polygon prelabels {path}: every `categories` entry needs an integer id and a name"
            )
        if category_id in names or name in names.values():
            raise PrelabelFileError(
                f"polygon prelabels {path}: category {category_id} / {name!r} is listed twice"
            )
        names[category_id] = name
    return names


def parse_prelabels(
    path: Path, images: dict[str, str], categories: tuple[str, ...]
) -> Prelabels:
    """Match a COCO polygon file to dataset images (``relative path -> item_id``)."""
    document = _read_coco(path)
    category_names = _category_names(document, path)
    result = Prelabels()

    # Image entries: id -> (item_id, file_name, size), or a reason it is unusable.
    by_id: dict[int, tuple[str, str, dict]] = {}
    unusable: dict[int, str] = {}
    claimed: dict[str, int] = {}
    entries: dict[int, dict] = {}
    for entry in document["images"]:
        image_id = _coco_int(entry.get("id")) if isinstance(entry, dict) else None
        file_name = entry.get("file_name") if isinstance(entry, dict) else None
        if image_id is None:
            result.issues.append(_issue(
                "image entry has no integer id", file_name=file_name if isinstance(file_name, str) else None))
            continue
        if image_id in entries:
            unusable[image_id] = "image id is used by more than one `images` entry"
            by_id.pop(image_id, None)
            continue
        entries[image_id] = entry
        if not isinstance(file_name, str):
            unusable[image_id] = "image entry has no file_name"
            continue
        if file_name not in images:
            unusable[image_id] = f"file_name {file_name!r} does not match any dataset image"
            continue
        if file_name in claimed:
            unusable[image_id] = f"file_name {file_name!r} appears in more than one `images` entry"
            other = claimed[file_name]
            if other in by_id:
                del by_id[other]
                unusable[other] = unusable[image_id]
            continue
        claimed[file_name] = image_id
        size = _coco_size(entry)
        if size is None:
            unusable[image_id] = "image entry needs positive integer width and height"
            continue
        by_id[image_id] = (images[file_name], file_name, size)

    loaded: dict[int, dict[str, list[dict]]] = {
        image_id: {"polygons": [], "boxes": [], "points": []} for image_id in by_id
    }
    rejected: dict[int, int] = {}
    skipped: dict[int, int] = {}
    for entry in document["annotations"]:
        annotation_id = _coco_int(entry.get("id")) if isinstance(entry, dict) else None
        image_id = _coco_int(entry.get("image_id")) if isinstance(entry, dict) else None
        if image_id is None or (image_id not in by_id and image_id not in unusable):
            result.issues.append(_issue(
                "annotation does not refer to an `images` entry",
                image_id=image_id, annotation_id=annotation_id))
            continue
        if image_id in unusable:
            skipped[image_id] = skipped.get(image_id, 0) + 1
            continue
        item_id, file_name, size = by_id[image_id]
        try:
            kind, shapes = _annotation_shapes(entry, size, category_names, categories)
            loaded[image_id][kind].extend(shapes)
        except ValueError as error:
            rejected[image_id] = rejected.get(image_id, 0) + 1
            result.issues.append(_issue(
                str(error), file_name=file_name, image_id=image_id, annotation_id=annotation_id))

    for image_id, reason in unusable.items():
        file_name = entries[image_id].get("file_name")
        result.issues.append(_issue(
            f"{reason}; its {skipped.get(image_id, 0)} annotation(s) were not loaded",
            image_id=image_id, file_name=file_name if isinstance(file_name, str) else None))
    for image_id, (item_id, file_name, size) in by_id.items():
        result.extras[item_id] = {
            key: value for key, value in entries[image_id].items() if key not in COCO_IMAGE_FIELDS
        }
        if image_id in rejected:
            result.issues.append(_issue(
                f"prelabels of this image were not loaded because {rejected[image_id]} "
                "annotation(s) are invalid; it starts blank",
                file_name=file_name, image_id=image_id))
            continue
        result.polygons[item_id] = {"image_size": size, **loaded[image_id]}
    return result


# ---------------------------------------------------------------- store


class PolygonStore(ImageTaskStore):
    task = "polygon"
    done_key = "annotated"
    sidecar_schemas = (1, SIDECAR_SCHEMA)

    def __init__(self, project) -> None:
        super().__init__(project)
        self._prelabels: Prelabels | None = None

    def excluded(self) -> tuple[Path, ...]:
        # The prelabel file is never a source image, whatever `patterns` says.
        return () if self.project.prelabels is None else (self.project.prelabels,)

    def prelabels(self) -> Prelabels:
        if self._prelabels is None:
            if self.project.prelabels is None:
                self._prelabels = Prelabels()
            else:
                images = {path: key for key, path in self.images()}
                self._prelabels = parse_prelabels(
                    self.project.prelabels, images, self.project.categories
                )
        return self._prelabels

    def valid_item(self, saved: dict) -> bool:
        revision = saved.get("revision")

        def shapes(key: str, fields: frozenset, required: bool) -> bool:
            value = saved.get(key, None if required else [])
            return isinstance(value, list) and all(
                isinstance(shape, dict) and set(shape) == fields for shape in value
            )

        return (
            is_image_size(saved.get("image_size"))
            and isinstance(revision, int)
            and revision >= 1
            and shapes("polygons", POLYGON_FIELDS, True)
            and shapes("boxes", BOX_FIELDS, False)
            and shapes("points", POINT_FIELDS, False)
        )

    def item_view(self, item_id: str, image_path: str, saved: dict | None) -> dict:
        prelabel = self.prelabels().polygons.get(item_id)
        if saved is not None:
            source, size, shapes = "annotation", saved["image_size"], _shapes(saved)
        elif prelabel is not None:
            source, size, shapes = "prelabel", prelabel["image_size"], _shapes(prelabel)
        else:
            source, size, shapes = "none", None, {"polygons": [], "boxes": [], "points": []}
        return {
            "item_id": item_id,
            "image_path": image_path,
            "revision": 0 if saved is None else saved["revision"],
            "source": source,
            "image_size": size,
            **shapes,
        }

    @staticmethod
    def upgrade(state: dict) -> dict:
        """Rewrite a schema 1 document as schema 2 (every item gets all three lists)."""
        if state["schema"] == SIDECAR_SCHEMA:
            return state
        return {
            **state,
            "schema": SIDECAR_SCHEMA,
            "items": {
                item_id: {**saved, **_shapes(saved)} for item_id, saved in state["items"].items()
            },
        }

    def status_fields(self) -> dict:
        return {
            "annotations": str(self.project.annotations),
            "categories": list(self.project.categories),
            "prelabels": None if self.project.prelabels is None else str(self.project.prelabels),
        }

    def queue(self, request: QueueRequest) -> QueuePage:
        self.prelabels()  # a broken prelabel file fails the queue, not a page later
        return super().queue(request)

    def status(self) -> TaskStatus:
        base = super().status()
        details = dict(base.details)
        try:
            prelabels = self.prelabels()
        except PrelabelFileError as error:
            details["prelabel_error"] = str(error)
            state = base.state if base.state == STATUS_MISSING else STATUS_INVALID
            return TaskStatus(state, details)
        if self.project.prelabels is not None:
            details["prelabel_images"] = len(prelabels.polygons)
            details["prelabel_issue_count"] = len(prelabels.issues)
            details["prelabel_issues"] = prelabels.issues[:MAX_REPORTED_ISSUES]
        return TaskStatus(base.state, details)

    def submit(self, submission: Submission) -> dict:
        result = submission.result
        base_revision = _revision(result.get("base_revision"))
        size = image_size(result.get("image_size"), "polygon")
        shapes = {
            "polygons": _polygons(result.get("polygons"), self.project.categories, size),
            "boxes": _boxes(result.get("boxes"), self.project.categories, size),
            "points": _keypoints(result.get("points"), self.project.categories, size),
        }
        image_path = self.image_path(submission.item_id)
        # Loaded prelabels pin the coordinate frame their polygons were made in.
        prelabel = self.prelabels().polygons.get(submission.item_id)
        expected = None if prelabel is None else prelabel["image_size"]
        with self.lock:
            state = self.upgrade(self._read())
            current = state["items"].get(submission.item_id)
            if current is not None:
                expected = current["image_size"]
            if expected is not None and expected != size:
                raise TaskOperationError(
                    f"polygon image_size {size} does not match the image's known size {expected}"
                )
            current_revision = 0 if current is None else current["revision"]
            if current is not None and _shapes(current) == shapes:
                return {"item_id": submission.item_id, **current}
            if base_revision != current_revision:
                raise TaskConflictError(
                    f"polygon item {submission.item_id!r} is at revision {current_revision}, "
                    f"but this edit started from revision {base_revision}; reload it and edit again"
                )
            saved = {
                "image_path": image_path,
                "image_size": size,
                **shapes,
                "revision": current_revision + 1,
            }
            state["items"][submission.item_id] = saved
            state["history"].append(
                {
                    "item_id": submission.item_id,
                    **saved,
                    "base_revision": base_revision,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            try:
                atomic_write_json(self.project.annotations, state)
            except OSError as error:
                raise TaskOperationError(f"cannot persist polygon result: {error}") from error
            return {"item_id": submission.item_id, **saved}

    def export(self, request: ExportRequest) -> ExportResult:
        with self.lock:
            state = self._read()
            if request.format in {"native", "json"}:
                if state["schema"] != SIDECAR_SCHEMA and self.sidecar.is_file():
                    # The download is always the current contract: upgrade in place.
                    state = self.upgrade(state)
                    try:
                        atomic_write_json(self.project.annotations, state)
                    except OSError as error:
                        raise TaskOperationError(f"cannot upgrade polygon JSON: {error}") from error
                path = self.native_export(self.upgrade(state), atomic_write_json)
                return ExportResult("polygon-json", (path,))
            if request.format != "coco":
                raise TaskOperationError(
                    f"polygon does not support export format {request.format!r}"
                )
            document = self.coco_document(state)
            output = self.project.annotations.with_suffix(".coco.json")
            try:
                atomic_write_json(output, document)
            except OSError as error:
                raise TaskOperationError(f"cannot export polygon COCO json: {error}") from error
            return ExportResult(
                "polygon-coco",
                (output,),
                {"images": len(document["images"]), "annotations": len(document["annotations"])},
            )

    def coco_document(self, state: dict) -> dict:
        """Standard COCO, ids in a deterministic order.

        Per image: one annotation per polygon (segmentation ring), then per box
        (``segmentation: []``), then per keypoint (``keypoints: [x, y, 2]``,
        every category declaring the single keypoint ``"point"``).
        """
        extras = self.prelabels().extras if self.project.prelabels is not None else {}
        category_ids = {name: index + 1 for index, name in enumerate(self.project.categories)}
        images, annotations = [], []
        ordered = sorted(state["items"].items(), key=lambda item: item[1]["image_path"])

        def add(image_id: int, category: str, **fields) -> None:
            annotations.append({
                "id": len(annotations) + 1,
                "image_id": image_id,
                "category_id": category_ids[category],
                **fields,
                "iscrowd": 0,
            })

        for image_id, (item_id, saved) in enumerate(ordered, start=1):
            images.append(
                {
                    **extras.get(item_id, {}),
                    "id": image_id,
                    "file_name": saved["image_path"],
                    "width": saved["image_size"]["width"],
                    "height": saved["image_size"]["height"],
                }
            )
            shapes = _shapes(saved)
            for polygon in shapes["polygons"]:
                points = polygon["points"]
                add(image_id, polygon["category"],
                    segmentation=[[coordinate for point in points for coordinate in point]],
                    area=shoelace_area(points), bbox=bounding_box(points))
            for box in shapes["boxes"]:
                add(image_id, box["category"], segmentation=[],
                    area=box["width"] * box["height"],
                    bbox=[box["x"], box["y"], box["width"], box["height"]])
            for point in shapes["points"]:
                add(image_id, point["category"], segmentation=[], area=0.0,
                    bbox=[point["x"], point["y"], 0.0, 0.0],
                    keypoints=[point["x"], point["y"], 2], num_keypoints=1)
        return {
            "info": coco_info("polygon-coco", COCO_VERSION),
            "images": images,
            "annotations": annotations,
            "categories": [
                {"id": category_ids[name], "name": name, "keypoints": ["point"], "skeleton": []}
                for name in self.project.categories
            ],
        }


class PolygonTaskType(ImageTaskType):
    type_name = "polygon"
    project_type = PolygonProject
    store_type = PolygonStore
    config_loader = staticmethod(load_config)
