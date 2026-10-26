"""API router assembly."""
from fastapi import APIRouter

from . import projects, pipelines, engineering, system

api_router = APIRouter(prefix="/api")
api_router.include_router(system.router)
api_router.include_router(projects.router)
api_router.include_router(pipelines.router)
api_router.include_router(engineering.router)
