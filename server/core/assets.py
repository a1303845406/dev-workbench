"""FR-07 prompt asset management: versions, diff, rollback, feedback (G1/G6),
plus task kanban status transitions (G5)."""
from __future__ import annotations

import difflib
import re
from pathlib import Path

from . import paths
from .audit import audit
from . import orchestrator as orch
from .errors import ApiError, prompt_not_final
from .persist import (atomic_write_json, atomic_write_text, load_json, now_iso,
                      parse_front_matter, read_text, with_front_matter)


def _pdir(project: str) -> Path:
    return paths.project_dir(project)


def _prompt_root(project: str) -> Path:
    return _pdir(project) / "prompts"


def list_prompts(project: str, *, module: str | None = None, task: str | None = None,
                 status: str | None = None) -> list[dict]:
    root = _prompt_root(project)
    out = []
    if not root.exists():
        return out
    for f in sorted(root.rglob("v*.md")):
        meta, _ = parse_front_matter(read_text(f))
        if module and meta.get("module") != module:
            continue
        if task and meta.get("task_id") != task:
            continue
        if status and meta.get("status") != status:
            continue
        rel = f.relative_to(_pdir(project))
        out.append({**meta, "path": str(rel), "chars": len(read_text(f))})
    return out


def _find_prompt(project: str, prompt_id: str) -> Path:
    root = _prompt_root(project)
    for f in root.rglob("v*.md"):
        meta, _ = parse_front_matter(read_text(f))
        if meta.get("prompt_id") == prompt_id:
            return f
    raise ApiError("PROMPT_NOT_FOUND", 404, "提示词不存在", {"prompt_id": prompt_id})


def get_prompt(project: str, prompt_id: str) -> dict:
    f = _find_prompt(project, prompt_id)
    meta, body = parse_front_matter(read_text(f))
    return {**meta, "body": body}


def edit_prompt(project: str, prompt_id: str, body: str) -> dict:
    """Manual edit → new draft version (G4 preview/fine-tune)."""
    f = _find_prompt(project, prompt_id)
    meta, _ = parse_front_matter(read_text(f))
    module, task = meta["module"], meta["task_id"]
    version = orch.next_prompt_version(project, module, task)
    new_meta = {**meta, "prompt_id": f"P-{task}-{module}-v{version}", "version": version,
                "status": "draft", "generated_at": now_iso(),
                "derived_from": prompt_id}
    path = f.parent / f"v{version}.md"
    atomic_write_text(path, with_front_matter(new_meta, body if body.endswith("\n") else body + "\n"))
    audit("prompt.edit", project=project, target=new_meta["prompt_id"])
    return {**new_meta, "body": body}


def finalize_prompt(project: str, prompt_id: str) -> dict:
    f = _find_prompt(project, prompt_id)
    meta, body = parse_front_matter(read_text(f))
    meta["status"] = "final"
    meta["finalized_at"] = now_iso()
    atomic_write_text(f, with_front_matter(meta, body))
    audit("prompt.finalize", project=project, target=prompt_id)
    stale = _stale_rings_after_finalize(project, meta["module"], meta["task_id"],
                                        int(meta.get("version", 1)))
    if stale:
        import asyncio
        asyncio.get_event_loop().create_task(
            _emit_stale(prompt_id, stale))
    return {"status": "final", "version": meta.get("version"),
            "handoff_stale": {"affected_rings": stale, "action_required": "regen-handoff"} if stale else None}


async def _emit_stale(prompt_id: str, rings: list[str]) -> None:
    from . import events
    await events.publish("handoff.stale", {"prompt_id": prompt_id, "affected_rings": rings})


def _stale_rings_after_finalize(project: str, module: str, task: str, version: int) -> list[str]:
    """Pending rings whose upstream handoff was based on an older version."""
    try:
        plan = load_json(orch._plan_path(project))
    except ApiError:
        return []
    stale = []
    for r in plan.get("rings", []):
        if r.get("status") != "pending":
            continue
        for dep in r.get("depends_rings", []):
            up = next((x for x in plan["rings"] if x["ring_id"] == dep), None)
            if not up or up.get("task") != task or up.get("module") != module:
                continue
            _, based = orch._handoff_for(project, up)
            if based != version:
                stale.append(r["ring_id"])
    return stale


