from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from server.core.workspace import WorkspaceScanner

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DEVWB_HOME', str(tmp_path / 'data'))
    from server.app import app
    with TestClient(app) as value:
        yield value

def create_flow(client):
    project = client.post('/api/v2/projects', json={'name': 'phase1', 'requirement': 'control room'})
    assert project.status_code == 201, project.text
    pdata = project.json()
    pid = pdata['id']
    module = client.post(f'/api/v2/projects/{pid}/modules', json={'name': 'Control'})
    assert module.status_code == 201
    other_project = client.post('/api/v2/projects', json={'name': 'other-flow'}).json()
    other_module = client.post(f"/api/v2/projects/{other_project['id']}/modules", json={'name': 'Other root'}).json()
    foreign_parent = client.post(f"/api/v2/projects/{pid}/modules", json={'name': 'Foreign child', 'parent_id': other_module['id']})
    assert foreign_parent.status_code == 404
    feature = client.post(f'/api/v2/projects/{pid}/features', json={'module_id': module.json()['id'], 'name': 'History'})
    assert feature.status_code == 201
    task = client.post(f'/api/v2/projects/{pid}/tasks', json={'feature_id': feature.json()['id'], 'title': 'Show history', 'description': 'Display a paginated execution history for the project task, including status, timestamps, and rework attempts. The history must be project-scoped and remain visible after a retry.', 'acceptance': 'Given an approved task, the control room shows the execution status and timestamps; after a failed run and retry, both runs remain visible and the current run is distinguishable.'})
    assert task.status_code == 201
    return pid, task.json()['id']

def test_project_starts_as_draft_without_demo_requirement_and_can_confirm_requirement(client):
    project = client.post('/api/v2/projects', json={'name': 'draft-project', 'requirement': ''})
    assert project.status_code == 201, project.text
    body = project.json()
    assert body['status'] == 'draft'
    requirement = next(item for item in body['artifacts'] if item['kind'] == 'requirement')
    assert requirement['content'] == ''
    assert '仓库管理控制台' not in requirement['content']

    saved = client.post(f"/api/v2/projects/{body['id']}/requirement", json={'content': '整理客户工单并支持按状态筛选', 'confirm': False})
    assert saved.status_code == 200
    assert saved.json()['status'] == 'requirements'
    assert next(item for item in saved.json()['artifacts'] if item['kind'] == 'requirement')['version_status'] == 'draft'

    confirmed = client.post(f"/api/v2/projects/{body['id']}/requirement", json={'content': '整理客户工单并支持按状态筛选', 'confirm': True})
    assert confirmed.status_code == 200
    assert confirmed.json()['status'] == 'requirements_confirmed'
    assert next(item for item in confirmed.json()['artifacts'] if item['kind'] == 'requirement')['version_status'] == 'approved'

    blank = client.post(f"/api/v2/projects/{body['id']}/requirement", json={'content': ' ', 'confirm': True})
    assert blank.status_code == 422


def test_real_skill_install_and_automatic_split(client):
    pid, task_id = create_flow(client)
    skill = client.post(f"/api/v2/projects/{pid}/skills", json={"name": "bs-feature", "kind": "workflow", "content": "Build a vertical slice with tests."})
    assert skill.status_code == 201, skill.text
    listed = client.get(f"/api/v2/projects/{pid}/skills")
    assert listed.status_code == 200 and listed.json()["skills"][0]["name"] == "bs-feature"
    split = client.post(f"/api/v2/tasks/{task_id}/split")
    assert split.status_code == 200, split.text
    body = split.json()
    assert body["provider_mode"] in ("deterministic", "real", "codex")
    assert body["skills"] == ["bs-feature"]
    assert len(body["proposal"]) >= 2


