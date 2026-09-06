"""Task-neutral plugin contract and deterministic module registration.

Task modules translate these small platform messages to their own domain model.
They must not require the platform registry or HTTP layer to understand task-specific
labels, queue rows, status fields, or export formats.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable

TASK_TYPE_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
REQUIRED_CAPABILITIES = ("load", "queue", "submit", "status", "export")


class TaskTypeError(RuntimeError):
    """Base error exposed at the task-plugin boundary."""

    code = "task_type_error"


class InvalidTaskTypeModuleError(TaskTypeError):
    """A module name or required capability does not satisfy the contract."""

    code = "invalid_task_type_module"


class DuplicateTaskTypeError(TaskTypeError):
    """Two modules attempted to own the same task type name."""

    code = "duplicate_task_type"


class UnknownTaskTypeError(TaskTypeError):
    """A project refers to a task type that has no registered module."""

    code = "unknown_task_type"


class TaskOperationError(TaskTypeError):
    """A valid module could not perform the requested task operation."""

    code = "task_operation_error"


@dataclass(frozen=True)
class TaskProject:
    """Task-neutral paths plus the module-owned loaded configuration value."""

    config_path: Path
    root: Path
    value: object


@dataclass(frozen=True)
class QueueRequest:
    offset: int = 0
    limit: int = 60
    filters: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.offset < 0:
            raise ValueError("queue offset must be non-negative")
        if self.limit <= 0:
            raise ValueError("queue limit must be positive")


@dataclass(frozen=True)
class QueuePage:
    total: int
    offset: int
    limit: int
    items: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class Submission:
    item_id: str
    result: Mapping[str, object]


@dataclass(frozen=True)
class SubmissionResult:
    item: Mapping[str, object]
    status: "TaskStatus"


@dataclass(frozen=True)
class TaskStatus:
    state: str
    details: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExportRequest:
    format: str = "native"
    options: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExportResult:
    format: str
    artifacts: tuple[Path, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class TaskTypeModule(Protocol):
    """Minimal capability surface implemented by every annotation task type."""

    type_name: str

    def load(self, config_path: Path) -> TaskProject: ...

    def queue(self, project: TaskProject, request: QueueRequest) -> QueuePage: ...

    def submit(self, project: TaskProject, submission: Submission) -> SubmissionResult: ...

    def status(self, project: TaskProject) -> TaskStatus: ...

    def export(self, project: TaskProject, request: ExportRequest) -> ExportResult: ...


class TaskTypeRegistry:
    """Validated collection in which exactly one module owns each type name."""

    def __init__(self, modules: tuple[TaskTypeModule, ...] = ()):
        self._modules: dict[str, TaskTypeModule] = {}
        for module in modules:
            self.register(module)

    def register(self, module: TaskTypeModule) -> None:
        name = getattr(module, "type_name", None)
        if not isinstance(name, str) or not TASK_TYPE_NAME.fullmatch(name):
            raise InvalidTaskTypeModuleError(
                "task type module requires a lowercase `type_name`"
            )
        missing = [
            capability
            for capability in REQUIRED_CAPABILITIES
            if not callable(getattr(module, capability, None))
        ]
        if missing:
            raise InvalidTaskTypeModuleError(
                f"task type {name!r} is missing capabilities: {missing}"
            )
        if name in self._modules:
            raise DuplicateTaskTypeError(f"duplicate task type {name!r}")
        self._modules[name] = module

    def require(self, name: str) -> TaskTypeModule:
        try:
            return self._modules[name]
        except KeyError:
            raise UnknownTaskTypeError(
                f"unknown task type {name!r}; registered: {sorted(self._modules)}"
            ) from None

    def names(self) -> tuple[str, ...]:
        return tuple(self._modules)


def default_task_types() -> TaskTypeRegistry:
    """Build the platform's built-in registry without mutable global state."""
    from .reid_task import ReIDTaskType

    return TaskTypeRegistry((ReIDTaskType(),))
