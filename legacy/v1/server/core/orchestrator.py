"""FR-04 prompt-engineering orchestrator.

- compose: complexity → module combination (hard-constraint validated) + task proposal
- split: context-budget task splitting → pipeline/tasks.json (multi-level)
- plan: build/preview/adjust/confirm ring sequence (task × module)
- run: async ring execution loop with 4-layer input packet, budget gate
  (compress → block), retry/suspend, full on-disk audit, SSE progress
- handoff protocol + finalize linkage (AC-16)
"""
from __future__ import annotations

import asyncio
import json
import math
import re
from pathlib import Path

from . import events, paths
from .audit import audit
from .config import config_center
from .constraints import global_layer, package_version
from .errors import ApiError, budget_exceeded, hard_constraint_violation, handoff_stale
from .persist import (atomic_write_json, atomic_write_text, load_json, now_iso,
                      parse_front_matter, read_text, with_front_matter)
from .tools_registry import enabled_guides

SCHEMA = 1
ALL_MODULES = ["requirement", "design-outline", "prototype", "detailed-design", "database",
               "development-backend", "development-frontend", "test", "regression", "acceptance"]
DEV_MODULES = [m for m in ALL_MODULES if m.startswith("development-")]
MODULE_NAMES = {
    "requirement": "需求", "design-outline": "概设", "prototype": "原型", "detailed-design": "详设",
    "database": "数据库设计", "development-backend": "后端开发", "development-frontend": "前端开发",
    "test": "测试", "regression": "回归", "acceptance": "验收",
}
MAX_RETRIES = 3
PARALLELISM = 2
_HANDOFF_SPLIT = "===HANDOFF==="


# ------------------------------------------------------------------ helpers

def _pdir(project: str) -> Path:
    return paths.project_dir(project)


def _tasks_path(project: str) -> Path:
    return _pdir(project) / "pipeline" / "tasks.json"


def _plan_dir(project: str) -> Path:
    return _pdir(project) / "pipeline" / "orchestration"


def _plan_path(project: str) -> Path:
    return _plan_dir(project) / "plan.json"


def get_tasks(project: str) -> dict:
    path = _tasks_path(project)
    if not path.exists():
        raise ApiError("TASKS_NOT_FOUND", 404, "尚未生成任务拆分（先执行 compose/split）")
    return load_json(path)


def effective_budget(budget: dict) -> int:
    limit = int(budget.get("context_limit_chars", 128000))
    coef = float(budget.get("coefficient", 0.6))
    return int(limit * coef)


def _project_meta(project: str) -> dict:
    pj = _pdir(project) / "project.json"
    if not pj.exists():
        raise ApiError("PROJECT_NOT_FOUND", 404, "项目不存在", {"project": project})
    return load_json(pj)


def _rules_md(project: str) -> str:
    p = _pdir(project) / "rules.md"
    return read_text(p) if p.exists() else ""


def validate_combination(modules: list[str]) -> None:
    missing = []
    if "requirement" not in modules:
        missing.append("requirement")
    if not any(m in modules for m in DEV_MODULES):
        missing.append("<任一 development-*>")
    if "test" not in modules:
        missing.append("test")
    if "acceptance" not in modules:
        missing.append("acceptance")
    if missing:
        raise hard_constraint_violation(missing)


# ------------------------------------------------------------------ compose

