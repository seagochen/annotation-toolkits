"""The one table of ReID stages: names, where each may run, and its options.

The CLI's ``stage`` choices, the background job runner's runnable stages and
the platform's exposed actions are all derived from ``STAGES``; ``app.DISPATCH``
maps the same names to their functions. Adding or renaming a stage is one row
here plus its function in ``app.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

# Every boolean option a stage function may read from ``args``. The CLI defines
# one flag per entry, so stage functions can rely on all of them existing.
BOOLEAN_OPTIONS = ("apply", "dry_run", "strict", "json")


@dataclass(frozen=True)
class Stage:
    name: str
    job: bool = True        # runnable by jobs.JobRunner in the background
    platform: bool = True   # exposed as an action of the platform's reid task type
    options: frozenset[str] = frozenset()  # subset of BOOLEAN_OPTIONS it reads


STAGES = (
    # `status` is cheap enough to stay project metadata; `init` is never a job
    # because registered projects already have a configuration.
    Stage("status", job=False, platform=False),
    Stage("init", job=False, platform=False),
    Stage("extract"),
    Stage("mine"),
    Stage("check", options=frozenset({"strict", "json"})),
    Stage("finalize"),
    Stage("purge-domain", options=frozenset({"apply"})),
    Stage("train", options=frozenset({"dry_run"})),
    # Runs an external evaluator; CLI and job runner only, not a platform action.
    Stage("evaluate", platform=False, options=frozenset({"dry_run"})),
)

STAGE_NAMES = tuple(stage.name for stage in STAGES)
JOB_STAGES = tuple(stage.name for stage in STAGES if stage.job)
PLATFORM_ACTIONS = {stage.name: stage.options for stage in STAGES if stage.platform}