def test_end_to_end_with_force_review_and_idempotent_receipt(client):
    pid, task_id = create_flow(client)
    assert client.post(f'/api/v2/tasks/{task_id}/review', json={'status': 'failed', 'reason': 'missing evidence'}).status_code == 200
    denied = client.post(f'/api/v2/tasks/{task_id}/approve', json={'force': True})
    assert denied.status_code == 422
    approved = client.post(f'/api/v2/tasks/{task_id}/approve', json={'force': True, 'reason': 'human reviewed risk'})
    assert approved.status_code == 200
    assert client.post(f'/api/v2/tasks/{task_id}/prompt').status_code == 201
    started = client.post(f'/api/v2/tasks/{task_id}/execute')
    assert started.status_code == 201
    execution = started.json()
    eid = execution['execution']['id']
    receipt = {'report_id': 'stable-report', 'execution_id': eid, 'changed_files': [], 'tests': ['unit'], 'summary': 'nothing changed'}
    first = client.post('/api/v2/mcp/receipts', headers={'x-mcp-token': execution['mcp_token']}, json=receipt)
    assert first.status_code == 200
    second = client.post('/api/v2/mcp/receipts', headers={'x-mcp-token': execution['mcp_token']}, json=receipt)
    assert second.status_code == 200 and second.json()['duplicate'] is True
    other_started = client.post(f'/api/v2/tasks/{task_id}/execute')
    assert other_started.status_code == 409
    project_detail = client.get(f'/api/v2/projects/{pid}').json()
    workspace = Path(project_detail['workspace_path'])
    changed = workspace / 'phase1-proof.txt'
    changed.write_text('verified', encoding='utf-8')
    receipt_with_change = {'report_id': 'real-change', 'execution_id': eid, 'changed_files': ['phase1-proof.txt'], 'tests': ['unit'], 'summary': 'one tracked change'}
    real_report = client.post('/api/v2/mcp/receipts', headers={'x-mcp-token': execution['mcp_token']}, json=receipt_with_change)
    assert real_report.status_code == 200, real_report.text
    assert real_report.json()['execution']['status'] == 'awaiting_acceptance'
    assert client.post(f'/api/v2/projects/{pid}/tests', json={'command': 'python -c print(1)', 'execution_id': eid}).json()['status'] == 'passed'
    early = client.post(f'/api/v2/executions/{eid}/acceptance', json={'status': 'accepted', 'summary': 'too early'})
    assert early.status_code == 422 and early.json()['error']['code'] == 'WORKBENCH_CONCLUSION_REQUIRED'
    assert client.post(f'/api/v2/executions/{eid}/conclusion', json={'status': 'passed', 'summary': 'machine checks passed'}).status_code == 200
    accepted = client.post(f'/api/v2/executions/{eid}/acceptance', json={'status': 'accepted', 'summary': 'human accepted'})
    assert accepted.status_code == 200 and accepted.json()['status'] == 'accepted'

def test_task_detail_includes_full_execution_evidence(client):
    pid, task_id = create_flow(client)
    assert client.post(f"/api/v2/tasks/{task_id}/review").status_code == 200
    assert client.post(f"/api/v2/tasks/{task_id}/approve").status_code == 200
    assert client.post(f"/api/v2/tasks/{task_id}/prompt").status_code == 201
    started = client.post(f"/api/v2/tasks/{task_id}/execute").json()
    execution = started["execution"]
    eid = execution["id"]
    report = {"report_id":"detail-evidence","execution_id":eid,"changed_files":[],"tests":["python -c pass"],"summary":"evidence"}
    receipt = client.post('/api/v2/mcp/receipts', headers={"x-mcp-token":started["mcp_token"]}, json=report)
    assert receipt.status_code == 200
    test_run = client.post(f"/api/v2/projects/{pid}/tests", json={"command":"python -c pass","execution_id":eid})
    assert test_run.status_code == 201
    assert client.post(f"/api/v2/executions/{eid}/conclusion", json={"status":"passed","summary":"checks"}).status_code == 200
    detail = client.get(f"/api/v2/projects/{pid}").json()
    task = next(item for item in detail["tasks"] if item["id"] == task_id)
    loaded = task["executions"][0]
    assert loaded["id"] == eid
    assert loaded["receipts"][0]["report_id"] == "detail-evidence"
    assert loaded["tests"][0]["status"] == "passed"
    assert loaded["conclusions"][0]["kind"] == "workbench"


def test_prompt_preserves_review_provider_and_task_context(client):
    _, task_id = create_flow(client)
    reviewed = client.post(f"/api/v2/tasks/{task_id}/review")
    assert reviewed.status_code == 200
    assert reviewed.json()["ai_review_status"] == "passed"
    assert client.post(f"/api/v2/tasks/{task_id}/approve").status_code == 200
    prompt = client.post(f"/api/v2/tasks/{task_id}/prompt")
    assert prompt.status_code == 201
    body = prompt.json()
    assert body["provider_mode"] in ("deterministic", "real", "codex")
    assert body["context"]["task_id"] == task_id
    assert body["context"]["project_id"]
    assert body["context"]["review_status"] == "passed"
    assert body["context"]["provider_mode"] == body["provider_mode"]