async def compose(project: str, complexity: str, context_file_ids: list[str], *,
                  user_modules_override: list[str] | None = None) -> dict:
    meta = _project_meta(project)
    cmap = config_center.complexity_map()
    level = (cmap.get("levels") or {}).get(complexity)
    if level is None:
        raise ApiError("COMPLEXITY_UNKNOWN", 400, "未知复杂度档位",
                       {"known": list((cmap.get("levels") or {}).keys())})
    modules = list(level.get("modules", []))
    deviation = False
    if user_modules_override:
        modules = list(user_modules_override)
        deviation = True
    if modules == ["*all*"]:
        modules = list(ALL_MODULES)
    validate_combination(modules)

    docs_blob = []
    for fid in context_file_ids:
        p = _pdir(project) / fid
        if p.exists() and p.is_file():
            text = read_text(p)
            docs_blob.append(f"### 文档：{fid}\n{text[:8000]}")

    from .provider import Message, get_provider, resolve_outbound
    provider = get_provider(project)
    user = ("<!-- 上下文文档节选 -->\n" + "\n\n".join(docs_blob) +
            f"\n\n<!-- 复杂度: {complexity} · 模块组合: {modules} -->\n"
            "请按所选模块组合给出任务拆分草案，仅输出 JSON："
            '{"tasks_proposal":[{"title","module","description","acceptance","req_refs",'
            '"est_chars"}]}，module 取值限定在所选组合内。')
    resolve_outbound(provider.name, context_file_ids, len(user))
    result = await provider.chat([
        Message("system", "你是提示词工程编排师。[mock-mode: compose]"),
        Message("user", user)])
    proposal = _parse_compose(result.text, modules)
    audit("compose", project=project, detail={"complexity": complexity, "modules": modules})
    return {"modules_selected": modules, "complexity": complexity,
            "hard_constraint": ["requirement", "<任一 development-*>", "test", "acceptance"],
            "deviation_from_map": deviation, "constraints_package_version": package_version(),
            **proposal}


def _parse_compose(text: str, modules: list[str]) -> dict:
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(m.group(0) if m else text)
        tasks = data.get("tasks_proposal") or []
        clean = []
        for t in tasks:
            mod = t.get("module") if t.get("module") in modules else modules[min(1, len(modules) - 1)]
            clean.append({"title": str(t.get("title", "未命名任务"))[:60],
                          "module": mod,
                          "description": str(t.get("description", "")),
                          "acceptance": str(t.get("acceptance", "")),
                          "req_refs": list(t.get("req_refs", [])),
                          "est_chars": int(t.get("est_chars", 20000))})
        if clean:
            return {"tasks_proposal": clean}
    except (json.JSONDecodeError, AttributeError, ValueError):
        pass
    # deterministic fallback: one task per selected module (requirement/dev/test...)
    fallback = [{"title": f"{MODULE_NAMES.get(mo, mo)}·任务", "module": mo,
                 "description": f"围绕所选上下文完成{MODULE_NAMES.get(mo, mo)}环节的产出。",
                 "acceptance": "产出符合模块角色要求且可验收", "req_refs": [], "est_chars": 18000}
                for mo in modules]
    return {"tasks_proposal": fallback}


# ------------------------------------------------------------------ split

