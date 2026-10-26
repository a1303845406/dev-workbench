from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .ci import run_command
from .db import Database, jdump, jload, now
from .errors import DomainError
from .provider import Provider
from .workspace import WorkspaceScanner


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def row(row: Any) -> dict[str, Any] | None:
    return dict(row) if row else None


def unpack(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if data is None:
        return None
    for key in list(data):
        if key.endswith("_json"):
            value = data.pop(key)
            data[key[:-5]] = jload(value, [])
    return data


class Workbench:
    def __init__(self, db: Database, root: Path):
        self.db = db
        self.root = root
        self.provider = Provider()

    def event(self, conn, project_id: str, event_type: str, entity_type: str, entity_id: str, payload: Any, actor: str = "system") -> None:
        conn.execute("INSERT INTO events(project_id,event_type,entity_type,entity_id,payload_json,actor,created_at) VALUES(?,?,?,?,?,?,?)", (project_id, event_type, entity_type, entity_id, jdump(payload), actor, now()))

    def project(self, project_id: str) -> dict[str, Any]:
        data = row(self.db.one("SELECT * FROM projects WHERE id=?", (project_id,)))
        if not data:
            raise DomainError("PROJECT_NOT_FOUND", "project not found", 404)
        return data

    def create_project(self, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("name", "")).strip()
        if not name:
            raise DomainError("INVALID_PROJECT", "name is required")
        project_id, repository_id, timestamp = uid("prj"), uid("repo"), now()
        workspace = Path(data.get("workspace_path") or (self.root / "projects" / project_id / "workspace")).resolve()
        created_dirs: list[Path] = []
        try:
            with self.db.tx() as conn:
                existing_workspaces = [Path(item["workspace_path"]).resolve() for item in conn.execute("SELECT workspace_path FROM projects")]
                if any(workspace == existing or workspace in existing.parents or existing in workspace.parents for existing in existing_workspaces):
                    raise DomainError("WORKSPACE_IN_USE", "workspace overlaps another project workspace", 409)
                cursor = workspace
                while not cursor.exists() and cursor != cursor.parent:
                    created_dirs.append(cursor)
                    cursor = cursor.parent
                workspace.mkdir(parents=True, exist_ok=True)
                conn.execute("INSERT INTO projects(id,name,workspace_path,created_at,updated_at) VALUES(?,?,?,?,?)", (project_id, name, str(workspace), timestamp, timestamp))
                repository_name = str(data.get("repository_name") or "主仓库").strip() or "主仓库"
                conn.execute("INSERT INTO repositories(id,project_id,name,path,branch,commit_sha,created_at) VALUES(?,?,?,?,?,?,?)", (repository_id, project_id, repository_name, str(workspace), data.get("branch", ""), data.get("commit_sha", ""), timestamp))
                self.event(conn, project_id, "project_created", "Project", project_id, {"name": name})
        except Exception as exc:
            for directory in created_dirs:
                try:
                    directory.rmdir()
                except OSError:
                    pass
            if "UNIQUE" in str(exc):
                raise DomainError("PROJECT_EXISTS", "project name already exists", 409) from exc
            raise
        try:
            requirement = str(data.get("requirement", "")).strip()
            for kind, title in [("requirement", "立项需求"), ("tech-stack", "技术选型"), ("design-outline", "全局设计"), ("prototype", "原型"), ("database", "数据基线"), ("scaffold", "脚手架")]:
                self.create_artifact(project_id, kind, title, requirement if kind == "requirement" else f"{title}（待完善）")
            self.capture_baseline(project_id, repository_id)
        except Exception:
            with self.db.tx() as conn:
                conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
            for directory in created_dirs:
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise
        return self.get_project(project_id)

    def list_projects(self) -> list[dict[str, Any]]:
        return [self.summary(row(item)) for item in self.db.all("SELECT * FROM projects ORDER BY created_at DESC")]

    def summary(self, project: dict[str, Any]) -> dict[str, Any]:
        project["task_counts"] = {item["plan_status"]: item["count"] for item in self.db.all("SELECT plan_status,COUNT(*) AS count FROM task_cards WHERE project_id=? GROUP BY plan_status", (project["id"],))}
        project["artifact_count"] = self.db.one("SELECT COUNT(*) AS count FROM artifacts WHERE project_id=?", (project["id"],))["count"]
        project["open_decisions"] = self.db.one("SELECT COUNT(*) AS count FROM decisions WHERE project_id=? AND status='open'", (project["id"],))["count"]
        return project

    def repository_status(self, repository: dict[str, Any]) -> dict[str, Any]:
        path = Path(repository["path"]).resolve()
        result: dict[str, Any] = {"exists": path.is_dir(), "is_git": False, "branch": repository.get("branch", ""), "commit_sha": repository.get("commit_sha", ""), "changed_files": [], "state": "missing"}
        if not path.is_dir():
            return result
        def git_value(args: list[str]) -> tuple[int, str]:
            try:
                completed = subprocess.run(["git", *args], cwd=path, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=10, check=False)
                return completed.returncode, completed.stdout
            except (OSError, subprocess.SubprocessError):
                return 1, ""
        branch_code, branch_raw = git_value(["branch", "--show-current"])
        commit_code, commit_raw = git_value(["rev-parse", "HEAD"])
        status_code, status_raw = git_value(["status", "--porcelain", "--untracked-files=all"])
        is_git = branch_code == 0 and commit_code == 0
        changed = []
        if is_git and status_code == 0:
            for line in status_raw.splitlines():
                if len(line) >= 4:
                    changed.append(line[3:].rstrip().replace(chr(92), "/").strip(chr(34)))
        branch = branch_raw.rstrip("\r\n") if is_git else result["branch"]
        commit = commit_raw.rstrip("\r\n") if is_git else result["commit_sha"]
        result.update({"is_git": is_git, "branch": branch, "commit_sha": commit, "changed_files": changed, "state": "dirty" if changed else ("clean" if is_git else "not_git")})
        return result

    def register_repository(self, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self.project(project_id)
        name = str(data.get("name") or "主仓库").strip() or "主仓库"
        path_value = str(data.get("path", "")).strip()
        if not path_value:
            raise DomainError("INVALID_REPOSITORY", "path is required", 422)
        path = Path(path_value).expanduser().resolve()
        if not path.is_dir():
            raise DomainError("REPOSITORY_NOT_FOUND", "repository path does not exist", 404)
        if not (path / ".git").exists():
            probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path, text=True, capture_output=True, timeout=10, check=False)
            if probe.returncode == 0:
                raise DomainError("REPOSITORY_NOT_ROOT", "repository path must be a repository root", 422)
        repository_id, timestamp = uid("repo"), now()
        with self.db.tx() as conn:
            duplicate = conn.execute("SELECT id FROM repositories WHERE project_id=? AND path=?", (project_id, str(path))).fetchone()
            if duplicate:
                raise DomainError("REPOSITORY_EXISTS", "repository path already registered in project", 409)
            conn.execute("INSERT INTO repositories(id,project_id,name,path,branch,commit_sha,created_at) VALUES(?,?,?,?,?,?,?)", (repository_id, project_id, name, str(path), data.get("branch", ""), data.get("commit_sha", ""), timestamp))
            self.event(conn, project_id, "repository_registered", "Repository", repository_id, {"name": name, "path": str(path)})
        return self.get_project(project_id)["repositories"][-1]

    def get_project(self, project_id: str) -> dict[str, Any]:
        project = self.summary(self.project(project_id))
        project["repositories"] = [row(item) for item in self.db.all("SELECT * FROM repositories WHERE project_id=?", (project_id,))]
        for repository in project["repositories"]:
            repository["status"] = self.repository_status(repository)
        project["artifacts"] = self.artifacts(project_id)
        project["modules"] = [row(item) for item in self.db.all("SELECT * FROM modules WHERE project_id=? ORDER BY created_at", (project_id,))]
        project["features"] = [row(item) for item in self.db.all("SELECT * FROM features WHERE project_id=? ORDER BY created_at", (project_id,))]
        project["tasks"] = self.tasks(project_id)
        project["events"] = self.events(project_id)[:50]
        project["decisions"] = self.decisions(project_id)
        return project

    def create_artifact(self, project_id: str, kind: str, title: str, content: str, status: str = "draft", based_on: str | None = None) -> dict[str, Any]:
        self.project(project_id)
        artifact_id, version_id, timestamp = uid("art"), uid("av"), now()
        with self.db.tx() as conn:
            conn.execute("INSERT INTO artifacts(id,project_id,kind,title,created_at) VALUES(?,?,?,?,?)", (artifact_id, project_id, kind, title, timestamp))
            conn.execute("INSERT INTO artifact_versions(id,artifact_id,version,content,content_sha,status,based_on_version_id,created_at) VALUES(?,?,?,?,?,?,?,?)", (version_id, artifact_id, "v1.0", content, sha(content), status, based_on, timestamp))
            conn.execute("UPDATE artifacts SET current_version_id=? WHERE id=?", (version_id, artifact_id))
            self.event(conn, project_id, "artifact_version_created", "ArtifactVersion", version_id, {"artifact_id": artifact_id, "version": "v1.0"})
        return self.artifact(artifact_id)

    def artifact(self, artifact_id: str) -> dict[str, Any]:
        data = row(self.db.one("SELECT a.*,v.id AS version_id,v.version,v.content,v.status AS version_status,v.content_sha FROM artifacts a JOIN artifact_versions v ON v.id=a.current_version_id WHERE a.id=?", (artifact_id,)))
        if not data:
            raise DomainError("ARTIFACT_NOT_FOUND", "artifact not found", 404)
        return data

    def artifacts(self, project_id: str) -> list[dict[str, Any]]:
        return [self.artifact(item["id"]) for item in self.db.all("SELECT id FROM artifacts WHERE project_id=? ORDER BY created_at", (project_id,))]

    def new_version(self, artifact_id: str, data: dict[str, Any]) -> dict[str, Any]:
        old = self.artifact(artifact_id)
        count = self.db.one("SELECT COUNT(*) AS count FROM artifact_versions WHERE artifact_id=?", (artifact_id,))["count"]
        version_id, timestamp = uid("av"), now()
        with self.db.tx() as conn:
            conn.execute("INSERT INTO artifact_versions(id,artifact_id,version,content,content_sha,status,based_on_version_id,created_at) VALUES(?,?,?,?,?,?,?,?)", (version_id, artifact_id, f"v{count + 1}.0", data.get("content", ""), sha(data.get("content", "")), data.get("status", "draft"), old["version_id"], timestamp))
            conn.execute("UPDATE artifacts SET current_version_id=? WHERE id=?", (version_id, artifact_id))
            self.event(conn, old["project_id"], "artifact_version_created", "ArtifactVersion", version_id, {"artifact_id": artifact_id, "based_on": old["version_id"]})
        return self.artifact(artifact_id)

    def create_module(self, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self.project(project_id)
        if not str(data.get("name", "")).strip():
            raise DomainError("INVALID_MODULE", "name is required", 422)
        parent_id = data.get("parent_id")
        if parent_id and not self.db.one("SELECT id FROM modules WHERE id=? AND project_id=?", (parent_id, project_id)):
            raise DomainError("PARENT_MODULE_NOT_FOUND", "parent module not found in project", 404)
        module_id, timestamp = uid("mod"), now()
        with self.db.tx() as conn:
            conn.execute("INSERT INTO modules(id,project_id,parent_id,name,description,created_at) VALUES(?,?,?,?,?,?)", (module_id, project_id, parent_id, data["name"], data.get("description", ""), timestamp))
            self.event(conn, project_id, "module_created", "Module", module_id, data)
        return row(self.db.one("SELECT * FROM modules WHERE id=?", (module_id,)))

    def create_feature(self, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if not self.db.one("SELECT id FROM modules WHERE id=? AND project_id=?", (data["module_id"], project_id)):
            raise DomainError("MODULE_NOT_FOUND", "module not found", 404)
        if not str(data.get("name", "")).strip():
            raise DomainError("INVALID_FEATURE", "name is required", 422)
        feature_id, timestamp = uid("feat"), now()
        with self.db.tx() as conn:
            conn.execute("INSERT INTO features(id,project_id,module_id,name,description,created_at) VALUES(?,?,?,?,?,?)", (feature_id, project_id, data["module_id"], data["name"], data.get("description", ""), timestamp))
            self.event(conn, project_id, "feature_created", "Feature", feature_id, data)
        return row(self.db.one("SELECT * FROM features WHERE id=?", (feature_id,)))

    def create_task(self, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if not self.db.one("SELECT id FROM features WHERE id=? AND project_id=?", (data["feature_id"], project_id)):
            raise DomainError("FEATURE_NOT_FOUND", "feature not found", 404)
        if not str(data.get("title", "")).strip():
            raise DomainError("INVALID_TASK", "title is required", 422)
        artifact_refs = data.get("artifact_refs", []) or []
        if not isinstance(artifact_refs, list) or any(not isinstance(ref, str) or not ref.strip() for ref in artifact_refs):
            raise DomainError("INVALID_ARTIFACT_REFS", "artifact_refs must be a list of non-empty artifact ids", 422)
        if artifact_refs:
            placeholders = ",".join("?" for _ in artifact_refs)
            args = (project_id, *artifact_refs)
            found = self.db.one(f"SELECT COUNT(*) AS count FROM artifacts WHERE project_id=? AND id IN ({placeholders})", args)["count"]
            if found != len(set(artifact_refs)):
                raise DomainError("ARTIFACT_REF_NOT_FOUND", "artifact reference does not belong to project", 404)
        supersedes_task_id = data.get("supersedes_task_id")
        task_id, timestamp = uid("task"), now()
        with self.db.tx() as conn:
            old_task = None
            if supersedes_task_id:
                old_task = conn.execute("SELECT id,plan_status FROM task_cards WHERE id=? AND project_id=?", (supersedes_task_id, project_id)).fetchone()
                if not old_task:
                    raise DomainError("SUPERSEDES_TASK_NOT_FOUND", "superseded task not found in project", 404)
                if old_task["plan_status"] == "superseded":
                    raise DomainError("TASK_SUPERSEDED", "task is already superseded", 409)
            conn.execute("INSERT INTO task_cards(id,project_id,feature_id,kind,title,description,acceptance,artifact_refs_json,supersedes_task_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (task_id, project_id, data["feature_id"], data.get("kind", "development"), data["title"], data.get("description", ""), data.get("acceptance", ""), jdump(artifact_refs), supersedes_task_id, timestamp, timestamp))
            if old_task:
                conn.execute("UPDATE task_cards SET plan_status='superseded',updated_at=? WHERE id=?", (timestamp, supersedes_task_id))
                conn.execute("UPDATE execution_runs SET status='rework_required',ended_at=? WHERE task_card_id=? AND status IN ('executing','machine_verifying','awaiting_acceptance')", (timestamp, supersedes_task_id))
                self.event(conn, project_id, "task_superseded", "TaskCard", supersedes_task_id, {"reason": "replaced by new task", "replacement_task_id": task_id}, "human")
            self.event(conn, project_id, "task_created", "TaskCard", task_id, {**data, "artifact_refs": artifact_refs})
        return self.task(task_id)

    def skills(self, project_id: str) -> list[dict[str, Any]]:
        self.project(project_id)
        rows = self.db.all("SELECT * FROM skill_assets WHERE project_id IS NULL OR project_id=? ORDER BY name", (project_id,))
        return [unpack(row(item)) for item in rows]

    def seed_skill(self, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self.project(project_id)
        name, kind, content = str(data.get("name", "")).strip(), str(data.get("kind", "workflow")).strip(), str(data.get("content", "")).strip()
        if not name or not content:
            raise DomainError("INVALID_SKILL", "name and content are required", 422)
        skill_id, timestamp = uid("skill"), now()
        with self.db.tx() as conn:
            conn.execute("INSERT INTO skill_assets(id,project_id,name,kind,content,version,enabled,created_at) VALUES(?,?,?,?,?,?,?,?)", (skill_id, project_id, name, kind, content, str(data.get("version", "1.0")), 1, timestamp))
            self.event(conn, project_id, "skill_installed", "SkillAsset", skill_id, {"name": name, "kind": kind})
        return unpack(row(self.db.one("SELECT * FROM skill_assets WHERE id=?", (skill_id,))))

    def split_task(self, task_id: str) -> dict[str, Any]:
        task = self.task(task_id)
        skills = self.skills(task["project_id"])
        skill_text = "\n".join(item["content"] for item in skills if item.get("enabled"))
        prompt = ("Split the development task into 2-4 independently testable child tasks. Return JSON array only.\n" +
                  "Skills:\n" + skill_text + "\nTask:\n" + json.dumps({"title": task["title"], "description": task["description"], "acceptance": task["acceptance"]}, ensure_ascii=False))
        provider_mode = self.provider.mode
        proposal = []
        if provider_mode == "real":
            try:
                raw = __import__("asyncio").run(self.provider.complete("You split software tasks. JSON array only.", prompt))
                proposal = json.loads(re.search(r"\[.*\]", raw, re.DOTALL).group(0))
            except Exception:
                proposal = []
        if not proposal:
            proposal = [{"title": task["title"] + " - 核心实现", "description": task["description"], "acceptance": task["acceptance"]}, {"title": task["title"] + " - 测试验收", "description": "为核心实现补充自动化测试并验证验收标准。", "acceptance": "测试通过且结果可复现。"}]
        split_id, timestamp = uid("split"), now()
        with self.db.tx() as conn:
            conn.execute("INSERT INTO task_splits(id,project_id,source_task_id,provider_mode,skills_json,proposal_json,created_at) VALUES(?,?,?,?,?,?,?)", (split_id, task["project_id"], task_id, provider_mode, jdump([item["name"] for item in skills if item.get("enabled")]), jdump(proposal), timestamp))
            self.event(conn, task["project_id"], "task_auto_split", "TaskSplit", split_id, {"source_task_id": task_id, "count": len(proposal), "provider_mode": provider_mode})
        return {"id": split_id, "source_task_id": task_id, "provider_mode": provider_mode, "skills": [item["name"] for item in skills if item.get("enabled")], "proposal": proposal}

    def task(self, task_id: str) -> dict[str, Any]:
        data = unpack(row(self.db.one("SELECT * FROM task_cards WHERE id=?", (task_id,))))
        if not data:
            raise DomainError("TASK_NOT_FOUND", "task not found", 404)
        data["prompts"] = [row(item) for item in self.db.all("SELECT id,provider_mode,created_at FROM prompt_instances WHERE task_card_id=? ORDER BY created_at DESC", (task_id,))]
        data["executions"] = [unpack(row(item)) for item in self.db.all("SELECT * FROM execution_runs WHERE task_card_id=? ORDER BY created_at DESC", (task_id,))]
        return data

    def tasks(self, project_id: str) -> list[dict[str, Any]]:
        return [self.task(item["id"]) for item in self.db.all("SELECT id FROM task_cards WHERE project_id=? ORDER BY created_at DESC", (project_id,))]

    def review_task(self, task_id: str, data: dict[str, Any]) -> dict[str, Any]:
        task = self.task(task_id)
        if task["plan_status"] == "superseded":
            raise DomainError("TASK_SUPERSEDED", "superseded task cannot be reviewed", 409)
        supplied_status = data.get("status")
        review = self.provider.review(task["title"], task["description"], task["acceptance"]) if supplied_status is None else {"status": supplied_status, "reason": data.get("reason", ""), "provider_mode": "supplied"}
        status = review["status"]
        if status not in ("passed", "failed"):
            raise DomainError("INVALID_REVIEW_STATUS", "status must be passed or failed", 422)
        reason = str(data.get("reason") or review.get("reason", ""))
        event_data = {**data, "status": status, "reason": reason, "provider_mode": review.get("provider_mode", "supplied")}
        with self.db.tx() as conn:
            conn.execute("UPDATE task_cards SET plan_status='under_review',ai_review_status=?,ai_review_reason=?,updated_at=? WHERE id=?", (status, reason, now(), task_id))
            self.event(conn, task["project_id"], "task_reviewed", "TaskCard", task_id, event_data, data.get("actor", "ai"))
        return self.task(task_id)

    def approve_task(self, task_id: str, data: dict[str, Any]) -> dict[str, Any]:
        task = self.task(task_id)
        if task["plan_status"] == "superseded":
            raise DomainError("TASK_SUPERSEDED", "superseded task cannot be approved", 409)
        if task["ai_review_status"] != "passed" and not data.get("force"):
            raise DomainError("REVIEW_REQUIRED", "AI review did not pass; force approval requires a reason", 422)
        if data.get("force") and not str(data.get("reason", "")).strip():
            raise DomainError("REASON_REQUIRED", "force approval requires reason", 422)
        with self.db.tx() as conn:
            conn.execute("UPDATE task_cards SET plan_status='approved',updated_at=? WHERE id=?", (now(), task_id))
            self.event(conn, task["project_id"], "task_approved", "TaskCard", task_id, data, "human")
        return self.task(task_id)

    def supersede(self, task_id: str, reason: str) -> dict[str, Any]:
        task = self.task(task_id)
        if task["plan_status"] == "superseded":
            raise DomainError("TASK_SUPERSEDED", "task is already superseded", 409)
        reason = str(reason).strip()
        if not reason:
            raise DomainError("REASON_REQUIRED", "superseding a task requires a reason", 422)
        timestamp = now()
        with self.db.tx() as conn:
            conn.execute("UPDATE task_cards SET plan_status='superseded',updated_at=? WHERE id=?", (timestamp, task_id))
            conn.execute("UPDATE execution_runs SET status='rework_required',ended_at=? WHERE task_card_id=? AND status IN ('executing','machine_verifying','awaiting_acceptance')", (timestamp, task_id))
            self.event(conn, task["project_id"], "task_superseded", "TaskCard", task_id, {"reason": reason}, "human")
        return self.task(task_id)

    def create_prompt(self, task_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        task = self.task(task_id)
        if task["plan_status"] != "approved":
            raise DomainError("TASK_NOT_APPROVED", "task must be approved before prompt generation", 422)
        project = self.project(task["project_id"])
        prompt = f"# {task['title']}\\n\\nProject: {project['name']}\\nKind: {task['kind']}\\n\\n## Context\\n{task['description']}\\n\\n## Acceptance\\n{task['acceptance']}\\n\\n## Report\\nReport changed_files, commands, tests, known issues and artifacts through the local Workbench MCP endpoint."
        prompt_id, timestamp = uid("prompt"), now()
        provider_mode = self.provider.mode
        context = {
            "task_id": task_id,
            "project_id": project["id"],
            "artifact_refs": task.get("artifact_refs", []),
            "review_status": task["ai_review_status"],
            "review_reason": task.get("ai_review_reason", ""),
            "provider_mode": provider_mode,
        }
        with self.db.tx() as conn:
            conn.execute("INSERT INTO prompt_instances(id,task_card_id,content,context_json,provider_mode,created_at) VALUES(?,?,?,?,?,?)", (prompt_id, task_id, prompt, jdump(context), provider_mode, timestamp))
            self.event(conn, task["project_id"], "prompt_created", "PromptInstance", prompt_id, {"task_id": task_id, "provider_mode": provider_mode, "artifact_refs": task.get("artifact_refs", [])})
        return unpack(row(self.db.one("SELECT * FROM prompt_instances WHERE id=?", (prompt_id,))))

    def capture_baseline(self, project_id: str, repository_id: str) -> dict[str, Any]:
        repository = row(self.db.one("SELECT * FROM repositories WHERE id=? AND project_id=?", (repository_id, project_id)))
        if not repository:
            raise DomainError("REPOSITORY_NOT_FOUND", "repository not found", 404)
        branch, commit = repository["branch"], repository["commit_sha"]
        try:
            branch = subprocess.run(["git", "branch", "--show-current"], cwd=repository["path"], text=True, capture_output=True, timeout=10, check=False).stdout.strip() or branch
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository["path"], text=True, capture_output=True, timeout=10, check=False).stdout.strip() or commit
        except (OSError, subprocess.SubprocessError):
            pass
        baseline_id, timestamp = uid("base"), now()
        with self.db.tx() as conn:
            scanner = WorkspaceScanner(repository["path"])
            baseline_hashes = scanner.snapshot()
            baseline_files = sorted(baseline_hashes)
            conn.execute("INSERT INTO code_baselines(id,project_id,repository_id,branch,commit_sha,workspace_path,manifest_json,created_at) VALUES(?,?,?,?,?,?,?,?)", (baseline_id, project_id, repository_id, branch, commit, repository["path"], jdump({"files": baseline_files, "hashes": baseline_hashes}), timestamp))
            self.event(conn, project_id, "baseline_captured", "CodeBaseline", baseline_id, {"branch": branch, "commit": commit, "file_count": len(baseline_files)})
        return row(self.db.one("SELECT * FROM code_baselines WHERE id=?", (baseline_id,)))

    def start_execution(self, task_id: str) -> dict[str, Any]:
        task = self.task(task_id)
        if task["plan_status"] != "approved":
            raise DomainError("TASK_NOT_APPROVED", "task must be approved before execution", 422)
        prompt = self.db.one("SELECT * FROM prompt_instances WHERE task_card_id=? ORDER BY created_at DESC LIMIT 1", (task_id,))
        if not prompt:
            raise DomainError("PROMPT_REQUIRED", "create a prompt instance first", 422)
        active = self.db.one("SELECT id FROM execution_runs WHERE task_card_id=? AND status IN ('executing','machine_verifying','awaiting_acceptance')", (task_id,))
        if active:
            raise DomainError("EXECUTION_ACTIVE", "task already has an active execution", 409)
        repository = row(self.db.one("SELECT * FROM repositories WHERE project_id=? LIMIT 1", (task["project_id"],)))
        baseline = self.capture_baseline(task["project_id"], repository["id"])
        execution_id, token, timestamp = uid("exec"), secrets.token_urlsafe(24), now()
        with self.db.tx() as conn:
            conn.execute("INSERT INTO execution_runs(id,project_id,task_card_id,prompt_instance_id,baseline_id,status,mcp_token_hash,started_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (execution_id, task["project_id"], task_id, prompt["id"], baseline["id"], "executing", sha(token), timestamp, timestamp))
            self.event(conn, task["project_id"], "execution_started", "ExecutionRun", execution_id, {"task_id": task_id, "baseline_id": baseline["id"]}, "human")
        return {"execution": self.execution(execution_id), "mcp_token": token}

    def execution(self, execution_id: str) -> dict[str, Any]:
        data = unpack(row(self.db.one("SELECT * FROM execution_runs WHERE id=?", (execution_id,))))
        if not data:
            raise DomainError("EXECUTION_NOT_FOUND", "execution not found", 404)
        data["receipts"] = [unpack(row(item)) for item in self.db.all("SELECT * FROM execution_receipts WHERE execution_id=? ORDER BY submitted_at", (execution_id,))]
        data["tests"] = [unpack(row(item)) for item in self.db.all("SELECT * FROM test_runs WHERE execution_id=? ORDER BY started_at", (execution_id,))]
        data["conclusions"] = [unpack(row(item)) for item in self.db.all("SELECT * FROM conclusions WHERE execution_id=? ORDER BY created_at", (execution_id,))]
        return data

    def report(self, headers: dict[str, str], data: dict[str, Any]) -> dict[str, Any]:
        execution_id = data.get("execution_id")
        execution = row(self.db.one("SELECT * FROM execution_runs WHERE id=?", (execution_id,)))
        if not execution or sha(headers.get("x-mcp-token", "")) != execution["mcp_token_hash"]:
            raise DomainError("MCP_UNAUTHORIZED", "invalid execution or token", 401)
        report_id = data.get("report_id") or uid("report")
        baseline = row(self.db.one("SELECT * FROM code_baselines WHERE id=?", (execution["baseline_id"],)))
        baseline_manifest = jload(baseline["manifest_json"], {})
        verification = WorkspaceScanner(baseline["workspace_path"]).verify(data.get("changed_files", []), baseline_manifest)
        complete = all(field in data for field in ("report_id", "execution_id", "changed_files", "tests", "summary"))
        status = "awaiting_acceptance" if complete and verification["verified"] else "rework_required"
        timestamp = now()
        payload = dict(data)
        payload["workspace_verification"] = verification
        duplicate: dict[str, Any] | None = None
        with self.db.tx() as conn:
            duplicate = row(conn.execute("SELECT * FROM execution_receipts WHERE report_id=?", (report_id,)).fetchone())
            if duplicate:
                stored_payload = jload(duplicate["payload_json"], {})
                stored_request = dict(stored_payload) if isinstance(stored_payload, dict) else {}
                stored_request.pop("workspace_verification", None)
                if duplicate["execution_id"] != execution_id or stored_request != data:
                    raise DomainError("REPORT_ID_CONFLICT", "report_id already belongs to a different report", 409)
            else:
                current = row(conn.execute("SELECT * FROM execution_runs WHERE id=?", (execution_id,)).fetchone())
                task = row(conn.execute("SELECT * FROM task_cards WHERE id=?", (current["task_card_id"],)).fetchone())
                if current["status"] in ("completed", "blocked") or task["plan_status"] == "superseded":
                    raise DomainError("STALE_EXECUTION", "execution is stale; report rejected", 409)
                conn.execute("INSERT INTO execution_receipts(id,report_id,execution_id,report_type,payload_json,changed_files_json,submitted_at) VALUES(?,?,?,?,?,?,?)", (report_id, report_id, execution_id, data.get("report_type", "result"), jdump(payload), jdump(data.get("changed_files", [])), timestamp))
                conn.execute("UPDATE execution_runs SET status=?,machine_checks_json=?,ended_at=? WHERE id=?", (status, jdump({"receipt_complete": complete, "workspace_verification": verification}), timestamp, execution_id))
                self.event(conn, current["project_id"], "execution_reported", "ExecutionReceipt", report_id, {"execution_id": execution_id, "verification": verification}, "mcp")
        if duplicate:
            return {"duplicate": True, "receipt": unpack(duplicate), "execution": self.execution(execution_id)}
        return {"duplicate": False, "receipt": unpack(row(self.db.one("SELECT * FROM execution_receipts WHERE id=?", (report_id,)))), "execution": self.execution(execution_id)}

    def test_run(self, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
        project = self.project(project_id)
        execution_id = data.get("execution_id")
        if execution_id:
            execution = row(self.db.one("SELECT project_id FROM execution_runs WHERE id=?", (execution_id,)))
            if not execution or execution["project_id"] != project_id:
                raise DomainError("EXECUTION_PROJECT_MISMATCH", "execution does not belong to project", 422)
        test_id, timestamp = uid("test"), now()
        output = self.root / "runs" / project_id / f"{test_id}.log"
        try:
            result = run_command(data["command"], project["workspace_path"], output, int(data.get("timeout", 120)))
        except ValueError as exc:
            raise DomainError("CI_REJECTED", str(exc), 422) from exc
        with self.db.tx() as conn:
            conn.execute("INSERT INTO test_runs(id,project_id,execution_id,command,status,exit_code,stdout_path,report_json,started_at,ended_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (test_id, project_id, data.get("execution_id"), data["command"], result["status"], result["exit_code"], result["stdout_path"], jdump(result), timestamp, now()))
            self.event(conn, project_id, "test_run_finished", "TestRun", test_id, result)
        return unpack(row(self.db.one("SELECT * FROM test_runs WHERE id=?", (test_id,))))

    def conclusion(self, execution_id: str, data: dict[str, Any], kind: str) -> dict[str, Any]:
        execution = self.execution(execution_id)
        status = data.get("status", "rework_required")
        if kind == "acceptance" and status not in ("accepted", "risk_accepted", "rejected"):
            raise DomainError("INVALID_ACCEPTANCE", "status must be accepted, risk_accepted or rejected")
        if kind == "workbench" and status not in ("passed", "failed", "rework_required"):
            raise DomainError("INVALID_WORKBENCH_CONCLUSION", "status must be passed, failed or rework_required")
        if kind == "workbench":
            if execution["status"] not in ("awaiting_acceptance", "rework_required"):
                raise DomainError("EXECUTION_NOT_READY", "execution must have a submitted MCP receipt before workbench conclusion", 422)
            receipt = self.db.one("SELECT id FROM execution_receipts WHERE execution_id=? ORDER BY submitted_at DESC LIMIT 1", (execution_id,))
            if not receipt:
                raise DomainError("RECEIPT_REQUIRED", "MCP receipt is required before workbench conclusion", 422)
            test_runs = self.db.all("SELECT status FROM test_runs WHERE execution_id=? ORDER BY started_at", (execution_id,))
            if not test_runs:
                raise DomainError("TEST_RUN_REQUIRED", "at least one TestRun is required before workbench conclusion", 422)
            checks = execution.get("machine_checks", {})
            verification = checks.get("workspace_verification", {}) if isinstance(checks, dict) else {}
            failed_tests = [item for item in test_runs if item["status"] != "passed"]
            if status == "passed" and (not checks.get("receipt_complete") or verification.get("verified") is not True or failed_tests):
                raise DomainError("MACHINE_CHECKS_FAILED", "failed machine checks require a failed conclusion and risk acceptance", 422)
        if kind == "acceptance":
            workbench = self.db.one("SELECT status FROM conclusions WHERE execution_id=? AND kind=? ORDER BY created_at DESC LIMIT 1", (execution_id, "workbench"))
            if not workbench:
                raise DomainError("WORKBENCH_CONCLUSION_REQUIRED", "workbench conclusion is required before human acceptance", 422)
            if execution["status"] not in ("awaiting_acceptance", "rework_required"):
                raise DomainError("EXECUTION_NOT_ACCEPTABLE", "execution is not ready for human acceptance", 422)
            if status == "accepted" and workbench["status"] != "passed":
                raise DomainError("RISK_ACCEPTANCE_REQUIRED", "failed machine checks require risk acceptance or rejection", 422)
            if status == "risk_accepted" and not data.get("risks"):
                raise DomainError("RISKS_REQUIRED", "risk acceptance requires at least one documented risk", 422)
        conclusion_id, timestamp = uid("con"), now()
        with self.db.tx() as conn:
            conn.execute("INSERT INTO conclusions(id,project_id,execution_id,kind,status,summary,risks_json,evidence_json,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (conclusion_id, execution["project_id"], execution_id, kind, status, data.get("summary", data.get("reason", "")), jdump(data.get("risks", [])), jdump(data.get("evidence", [])), data.get("actor", "human"), timestamp))
            if kind == "acceptance":
                next_status = "completed" if status in ("accepted", "risk_accepted") else "rework_required"
                conn.execute("UPDATE execution_runs SET status=?,ended_at=? WHERE id=?", (next_status, timestamp, execution_id))
            else:
                conn.execute("UPDATE execution_runs SET status=? WHERE id=?", ("awaiting_acceptance" if status == "passed" else "rework_required", execution_id))
            self.event(conn, execution["project_id"], f"{kind}_created", "Conclusion", conclusion_id, data, data.get("actor", "human"))
        return row(self.db.one("SELECT * FROM conclusions WHERE id=?", (conclusion_id,)))

    def events(self, project_id: str) -> list[dict[str, Any]]:
        return [unpack(row(item)) for item in self.db.all("SELECT * FROM events WHERE project_id=? ORDER BY id DESC", (project_id,))]

    def decisions(self, project_id: str) -> list[dict[str, Any]]:
        return [unpack(row(item)) for item in self.db.all("SELECT * FROM decisions WHERE project_id=? ORDER BY created_at DESC", (project_id,))]
