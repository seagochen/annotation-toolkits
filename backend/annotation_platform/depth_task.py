"""Depth-map brush annotation task module.

Annotators refine a precomputed baseline grayscale depth map by painting
with a radius brush that raises or lowers depth values (see
frontend/src/components/image-canvas/raster-buffer.ts, the same shared
raster primitive #21 segmentation's brush uses). The brush interaction and
multiply-blend preview are entirely client-side; the backend only ever
receives and stores the final full-resolution raster for an item, exactly
like segmentation's mask, reusing `annotation_platform.imaging` to persist
it as a real PNG.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from local_files import atomic_write_bytes, atomic_write_json

from .image_dataset import (
    ImageTaskStore,
    ImageTaskType,
    config_patterns,
    dataset_relative,
    image_size,
    is_image_size,
    pixels,
    read_config,
)
from .imaging import encode_gray8_png
from .task_types import (
    ExportRequest,
    ExportResult,
    Submission,
    TaskConflictError,
    TaskOperationError,
)

CONFIG_KEYS = frozenset({"dataset", "depth_maps", "patterns", "annotations"})


@dataclass(frozen=True)
class DepthProject:
    config_path: Path
    dataset: Path
    depth_maps: Path
    patterns: tuple[str, ...]
    annotations: Path


def load_config(path: Path) -> DepthProject:
    raw, dataset = read_config(path, "depth", CONFIG_KEYS)
    patterns = config_patterns(raw, "depth")
    depth_maps = dataset_relative(dataset, raw.get("depth_maps"), "depth", "depth_maps",
                                  ".depth-baseline")
    annotations = dataset_relative(dataset, raw.get("annotations"), "depth", "annotations",
                                   ".annotations/depth")
    if depth_maps == annotations or depth_maps in annotations.parents or annotations in depth_maps.parents:
        raise TaskOperationError("depth `depth_maps` and `annotations` must not overlap")
    return DepthProject(path.resolve(), dataset, depth_maps, patterns, annotations)


def _baseline_path(project: DepthProject, image_path: str) -> Path:
    return project.depth_maps / Path(image_path).with_suffix(".png")


class DepthStore(ImageTaskStore):
    task = "depth"
    done_key = "edited"
    sidecar_label = "index"

    def excluded(self) -> tuple[Path, ...]:
        return (self.project.annotations, self.project.depth_maps)

    def valid_item(self, saved: dict) -> bool:
        baseline = saved.get("baseline_path")
        return (
            is_image_size(saved.get("image_size"))
            and isinstance(saved.get("depth_path"), str)
            and (baseline is None or isinstance(baseline, str))
            and isinstance(saved.get("pixel_hash"), str)
        )

    def _baseline_relative(self, image_path: str) -> str | None:
        candidate = _baseline_path(self.project, image_path)
        if not candidate.is_file():
            return None
        return candidate.relative_to(self.project.dataset).as_posix()

    def item_view(self, item_id: str, image_path: str, saved: dict | None) -> dict:
        return {
            "item_id": item_id,
            "image_path": image_path,
            "baseline_path": self._baseline_relative(image_path),
            "depth_path": None if saved is None else saved["depth_path"],
            "image_size": None if saved is None else saved["image_size"],
        }

    def status_fields(self) -> dict:
        return {
            "depth_maps": str(self.project.depth_maps),
            "annotations": str(self.project.annotations),
        }

    def submit(self, submission: Submission) -> dict:
        size = image_size(submission.result.get("image_size"), "depth")
        raster = pixels(submission.result.get("pixels"), "depth", size)
        image_path = self.image_path(submission.item_id)
        pixel_hash = hashlib.sha256(raster).hexdigest()
        with self.lock:
            state = self._read()
            current = state["items"].get(submission.item_id)
            depth_name = f"{submission.item_id}.png"
            depth_path = self.project.annotations / depth_name
            if current is not None:
                if current.get("pixel_hash") == pixel_hash and current.get("image_size") == size:
                    return {"item_id": submission.item_id, **current}
                raise TaskConflictError(
                    f"depth item {submission.item_id!r} is already edited"
                )
            png = encode_gray8_png(size["width"], size["height"], raster)
            saved = {
                "image_path": image_path,
                "image_size": size,
                "depth_path": depth_name,
                "baseline_path": self._baseline_relative(image_path),
                "pixel_hash": pixel_hash,
            }
            try:
                atomic_write_bytes(depth_path, png)
                state["items"][submission.item_id] = saved
                state["history"].append(
                    {
                        "item_id": submission.item_id,
                        "image_path": image_path,
                        "image_size": size,
                        "depth_path": depth_name,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                atomic_write_json(self.sidecar, state)
            except OSError as error:
                raise TaskOperationError(f"cannot persist depth result: {error}") from error
            return {"item_id": submission.item_id, **saved}

    def export(self, request: ExportRequest) -> ExportResult:
        with self.lock:
            state = self._read()
            if request.format not in {"native", "json"}:
                raise TaskOperationError(
                    f"depth does not support export format {request.format!r}"
                )
            index = self.native_export(state, atomic_write_json)
            maps = tuple(
                self.project.annotations / saved["depth_path"]
                for saved in state["items"].values()
            )
            return ExportResult("depth-maps", (index, *maps))


class DepthTaskType(ImageTaskType):
    type_name = "depth"
    project_type = DepthProject
    store_type = DepthStore
    config_loader = staticmethod(load_config)
