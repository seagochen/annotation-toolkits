"""The kinds of source files a project can hold, and how each one is checked.

A dataset file is either an image (shown with ``<img>``, never decoded by the
backend) or a UTF-8 text document (decoded here, so every task reads the same
characters the browser shows and offsets mean the same thing on both sides).
Uploads are checked against the kinds a task type accepts; a COCO prelabel
file is the one JSON file a project may receive.
"""

from __future__ import annotations

import codecs
import json
from pathlib import Path

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
TEXT_SUFFIXES = frozenset({".txt", ".md"})
JSON_SUFFIXES = frozenset({".json"})

# Upload kind -> the extensions it covers.
KIND_SUFFIXES = {"image": IMAGE_SUFFIXES, "text": TEXT_SUFFIXES, "json": JSON_SUFFIXES}

# A document is sent whole inside a queue item; anything bigger is refused
# with an explicit error instead of stalling the queue.
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
UTF8_BOM = "﻿"


class DocumentError(ValueError):
    """A text document cannot be read as UTF-8 text."""


def is_image_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def media_kind(path: str | Path) -> str:
    """``image`` for image extensions; every other matched file is ``text``."""
    return "image" if is_image_path(path) else "text"


def decode_text(data: bytes) -> str:
    """Strict UTF-8; a leading BOM is dropped and nothing else is changed.

    Line endings are kept as they are: span offsets index this exact string.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DocumentError(
            f"is not valid UTF-8 (byte {error.start}): save it as UTF-8 text"
        ) from error
    if "\x00" in text:
        raise DocumentError("contains NUL bytes; it does not look like a text file")
    return text[1:] if text.startswith(UTF8_BOM) else text


def read_document(path: Path) -> str:
    try:
        size = path.stat().st_size
        if size > MAX_DOCUMENT_BYTES:
            raise DocumentError(
                f"is {size} bytes; text documents are limited to {MAX_DOCUMENT_BYTES} bytes"
            )
        return decode_text(path.read_bytes())
    except OSError as error:
        raise DocumentError(f"cannot be read: {error}") from error


def kind_of(suffix: str) -> str | None:
    suffix = suffix.lower()
    for kind, suffixes in KIND_SUFFIXES.items():
        if suffix in suffixes:
            return kind
    return None


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


def is_text_file(path: Path) -> bool:
    """UTF-8 without NUL bytes, read incrementally so size does not matter."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                if b"\x00" in chunk:
                    return False
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
    except (OSError, UnicodeDecodeError):
        return False
    return True


def is_json_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            json.loads(handle.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    return True


def has_kind_content(path: Path, kind: str) -> bool:
    if kind == "image":
        with path.open("rb") as handle:
            return is_image_content(handle.read(12))
    if kind == "text":
        return is_text_file(path)
    if kind == "json":
        return is_json_file(path)
    return False
