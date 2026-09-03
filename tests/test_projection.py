"""Pin the head->body projection against the deployed implementation.

GOLDEN was produced on 2026-08-27 by the production deployment's
``surv.identity.state.head_to_body`` at its production calibration (algorithm
space 1200x538, mirrored nadir 587.7/612.3 x 455.2, R_REF 250, K 1.0/2.5/5.0).
That repo is not importable here, so the numbers are frozen instead: if either
side ever changes the formula, this test fails and the drift is caught before a
dataset is built on a geometry the deployment does not share.

The cases deliberately cover both sides of the seam, a head straddling it, heads
near and far from the nadir, a head BELOW the nadir line (where the projection
extends upward), and boxes that clip on the frame edge.
"""

import numpy as np
import pytest

from reid_annotation_tool.projection import BodyProjection

GOLDEN = (
    ((100.0, 60.0, 130.0, 90.0), (100.0, 60.0, 215.086, 158.436)),
    ((300.0, 200.0, 340.0, 245.0), (300.0, 200.0, 404.248, 307.829)),
    ((560.0, 400.0, 600.0, 440.0), (560.0, 400.0, 601.848, 448.448)),
    ((620.0, 410.0, 664.0, 455.0), (612.1592, 410.0, 664.0, 461.129)),
    ((900.0, 150.0, 924.0, 178.0), (856.8432, 150.0, 924.0, 226.9216)),
    ((1150.0, 300.0, 1195.0, 350.0), (998.746, 300.0, 1195.0, 389.06)),
    ((30.0, 470.0, 70.0, 515.0), (30.0, 459.929, 199.048, 515.0)),
    ((596.0, 100.0, 604.0, 110.0), (596.0, 100.0, 604.5904, 131.012)),
)


@pytest.mark.parametrize("head,expected", GOLDEN)
def test_projection_matches_the_deployed_geometry(head, expected):
    body = BodyProjection().body_in_space(head)
    assert np.allclose(body, np.asarray(expected, np.float32), atol=1e-3)


def test_projecting_a_frame_of_calibration_size_is_the_identity_mapping():
    calibration = BodyProjection()
    head = (300.0, 200.0, 340.0, 245.0)
    projected = calibration.project(head, calibration.space_w, calibration.space_h)
    assert np.allclose(projected, calibration.body_in_space(head), atol=1e-3)


def test_projection_scales_with_the_frame_resolution():
    """A recording at 2.5x the calibration width must yield 2.5x the body box."""
    calibration = BodyProjection()
    factor = 2.5
    head = (300.0, 200.0, 340.0, 245.0)
    scaled = tuple(value * factor for value in head)
    body = calibration.project(scaled, calibration.space_w * factor,
                               calibration.space_h * factor)
    assert np.allclose(body, calibration.body_in_space(head) * factor, atol=1e-2)


def test_the_body_grows_away_from_the_nadir_on_both_sides_of_the_seam():
    calibration = BodyProjection()
    left = calibration.body_in_space((300.0, 200.0, 340.0, 245.0))
    right = calibration.body_in_space((860.0, 200.0, 900.0, 245.0))
    assert left[0] == pytest.approx(300.0)     # left of seam: anchored on x1
    assert right[2] == pytest.approx(900.0)    # right of seam: anchored on x2
    assert left[2] - left[0] > 40.0 and right[2] - right[0] > 40.0


def test_extent_factor_is_clamped_to_its_calibrated_band():
    calibration = BodyProjection()
    assert calibration.extent_k(0.0) == pytest.approx(calibration.k_min)
    assert calibration.extent_k(calibration.r_ref) == pytest.approx(calibration.k_ref)
    assert calibration.extent_k(1e6) == pytest.approx(calibration.k_max)


def test_calibration_round_trips_through_the_manifest_mapping():
    """as_dict is what the manifest stores, so it must carry every field."""
    values = BodyProjection().as_dict()
    assert values["projection_space"] == [1200.0, 538.0]
    assert values["nadir_left"] == [587.7, 455.2]
    assert values["nadir_right"] == [612.3, 455.2]
    assert values["extent_k"] == [1.0, 2.5, 5.0]
    assert values["seam_x"] == 600.0 and values["extent_r_ref"] == 250.0
