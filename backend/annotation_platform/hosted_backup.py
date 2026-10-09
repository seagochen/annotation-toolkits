"""Offline backup/restore of the application's complete multi-user data root."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile

MANIFEST = "annotation-backup.json"


def _files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Backup data cannot contain links or special files")
        if path.is_file() and path != root / MANIFEST:
            files.append(path)
    return sorted(files)


def _hash(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root: Path) -> list[dict]:
    return [{"path": file.relative_to(root).as_posix(), "size": file.stat().st_size, "sha256": _hash(file)}
            for file in _files(root)]


def _databases(root: Path) -> dict:
    counts = {}
    for file in _files(root):
        if file.name != "ai-jobs.sqlite":
            continue
        with sqlite3.connect(file.as_uri() + "?mode=ro", uri=True) as connection:
            if (connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or connection.execute("PRAGMA foreign_key_check").fetchall()
                or connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete"):
                raise ValueError("AI database is not a stable, valid SQLite file")
            counts[file.relative_to(root).as_posix()] = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    return counts


def verify(source: Path) -> dict:
    source = source.resolve(strict=True)
    _files(source)
    manifest = source / MANIFEST
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Backup manifest must be a regular file")
    report = json.loads(manifest.read_text(encoding="utf8"))
    if report.get("schemaVersion") != 1 or report.get("moduleId") != "annotation" or not isinstance(report.get("files"), list):
        raise ValueError("Invalid Annotation backup")
    seen = set()
    for entry in report["files"]:
        path = entry.get("path")
        if (not isinstance(path, str) or "\\" in path or PurePosixPath(path).is_absolute()
            or any(part in {"", ".", ".."} for part in path.split("/")) or path in seen or path == MANIFEST):
            raise ValueError("Invalid backup file path")
        seen.add(path)
    if _inventory(source) != report["files"]:
        raise ValueError("Backup files, sizes or checksums do not match")
    if _databases(source) != report.get("databases", {}):
        raise ValueError("Backup database counts do not match")
    return report


def _target(source: Path, target: Path) -> Path:
    resolved = target.resolve()
    if (target.exists() or source == resolved or source.is_relative_to(resolved) or resolved.is_relative_to(source)):
        raise ValueError("Backup/restore requires a new destination outside the source")
    return target.absolute()


def backup(source: Path, destination: Path, *, quiesced: bool = False) -> dict:
    if not quiesced:
        raise ValueError("Stop the application before backing up; --quiesced is required")
    source = source.resolve(strict=True)
    destination = _target(source, destination)
    initial = _inventory(source)
    if (source / MANIFEST).exists():
        raise ValueError("Source already contains a backup manifest")
    stage = Path(tempfile.mkdtemp(prefix=".annotation-backup-", dir=destination.parent))
    try:
        for entry in initial:
            file = stage / entry["path"]
            file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / entry["path"], file)
        report = {"schemaVersion": 1, "moduleId": "annotation", "files": initial, "databases": _databases(source)}
        (stage / MANIFEST).write_text(json.dumps(report, ensure_ascii=False) + "\n", encoding="utf8")
        verify(stage)
        if _inventory(source) != initial:
            raise ValueError("Application data changed during backup")
        stage.rename(destination)
        return report
    finally:
        if stage.exists(): shutil.rmtree(stage)


def restore(source: Path, destination: Path) -> dict:
    source = source.resolve(strict=True)
    report = verify(source)
    destination = _target(source, destination)
    stage = Path(tempfile.mkdtemp(prefix=".annotation-restore-", dir=destination.parent))
    try:
        for entry in report["files"]:
            target = stage / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / entry["path"], target)
        shutil.copyfile(source / MANIFEST, stage / MANIFEST)
        verify(stage)
        (stage / MANIFEST).unlink()
        stage.rename(destination)
        return report
    finally:
        if stage.exists(): shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "verify", "restore"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--quiesced", action="store_true")
    args = parser.parse_args()
    if args.action == "verify":
        report = verify(args.source)
    else:
        if args.destination is None: parser.error("destination is required")
        report = backup(args.source, args.destination, quiesced=args.quiesced) if args.action == "backup" else restore(args.source, args.destination)
    print(json.dumps({"moduleId": report["moduleId"], "files": len(report["files"])}))


if __name__ == "__main__":
    main()
