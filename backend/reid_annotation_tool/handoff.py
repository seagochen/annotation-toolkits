"""Hand a reviewed dataset to somebody else's trainer, and record what was fed.

Nothing here trains anything -- there is no model, no loss and no optimiser in
this package, on purpose. This module is the handoff: it refuses to start on a
dataset whose identity logic contradicts itself, tells the trainer where the
dataset and the reviewed pair manifest are, launches it, and writes down the
exact bytes it consumed so a checkpoint can be traced back to the annotations
that produced it.

The interface is four keys, and they are the only ones this tool writes into
the trainer's config (see ``OWNED``): the dataset root, the pair manifest, and
where the run should land. Backbones, objectives, learning rates and epochs are
the trainer's business, not the dataset's -- they come from the user's own
trainer config (``train.base_config``) and ``train.set`` overrides, and this
tool neither defines defaults for them nor validates them. A dataset tool that
shipped an opinion about somebody else's optimiser would be wrong for every
deployment but one, and would quietly rot as their trainer moved on.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

from . import conflicts as conflict_engine
from .config import ConfigError, parse_override
from .core import atomic_write_json, read_csv, sha256

TASK_DIRECTORY = {"reid": "reid", "cls": "cls", "both": "both"}

# The only values this tool writes into the trainer's config, because it is the
# only party that knows them. They are also refused as ``train.set`` overrides:
# a run that trained on a different dataset than its manifest records would make
# the whole provenance chain a lie.
OWNED = (("data", "reid_root"), ("data", "reid_csv"),
         ("output", "project"), ("output", "name"))


def split_label_counts(rows: list[dict]) -> dict[str, dict[str, int]]:
    counts: Counter[tuple[str, int]] = Counter(
        (row["split"], int(row["label"])) for row in rows)
    return {split: {"positive": counts[(split, 1)], "negative": counts[(split, 0)]}
            for split in sorted({row["split"] for row in rows})}


def validate_pairs(path: Path, tasks: str) -> dict:
    rows = read_csv(path)
    required = {"img1", "img2", "label", "split"}
    missing = required - set(rows[0] if rows else {})
    if missing:
        raise SystemExit(f"{path} is missing columns: {sorted(missing)}")
    invalid = [row for row in rows if row.get("label") not in {"0", "1"}]
    if invalid:
        raise SystemExit(f"{path} contains {len(invalid)} unresolved/invalid labels")
    counts = split_label_counts(rows)
    if tasks in {"reid", "both"}:
        for split in ("train", "val"):
            value = counts.get(split, {"positive": 0, "negative": 0})
            if not value["positive"] or not value["negative"]:
                raise SystemExit(
                    f"split={split} needs both positive and negative pairs "
                    f"(found {value}); the trainer cannot evaluate ROC-AUC otherwise")
    return counts


def pair_provenance(path: Path) -> dict:
    """Auditable source counts without changing the trainer's compact pair schema."""
    rows = read_csv(path)
    by_split_evidence = Counter((row.get("split", ""), row.get("evidence", "unknown"))
                                for row in rows)
    return {
        "rows": len(rows),
        "human_reviewed": sum(row.get("evidence", "").startswith("reviewed_") for row in rows),
        "by_split_evidence": {
            split: dict(sorted((evidence, count) for (item_split, evidence), count
                               in by_split_evidence.items() if item_split == split))
            for split in sorted({key[0] for key in by_split_evidence})
        },
    }


def _load_base_config(path, stage: str, receives: str) -> dict:
    """The user's own trainer/evaluator config, or an empty one with a note."""
    if not path:
        print(f"note: {stage}.base_config is empty, so the {receives} and must "
              "supply its own defaults", flush=True)
        return {}
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise SystemExit(f"{path}: base config must be a YAML mapping")
    return config


def _apply_overrides(config: dict, overrides: list[str], stage: str,
                     owned: tuple[tuple[str, str], ...], derived_from: str) -> None:
    """``{stage}.set`` entries onto the config; keys this tool owns are refused."""
    for override in overrides or []:
        try:
            section, key, value = parse_override(override, f"{stage}.set")
        except ConfigError as error:
            raise SystemExit(str(error)) from error
        if (section, key) in owned:
            raise SystemExit(
                f"{section}.{key} is derived from the project ({derived_from}) "
                "and cannot be overridden here")
        config.setdefault(section, {})[key] = value


def build_config(root: Path, args, project: Path) -> dict:
    """The user's own trainer config, plus the four keys only this tool knows.

    Everything else in the file is theirs and is passed through untouched: this
    is a dataset tool, and a backbone or a learning rate is not a property of a
    dataset.
    """
    config = _load_base_config(
        args.base_config, "train",
        "trainer receives only the dataset and output paths")
    _apply_overrides(config, args.set, "train", OWNED,
                     "dataset root, train.pairs, train.name")
    # Written last so no override can point the trainer at another dataset.
    config.setdefault("data", {}).update({"reid_root": str(root), "reid_csv": args.pairs})
    config.setdefault("output", {}).update({"project": str(project), "name": args.name})
    return config


