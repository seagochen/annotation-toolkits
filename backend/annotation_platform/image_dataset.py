"""Shared base for task types that annotate a local directory of files.

Classification, captioning, detection, segmentation, depth, polygon and
text_span all read the same kind of project: a ``dataset`` directory, glob
``patterns`` for its files (images, or UTF-8 text documents for the text
tasks), and an ``annotations`` sidecar inside the dataset. Everything that is
the same for all of them lives here, once:

- config parsing of those keys (``read_config``, ``config_patterns``,
  ``dataset_relative``) and text lists (``text_list``);
- image discovery and the persisted item id (``discover_images``, ``item_id``);
- the sidecar document (``schema``/``items``/``history``) and its validation;
- queue filtering/paging, the platform status vocabulary and its rule;
- common result payloads (``finite_number``, ``image_size``, ``pixels``);
- the ``ImageTaskType`` protocol wrapper.

Each task module keeps only what is its own: its config dataclass, the
semantics of its ``result``, the shape of a queue item and its export formats.
Error messages keep the task name (``task``) so they read exactly as before.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
from pathlib import Path
from typing import Callable, ClassVar

import yaml

from local_files import file_lock

from .file_kinds import DocumentError, media_kind, read_document

from .task_types import (
    STATUS_EMPTY,
    STATUS_MISSING,
    STATUS_REVIEWED,
    STATUS_REVIEWING,
    ExportRequest,
    ExportResult,
    QueuePage,
    QueueRequest,
    Submission,
    SubmissionResult,
    TaskOperationError,
    TaskProject,
    TaskStatus,
)

DEFAULT_PATTERNS = ("**/*.jpg", "**/*.jpeg", "**/*.png", "**/*.webp")

# Queue `status` filter values: whether the item must already have a result.
STATUS_FILTERS = {"pending": False, "annotated": True}


def item_id(relative_path: str) -> str:
    """The persisted id of an image: stable for its dataset-relative path."""
    return "i" + hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16]


def text_list(value: object, task: str, name: str) -> tuple[str, ...]:
    """A non-empty list of unique, non-blank strings (stripped)."""
    if not isinstance(value, list) or not value:
        raise TaskOperationError(f"{task} `{name}` must be a non-empty list")
    values = tuple(item.strip() for item in value if isinstance(item, str))
    if len(values) != len(value) or any(not item for item in values):
        raise TaskOperationError(f"{task} `{name}` entries must be text")
    if len(set(values)) != len(values):
        raise TaskOperationError(f"{task} `{name}` entries must be unique")
    return values


def read_config(path: Path, task: str, keys: frozenset[str]) -> tuple[dict, Path]:
    """Read the YAML mapping, refuse unknown keys, resolve ``dataset``."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise TaskOperationError(f"cannot read {task} config {path}: {error}") from error
    if not isinstance(raw, dict):
        raise TaskOperationError(f"{task} config must be a YAML mapping")
    unknown = sorted(set(raw) - keys)
    if unknown:
        raise TaskOperationError(f"unknown {task} config keys: {unknown}")
    dataset_value = raw.get("dataset")
    if not isinstance(dataset_value, str) or not dataset_value.strip():
        raise TaskOperationError(f"{task} `dataset` must be non-empty text")
    dataset_path = Path(dataset_value).expanduser()
    dataset = (
        dataset_path.resolve()
        if dataset_path.is_absolute()
        else (path.parent / dataset_path).resolve()
    )
    return raw, dataset


def config_patterns(raw: dict, task: str) -> tuple[str, ...]:
    patterns = text_list(raw.get("patterns", list(DEFAULT_PATTERNS)), task, "patterns")
    if any(Path(pattern).is_absolute() or ".." in Path(pattern).parts for pattern in patterns):
        raise TaskOperationError(f"{task} `patterns` must stay inside dataset")
    return patterns


def dataset_relative(dataset: Path, value: object, task: str, key: str, default: str) -> Path:
    """A path config key that must name a location inside the dataset."""
    raw = value if value is not None else default
    if not isinstance(raw, str) or not raw.strip():
        raise TaskOperationError(f"{task} `{key}` must be non-empty text")
    relative = Path(raw)
    if relative.is_absolute():
        raise TaskOperationError(f"{task} `{key}` must be dataset-relative")
    resolved = (dataset / relative).resolve()
    if dataset not in resolved.parents:
        raise TaskOperationError(f"{task} `{key}` must stay inside dataset")
    return resolved


