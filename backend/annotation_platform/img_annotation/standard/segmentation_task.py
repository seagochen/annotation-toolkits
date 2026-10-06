"""Image segmentation task module: one raster category mask per image.

Polygon drawing is a frontend-only authoring aid (see
frontend/src/img-annotation/common/image-canvas/polygon-tool.ts): the browser
rasterizes polygons into the same pixel buffer a brush would paint, so this
module only ever handles one representation -- a full-image raster where
each byte is a category index (0 = background, 1..N per `categories`
order). The raster is persisted as a real PNG via
`annotation_platform.img_annotation.common.imaging.encode_gray8_png` so it can be viewed and
consumed outside the platform, alongside a JSON index for queue/status/
conflict bookkeeping.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from local_files import atomic_write_bytes, atomic_write_json

from ...export_contracts import coco_info
from ..common.image_dataset import (
    ImageTaskStore,
    ImageTaskType,
    config_patterns,
    dataset_relative,
    image_size,
    is_image_size,
    pixels,
    read_config,
    text_list,
)
from ..common.imaging import encode_gray8_png
from ...task_types import (
    ExportRequest,
    ExportResult,
    Submission,
    TaskConflictError,
    TaskOperationError,
)

CONFIG_KEYS = frozenset({"dataset", "categories", "patterns", "annotations"})


@dataclass(frozen=True)
class SegmentationProject:
    config_path: Path
    dataset: Path
    categories: tuple[str, ...]
    patterns: tuple[str, ...]
    annotations: Path


def load_config(path: Path) -> SegmentationProject:
    raw, dataset = read_config(path, "segmentation", CONFIG_KEYS)
    categories = text_list(raw.get("categories"), "segmentation", "categories")
    if len(categories) > 254:
        raise TaskOperationError("segmentation `categories` supports at most 254 entries")
    patterns = config_patterns(raw, "segmentation")
    annotations = dataset_relative(
        dataset, raw.get("annotations"), "segmentation", "annotations",
        ".annotations/segmentation")
    return SegmentationProject(path.resolve(), dataset, categories, patterns, annotations)


def _category_pixels(value: object, size: dict, category_count: int) -> bytes:
    decoded = pixels(value, "segmentation", size)
    if decoded and max(decoded) > category_count:
        raise TaskOperationError("segmentation `pixels` reference an unknown category index")
    return decoded


class SegmentationStore(ImageTaskStore):
    task = "segmentation"
    done_key = "segmented"
    sidecar_label = "index"

    def valid_item(self, saved: dict) -> bool:
        return (
            is_image_size(saved.get("image_size"))
            and isinstance(saved.get("mask_path"), str)
            and isinstance(saved.get("pixel_hash"), str)
        )

    def item_view(self, item_id: str, image_path: str, saved: dict | None) -> dict:
        return {
            "item_id": item_id,
            "image_path": image_path,
            "mask_path": None if saved is None else saved["mask_path"],
            "image_size": None if saved is None else saved["image_size"],
        }

    def status_fields(self) -> dict:
        return {
            "annotations": str(self.project.annotations),
            "categories": list(self.project.categories),
        }

    def submit(self, submission: Submission) -> dict:
        size = image_size(submission.result.get("image_size"), "segmentation")
        raster = _category_pixels(submission.result.get("pixels"), size,
                                  len(self.project.categories))
        image_path = self.image_path(submission.item_id)
        pixel_hash = hashlib.sha256(raster).hexdigest()
        with self.lock:
            state = self._read()
            current = state["items"].get(submission.item_id)
            mask_name = f"{submission.item_id}.png"
            mask_path = self.project.annotations / mask_name
            if current is not None:
                if current.get("pixel_hash") == pixel_hash and current.get("image_size") == size:
                    return {"item_id": submission.item_id, **current}
                raise TaskConflictError(
                    f"segmentation item {submission.item_id!r} is already segmented"
                )
            png = encode_gray8_png(size["width"], size["height"], raster)
            saved = {
                "image_path": image_path,
                "image_size": size,
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
                        "image_size": size,
                        "mask_path": mask_name,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                atomic_write_json(self.sidecar, state)
            except OSError as error:
                raise TaskOperationError(f"cannot persist segmentation result: {error}") from error
            return {"item_id": submission.item_id, **saved}

    def export(self, request: ExportRequest) -> ExportResult:
        with self.lock:
            state = self._read()
            if request.format in {"native", "json"}:
                index = self.native_export(state, atomic_write_json)
                masks = tuple(
                    self.project.annotations / saved["mask_path"]
                    for saved in state["items"].values()
                )
                return ExportResult("segmentation-masks", (index, *masks))
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
                    # RLE/polygon COCO -- see docs/detailed_design/70_外部接口.md (segmentation).
                    "segmentation_mask": saved["mask_path"],
                }
            )
        return {
            "info": coco_info("segmentation-coco"),
            "images": images,
            "categories": categories,
            "annotations": annotations,
        }


class SegmentationTaskType(ImageTaskType):
    type_name = "segmentation"
    project_type = SegmentationProject
    store_type = SegmentationStore
    config_loader = staticmethod(load_config)
