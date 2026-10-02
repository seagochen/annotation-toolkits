"""The platform's multi-project registry (``projects.yaml``).

Each entry owns only platform metadata (id, name, task type) and the path to
that project's task configuration. Dataset paths and all task settings stay in
that file and are validated by the task type module's ``load()``, so the
registry never interprets task-specific keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .task_types import (
    TaskOperationError,
    TaskProject,
    TaskStatus,
    TaskTypeModule,
    TaskTypeRegistry,
    UnknownTaskTypeError,
    default_task_types,
)

ENTRY_KEYS = frozenset({"id", "name", "task_type", "config"})
PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

class ProjectRegistryError(RuntimeError):
    """The registry is malformed or cannot safely identify a project."""


@dataclass(frozen=True)
class RegisteredProject:
    id: str
    name: str
    config_path: Path
    project: TaskProject
    module: TaskTypeModule

    @property
    def task_type(self) -> str:
        return self.module.type_name

    def describe(self, status: TaskStatus | None = None) -> dict:
        """The ``ProjectListItem`` fields; pass ``status`` to avoid computing it twice."""
        if status is None:
            status = self.module.status(self.project)
        return {
            "id": self.id,
            "name": self.name,
            "task_type": self.task_type,
            "root": str(self.project.root),
            "status": status.state,
        }


class ProjectRegistry:
    """Validated, deterministic collection of locally configured projects."""

    def __init__(self, entries: list[RegisteredProject], path: Path):
        self.path = path
        self._entries = {entry.id: entry for entry in entries}

    @classmethod
    def load(
        cls,
        path: str | Path,
        task_types: TaskTypeRegistry | None = None,
    ) -> ProjectRegistry:
        if task_types is None:
            task_types = default_task_types()
        source = Path(path).expanduser().resolve()
        raw_entries = _read_entries(source)
        entries: list[RegisteredProject] = []
        ids: set[str] = set()
        roots: dict[Path, str] = {}
        for raw, owner in raw_entries:
            entry = _load_entry(raw, owner, task_types)
            if entry.id in ids:
                raise ProjectRegistryError(f"{source}: duplicate project id {entry.id!r}")
            ids.add(entry.id)
            root = entry.project.root.resolve()
            if root in roots:
                raise ProjectRegistryError(
                    f"{source}: projects {roots[root]!r} and {entry.id!r} "
                    f"share dataset root {root}"
                )
            roots[root] = entry.id
            entries.append(entry)
        return cls(entries, source)

    def entries(self) -> tuple[RegisteredProject, ...]:
        """Loaded entries without computing status (which scans every dataset)."""
        return tuple(self._entries.values())

    def list_projects(self) -> list[dict]:
        return [entry.describe() for entry in self._entries.values()]

    def get_entry(self, project_id: str) -> RegisteredProject:
        """Return platform metadata and the loaded task project together."""
        try:
            return self._entries[project_id]
        except KeyError:
            raise ProjectRegistryError(
                f"unknown project {project_id!r}; available: {sorted(self._entries)}"
            ) from None


def _read_entries(source: Path) -> list[tuple[dict, Path]]:
    if source.is_dir():
        files = sorted({*source.glob("*.yaml"), *source.glob("*.yml")})
        if not files:
            raise ProjectRegistryError(f"{source}: no project entry YAML files")
        return [(_read_mapping(path), path) for path in files]
    if not source.is_file():
        raise ProjectRegistryError(f"registry not found: {source}")
    raw = _read_mapping(source)
    if set(raw) != {"projects"}:
        raise ProjectRegistryError(f"{source}: expected only a `projects` list")
    projects = raw["projects"]
    if not isinstance(projects, list):
        raise ProjectRegistryError(f"{source}: `projects` must be a list")
    return [(entry, source) for entry in projects]


def _read_mapping(path: Path) -> dict:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ProjectRegistryError(f"cannot read {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ProjectRegistryError(f"{path}: entry must be a YAML mapping")
    return raw


def _required_text(raw: dict, key: str, owner: Path) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProjectRegistryError(f"{owner}: `{key}` must be a non-empty string")
    return value.strip()


def _load_entry(
    raw: object, owner: Path, task_types: TaskTypeRegistry
) -> RegisteredProject:
    if not isinstance(raw, dict):
        raise ProjectRegistryError(f"{owner}: each project entry must be a mapping")
    unknown = sorted(set(raw) - ENTRY_KEYS)
    if unknown:
        raise ProjectRegistryError(f"{owner}: unknown project keys {unknown}; known: {sorted(ENTRY_KEYS)}")
    project_id = _required_text(raw, "id", owner)
    if not PROJECT_ID.fullmatch(project_id):
        raise ProjectRegistryError(f"{owner}: invalid project id {project_id!r}")
    name = _required_text(raw, "name", owner)
    task_type = _required_text(raw, "task_type", owner)
    try:
        module = task_types.require(task_type)
    except UnknownTaskTypeError as error:
        raise ProjectRegistryError(f"{owner}: {error}") from error
    configured_path = Path(_required_text(raw, "config", owner)).expanduser()
    config_path = (
        configured_path
        if configured_path.is_absolute()
        else (owner.parent / configured_path).resolve()
    )
    if not config_path.is_file():
        raise ProjectRegistryError(f"{owner}: project config not found: {config_path}")
    try:
        project = module.load(config_path)
    except TaskOperationError as error:
        raise ProjectRegistryError(f"{owner}: invalid project config: {error}") from error
    return RegisteredProject(project_id, name, config_path, project, module)
