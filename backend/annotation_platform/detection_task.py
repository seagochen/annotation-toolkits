"""Image-level bounding-box object detection task module."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .local_files import atomic_write_json, file_lock
from .task_types import (
    ExportRequest,
    ExportResult,
    QueuePage,
    QueueRequest,
    Submission,
    SubmissionResult,
    TaskConflictError,
    TaskOperationError,
    TaskProject,
    TaskStatus,
)

CONFIG_KEYS = frozenset({"dataset", "categories", "patterns", "annotations"})
DEFAULT_PATTERNS = ("**/*.jpg", "**/*.jpeg", "**/*.png", "**/*.webp")
BOX_FIELDS = ("category", "x", "y", "width", "height")


@dataclass(frozen=True)
class DetectionProject:
    config_path: Path
    dataset: Path
    categories: tuple[str, ...]
    patterns: tuple[str, ...]
    annotations: Path


def _text_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise TaskOperationError(f"detection `{name}` must be a non-empty list")
    values = tuple(item.strip() for item in value if isinstance(item, str))
    if len(values) != len(value) or any(not item for item in values):
        raise TaskOperationError(f"detection `{name}` entries must be text")
    if len(set(values)) != len(values):
        raise TaskOperationError(f"detection `{name}` entries must be unique")
    return values


def load_config(path: Path) -> DetectionProject:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise TaskOperationError(f"cannot read detection config {path}: {error}") from error
    if not isinstance(raw, dict):
        raise TaskOperationError("detection config must be a YAML mapping")
    unknown = sorted(set(raw) - CONFIG_KEYS)
    if unknown:
        raise TaskOperationError(f"unknown detection config keys: {unknown}")
    dataset_value = raw.get("dataset")
    if not isinstance(dataset_value, str) or not dataset_value.strip():
        raise TaskOperationError("detection `dataset` must be non-empty text")
    dataset_path = Path(dataset_value).expanduser()
    dataset = (
        dataset_path.resolve()
        if dataset_path.is_absolute()
        else (path.parent / dataset_path).resolve()
    )
    categories = _text_list(raw.get("categories"), "categories")
    patterns = _text_list(raw.get("patterns", list(DEFAULT_PATTERNS)), "patterns")
    if any(Path(pattern).is_absolute() or ".." in Path(pattern).parts for pattern in patterns):
        raise TaskOperationError("detection `patterns` must stay inside dataset")
    annotation_value = raw.get("annotations", ".annotations/detection.json")
    if not isinstance(annotation_value, str) or not annotation_value.strip():
        raise TaskOperationError("detection `annotations` must be non-empty text")
    relative_annotation = Path(annotation_value)
    if relative_annotation.is_absolute():
        raise TaskOperationError("detection `annotations` must be dataset-relative")
    annotations = (dataset / relative_annotation).resolve()
    if dataset not in annotations.parents:
        raise TaskOperationError("detection `annotations` must stay inside dataset")
    return DetectionProject(path.resolve(), dataset, categories, patterns, annotations)


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TaskOperationError(f"detection `{name}` must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise TaskOperationError(f"detection `{name}` must be finite")
    return number


def _image_size(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"width", "height"}:
        raise TaskOperationError("detection result requires `image_size` with width/height")
    width = _finite_number(value["width"], "image_size.width")
    height = _finite_number(value["height"], "image_size.height")
    if width <= 0 or height <= 0:
        raise TaskOperationError("detection `image_size` must be positive")
    return {"width": width, "height": height}


def _boxes(value: object, categories: tuple[str, ...], image_size: dict) -> list[dict]:
    if not isinstance(value, list):
        raise TaskOperationError("detection result requires a `boxes` list")
    normalized = []
    for index, box in enumerate(value):
        if not isinstance(box, dict) or set(box) != set(BOX_FIELDS):
            raise TaskOperationError(f"detection box {index} has invalid fields")
        category = box["category"]
        if category not in categories:
            raise TaskOperationError(f"unknown detection category {category!r}")
        x = _finite_number(box["x"], f"box[{index}].x")
        y = _finite_number(box["y"], f"box[{index}].y")
        width = _finite_number(box["width"], f"box[{index}].width")
        height = _finite_number(box["height"], f"box[{index}].height")
        if width <= 0 or height <= 0:
            raise TaskOperationError(f"detection box {index} must have positive size")
        if x < 0 or y < 0 or x + width > image_size["width"] or y + height > image_size["height"]:
            raise TaskOperationError(f"detection box {index} is out of image bounds")
        normalized.append(
            {"category": category, "x": x, "y": y, "width": width, "height": height}
        )
    normalized.sort(key=lambda box: tuple(box[field] if field != "category" else box[field] for field in BOX_FIELDS))
    return normalized


class DetectionStore:
    def __init__(self, project: DetectionProject):
        self.project = project
        self.lock = file_lock(project.annotations)

    def images(self) -> tuple[tuple[str, str], ...]:
        root = self.project.dataset
        if not root.is_dir():
            return ()
        found: dict[str, str] = {}
        for pattern in self.project.patterns:
            for path in root.glob(pattern):
                resolved = path.resolve()
                if root not in resolved.parents or not resolved.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                item_id = "i" + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
                found[item_id] = relative
        return tuple(sorted(found.items(), key=lambda item: item[1]))

    def _read(self) -> dict:
        path = self.project.annotations
        if not path.is_file():
            return {"schema": 1, "items": {}, "history": []}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise TaskOperationError(f"cannot read detection annotations: {error}") from error
        if (
            not isinstance(value, dict)
            or value.get("schema") != 1
            or not isinstance(value.get("items"), dict)
            or not isinstance(value.get("history"), list)
        ):
            raise TaskOperationError("invalid detection annotation document")
        return value

    def queue(self, request: QueueRequest) -> QueuePage:
        unsupported = sorted(set(request.filters) - {"status", "q"})
        if unsupported:
            raise TaskOperationError(f"unsupported detection filters: {unsupported}")
        with self.lock:
            state = self._read()
            query = request.filters.get("q", "").lower()
            status = request.filters.get("status", "")
            selected = []
            for item_id, image_path in self.images():
                saved = state["items"].get(item_id)
                if status == "pending" and saved is not None:
                    continue
                if status in {"annotated", "labelled", "labeled"} and saved is None:
                    continue
                if query and query not in image_path.lower():
                    continue
                selected.append(
                    {
                        "item_id": item_id,
                        "image_path": image_path,
                        "boxes": [] if saved is None else saved["boxes"],
                    }
                )
            page = selected[request.offset : request.offset + request.limit]
            return QueuePage(
                total=len(selected),
                offset=request.offset,
                limit=request.limit,
                items=tuple(page),
            )

    def submit(self, submission: Submission) -> dict:
        image_size = _image_size(submission.result.get("image_size"))
        boxes = _boxes(submission.result.get("boxes"), self.project.categories, image_size)
        images = dict(self.images())
        image_path = images.get(submission.item_id)
        if image_path is None:
            raise TaskOperationError(f"unknown detection item {submission.item_id!r}")
        with self.lock:
            state = self._read()
            current = state["items"].get(submission.item_id)
            if current is not None:
                if current.get("image_size") == image_size and current.get("boxes") == boxes:
                    return {"item_id": submission.item_id, **current}
                raise TaskConflictError(
                    f"detection item {submission.item_id!r} is already annotated"
                )
            saved = {"image_path": image_path, "image_size": image_size, "boxes": boxes}
            state["items"][submission.item_id] = saved
            state["history"].append(
                {
                    "item_id": submission.item_id,
                    **saved,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            try:
                atomic_write_json(self.project.annotations, state)
            except OSError as error:
                raise TaskOperationError(f"cannot persist detection result: {error}") from error
            return {"item_id": submission.item_id, **saved}

    def status(self) -> TaskStatus:
        with self.lock:
            state = self._read()
            images = self.images()
            current_ids = {item_id for item_id, _ in images}
            annotated = sum(item_id in state["items"] for item_id in current_ids)
        total = len(images)
        if not self.project.dataset.exists():
            status = "missing"
        elif not total:
            status = "empty"
        else:
            status = "reviewed" if annotated == total else "reviewing"
        return TaskStatus(
            status,
            {
                "config": str(self.project.config_path),
                "dataset": str(self.project.dataset),
                "annotations": str(self.project.annotations),
                "categories": list(self.project.categories),
                "total": total,
                "annotated": annotated,
                "pending": total - annotated,
            },
        )

    def export(self, request: ExportRequest) -> ExportResult:
        with self.lock:
            state = self._read()
            if request.format in {"native", "json"}:
                try:
                    if not self.project.annotations.is_file():
                        atomic_write_json(self.project.annotations, state)
                except OSError as error:
                    raise TaskOperationError(f"cannot export detection JSON: {error}") from error
                return ExportResult("detection-json", (self.project.annotations,))
            if request.format != "coco":
                raise TaskOperationError(
                    f"detection does not support export format {request.format!r}"
                )
            output = self.project.annotations.with_name("coco.json")
            document = self._coco_document(state)
            try:
                atomic_write_json(output, document)
            except OSError as error:
                raise TaskOperationError(f"cannot export detection COCO json: {error}") from error
            return ExportResult(
                "detection-coco",
                (output,),
                {"images": len(document["images"]), "annotations": len(document["annotations"])},
            )

    def _coco_document(self, state: dict) -> dict:
        category_ids = {name: index + 1 for index, name in enumerate(self.project.categories)}
        categories = [{"id": category_ids[name], "name": name} for name in self.project.categories]
        images = []
        annotations = []
        annotation_id = 1
        for image_id, (item_id, saved) in enumerate(sorted(state["items"].items()), start=1):
            images.append(
                {
                    "id": image_id,
                    "file_name": saved["image_path"],
                    "width": saved["image_size"]["width"],
                    "height": saved["image_size"]["height"],
                }
            )
            for box in saved["boxes"]:
                annotations.append(
                    {
                        "id": annotation_id,
                        "image_id": image_id,
                        "category_id": category_ids[box["category"]],
                        "bbox": [box["x"], box["y"], box["width"], box["height"]],
                        "area": box["width"] * box["height"],
                        "iscrowd": 0,
                    }
                )
                annotation_id += 1
        return {"images": images, "categories": categories, "annotations": annotations}


class DetectionTaskType:
    type_name = "detection"

    @staticmethod
    def _project(project: TaskProject) -> DetectionProject:
        if not isinstance(project.value, DetectionProject):
            raise TaskOperationError("detection requires its own project configuration")
        return project.value

    def load(self, config_path: Path) -> TaskProject:
        project = load_config(config_path)
        if project.dataset.exists() and not project.dataset.is_dir():
            raise TaskOperationError(f"dataset root is not a directory: {project.dataset}")
        return TaskProject(project.config_path, project.dataset, project)

    def queue(self, project: TaskProject, request: QueueRequest) -> QueuePage:
        return DetectionStore(self._project(project)).queue(request)

    def submit(self, project: TaskProject, submission: Submission) -> SubmissionResult:
        store = DetectionStore(self._project(project))
        item = store.submit(submission)
        return SubmissionResult(item, store.status())

    def status(self, project: TaskProject) -> TaskStatus:
        return DetectionStore(self._project(project)).status()

    def export(self, project: TaskProject, request: ExportRequest) -> ExportResult:
        return DetectionStore(self._project(project)).export(request)
