from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .api.v2 import router as v2_router
from .api.model_settings import router as model_settings_router
from fastapi.exceptions import RequestValidationError
from .core.db import database
from .core.errors import DomainError

@asynccontextmanager
async def lifespan(_: FastAPI):
    database()
    yield

app = FastAPI(title="Dev Workbench v2", version="2.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
app.include_router(v2_router, prefix="/api")
app.include_router(model_settings_router, prefix="/api")

@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/v2/settings/model"):
        return JSONResponse(status_code=422, content={"error": {"code": "INVALID_MODEL_SETTINGS", "message": "模型设置字段格式无效，请检查填写内容。"}})
    # Do not echo raw request input (which may contain credentials).
    return JSONResponse(status_code=422, content={"detail": [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]} for item in exc.errors()]})

@app.exception_handler(DomainError)
async def domain_error(_: Request, exc: DomainError):
    return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail}})

@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL", "message": "操作失败，请检查服务状态。"}})


@app.get("/api/health")
def health():
    database()
    return {"status": "ok", "service": "devworkbench-v2"}

_FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "dist"

@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    if path.startswith("api/"):
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "not found"}})
    target = (_FRONTEND / path).resolve()
    if target.is_file() and _FRONTEND in target.parents:
        return FileResponse(target)
    index = _FRONTEND / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse(status_code=404, content={"error": {"code": "FRONTEND_NOT_BUILT", "message": "frontend build not found"}})
