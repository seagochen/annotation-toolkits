"""Export the local FastAPI schema for the TypeScript client generator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1]
BACKEND = FRONTEND.parent / "backend"
sys.path.insert(0, str(BACKEND))

from annotation_platform.server import create_app  # noqa: E402


def main() -> None:
    target = FRONTEND / "src" / "api" / "openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
