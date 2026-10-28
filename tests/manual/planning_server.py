"""Isolated browser fixture. NEVER use this mock model in the real workbench.
Run: python -m tests.manual.planning_server (port 18642; temporary data).
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

from tests.support.planning import requirement, decomposition, design, FakeProvider
from devworkbench.infrastructure.persistence.database import database, data_root
from devworkbench.modules.workbench.application import Workbench
from devworkbench.modules.planning.application import Planning

class BrowserFixtureProvider(FakeProvider):
    async def complete(self, system, user):
        await asyncio.sleep(0.2)
        context = json.loads(user)
        kind = context["action"]
        proposal = {"requirements": requirement, "decomposition": decomposition, "design": design}.get(kind)
        reply = "【浏览器回归用模拟回复，不是真实模型】先确认两个场景：谁创建工单？处理失败时应如何通知？"
        if proposal:
            reply = "【浏览器回归用模拟回复】已整理候选稿，请先比较、采纳，再进行人工审查。"
        return json.dumps(dict(reply=reply, citations=["knowledge-srs-v1"], proposal=proposal() if proposal else None), ensure_ascii=False)

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="devwb-planning-browser-") as root:
        os.environ["DEVWB_HOME"] = root
        from devworkbench.main import app
        import devworkbench.modules.planning.router as api
        api.planner = lambda: Planning(database(), data_root(), BrowserFixtureProvider())
        Workbench(database(), data_root()).create_project({"name": "工单共创 · 交互回归模拟", "requirement": "希望为小团队设计一个简单的工单系统。"})
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=18642)
