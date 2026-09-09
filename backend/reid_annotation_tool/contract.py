"""The boundary between the user's tracking pipeline and this tool.

Detection, identity assignment and tracking are **not** this tool's job. They
depend on the user's models, their scene and their hardware, and a ReID
workbench that shipped its own opinion about them would be wrong for every
deployment but one. So the boundary is a single plain Python file the config
points at, in the same shape as ee-pf-jetson-engine's ``pyline`` scripts: the
host owns the loop and the data contract, the script owns the models.

This tool starts where the tracker stops. It takes the boxes, cuts the crops,
applies the geometry and purity rules that make a crop trustworthy evidence,
builds the dataset structure, exposes it to the annotation platform, and writes the
answers back.

A pipeline script must define::

    process_frame(image, source, config) -> list[Observation]

and may additionally define::

    open_source(source, config)                 # new recording: reset your tracker
    close_source(source, config)                # recording finished
    detect_crops(images, config) -> list[list[Observation]]

A script that wants named models from the project's ``models:`` section
(see ``reid_annotation_tool.registry.ModelRegistry``) declares a fourth
parameter and receives it as a keyword::

    def process_frame(image, source, config, registry) -> list[Observation]:
        weights = registry.resolve_path("detector")

The registry is entirely optional: a plain 3-parameter ``process_frame``
keeps working exactly as before, unchanged. It is passed as a constructor
argument, never folded into ``config`` -- ``Pipeline.describe()`` writes
``config`` straight into ``manifest.json``, and a live registry object
would break that JSON serialization the moment any pipeline used it.

``detect_crops`` is the crop firewall's second pass: the host hands back the
finished crops and the script re-runs its detector on them. The host, not the
script, decides what counts as contamination — that is dataset policy, and it
must not vary per pipeline.

Everything the script returns is in **source pixels** of the frame it was
given. The host never guesses a coordinate space.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

BUNDLED = Path(__file__).resolve().parent / "pipelines"
REQUIRED_HOOK = "process_frame"
OPTIONAL_HOOKS = ("open_source", "close_source", "detect_crops")


@dataclass(slots=True)
class Observation:
    """One object the user's pipeline found in one frame.

    ``track_id`` is what turns an observation into an identity: boxes carrying
    one stay linked across frames and become a ReID track. A box **without**
    one is a bystander — it never becomes an identity, but it still vetoes any
    crop it contaminates, which is how the purity rules work without the host
    owning a detector. Report every object you detected, not only the tracked
    ones, or occlusion goes unnoticed.
    """

    box: np.ndarray
    track_id: int | None = None
    class_id: int = 0
    confidence: float = 1.0

    def __post_init__(self) -> None:
        self.box = np.asarray(self.box, np.float32).reshape(4)


@dataclass(slots=True, frozen=True)
class Source:
    """The recording a frame came from, and where in it the frame sits."""

    name: str            # stable recording id, unique across cameras
    path: str
    index: int           # frame number in the source
    timestamp: float     # seconds; wall-clock when the config supplies a regex
    width: int
    height: int
    fps: float
    split: str


class PipelineError(RuntimeError):
    """A pipeline script is missing, unloadable or broke its contract."""


def resolve(reference: str) -> Path:
    """A path, or the bare name of a bundled pipeline.

    Bundled scripts are the reference implementations, not a fallback the host
    reaches for on its own: nothing is auto-selected, the config always names
    one. ``ultralytics`` and ``tracking_csv`` are the two shipped today.
    """
    direct = Path(reference).expanduser()
    if direct.suffix == ".py" or direct.exists():
        if not direct.is_file():
            raise PipelineError(f"pipeline script not found: {direct}")
        return direct.resolve()
    bundled = BUNDLED / f"{reference}.py"
    if not bundled.is_file():
        shipped = sorted(path.stem for path in BUNDLED.glob("*.py")
                         if not path.stem.startswith("_"))
        raise PipelineError(
            f"unknown pipeline {reference!r}; give a path to a .py file or one "
            f"of the bundled names: {', '.join(shipped)}")
    return bundled


@dataclass
class Pipeline:
    """A loaded pipeline script, with its optional hooks resolved once.

    The digest goes into the dataset manifest beside the calibration: which
    script produced a dataset is part of how that dataset can be reproduced,
    and "the same file name" has never been evidence of the same file.
    """

    path: Path
    module: object
    config: dict = field(default_factory=dict)
    digest: str = ""
    registry: object = None
    _process_frame_wants_registry: bool = False

    @classmethod
    def load(cls, reference: str, config: dict | None = None, registry: object = None,
             ) -> "Pipeline":
        path = resolve(reference)
        name = f"reid_pipeline_{path.stem}"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise PipelineError(f"cannot import pipeline script: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as error:                        # noqa: BLE001 - reported as-is
            raise PipelineError(f"{path} failed to import: {error}") from error
        hook = getattr(module, REQUIRED_HOOK, None)
        if not callable(hook):
            raise PipelineError(
                f"{path} defines no {REQUIRED_HOOK}(image, source, config); "
                "see reid_annotation_tool/pipelines/ for reference scripts")
        return cls(path, module, dict(config or {}),
                   hashlib.sha256(path.read_bytes()).hexdigest(), registry,
                   cls._wants_registry(hook, 3))

    def _hook(self, name: str):
        hook = getattr(self.module, name, None)
        return hook if callable(hook) else None

    @staticmethod
    def _wants_registry(hook, fixed_arity: int) -> bool:
        return len(inspect.signature(hook).parameters) > fixed_arity

    def process_frame(self, image: np.ndarray, source: Source) -> list[Observation]:
        # Precomputed once at load() rather than inspected here: this runs
        # every frame, unlike the rare hooks below.
        if self._process_frame_wants_registry:
            found = self.module.process_frame(image, source, self.config, registry=self.registry)
        else:
            found = self.module.process_frame(image, source, self.config)
        return validate(found, self.path)

    def open_source(self, source: Source) -> None:
        """Runs once per recording -- where the bundled ultralytics pipeline
        actually builds its detector (see its module docstring), so a
        registry-aware detector path has to reach this hook too, not only
        process_frame."""
        hook = self._hook("open_source")
        if hook is None:
            return
        if self._wants_registry(hook, 2):
            hook(source, self.config, registry=self.registry)
        else:
            hook(source, self.config)

    def close_source(self, source: Source) -> None:
        hook = self._hook("close_source")
        if hook is not None:
            hook(source, self.config)

    def detect_crops(self, images: list[np.ndarray]) -> list[list[Observation]] | None:
        """Second-pass detections inside finished crops, or None if unsupported.

        None is not an error: a pipeline that reads pre-computed tracking data
        has no detector to re-run. The host records that the firewall stage was
        unavailable rather than pretending the crops passed it.
        """
        hook = self._hook("detect_crops")
        if hook is None:
            return None
        found_list = (hook(images, self.config, registry=self.registry)
                     if self._wants_registry(hook, 2) else hook(images, self.config))
        return [validate(found, self.path) for found in found_list]

    def describe(self) -> dict:
        """Manifest entry: which script, which bytes, which hooks it provided."""
        return {"script": str(self.path), "sha256": self.digest,
                "hooks": [name for name in OPTIONAL_HOOKS if self._hook(name)],
                "config": self.config}


def validate(found: object, path: Path) -> list[Observation]:
    """Fail at the boundary with the script's name, not three frames later."""
    if found is None:
        return []
    if not isinstance(found, (list, tuple)):
        raise PipelineError(
            f"{path}: process_frame must return a list of Observation, got "
            f"{type(found).__name__}")
    for item in found:
        if not isinstance(item, Observation):
            raise PipelineError(
                f"{path}: returned {type(item).__name__}, expected "
                "reid_annotation_tool.contract.Observation")
    return list(found)
