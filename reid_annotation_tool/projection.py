"""Perspective head->body projection: the crop geometry a deployment actually sees.

A detector box marks a HEAD; the ReID model is trained on FULL BODIES. The body
rectangle is not a fixed multiple of the head box -- it depends on where the head
sits relative to the camera's nadir, so recovering it needs the scene's calibration.

This is the extractor's ONLY crop geometry, deliberately not a switch. A dataset
whose crops are the raw detector boxes cannot be compared with a deployment that
crops projected bodies: every absolute cosine gate calibrated on one geometry is
silently wrong on the other, and "silently wrong" here means a threshold that reads
as unchanged while it has in fact loosened. Making the production geometry the
default algorithm is what keeps a dataset transferable to production thresholds.

The calibration lives in configuration, never in this module's logic, so a second
scene only needs new numbers. The defaults are the measured production values of
the deployment this tool feeds (`surv.identity.state`: SEAM_X, NADIR_L/NADIR_R,
R_REF, K_*), and tests/test_projection.py pins this implementation against that
one with golden vectors so the two cannot drift apart unnoticed.

Boxes enter and leave in FRAME pixels. The projection itself runs in the
calibration space, because that is the space the nadir and extent constants were
measured in; a frame of any resolution is scaled in and back out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Measured production calibration of the deployment this tool feeds (algorithm space 1200x538).
# The mirrored nadir pair is what makes the projection perspective-aware: a head far
# from its nadir belongs to a body seen more side-on, hence a wider extent factor.
DEFAULT_SPACE = (1200.0, 538.0)
DEFAULT_SEAM_X = 600.0
DEFAULT_NADIR_LEFT = (587.7, 455.2)
DEFAULT_NADIR_RIGHT = (612.3, 455.2)      # mirrored: 2 * SEAM_X - 587.7
DEFAULT_EXTENT_K = (1.0, 2.5, 5.0)        # k_min, k_ref, k_max
DEFAULT_EXTENT_R_REF = 250.0


@dataclass(frozen=True)
class BodyProjection:
    """One camera's head->body geometry. Immutable: it is recorded in the manifest."""

    space_w: float = DEFAULT_SPACE[0]
    space_h: float = DEFAULT_SPACE[1]
    seam_x: float = DEFAULT_SEAM_X
    nadir_left: tuple[float, float] = DEFAULT_NADIR_LEFT
    nadir_right: tuple[float, float] = DEFAULT_NADIR_RIGHT
    k_min: float = DEFAULT_EXTENT_K[0]
    k_ref: float = DEFAULT_EXTENT_K[1]
    k_max: float = DEFAULT_EXTENT_K[2]
    r_ref: float = DEFAULT_EXTENT_R_REF

    @classmethod
    def from_args(cls, args) -> "BodyProjection":
        """Build from the flattened ``projection`` project configuration."""
        k_min, k_ref, k_max = args.extent_k
        return cls(space_w=float(args.projection_space[0]),
                   space_h=float(args.projection_space[1]),
                   seam_x=float(args.seam_x),
                   nadir_left=(float(args.nadir_left[0]), float(args.nadir_left[1])),
                   nadir_right=(float(args.nadir_right[0]), float(args.nadir_right[1])),
                   k_min=float(k_min), k_ref=float(k_ref), k_max=float(k_max),
                   r_ref=float(args.extent_r_ref))

    def as_dict(self) -> dict:
        """Flat mapping for manifest.json -- a crop can always be re-derived."""
        return {"projection_space": [self.space_w, self.space_h],
                "seam_x": self.seam_x,
                "nadir_left": list(self.nadir_left),
                "nadir_right": list(self.nadir_right),
                "extent_k": [self.k_min, self.k_ref, self.k_max],
                "extent_r_ref": self.r_ref}

    def nadir_for_x(self, px: float) -> tuple[float, float]:
        """Which of the two mirrored nadirs governs this column."""
        return self.nadir_left if px < self.seam_x else self.nadir_right

    def extent_k(self, radius: float) -> float:
        """Head-to-body extent factor, growing with distance from the nadir."""
        raw = self.k_min + (self.k_ref - self.k_min) * (radius / self.r_ref)
        return min(max(raw, self.k_min), self.k_max)

    def body_in_space(self, head) -> np.ndarray:
        """Head xyxy -> body xyxy, both already in calibration space.

        The body grows from the head AWAY from the nadir: the sign of the
        head->nadir direction decides which edge of the head box stays anchored,
        which is what keeps the projection correct on both sides of the seam.
        """
        x1, y1, x2, y2 = (float(value) for value in head[:4])
        head_w, head_h = max(x2 - x1, 1.0), max(y2 - y1, 1.0)
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        nadir_x, nadir_y = self.nadir_for_x(cx)
        dx, dy = nadir_x - cx, nadir_y - cy
        radius = math.hypot(dx, dy)
        ux, uy = (dx / radius, dy / radius) if radius > 1e-6 else (0.0, 1.0)
        factor = self.extent_k(radius)
        body_w = head_w * (1.0 + (factor - 1.0) * abs(ux))
        body_h = head_h * (1.0 + (factor - 1.0) * abs(uy))
        bx1, bx2 = (x1, x1 + body_w) if ux >= 0.0 else (x2 - body_w, x2)
        by1, by2 = (y1, y1 + body_h) if uy >= 0.0 else (y2 - body_h, y2)
        return np.asarray([max(0.0, bx1), max(0.0, by1),
                           min(self.space_w, bx2), min(self.space_h, by2)], np.float32)

    def project(self, head_frame, frame_w: float, frame_h: float) -> np.ndarray:
        """Head xyxy in frame pixels -> body xyxy in frame pixels.

        Scaling in and back out is what lets one calibration serve a low-resolution
        live feed and a high-resolution recording of the same camera: the projection
        is defined by the scene's perspective, not by the pixel count.
        """
        scale_x, scale_y = self.space_w / float(frame_w), self.space_h / float(frame_h)
        head_space = (head_frame[0] * scale_x, head_frame[1] * scale_y,
                      head_frame[2] * scale_x, head_frame[3] * scale_y)
        body = self.body_in_space(head_space)
        return np.asarray([body[0] / scale_x, body[1] / scale_y,
                           body[2] / scale_x, body[3] / scale_y], np.float32)
