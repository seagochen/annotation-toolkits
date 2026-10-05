"""Span (entity / interval) annotation over UTF-8 text documents.

Each item is one text file; its result is a list of labelled half-open
intervals ``[start, end)``. Positions count **Unicode code points** of the
document exactly as ``file_kinds.read_document`` decodes it (BOM dropped,
line endings kept), which is Python string indexing -- the frontend converts
to and from its UTF-16 indices. Spans may overlap, nest and cross line or
paragraph breaks; the only rule is ``0 <= start < end <= len(text)``.

Nothing here assumes the positions are characters beyond that bound check:
``_spans`` takes the item's length, so an audio/video task could reuse the
same interval shape with time units (not implemented).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from local_files import atomic_write_json

from .image_dataset import (
    ImageTaskStore,
    ImageTaskType,
    config_patterns,
    dataset_relative,
    read_config,
    text_list,
)
from .task_types import (
    ExportRequest,
    ExportResult,
    Submission,
    TaskConflictError,
    TaskOperationError,
)

CONFIG_KEYS = frozenset({"dataset", "labels", "patterns", "annotations"})
DEFAULT_PATTERNS = ("**/*.txt",)
SPAN_FIELDS = frozenset({"start", "end", "label"})


@dataclass(frozen=True)
class TextSpanProject:
    config_path: Path
    dataset: Path
    labels: tuple[str, ...]
    patterns: tuple[str, ...]
    annotations: Path


def load_config(path: Path) -> TextSpanProject:
    raw, dataset = read_config(path, "text_span", CONFIG_KEYS)
    labels = text_list(raw.get("labels"), "text_span", "labels")
    patterns = config_patterns({"patterns": list(DEFAULT_PATTERNS), **raw}, "text_span")
    annotations = dataset_relative(
        dataset, raw.get("annotations"), "text_span", "annotations",
        ".annotations/text_span.json")
    return TextSpanProject(path.resolve(), dataset, labels, patterns, annotations)


def _position(value: object, name: str) -> int:
    # bool is an int subclass; `true` is not a position.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskOperationError(f"text_span `{name}` must be an integer")
    return value


def _spans(value: object, labels: tuple[str, ...], length: int) -> list[dict]:
    """Validated spans in a canonical order: by start, end, then label order."""
    if not isinstance(value, list):
        raise TaskOperationError("text_span result requires a `spans` list")
    normalized = []
    for index, span in enumerate(value):
        if not isinstance(span, dict) or set(span) != SPAN_FIELDS:
            raise TaskOperationError(
                f"text_span span {index} must have exactly start, end and label"
            )
        start = _position(span["start"], f"spans[{index}].start")
        end = _position(span["end"], f"spans[{index}].end")
        if not 0 <= start < end <= length:
            raise TaskOperationError(
                f"text_span span {index} [{start}, {end}) must satisfy "
                f"0 <= start < end <= {length} (document length in code points)"
            )
        label = span["label"]
        if label not in labels:
            raise TaskOperationError(f"unknown text_span label {label!r}")
        normalized.append({"start": start, "end": end, "label": label})
    order = {label: index for index, label in enumerate(labels)}
    normalized.sort(key=lambda span: (span["start"], span["end"], order[span["label"]]))
    for previous, current in zip(normalized, normalized[1:]):
        if previous == current:
            raise TaskOperationError(
                f"text_span span [{current['start']}, {current['end']}) "
                f"{current['label']!r} is listed twice"
            )
    return normalized


class TextSpanStore(ImageTaskStore):
    task = "text_span"
    done_key = "annotated"

    def valid_item(self, saved: dict) -> bool:
        spans = saved.get("spans")
        return (
            isinstance(saved.get("length"), int)
            and isinstance(spans, list)
            and all(isinstance(span, dict) and set(span) == SPAN_FIELDS for span in spans)
        )

    def item_view(self, item_id: str, image_path: str, saved: dict | None) -> dict:
        try:
            text, error = self.document_text(image_path), None
        except TaskOperationError as problem:
            text, error = None, str(problem)
        return {
            "item_id": item_id,
            "image_path": image_path,
            "media": "text",
            "text": text,
            "text_error": error,
            "spans": [] if saved is None else saved["spans"],
        }

    def status_fields(self) -> dict:
        return {
            "annotations": str(self.project.annotations),
            "labels": list(self.project.labels),
        }

    def submit(self, submission: Submission) -> dict:
        image_path = self.image_path(submission.item_id)
        text = self.document_text(image_path)
        spans = _spans(submission.result.get("spans"), self.project.labels, len(text))
        with self.lock:
            state = self._read()
            current = state["items"].get(submission.item_id)
            if current is not None:
                if current.get("spans") == spans and current.get("length") == len(text):
                    return {"item_id": submission.item_id, **current}
                raise TaskConflictError(
                    f"text_span item {submission.item_id!r} is already annotated"
                )
            # `length` pins the document the offsets were made against: if the
            # file is edited later, export can tell the spans no longer fit.
            saved = {"image_path": image_path, "length": len(text), "spans": spans}
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
                raise TaskOperationError(f"cannot persist text_span result: {error}") from error
            return {"item_id": submission.item_id, **saved}

    def export(self, request: ExportRequest) -> ExportResult:
        with self.lock:
            state = self._read()
            if request.format not in {"native", "json"}:
                raise TaskOperationError(
                    f"text_span does not support export format {request.format!r}"
                )
            path = self.native_export(state, atomic_write_json)
            return ExportResult("text-span-json", (path,), {"items": len(state["items"])})


class TextSpanTaskType(ImageTaskType):
    type_name = "text_span"
    project_type = TextSpanProject
    store_type = TextSpanStore
    config_loader = staticmethod(load_config)
