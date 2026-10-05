"""Free-text captioning of images, or free-text generation (translation,
summary) for text documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from local_files import atomic_write_csv, atomic_write_json

from .file_kinds import is_image_path
from .image_dataset import (
    ImageTaskStore,
    ImageTaskType,
    config_patterns,
    dataset_relative,
    read_config,
)
from .task_types import (
    ExportRequest,
    ExportResult,
    Submission,
    TaskConflictError,
    TaskOperationError,
)

CONFIG_KEYS = frozenset({"dataset", "patterns", "annotations"})
MAX_CAPTION_LENGTH = 2000
# Translating or summarizing a document can need far more than a caption.
MAX_GENERATED_TEXT_LENGTH = 20000


@dataclass(frozen=True)
class CaptionProject:
    config_path: Path
    dataset: Path
    patterns: tuple[str, ...]
    annotations: Path


def load_config(path: Path) -> CaptionProject:
    raw, dataset = read_config(path, "caption", CONFIG_KEYS)
    patterns = config_patterns(raw, "caption")
    annotations = dataset_relative(
        dataset, raw.get("annotations"), "caption", "annotations", ".annotations/caption.json")
    return CaptionProject(path.resolve(), dataset, patterns, annotations)


def _normalize_caption(value: object, limit: int = MAX_CAPTION_LENGTH) -> str:
    if not isinstance(value, str):
        raise TaskOperationError("caption result requires a text `caption`")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise TaskOperationError("caption must not be empty or whitespace-only")
    if len(normalized) > limit:
        raise TaskOperationError(f"caption exceeds {limit} characters")
    return normalized


class CaptionStore(ImageTaskStore):
    task = "caption"
    done_key = "captioned"

    def valid_item(self, saved: dict) -> bool:
        return isinstance(saved.get("caption"), str)

    def item_view(self, item_id: str, image_path: str, saved: dict | None) -> dict:
        return {
            "item_id": item_id,
            "image_path": image_path,
            **self.media_fields(image_path),
            "caption": "" if saved is None else saved["caption"],
        }

    def status_fields(self) -> dict:
        return {"annotations": str(self.project.annotations)}

    def submit(self, submission: Submission) -> dict:
        image_path = self.submittable_path(submission.item_id)
        limit = MAX_CAPTION_LENGTH if is_image_path(image_path) else MAX_GENERATED_TEXT_LENGTH
        caption = _normalize_caption(submission.result.get("caption"), limit)
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

    def export(self, request: ExportRequest) -> ExportResult:
        with self.lock:
            state = self._read()
            if request.format in {"native", "json"}:
                path = self.native_export(state, atomic_write_json)
                return ExportResult("caption-json", (path,))
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


class CaptionTaskType(ImageTaskType):
    type_name = "captioning"
    project_type = CaptionProject
    store_type = CaptionStore
    config_loader = staticmethod(load_config)