def test_task_artifact_reference_is_project_scoped(client):
    pid, task_id = create_flow(client)
    project = client.get(f"/api/v2/projects/{pid}").json()
    feature_id = project["features"][0]["id"]
    artifact_id = project["artifacts"][0]["id"]
    valid = client.post(f"/api/v2/projects/{pid}/tasks", json={"feature_id": feature_id, "title": "Linked task", "artifact_refs": [artifact_id]})
    assert valid.status_code == 201
    other = client.post("/api/v2/projects", json={"name": "artifact-owner"}).json()
    other_artifact = other["artifacts"][0]["id"]
    foreign = client.post(f"/api/v2/projects/{pid}/tasks", json={"feature_id": feature_id, "title": "Foreign artifact", "artifact_refs": [other_artifact]})
    assert foreign.status_code == 404
    malformed = client.post(f"/api/v2/projects/{pid}/tasks", json={"feature_id": feature_id, "title": "Malformed refs", "artifact_refs": [""]})
    assert malformed.status_code == 422

def test_supersede_contract_rejects_blank_reason(client):
    _, task_id = create_flow(client)
    invalid = client.post(f"/api/v2/tasks/{task_id}/supersede", json={"reason": "   "})
    assert invalid.status_code == 422


def test_replacement_task_atomically_supersedes_old_task_and_execution(client):
    pid, old_task_id = create_flow(client)
    detail = client.get(f'/api/v2/projects/{pid}').json()
    feature_id = detail['features'][0]['id']
    assert client.post(f'/api/v2/tasks/{old_task_id}/review', json={'status': 'passed'}).status_code == 200
    assert client.post(f'/api/v2/tasks/{old_task_id}/approve').status_code == 200
    assert client.post(f'/api/v2/tasks/{old_task_id}/prompt').status_code == 201
    started = client.post(f'/api/v2/tasks/{old_task_id}/execute').json()
    replacement = client.post(f'/api/v2/projects/{pid}/tasks', json={
        'feature_id': feature_id,
        'title': 'Show history with current state',
        'description': 'display current execution history',
        'acceptance': 'visible and traceable',
        'supersedes_task_id': old_task_id,
    })
    assert replacement.status_code == 201, replacement.text
    new_task = replacement.json()
    assert new_task['supersedes_task_id'] == old_task_id
    old_detail = client.get(f'/api/v2/projects/{pid}').json()
    old_record = next(item for item in old_detail['tasks'] if item['id'] == old_task_id)
    assert old_record['plan_status'] == 'superseded'
    assert old_record['executions'][0]['status'] == 'rework_required'
    assert any(event['event_type'] == 'task_superseded' and event['entity_id'] == old_task_id for event in old_detail['events'])
    late = client.post('/api/v2/mcp/receipts', headers={'x-mcp-token': started['mcp_token']}, json={
        'report_id': 'replacement-late',
        'execution_id': started['execution']['id'],
        'changed_files': [],
        'tests': [],
        'summary': 'late old execution',
    })
    assert late.status_code == 409

def test_rework_and_superseded_execution_are_blocked(client):
    _, task_id = create_flow(client)
    assert client.post(f'/api/v2/tasks/{task_id}/review', json={'status': 'passed'}).status_code == 200
    assert client.post(f'/api/v2/tasks/{task_id}/approve').status_code == 200
    assert client.post(f'/api/v2/tasks/{task_id}/prompt').status_code == 201
    started = client.post(f'/api/v2/tasks/{task_id}/execute').json()
    assert client.post(f'/api/v2/tasks/{task_id}/supersede', json={'reason': 'requirement changed'}).status_code == 200
    stale = client.post('/api/v2/mcp/receipts', headers={'x-mcp-token': started['mcp_token']}, json={'report_id': 'stale', 'execution_id': started['execution']['id'], 'changed_files': [], 'tests': [], 'summary': 'late'})
    assert stale.status_code == 409

def test_ci_rejects_shell_escape_and_isolates_project(client):
    pid, task_id = create_flow(client)
    bad = client.post(f'/api/v2/projects/{pid}/tests', json={'command': 'python -c print(1) && whoami'})
    assert bad.status_code == 422
    traversal = client.post(f'/api/v2/projects/{pid}/tests', json={'command': 'python -c print(1) ../escape'})
    assert traversal.status_code == 422
    absolute_windows = client.post(f'/api/v2/projects/{pid}/tests', json={'command': 'python C:/outside.py'})
    assert absolute_windows.status_code == 422
    other = client.post('/api/v2/projects', json={'name': 'other'}).json()['id']
    mismatch = client.post(f'/api/v2/projects/{pid}/tests', json={'command': 'python -c print(1)', 'execution_id': 'missing'})
    assert mismatch.status_code == 422
    assert other != pid

