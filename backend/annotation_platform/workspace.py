"""Web-managed project workspace: registry, project directories and data import.

Projects are created, edited, filled and deleted only through the web UI, so
this module owns every write the platform makes outside annotation results::

    <workspace>/projects.yaml                the registry (ProjectRegistry format)
    <workspace>/projects/<id>/config.yaml    the task config written from the form
    <workspace>/projects/<id>/data/          the managed dataset root

A project either keeps its images in that managed ``data/`` directory
(uploads, zip imports, ReID extraction) or links an existing server directory
as its dataset root. Nothing here is created until the first write: a
workspace without ``projects.yaml`` is simply empty, and building the app
(``frontend/scripts/export_openapi.py`` does) must not touch the disk.
"""

from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
import unicodedata
import uuid
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Iterable, Mapping

import yaml

from reid_annotation_tool.config import ConfigError as ReIDConfigError

from .local_files import atomic_write_bytes, file_lock
from .project_forms import (
    MODEL_PATH_KEYS,
    ManagementError,
    check_locks,
    check_pipeline_script,
    check_server_only,
    coerce_settings,
    current_values,
    describe_field,
    get_dotted,
    initial_values,
    set_dotted,
    task_type_spec,
)
from .project_registry import (
    PROJECT_ID,
    ProjectRegistry,
    ProjectRegistryError,
    RegisteredProject,
)
from .task_types import (
    ExportRequest,
    TaskOperationError,
    TaskTypeModule,
    TaskTypeRegistry,
    UnknownTaskTypeError,
)

WORKSPACE_ENV = "ANNOTATION_WORKSPACE"
IMPORT_ROOTS_ENV = "ANNOTATION_IMPORT_ROOTS"
DEFAULT_WORKSPACE = "workspace"
REGISTRY_NAME = "projects.yaml"
PROJECTS_DIR = "projects"
CONFIG_NAME = "config.yaml"
DATA_DIR = "data"
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
MAX_NAME_LENGTH = 200
MAX_ID_LENGTH = 48
EXPORT_FORMAT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def configured_import_roots() -> tuple[str, ...]:
    raw = os.environ.get(IMPORT_ROOTS_ENV, "")
    return tuple(value for value in raw.split(os.pathsep) if value.strip())


def _import_roots(values: Iterable[str | Path]) -> tuple[Path, ...]:
    roots: list[Path] = []
    for value in values:
        path = Path(str(value).strip()).expanduser()
        # A relative root would mean something different per working
        # directory, which is the wrong kind of surprise for a security bound.
        if not path.is_absolute():
            raise ValueError(f"{IMPORT_ROOTS_ENV} entries must be absolute paths: {value!r}")
        resolved = path.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def slugify(name: str, fallback: str = "project") -> str:
    """Lowercase ASCII project id stem.

    Non-Latin names (e.g. Chinese) have no ASCII letters left; they fall back
    to the task type so ids stay telling (``detection``, ``reid-2``) instead of
    a row of ``project-N``.
    """
    ascii_text = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug[:MAX_ID_LENGTH].rstrip("-") or fallback


def image_path_parts(relative: str) -> tuple[str, ...]:
    """Validate a dataset-relative image path from an upload or archive entry.

    Hidden segments are refused outright: they are where the platform keeps
    its own state (``.annotations``, ``.jobs``, depth baselines), and an
    upload must never be able to overwrite annotation results.
    """
    if (
        not relative
        or relative.startswith("/")
        or "\\" in relative
        or "\x00" in relative
    ):
        raise ManagementError("invalid_path", f"invalid relative path {relative!r}")
    parts = tuple(relative.split("/"))
    if any(not part or part in {".", ".."} or part.startswith(".") for part in parts):
        raise ManagementError(
            "invalid_path",
            f"invalid relative path {relative!r}: empty, `..` or hidden segments are not allowed",
        )
    name = Path(parts[-1])
    if name.suffix.lower() not in IMAGE_SUFFIXES:
        raise ManagementError(
            "unsupported_file",
            f"only images can be imported ({', '.join(sorted(IMAGE_SUFFIXES))}): {relative!r}",
        )
    # Stored with a lowercase extension: the default `patterns` globs are
    # lowercase and case-sensitive on Linux, so IMG_0001.JPG would otherwise
    # be imported yet never show up in the queue.
    return (*parts[:-1], name.stem + name.suffix.lower())


