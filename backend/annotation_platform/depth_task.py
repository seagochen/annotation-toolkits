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

CONFIG_KEYS = frozenset({"dataset", "depth_maps", "patterns", "annotations"})
DEFAULT_PATTERNS = ("**/*.jpg", "**/*.jpeg", "**/*.png", "**/*.webp")


@dataclass(frozen=True)
class DepthProject:
    config_path: Path
    dataset: Path
    depth_maps: Path
    patterns: tuple[str, ...]
    annotations: Path


def _text_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise TaskOperationError(f"depth `{name}` must be a non-empty list")
    values = tuple(item.strip() for item in value if isinstance(item, str))
    if len(values) != len(value) or any(not item for item in values):
        raise TaskOperationError(f"depth `{name}` entries must be text")
    return values


def _dataset_relative_dir(dataset: Path, value: object, key: str, default: str) -> Path:
    raw = value if value is not None else default
    if not isinstance(raw, str) or not raw.strip():
        raise TaskOperationError(f"depth `{key}` must be non-empty text")
    relative = Path(raw)
    if relative.is_absolute():
        raise TaskOperationError(f"depth `{key}` must be dataset-relative")
    resolved = (dataset / relative).resolve()
    if dataset not in resolved.parents:
        raise TaskOperationError(f"depth `{key}` must stay inside dataset")
    return resolved


def load_config(path: Path) -> DepthProject:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise TaskOperationError(f"cannot read depth config {path}: {error}") from error
    if not isinstance(raw, dict):
        raise TaskOperationError("depth config must be a YAML mapping")
    unknown = sorted(set(raw) - CONFIG_KEYS)
    if unknown:
        raise TaskOperationError(f"unknown depth config keys: {unknown}")
    dataset_value = raw.get("dataset")
    if not isinstance(dataset_value, str) or not dataset_value.strip():
        raise TaskOperationError("depth `dataset` must be non-empty text")
    dataset_path = Path(dataset_value).expanduser()
    dataset = (
        dataset_path.resolve()
        if dataset_path.is_absolute()
        else (path.parent / dataset_path).resolve()
    )
    patterns = _text_list(raw.get("patterns", list(DEFAULT_PATTERNS)), "patterns")
    if any(Path(pattern).is_absolute() or ".." in Path(pattern).parts for pattern in patterns):
        raise TaskOperationError("depth `patterns` must stay inside dataset")
    depth_maps = _dataset_relative_dir(dataset, raw.get("depth_maps"), "depth_maps", ".depth-baseline")
    annotations = _dataset_relative_dir(dataset, raw.get("annotations"), "annotations", ".annotations/depth")
    if depth_maps == annotations or depth_maps in annotations.parents or annotations in depth_maps.parents:
        raise TaskOperationError("depth `depth_maps` and `annotations` must not overlap")
    return DepthProject(path.resolve(), dataset, depth_maps, patterns, annotations)


