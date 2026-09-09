"""Image segmentation task module: one raster category mask per image.

Polygon drawing is a frontend-only authoring aid (see
frontend/src/components/image-canvas/polygon-tool.ts): the browser
rasterizes polygons into the same pixel buffer a brush would paint, so this
module only ever handles one representation -- a full-image raster where
each byte is a category index (0 = background, 1..N per `categories`
order). The raster is persisted as a real PNG via
`annotation_platform.imaging.encode_gray8_png` so it can be viewed and
consumed outside the platform, alongside a JSON index for queue/status/
conflict bookkeeping.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .imaging import encode_gray8_png
from .local_files import atomic_write_bytes, atomic_write_json, file_lock
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


@dataclass(frozen=True)
class SegmentationProject:
    config_path: Path
    dataset: Path
    categories: tuple[str, ...]
    patterns: tuple[str, ...]
    annotations: Path


def _text_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise TaskOperationError(f"segmentation `{name}` must be a non-empty list")
    values = tuple(item.strip() for item in value if isinstance(item, str))
    if len(values) != len(value) or any(not item for item in values):
        raise TaskOperationError(f"segmentation `{name}` entries must be text")
    if len(set(values)) != len(values):
        raise TaskOperationError(f"segmentation `{name}` entries must be unique")
    return values


def load_config(path: Path) -> SegmentationProject:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise TaskOperationError(f"cannot read segmentation config {path}: {error}") from error
    if not isinstance(raw, dict):
        raise TaskOperationError("segmentation config must be a YAML mapping")
    unknown = sorted(set(raw) - CONFIG_KEYS)
    if unknown:
        raise TaskOperationError(f"unknown segmentation config keys: {unknown}")
    dataset_value = raw.get("dataset")
    if not isinstance(dataset_value, str) or not dataset_value.strip():
        raise TaskOperationError("segmentation `dataset` must be non-empty text")
    dataset_path = Path(dataset_value).expanduser()
    dataset = (
        dataset_path.resolve()
        if dataset_path.is_absolute()
        else (path.parent / dataset_path).resolve()
    )
    categories = _text_list(raw.get("categories"), "categories")
    if len(categories) > 254:
        raise TaskOperationError("segmentation `categories` supports at most 254 entries")
    patterns = _text_list(raw.get("patterns", list(DEFAULT_PATTERNS)), "patterns")
    if any(Path(pattern).is_absolute() or ".." in Path(pattern).parts for pattern in patterns):
        raise TaskOperationError("segmentation `patterns` must stay inside dataset")
    annotation_value = raw.get("annotations", ".annotations/segmentation")
    if not isinstance(annotation_value, str) or not annotation_value.strip():
        raise TaskOperationError("segmentation `annotations` must be non-empty text")
    relative_annotation = Path(annotation_value)
    if relative_annotation.is_absolute():
        raise TaskOperationError("segmentation `annotations` must be dataset-relative")
    annotations = (dataset / relative_annotation).resolve()
    if dataset not in annotations.parents:
        raise TaskOperationError("segmentation `annotations` must stay inside dataset")
    return SegmentationProject(path.resolve(), dataset, categories, patterns, annotations)


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TaskOperationError(f"segmentation `{name}` must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise TaskOperationError(f"segmentation `{name}` must be finite")
    return number


def _image_size(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"width", "height"}:
        raise TaskOperationError("segmentation result requires `image_size` with width/height")
    width = _finite_number(value["width"], "image_size.width")
    height = _finite_number(value["height"], "image_size.height")
    if width <= 0 or height <= 0 or width != int(width) or height != int(height):
        raise TaskOperationError("segmentation `image_size` must be positive whole numbers")
    return {"width": int(width), "height": int(height)}


def _pixels(value: object, image_size: dict, category_count: int) -> bytes:
    if not isinstance(value, str) or not value:
        raise TaskOperationError("segmentation result requires base64 `pixels`")
    try:
        decoded = base64.b64decode(value, validate=True)
    except binascii.Error as error:
        raise TaskOperationError(f"segmentation `pixels` is not valid base64: {error}") from error
    expected = image_size["width"] * image_size["height"]
    if len(decoded) != expected:
        raise TaskOperationError(
            f"segmentation `pixels` must contain {expected} bytes, got {len(decoded)}"
        )
    if decoded and max(decoded) > category_count:
        raise TaskOperationError("segmentation `pixels` reference an unknown category index")
    return decoded


class SegmentationStore:
    def __init__(self, project: SegmentationProject):
        self.project = project
        self.index_path = project.annotations / "index.json"
        self.lock = file_lock(self.index_path)

    def images(self) -> tuple[tuple[str, str], ...]:
        root = self.project.dataset
        if not root.is_dir():
            return ()
        annotations = self.project.annotations
        found: dict[str, str] = {}
        for pattern in self.project.patterns:
            for path in root.glob(pattern):
                resolved = path.resolve()
                if root not in resolved.parents or not resolved.is_file():
                    continue
                if resolved == annotations or annotations in resolved.parents:
                    continue
                relative = path.relative_to(root).as_posix()
                item_id = "i" + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
                found[item_id] = relative
        return tuple(sorted(found.items(), key=lambda item: item[1]))

    def _read(self) -> dict:
        if not self.index_path.is_file():
            return {"schema": 1, "items": {}, "history": []}
        try:
            value = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise TaskOperationError(f"cannot read segmentation index: {error}") from error
        if (
            not isinstance(value, dict)
            or value.get("schema") != 1
            or not isinstance(value.get("items"), dict)
            or not isinstance(value.get("history"), list)
        ):
            raise TaskOperationError("invalid segmentation index document")
        return value

    def queue(self, request: QueueRequest) -> QueuePage:
        unsupported = sorted(set(request.filters) - {"status", "q"})
        if unsupported:
            raise TaskOperationError(f"unsupported segmentation filters: {unsupported}")
        with self.lock:
            state = self._read()
            query = request.filters.get("q", "").lower()
            status = request.filters.get("status", "")
            selected = []
            for item_id, image_path in self.images():
                saved = state["items"].get(item_id)
                if status == "pending" and saved is not None:
                    continue
                if status in {"segmented", "labelled", "labeled"} and saved is None:
                    continue
                if query and query not in image_path.lower():
                    continue
                selected.append(
                    {
                        "item_id": item_id,
                        "image_path": image_path,
                        "mask_path": None if saved is None else saved["mask_path"],
                        "image_size": None if saved is None else saved["image_size"],
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
        pixels = _pixels(submission.result.get("pixels"), image_size, len(self.project.categories))
        images = dict(self.images())
        image_path = images.get(submission.item_id)
        if image_path is None:
            raise TaskOperationError(f"unknown segmentation item {submission.item_id!r}")
        pixel_hash = hashlib.sha256(pixels).hexdigest()
        with self.lock:
            state = self._read()
            current = state["items"].get(submission.item_id)
            mask_name = f"{submission.item_id}.png"
            mask_path = self.project.annotations / mask_name
            if current is not None:
                if current.get("pixel_hash") == pixel_hash and current.get("image_size") == image_size:
                    return {"item_id": submission.item_id, **current}
                raise TaskConflictError(
                    f"segmentation item {submission.item_id!r} is already segmented"
                )
            png = encode_gray8_png(image_size["width"], image_size["height"], pixels)
            saved = {
                "image_path": image_path,
                "image_size": image_size,
                "mask_path": mask_name,
                "pixel_hash": pixel_hash,
            }
            try:
                atomic_write_bytes(mask_path, png)
                state["items"][submission.item_id] = saved
                state["history"].append(
                    {
                        "item_id": submission.item_id,
                        "image_path": image_path,
                        "image_size": image_size,
                        "mask_path": mask_name,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                atomic_write_json(self.index_path, state)
            except OSError as error:
                raise TaskOperationError(f"cannot persist segmentation result: {error}") from error
            return {"item_id": submission.item_id, **saved}

    def status(self) -> TaskStatus:
        with self.lock:
            state = self._read()
            images = self.images()
            current_ids = {item_id for item_id, _ in images}
            segmented = sum(item_id in state["items"] for item_id in current_ids)
        total = len(images)
        if not self.project.dataset.exists():
            status = "missing"
        elif not total:
            status = "empty"
        else:
            status = "reviewed" if segmented == total else "reviewing"
        return TaskStatus(
            status,
            {
                "config": str(self.project.config_path),
                "dataset": str(self.project.dataset),
                "annotations": str(self.project.annotations),
                "categories": list(self.project.categories),
                "total": total,
                "segmented": segmented,
                "pending": total - segmented,
            },
        )

    def export(self, request: ExportRequest) -> ExportResult:
        with self.lock:
            state = self._read()
            if request.format in {"native", "json"}:
                try:
                    if not self.index_path.is_file():
                        atomic_write_json(self.index_path, state)
                except OSError as error:
                    raise TaskOperationError(
                        f"cannot export segmentation index: {error}"
                    ) from error
                masks = tuple(
                    self.project.annotations / saved["mask_path"]
                    for saved in state["items"].values()
                )
                return ExportResult("segmentation-masks", (self.index_path, *masks))
            if request.format != "coco":
                raise TaskOperationError(
                    f"segmentation does not support export format {request.format!r}"
                )
            output = self.project.annotations / "coco.json"
            document = self._coco_document(state)
            try:
                atomic_write_json(output, document)
            except OSError as error:
                raise TaskOperationError(
                    f"cannot export segmentation COCO json: {error}"
                ) from error
            return ExportResult(
                "segmentation-coco",
                (output,),
                {"images": len(document["images"])},
            )

    def _coco_document(self, state: dict) -> dict:
        category_ids = {name: index + 1 for index, name in enumerate(self.project.categories)}
        categories = [{"id": category_ids[name], "name": name} for name in self.project.categories]
        images = []
        annotations = []
        for image_id, (item_id, saved) in enumerate(sorted(state["items"].items()), start=1):
            images.append(
                {
                    "id": image_id,
                    "file_name": saved["image_path"],
                    "width": saved["image_size"]["width"],
                    "height": saved["image_size"]["height"],
                }
            )
            annotations.append(
                {
                    "image_id": image_id,
                    # Mask-referencing "compatible" segmentation, not full
                    # RLE/polygon COCO -- see docs/segmentation.md.
                    "segmentation_mask": saved["mask_path"],
                }
            )
        return {"images": images, "categories": categories, "annotations": annotations}


class SegmentationTaskType:
    type_name = "segmentation"

    @staticmethod
    def _project(project: TaskProject) -> SegmentationProject:
        if not isinstance(project.value, SegmentationProject):
            raise TaskOperationError("segmentation requires its own project configuration")
        return project.value

    def load(self, config_path: Path) -> TaskProject:
        project = load_config(config_path)
        if project.dataset.exists() and not project.dataset.is_dir():
            raise TaskOperationError(f"dataset root is not a directory: {project.dataset}")
        return TaskProject(project.config_path, project.dataset, project)

    def queue(self, project: TaskProject, request: QueueRequest) -> QueuePage:
        return SegmentationStore(self._project(project)).queue(request)

    def submit(self, project: TaskProject, submission: Submission) -> SubmissionResult:
        store = SegmentationStore(self._project(project))
        item = store.submit(submission)
        return SubmissionResult(item, store.status())

    def status(self, project: TaskProject) -> TaskStatus:
        return SegmentationStore(self._project(project)).status()

    def export(self, project: TaskProject, request: ExportRequest) -> ExportResult:
        return SegmentationStore(self._project(project)).export(request)
