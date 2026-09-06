"""Thin task-plugin adapter for the existing ReID implementation."""

from __future__ import annotations

from pathlib import Path

from reid_annotation_tool import config as reid_config
from reid_annotation_tool.app import latest_pairs
from reid_annotation_tool.server import LabelConflictError, Store

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


class ReIDTaskType:
    type_name = "reid"

    @staticmethod
    def _project(project: TaskProject) -> reid_config.Project:
        if not isinstance(project.value, reid_config.Project):
            raise TaskOperationError("reid requires a ReID project configuration")
        return project.value

    def load(self, config_path: Path) -> TaskProject:
        try:
            project = reid_config.load(config_path)
        except reid_config.ConfigError as error:
            raise TaskOperationError(str(error)) from error
        if project.dataset.exists() and not project.dataset.is_dir():
            raise TaskOperationError(
                f"dataset root is not a directory: {project.dataset}"
            )
        return TaskProject(project.path, project.dataset, project)

    @staticmethod
    def _store(project: reid_config.Project) -> Store:
        return Store(
            project.dataset,
            project.live_round(),
            project.base_pairs,
            project.rounds(),
        )

    def queue(self, project: TaskProject, request: QueueRequest) -> QueuePage:
        selected = self._store(self._project(project)).queue(
            kind=request.filters.get("kind", ""),
            split=request.filters.get("split", ""),
            status=request.filters.get("status", ""),
            search=request.filters.get("q", ""),
            offset=request.offset,
            limit=min(request.limit, 200),
        )
        return QueuePage(
            total=selected["total"],
            offset=selected["offset"],
            limit=selected["limit"],
            items=tuple(selected["rows"]),
        )

    def submit(
        self, project: TaskProject, submission: Submission
    ) -> SubmissionResult:
        reid_project = self._project(project)
        label = submission.result.get("label")
        if label not in {"same", "different", "unclear"}:
            raise TaskOperationError("reid submission requires a supported `label`")
        live_round = reid_project.live_round()
        if live_round is None:
            raise TaskOperationError("reid project has no review queue")
        notes = submission.result.get("notes")
        try:
            item = self._store(reid_project).set_label(
                submission.item_id,
                str(label),
                None if notes is None else str(notes),
                overwrite=False,
            )
        except LabelConflictError as error:
            raise TaskConflictError(str(error)) from error
        except OSError as error:
            raise TaskOperationError(
                f"cannot persist queue item {submission.item_id!r}: {error}"
            ) from error
        if item is None:
            raise TaskOperationError(f"unknown queue item {submission.item_id!r}")
        return SubmissionResult(item=item, status=self.status(project))

    def status(self, project: TaskProject) -> TaskStatus:
        summary = self._project(project).summary()
        if not summary["exists"]:
            state = "missing"
        elif not summary["identities"]:
            state = "empty"
        elif not summary["live_round"]:
            state = "needs_mining"
        else:
            state = "reviewing" if summary["pending"] else "reviewed"
        return TaskStatus(state=state, details=summary)

    def export(self, project: TaskProject, request: ExportRequest) -> ExportResult:
        reid_project = self._project(project)
        if request.format != "native":
            raise TaskOperationError(
                f"reid does not support export format {request.format!r}"
            )
        artifact = reid_project.dataset / latest_pairs(reid_project)
        if not artifact.is_file():
            raise TaskOperationError("reid project has no pairs artifact to export")
        return ExportResult(
            format="reid-pairs-csv",
            artifacts=(artifact,),
            metadata={"dataset": str(reid_project.dataset)},
        )
