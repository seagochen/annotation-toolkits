"""stages.STAGES is the one stage table; everything else must agree with it."""

from reid_annotation_tool import app
from reid_annotation_tool.jobs import JOB_STAGES
from reid_annotation_tool.stages import (BOOLEAN_OPTIONS, PLATFORM_ACTIONS, STAGE_NAMES,
                                         STAGES)


def test_every_stage_but_init_has_a_dispatch_function():
    assert set(app.DISPATCH) == set(STAGE_NAMES) - {"init"}


def test_cli_accepts_every_stage_and_defines_every_boolean_option():
    args = app.parser().parse_args([])
    assert all(getattr(args, name) is False for name in BOOLEAN_OPTIONS)
    for name in STAGE_NAMES:
        assert app.parser().parse_args([name]).stage == name


def test_stage_options_and_platform_actions_are_consistent():
    for stage in STAGES:
        assert stage.options <= set(BOOLEAN_OPTIONS), stage.name
    # The platform starts actions through the job runner.
    assert set(PLATFORM_ACTIONS) <= set(JOB_STAGES)
    assert "evaluate" in JOB_STAGES and "evaluate" not in PLATFORM_ACTIONS
