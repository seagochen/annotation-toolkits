"""Command-line export and contract validation, outside the web UI.

    python -m annotation_platform.exports contracts
    python -m annotation_platform.exports export --workspace WS PROJECT --format coco --output out.json
    python -m annotation_platform.exports check detection-coco/v1 out.json

``export`` runs exactly what the download endpoint runs (the task module's
export, then contract validation) and copies the result to ``--output``; the
same project state always produces the same bytes. ``check`` validates files
produced anywhere against a contract. Exit status is 0 on success, 1 when a
file does not match its contract, 2 on any other error.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from .export_contracts import BY_ID, CONTRACTS, ContractViolation, validate_files
from .project_forms import ManagementError
from .task_types import default_task_types
from .workspace import DEFAULT_WORKSPACE, WORKSPACE_ENV, Workspace


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m annotation_platform.exports",
        description="Export projects and validate exports against their versioned contracts.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("contracts", help="list every export contract and the formats that produce it")

    export = commands.add_parser("export", help="export a project and validate it against its contract")
    export.add_argument("project", help="project id")
    export.add_argument("--format", default="native", help="export format (default: native)")
    export.add_argument("--output", required=True, type=Path, help="file to write (.zip for multi-file exports)")
    export.add_argument(
        "--workspace",
        default=None,
        help=f"workspace directory or registry YAML (default: ${WORKSPACE_ENV} or ./{DEFAULT_WORKSPACE})",
    )

    check = commands.add_parser("check", help="validate exported files against a contract")
    check.add_argument("contract", help="contract id, e.g. detection-coco/v1")
    check.add_argument("files", nargs="+", type=Path, help="the export's files, in export order")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "contracts":
        formats: dict[str, list[str]] = {}
        for (task_type, export_format), contract in CONTRACTS.items():
            formats.setdefault(contract.id, []).append(f"{task_type}:{export_format}")
        for contract_id in sorted(BY_ID):
            # A superseded version is no longer produced by any format; `check` still accepts it.
            produced_by = ", ".join(formats.get(contract_id, ())) or "(legacy: check only)"
            print(f"{contract_id}\t{produced_by}\t{BY_ID[contract_id].summary}")
        return 0
    if args.command == "check":
        try:
            validate_files(args.contract, args.files)
        except ContractViolation as error:
            print(f"invalid: {error}", file=sys.stderr)
            return 1
        print(f"ok: {args.contract}")
        return 0

    location = args.workspace or os.environ.get(WORKSPACE_ENV, DEFAULT_WORKSPACE)
    workspace = Workspace(location, default_task_types())
    try:
        download = workspace.export(args.project, args.format)
    except ManagementError as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        return 1 if error.code == "export_invalid" else 2
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(download.path, args.output)
    finally:
        if download.temporary:
            download.path.unlink(missing_ok=True)
    print(f"{download.contract}\t{args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
