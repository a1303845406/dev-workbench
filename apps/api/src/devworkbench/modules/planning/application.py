"""Transactional, version-bound human review; AI never mutates accepted documents."""
from __future__ import annotations

import asyncio
import copy
import json
import uuid
import httpx
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError
from devworkbench.infrastructure.persistence.database import Database, jdump, now
from devworkbench.shared.errors import DomainError
from devworkbench.infrastructure.models.settings import ModelSettings
from devworkbench.infrastructure.models.provider import Provider
from devworkbench.modules.planning.prompts import BUILTINS, prompt_for
from devworkbench.infrastructure.search.bing import search_web
from devworkbench.modules.planning.domain import normalize, review_checks

KINDS = ("requirement", "decomposition", "design")
JOBS: dict[tuple[str, str], asyncio.Task] = {}

def uid():
    return uuid.uuid4().hex

def version(state, version_id):
    found = next((v for v in state["versions"] if v["id"] == version_id), None)
    if not found:
        raise DomainError("VERSION_NOT_FOUND", "版本不存在或不属于当前项目", 404)
    return found

def current(state, kind):
    key = state["current"][kind]
    return version(state, key) if key else None

def gate(state, kind):
    required = KINDS[:KINDS.index(kind)]
    for upstream in required:
        item = current(state, upstream)
        if not item or item["status"] != "approved":
            raise DomainError("REVIEW_REQUIRED", "请先人工批准当前需求基线和上游拆分版本", 409)

def lineage(state, item):
    gate(state, item["kind"])
    for kind in KINDS[:KINDS.index(item["kind"])]:
        if item[kind + "_version_id"] != state["current"][kind]:
            raise DomainError("STALE_VERSION", "上游版本已变化，请基于当前版本重新生成", 409)

def invalidate(state, kind):
    downstream = KINDS[KINDS.index(kind) + 1:]
    for item in state["versions"]:
        if item["kind"] in downstream and item["status"] not in ("rejected", "stale"):
            item["status"] = "stale"

def new_version(state, kind, data, origin, base=None):
    try:
        data = normalize(kind, data)
    except (ValidationError, ValueError, KeyError):
        raise DomainError("INVALID_PROPOSAL", "文档结构无效：请检查必填字段、树归属和依赖循环", 422)
    base = base or state["current"]
    req = version(state, base["requirement"])["data"] if base["requirement"] else None
    split = version(state, base["decomposition"])["data"] if base["decomposition"] else None
    item = {"id": uid(), "kind": kind, "number": 1 + sum(v["kind"] == kind for v in state["versions"]),
            "data": data, "origin": origin, "status": "proposed" if origin == "ai" else "draft", "based_on": base[kind],
            "requirement_version_id": base["requirement"] if kind != "requirement" else None,
            "decomposition_version_id": base["decomposition"] if kind == "design" else None,
            "created_at": now(), "checks": review_checks(kind, data, req, split), "reviews": []}
    state["versions"].append(item)
    return item

