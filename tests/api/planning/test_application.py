"""Co-creation gates, immutable proposals, truthful evidence and recovery."""
import asyncio
import copy
import json

import pytest
from fastapi.testclient import TestClient
from devworkbench.infrastructure.persistence.database import database, data_root
from devworkbench.shared.errors import DomainError
from devworkbench.modules.planning.application import Planning, current
from devworkbench.modules.planning.domain import SECTIONS, normalize


from tests.support.planning import requirement, node, decomposition, design, FakeProvider

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVWB_HOME", str(tmp_path / "data"))
    from devworkbench.main import app
    with TestClient(app) as client:
        yield client


@pytest.fixture
def setup(client):
    pid = client.post("/api/v2/projects", json={"name": "planning-test"}).json()["id"]
    provider = FakeProvider()
    service = Planning(database(), data_root(), provider)
    service.get(pid)
    return client, service, provider, pid


def save(service, pid, kind, data):
    return service.save(pid, service.get(pid)["revision"], kind, data)


def approve(service, pid, kind):
    s = service.get(pid)
    return service.act(pid, s["revision"], s["current"][kind], "approve", "人工检查完成")


def approved(service, pid, through="design"):
    for kind, data in [("requirement", requirement()), ("decomposition", decomposition()), ("design", design())]:
        save(service, pid, kind, data)
        approve(service, pid, kind)
        if kind == through:
            break


def begin(service, pid, action="discuss", **kwargs):
    return service.begin(pid, dict(expected_revision=service.get(pid)["revision"], action=action, message="请一起推敲", allow_external=True, allow_search=False, search_query="", target_node_id=None, **kwargs))


def run(service, pid, action="discuss"):
    rid = begin(service, pid, action)
    asyncio.run(service.generate(pid, rid))
    return service.get(pid)


def test_failed_output_is_redacted_and_does_not_create_version(setup):
    _, service, provider, pid = setup
    before = service.get(pid)
    provider.response = "not-json " + provider.api_key
    state = run(service, pid, "requirements")
    failed = state["runs"][-1]
    assert failed["status"] == "failed"
    assert failed["response_text"] == "not-json [REDACTED]"
    assert provider.api_key not in json.dumps(state)
    assert state["versions"] == before["versions"]
    assert state["current"] == before["current"]


def test_failed_graph_reports_safe_actionable_reason(setup):
    _, service, provider, pid = setup
    approved(service, pid, "requirement")
    invalid = decomposition()
    invalid["nodes"][0]["dependencies"] = ["M-1"]
    provider.response = json.dumps({"reply": "candidate", "proposal": invalid})
    state = run(service, pid, "decomposition")
    assert "依赖不存在或引用自身" in state["runs"][-1]["error"]
    assert state["current"]["decomposition"] is None


def test_failed_field_name_cannot_leak_model_text_into_error(setup):
    _, service, provider, pid = setup
    invalid = requirement()
    invalid["private-customer-field"] = "private content"
    provider.response = json.dumps({"reply": "candidate", "proposal": invalid})
    state = run(service, pid, "requirements")
    assert "未知字段" in state["runs"][-1]["error"]
    assert "private" not in state["runs"][-1]["error"]


def test_initial_state_imports_legacy_as_unapproved(client):
    pid = client.post("/api/v2/projects", json={"name": "legacy", "requirement": "需要工单"}).json()["id"]
    url = f"/api/v2/projects/{pid}/planning"
    state = client.get(url).json()
    assert state["stage"] == "requirements"
    assert state["execution_locked"] is True
    assert state["messages"][0]["content"] == "需要工单"
    assert current(state, "requirement")["status"] == "draft"
    assert current(state, "requirement")["checks"]
    assert client.get(url).json()["revision"] == state["revision"]
    assert client.get("/api/v2/projects/missing/planning").status_code == 404


@pytest.mark.parametrize("headers", [{"origin": "https://untrusted.example"}, {"sec-fetch-site": "cross-site"}])
def test_planning_rejects_cross_site_requests(client, headers):
    pid = client.post("/api/v2/projects", json={"name": "origin-test"}).json()["id"]
    url = f"/api/v2/projects/{pid}/planning"
    assert client.get(url, headers=headers).status_code == 403
    response = client.post(url + "/generate", headers=headers, json={"expected_revision": 0, "action": "discuss", "message": "拒绝跨站触发", "allow_external": True})
    assert response.status_code == 403
    assert client.get(url).json()["messages"] == []