def test_risk_acceptance_and_rework_are_recorded(client):
    pid, task_id = create_flow(client)
    assert client.post(f'/api/v2/tasks/{task_id}/review', json={'status': 'passed'}).status_code == 200
    assert client.post(f'/api/v2/tasks/{task_id}/approve').status_code == 200
    assert client.post(f'/api/v2/tasks/{task_id}/prompt').status_code == 201
    started = client.post(f'/api/v2/tasks/{task_id}/execute').json()
    eid = started['execution']['id']
    report = {'report_id': 'risk-report', 'execution_id': eid, 'changed_files': [], 'tests': [], 'summary': 'machine report'}
    assert client.post('/api/v2/mcp/receipts', headers={'x-mcp-token': started['mcp_token']}, json=report).status_code == 200
    test_run = client.post(f'/api/v2/projects/{pid}/tests', json={'command': 'python -c print(1)', 'execution_id': eid})
    assert test_run.status_code == 201 and test_run.json()['status'] == 'passed'
    workbench = client.post(f'/api/v2/executions/{eid}/conclusion', json={'status': 'passed', 'summary': 'checks'})
    assert workbench.status_code == 200
    risk = client.post(f'/api/v2/executions/{eid}/acceptance', json={'status': 'risk_accepted', 'summary': 'known issue accepted', 'risks': ['coverage gap']})
    assert risk.status_code == 200 and risk.json()['status'] == 'risk_accepted'
    assert client.get(f'/api/v2/executions/{eid}').json()['status'] == 'completed'

def test_workbench_rejects_missing_receipt_and_failed_checks(client):
    pid, task_id = create_flow(client)
    assert client.post(f'/api/v2/tasks/{task_id}/review', json={'status': 'passed'}).status_code == 200
    assert client.post(f'/api/v2/tasks/{task_id}/approve').status_code == 200
    assert client.post(f'/api/v2/tasks/{task_id}/prompt').status_code == 201
    started = client.post(f'/api/v2/tasks/{task_id}/execute').json()
    eid = started['execution']['id']
    no_receipt = client.post(f'/api/v2/executions/{eid}/conclusion', json={'status': 'passed', 'summary': 'too early'})
    assert no_receipt.status_code == 422
    report = {'report_id': 'bad-checks', 'execution_id': eid, 'changed_files': ['missing.txt'], 'tests': [], 'summary': 'declared missing file'}
    assert client.post('/api/v2/mcp/receipts', headers={'x-mcp-token': started['mcp_token']}, json=report).status_code == 200
    test_run = client.post(f'/api/v2/projects/{pid}/tests', json={'command': 'python -c print(1)', 'execution_id': eid})
    assert test_run.status_code == 201
    failed = client.post(f'/api/v2/executions/{eid}/conclusion', json={'status': 'passed', 'summary': 'must reject'})
    assert failed.status_code == 422

def test_failed_workbench_conclusion_allows_new_execution_retry(client):
    pid, task_id = create_flow(client)
    assert client.post(f'/api/v2/tasks/{task_id}/review', json={'status': 'passed'}).status_code == 200
    assert client.post(f'/api/v2/tasks/{task_id}/approve').status_code == 200
    assert client.post(f'/api/v2/tasks/{task_id}/prompt').status_code == 201
    first = client.post(f'/api/v2/tasks/{task_id}/execute').json()
    first_id = first['execution']['id']
    receipt = {'report_id': 'failed-attempt', 'execution_id': first_id, 'changed_files': [], 'tests': [], 'summary': 'attempt needs rework'}
    assert client.post('/api/v2/mcp/receipts', headers={'x-mcp-token': first['mcp_token']}, json=receipt).status_code == 200
    test_run = client.post(f'/api/v2/projects/{pid}/tests', json={'command': 'python -c print(1)', 'execution_id': first_id})
    assert test_run.status_code == 201
    failed = client.post(f'/api/v2/executions/{first_id}/conclusion', json={'status': 'failed', 'summary': 'review requested'})
    assert failed.status_code == 200
    second = client.post(f'/api/v2/tasks/{task_id}/execute')
    assert second.status_code == 201
    second_id = second.json()['execution']['id']
    assert second_id != first_id
    assert client.get(f'/api/v2/executions/{first_id}').json()['status'] == 'rework_required'

