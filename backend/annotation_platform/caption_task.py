"""Per-image free-text captioning task module."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .local_files import atomic_write_csv, atomic_write_json, file_lock
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

CONFIG_KEYS = frozenset({"dataset", "patterns", "annotations"})
DEFAULT_PATTERNS = ("**/*.jpg", "**/*.jpeg", "**/*.png", "**/*.webp")
MAX_CAPTION_LENGTH = 2000


@dataclass(frozen=True)
class CaptionProject:
    config_path: Path
    dataset: Path
    patterns: tuple[str, ...]
    annotations: Path


def _text_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise TaskOperationError(f"caption `{name}` must be a non-empty list")
    values = tuple(item.strip() for item in value if isinstance(item, str))
    if len(values) != len(value) or any(not item for item in values):
        raise TaskOperationError(f"caption `{name}` entries must be text")
    return values


def load_config(path: Path) -> CaptionProject:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise TaskOperationError(f"cannot read caption config {path}: {error}") from error
    if not isinstance(raw, dict):
        raise TaskOperationError("caption config must be a YAML mapping")
    unknown = sorted(set(raw) - CONFIG_KEYS)
    if unknown:
        raise TaskOperationError(f"unknown caption config keys: {unknown}")
    dataset_value = raw.get("dataset")
    if not isinstance(dataset_value, str) or not dataset_value.strip():
        raise TaskOperationError("caption `dataset` must be non-empty text")
    dataset_path = Path(dataset_value).expanduser()
    dataset = (
        dataset_path.resolve()
        if dataset_path.is_absolute()
        else (path.parent / dataset_path).resolve()
    )
    patterns = _text_list(raw.get("patterns", list(DEFAULT_PATTERNS)), "patterns")
    if any(Path(pattern).is_absolute() or ".." in Path(pattern).parts for pattern in patterns):
        raise TaskOperationError("caption `patterns` must stay inside dataset")
    annotation_value = raw.get("annotations", ".annotations/caption.json")
    if not isinstance(annotation_value, str) or not annotation_value.strip():
        raise TaskOperationError("caption `annotations` must be non-empty text")
    relative_annotation = Path(annotation_value)
    if relative_annotation.is_absolute():
        raise TaskOperationError("caption `annotations` must be dataset-relative")
    annotations = (dataset / relative_annotation).resolve()
    if dataset not in annotations.parents:
        raise TaskOperationError("caption `annotations` must stay inside dataset")
    return CaptionProject(path.resolve(), dataset, patterns, annotations)


def _normalize_caption(value: object) -> str:
    if not isinstance(value, str):
        raise TaskOperationError("caption result requires a text `caption`")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise TaskOperationError("caption must not be empty or whitespace-only")
    if len(normalized) > MAX_CAPTION_LENGTH:
        raise TaskOperationError(f"caption exceeds {MAX_CAPTION_LENGTH} characters")
    return normalized


class CaptionStore:
    def __init__(self, project: CaptionProject):
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
            raise TaskOperationError(f"cannot read caption annotations: {error}") from error
        if (
            not isinstance(value, dict)
            or value.get("schema") != 1
            or not isinstance(value.get("items"), dict)
            or not isinstance(value.get("history"), list)
        ):
            raise TaskOperationError("invalid caption annotation document")
        for item_id, saved in value["items"].items():
            if (
                not isinstance(item_id, str)
                or not isinstance(saved, dict)
                or not isinstance(saved.get("image_path"), str)
                or not isinstance(saved.get("caption"), str)
            ):
                raise TaskOperationError("invalid caption annotation item")
        if any(not isinstance(event, dict) for event in value["history"]):
            raise TaskOperationError("invalid caption annotation history")
        return value

    def queue(self, request: QueueRequest) -> QueuePage:
        unsupported = sorted(set(request.filters) - {"status", "q"})
        if unsupported:
            raise TaskOperationError(f"unsupported caption filters: {unsupported}")
        with self.lock:
            state = self._read()
            query = request.filters.get("q", "").lower()
            status = request.filters.get("status", "")
            selected = []
            for item_id, image_path in self.images():
                saved = state["items"].get(item_id)
                if status == "pending" and saved is not None:
                    continue
                if status in {"captioned", "labelled", "labeled"} and saved is None:
                    continue
                if query and query not in image_path.lower():
                    continue
                selected.append(
                    {
                        "item_id": item_id,
                        "image_path": image_path,
                        "caption": "" if saved is None else saved["caption"],
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
        caption = _normalize_caption(submission.result.get("caption"))
        images = dict(self.images())
        image_path = images.get(submission.item_id)
        if image_path is None:
            raise TaskOperationError(f"unknown caption item {submission.item_id!r}")
        with self.lock:
            state = self._read()
            current = state["items"].get(submission.item_id)
            if current is not None:
                if current.get("caption") == caption:
                    return {"item_id": submission.item_id, **current}
                raise TaskConflictError(
                    f"caption item {submission.item_id!r} is already captioned"
                )
            saved = {"image_path": image_path, "caption": caption}
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
                raise TaskOperationError(f"cannot persist caption result: {error}") from error
            return {"item_id": submission.item_id, **saved}

    def status(self) -> TaskStatus:
        with self.lock:
            state = self._read()
            images = self.images()
            current_ids = {item_id for item_id, _ in images}
            captioned = sum(item_id in state["items"] for item_id in current_ids)
        total = len(images)
        if not self.project.dataset.exists():
            status = "missing"
        elif not total:
            status = "empty"
        else:
            status = "reviewed" if captioned == total else "reviewing"
        return TaskStatus(
            status,
            {
                "config": str(self.project.config_path),
                "dataset": str(self.project.dataset),
                "annotations": str(self.project.annotations),
                "total": total,
                "captioned": captioned,
                "pending": total - captioned,
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
                    raise TaskOperationError(f"cannot export caption JSON: {error}") from error
                return ExportResult("caption-json", (self.project.annotations,))
            if request.format != "csv":
                raise TaskOperationError(
                    f"caption does not support export format {request.format!r}"
                )
            output = self.project.annotations.with_suffix(".csv")
            rows = [
                {"item_id": item_id, "image_path": saved["image_path"], "caption": saved["caption"]}
                for item_id, saved in sorted(state["items"].items())
            ]
            try:
                atomic_write_csv(output, ("item_id", "image_path", "caption"), rows)
            except OSError as error:
                raise TaskOperationError(f"cannot export caption CSV: {error}") from error
            return ExportResult("caption-csv", (output,), {"items": len(rows)})


class CaptionTaskType:
    type_name = "caption"

    @staticmethod
    def _project(project: TaskProject) -> CaptionProject:
        if not isinstance(project.value, CaptionProject):
            raise TaskOperationError("caption requires its own project configuration")
        return project.value

    def load(self, config_path: Path) -> TaskProject:
        project = load_config(config_path)
        if project.dataset.exists() and not project.dataset.is_dir():
            raise TaskOperationError(f"dataset root is not a directory: {project.dataset}")
        return TaskProject(project.config_path, project.dataset, project)

    def queue(self, project: TaskProject, request: QueueRequest) -> QueuePage:
        return CaptionStore(self._project(project)).queue(request)

    def submit(self, project: TaskProject, submission: Submission) -> SubmissionResult:
        store = CaptionStore(self._project(project))
        item = store.submit(submission)
        return SubmissionResult(item, store.status())

    def status(self, project: TaskProject) -> TaskStatus:
        return CaptionStore(self._project(project)).status()

    def export(self, project: TaskProject, request: ExportRequest) -> ExportResult:
        return CaptionStore(self._project(project)).export(request)