def test_three_human_gates_and_no_execution_unlock(setup):
    client, service, _, pid = setup
    with pytest.raises(DomainError, match="请先人工批准"):
        save(service, pid, "decomposition", decomposition())
    approved(service, pid)
    s = service.get(pid)
    assert s["stage"] == "design_approved" and s["execution_locked"]
    assert len(s["versions"]) == 3
    for item in s["versions"]:
        assert item["reviews"][0]["version_id"] == item["id"]
        assert item["reviews"][0]["decision"] == "approve"
    r = client.post(f"/api/v2/projects/{pid}/tasks", json={"feature_id": "whatever", "title": "not allowed"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "PLANNING_EXECUTION_LOCKED"
    assert client.get(f"/api/v2/projects/{pid}").json()["tasks"] == []


def test_requirement_change_invalidates_all_downstream(setup):
    _, service, _, pid = setup
    approved(service, pid)
    before = service.get(pid)
    previous = copy.deepcopy(before["versions"])
    after = save(service, pid, "requirement", requirement())
    assert after["stage"] == "requirements"
    assert current(after, "decomposition")["status"] == "stale"
    assert current(after, "design")["status"] == "stale"
    for old in previous:
        assert next(v for v in after["versions"] if v["id"] == old["id"])["data"] == old["data"]
    with pytest.raises(DomainError):
        service.act(pid, after["revision"], before["current"]["requirement"], "approve")


def test_revision_conflict_is_non_destructive(setup):
    _, service, _, pid = setup
    s = service.get(pid)
    save(service, pid, "requirement", requirement())
    with pytest.raises(DomainError) as e:
        service.save(pid, s["revision"], "requirement", requirement())
    assert e.value.code == "REVISION_CONFLICT"
    assert len(service.get(pid)["versions"]) == 1


def test_unresolved_and_incomplete_documents_cannot_be_approved(setup):
    _, service, _, pid = setup
    s = save(service, pid, "requirement", {"document": "随意的摘要"})
    assert current(s, "requirement")["checks"]
    with pytest.raises(DomainError): approve(service, pid, "requirement")
    s = save(service, pid, "requirement", requirement(["权限待确认"]))
    assert "待确认：权限待确认" in current(s, "requirement")["checks"]
    with pytest.raises(DomainError): approve(service, pid, "requirement")
    save(service, pid, "requirement", requirement())
    approve(service, pid, "requirement")
    data = decomposition()
    for item in data["nodes"]: item["requirement_ids"] = ["FR-999"]
    s = save(service, pid, "decomposition", data)
    assert any("缺少特性覆盖" in c for c in current(s, "decomposition")["checks"])
    assert any("不存在的需求" in c for c in current(s, "decomposition")["checks"])
    with pytest.raises(DomainError): approve(service, pid, "decomposition")


def test_design_package_requires_cross_artifact_feature_trace(setup):
    _, service, _, pid = setup
    approved(service, pid, "decomposition")
    data = design(); data["data_design"] = "F-10 不是 F-1"
    # remove all exact feature references, including explanatory mention
    data["data_design"] = "F-10 不是前缀匹配"
    s = save(service, pid, "design", data)
    assert "数据设计缺少特性追踪：F-1" in current(s, "design")["checks"]
    with pytest.raises(DomainError): approve(service, pid, "design")


def test_return_for_changes_invalidates_and_records_opinion(setup):
    _, service, _, pid = setup
    approved(service, pid)
    s = service.get(pid)
    with pytest.raises(DomainError): service.act(pid, s["revision"], s["current"]["decomposition"], "changes_requested")
    s = service.act(pid, s["revision"], s["current"]["decomposition"], "changes_requested", "重新划分职责")
    assert current(s, "decomposition")["status"] == "changes_requested"
    assert current(s, "design")["status"] == "stale"
    assert current(s, "decomposition")["reviews"][-1]["comment"] == "重新划分职责"


@pytest.mark.parametrize("malformation", ["cycle", "dependency", "duplicate", "orphan", "feature_parent"])
def test_invalid_trees_are_rejected(malformation):
    d = decomposition()
    if malformation == "cycle": d["nodes"][0]["parent_id"] = "M-2"
    elif malformation == "dependency":
        d["nodes"][0]["dependencies"] = ["M-2"]; d["nodes"][1]["dependencies"] = ["M-1"]
    elif malformation == "duplicate": d["nodes"][1]["id"] = "M-1"
    elif malformation == "orphan": d["nodes"][1]["parent_id"] = "missing"
    else: d["nodes"][1]["parent_id"] = "F-1"
    with pytest.raises(ValueError): normalize("decomposition", d)


def test_local_resplit_preserves_outside_branch_and_ownership():
    old = normalize("decomposition", decomposition())
    new = copy.deepcopy(old); new["nodes"][1]["title"] = "重新命名"
    Planning.check_local(old, new, "M-2")
    for change in ["outside", "move", "new-root", "overview"]:
        new = copy.deepcopy(old)
        if change == "outside": new["nodes"][3]["title"] = "外部篡改"
        elif change == "move": new["nodes"][2]["parent_id"] = "M-other"
        elif change == "new-root": new["nodes"].append(node("M-extra"))
        else: new["overview"] = "全局变化"
        with pytest.raises(ValueError): Planning.check_local(old, new, "M-2")


def test_discussion_retains_context_and_real_citations(setup):
    _, service, provider, pid = setup
    s = service.get(pid)
    service.source(pid, s["revision"], "以往经验", "experience", "本地经验证据")
    s = run(service, pid)
    assert s["runs"][-1]["status"] == "completed"
    assert len(s["messages"]) == 2 and not s["versions"]
    assert s["runs"][-1]["citations"] == ["knowledge-srs-v1"]
    assert any(x["content"] == "本地经验证据" for x in provider.seen[1]["sources"])
    assert not any(x["tool"] == "web_search" for x in s["runs"][-1]["tools"])


def test_review_feedback_is_provided_to_next_generation(setup):
    _, service, provider, pid = setup
    approved(service, pid, "requirement")
    s = service.get(pid)
    service.act(pid, s["revision"], s["current"]["requirement"], "changes_requested", "补充失败后输入保留规则")
    run(service, pid)
    assert provider.seen[1]["review_feedback"]["requirement"][-1]["comment"] == "补充失败后输入保留规则"


def test_ai_proposal_requires_adoption_and_separate_approval(setup):
    _, service, provider, pid = setup
    provider.response = json.dumps(dict(reply="形成需求候选", citations=[], proposal=requirement()))
    s = run(service, pid, "requirements")
    item = s["versions"][-1]
    assert item["status"] == "proposed" and s["current"]["requirement"] is None
    with pytest.raises(DomainError): service.act(pid, s["revision"], item["id"], "approve")
    s = service.act(pid, s["revision"], item["id"], "adopt")
    assert current(s, "requirement")["status"] == "draft"
    assert approve(service, pid, "requirement")["stage"] == "decomposition"


@pytest.mark.parametrize("response", ["not json", '{"reply":"ok","citations":["fake"]}', '{"reply":"ok","proposal":null}', '{"reply":"ok","proposal":{"document":"ok","extra":"no"}}'])
def test_invalid_model_responses_never_replace_documents(setup, response):
    _, service, provider, pid = setup
    approved(service, pid, "requirement")
    before = service.get(pid)["current"].copy()
    provider.response = response
    s = run(service, pid, "requirements")
    assert s["runs"][-1]["status"] == "failed"
    assert s["current"] == before
    assert len(s["versions"]) == 1
    assert "secret-fixture" not in json.dumps(s)


@pytest.mark.parametrize("response, expected", [("not json", "JSON"), ('{"reply":"ok","proposal":{"document":"x","unexpected-secret":"private"}}', "结构契约"), ('{"reply":"ok","citations":["secret-fixture-not-real"]}', "来源 ID"), ('{"reply":"ok","proposal":null}', "proposal")])
def test_generation_diagnostics_are_actionable_and_redacted(setup, response, expected):
    _, service, provider, pid = setup
    provider.response = response
    s = run(service, pid, "requirements")
    assert expected in s["runs"][-1]["error"]
    assert "private" not in s["runs"][-1]["error"]
    assert "secret-fixture" not in json.dumps(s)


def test_late_proposal_marked_stale_not_overwritten(setup):
    _, service, provider, pid = setup
    provider.response = json.dumps(dict(reply="候选", citations=[], proposal=requirement()))
    rid = begin(service, pid, "requirements")
    latest = save(service, pid, "requirement", requirement())["current"]["requirement"]
    asyncio.run(service.generate(pid, rid))
    s = service.get(pid)
    assert s["versions"][-1]["status"] == "stale"
    assert s["current"]["requirement"] == latest
    with pytest.raises(DomainError): service.act(pid, s["revision"], s["versions"][-1]["id"], "adopt")


def test_cancel_and_recover_do_not_retry_or_accept(setup):
    _, service, provider, pid = setup
    rid = begin(service, pid)
    with pytest.raises(DomainError) as e: begin(service, pid)
    assert e.value.code == "RUN_ACTIVE"
    service.cancel(pid, service.get(pid)["revision"], rid)
    asyncio.run(service.generate(pid, rid))
    assert provider.seen is None
    assert service.get(pid)["runs"][-1]["status"] == "cancelled"
    begin(service, pid)
    service.recover()
    assert service.get(pid)["runs"][-1]["status"] == "interrupted"
    assert len(service.get(pid)["messages"]) == 2


def test_explicit_consent_and_deterministic_honesty(setup):
    client, service, provider, pid = setup
    url = f"/api/v2/projects/{pid}/planning"
    import devworkbench.modules.planning.router as api
    original = api.planner
    api.planner = lambda: service
    try:
        body = dict(expected_revision=0, action="discuss", message="想法")
        assert client.post(url + "/generate", json=body).json()["error"]["code"] == "CONSENT_REQUIRED"
        body.update(allow_external=True, allow_search=True)
        assert client.post(url + "/generate", json=body).json()["error"]["code"] == "QUERY_REQUIRED"
        provider.mode = "deterministic"
        assert client.post(url + "/generate", json=body).json()["error"]["code"] == "MODEL_REQUIRED"
        assert client.post(url + "/sources", json=dict(expected_revision=-1, title="a", kind="experience", content="a")).status_code == 422
    finally:
        api.planner = original


def test_search_failure_is_truthful_and_evidence_is_project_scoped(setup, monkeypatch):
    _, service, provider, pid = setup
    async def fail(query): raise RuntimeError("offline-secret")
    monkeypatch.setattr("devworkbench.modules.planning.application.search_web", fail)
    body = dict(expected_revision=0, action="discuss", message="场景", allow_external=True, allow_search=True, search_query="公开知识", target_node_id=None)
    rid = service.begin(pid, body)
    asyncio.run(service.generate(pid, rid))
    s = service.get(pid)
    assert s["runs"][-1]["status"] == "completed"
    assert next(t for t in s["runs"][-1]["tools"] if t["tool"] == "web_search")["status"] == "failed"
    assert all(x["kind"] != "web" for x in s["runs"][-1]["evidence"])
    assert "offline-secret" not in json.dumps(s)


def test_search_evidence_and_secret_redaction(setup, monkeypatch):
    _, service, provider, pid = setup
    async def found(query): return [dict(id="web-1", title="公开文档", kind="web", version="snippet", content="真实摘要", url="https://example.com")]
    monkeypatch.setattr("devworkbench.modules.planning.application.search_web", found)
    provider.response = json.dumps(dict(reply=provider.api_key + " [web-1]", citations=["web-1"], proposal=None))
    body = dict(expected_revision=0, action="discuss", message="场景", allow_external=True, allow_search=True, search_query="公开知识", target_node_id=None)
    rid = service.begin(pid, body)
    asyncio.run(service.generate(pid, rid))
    s = service.get(pid)
    assert "[REDACTED]" in s["messages"][-1]["content"]
    assert s["runs"][-1]["evidence"][-1]["url"] == "https://example.com"
    assert provider.api_key not in json.dumps(s)


def test_project_isolation_and_legacy_bypass_guards(client):
    from tests.support.workbench import create_flow
    pid, tid = create_flow(client)
    other = client.post("/api/v2/projects", json={"name": "other-scope"}).json()["id"]
    service = Planning(database(), data_root(), FakeProvider())
    s = save(service, pid, "requirement", requirement())
    foreign = service.get(other)
    with pytest.raises(DomainError) as e: service.act(other, foreign["revision"], s["current"]["requirement"], "approve")
    assert e.value.code == "VERSION_NOT_FOUND"
    from devworkbench.modules.workbench.application import Workbench
    wb = Workbench(database(), data_root())
    for action in [lambda: wb.create_prompt(tid), lambda: wb.start_execution(tid), lambda: wb.save_requirement(pid, "绕过")]:
        with pytest.raises(DomainError) as e: action()
        assert e.value.code == "PLANNING_EXECUTION_LOCKED"
    assert any(t["id"] == tid for t in client.get(f"/api/v2/projects/{pid}").json()["tasks"])
