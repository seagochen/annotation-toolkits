"""Local, single-user annotation platform HTTP service."""

from .server import create_app

__all__ = ["create_app"]
