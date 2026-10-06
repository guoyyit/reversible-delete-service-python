"""HTTP API wiring."""

from __future__ import annotations

from fastapi import FastAPI

from .config import Settings
from .db import Database
from .service import ResourceService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.db_path)
    service = ResourceService(db, settings)

    app = FastAPI(title="reversible-delete-service")
    app.state.settings = settings
    app.state.db = db
    app.state.service = service

    @app.get("/")
    def read_root() -> dict:
        return {"Hello": "World"}

    return app


app = create_app()
