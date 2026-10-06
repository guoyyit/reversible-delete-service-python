"""HTTP 接口层。身份通过请求头传递：X-User-Id（必需）、X-User-Role（可选，admin）。"""
from __future__ import annotations

import threading

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import Caller
from app.config import Settings
from app.errors import DomainError
from app.models import AuditRecord, Resource
from app.service import ResourceService
from app.store import StateStore


# ---------- 请求/响应模型 ----------

class CreateResourceRequest(BaseModel):
    name: str
    parent_id: str | None = None


class BatchRequest(BaseModel):
    ids: list[str]


def resource_view(r: Resource) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "owner_id": r.owner_id,
        "parent_id": r.parent_id,
        "deleted": r.deleted,
        "deleted_at": r.deleted_at,
        "deleted_by": r.deleted_by,
        "version": r.version,
        "created_at": r.created_at,
    }


def audit_view(a: AuditRecord) -> dict:
    return {
        "id": a.id,
        "actor_id": a.actor_id,
        "action": a.action,
        "resource_id": a.resource_id,
        "result": a.result,
        "detail": a.detail,
        "timestamp": a.timestamp,
    }


# ---------- 依赖 ----------

def get_caller(
    x_user_id: str | None = Header(default=None),
    x_user_role: str = Header(default="user"),
) -> Caller:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="missing X-User-Id header")
    return Caller(user_id=x_user_id, role=x_user_role)


def get_service() -> ResourceService:
    raise NotImplementedError  # 由 create_app 覆盖


# ---------- 应用工厂 ----------

def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = StateStore(settings.state_file)
    store.load()
    service = ResourceService(store, settings)

    app = FastAPI(title="Reversible Delete Resource Service")
    app.state.service = service
    app.state.settings = settings

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request, exc: DomainError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    # 启动即回收一次：覆盖“重启恢复”场景，崩溃前到期的资源在重启后被清除
    service.recycle_expired()

    # 后台回收线程：周期性清除到期资源；recycle_expired 幂等可重入
    if settings.recycle_interval_seconds > 0:
        def _recycle_loop() -> None:
            import time
            while True:
                time.sleep(settings.recycle_interval_seconds)
                try:
                    service.recycle_expired()
                except Exception:
                    pass  # 下一轮重试，回收本身幂等

        threading.Thread(target=_recycle_loop, daemon=True).start()

    svc = lambda: service  # noqa: E731

    @app.post("/resources", status_code=201)
    def create_resource(req: CreateResourceRequest, caller: Caller = Depends(get_caller)):
        r = service.create(req.name, owner_id=caller.user_id, parent_id=req.parent_id)
        return resource_view(r)

    @app.get("/resources")
    def list_resources(parent_id: str | None = Query(default=None),
                       include_deleted: bool = Query(default=False),
                       caller: Caller = Depends(get_caller)):
        items = service.list(caller, parent_id=parent_id, include_deleted=include_deleted)
        return {"items": [resource_view(r) for r in items],
                "count": service.count(caller, parent_id=parent_id)}

    @app.get("/resources/{resource_id}")
    def get_resource(resource_id: str,
                     include_deleted: bool = Query(default=False),
                     caller: Caller = Depends(get_caller)):
        return resource_view(service.get(resource_id, caller, include_deleted))

    @app.delete("/resources/{resource_id}")
    def delete_resource(resource_id: str, caller: Caller = Depends(get_caller)):
        return resource_view(service.delete(resource_id, caller))

    @app.post("/resources/{resource_id}/restore")
    def restore_resource(resource_id: str, caller: Caller = Depends(get_caller)):
        return resource_view(service.restore(resource_id, caller))

    @app.post("/resources/batch-delete")
    def batch_delete(req: BatchRequest, caller: Caller = Depends(get_caller)):
        items = service.batch_delete(req.ids, caller)
        return {"deleted": [resource_view(r) for r in items]}

    @app.post("/resources/batch-restore")
    def batch_restore(req: BatchRequest, caller: Caller = Depends(get_caller)):
        items = service.batch_restore(req.ids, caller)
        return {"restored": [resource_view(r) for r in items]}

    @app.post("/admin/recycle")
    def recycle(caller: Caller = Depends(get_caller)):
        if not caller.is_admin:
            raise HTTPException(status_code=403, detail="admin only")
        return {"purged": service.recycle_expired()}

    @app.get("/audit")
    def audit(caller: Caller = Depends(get_caller)):
        if not caller.is_admin:
            raise HTTPException(status_code=403, detail="admin only")
        with store.locked():
            return {"items": [audit_view(a) for a in store.audit]}

    return app