def discover_images(
    root: Path, patterns: tuple[str, ...], excluded: tuple[Path, ...] = ()
) -> tuple[tuple[str, str], ...]:
    """``(item_id, relative_path)`` of every matching file, sorted by path.

    Files outside the dataset (through symlinks) and inside ``excluded``
    folders (a task's own output, baselines) are never queue items.
    """
    if not root.is_dir():
        return ()
    found: dict[str, str] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            resolved = path.resolve()
            if root not in resolved.parents or not resolved.is_file():
                continue
            if any(resolved == folder or folder in resolved.parents for folder in excluded):
                continue
            relative = path.relative_to(root).as_posix()
            found[item_id(relative)] = relative
    return tuple(sorted(found.items(), key=lambda item: item[1]))


def finite_number(value: object, task: str, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TaskOperationError(f"{task} `{name}` must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise TaskOperationError(f"{task} `{name}` must be finite")
    return number


def image_size(value: object, task: str) -> dict:
    if not isinstance(value, dict) or set(value) != {"width", "height"}:
        raise TaskOperationError(f"{task} result requires `image_size` with width/height")
    width = finite_number(value["width"], task, "image_size.width")
    height = finite_number(value["height"], task, "image_size.height")
    if width <= 0 or height <= 0 or width != int(width) or height != int(height):
        raise TaskOperationError(f"{task} `image_size` must be positive whole numbers")
    return {"width": int(width), "height": int(height)}


def pixels(value: object, task: str, size: dict) -> bytes:
    """A base64 single-channel raster with exactly width*height bytes."""
    if not isinstance(value, str) or not value:
        raise TaskOperationError(f"{task} result requires base64 `pixels`")
    try:
        decoded = base64.b64decode(value, validate=True)
    except binascii.Error as error:
        raise TaskOperationError(f"{task} `pixels` is not valid base64: {error}") from error
    expected = size["width"] * size["height"]
    if len(decoded) != expected:
        raise TaskOperationError(
            f"{task} `pixels` must contain {expected} bytes, got {len(decoded)}"
        )
    return decoded


def is_image_size(value: object) -> bool:
    """Shape check for an ``image_size`` already stored in a sidecar."""
    return (
        isinstance(value, dict)
        and set(value) == {"width", "height"}
        and all(isinstance(value[key], int) for key in value)
    )


class ImageTaskStore:
    """Sidecar-backed queue, status and validation shared by the image tasks.

    Subclasses set ``task`` (the name used in messages), ``done_key`` (the
    status summary's count of finished items) and, for index-plus-files
    tasks, ``sidecar_label = "index"``; and implement ``item_view``,
    ``status_fields`` and ``valid_item``.
    """

    task: ClassVar[str]
    done_key: ClassVar[str]
    # "annotation": the sidecar is the annotations file itself;
    # "index": the annotations setting is a folder holding index.json + files.
    sidecar_label: ClassVar[str] = "annotation"

    def __init__(self, project) -> None:
        self.project = project
        self.sidecar = (
            project.annotations / "index.json"
            if self.sidecar_label == "index"
            else project.annotations
        )
        self.lock = file_lock(self.sidecar)

    # -- per-task hooks -----------------------------------------------------

    def excluded(self) -> tuple[Path, ...]:
        """Folders inside the dataset that hold no source images."""
        return (self.project.annotations,) if self.sidecar_label == "index" else ()

    def valid_item(self, saved: dict) -> bool:
        """Task-specific shape of one stored item (beyond ``image_path``)."""
        raise NotImplementedError

    def item_view(self, item_id: str, image_path: str, saved: dict | None) -> dict:
        raise NotImplementedError

    def status_fields(self) -> dict:
        """Summary fields between ``dataset`` and ``total``, in display order."""
        raise NotImplementedError

    # -- shared behaviour ---------------------------------------------------

    def images(self) -> tuple[tuple[str, str], ...]:
        return discover_images(self.project.dataset, self.project.patterns, self.excluded())

    def image_path(self, item_id: str) -> str:
        path = dict(self.images()).get(item_id)
        if path is None:
            raise TaskOperationError(f"unknown {self.task} item {item_id!r}")
        return path

    def document_text(self, relative: str) -> str:
        """A text item's content, or an explicit error naming the file."""
        try:
            return read_document(self.project.dataset / relative)
        except DocumentError as error:
            raise TaskOperationError(f"{self.task} document {relative!r} {error}") from error

    def media_fields(self, relative: str) -> dict:
        """``media`` plus, for text, its content or the reason it is unreadable.

        Images are shown by the browser straight from the files endpoint;
        text is decoded here so the page never guesses an encoding.
        """
        if media_kind(relative) == "image":
            return {"media": "image"}
        try:
            return {"media": "text", "text": self.document_text(relative), "text_error": None}
        except TaskOperationError as error:
            return {"media": "text", "text": None, "text_error": str(error)}

    def submittable_path(self, item_id: str) -> str:
        """``image_path`` of an item a result may be saved for.

        A text document that cannot be decoded was never shown to anyone, so
        nothing may be recorded against it.
        """
        relative = self.image_path(item_id)
        if media_kind(relative) == "text":
            self.document_text(relative)
        return relative

    def _read(self) -> dict:
        label = self.sidecar_label
        if not self.sidecar.is_file():
            return {"schema": 1, "items": {}, "history": []}
        try:
            value = json.loads(self.sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            noun = "annotations" if label == "annotation" else label
            raise TaskOperationError(f"cannot read {self.task} {noun}: {error}") from error
        if (
            not isinstance(value, dict)
            or value.get("schema") != 1
            or not isinstance(value.get("items"), dict)
            or not isinstance(value.get("history"), list)
        ):
            raise TaskOperationError(f"invalid {self.task} {label} document")
        for key, saved in value["items"].items():
            if (
                not isinstance(key, str)
                or not isinstance(saved, dict)
                or not isinstance(saved.get("image_path"), str)
                or not self.valid_item(saved)
            ):
                raise TaskOperationError(f"invalid {self.task} {label} item")
        if any(not isinstance(event, dict) for event in value["history"]):
            raise TaskOperationError(f"invalid {self.task} {label} history")
        return value

    def queue(self, request: QueueRequest) -> QueuePage:
        unsupported = sorted(set(request.filters) - {"status", "q"})
        if unsupported:
            raise TaskOperationError(f"unsupported {self.task} filters: {unsupported}")
        status = request.filters.get("status", "")
        if status and status not in STATUS_FILTERS:
            raise TaskOperationError(
                f"unsupported {self.task} status filter {status!r}; "
                f"one of {sorted(STATUS_FILTERS)}"
            )
        query = request.filters.get("q", "").lower()
        with self.lock:
            state = self._read()
            selected = []
            for key, path in self.images():
                saved = state["items"].get(key)
                if status and STATUS_FILTERS[status] != (saved is not None):
                    continue
                if query and query not in path.lower():
                    continue
                # Every image task's queue items say whether a result exists, so
                # the image list can mark finished items without knowing the
                # task's own result fields (an empty detection is still done).
                selected.append({**self.item_view(key, path, saved), "annotated": saved is not None})
            page = selected[request.offset : request.offset + request.limit]
            return QueuePage(
                total=len(selected),
                offset=request.offset,
                limit=request.limit,
                items=tuple(page),
            )

    def status(self) -> TaskStatus:
        with self.lock:
            state = self._read()
            images = self.images()
            done = sum(key in state["items"] for key, _ in images)
        total = len(images)
        if not self.project.dataset.exists():
            state_name = STATUS_MISSING
        elif not total:
            state_name = STATUS_EMPTY
        else:
            state_name = STATUS_REVIEWED if done == total else STATUS_REVIEWING
        return TaskStatus(
            state_name,
            {
                "config": str(self.project.config_path),
                "dataset": str(self.project.dataset),
                **self.status_fields(),
                "total": total,
                self.done_key: done,
                "pending": total - done,
            },
        )

    def native_export(self, state: dict, write_json: Callable[[Path, object], None]) -> Path:
        """Materialize an empty sidecar so a native export always has a file.

        ``write_json`` is the caller's ``atomic_write_json`` so tests can patch
        it per module.
        """
        try:
            if not self.sidecar.is_file():
                write_json(self.sidecar, state)
        except OSError as error:
            noun = "JSON" if self.sidecar_label == "annotation" else "index"
            raise TaskOperationError(f"cannot export {self.task} {noun}: {error}") from error
        return self.sidecar


class ImageTaskType:
    """The ``TaskTypeModule`` wrapper every image task shares.

    Subclasses set ``type_name``, ``project_type`` (the config dataclass),
    ``store_type`` and ``config_loader`` (``staticmethod(load_config)``).
    """

    type_name: ClassVar[str]
    project_type: ClassVar[type]
    store_type: ClassVar[type[ImageTaskStore]]
    config_loader: ClassVar[Callable[[Path], object]]

    def _store(self, project: TaskProject) -> ImageTaskStore:
        if not isinstance(project.value, self.project_type):
            raise TaskOperationError(
                f"{self.store_type.task} requires its own project configuration"
            )
        return self.store_type(project.value)

    def load(self, config_path: Path) -> TaskProject:
        project = self.config_loader(config_path)
        if project.dataset.exists() and not project.dataset.is_dir():
            raise TaskOperationError(f"dataset root is not a directory: {project.dataset}")
        return TaskProject(project.config_path, project.dataset, project)

    def queue(self, project: TaskProject, request: QueueRequest) -> QueuePage:
        return self._store(project).queue(request)

    def submit(self, project: TaskProject, submission: Submission) -> SubmissionResult:
        store = self._store(project)
        item = store.submit(submission)
        return SubmissionResult(item, store.status())

    def status(self, project: TaskProject) -> TaskStatus:
        return self._store(project).status()

    def export(self, project: TaskProject, request: ExportRequest) -> ExportResult:
        return self._store(project).export(request)
