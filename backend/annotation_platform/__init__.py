"""Local, single-user annotation platform."""

from __future__ import annotations


def create_app(*args, **kwargs):
    """Import the HTTP layer lazily so task contracts have no server side effects."""
    from .server import create_app as factory

    return factory(*args, **kwargs)


__all__ = ["create_app"]
