"""Prompt-engineering endpoints (FR-04/05/07): compose/split/plan/rings,
prompts, testcases, kanban."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..core import assets, orchestrator, testcases
from .deps import require_outbound

router = APIRouter()


# ------------------------------------------------------------------ compose/split

@router.post("/projects/{pid}/compose")
async def compose(pid: str, body: dict):
    conf = require_outbound(body)
    result = await orchestrator.compose(pid, body.get("complexity", "中等"),
                                        body.get("context_file_ids", []),
                                        user_modules_override=body.get("user_modules_override"))
    result["outbound"] = {"confirmed_at": conf.get("confirmed_at")}
    return result


@router.post("/projects/{pid}/split")
def split(pid: str, body: dict):
    return orchestrator.split(pid, body.get("proposal") or {},
                              body.get("complexity", "中等"),
                              body.get("modules_selected") or [],
                              context_limit_chars=int(body.get("context_limit_chars", 128000)),
                              coefficient=float(body.get("coefficient", 0.6)))


@router.get("/projects/{pid}/tasks")
def get_tasks(pid: str):
    return orchestrator.get_tasks(pid)


# ------------------------------------------------------------------ plan/rings

@router.get("/projects/{pid}/plan")
def get_plan(pid: str):
    return orchestrator.get_plan(pid)


@router.post("/projects/{pid}/plan/generate")
def generate_plan(pid: str):
    return orchestrator.build_plan(pid)


@router.put("/projects/{pid}/plan")
def adjust_plan(pid: str, body: dict):
    return orchestrator.adjust_plan(pid, body.get("rings", []))


@router.post("/projects/{pid}/plan/confirm")
async def confirm_plan(pid: str, body: dict):
    conf = require_outbound(body)
    plan = await orchestrator.confirm_plan(pid, conf.get("confirmed_at", ""))
    return plan


@router.post("/plans/{plan_id}/pause")
async def pause_plan(pid: str, plan_id: str):
    return await orchestrator.pause_plan(pid)


@router.post("/plans/{plan_id}/resume")
async def resume_plan(pid: str, plan_id: str):
    return await orchestrator.resume_plan(pid)


@router.get("/rings/{ring_id}")
def get_ring(ring_id: str, pid: str = ""):
    project = pid or _project_of_ring(ring_id)
    return orchestrator.get_ring(project, ring_id)


def _project_of_ring(ring_id: str) -> str:
    from ..core import paths
    root = paths.projects_root()
    if root.exists():
        for d in root.iterdir():
            if (d / "pipeline" / "orchestration" / ring_id).exists():
                return d.name
    from ..core.errors import ApiError
    raise ApiError("RING_NOT_FOUND", 404, "环不存在", {"ring_id": ring_id})


# ------------------------------------------------------------------ prompts

@router.get("/projects/{pid}/prompts")
def list_prompts(pid: str, module: str | None = None, task: str | None = None,
                 status: str | None = None):
    return {"prompts": assets.list_prompts(pid, module=module, task=task, status=status),
            "total": 0}


@router.get("/prompts/{prompt_id}")
def get_prompt(prompt_id: str, pid: str = ""):
    return assets.get_prompt(pid or _project_of_prompt(prompt_id), prompt_id)


@router.put("/prompts/{prompt_id}")
def edit_prompt(prompt_id: str, body: dict, pid: str = ""):
    return assets.edit_prompt(pid or _project_of_prompt(prompt_id), prompt_id,
                              body.get("body", ""))


@router.post("/prompts/{prompt_id}/finalize")
def finalize_prompt(prompt_id: str, pid: str = ""):
    return assets.finalize_prompt(pid or _project_of_prompt(prompt_id), prompt_id)


@router.post("/prompts/{prompt_id}/regen-handoff")
def regen_handoff(prompt_id: str, pid: str = ""):
    return assets.regen_handoff(pid or _project_of_prompt(prompt_id), prompt_id)


@router.get("/prompts/{prompt_id}/diff")
def diff_prompt(prompt_id: str, v1: int, v2: int, pid: str = ""):
    return assets.diff_versions(pid or _project_of_prompt(prompt_id), prompt_id, v1, v2)


@router.post("/prompts/{prompt_id}/rollback")
def rollback_prompt(prompt_id: str, body: dict, pid: str = ""):
    return assets.rollback(pid or _project_of_prompt(prompt_id), prompt_id,
                           int(body.get("to_version", 1)))


@router.post("/prompts/{prompt_id}/feedback")
def feedback(prompt_id: str, body: dict, pid: str = ""):
    return assets.add_feedback(pid or _project_of_prompt(prompt_id), prompt_id,
                               body.get("result", "成功"), body.get("note", ""))


@router.get("/projects/{pid}/feedback")
def read_feedback(pid: str):
    return {"content": assets.read_feedback(pid)}


def _project_of_prompt(prompt_id: str) -> str:
    from ..core import paths
    root = paths.projects_root()
    if root.exists():
        for d in root.iterdir():
            for f in (d / "prompts").rglob("v*.md") if (d / "prompts").exists() else []:
                if prompt_id in f.read_text(encoding="utf-8")[:400]:
                    return d.name
    from ..core.errors import ApiError
    raise ApiError("PROMPT_NOT_FOUND", 404, "提示词不存在", {"prompt_id": prompt_id})


# ------------------------------------------------------------------ testcases

@router.get("/projects/{pid}/testcases")
def list_testcases(pid: str):
    return {"testcases": testcases.list_testcases(pid)}


@router.post("/projects/{pid}/testcases")
async def gen_testcases(pid: str, body: dict):
    require_outbound(body)
    results = await testcases.generate(pid, body.get("task_ids", []),
                                       performance=bool(body.get("performance", False)))
    return {"results": results}


# ------------------------------------------------------------------ kanban

@router.patch("/projects/{pid}/tasks/{tid}/status")
def set_task_status(pid: str, tid: str, body: dict):
    return assets.set_task_status(pid, tid, body.get("status", "pending"),
                                  body.get("note", ""))


@router.get("/projects/{pid}/kanban")
def kanban(pid: str):
    return assets.kanban(pid)