def test_project_state_survives_client_restart(client):
    pid, _ = create_flow(client)
    from server.app import app
    with TestClient(app) as restarted:
        recovered = restarted.get(f'/api/v2/projects/{pid}')
        assert recovered.status_code == 200
        assert recovered.json()['id'] == pid
        assert len(recovered.json()['artifacts']) == 6


def test_workspace_binding_is_unique(client, tmp_path):
    shared = str(tmp_path / 'shared-workspace')
    first = client.post('/api/v2/projects', json={'name': 'workspace-a', 'workspace_path': shared})
    assert first.status_code == 201
    second = client.post('/api/v2/projects', json={'name': 'workspace-b', 'workspace_path': shared})
    assert second.status_code == 409
    assert second.json()['error']['code'] == 'WORKSPACE_IN_USE'


def test_workspace_rollback_removes_only_new_directories(client, tmp_path, monkeypatch):
    from server.core import service as service_module

    existing_parent = tmp_path / 'existing-parent'
    existing_parent.mkdir()
    target = existing_parent / 'child' / 'workspace'
    original = service_module.Workbench.event

    def fail_event(self, conn, project_id, event_type, entity_type, entity_id, payload, actor='system'):
        raise RuntimeError('event persistence failed')

    monkeypatch.setattr(service_module.Workbench, 'event', fail_event)
    from server.app import app
    with TestClient(app, raise_server_exceptions=False) as rollback_client:
        response = rollback_client.post('/api/v2/projects', json={'name': 'rollback-project', 'workspace_path': str(target)})
    assert response.status_code == 500
    assert not target.exists()
    assert not target.parent.exists()
    assert existing_parent.exists()
    monkeypatch.setattr(service_module.Workbench, 'event', original)


def test_project_initialization_failure_removes_persisted_records_and_artifacts(client, tmp_path, monkeypatch):
    from server.core import service as service_module

    existing_parent = tmp_path / "artifact-failure-parent"
    existing_parent.mkdir()
    target = existing_parent / "child" / "workspace"

    def fail_baseline(self, project_id, repository_id):
        raise RuntimeError("baseline capture failed")

    monkeypatch.setattr(service_module.Workbench, "capture_baseline", fail_baseline)
    from server.app import app
    with TestClient(app, raise_server_exceptions=False) as failure_client:
        response = failure_client.post("/api/v2/projects", json={"name": "artifact-failure", "workspace_path": str(target), "requirement": "rollback"})
        assert response.status_code == 500
        projects = failure_client.get("/api/v2/projects").json()["projects"]
    assert not any(item["name"] == "artifact-failure" for item in projects)
    assert not target.exists()
    assert not target.parent.exists()
    assert existing_parent.exists()


def test_workspace_scanner_empty_baseline_does_not_use_parent_git(tmp_path):
    workspace = tmp_path / "empty-child"
    workspace.mkdir()
    result = WorkspaceScanner(str(workspace)).verify([], {"files": [], "hashes": {}})
    assert result["verified"] is True
    assert result["actual_files"] == []
    assert result["delta_files"] == []
    assert result["undeclared_files"] == []


def test_workspace_scanner_detects_non_git_modified_existing_file(tmp_path):
    workspace = tmp_path / 'non-git'
    workspace.mkdir()
    tracked = workspace / 'tracked.txt'
    tracked.write_text('before', encoding='utf-8')
    scanner = WorkspaceScanner(str(workspace))
    baseline = scanner.snapshot()
    tracked.write_text('after', encoding='utf-8')
    result = scanner.verify(['tracked.txt'], {'files': sorted(baseline), 'hashes': baseline})
    assert result['verified'] is True
    assert result['delta_files'] == ['tracked.txt']


def test_repository_registration_and_git_status(client, tmp_path):
    project = client.post("/api/v2/projects", json={"name": "repository-project"}).json()
    repo = tmp_path / "repo"
    repo.mkdir()
    project_id = project["id"]
    registered = client.post(f"/api/v2/projects/{project_id}/repositories", json={"name": "catalog", "path": str(repo)})
    assert registered.status_code == 201, registered.text
    detail = client.get(f"/api/v2/projects/{project_id}").json()
    assert detail["repositories"][-1]["name"] == "catalog"
    assert detail["repositories"][-1]["status"]["state"] == "not_git"
    duplicate = client.post(f"/api/v2/projects/{project_id}/repositories", json={"name": "catalog2", "path": str(repo)})
    assert duplicate.status_code == 409


