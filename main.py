"""Entry point kept for `uvicorn main:app` compatibility."""

from app.main import app, create_app

__all__ = ["app", "create_app"]
