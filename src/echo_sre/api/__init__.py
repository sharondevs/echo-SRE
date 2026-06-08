"""FastAPI SSE server — the deployable ECHO-SRE service the portfolio streams from."""

from .app import create_app

__all__ = ["create_app"]