def test_workspace_binding_rejects_nested_workspace(client, tmp_path):
    parent = tmp_path / 'parent-workspace'
    child = parent / 'child'
    first = client.post('/api/v2/projects', json={'name': 'parent-workspace', 'workspace_path': str(parent)})
    assert first.status_code == 201
    second = client.post('/api/v2/projects', json={'name': 'child-workspace', 'workspace_path': str(child)})
    assert second.status_code == 409
    assert second.json()['error']['code'] == 'WORKSPACE_IN_USE'


def test_workspace_scanner_rejects_absolute_and_parent_paths(tmp_path):
    workspace = tmp_path / 'paths'
    workspace.mkdir()
    result = WorkspaceScanner(str(workspace)).verify(['C:/outside.txt', 'nested/../escape'], set())
    assert result['verified'] is False
    assert sorted(result['unsafe_files']) == ['C:/outside.txt', 'nested/../escape']


def test_receipt_contract_rejects_blank_identifiers_and_summary(client):
    _, task_id = create_flow(client)
    assert client.post(f"/api/v2/tasks/{task_id}/review", json={"status": "passed"}).status_code == 200
    assert client.post(f"/api/v2/tasks/{task_id}/approve").status_code == 200
    assert client.post(f"/api/v2/tasks/{task_id}/prompt").status_code == 201
    started = client.post(f"/api/v2/tasks/{task_id}/execute").json()
    base = {"report_id": "valid", "execution_id": started["execution"]["id"], "changed_files": [], "tests": [], "summary": "valid"}
    for field in ("report_id", "execution_id", "summary"):
        invalid = dict(base, **{field: "   "})
        assert client.post("/api/v2/mcp/receipts", headers={"x-mcp-token": started["mcp_token"]}, json=invalid).status_code == 422

def test_phase1_state_guards_and_report_conflict(client):
    pid, task_id = create_flow(client)
    assert client.post(f'/api/v2/tasks/{task_id}/prompt').status_code == 422
    assert client.post(f'/api/v2/tasks/{task_id}/supersede', json={'reason': 'requirement changed'}).status_code == 200
    assert client.post(f'/api/v2/tasks/{task_id}/review', json={'status': 'passed'}).status_code == 409
    assert client.post(f'/api/v2/tasks/{task_id}/approve', json={'force': True, 'reason': 'obsolete'}).status_code == 409

    task = client.post(f'/api/v2/projects/{pid}/tasks', json={'feature_id': client.get(f'/api/v2/projects/{pid}').json()['features'][0]['id'], 'title': 'Report conflict', 'description': 'show conflict', 'acceptance': 'reject altered report'}).json()
    task_url = task['id']
    assert client.post(f'/api/v2/tasks/{task_url}/review', json={'status': 'passed'}).status_code == 200
    assert client.post(f'/api/v2/tasks/{task_url}/approve').status_code == 200
    assert client.post(f'/api/v2/tasks/{task_url}/prompt').status_code == 201
    bad_review = client.post(f'/api/v2/tasks/{task_url}/review', json={'status': 'unknown'})
    assert bad_review.status_code == 422 and bad_review.json()['error']['code'] == 'INVALID_REVIEW_STATUS'

    auto_review = client.post(f'/api/v2/tasks/{task_url}/review', json={})
    assert auto_review.status_code == 200 and auto_review.json()['ai_review_status'] == 'passed'
    assert client.post(f'/api/v2/tasks/{task_url}/approve').status_code == 200
    started = client.post(f'/api/v2/tasks/{task_url}/execute').json()
    receipt = {'report_id': 'conflict-report', 'execution_id': started['execution']['id'], 'changed_files': [], 'tests': [], 'summary': 'stable'}
    assert client.post('/api/v2/mcp/receipts', headers={'x-mcp-token': started['mcp_token']}, json=receipt).status_code == 200
    altered = dict(receipt, summary='altered')
    conflict = client.post('/api/v2/mcp/receipts', headers={'x-mcp-token': started['mcp_token']}, json=altered)
    assert conflict.status_code == 409
    assert conflict.json()['error']['code'] == 'REPORT_ID_CONFLICT'