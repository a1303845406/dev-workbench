from __future__ import annotations

from typing import Literal
from fastapi import APIRouter, Depends
from pydantic import Field
from devworkbench.infrastructure.persistence.database import database, data_root
from devworkbench.modules.planning.application import Planning
from devworkbench.modules.planning.domain import StrictModel

from devworkbench.shared.http import local_request

router = APIRouter(prefix="/v2/projects/{project_id}/planning", tags=["planning"], dependencies=[Depends(local_request)])

def planner():
    return Planning(database(), data_root())

class Revision(StrictModel):
    expected_revision: int = Field(ge=0, strict=True)

class Generate(Revision):
    action: Literal["discuss", "requirements", "decomposition", "design"]
    message: str = Field(default="", max_length=20000)
    allow_external: bool = False
    allow_search: bool = False
    search_query: str = Field(default="", max_length=300)
    target_node_id: str | None = Field(default=None, max_length=64)

class Draft(Revision):
    kind: Literal["requirement", "decomposition", "design"]
    data: dict

class Review(Revision):
    decision: Literal["approve", "changes_requested"]
    comment: str = Field(default="", max_length=10000)

class Source(Revision):
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["project", "experience", "knowledge"]
    content: str = Field(min_length=1, max_length=20000)

@router.get("")
def state(project_id: str):
    return planner().get(project_id)

@router.post("/generate", status_code=202)
async def generate(project_id: str, body: Generate):
    service = planner()
    run_id = service.begin(project_id, body.model_dump())
    service.launch(project_id, run_id)
    return service.get(project_id)

@router.post("/runs/{run_id}/cancel")
async def cancel(project_id: str, run_id: str, body: Revision):
    return planner().cancel(project_id, body.expected_revision, run_id)

@router.post("/versions", status_code=201)
def save(project_id: str, body: Draft):
    return planner().save(project_id, body.expected_revision, body.kind, body.data)

@router.post("/versions/{version_id}/adopt")
def adopt(project_id: str, version_id: str, body: Revision):
    return planner().act(project_id, body.expected_revision, version_id, "adopt")

@router.post("/versions/{version_id}/reject")
def reject(project_id: str, version_id: str, body: Revision):
    return planner().act(project_id, body.expected_revision, version_id, "reject")

@router.post("/versions/{version_id}/review")
def review(project_id: str, version_id: str, body: Review):
    return planner().act(project_id, body.expected_revision, version_id, body.decision, body.comment)

@router.post("/sources", status_code=201)
def source(project_id: str, body: Source):
    return planner().source(project_id, body.expected_revision, body.title, body.kind, body.content)