def is_image_content(head: bytes) -> bool:
    """Whether leading bytes carry a JPEG, PNG or WebP signature.

    Only the signature, not a decode (the backend never decodes images): it is
    enough to stop arbitrary payloads -- a pickle named `weights.jpg` -- from
    being planted in the dataset under an image extension.
    """
    return (
        head.startswith(b"\xff\xd8\xff")
        or head.startswith(b"\x89PNG\r\n\x1a\n")
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )


def _not_an_image(name: str) -> ManagementError:
    return ManagementError(
        "unsupported_file", f"{name!r} is not a JPEG, PNG or WebP image"
    )


async def spool(chunks: AsyncIterator[bytes], directory: Path, suffix: str) -> tuple[Path, int]:
    """Stream a request body to a hidden temporary file in ``directory``.

    Same directory as the destination so the final rename is atomic; the
    ``.part``-style suffix keeps a half-written upload out of image globs.
    """
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f".upload-{uuid.uuid4().hex}{suffix}"
    size = 0
    try:
        with temporary.open("wb") as handle:
            async for chunk in chunks:
                handle.write(chunk)
                size += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary, size


@dataclass(frozen=True)
class ExportDownload:
    path: Path
    filename: str
    # True when `path` is a zip built for this response and must be removed
    # once it has been sent.
    temporary: bool