def split(project: str, proposal: dict, complexity: str, modules_selected: list[str], *,
          context_limit_chars: int = 128000, coefficient: float = 0.6) -> dict:
    budget = {"context_limit_chars": context_limit_chars, "coefficient": coefficient}
    eff = effective_budget(budget)
    tasks: list[dict] = []
    idx = 0
    for item in proposal.get("tasks_proposal", []):
        idx += 1
        tid = f"T{idx}"
        est = int(item.get("est_chars", eff // 2))
        base = {"id": tid, "parent_id": None, "level": 1, "module": item["module"],
                "title": item["title"], "description": item.get("description", ""),
                "acceptance": item.get("acceptance", ""), "req_refs": item.get("req_refs", []),
                "depends_on": [], "budget_chars": eff, "est_chars": est,
                "status": "pending", "adjust": None}
        if est <= eff:
            tasks.append(base)
            continue
        # over budget → recursive split into chained children (module responsibility slices)
        n = min(6, max(2, math.ceil(est / eff)))
        tasks.append({**base, "title": base["title"] + "（容器）"})
        for i in range(n):
            child = {**base, "id": f"{tid}.{i + 1}", "parent_id": tid, "level": 2,
                     "title": f"{base['title']}·部分{i + 1}/{n}",
                     "description": _slice_text(base["description"], n, i),
                     "est_chars": math.ceil(est / n),
                     "depends_on": [f"{tid}.{i}"] if i else []}
            tasks.append(child)
    data = {"schemaVersion": SCHEMA, "budget": budget, "effective_chars": eff,
            "complexity": complexity, "modules_selected": modules_selected,
            "user_adjusted": False, "tasks": tasks}
    atomic_write_json(_tasks_path(project), data)
    audit("split", project=project, detail={"tasks": len(tasks), "effective_chars": eff})
    over = [t["id"] for t in tasks if _is_leaf(tasks, t) and t.get("est_chars", 0) > eff]
    return {**data, "overbudget_leaves": over}


def _slice_text(text: str, n: int, i: int) -> str:
    if not text:
        return f"第 {i + 1}/{n} 部分（按模块内职责边界切分）"
    size = math.ceil(len(text) / n)
    part = text[i * size:(i + 1) * size]
    return part or f"第 {i + 1}/{n} 部分"


def _is_leaf(tasks: list[dict], t: dict) -> bool:
    return not any(x.get("parent_id") == t["id"] for x in tasks)


def leaves(tasks: list[dict]) -> list[dict]:
    return [t for t in tasks if _is_leaf(tasks, t)]


# ------------------------------------------------------------------ plan

def build_plan(project: str) -> dict:
    tasks_data = get_tasks(project)
    mods = tasks_data.get("modules_selected") or []
    validate_combination(mods)
    ls = leaves(tasks_data["tasks"])
    rings: list[dict] = []
    ring_no = 0
    group_of: dict[str, str] = {}
    # Kahn levels for parallel groups
    deps_map = {t["id"]: set(t.get("depends_on") or []) for t in ls}
    level_of: dict[str, int] = {}

    def level(tid: str, seen: set | None = None) -> int:
        seen = seen or set()
        if tid in level_of:
            return level_of[tid]
        if tid in seen:
            return 0
        seen.add(tid)
        ds = deps_map.get(tid) or set()
        lvl = 0 if not ds else 1 + max(level(d, seen) for d in ds)
        level_of[tid] = lvl
        return lvl

    for t in ls:
        level(t["id"], set())

    last_ring_of_task: dict[str, str] = {}
    for t in sorted(ls, key=lambda x: (level_of.get(x["id"], 0), x["id"])):
        prev_ring = None
        for mo in mods:
            ring_no += 1
            rid = f"R{ring_no:03d}"
            depends = []
            if prev_ring:
                depends.append(prev_ring)
            for dep_tid in sorted(deps_map.get(t["id"], set())):
                if dep_tid in last_ring_of_task:
                    depends.append(last_ring_of_task[dep_tid])
            grp = f"G{level_of.get(t['id'], 0) + 1}"
            rings.append({"ring_id": rid, "task": t["id"], "module": mo, "seq": ring_no,
                          "parallel_group": grp, "depends_rings": depends,
                          "status": "pending", "retries": 0})
            prev_ring = rid
        last_ring_of_task[t["id"]] = prev_ring or ""
    plan = {"schemaVersion": SCHEMA, "project": project,
            "id": f"plan-{now_iso().replace(':', '').replace('-', '')}",
            "status": "awaiting-confirm", "rings": rings, "user_adjusted_order": False,
            "generated_at": now_iso(), "stale_rings": []}
    _save_plan(project, plan)
    return plan


def _save_plan(project: str, plan: dict) -> None:
    _plan_dir(project).mkdir(parents=True, exist_ok=True)
    atomic_write_json(_plan_path(project), plan)


def get_plan(project: str) -> dict:
    path = _plan_path(project)
    if not path.exists():
        raise plan_not_found()
    plan = load_json(path)
    rings = plan.get("rings", [])
    total = len(rings)
    done = sum(1 for r in rings if r.get("status") == "done")
    return {**plan, "done_rings": done, "total_rings": total}


def plan_not_found() -> ApiError:
    return ApiError("PLAN_NOT_FOUND", 404, "编排计划不存在（先生成计划）")


def adjust_plan(project: str, new_rings: list[dict]) -> dict:
    plan = load_json(_plan_path(project))
    old = {r["ring_id"]: r for r in plan["rings"]}
    seen = []
    for item in new_rings:
        rid = item.get("ring_id")
        if rid not in old:
            raise ApiError("RING_UNKNOWN", 400, "未知环 id", {"ring_id": rid})
        seen.append(rid)
    if set(seen) != set(old):
        raise ApiError("RING_SET_MISMATCH", 400, "调序必须包含全部环")
    pos = {rid: i for i, rid in enumerate(seen)}
    for r in old.values():
        for dep in r.get("depends_rings", []):
            if pos[dep] > pos[r["ring_id"]]:
                raise ApiError("PLAN_ORDER_INVALID", 409, "调序违反依赖关系",
                               {"ring": r["ring_id"], "depends_on": dep})
    for i, rid in enumerate(seen):
        old[rid]["seq"] = i + 1
        old[rid]["parallel_group"] = new_rings[i].get("parallel_group", old[rid]["parallel_group"])
    plan["rings"] = [old[rid] for rid in seen]
    plan["user_adjusted_order"] = True
    _save_plan(project, plan)
    return plan


# --------------------------------------------------------------- execution

def _prompt_dir(project: str, module: str, task: str) -> Path:
    return _pdir(project) / "prompts" / module / task


def latest_final_version(project: str, module: str, task: str) -> int | None:
    pdir = _prompt_dir(project, module, task)
    if not pdir.exists():
        return None
    finals = []
    for f in pdir.glob("v*.md"):
        meta, _ = parse_front_matter(read_text(f))
        if meta.get("status") == "final":
            finals.append(int(meta.get("version", 0)))
    return max(finals) if finals else None


def _handoff_for(project: str, ring: dict) -> tuple[str, int]:
    """Return (handoff text, based_on_version) for a completed upstream ring."""
    path = _ring_dir(project, ring["ring_id"]) / "handoff.md"
    if not path.exists():
        return "", 0
    meta, body = parse_front_matter(read_text(path))
    return body.strip(), int(meta.get("based_on_version", 0))


def _ring_dir(project: str, ring_id: str) -> Path:
    return _plan_dir(project) / ring_id


def assemble_input(project: str, ring: dict, tasks_data: dict) -> dict:
    """4-layer input packet (04 文档 §2.4). Returns {layers: {...}, text, chars}."""
    task = next((t for t in tasks_data["tasks"] if t["id"] == ring["task"]), None)
    _, role_body = config_center.module_prompt(f"modules/{ring['module']}.md")
    role = f"<!-- layer:role -->\n[module: {ring['module']}]\n{role_body.strip()}"
    glob = global_layer(_rules_md(project))
    guides = enabled_guides(project, _module_category(ring["module"]))
    guide_text = "\n".join(f"- {g['guide_template'].format(目标=task['title'] if task else '本任务', 库='相关库')}"
                           for g in guides)
    req_refs = ", ".join(task.get("req_refs", [])) if task else ""
    task_layer = (f"<!-- layer:task -->\n## 任务 {ring['task']}：{task.get('title') if task else ring['task']}\n"
                  f"- 模块：{MODULE_NAMES.get(ring['module'], ring['module'])}\n"
                  f"- 描述：{task.get('description') if task else ''}\n"
                  f"- 验收：{task.get('acceptance') if task else ''}\n"
                  f"- 关联需求：{req_refs or '（无）'}\n"
                  f"- 复杂度：{tasks_data.get('complexity')}\n"
                  f"- 预算：{task.get('budget_chars') if task else tasks_data.get('effective_chars')} 字符\n"
                  + (f"\n### 协同工具指引（可删除）\n{guide_text}\n" if guide_text else ""))
    upstream_parts = []
    plan = load_json(_plan_path(project))
    by_id = {r["ring_id"]: r for r in plan["rings"]}
    for dep in ring.get("depends_rings", []):
        up = by_id.get(dep)
        if not up or up.get("status") != "done":
            continue
        text, based = _handoff_for(project, up)
        final_v = latest_final_version(project, up["module"], up["task"])
        if final_v is not None and based != final_v:
            raise handoff_stale([ring["ring_id"]])
        upstream_parts.append(f"### 上游环 {dep}（{up['task']}·{MODULE_NAMES.get(up['module'], up['module'])}）交接摘要\n{text}\n"
                              f"路径引用：prompts/{up['module']}/{up['task']}/ 最新定稿版")
    upstream = "<!-- layer:upstream -->\n" + ("\n".join(upstream_parts) if upstream_parts else "（无直接上游依赖）")
    text = f"{role}\n\n{glob}\n\n{task_layer}\n\n{upstream}"
    return {"role": role, "global": glob, "task": task_layer, "upstream": upstream,
            "text": text, "chars": len(text)}


def _module_category(module: str) -> str:
    if module in ("development-backend", "development-frontend"):
        return "doc-fetch"
    if module in ("database", "detailed-design"):
        return "code-intel"
    return "reasoning"


def _budget_check(inp: dict, eff: int) -> tuple[dict | None, bool]:
    """Returns (compressed_layers or None, blocked). Compress upstream first."""
    if inp["chars"] <= eff:
        return None, False
    compressed = {**inp}
    head, sep, _ = compressed["upstream"].partition("路径引用：")
    if sep and "（仅保留路径引用" not in head:
        compressed["upstream"] = head + "路径引用：（仅保留路径引用，全文因预算压缩）\n" + sep
        compressed["text"] = f"{compressed['role']}\n\n{compressed['global']}\n\n{compressed['task']}\n\n{compressed['upstream']}"
        compressed["chars"] = len(compressed["text"])
        if compressed["chars"] <= eff:
            return compressed, False
    return None, True


async def confirm_plan(project: str, outbound_confirmed_at: str) -> dict:
    plan = load_json(_plan_path(project))
    tasks_data = get_tasks(project)
    eff = effective_budget(tasks_data["budget"])
    for r in plan["rings"]:
        try:
            inp = assemble_input(project, r, tasks_data)
        except ApiError as e:
            if e.code == "HANDOFF_STALE":
                plan["status"] = "awaiting-confirm"
                _save_plan(project, plan)
                raise
            raise
        compressed, blocked = _budget_check(inp, eff)
        if blocked:
            r["status"] = "blocked"
            plan["status"] = "suspended"
            plan["resume_hint"] = f"{r['ring_id']} 超预算且压缩后仍超限（BUDGET_EXCEEDED_BLOCKED），请回步骤③拆分上游任务"
            _save_plan(project, plan)
            raise budget_exceeded({"ring": r["ring_id"], "chars": inp["chars"], "effective": eff})
    plan["status"] = "running"
    plan["outbound_confirmed_at"] = outbound_confirmed_at
    _save_plan(project, plan)
    audit("plan.confirm", project=project, detail={"rings": len(plan["rings"])})
    asyncio.get_event_loop().create_task(_run_plan(project))
    return plan


async def _run_plan(project: str) -> None:
    plan = load_json(_plan_path(project))
    tasks_data = get_tasks(project)
    eff = effective_budget(tasks_data["budget"])
    total = len(plan["rings"])
    done_n = 0
    while True:
        plan = load_json(_plan_path(project))
        if plan["status"] not in ("running",):
            return
        rings = plan["rings"]
        done_n = sum(1 for r in rings if r["status"] == "done")
        ready = [r for r in rings if r["status"] == "pending"
                 and all(by_id(rings, d).get("status") == "done" for d in r.get("depends_rings", []))]
        blocked_left = any(r["status"] in ("blocked", "suspended", "failed") for r in rings)
        if not ready:
            if all(r["status"] == "done" for r in rings):
                plan["status"] = "done"
                _save_plan(project, plan)
                await events.publish("plan.status", {"plan_id": plan["id"], "status": "done",
                                                     "done_rings": total, "total": total})
                audit("plan.done", project=project)
            elif blocked_left:
                plan["status"] = "suspended"
                suspended = next(r for r in rings if r["status"] in ("blocked", "suspended", "failed"))
                plan["resume_hint"] = f"挂起环：{suspended['ring_id']}（{suspended.get('last_error', 'unknown')}）"
                _save_plan(project, plan)
                await events.publish("ring.suspended", {"ring_id": suspended["ring_id"],
                                                        "error_code": suspended.get("last_error", "")})
            return
        batch = ready[:PARALLELISM]
        await asyncio.gather(*(_execute_ring(project, r, tasks_data, eff) for r in batch))


def by_id(rings: list[dict], rid: str) -> dict:
    return next((r for r in rings if r["ring_id"] == rid), {"status": "unknown"})


async def _execute_ring(project: str, ring: dict, tasks_data: dict, eff: int) -> None:
    from .provider import Message, get_provider
    plan = load_json(_plan_path(project))
    ring = next(r for r in plan["rings"] if r["ring_id"] == ring["ring_id"])
    provider = get_provider(project)
    ring["status"] = "running"
    ring["provider"] = provider.name
    ring["model"] = provider.model
    ring["started_at"] = now_iso()
    ring["confirmed_at"] = plan.get("outbound_confirmed_at")
    _save_plan(project, plan)
    await events.publish("ring.started", {"ring_id": ring["ring_id"], "task": ring["task"],
                                          "module": ring["module"]})
    try:
        inp = assemble_input(project, ring, tasks_data)
    except ApiError as e:
        ring["status"] = "suspended"
        ring["last_error"] = e.code
        plan["status"] = "suspended"
        plan["resume_hint"] = f"{ring['ring_id']} 阻断：{e.code}"
        _save_plan(project, plan)
        await events.publish("ring.failed", {"ring_id": ring["ring_id"], "error_code": e.code})
        return
    compressed, blocked = _budget_check(inp, eff)
    if blocked:
        ring["status"] = "blocked"
        ring["last_error"] = "BUDGET_EXCEEDED_BLOCKED"
        plan["status"] = "suspended"
        plan["resume_hint"] = f"{ring['ring_id']} 超预算阻断，请拆分上游任务"
        _save_plan(project, plan)
        await events.publish("ring.failed", {"ring_id": ring["ring_id"],
                                             "error_code": "BUDGET_EXCEEDED_BLOCKED"})
        return
    if compressed:
        inp = compressed

    chunks: list[str] = []

    async def cb(delta: str) -> None:
        chunks.append(delta)
        await events.publish("ring.delta", {"ring_id": ring["ring_id"], "delta": delta})

    from .provider import ProviderError
    attempt = 0
    while attempt <= MAX_RETRIES:
        try:
            result = await provider.chat([Message("system", inp["role"]),
                                          Message("user", f"{inp['global']}\n\n{inp['task']}\n\n{inp['upstream']}")],
                                         stream_cb=cb)
            break
        except ProviderError as e:
            attempt += 1
            ring["retries"] = attempt
            ring["last_error"] = e.code
            _save_plan(project, plan)
            if not e.retryable or attempt > MAX_RETRIES:
                ring["status"] = "suspended" if not e.retryable else "failed"
                plan["status"] = "suspended"
                plan["resume_hint"] = f"挂起环：{ring['ring_id']}（重试 {attempt} 次失败：{e.code}）"
                _save_plan(project, plan)
                await events.publish("ring.suspended", {"ring_id": ring["ring_id"],
                                                        "error_code": e.code, "retries": attempt})
                return
            await asyncio.sleep(0.5 * attempt)
    else:
        return

    body, handoff, handoff_ok = _split_handoff(result.text)
    _persist_ring(project, ring, inp, body, handoff, handoff_ok, result)
    _write_prompt_v1(project, ring, body)
    ring["status"] = "done"
    ring["finished_at"] = now_iso()
    plan = load_json(_plan_path(project))
    ring = next(r for r in plan["rings"] if r["ring_id"] == ring["ring_id"])
    ring["status"] = "done"
    ring["finished_at"] = now_iso()
    _save_plan(project, plan)
    done = sum(1 for r in plan["rings"] if r["status"] == "done")
    await events.publish("ring.completed", {"ring_id": ring["ring_id"], "task": ring["task"],
                                            "module": ring["module"], "output_chars": len(body),
                                            "budget_actual": inp["chars"],
                                            "handoff_chars": len(handoff)})
    await events.publish("plan.status", {"plan_id": plan["id"], "status": plan["status"],
                                         "done_rings": done, "total": len(plan["rings"])})


def _split_handoff(text: str) -> tuple[str, str, bool]:
    if _HANDOFF_SPLIT in text:
        body, _, handoff = text.partition(_HANDOFF_SPLIT)
        return body.strip(), handoff.strip()[:600], True
    return text.strip(), "", False


def _persist_ring(project: str, ring: dict, inp: dict, body: str, handoff: str,
                  handoff_ok: bool, result) -> None:
    rdir = _ring_dir(project, ring["ring_id"])
    rdir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(rdir / "input.md",
                      f"{inp['role']}\n\n{inp['global']}\n\n{inp['task']}\n\n{inp['upstream']}")
    atomic_write_text(rdir / "output.md", body)
    atomic_write_text(rdir / "handoff.md", with_front_matter(
        {"based_on_version": 1, "based_on_ring": ring["ring_id"], "valid": handoff_ok}, handoff + "\n"))
    atomic_write_json(rdir / "meta.json", {
        "ring_id": ring["ring_id"], "task": ring["task"], "module": ring["module"],
        "status": "done", "retries": ring.get("retries", 0),
        "budget": {"effective": effective_budget(get_tasks(project)["budget"]),
                   "actual_chars": inp["chars"]},
        "provider": ring.get("provider"), "model": ring.get("model"),
        "started_at": ring.get("started_at"), "finished_at": now_iso(),
        "confirmed_at": ring.get("confirmed_at"), "usage": result.usage,
        "handoff_valid": handoff_ok,
    })


def next_prompt_version(project: str, module: str, task: str) -> int:
    pdir = _prompt_dir(project, module, task)
    if not pdir.exists():
        return 1
    versions = [0]
    for f in pdir.glob("v*.md"):
        m = re.fullmatch(r"v(\d+)\.md", f.name)
        if m:
            versions.append(int(m.group(1)))
    return max(versions) + 1


def _write_prompt_v1(project: str, ring: dict, body: str) -> None:
    """Ring output lands as a draft prompt version (预览态, FR-04 规则 6)."""
    version = next_prompt_version(project, ring["module"], ring["task"])
    path = _prompt_dir(project, ring["module"], ring["task"]) / f"v{version}.md"
    tasks_data = get_tasks(project)
    meta = {"prompt_id": f"P-{ring['task']}-{ring['module']}-v{version}", "version": version,
            "module": ring["module"], "task_id": ring["task"], "status": "draft",
            "generated_at": now_iso(),
            "gen_params": {"complexity": tasks_data.get("complexity"),
                           "budget_coefficient": tasks_data["budget"].get("coefficient", 0.6),
                           "constraints_package_version": package_version(),
                           "rules_md_digest": f"sha256:{__import__('hashlib').sha256(_rules_md(project).encode()).hexdigest()[:16]}",
                           "ring_ref": f"orchestration/{ring['ring_id']}"},
            "tool_guides": [g["name"] for g in enabled_guides(project, _module_category(ring["module"]))],
            "sanitized": True}
    atomic_write_text(path, with_front_matter(meta, body + "\n"))


# ------------------------------------------------------------------ resume

async def resume_plan(project: str) -> dict:
    plan = load_json(_plan_path(project))
    if plan["status"] not in ("suspended", "paused"):
        raise ApiError("PLAN_NOT_SUSPENDED", 409, "计划未处于挂起/暂停状态", {"status": plan["status"]})
    for r in plan["rings"]:
        if r["status"] in ("failed", "suspended"):
            r["status"] = "pending"
            r["retries"] = 0
    plan["status"] = "running"
    plan.pop("resume_hint", None)
    _save_plan(project, plan)
    audit("plan.resume", project=project)
    asyncio.get_event_loop().create_task(_run_plan(project))
    return plan


async def pause_plan(project: str) -> dict:
    plan = load_json(_plan_path(project))
    if plan["status"] != "running":
        raise ApiError("PLAN_NOT_RUNNING", 409, "计划未在运行", {"status": plan["status"]})
    plan["status"] = "paused"
    for r in plan["rings"]:
        if r["status"] == "running":
            r["status"] = "pending"
    _save_plan(project, plan)
    return plan


def get_ring(project: str, ring_id: str) -> dict:
    plan = load_json(_plan_path(project))
    ring = next((r for r in plan["rings"] if r["ring_id"] == ring_id), None)
    if ring is None:
        raise ApiError("RING_NOT_FOUND", 404, "环不存在", {"ring_id": ring_id})
    rdir = _ring_dir(project, ring_id)
    files = {}
    for name in ("input.md", "output.md", "handoff.md", "meta.json"):
        p = rdir / name
        files[name] = read_text(p) if p.exists() else None
    return {"ring": ring, "files": files}