def _baseline_path(project: DepthProject, image_path: str) -> Path:
    return project.depth_maps / Path(image_path).with_suffix(".png")


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TaskOperationError(f"depth `{name}` must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise TaskOperationError(f"depth `{name}` must be finite")
    return number


def _image_size(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"width", "height"}:
        raise TaskOperationError("depth result requires `image_size` with width/height")
    width = _finite_number(value["width"], "image_size.width")
    height = _finite_number(value["height"], "image_size.height")
    if width <= 0 or height <= 0 or width != int(width) or height != int(height):
        raise TaskOperationError("depth `image_size` must be positive whole numbers")
    return {"width": int(width), "height": int(height)}


def _pixels(value: object, image_size: dict) -> bytes:
    if not isinstance(value, str) or not value:
        raise TaskOperationError("depth result requires base64 `pixels`")
    try:
        decoded = base64.b64decode(value, validate=True)
    except binascii.Error as error:
        raise TaskOperationError(f"depth `pixels` is not valid base64: {error}") from error
    expected = image_size["width"] * image_size["height"]
    if len(decoded) != expected:
        raise TaskOperationError(f"depth `pixels` must contain {expected} bytes, got {len(decoded)}")
    return decoded


class DepthStore:
    def __init__(self, project: DepthProject):
        self.project = project
        self.index_path = project.annotations / "index.json"
        self.lock = file_lock(self.index_path)

    def images(self) -> tuple[tuple[str, str], ...]:
        root = self.project.dataset
        if not root.is_dir():
            return ()
        excluded = (self.project.annotations, self.project.depth_maps)
        found: dict[str, str] = {}
        for pattern in self.project.patterns:
            for path in root.glob(pattern):
                resolved = path.resolve()
                if root not in resolved.parents or not resolved.is_file():
                    continue
                if any(resolved == folder or folder in resolved.parents for folder in excluded):
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
            raise TaskOperationError(f"cannot read depth index: {error}") from error
        if (
            not isinstance(value, dict)
            or value.get("schema") != 1
            or not isinstance(value.get("items"), dict)
            or not isinstance(value.get("history"), list)
        ):
            raise TaskOperationError("invalid depth index document")
        return value

    def _baseline_relative(self, image_path: str) -> str | None:
        candidate = _baseline_path(self.project, image_path)
        if not candidate.is_file():
            return None
        return candidate.relative_to(self.project.dataset).as_posix()

    def queue(self, request: QueueRequest) -> QueuePage:
        unsupported = sorted(set(request.filters) - {"status", "q"})
        if unsupported:
            raise TaskOperationError(f"unsupported depth filters: {unsupported}")
        with self.lock:
            state = self._read()
            query = request.filters.get("q", "").lower()
            status = request.filters.get("status", "")
            selected = []
            for item_id, image_path in self.images():
                saved = state["items"].get(item_id)
                if status == "pending" and saved is not None:
                    continue
                if status in {"edited", "labelled", "labeled"} and saved is None:
                    continue
                if query and query not in image_path.lower():
                    continue
                selected.append(
                    {
                        "item_id": item_id,
                        "image_path": image_path,
                        "baseline_path": self._baseline_relative(image_path),
                        "depth_path": None if saved is None else saved["depth_path"],
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
        pixels = _pixels(submission.result.get("pixels"), image_size)
        images = dict(self.images())
        image_path = images.get(submission.item_id)
        if image_path is None:
            raise TaskOperationError(f"unknown depth item {submission.item_id!r}")
        pixel_hash = hashlib.sha256(pixels).hexdigest()
        with self.lock:
            state = self._read()
            current = state["items"].get(submission.item_id)
            depth_name = f"{submission.item_id}.png"
            depth_path = self.project.annotations / depth_name
            if current is not None:
                if current.get("pixel_hash") == pixel_hash and current.get("image_size") == image_size:
                    return {"item_id": submission.item_id, **current}
                raise TaskConflictError(
                    f"depth item {submission.item_id!r} is already edited"
                )
            png = encode_gray8_png(image_size["width"], image_size["height"], pixels)
            saved = {
                "image_path": image_path,
                "image_size": image_size,
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
                        "image_size": image_size,
                        "depth_path": depth_name,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                atomic_write_json(self.index_path, state)
            except OSError as error:
                raise TaskOperationError(f"cannot persist depth result: {error}") from error
            return {"item_id": submission.item_id, **saved}

    def status(self) -> TaskStatus:
        with self.lock:
            state = self._read()
            images = self.images()
            current_ids = {item_id for item_id, _ in images}
            edited = sum(item_id in state["items"] for item_id in current_ids)
        total = len(images)
        if not self.project.dataset.exists():
            status = "missing"
        elif not total:
            status = "empty"
        else:
            status = "reviewed" if edited == total else "reviewing"
        return TaskStatus(
            status,
            {
                "config": str(self.project.config_path),
                "dataset": str(self.project.dataset),
                "depth_maps": str(self.project.depth_maps),
                "annotations": str(self.project.annotations),
                "total": total,
                "edited": edited,
                "pending": total - edited,
            },
        )

    def export(self, request: ExportRequest) -> ExportResult:
        with self.lock:
            state = self._read()
            if request.format not in {"native", "json"}:
                raise TaskOperationError(
                    f"depth does not support export format {request.format!r}"
                )
            try:
                if not self.index_path.is_file():
                    atomic_write_json(self.index_path, state)
            except OSError as error:
                raise TaskOperationError(f"cannot export depth index: {error}") from error
            maps = tuple(
                self.project.annotations / saved["depth_path"]
                for saved in state["items"].values()
            )
            return ExportResult("depth-maps", (self.index_path, *maps))


class DepthTaskType:
    type_name = "depth"

    @staticmethod
    def _project(project: TaskProject) -> DepthProject:
        if not isinstance(project.value, DepthProject):
            raise TaskOperationError("depth requires its own project configuration")
        return project.value

    def load(self, config_path: Path) -> TaskProject:
        project = load_config(config_path)
        if project.dataset.exists() and not project.dataset.is_dir():
            raise TaskOperationError(f"dataset root is not a directory: {project.dataset}")
        return TaskProject(project.config_path, project.dataset, project)

    def queue(self, project: TaskProject, request: QueueRequest) -> QueuePage:
        return DepthStore(self._project(project)).queue(request)

    def submit(self, project: TaskProject, submission: Submission) -> SubmissionResult:
        store = DepthStore(self._project(project))
        item = store.submit(submission)
        return SubmissionResult(item, store.status())

    def status(self, project: TaskProject) -> TaskStatus:
        return DepthStore(self._project(project)).status()

    def export(self, project: TaskProject, request: ExportRequest) -> ExportResult:
        return DepthStore(self._project(project)).export(request)