class Planning:
    def __init__(self, db: Database, root: Path, provider=None):
        self.db, self.root = db, root
        self.provider = provider or Provider(ModelSettings(root).effective())

    def load(self, conn, project_id):
        project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            raise DomainError("PROJECT_NOT_FOUND", "项目不存在", 404)
        row = conn.execute("SELECT state_json FROM planning_workspaces WHERE project_id=?", (project_id,)).fetchone()
        if row:
            return json.loads(row["state_json"])
        state = {"revision": 0, "messages": [], "versions": [], "current": dict.fromkeys(KINDS), "runs": [], "sources": copy.deepcopy(BUILTINS)}
        legacy = conn.execute("SELECT v.content FROM artifacts a JOIN artifact_versions v ON a.current_version_id=v.id WHERE a.project_id=? AND a.kind='requirement' LIMIT 1", (project_id,)).fetchone()
        if legacy and legacy["content"].strip():
            state["messages"].append({"id": uid(), "role": "user", "content": legacy["content"], "created_at": now()})
            draft = new_version(state, "requirement", {"document": legacy["content"], "unresolved": ["旧版底稿导入，请共同梳理为标准需求后重新审查"]}, "human")
            state["current"]["requirement"] = draft["id"]
        conn.execute("INSERT INTO planning_workspaces VALUES(?,?)", (project_id, jdump(state)))
        return state

    def persist(self, conn, project_id, state, event):
        state["revision"] += 1
        conn.execute("UPDATE planning_workspaces SET state_json=? WHERE project_id=?", (jdump(state), project_id))
        conn.execute("INSERT INTO events(project_id,event_type,entity_type,entity_id,payload_json,actor,created_at) VALUES(?,?,?,?,?,?,?)", (project_id, event, "PlanningWorkspace", project_id, jdump({"revision": state["revision"]}), "planning", now()))

    @contextmanager
    def change(self, project_id, expected, event):
        with self.db.tx() as conn:
            state = self.load(conn, project_id)
            if expected is not None and expected != state["revision"]:
                raise DomainError("REVISION_CONFLICT", "其他操作更新了项目，请刷新后重试；未保存内容不会被覆盖", 409)
            yield state
            self.persist(conn, project_id, state, event)

    def get(self, project_id):
        with self.db.tx() as conn:
            state = self.load(conn, project_id)
        state["provider_mode"] = self.provider.mode
        state["execution_locked"] = True
        state["stage"] = "requirements"
        req, split, design = (current(state, k) for k in KINDS)
        if req and req["status"] == "approved":
            state["stage"] = "decomposition"
            if split and split["status"] == "approved":
                state["stage"] = "design"
                if design and design["status"] == "approved":
                    state["stage"] = "design_approved"
        return state

    def save(self, project_id, expected, kind, data):
        with self.change(project_id, expected, "planning_draft_saved") as state:
            gate(state, kind)
            item = new_version(state, kind, data, "human")
            invalidate(state, kind)
            state["current"][kind] = item["id"]
        return self.get(project_id)

    def act(self, project_id, expected, version_id, action, comment=""):
        with self.change(project_id, expected, "planning_" + action) as state:
            item = version(state, version_id)
            if action in ("adopt", "reject"):
                if item["status"] != "proposed":
                    raise DomainError("NOT_PROPOSED", "只能处理尚未采纳的候选版本", 409)
                if action == "reject":
                    item["status"] = "rejected"
                else:
                    lineage(state, item)
                    if item["based_on"] != state["current"][item["kind"]]:
                        raise DomainError("STALE_VERSION", "当前底稿已变化，不能覆盖，请重新生成候选稿", 409)
                    invalidate(state, item["kind"])
                    item["status"] = "draft"
                    state["current"][item["kind"]] = item["id"]
            else:
                if state["current"][item["kind"]] != item["id"] or item["status"] not in ("draft", "changes_requested", "approved"):
                    raise DomainError("NOT_CURRENT", "仅可审查当前有效底稿；候选稿请先采纳", 409)
                lineage(state, item)
                if action == "approve":
                    if item["checks"]:
                        raise DomainError("REVIEW_BLOCKED", "请先解决结构检查和待确认项", 409, item["checks"])
                    item["status"] = "approved"
                elif action == "changes_requested":
                    if not comment.strip():
                        raise DomainError("COMMENT_REQUIRED", "请填写返工意见", 422)
                    item["status"] = "changes_requested"
                    invalidate(state, item["kind"])
                else:
                    raise DomainError("INVALID_ACTION", "未知审查操作", 422)
                item["review_comment"] = comment
                item["reviews"].append({"decision": action, "comment": comment, "created_at": now(), "version_id": item["id"]})
        return self.get(project_id)

    def source(self, project_id, expected, title, kind, content):
        with self.change(project_id, expected, "planning_source_added") as state:
            if len(state["sources"]) >= 40:
                raise DomainError("SOURCE_LIMIT", "最多保存40份项目资料，请精简后再加入", 422)
            state["sources"].append({"id": uid(), "title": title, "kind": kind, "content": content, "version": "1", "created_at": now()})
        return self.get(project_id)

    def begin(self, project_id, body):
        if self.provider.mode not in ("real", "codex"):
            raise DomainError("MODEL_REQUIRED", "当前为规则模式，不会模拟 AI 对话。请先配置 API 或 Codex 模型服务", 409)
        if not body["allow_external"]:
            raise DomainError("CONSENT_REQUIRED", "请确认允许把本项目对话、文档和资料发送给所选模型服务", 422)
        if body["allow_search"] and not body["search_query"].strip():
            raise DomainError("QUERY_REQUIRED", "联网检索需明确填写可公开的查询词，不自动发送私有需求", 422)
        kind = {"discuss": "requirement", "requirements": "requirement", "decomposition": "decomposition", "design": "design"}[body["action"]]
        with self.change(project_id, body["expected_revision"], "planning_run_started") as state:
            gate(state, kind)
            if any(r["status"] == "running" for r in state["runs"]):
                raise DomainError("RUN_ACTIVE", "当前项目已有生成任务，请等待或取消", 409)
            target = body.get("target_node_id")
            if target:
                split = current(state, "decomposition")
                if kind != "decomposition" or not split or not any(n["id"] == target for n in split["data"]["nodes"]):
                    raise DomainError("NODE_NOT_FOUND", "局部拆分目标不存在", 422)
            run = {"id": uid(), "action": body["action"], "kind": kind, "status": "running", "error": "", "created_at": now(),
                   "provider_mode": self.provider.mode, "evidence": [], "tools": [], "base": copy.deepcopy(state["current"]), "request": body}
            state["runs"].append(run)
            state["messages"].append({"id": uid(), "role": "user", "content": body["message"] or "请生成当前阶段的完整候选稿", "created_at": now(), "run_id": run["id"]})
        return run["id"]

    def launch(self, project_id, run_id):
        key = (str(self.db.path), run_id)
        task = asyncio.create_task(self.generate(project_id, run_id))
        JOBS[key] = task
        task.add_done_callback(lambda _: JOBS.pop(key, None))

    def cancel(self, project_id, expected, run_id):
        with self.change(project_id, expected, "planning_run_cancelled") as state:
            run = next((r for r in state["runs"] if r["id"] == run_id), None)
            if not run or run["status"] != "running":
                raise DomainError("RUN_NOT_ACTIVE", "任务已结束或不存在", 409)
            run["status"] = "cancelled"
            run["error"] = "已取消；不会采纳迟到的模型结果。外部服务可能仍计费。"
        task = JOBS.get((str(self.db.path), run_id))
        if task:
            task.cancel()
        return self.get(project_id)

    async def generate(self, project_id, run_id):
        evidence, tool_log, raw = [], [], ""
        try:
            state = self.get(project_id)
            run = next(r for r in state["runs"] if r["id"] == run_id)
            if run["status"] != "running":
                return
            request = run["request"]
            # Project-scoped sources only. Each supplied excerpt is retained in run evidence.
            evidence = copy.deepcopy(state["sources"])
            tool_log.append({"tool": "project_context", "status": "completed", "message": f"读取 {len(evidence)} 份内置知识/项目资料/人工经验；未读取其他项目"})
            if request["allow_search"]:
                try:
                    results = await search_web(request["search_query"])
                    evidence.extend(results)
                    tool_log.append({"tool": "web_search", "status": "completed" if results else "empty", "message": f"公开查询：{request['search_query']}；{len(results)} 条搜索摘要（未抓取全文）"})
                except Exception:
                    tool_log.append({"tool": "web_search", "status": "failed", "message": "联网检索失败，本轮不使用网页证据"})
            context = {"action": run["action"], "instruction": request["message"], "target_node_id": request.get("target_node_id"),
                       "history": state["messages"], "documents": {k: version(state, v)["data"] if v else None for k, v in run["base"].items()},
                       "review_feedback": {k: version(state, v)["reviews"] if v else [] for k, v in run["base"].items()},
                       "sources": evidence, "tools": tool_log}
            encoded = jdump(context)
            if len(encoded) > 450000:
                raise DomainError("CONTEXT_LIMIT", "当前项目上下文过大，请精简资料后重试", 422)
            try:
                raw = (await asyncio.wait_for(self.provider.complete(prompt_for(run["action"]), encoded), timeout=110)).strip()
            finally:
                metadata = getattr(self.provider, "last_completion", {})
                tool_log.append({"tool": "model_completion", "status": "completed" if metadata else "attempted",
                                 "message": "模型调用；计量来自供应商响应，未返回计量时不估算费用", **metadata})
            if self.provider.api_key:
                raw = raw.replace(self.provider.api_key, "[REDACTED]")
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            if len(raw) > 500000:
                raise ValueError("response too large")
            response = json.loads(raw)
            if not isinstance(response, dict) or not isinstance(response.get("reply"), str) or not response["reply"].strip():
                raise DomainError("MODEL_ENVELOPE", "模型响应缺少非空 reply 字符串，请按 JSON 契约重试", 422)
            citations = response.get("citations", [])
            if not isinstance(citations, list) or any(not isinstance(c, str) or c not in {s["id"] for s in evidence} for c in citations):
                raise DomainError("MODEL_CITATIONS", "模型引用了未提供的来源 ID，候选稿未采纳", 422)
            proposal = response.get("proposal")
            if proposal is None and run["action"] != "discuss":
                raise DomainError("MODEL_PROPOSAL_MISSING", "本轮要求生成文档，但模型没有返回 proposal 候选数据", 422)
            if proposal is not None:
                proposal = normalize(run["kind"], proposal)
                if request.get("target_node_id"):
                    self.check_local(context["documents"]["decomposition"], proposal, request["target_node_id"])
            with self.change(project_id, None, "planning_run_completed") as latest:
                live = next(r for r in latest["runs"] if r["id"] == run_id)
                if live["status"] != "running":
                    return
                live.update(status="completed", evidence=evidence, tools=tool_log, citations=citations)
                latest["messages"].append({"id": uid(), "role": "assistant", "content": response["reply"], "created_at": now(), "run_id": run_id})
                if proposal is not None:
                    item = new_version(latest, run["kind"], proposal, "ai", run["base"])
                    live["result_version_id"] = item["id"]
                    try:
                        lineage(latest, item)
                        if item["based_on"] != latest["current"][item["kind"]]:
                            item["status"] = "stale"
                    except DomainError:
                        item["status"] = "stale"
        except asyncio.CancelledError:
            self.fail(project_id, run_id, "interrupted", "服务停止或任务取消；请人工重新发起", evidence, tool_log)
        except Exception as exc:
            if isinstance(exc, DomainError):
                message = exc.message
            elif isinstance(exc, json.JSONDecodeError):
                message = f"模型输出不是完整有效的 JSON（行 {exc.lineno}，列 {exc.colno}），请要求严格 JSON 后人工重试；原文和审批不变。"
            elif isinstance(exc, ValidationError):
                safe_fields = {"document", "unresolved", "checks", "overview", "nodes", "id", "parent_id", "type", "title", "responsibility", "exclusions", "requirement_ids", "acceptance", "dependencies", "rationale", "detailed_design", "prototype_html", "data_design"}
                safe_graph = {"节点 ID 重复", "父节点必须是存在的模块", "特性必须归属于模块", "依赖不存在或引用自身", "模块归属或依赖存在循环"}
                details = []
                for issue in exc.errors(include_input=False, include_url=False)[:5]:
                    path = ".".join(str(p) if isinstance(p, int) or p in safe_fields else "未知字段" for p in issue["loc"]) or "根对象"
                    graph = str(issue.get("ctx", {}).get("error", ""))
                    details.append(path + "：" + (graph if graph in safe_graph else "字段类型/必填项不符合契约"))
                message = f"候选数据不符合结构契约（{exc.error_count()} 项错误）：" + "；".join(details)
            elif isinstance(exc, httpx.HTTPStatusError):
                message = f"模型服务返回 HTTP {exc.response.status_code}，请检查配置、配额或稍后人工重试。"
            elif isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
                message = "模型请求超时，未自动重试；供应商可能已计费，请先核对记录。"
            elif isinstance(exc, httpx.RequestError):
                message = "模型网络连接失败，请检查网络或服务地址；未自动重试。"
            else:
                message = "模型调用或候选稿校验失败。请检查连接、输出格式后重试；原文和审批不变。"
            self.fail(project_id, run_id, "failed", message, evidence, tool_log, raw[:500000])

    def fail(self, project_id, run_id, status, error, evidence, logs, response_text=""):
        with self.change(project_id, None, "planning_run_" + status) as state:
            run = next(r for r in state["runs"] if r["id"] == run_id)
            if run["status"] == "running":
                run.update(status=status, error=error, evidence=evidence, tools=logs)
                if response_text:
                    run["response_text"] = response_text

    @staticmethod
    def check_local(old, new, target):
        branch = {target}
        while True:
            expanded = branch | {n["id"] for n in old["nodes"] if n["parent_id"] in branch}
            if expanded == branch:
                break
            branch = expanded
        before = {n["id"]: n for n in old["nodes"]}
        after = {n["id"]: n for n in new["nodes"]}
        if target not in after or after[target]["parent_id"] != before[target]["parent_id"]:
            raise ValueError("local target moved")
        if any(after.get(key) != value for key, value in before.items() if key not in branch):
            raise ValueError("local split changed unrelated nodes")
        if old["overview"] != new["overview"]:
            raise ValueError("local split changed overall design")
        for key in (after.keys() - before.keys()) | ((branch - {target}) & after.keys()):
            parent = after[key]["parent_id"]
            while parent and parent != target:
                parent = after[parent]["parent_id"]
            if parent != target:
                raise ValueError("new node outside target subtree")

    def recover(self):
        with self.db.tx() as conn:
            for row in conn.execute("SELECT project_id,state_json FROM planning_workspaces").fetchall():
                state = json.loads(row["state_json"])
                changed = False
                for run in state["runs"]:
                    if run["status"] == "running":
                        run.update(status="interrupted", error="服务已重启，未自动重试或采纳，请重新发起")
                        changed = True
                if changed:
                    self.persist(conn, row["project_id"], state, "planning_recovered")

async def shutdown_jobs():
    tasks = list(JOBS.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
