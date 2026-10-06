"""Image-level bounding-box object detection task module."""

from __future__ import annotations

from dataclasses import dataclass
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
    ExportRequest,
    ExportResult,
    Submission,
    TaskConflictError,
    TaskOperationError,
)

CONFIG_KEYS = frozenset({"dataset", "categories", "patterns", "annotations"})
BOX_FIELDS = ("category", "x", "y", "width", "height")


@dataclass(frozen=True)
class DetectionProject:
    config_path: Path
    dataset: Path
    categories: tuple[str, ...]
    patterns: tuple[str, ...]
    annotations: Path


def load_config(path: Path) -> DetectionProject:
    raw, dataset = read_config(path, "detection", CONFIG_KEYS)
    categories = text_list(raw.get("categories"), "detection", "categories")
    patterns = config_patterns(raw, "detection")
    annotations = dataset_relative(
        dataset, raw.get("annotations"), "detection", "annotations", ".annotations/detection.json")
    return DetectionProject(path.resolve(), dataset, categories, patterns, annotations)


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
        x = finite_number(box["x"], "detection", f"box[{index}].x")
        y = finite_number(box["y"], "detection", f"box[{index}].y")
        width = finite_number(box["width"], "detection", f"box[{index}].width")
        height = finite_number(box["height"], "detection", f"box[{index}].height")
        if width <= 0 or height <= 0:
            raise TaskOperationError(f"detection box {index} must have positive size")
        if x < 0 or y < 0 or x + width > image_size["width"] or y + height > image_size["height"]:
            raise TaskOperationError(f"detection box {index} is out of image bounds")
        normalized.append(
            {"category": category, "x": x, "y": y, "width": width, "height": height}
        )
    normalized.sort(key=lambda box: tuple(box[field] if field != "category" else box[field] for field in BOX_FIELDS))
    return normalized


class DetectionStore(ImageTaskStore):
    task = "detection"
    done_key = "annotated"

    def valid_item(self, saved: dict) -> bool:
        boxes = saved.get("boxes")
        return (
            is_image_size(saved.get("image_size"))
            and isinstance(boxes, list)
            and all(isinstance(box, dict) and set(box) == set(BOX_FIELDS) for box in boxes)
        )

    def item_view(self, item_id: str, image_path: str, saved: dict | None) -> dict:
        return {
            "item_id": item_id,
            "image_path": image_path,
            "boxes": [] if saved is None else saved["boxes"],
        }

    def status_fields(self) -> dict:
        return {
            "annotations": str(self.project.annotations),
            "categories": list(self.project.categories),
        }

    def submit(self, submission: Submission) -> dict:
        size = image_size(submission.result.get("image_size"), "detection")
        boxes = _boxes(submission.result.get("boxes"), self.project.categories, size)
        image_path = self.image_path(submission.item_id)
        with self.lock:
            state = self._read()
            current = state["items"].get(submission.item_id)
            if current is not None:
                if current.get("image_size") == size and current.get("boxes") == boxes:
                    return {"item_id": submission.item_id, **current}
                raise TaskConflictError(
                    f"detection item {submission.item_id!r} is already annotated"
                )
            saved = {"image_path": image_path, "image_size": size, "boxes": boxes}
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

    def export(self, request: ExportRequest) -> ExportResult:
        with self.lock:
            state = self._read()
            if request.format in {"native", "json"}:
                path = self.native_export(state, atomic_write_json)
                return ExportResult("detection-json", (path,))
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
        return {
            "info": coco_info("detection-coco"),
            "images": images,
            "categories": categories,
            "annotations": annotations,
        }


class DetectionTaskType(ImageTaskType):
    type_name = "detection"
    project_type = DetectionProject
    store_type = DetectionStore
    config_loader = staticmethod(load_config)
