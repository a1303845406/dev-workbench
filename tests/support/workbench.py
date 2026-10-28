"""HTTP fixture builder for legacy workbench compatibility tests."""
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

