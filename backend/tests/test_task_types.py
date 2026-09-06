from pathlib import Path

import pytest

from annotation_platform.reid_task import ReIDTaskType
from annotation_platform.task_types import (
    DuplicateTaskTypeError,
    ExportRequest,
    ExportResult,
    InvalidTaskTypeModuleError,
    QueuePage,
    QueueRequest,
    Submission,
    SubmissionResult,
    TaskOperationError,
    TaskProject,
    TaskStatus,
    TaskTypeRegistry,
    UnknownTaskTypeError,
    default_task_types,
)
from reid_annotation_tool.core import read_csv

from conftest import CANDIDATE_FIELDS, candidate, write_csv


class CompleteModule:
    type_name = "demo"

    def load(self, config_path: Path) -> TaskProject:
        return TaskProject(config_path, config_path.parent / "data", {"loaded": True})

    def queue(self, project: TaskProject, request: QueueRequest) -> QueuePage:
        return QueuePage(0, request.offset, request.limit, ())

    def submit(
        self, project: TaskProject, submission: Submission
    ) -> SubmissionResult:
        return SubmissionResult({"id": submission.item_id}, self.status(project))

    def status(self, project: TaskProject) -> TaskStatus:
        return TaskStatus("ready", {"source": str(project.config_path)})

    def export(self, project: TaskProject, request: ExportRequest) -> ExportResult:
        return ExportResult(request.format, ())


def test_registry_rejects_duplicate_unknown_and_incomplete_modules():
    registry = TaskTypeRegistry((CompleteModule(),))
    assert registry.names() == ("demo",)
    assert registry.require("demo").type_name == "demo"

    with pytest.raises(DuplicateTaskTypeError, match="duplicate task type 'demo'"):
        registry.register(CompleteModule())
    with pytest.raises(UnknownTaskTypeError, match="unknown task type 'missing'"):
        registry.require("missing")

    class IncompleteModule:
        type_name = "incomplete"

    with pytest.raises(InvalidTaskTypeModuleError, match="missing capabilities"):
        TaskTypeRegistry((IncompleteModule(),))


def test_registry_rejects_invalid_type_names():
    module = CompleteModule()
    module.type_name = "Not Valid"
    with pytest.raises(InvalidTaskTypeModuleError, match="lowercase"):
        TaskTypeRegistry((module,))


def test_default_registry_contains_reid():
    assert default_task_types().names() == ("reid",)


def test_reid_adapter_reuses_queue_submission_status_and_export(dataset, tmp_path):
    review = dataset / "review" / "v1" / "candidates.csv"
    write_csv(
        review,
        CANDIDATE_FIELDS,
        [candidate("c1", "a", "b"), candidate("c2", "c", "d", "same")],
    )
    config = tmp_path / "reid.yaml"
    config.write_text(
        f"dataset: {dataset}\npipeline:\n  script: tracking_csv\n",
        encoding="utf-8",
    )
    module = ReIDTaskType()
    project = module.load(config)

    page = module.queue(project, QueueRequest(filters={"status": "pending"}))
    assert page.total == 1
    assert page.items[0]["candidate_id"] == "c1"
    assert module.status(project).state == "reviewing"

    result = module.submit(project, Submission("c1", {"label": "different"}))
    assert result.item["review_label"] == "different"
    assert result.status.state == "reviewed"
    assert read_csv(review)[0]["review_label"] == "different"

    exported = module.export(project, ExportRequest())
    assert exported.format == "reid-pairs-csv"
    assert exported.artifacts == (dataset / "pairs.csv",)


def test_reid_adapter_rejects_invalid_submission_and_export(dataset, tmp_path):
    config = tmp_path / "reid.yaml"
    config.write_text(
        f"dataset: {dataset}\npipeline:\n  script: tracking_csv\n",
        encoding="utf-8",
    )
    module = ReIDTaskType()
    project = module.load(config)
    with pytest.raises(TaskOperationError, match="supported `label`"):
        module.submit(project, Submission("c1", {"label": "maybe"}))
    with pytest.raises(TaskOperationError, match="does not support export format"):
        module.export(project, ExportRequest(format="coco"))