def git_revision(path: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def latest_run(project: Path, tasks: str, name: str) -> Path | None:
    directory = project / TASK_DIRECTORY.get(tasks, tasks)
    if not directory.is_dir():
        return None
    matches = [path for path in directory.iterdir()
               if path.is_dir() and (path.name == name or path.name.startswith(name))]
    return max(matches, key=lambda path: path.stat().st_mtime, default=None)


def run(command: list[str], cwd: Path, sink=None) -> int:
    """Run a subprocess, optionally streaming its combined output line by line.

    ``sink`` is None for every existing caller (the CLI, watching its own
    terminal), so `subprocess.run` is untouched there. The web job runner
    passes a sink to capture output for a job whose console the browser
    cannot see -- see reid_annotation_tool/jobs.py.
    """
    print("running:", " ".join(command), flush=True)
    if sink is None:
        return subprocess.run(command, cwd=str(cwd), check=False).returncode
    process = subprocess.Popen(command, cwd=str(cwd), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in process.stdout:
        line = line.rstrip("\n")
        print(line, flush=True)
        sink(line)
    process.stdout.close()
    return process.wait()


EVAL_OWNED = (("data", "reid_root"), ("data", "reid_csv"), ("checkpoint", "path"),
              ("output", "project"), ("output", "name"), ("output", "metrics_path"))


def build_eval_config(root: Path, args, project: Path, checkpoint: Path) -> dict:
    """Same idea as `build_config`, plus the one thing training doesn't need:

    where the checkpoint being measured lives. `output.metrics_path` exists
    because, unlike a training run's weights directory, there is no natural
    file name to glob for a metrics file -- the host tells the evaluator
    exactly where to write it.
    """
    config = _load_base_config(
        args.base_config, "evaluate",
        "evaluator receives only the dataset/checkpoint/output paths")
    _apply_overrides(config, args.set, "evaluate", EVAL_OWNED,
                     "dataset root, evaluate.pairs/checkpoint/name")

    # Written last so no override can point the evaluator at another dataset
    # or checkpoint.
    config.setdefault("data", {}).update({"reid_root": str(root), "reid_csv": args.pairs})
    config.setdefault("checkpoint", {}).update({"path": str(checkpoint)})
    config.setdefault("output", {}).update({
        "project": str(project), "name": args.name,
        "metrics_path": str(project / "metrics.json"),
    })
    return config


def _pairs_path(root: Path, pairs: str) -> Path:
    path = root / pairs if not Path(pairs).is_absolute() else Path(pairs)
    if not path.is_file():
        raise SystemExit(f"pair CSV not found: {path}")
    return path


def _conflict_gate(root: Path, pairs: Path, args, stage: str, verb: str) -> dict:
    """Refuse to hand over a dataset whose identity logic contradicts itself."""
    reviews = [root / path for path in (args.review or [])]
    gate = conflict_engine.report(root, pairs, [path for path in reviews if path.is_file()])
    if gate["errors"] and not args.allow_conflicts:
        raise SystemExit(
            f"refusing to {verb}: {gate['errors']} identity-logic conflicts\n"
            + conflict_engine.main_text(gate)
            + f"\nresolve them in a new review round, or set "
              f"`{stage}.allow_conflicts: true` to override")
    return gate


def _write_config(workspace: Path, config: dict) -> Path:
    config_path = workspace / "config.yaml"
    config_path.write_text(yaml.dump(config, allow_unicode=True, sort_keys=False),
                           encoding="utf-8")
    print(f"config: {config_path}", flush=True)
    return config_path


def _manifest(root: Path, pairs: Path, counts: dict, inputs: dict, gate: dict,
              tool: tuple[str, Path], command: list[str], config: dict) -> dict:
    """The fields every handoff records; ``inputs`` are the stage's own hashes
    (training: dataset files; evaluation: the checkpoint)."""
    role, path = tool
    return {
        "schema": 1, "created_at": datetime.now().astimezone().isoformat(),
        "dataset_root": str(root), "pairs": str(pairs), "pairs_sha256": sha256(pairs),
        "pair_counts": counts, "pair_provenance": pair_provenance(pairs),
        **inputs,
        "conflict_gate": {key: gate[key]
                          for key in ("errors", "warnings", "pending_reviews", "by_kind")},
        role: str(path), f"{role}_git": git_revision(path),
        "command": command, "config": config,
    }


def _save_dry_run(path: Path, manifest: dict) -> dict:
    manifest["status"] = "dry-run"
    atomic_write_json(path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def evaluate(root: Path, args) -> dict:
    """Hand a checkpoint and the protected test split to somebody else's
    evaluator, and record what was fed -- the measurement-side twin of
    `train()`. Nothing here computes a metric; this tool has no opinion about
    what "good" means for someone else's model.
    """
    evaluator = Path(args.evaluator).resolve()
    entry = evaluator / "scripts" / "evaluate.py"
    if not entry.is_file():
        raise SystemExit(f"evaluator entry point not found: {entry}")
    checkpoint = Path(args.checkpoint)
    if not checkpoint.is_file():
        raise SystemExit(f"checkpoint not found: {checkpoint}")
    pairs = _pairs_path(root, args.pairs)
    gate = _conflict_gate(root, pairs, args, "evaluate", "evaluate")
    counts = split_label_counts(read_csv(pairs))
    value = counts.get(args.split, {"positive": 0, "negative": 0})
    if not value["positive"] or not value["negative"]:
        raise SystemExit(
            f"split={args.split} needs both positive and negative pairs "
            f"(found {value}); the evaluator cannot compute ROC-AUC otherwise")

    workspace = root / "evaluation" / args.name
    workspace.mkdir(parents=True, exist_ok=True)
    project = workspace / "runs"
    config = build_eval_config(root, args, project, checkpoint)
    config_path = _write_config(workspace, config)

    command = [args.python, str(entry), "--config", str(config_path)]
    inputs = {"checkpoint": str(checkpoint), "checkpoint_sha256": sha256(checkpoint)}
    manifest = _manifest(root, pairs, counts, inputs, gate, ("evaluator", evaluator),
                         command, config)
    if args.dry_run:
        return _save_dry_run(workspace / "eval-manifest.json", manifest)

    code = run(command, evaluator, getattr(args, "sink", None))
    manifest["exit_code"] = code
    manifest["status"] = "evaluated" if code == 0 else "failed"
    metrics_path = project / "metrics.json"
    if code == 0 and metrics_path.is_file():
        try:
            manifest["metrics"] = json.loads(metrics_path.read_text(encoding="utf-8"))
            manifest["metrics_sha256"] = sha256(metrics_path)
        except json.JSONDecodeError as error:
            manifest["metrics_error"] = f"{metrics_path}: {error}"
    atomic_write_json(workspace / "eval-manifest.json", manifest)
    print(f"manifest: {workspace / 'eval-manifest.json'}", flush=True)
    return manifest


def train(root: Path, args) -> dict:
    trainer = Path(args.trainer).resolve()
    entry = trainer / "scripts" / "train.py"
    if not entry.is_file():
        raise SystemExit(f"trainer entry point not found: {entry}")
    pairs = _pairs_path(root, args.pairs)
    gate = _conflict_gate(root, pairs, args, "train", "train")
    counts = validate_pairs(pairs, args.tasks)

    workspace = root / "training" / args.name
    workspace.mkdir(parents=True, exist_ok=True)
    project = workspace / "runs"
    config = build_config(root, args, project)
    config_path = _write_config(workspace, config)

    command = [args.python, str(entry), "--config", str(config_path)]
    inputs = {"dataset_hashes": {name: sha256(root / name) for name in
                                 ("identities.csv", "tracks.csv") if (root / name).is_file()}}
    manifest = _manifest(root, pairs, counts, inputs, gate, ("trainer", trainer),
                         command, config)
    if args.dry_run:
        return _save_dry_run(workspace / "training-manifest.json", manifest)

    code = run(command, trainer, getattr(args, "sink", None))
    manifest["exit_code"] = code
    manifest["status"] = "trained" if code == 0 else "failed"
    run_directory = latest_run(project, args.tasks, args.name)
    if run_directory is not None:
        manifest["run_directory"] = str(run_directory)
        weights = sorted((run_directory / "weights").glob("*.pt")) \
            if (run_directory / "weights").is_dir() else []
        manifest["weights"] = {path.name: sha256(path) for path in weights}
        best = next((path for path in weights if path.name == "best_reid.pt"),
                    next((path for path in weights if path.name == "best.pt"),
                         weights[0] if weights else None))
        if code == 0 and args.export and best is not None:
            export = trainer / "scripts" / "export_onnx.py"
            export_command = [args.python, str(export), "--weights", str(best)]
            if args.tasks != "both":
                export_command += ["--tasks", args.tasks]
            manifest["export_command"] = export_command
            manifest["export_exit_code"] = run(export_command, trainer, getattr(args, "sink", None))
            onnx = best.with_suffix(".onnx")
            if onnx.is_file():
                manifest["onnx"] = {"path": str(onnx), "sha256": sha256(onnx)}
                print(f"exported: {onnx}", flush=True)
    atomic_write_json(workspace / "training-manifest.json", manifest)
    print(f"manifest: {workspace / 'training-manifest.json'}", flush=True)
    return manifest
