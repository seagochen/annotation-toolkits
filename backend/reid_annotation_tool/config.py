"""One project file for the whole workbench, and the defaults behind it.

Every stage used to be reached by a command line carrying dozens of flags, and
the daily checks additionally made the reviewer retype the same four paths —
getting ``--review`` wrong there silently changed which answers
the conflict engine could see. So the paths are stated once, the tuning lives
in named sections with defaults that live in code, and the stages take no
arguments at all.

What is genuinely per-invocation stays on the command line; everything else is
a key in ``reid.yaml``. Sections are grouped by *who owns the decision*:

``dataset``     where the dataset lives; every stage derives its paths from it
``pipeline``    the user's own detection/tracking script and its knobs
``extract``     which recordings, how densely, how time and splits are read
``projection``  the camera calibration for the head->body crop geometry
``crops``       what makes a crop trustworthy: purity, quality, sampling
``pairs``       what makes two crops evidence
``mine``        candidate ranking
``train``       the handoff to an external trainer (paths only, never its hyperparameters)
``evaluate``    the handoff to an external evaluator (paths only, never a metric definition)
``models``      named model checkpoints a pipeline script may look up by name

Defaults are the values this tool shipped as CLI defaults, so an existing
project keeps behaving the same once its flags move into the file.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import yaml

from .core import atomic_write_text, read_csv

CONFIG_NAMES = ("reid.yaml", "reid.yml", "reid-annotation.yaml")
CONFIG_ENV = "REID_CONFIG"

# Section defaults. A key that is not here is rejected on load: a silently
# ignored typo in a threshold is exactly the kind of mistake that produces a
# quietly wrong dataset.
DEFAULTS: dict[str, dict] = {
    "pipeline": {"script": ""},
    "extract": {
        "video": [], "videos_dir": "", "pattern": "*.mp4", "out": "",
        "frame_stride": 1, "max_frames": 0, "track_gap": 15,
        "jpeg_quality": 95, "seed": 20260824,
    },
    "splits": {
        "split": "", "day_split": [], "split_ratios": [0.7, 0.15, 0.15],
        "video_gap_sec": 86400.0, "timestamp_regex": "", "timestamp_source": "filename",
        "timestamp_format": "%Y%m%d%H%M%S", "video_id_regex": "",
    },
    "projection": {
        "projection_space": [1200.0, 538.0], "seam_x": 600.0,
        "nadir_left": [587.7, 455.2], "nadir_right": [612.3, 455.2],
        "extent_k": [1.0, 2.5, 5.0], "extent_r_ref": 250.0,
    },
    "crops": {
        "crop_margin": 0.0, "min_crop_side": 48, "min_blur": 60.0,
        "min_brightness": 30.0, "max_brightness": 225.0, "max_exposure_clip": 0.50,
        "max_neighbour_iou": 0.10, "max_neighbour_contained": 0.35,
        "max_motion_shift": 0.75, "min_scale_ratio": 0.5, "max_scale_ratio": 2.0,
        "sample_interval": 0.75, "max_track_crops": 12, "min_track_crops": 4,
        "min_track_frames": 15, "min_track_sec": 1.5,
        "no_crop_firewall": False, "crop_conf": 0.02, "crop_primary_conf": 0.30,
        "max_crop_overlap": 0.30, "min_crop_relative_area": 0.0,
        "reject_track_multi_crops": 1,
    },
    "pairs": {
        "max_covisible_iou": 0.05, "min_covisible_frames": 5,
        "min_positive_gap": 1.0, "max_positive_pairs": 20, "negative_ratio": 1.0,
    },
    "mine": {
        # Only train may be extended by reviewed labels. Val/test can still be
        # requested explicitly for audit-only rounds.
        "output_dir": "", "splits": ["train"], "min_cosine": 0.80,
        "min_gap_sec": 5.0, "per_split": 200, "max_per_identity": 4,
        "purity_per_split": 50, "purity_max_cosine": 0.55,
        "allow_cross_day": False, "allow_cross_camera": False,
        "reid_onnx": "", "reid_input_size": 224, "reid_preprocess": "letterbox-bgr",
        "reid_provider": "auto", "reid_batch_size": 64,
    },
    # Only the handoff itself. A backbone or a learning rate is not a property
    # of a dataset, so it is not a key of this tool -- see RETIRED below.
    "train": {
        "pairs": "", "trainer": "", "python": "", "name": "reviewed",
        "tasks": "reid", "base_config": "", "set": [], "export": False,
        "allow_conflicts": False, "dry_run": False,
    },
    # The handoff for measuring a trained checkpoint, mirroring `train` above:
    # paths only, never a metric definition -- what counts as a good score is
    # the evaluator's business, not the dataset's.
    "evaluate": {
        "pairs": "", "evaluator": "", "python": "", "checkpoint": "",
        "name": "eval", "split": "test", "tasks": "reid",
        "base_config": "", "set": [], "allow_conflicts": False, "dry_run": False,
    },
    # Named model checkpoints, open like `pipeline` (see OPEN_SECTIONS) because
    # each entry is itself a small mapping, not a flat key -> scalar. The one
    # rule this tool owns -- every entry needs `path` -- is checked at the
    # point of use (`Project.models()`), the same way `pipeline()` is the only
    # place that checks `script` is non-empty.
    "models": {},
}

# The known keys of one `models.<name>` entry. Not in DEFAULTS proper because
# `models` itself holds names, not these keys directly -- see Project.models().
MODEL_DEFAULTS: dict = {"path": "", "framework": "pytorch", "device": "cpu", "kind": ""}

# Keys this tool used to own and deliberately handed back. Carrying one is an
# error like any unknown key, but a bare "unknown key" would leave the reader
# guessing whether it was a typo or a removal, so the error says where it went.
RETIRED: dict[str, tuple[frozenset[str], str]] = {
    "train": (frozenset({
        "backbone", "backbone_lr", "batch_size", "cls_epochs", "cls_lr", "csv_cls",
        "data_cls", "device", "img_size", "no_pretrained", "num_classes",
        "objective", "patience", "pretrained_path", "reid_dim", "reid_epochs",
        "reid_lr", "seed", "workers",
    }), "belong to your trainer, not to the dataset: put them in the YAML named "
        "by `train.base_config`, or pass them with `train.set: [\"section.key=value\"]`"),
}

# Which sections make up each stage's flat argument namespace. The stage code
# still reads plain attributes; the grouping exists for the human editing the
# file, not for the code.
STAGE_SECTIONS = {
    "extract": ("extract", "splits", "projection", "crops", "pairs"),
    "mine": ("mine",),
    "train": ("train",),
    "evaluate": ("evaluate",),
}


# Sections whose keys this tool does not own and therefore cannot validate.
OPEN_SECTIONS = ("pipeline", "models")
RETIRED_SECTIONS = frozenset({"serve"})


class ConfigError(RuntimeError):
    """The project file is missing, malformed or names something unknown."""


def in_directory(directory: Path) -> Path | None:
    for name in CONFIG_NAMES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def find(explicit: str | Path | None = None, start: Path | None = None) -> Path:
    """The project file: the one given, the one $REID_CONFIG names, or the
    nearest one at or above ``start``.

    ``explicit`` may be the file or the directory holding it — the common
    layout is code in one checkout and the config beside the dataset, so
    "point me at the dataset" has to work as well as "point me at the file".
    """
    for reference in (explicit, os.environ.get(CONFIG_ENV)):
        if not reference:
            continue
        path = Path(reference).expanduser()
        if path.is_dir():
            found = in_directory(path)
            if found is None:
                raise ConfigError(f"no {CONFIG_NAMES[0]} in {path}")
            return found.resolve()
        if not path.is_file():
            raise ConfigError(f"config not found: {path}")
        return path.resolve()
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        found = in_directory(directory)
        if found is not None:
            return found
    raise ConfigError(
        f"no {CONFIG_NAMES[0]} in {here} or any parent directory.\n"
        "  数据集里已经有配置的话，直接指过去（文件或目录都行）：\n"
        "      python app.py --config /path/to/dataset\n"
        f"  或者设一次环境变量：  export {CONFIG_ENV}=/path/to/dataset\n"
        "  全新的项目才需要：    python app.py init")


def retired_hint(section: str, keys: list[str]) -> str:
    """Name the keys that were removed rather than mistyped, and where they went."""
    entry = RETIRED.get(section)
    if entry is None:
        return ""
    hit = sorted(set(keys) & entry[0])
    return f"\n  {sorted(hit)} {entry[1]}" if hit else ""


def load(path: Path) -> "Project":
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: the project file must be a YAML mapping")
    unknown = sorted(set(raw) - set(DEFAULTS) - {"dataset"} - RETIRED_SECTIONS)
    if unknown:
        raise ConfigError(f"{path}: unknown sections {unknown}; "
                          f"known: {sorted(['dataset', *DEFAULTS])}")
    merged = {name: {**values} for name, values in DEFAULTS.items()}
    for name, values in raw.items():
        if name == "dataset":
            continue
        if name in RETIRED_SECTIONS:
            continue
        if not isinstance(values, dict):
            raise ConfigError(f"{path}: section `{name}` must be a mapping")
        # `pipeline` is the one open section: its keys belong to the user's
        # script, and this tool has no business knowing them.
        strange = [] if name in OPEN_SECTIONS else sorted(set(values) - set(DEFAULTS[name]))
        if strange:
            raise ConfigError(f"{path}: unknown `{name}` keys {strange}; "
                              f"known: {sorted(DEFAULTS[name])}"
                              + retired_hint(name, strange))
        merged[name].update(values)
    dataset = raw.get("dataset") or ""
    if not dataset:
        raise ConfigError(f"{path}: `dataset:` (the dataset root) is required")
    return Project(path, resolve(path.parent, dataset), merged)


def dump(dataset: str, sections: dict) -> str:
    """Render project sections back to YAML.

    Not comment-preserving -- PyYAML round-trips drop hand-written comments,
    and every default fills in explicitly rather than staying implicit.
    Saving from the web UI is an explicit, flagged trade-off (surfaced in the
    UI); hand-edit the file directly when comments or brevity matter.
    """
    return yaml.dump({"dataset": dataset, **sections}, allow_unicode=True, sort_keys=False)


def save(project: "Project", sections: dict) -> None:
    """Validate a candidate set of sections before ever touching the real file.

    Written to a scratch file first and loaded through the same `load()` this
    tool trusts everywhere else: a rejected edit can never corrupt the live
    config, because the live file is only replaced once the candidate passed.
    """
    raw = yaml.safe_load(project.path.read_text(encoding="utf-8")) or {}
    dataset_value = raw.get("dataset", str(project.dataset))
    text = dump(dataset_value, sections)
    scratch = project.path.with_suffix(project.path.suffix + ".validate.tmp")
    scratch.write_text(text, encoding="utf-8")
    try:
        load(scratch)
    finally:
        scratch.unlink(missing_ok=True)
    atomic_write_text(project.path, text)


def resolve(base: Path, value: str | Path) -> Path:
    """Relative paths are relative to the project file, never to the shell's cwd.

    A config that means something different depending on where it was invoked
    from is a config that will eventually write a dataset into the wrong place.
    """
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def apply_override(sections: dict, override: str) -> None:
    """``section.key=value`` from the command line, parsed as YAML scalar."""
    name, _, rest = override.partition(".")
    key, _, raw = rest.partition("=")
    if not name or not key or not rest.count("="):
        raise ConfigError(f"--set expects section.key=value, got {override!r}")
    if name not in sections:
        raise ConfigError(f"--set: unknown section {name!r}")
    if name not in OPEN_SECTIONS and key not in DEFAULTS[name]:
        raise ConfigError(f"--set: unknown `{name}` key {key!r}"
                          + retired_hint(name, [key]))
    sections[name][key] = yaml.safe_load(raw)


class Project:
    """A loaded project: the dataset root, the sections, and the derived paths.

    Path discovery lives here rather than in every stage because it is the
    thing that used to be retyped and mistyped. ``review/<round>/candidates.csv``
    directories are the rounds; the newest is the live queue and *all* of them
    are the history the conflict engine must see.
    """

    def __init__(self, path: Path, dataset: Path, sections: dict):
        self.path, self.dataset, self.sections = path, dataset, sections

    def resolve(self, value: str | Path) -> Path:
        return resolve(self.path.parent, value)

    @property
    def base_pairs(self) -> Path:
        return self.dataset / "pairs.csv"

    def rounds(self) -> list[Path]:
        """Every review round ever written, oldest answered first.

        Recursive on purpose: real datasets nest rounds by batch
        (``review/batch-01-.../cross_track_review_v2/candidates.csv``), and a
        single-level glob would silently drop those answers from conflict
        detection — the exact failure the discovery was meant to prevent.
        """
        review = self.dataset / "review"
        if not review.is_dir():
            return []
        return sorted((path for path in review.rglob("candidates.csv")),
                      key=lambda path: (path.stat().st_mtime_ns, str(path)))

    def live_round(self) -> Path | None:
        """The queue a reviewer is answering: the newest round with questions left.

        Not by name — round directories are named by batch and purpose
        (``backfill-v1``, ``batch-02-20260828-30``), so alphabetical order says
        nothing about which one is open. And not by mtime alone either: purging,
        archiving and finalize all rewrite old rounds, which would hand the
        reviewer a finished queue and lose their place. What actually defines
        the live round is that it still has unanswered rows; mtime only breaks
        ties among those. When every round is answered, the newest is shown so
        the page still has something to display.
        """
        rounds = self.rounds()
        for path in reversed(rounds):
            if any(not row.get("review_label") for row in read_csv(path)):
                return path
        return rounds[-1] if rounds else None

    def next_round(self) -> str:
        """``review/vN`` for the round `mine` would write next."""
        used = {path.parent.name for path in self.rounds()}
        index = 1
        while f"v{index}" in used:
            index += 1
        return f"review/v{index}"

    def stage(self, name: str) -> SimpleNamespace:
        """Flat namespace for one stage, merged from the sections it owns."""
        values: dict = {}
        for section in STAGE_SECTIONS.get(name, (name,)):
            values.update(self.sections[section])
        return SimpleNamespace(**values)

    def pipeline(self) -> tuple[str, dict]:
        """(script reference, its config) — the boundary declared in one place."""
        values = dict(self.sections["pipeline"])
        script = values.pop("script", "")
        if not script:
            raise ConfigError(
                f"{self.path}: `pipeline.script:` is required — this tool does not "
                "detect or track; name the script that does (or a bundled one: "
                "ultralytics, tracking_csv)")
        # Bare names select bundled pipelines. A user script is a project-file
        # path, so the shell's working directory must not change what is loaded.
        script_path = Path(str(script)).expanduser()
        if script_path.suffix == ".py" or script_path.is_absolute() or len(script_path.parts) > 1:
            script = str(self.resolve(script_path))

        # Only bundled adapters have path keys this host can interpret. Custom
        # pipeline keys remain untouched because their meaning belongs to the script.
        bundled_paths = {
            "tracking_csv": ("file",),
            "ultralytics": ("detector", "reid_onnx"),
        }
        for key in bundled_paths.get(str(script), ()):
            if values.get(key):
                values[key] = str(self.resolve(values[key]))
        return str(script), values

    def models(self) -> dict[str, dict]:
        """Named model registry entries, validated and path-resolved.

        `models` is open in DEFAULTS (see OPEN_SECTIONS) for the same reason
        `pipeline` is: its per-entry shape doesn't fit a flat section. The one
        rule this tool owns -- every entry needs `path` -- is checked here, at
        the point of use, the same way pipeline() checks `script`.
        """
        entries = {}
        for name, values in self.sections["models"].items():
            if not isinstance(values, dict):
                raise ConfigError(f"{self.path}: `models.{name}` must be a mapping")
            strange = sorted(set(values) - set(MODEL_DEFAULTS))
            if strange:
                raise ConfigError(f"{self.path}: unknown `models.{name}` keys {strange}; "
                                  f"known: {sorted(MODEL_DEFAULTS)}")
            merged = {**MODEL_DEFAULTS, **values}
            if not merged["path"]:
                raise ConfigError(f"{self.path}: `models.{name}.path` is required")
            merged["path"] = str(self.resolve(merged["path"]))
            entries[name] = merged
        return entries

    def summary(self) -> dict:
        """What `app.py` prints with no arguments: is this dataset ready, for what."""
        from .core import read_csv

        def count(name: str) -> int:
            path = self.dataset / name
            return len(read_csv(path)) if path.is_file() else 0

        live = self.live_round()
        pending = labelled = 0
        if live is not None:
            rows = read_csv(live)
            labelled = sum(1 for row in rows if row.get("review_label"))
            pending = len(rows) - labelled
        return {
            "config": str(self.path), "dataset": str(self.dataset),
            "exists": self.dataset.is_dir(),
            "identities": count("identities.csv"), "tracks": count("tracks.csv"),
            "pairs": count("pairs.csv"),
            "rounds": [str(path.parent.relative_to(self.dataset)) for path in self.rounds()],
            "live_round": str(live.relative_to(self.dataset)) if live else "",
            "labelled": labelled, "pending": pending,
        }