def regen_handoff(project: str, prompt_id: str) -> dict:
    """One-click handoff regeneration from the finalized prompt (AC-16)."""
    from .provider import Message, get_provider
    meta = get_prompt(project, prompt_id)
    if meta.get("status") != "final":
        raise prompt_not_final(prompt_id)
    module, task, version = meta["module"], meta["task_id"], int(meta.get("version", 1))
    provider = get_provider(project)
    user = f"请为以下定稿提示词重写交接摘要（≤500字符）。\n\n{meta['body'][:4000]}"
    result = provider_chat_sync(provider, [Message("system", "摘要助手。[mock-mode: handoff]"),
                                           Message("user", user)])
    _, handoff, ok = orch._split_handoff(result.text)
    # find the ring of this task/module and refresh its handoff
    plan = load_json(orch._plan_path(project))
    ring = next((r for r in plan["rings"] if r["task"] == task and r["module"] == module), None)
    if ring is not None:
        hpath = orch._ring_dir(project, ring["ring_id"]) / "handoff.md"
        atomic_write_text(hpath, with_front_matter(
            {"based_on_version": version, "based_on_ring": ring["ring_id"], "valid": True,
             "regenerated_at": now_iso()}, handoff + "\n"))
    audit("prompt.regen_handoff", project=project, target=prompt_id)
    return {"prompt_id": prompt_id, "based_on_version": version, "handoff": handoff,
            "affected_ring": ring["ring_id"] if ring else None}


def provider_chat_sync(provider, messages):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(provider.chat(messages))


def diff_versions(project: str, prompt_id: str, v1: int, v2: int) -> dict:
    f = _find_prompt(project, prompt_id)
    meta, _ = parse_front_matter(read_text(f))
    module, task = meta["module"], meta["task_id"]

    def _load(v: int) -> tuple[int, str]:
        p = _prompt_root(project) / module / task / f"v{v}.md"
        if not p.exists():
            raise ApiError("VERSION_NOT_FOUND", 404, "版本不存在", {"version": v})
        _, body = parse_front_matter(read_text(p))
        return v, body

    _, b1 = _load(v1)
    _, b2 = _load(v2)
    sm = difflib.SequenceMatcher(None, b1.splitlines(), b2.splitlines())
    left, right = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                left.append("  " + b1.splitlines()[i1 + k])
                right.append("  " + b1.splitlines()[i1 + k])
        else:
            for k in range(i1, i2):
                left.append("- " + b1.splitlines()[k])
                right.append("")
            for k in range(j1, j2):
                left.append("")
                right.append("+ " + b2.splitlines()[k])
    return {"v1": v1, "v2": v2, "left": left[:400], "right": right[:400],
            "changed": sum(1 for l in left if l.startswith("- ")) + sum(1 for r in right if r.startswith("+ "))}


def rollback(project: str, prompt_id: str, to_version: int) -> dict:
    f = _find_prompt(project, prompt_id)
    meta, _ = parse_front_matter(read_text(f))
    module, task = meta["module"], meta["task_id"]
    src = _prompt_root(project) / module / task / f"v{to_version}.md"
    if not src.exists():
        raise ApiError("VERSION_NOT_FOUND", 404, "目标版本不存在", {"version": to_version})
    _, body = parse_front_matter(read_text(src))
    return edit_prompt(project, prompt_id, body)


def add_feedback(project: str, prompt_id: str, result: str, note: str) -> dict:
    path = _prompt_root(project) / "feedback.md"
    row = f"| {now_iso()} | {prompt_id} | {result} | {note or '—'} |\n"
    if not path.exists():
        atomic_write_text(path, "# 执行 / 工具反馈（G6，追加写）\n\n"
                                "| 时间 | 提示词id | 执行结果 | 备注（返工原因等） |\n"
                                "|------|----------|----------|--------------------|\n" + row)
    else:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(row)
    audit("prompt.feedback", project=project, target=prompt_id, detail={"result": result})
    return {"ok": True}


def read_feedback(project: str) -> str:
    path = _prompt_root(project) / "feedback.md"
    return read_text(path) if path.exists() else ""


# ------------------------------------------------------------------- kanban

_KANBAN = {"pending": "待投喂", "fed": "已投喂", "done": "已完成", "failed": "失败"}


def set_task_status(project: str, tid: str, status: str, note: str = "") -> dict:
    data = orch.get_tasks(project)
    task = next((t for t in data["tasks"] if t["id"] == tid), None)
    if task is None:
        raise ApiError("TASK_NOT_FOUND", 404, "任务不存在", {"task": tid})
    if status not in _KANBAN:
        raise ApiError("INVALID_STATUS", 400, "状态须为 pending|fed|done|failed", {"got": status})
    task["status"] = status
    orch._save_plan  # noqa: B018 - keep import side effect explicit
    atomic_write_json(orch._tasks_path(project), data)
    if status == "failed" or note:
        add_feedback(project, f"task:{tid}", _KANBAN[status], note)
    audit("task.status", project=project, target=tid, detail={"status": status})
    return task


def kanban(project: str) -> dict:
    data = orch.get_tasks(project)
    cols = {k: [] for k in _KANBAN}
    for t in data["tasks"]:
        cols.setdefault(t.get("status", "pending"), []).append(t)
    total = len(data["tasks"])
    done = len(cols["done"])
    return {"columns": [{"key": k, "name": v, "tasks": cols.get(k, [])} for k, v in _KANBAN.items()],
            "progress": {"done": done, "total": total}}
