"""Image-level single-label and multi-label classification task module."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from local_files import atomic_write_csv, atomic_write_json

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

CONFIG_KEYS = frozenset({"dataset", "labels", "mode", "patterns", "annotations"})


@dataclass(frozen=True)
class ClassificationProject:
    config_path: Path
    dataset: Path
    labels: tuple[str, ...]
    mode: str
    patterns: tuple[str, ...]
    annotations: Path


def load_config(path: Path) -> ClassificationProject:
    raw, dataset = read_config(path, "classification", CONFIG_KEYS)
    labels = text_list(raw.get("labels"), "classification", "labels")
    mode = raw.get("mode")
    if mode not in {"single", "multi"}:
        raise TaskOperationError("classification `mode` must be `single` or `multi`")
    patterns = config_patterns(raw, "classification")
    annotations = dataset_relative(
        dataset, raw.get("annotations"), "classification", "annotations",
        ".annotations/classification.json")
    return ClassificationProject(path.resolve(), dataset, labels, mode, patterns, annotations)


class ClassificationStore(ImageTaskStore):
    task = "classification"
    done_key = "labelled"

    def valid_item(self, saved: dict) -> bool:
        labels = saved.get("labels")
        if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
            return False
        if self._labels(labels) != labels:
            raise TaskOperationError("classification annotation labels are not normalized")
        return True

    def item_view(self, item_id: str, image_path: str, saved: dict | None) -> dict:
        return {
            "item_id": item_id,
            "image_path": image_path,
            "labels": [] if saved is None else saved["labels"],
        }

    def status_fields(self) -> dict:
        return {
            "annotations": str(self.project.annotations),
            "mode": self.project.mode,
            "labels": list(self.project.labels),
        }

    def _labels(self, value: object) -> list[str]:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise TaskOperationError("classification result requires a `labels` list")
        if len(set(value)) != len(value):
            raise TaskOperationError("classification labels must not contain duplicates")
        unknown = sorted(set(value) - set(self.project.labels))
        if unknown:
            raise TaskOperationError(f"unknown classification labels: {unknown}")
        if self.project.mode == "single" and len(value) != 1:
            raise TaskOperationError("single-label classification requires exactly one label")
        if self.project.mode == "multi" and not value:
            raise TaskOperationError("multi-label classification requires at least one label")
        return [label for label in self.project.labels if label in value]

    def submit(self, submission: Submission) -> dict:
        labels = self._labels(submission.result.get("labels"))
        image_path = self.image_path(submission.item_id)
        with self.lock:
            state = self._read()
            current = state["items"].get(submission.item_id)
            if current is not None:
                if current.get("labels") == labels:
                    return {"item_id": submission.item_id, **current}
                raise TaskConflictError(
                    f"classification item {submission.item_id!r} is already labelled"
                )
            saved = {"image_path": image_path, "labels": labels}
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
                raise TaskOperationError(f"cannot persist classification result: {error}") from error
            return {"item_id": submission.item_id, **saved}

    def export(self, request: ExportRequest) -> ExportResult:
        with self.lock:
            state = self._read()
            if request.format in {"native", "json"}:
                path = self.native_export(state, atomic_write_json)
                return ExportResult("classification-json", (path,))
            if request.format != "csv":
                raise TaskOperationError(
                    f"classification does not support export format {request.format!r}"
                )
            output = self.project.annotations.with_suffix(".csv")
            rows = [
                {
                    "item_id": item_id,
                    "image_path": saved["image_path"],
                    "labels": json.dumps(saved["labels"], ensure_ascii=False),
                }
                for item_id, saved in sorted(state["items"].items())
            ]
            try:
                atomic_write_csv(output, ("item_id", "image_path", "labels"), rows)
            except OSError as error:
                raise TaskOperationError(
                    f"cannot export classification CSV: {error}"
                ) from error
            return ExportResult("classification-csv", (output,), {"items": len(rows)})


class ClassificationTaskType(ImageTaskType):
    type_name = "classification"
    project_type = ClassificationProject
    store_type = ClassificationStore
    config_loader = staticmethod(load_config)
