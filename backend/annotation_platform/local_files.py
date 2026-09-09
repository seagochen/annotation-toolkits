"""Small atomic file primitives shared by local task modules."""

from __future__ import annotations

import csv
import json
import os
import threading
from pathlib import Path
from typing import Iterable, Mapping, Sequence

_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def file_lock(path: Path) -> threading.RLock:
    key = path.resolve()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def atomic_write_json(path: Path, value: object) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    atomic_write_bytes(path, payload.encode("utf-8"))


def atomic_write_csv(
    path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, object]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
