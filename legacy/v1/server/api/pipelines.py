"""FR-01 pipeline endpoints: create / chat / confirm / skip-s5 / detail."""
from __future__ import annotations

from fastapi import APIRouter

from ..core import pipeline
from .deps import require_outbound

router = APIRouter()


def _project_of(plid: str) -> str:
    project = pipeline.find_project_of_pipeline(plid)
    if project is None:
        from ..core.errors import ApiError
        raise ApiError("PIPELINE_NOT_FOUND", 404, "流水线不存在", {"plid": plid})
    return project


@router.get("/projects/{pid}/pipelines")
def list_pipelines(pid: str):
    return {"pipelines": pipeline.list_pipelines(pid)}


@router.post("/projects/{pid}/pipelines", status_code=201)
def create_pipeline(pid: str, body: dict):
    return pipeline.create_pipeline(pid, body.get("title", "需求探索"))


@router.get("/pipelines/{plid}")
def pipeline_detail(plid: str):
    return pipeline.pipeline_detail(_project_of(plid), plid)


@router.post("/pipelines/{plid}/chat")
async def chat(plid: str, body: dict):
    pid = _project_of(plid)
    conf = require_outbound(body)
    result = await pipeline.chat(pid, plid, body.get("stage", "S1"),
                                 body.get("user_input", ""), stream=bool(body.get("stream", False)))
    result["outbound"] = {"confirmed_at": conf.get("confirmed_at")}
    return result


@router.post("/pipelines/{plid}/confirm")
def confirm(plid: str, body: dict):
    pid = _project_of(plid)
    return pipeline.confirm(pid, plid, body.get("stage", "S1"),
                            comment=body.get("comment", ""),
                            part=body.get("part"))


@router.post("/pipelines/{plid}/skip-s5")
def skip_s5(plid: str):
    return pipeline.skip_s5(_project_of(plid), plid)
