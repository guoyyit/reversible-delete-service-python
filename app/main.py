"""HTTP API wiring.

Compatibility note: the pre-existing ``GET /`` endpoint is unchanged, and
all new endpoints are additive; delete semantics change from "gone forever"
to "reversible within the retention period" without altering the DELETE
contract (same verb/path, 200 response).
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .auth import Caller, resolve_caller
from .config import Settings
from .db import Database
from .errors import ServiceError
from .service import BatchConflictError, ResourceService


class CreateResourceRequest(BaseModel):
    name: str
    parent_id: str | None = None
    content: str | None = None
    owner_id: str | None = None


class BatchRequest(BaseModel):
    ids: list[str] = Field(min_length=1)
    atomic: bool = True
    reason: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.db_path)
    service = ResourceService(db, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # optional background recycler (config-driven)
        stop = threading.Event()
        thread: threading.Thread | None = None
        interval = settings.recycle_interval_seconds
        if interval > 0:
            def loop() -> None:
                system = Caller(user_id="system", is_admin=True)
                while not stop.wait(interval):
                    try:
                        service.recycle_expired(system)
                    except Exception:  # keep the loop alive; next pass retries
                        pass

            thread = threading.Thread(target=loop, name="recycler", daemon=True)
            thread.start()
        yield
        stop.set()
        if thread is not None:
            thread.join(timeout=2)
        db.close()

    app = FastAPI(title="reversible-delete-service", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.service = service

    # -- error handling -------------------------------------------------
    @app.exception_handler(ServiceError)
    def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
        if isinstance(exc, BatchConflictError):
            return JSONResponse(status_code=exc.status_code, content=exc.payload)
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": str(exc)},
        )

    # -- dependencies -----------------------------------------------------
    def caller_dep(x_user_id: str | None = Header(default=None)) -> Caller:
        return resolve_caller(x_user_id, settings)

    # -- legacy endpoint (unchanged contract) -----------------------------
    @app.get("/")
    def read_root() -> dict:
        return {"Hello": "World"}

    # -- resources ----------------------------------------------------------
    @app.post("/resources", status_code=201)
    def create_resource(body: CreateResourceRequest, caller: Caller = Depends(caller_dep)):
        resource = service.create(
            caller,
            name=body.name,
            parent_id=body.parent_id,
            content=body.content,
            owner_id=body.owner_id,
        )
        return asdict(resource)

    @app.get("/resources")
    def list_resources(
        parent_id: str | None = Query(default=None),
        include_deleted: bool = Query(default=False),
    ):
        return [
            asdict(r)
            for r in service.list(parent_id=parent_id, include_deleted=include_deleted)
        ]

    @app.get("/resources/{resource_id}")
    def get_resource(
        resource_id: str,
        include_deleted: bool = Query(default=False),
    ):
        return asdict(service.get(resource_id, include_deleted=include_deleted))

    @app.get("/resources/{resource_id}/children")
    def get_children(
        resource_id: str,
        include_deleted: bool = Query(default=False),
    ):
        return [
            asdict(r)
            for r in service.children(resource_id, include_deleted=include_deleted)
        ]

    @app.delete("/resources/{resource_id}")
    def delete_resource(
        resource_id: str,
        reason: str | None = Query(default=None),
        caller: Caller = Depends(caller_dep),
    ):
        return asdict(service.delete(caller, resource_id, reason=reason))

    @app.post("/resources/{resource_id}/restore")
    def restore_resource(resource_id: str, caller: Caller = Depends(caller_dep)):
        return asdict(service.restore(caller, resource_id))

    @app.post("/resources/batch-delete")
    def batch_delete(body: BatchRequest, caller: Caller = Depends(caller_dep)):
        return service.delete_batch(
            caller, body.ids, atomic=body.atomic, reason=body.reason
        )

    @app.post("/resources/batch-restore")
    def batch_restore(body: BatchRequest, caller: Caller = Depends(caller_dep)):
        return service.restore_batch(caller, body.ids, atomic=body.atomic)

    # -- stats / audit / recycling -------------------------------------------
    @app.get("/stats")
    def stats(include_deleted: bool = Query(default=False)):
        return {"count": service.count(include_deleted=include_deleted)}

    @app.get("/audit")
    def audit_log(
        limit: int = Query(default=200, le=1000),
        caller: Caller = Depends(caller_dep),
    ):
        return [asdict(e) for e in service.audit_log(caller, limit=limit)]

    @app.post("/admin/recycle")
    def recycle(caller: Caller = Depends(caller_dep)):
        return service.recycle_expired(caller)

    return app


app = create_app()
