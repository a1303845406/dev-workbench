"""Backend smoke tests: exercise the main FR flows end-to-end with the mock provider."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "server"))

os.environ.setdefault("DEVWB_HOME", str(REPO / ".test-workspace"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVWB_HOME", str(tmp_path / "workspace"))
    monkeypatch.setenv("DEVWB_BACKUP_DIR", str(tmp_path / "backups"))
    from fastapi.testclient import TestClient
    from server.app import app
    with TestClient(app) as c:
        yield c


CONF = {"provider": "mock-demo", "context_files": [], "estimated_chars": 100,
        "confirmed_at": "2026-01-01T00:00:00+08:00"}


def _mk_project(client, name="演示项目"):
    resp = client.post("/api/projects", json={"name": name, "template": "default-template"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_project_lifecycle(client):
    data = _mk_project(client, "生命周期")
    assert data["scaffold_report"]["files"]
    listed = client.get("/api/projects").json()["projects"]
    assert any(p["name"] == "生命周期" for p in listed)
    # duplicate name → 409
    assert client.post("/api/projects", json={"name": "生命周期"}).status_code == 409
    # delete without confirm → 400
    assert client.request("DELETE", "/api/projects/生命周期", json={}).status_code == 400
    assert client.request("DELETE", "/api/projects/生命周期",
                          json={"confirm_name": "生命周期"}).status_code == 204


def test_fr01_pipeline_flow(client):
    _mk_project(client, "需求流")
    plid = client.post("/api/projects/需求流/pipelines", json={}).json()["id"]
    # missing outbound confirmation → 428
    r = client.post(f"/api/pipelines/{plid}/chat",
                    json={"stage": "S1", "user_input": "做一个排产工具"})
    assert r.status_code == 428
    # S1..S7 rounds + confirms
    for stage in ["S1", "S2", "S3", "S4", "S5", "S6", "S7"]:
        r = client.post(f"/api/pipelines/{plid}/chat",
                        json={"stage": stage, "user_input": "继续", "outbound_confirmation": CONF})
        assert r.status_code == 200, r.text
        body = {"stage": stage, "comment": "同意"}
        if stage == "S5":
            body["part"] = "business"
            r1 = client.post(f"/api/pipelines/{plid}/confirm", json=body)
            assert r1.status_code == 200
            body["part"] = "scope"
        r = client.post(f"/api/pipelines/{plid}/confirm", json=body)
        assert r.status_code == 200, r.text
    detail = client.get(f"/api/pipelines/{plid}").json()
    assert detail["status"] == "done"
    assert "## 一、项目语境" in detail["draft"]
    assert "## 四、轮次记录" in detail["draft"]


def test_fr04_engineering_flow(client):
    _mk_project(client, "工程流")
    # compose
    r = client.post("/api/projects/工程流/compose", json={
        "complexity": "中等", "context_file_ids": ["rules.md"], "outbound_confirmation": CONF})
    assert r.status_code == 200, r.text
    proposal = r.json()
    assert "requirement" in proposal["modules_selected"]
    # hard constraint violation → 422
    r2 = client.post("/api/projects/工程流/compose", json={
        "complexity": "简单", "context_file_ids": [],
        "user_modules_override": ["requirement"], "outbound_confirmation": CONF})
    assert r2.status_code == 422
    # split
    r3 = client.post("/api/projects/工程流/split", json={
        "proposal": {"tasks_proposal": proposal["tasks_proposal"]},
        "complexity": proposal["complexity"], "modules_selected": proposal["modules_selected"],
        "context_limit_chars": 64000, "coefficient": 0.6})
    assert r3.status_code == 200
    tasks = r3.json()["tasks"]
    assert tasks
    # plan build + confirm + wait for mock execution
    client.post("/api/projects/工程流/plan/generate")
    plan = client.get("/api/projects/工程流/plan").json()
    assert plan["rings"]
    r4 = client.post("/api/projects/工程流/plan/confirm",
                     json={"outbound_confirmation": CONF})
    assert r4.status_code == 200, r4.text
    import asyncio
    for _ in range(100):
        p = client.get("/api/projects/工程流/plan").json()
        if p["status"] in ("done", "suspended"):
            break
        asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.2))
    assert p["status"] == "done", p.get("resume_hint")
    prompts = client.get("/api/projects/工程流/prompts").json()["prompts"]
    assert prompts, "环产出应生成提示词草稿"
    # finalize first prompt
    pid = prompts[0]["prompt_id"]
    r5 = client.post(f"/api/prompts/{pid}/finalize", params={"pid": "工程流"})
    assert r5.status_code == 200


def test_fr05_testcases(client):
    _mk_project(client, "用例流")
    client.post("/api/projects/工程流0/split", json={})  # noqa - ignore, other project
    # seed tasks via split on this project
    proposal = {"tasks_proposal": [{"title": "登录", "module": "development-backend",
                                    "description": "实现登录接口", "acceptance": "登录成功",
                                    "req_refs": ["F1"], "est_chars": 5000}]}
    client.post("/api/projects/用例流/split", json={
        "proposal": proposal, "complexity": "简单",
        "modules_selected": ["requirement", "development-backend", "test", "acceptance"]})
    r = client.post("/api/projects/用例流/testcases", json={
        "task_ids": ["T1"], "performance": False, "outbound_confirmation": CONF})
    assert r.status_code == 200
    body = r.json()["results"][0]["body"]
    assert "TC-P-" not in body
    assert "TC-F-" in body


def test_sanitize_and_export(client):
    _mk_project(client, "导出流")
    # seed rules with an api-key to trigger block
    client.patch("/api/projects/导出流/rules", json={"content": "key: sk-abcdefghijklmnopqrstuvwxyz123"})
    proposal = {"tasks_proposal": [{"title": "T", "module": "development-backend",
                                    "description": "d", "acceptance": "a",
                                    "req_refs": [], "est_chars": 5000}]}
    client.post("/api/projects/导出流/split", json={
        "proposal": proposal, "complexity": "简单",
        "modules_selected": ["requirement", "development-backend", "test", "acceptance"]})
    client.post("/api/projects/导出流/plan/generate")
    r = client.post("/api/projects/导出流/exports", json={"format": "taskmaster", "scope": {"tasks": True}})
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert "pending_confirm" in r.json() or "export_id" in r.json()


def test_scan_whitelist(client, tmp_path):
    _mk_project(client, "扫描流")
    # out-of-whitelist → 403
    r = client.post("/api/projects/扫描流/scan", json={"target": str(tmp_path / "elsewhere")})
    assert r.status_code in (400, 403)
    # register scan root then scan a temp dir
    target = tmp_path / "legacy"
    (target / "src").mkdir(parents=True)
    (target / "readme.md").write_text("# legacy", encoding="utf-8")
    (target / "test_app.py").write_text("def test_x(): pass", encoding="utf-8")
    client.patch("/api/projects/扫描流", json={"scan_roots": [str(tmp_path)]})
    r2 = client.post("/api/projects/扫描流/scan", json={"target": str(target)})
    assert r2.status_code == 200, r2.text
    assert r2.json()["files_done"] >= 2
    analysis = client.get("/api/projects/扫描流/analysis").json()
    assert "结构画像.md" in analysis


def test_rules_conflict_warning(client):
    _mk_project(client, "规则流")
    r = client.patch("/api/projects/规则流/rules",
                     json={"content": "## 补充约束\n允许硬编码演示配置"})
    assert r.status_code == 200
    assert r.json()["conflict_warning"]["hits"][0]["keyword"] == "允许硬编码"


def test_tools_and_settings(client):
    _mk_project(client, "工具流")
    tools = client.get("/api/projects/工具流/tools").json()
    assert tools["tools"]
    tools["tools"][0]["enabled"] = False
    assert client.put("/api/projects/工具流/tools", json=tools).status_code == 200
    settings = client.get("/api/settings").json()
    assert settings["providers"][0]["key"] == "***"
    assert client.get("/api/version").json()["version"]
