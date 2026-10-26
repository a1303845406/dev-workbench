"""FastAPI app assembly: routes, error model, static SPA hosting, startup checks.

Deployment: binds 127.0.0.1 only (NFR-03 / P-01 structural guarantee).
"""
from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .api import api_router
from .api.deps import error_payload, request_id
from .core import paths
from .core.errors import ApiError

app = FastAPI(title="开发工作台 · Dev Workbench", version="1.0.0",
              docs_url="/api/docs", openapi_url="/api/openapi.json")

app.include_router(api_router)


async def _api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status,
                        content=error_payload(exc.code, exc.message, exc.detail))


app.add_exception_handler(ApiError, _api_error_handler)  # type: ignore[arg-type]


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    return JSONResponse(status_code=500,
                        content=error_payload("INTERNAL", str(exc), None))


# ------------------------------------------------------------- static frontend

@app.get("/")
async def index():
    idx = paths.static_dir() / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return JSONResponse({"message": "前端尚未构建：请运行 frontend 构建脚本，或访问 /api/docs 查看接口。",
                         "api_docs": "/api/docs"})


@app.get("/{full_path:path}")
async def spa(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"error": {"code": "NOT_FOUND", "message": "unknown api path"}},
                            status_code=404)
    base = paths.static_dir().resolve()
    target = (base / full_path).resolve()
    if target.is_file() and str(target).startswith(str(base)):
        return FileResponse(target)
    idx = base / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return JSONResponse({"message": "前端尚未构建", "api_docs": "/api/docs"}, status_code=404)


@app.on_event("startup")
async def startup():
    # data dirs
    paths.projects_root().mkdir(parents=True, exist_ok=True)
    paths.backup_root().mkdir(parents=True, exist_ok=True)
    paths.logs_dir().mkdir(parents=True, exist_ok=True)
    # recovery: running plans orphaned by restart → suspended (FR-06 文件权威态)
    _recover_orphan_plans()
    # backup scheduler
    from .core import backup
    asyncio.get_event_loop().create_task(backup.scheduler_loop())
    _print_banner()


def _recover_orphan_plans() -> None:
    root = paths.projects_root()
    if not root.exists():
        return
    for d in root.iterdir():
        plan = d / "pipeline" / "orchestration" / "plan.json"
        if not plan.exists():
            continue
        try:
            from .core.persist import load_json, atomic_write_json
            data = load_json(plan)
            if data.get("status") == "running":
                data["status"] = "suspended"
                data["resume_hint"] = "服务重启导致执行中断，可从断点恢复（前序环不重算）"
                atomic_write_json(plan, data)
        except Exception:
            continue


def _print_banner() -> None:
    port = sys.argv[sys.argv.index("--port") + 1] if "--port" in sys.argv else "8642"
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    print("=" * 62)
    print("  开发工作台 · Dev Workbench v1.0.0")
    print(f"  访问地址: http://127.0.0.1:{port}/   （仅本机访问，NFR-03）")
    print("  API 文档: http://127.0.0.1:{}/api/docs".format(port))
    print("  数据目录: {}".format(paths.data_root()))
    print("  [!] 局域网/公网暴露前必须先实施门禁鉴权（SRS FU-02/FU-05）")
    print("=" * 62)


def main() -> None:
    import os
    import uvicorn
    port = int(os.environ.get("DEVWB_PORT", "8642"))
    uvicorn.run("server.app:app", host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