class Workspace:
    """The registry file plus the project tree next to it."""

    def __init__(
        self,
        location: str | Path,
        task_types: TaskTypeRegistry,
        import_roots: Iterable[str | Path] | None = None,
    ):
        path = Path(location).expanduser().resolve()
        # Backward compatible with the hand-written registry era: a YAML file
        # is the registry itself and its directory is the workspace.
        self.explicit_registry = path.is_file() or path.suffix.lower() in {".yaml", ".yml"}
        self.registry_path = path if self.explicit_registry else path / REGISTRY_NAME
        self.root = self.registry_path.parent
        self.projects_dir = self.root / PROJECTS_DIR
        self.task_types = task_types
        self.import_roots = _import_roots(
            configured_import_roots() if import_roots is None else import_roots
        )
        self._lock = file_lock(self.registry_path)

    # ------------------------------------------------------------- reading

    def load_registry(self) -> ProjectRegistry:
        if not self.explicit_registry and not self.registry_path.exists():
            # A fresh workspace has no registry until the first project exists.
            return ProjectRegistry([], self.registry_path)
        return ProjectRegistry.load(self.registry_path, self.task_types)

    def _registry(self) -> ProjectRegistry:
        try:
            return self.load_registry()
        except ProjectRegistryError as error:
            raise ManagementError("registry_invalid", str(error), status=500) from error

    def _entry(self, project_id: str) -> RegisteredProject:
        try:
            return self._registry().get_entry(project_id)
        except ProjectRegistryError as error:
            raise ManagementError("project_not_found", str(error), status=404) from error

    def managed_dir(self, project_id: str) -> Path:
        return self.projects_dir / project_id / DATA_DIR

    def _is_managed(self, entry: RegisteredProject) -> bool:
        return entry.project.root.resolve() == self.managed_dir(entry.id).resolve()

    def allowed_roots(self) -> list[str]:
        """Where a directory may be linked from: the workspace, then the env list."""
        roots = [self.root.resolve(), *self.import_roots]
        return [str(root) for root in dict.fromkeys(roots)]

    def settings(self, project_id: str) -> dict:
        return self._settings_view(self._entry(project_id))

    def _settings_view(self, entry: RegisteredProject) -> dict:
        spec = task_type_spec(entry.task_type)
        text = _read_text(entry.config_path)
        document = _parse_mapping(text)
        return {
            "id": entry.id,
            "name": entry.name,
            "task_type": entry.task_type,
            "values": current_values(spec, document),
            "fields": [] if spec is None else [describe_field(item) for item in spec.fields],
            "config_path": str(entry.config_path),
            "config_text": text,
            "data_source": {
                "mode": "managed" if self._is_managed(entry) else "directory",
                "path": str(entry.project.root),
            },
            "import_roots": self.allowed_roots(),
            "annotated": _annotated(entry),
        }

    # ------------------------------------------------------ create / edit

    def create_project(
        self, name: str, task_type: str, settings: Mapping[str, object]
    ) -> RegisteredProject:
        name = _project_name(name)
        spec = task_type_spec(task_type)
        try:
            module = self.task_types.require(task_type)
        except UnknownTaskTypeError as error:
            raise ManagementError("unknown_task_type", str(error)) from error
        if spec is None:
            raise ManagementError(
                "unknown_task_type", f"task type {task_type!r} cannot be created from the web UI"
            )
        document: dict = {"dataset": f"./{DATA_DIR}"}
        for key, value in initial_values(spec, settings).items():
            set_dotted(document, key, value)
        with self._lock:
            entries = self._raw_entries()
            project_id = self._unique_id(slugify(name, fallback=task_type), entries)
            project_dir = self.projects_dir / project_id
            self._check_model_paths({}, document, project_dir)
            # No exist_ok: the directory is ours to clean up only if we made it.
            project_dir.mkdir(parents=True)
            config_path = project_dir / CONFIG_NAME
            try:
                (project_dir / DATA_DIR).mkdir()
                project = _write_validated(module, config_path, _dump(document))
                entries.append(
                    {
                        "id": project_id,
                        "name": name,
                        "task_type": task_type,
                        "config": f"{PROJECTS_DIR}/{project_id}/{CONFIG_NAME}",
                    }
                )
                self._write_registry(entries)
            except BaseException:
                shutil.rmtree(project_dir, ignore_errors=True)
                raise
        return RegisteredProject(project_id, name, project.config_path, project, module)

    def update_settings(
        self,
        project_id: str,
        name: str | None = None,
        settings: Mapping[str, object] | None = None,
    ) -> dict:
        new_name = None if name is None else _project_name(name)
        with self._lock:
            entry = self._entry(project_id)
            if settings:
                spec = task_type_spec(entry.task_type)
                document = _read_mapping(entry.config_path)
                before = current_values(spec, document)
                # Values the form sends back unchanged are always accepted, so a
                # hand-configured value outside the form's options (a custom
                # pipeline script) does not block saving other fields.
                changed = {
                    key: value
                    for key, value in settings.items()
                    if key not in before or value != before[key]
                }
                updated = copy.deepcopy(document)
                for key, value in coerce_settings(spec, changed).items():
                    set_dotted(updated, key, value)
                check_server_only(spec, document, updated)
                self._check_model_paths(document, updated, entry.config_path.parent)
                if updated != document:
                    if _annotated(entry):
                        check_locks(spec, before, current_values(spec, updated))
                    _write_validated(entry.module, entry.config_path, _dump(updated))
            if new_name is not None and new_name != entry.name:
                self._rename(entry.id, new_name)
            return self._settings_view(self._entry(project_id))

    def update_config_text(self, project_id: str, text: str) -> dict:
        with self._lock:
            entry = self._entry(project_id)
            spec = task_type_spec(entry.task_type)
            try:
                updated = yaml.safe_load(text)
            except yaml.YAMLError as error:
                raise ManagementError("invalid_config", f"config is not valid YAML: {error}") from error
            if not isinstance(updated, dict):
                raise ManagementError("invalid_config", "config must be a YAML mapping")
            document = _read_mapping(entry.config_path)
            # Where the data and the results live is decided by import/link,
            # never by an editor: moving either would silently orphan results.
            for key in ("dataset", "annotations"):
                if updated.get(key) != document.get(key):
                    raise ManagementError(
                        "protected_setting",
                        f"`{key}` cannot be changed in the config editor",
                    )
            check_server_only(spec, document, updated)
            self._check_model_paths(document, updated, entry.config_path.parent)
            if spec is not None and spec.find_field("pipeline.script") is not None:
                check_pipeline_script(document, updated)
            if _annotated(entry):
                check_locks(spec, current_values(spec, document), current_values(spec, updated))
            # Written verbatim so the user's comments and layout survive.
            _write_validated(entry.module, entry.config_path, text)
            return self._settings_view(self._entry(project_id))

    def delete_project(self, project_id: str) -> None:
        with self._lock:
            entries = self._raw_entries()
            entry = next(
                (item for item in entries if isinstance(item, dict) and item.get("id") == project_id),
                None,
            )
            if entry is None:
                raise ManagementError(
                    "project_not_found", f"unknown project {project_id!r}", status=404
                )
            owned = self._owned_project_dir(project_id, entry.get("config"))
            # Registry first: a failed removal then leaves an orphan directory
            # (which also blocks reusing the id), never a dangling entry.
            self._write_registry([item for item in entries if item is not entry])
            if owned is not None:
                try:
                    shutil.rmtree(owned)
                except OSError as error:
                    raise ManagementError(
                        "delete_failed",
                        f"project unregistered but {owned} could not be removed: {error}",
                        status=500,
                    ) from error

    def _owned_project_dir(self, project_id: str, config: object) -> Path | None:
        """The project directory, only when the platform itself created it.

        Hand-registered projects (config elsewhere) are only unregistered: their
        files -- and any linked dataset -- are never the platform's to delete.
        """
        if not PROJECT_ID.fullmatch(project_id) or not isinstance(config, str):
            return None
        project_dir = self.projects_dir / project_id
        if project_dir.is_symlink() or not project_dir.is_dir():
            return None
        configured = Path(config).expanduser()
        if not configured.is_absolute():
            configured = self.registry_path.parent / configured
        projects = self.projects_dir.resolve()
        if (
            project_dir.resolve().parent != projects
            or configured.resolve() != (projects / project_id / CONFIG_NAME)
        ):
            return None
        return project_dir

    def _check_model_paths(self, before: Mapping, after: Mapping, config_dir: Path) -> None:
        """Refuse model/checkpoint paths that point into the project tree.

        Threat model: the API is unauthenticated, and `<workspace>/projects/`
        is the one place an HTTP client can put bytes of its choosing (uploads,
        zip imports, extraction output). Torch checkpoints are pickles, so a
        detector/ReID/checkpoint path aimed there would let a client upload a
        payload and have the platform (or the trainer it launches) execute it.
        Paths elsewhere were put there by someone with server access and stay
        editable. Only new or changed values are checked, so a value an
        administrator set by hand never blocks saving other settings.
        """
        old = _model_paths(before)
        projects = self.projects_dir.resolve()
        for key, value in _model_paths(after).items():
            if not isinstance(value, str) or not value.strip() or value == old.get(key):
                continue
            path = Path(value.strip()).expanduser()
            resolved = (path if path.is_absolute() else config_dir / path).resolve()
            if resolved == projects or projects in resolved.parents:
                raise ManagementError(
                    "path_not_allowed",
                    f"`{key}` must not point into the workspace project tree ({projects}): "
                    "files there can be uploaded over the API, and loading a model "
                    "file executes code",
                )

    # -------------------------------------------------------------- import

    def upload_root(self, project_id: str) -> Path:
        """The managed data directory files may be imported into."""
        entry = self._entry(project_id)
        spec = task_type_spec(entry.task_type)
        if spec is None or "upload" not in spec.import_modes:
            raise ManagementError(
                "upload_not_supported",
                f"{entry.task_type} projects cannot import files; link a directory instead",
            )
        if not self._is_managed(entry):
            raise ManagementError(
                "not_managed",
                "this project uses a linked directory; add files to that directory instead",
                status=409,
            )
        root = self.managed_dir(entry.id)
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()

    def upload_target(self, project_id: str, relative: str) -> tuple[Path, str]:
        """The destination file and its normalized dataset-relative path."""
        root = self.upload_root(project_id)
        parts = image_path_parts(relative)
        target = root.joinpath(*parts)
        _prepare_parent(root, target)
        return target, "/".join(parts)

    def link_directory(self, project_id: str, path: str) -> dict:
        with self._lock:
            registry = self._registry()
            try:
                entry = registry.get_entry(project_id)
            except ProjectRegistryError as error:
                raise ManagementError("project_not_found", str(error), status=404) from error
            spec = task_type_spec(entry.task_type)
            if spec is None or "directory" not in spec.import_modes:
                raise ManagementError(
                    "link_not_supported", f"{entry.task_type} projects cannot link a directory"
                )
            target = self._linkable_directory(path)
            if self._is_managed(entry) and _has_files(self.managed_dir(entry.id)):
                raise ManagementError(
                    "managed_data_present",
                    "this project already holds imported files; a directory can only "
                    "be linked while its managed data directory is empty",
                    status=409,
                )
            # The registry rejects two projects sharing a dataset root, which
            # would take the whole project list down; refuse before writing.
            for other in registry.entries():
                if other.id != entry.id and other.project.root.resolve() == target:
                    raise ManagementError(
                        "dataset_in_use",
                        f"{target} is already the dataset of project {other.id!r}",
                        status=409,
                    )
            document = _read_mapping(entry.config_path)
            document["dataset"] = str(target)
            _write_validated(entry.module, entry.config_path, _dump(document))
            return self._settings_view(self._entry(project_id))

    def _linkable_directory(self, value: str) -> Path:
        path = Path(value.strip()).expanduser()
        if not value.strip() or not path.is_absolute():
            raise ManagementError("invalid_directory", f"path must be absolute: {value!r}")
        resolved = path.resolve()
        # Checked before existence so the API is not an oracle for which
        # directories exist elsewhere on the server.
        if not self._may_link(resolved):
            raise ManagementError(
                "path_not_allowed",
                f"{resolved} is outside the directories that may be linked "
                f"({', '.join(self.allowed_roots())}); an administrator can allow more "
                f"with {IMPORT_ROOTS_ENV}",
            )
        if not resolved.is_dir():
            raise ManagementError("invalid_directory", f"not an existing directory: {resolved}")
        return resolved

    def _may_link(self, resolved: Path) -> bool:
        # The files endpoint serves everything under a dataset root without
        # authentication, so a link is confined to the workspace and to roots
        # an administrator listed. The workspace's own project tree is the
        # platform's: linking into it would share files between projects, and
        # linking an ancestor of it would serve the registry and every project.
        projects = self.projects_dir.resolve()
        if resolved == projects or projects in resolved.parents or resolved in projects.parents:
            return False
        return any(
            resolved == root or root in resolved.parents
            for root in (self.root.resolve(), *self.import_roots)
        )

    # -------------------------------------------------------------- export

    def export(self, project_id: str, export_format: str) -> ExportDownload:
        if not EXPORT_FORMAT.fullmatch(export_format):
            raise ManagementError("export_failed", f"invalid export format {export_format!r}")
        entry = self._entry(project_id)
        try:
            result = entry.module.export(entry.project, ExportRequest(format=export_format))
        except (TaskOperationError, ReIDConfigError) as error:
            raise ManagementError("export_failed", str(error)) from error
        artifacts = tuple(Path(path) for path in result.artifacts)
        if not artifacts:
            raise ManagementError("export_failed", "the export produced no files")
        missing = [str(path) for path in artifacts if not path.is_file()]
        if missing:
            raise ManagementError("export_failed", f"export artifacts are missing: {missing}")
        stem = f"{entry.id}-{export_format}"
        if len(artifacts) == 1:
            return ExportDownload(artifacts[0], stem + artifacts[0].suffix, temporary=False)
        return ExportDownload(_zip(artifacts, entry.project.root), stem + ".zip", temporary=True)

    # ------------------------------------------------------------ registry

    def _raw_entries(self) -> list:
        if not self.registry_path.exists():
            return []
        try:
            raw = yaml.safe_load(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise ManagementError(
                "registry_invalid", f"cannot read {self.registry_path}: {error}", status=500
            ) from error
        if not isinstance(raw, dict) or set(raw) != {"projects"} or not isinstance(
            raw["projects"], list
        ):
            raise ManagementError(
                "registry_invalid",
                f"{self.registry_path}: expected only a `projects` list",
                status=500,
            )
        return list(raw["projects"])

    def _write_registry(self, entries: list) -> None:
        text = yaml.safe_dump({"projects": entries}, allow_unicode=True, sort_keys=False)
        atomic_write_bytes(self.registry_path, text.encode("utf-8"))

    def _rename(self, project_id: str, name: str) -> None:
        entries = self._raw_entries()
        for item in entries:
            if isinstance(item, dict) and item.get("id") == project_id:
                item["name"] = name
        self._write_registry(entries)

    def _unique_id(self, stem: str, entries: list) -> str:
        # Case-insensitive, so ids stay distinct on case-folding filesystems.
        taken = {
            str(item.get("id", "")).lower() for item in entries if isinstance(item, dict)
        }
        candidate, counter = stem, 2
        while candidate in taken or (self.projects_dir / candidate).exists():
            suffix = f"-{counter}"
            candidate = stem[: MAX_ID_LENGTH - len(suffix)].rstrip("-") + suffix
            counter += 1
        return candidate


# ------------------------------------------------------------------ helpers


def _project_name(value: str) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if not name:
        raise ManagementError("invalid_name", "project name must not be empty")
    if len(name) > MAX_NAME_LENGTH:
        raise ManagementError(
            "invalid_name", f"project name must be at most {MAX_NAME_LENGTH} characters"
        )
    return name


def _model_paths(document: Mapping) -> dict[str, object]:
    values = {key: get_dotted(document, key) for key in MODEL_PATH_KEYS}
    models = document.get("models")
    if isinstance(models, Mapping):
        for name, model in models.items():
            if isinstance(model, Mapping):
                values[f"models.{name}.path"] = model.get("path")
    return values


def _annotated(entry: RegisteredProject) -> bool:
    """Whether any result exists that a settings change could reinterpret."""
    details = entry.module.status(entry.project).details
    total, pending = details.get("total"), details.get("pending")
    if isinstance(total, int) and isinstance(pending, int):
        return total - pending > 0
    labelled = details.get("labelled")  # ReID: answered rows of the live round
    return isinstance(labelled, int) and labelled > 0


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise ManagementError("invalid_config", f"cannot read {path}: {error}", status=500) from error


def _parse_mapping(text: str) -> dict:
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    return value if isinstance(value, dict) else {}


def _read_mapping(path: Path) -> dict:
    return _parse_mapping(_read_text(path))


def _dump(document: Mapping) -> str:
    # Not comment-preserving (PyYAML), like reid_annotation_tool.config.dump;
    # the raw config editor writes text verbatim when comments matter.
    return yaml.safe_dump(dict(document), allow_unicode=True, sort_keys=False)


def _write_validated(module: TaskTypeModule, config_path: Path, text: str):
    """Replace a config only after the task module accepted the candidate.

    The candidate sits next to the real file, so relative paths in it resolve
    exactly as they will after the rename, and a rejected edit can never leave
    the live config half-written.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    candidate = config_path.with_name(config_path.name + ".validate.tmp")
    try:
        with candidate.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            module.load(candidate)
        except (TaskOperationError, ReIDConfigError, yaml.YAMLError, OSError, ValueError) as error:
            message = str(error).replace(str(candidate), str(config_path))
            raise ManagementError("invalid_config", message) from error
        candidate.replace(config_path)
    except BaseException:
        candidate.unlink(missing_ok=True)
        raise
    return module.load(config_path)


def _prepare_parent(root: Path, target: Path) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ManagementError("invalid_path", f"cannot create {target.parent}: {error}") from error
    parent = target.parent.resolve()
    # A symlinked subdirectory must not carry an import out of the dataset.
    if parent != root and root not in parent.parents:
        raise ManagementError("invalid_path", f"{target} is outside the project data directory")
    if target.is_dir():
        raise ManagementError("invalid_path", f"{target} is a directory")


def place(temporary: Path, target: Path) -> None:
    """Check the content and atomically move a written temporary file into place."""
    with temporary.open("rb") as handle:
        head = handle.read(12)
    if not is_image_content(head):
        temporary.unlink(missing_ok=True)
        raise _not_an_image(target.name)
    try:
        temporary.replace(target)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ManagementError("invalid_path", f"cannot write {target}: {error}") from error


def _has_files(directory: Path) -> bool:
    for _, _, files in os.walk(directory):
        if files:
            return True
    return False


def extract_archive(archive: Path, root: Path) -> tuple[int, int]:
    """Copy the image entries of a zip into ``root``; returns (imported, skipped).

    Entries are filtered with the same rules as single uploads, which also
    drops zip-slip names (``../x.jpg``, absolute paths) and macOS metadata.
    Content is always written as a regular file, so symlink entries cannot
    point anywhere either.
    """
    try:
        bundle = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as error:
        raise ManagementError("invalid_archive", f"not a readable zip archive: {error}") from error
    imported = skipped = 0
    with bundle:
        for info in bundle.infolist():
            if info.is_dir():
                continue
            if "__MACOSX" in info.filename.split("/"):
                skipped += 1
                continue
            try:
                target = root.joinpath(*image_path_parts(info.filename))
                with bundle.open(info) as source:
                    if not is_image_content(source.read(12)):
                        raise _not_an_image(info.filename)
                _prepare_parent(root, target)
            except (ManagementError, OSError, RuntimeError, ValueError, zipfile.BadZipFile, zlib.error):
                skipped += 1
                continue
            temporary = target.parent / f".upload-{uuid.uuid4().hex}.part"
            try:
                with bundle.open(info) as source, temporary.open("wb") as handle:
                    shutil.copyfileobj(source, handle, 1024 * 1024)
                temporary.replace(target)
            except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, zlib.error):
                # Encrypted, corrupt or unsupported entries are skipped, not
                # fatal: the rest of the archive is still worth importing.
                temporary.unlink(missing_ok=True)
                skipped += 1
                continue
            imported += 1
    return imported, skipped


def _zip(artifacts: tuple[Path, ...], root: Path) -> Path:
    handle, name = tempfile.mkstemp(prefix="annotation-export-", suffix=".zip")
    os.close(handle)
    output = Path(name)
    base = root.resolve()
    try:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for artifact in artifacts:
                resolved = artifact.resolve()
                arcname = (
                    resolved.relative_to(base).as_posix()
                    if base in resolved.parents
                    else resolved.name
                )
                bundle.write(resolved, arcname)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return output
