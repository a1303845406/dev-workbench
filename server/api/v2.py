from __future__ import annotations

from fastapi import APIRouter, Body, Header
from pydantic import BaseModel, Field, field_validator

from ..core.db import database, data_root
from ..core.errors import DomainError
from ..core.service import Workbench

router = APIRouter(prefix="/v2", tags=["v2-control-room"])


def wb() -> Workbench:
    return Workbench(database(), data_root())

class ProjectIn(BaseModel):
    name: str
    requirement: str = ""
    workspace_path: str | None = None
    repository_name: str = "主仓库"
    branch: str = ""
    commit_sha: str = ""

    @field_validator("repository_name")
    @classmethod
    def strip_repository_name(cls, value: str) -> str:
        return value.strip() or "主仓库"
class ArtifactIn(BaseModel):
    kind: str
    title: str
    content: str = ""
class RepositoryIn(BaseModel):
    name: str = "主仓库"
    path: str
    branch: str = ""
    commit_sha: str = ""

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip() or "主仓库"

    @field_validator("path")
    @classmethod
    def strip_path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("path must not be blank")
        return value
class VersionIn(BaseModel):
    content: str
    status: str = "draft"
class ModuleIn(BaseModel):
    name: str
    description: str = ""
    parent_id: str | None = None
class FeatureIn(BaseModel):
    module_id: str
    name: str
    description: str = ""
class TaskIn(BaseModel):
    feature_id: str
    title: str
    kind: str = "development"
    description: str = ""
    acceptance: str = ""
    artifact_refs: list[str] = Field(default_factory=list)
    supersedes_task_id: str | None = None
class SkillIn(BaseModel):
    name: str
    kind: str = "workflow"
    content: str
    version: str = "1.0"

class ReviewIn(BaseModel):
    status: str | None = None
    reason: str = ""
    actor: str = "ai"
class ApproveIn(BaseModel):
    force: bool = False
    reason: str = ""
class SupersedeIn(BaseModel):
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

class ReceiptIn(BaseModel):
    @field_validator("report_id", "execution_id", "summary", "report_type")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    report_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    report_type: str = Field(default="result", min_length=1)
    agent: str = ""
    changed_files: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    known_issues: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
class TestIn(BaseModel):
    command: str = Field(min_length=1)
    execution_id: str | None = None
    timeout: int = 120
class ConclusionIn(BaseModel):
    status: str
    summary: str = ""
    reason: str = ""
    risks: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    actor: str = "human"

@router.get("/health")
def health():
    database(); return {"status": "ok", "service": "devworkbench-v2", "provider_mode": wb().provider.mode}
@router.get("/projects")
def projects(): return {"projects": wb().list_projects()}
@router.post("/projects", status_code=201)
def create_project(body: ProjectIn): return wb().create_project(body.model_dump())
@router.get("/projects/{project_id}")
def project(project_id: str): return wb().get_project(project_id)
@router.post("/projects/{project_id}/artifacts", status_code=201)
def artifact(project_id: str, body: ArtifactIn): return wb().create_artifact(project_id, body.kind, body.title, body.content)
@router.post("/projects/{project_id}/repositories", status_code=201)
def repository(project_id: str, body: RepositoryIn): return wb().register_repository(project_id, body.model_dump())
@router.post("/artifacts/{artifact_id}/versions", status_code=201)
def version(artifact_id: str, body: VersionIn): return wb().new_version(artifact_id, body.model_dump())
@router.post("/projects/{project_id}/modules", status_code=201)
def module(project_id: str, body: ModuleIn): return wb().create_module(project_id, body.model_dump())
@router.post("/projects/{project_id}/features", status_code=201)
def feature(project_id: str, body: FeatureIn): return wb().create_feature(project_id, body.model_dump())
@router.post("/projects/{project_id}/tasks", status_code=201)
def task(project_id: str, body: TaskIn): return wb().create_task(project_id, body.model_dump())
@router.get("/projects/{project_id}/skills")
def skills(project_id: str): return {"skills": wb().skills(project_id)}
@router.post("/projects/{project_id}/skills", status_code=201)
def install_skill(project_id: str, body: SkillIn): return wb().seed_skill(project_id, body.model_dump())
@router.post("/tasks/{task_id}/split")
def split_task(task_id: str): return wb().split_task(task_id)
@router.post("/tasks/{task_id}/review")
def review(task_id: str, body: ReviewIn = Body(default_factory=ReviewIn)): return wb().review_task(task_id, body.model_dump())
@router.post("/tasks/{task_id}/approve")
def approve(task_id: str, body: ApproveIn = Body(default_factory=ApproveIn)): return wb().approve_task(task_id, body.model_dump())
@router.post("/tasks/{task_id}/supersede")
def supersede(task_id: str, body: SupersedeIn): return wb().supersede(task_id, body.reason)
@router.post("/tasks/{task_id}/prompt", status_code=201)
def prompt(task_id: str): return wb().create_prompt(task_id)
@router.post("/tasks/{task_id}/execute", status_code=201)
def execute(task_id: str): return wb().start_execution(task_id)
@router.get("/executions/{execution_id}")
def execution(execution_id: str): return wb().execution(execution_id)
@router.post("/mcp/receipts")
def receipt(body: ReceiptIn, x_mcp_token: str = Header(default="")): return wb().report({"x-mcp-token": x_mcp_token}, body.model_dump())
@router.post("/projects/{project_id}/tests", status_code=201)
def test(project_id: str, body: TestIn): return wb().test_run(project_id, body.model_dump())
@router.post("/executions/{execution_id}/conclusion")
def conclusion(execution_id: str, body: ConclusionIn): return wb().conclusion(execution_id, body.model_dump(), "workbench")
@router.post("/executions/{execution_id}/acceptance")
def acceptance(execution_id: str, body: ConclusionIn): return wb().conclusion(execution_id, body.model_dump(), "acceptance")
@router.get("/projects/{project_id}/events")
def events(project_id: str): return {"events": wb().events(project_id)}
@router.get("/projects/{project_id}/decisions")
def decisions(project_id: str): return {"decisions": wb().decisions(project_id)}
