#!/usr/bin/env python3
"""Entry point for the ReID annotation workbench: `python app.py [stage]`.

The launcher itself lives in the package so an installed copy behaves the same
as a checkout; this file only makes the checkout runnable without installing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reid_annotation_tool.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
