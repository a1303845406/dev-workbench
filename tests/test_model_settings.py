import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from server.core.model_settings import ModelSettings
from server.core.provider import Provider


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVWB_HOME", str(tmp_path / "data"))
    for name in ("DEVWB_PROVIDER_BASE_URL", "DEVWB_PROVIDER_API_KEY", "DEVWB_PROVIDER_MODEL", "DEVWB_CODEX_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    from server.app import app
    with TestClient(app) as value:
        yield value


def payload(**extra):
    return {"mode": "real", "name": "test", "base_url": "https://example.com/v1", "model": "test-model", "key_action": "replace", "api_key": "secret-test-key-VERY-PRIVATE", **extra}


@pytest.mark.skipif(os.name != "nt", reason="DPAPI Windows integration")
def test_settings_encrypted_roundtrip_keep_clear_reset(client, monkeypatch):
    monkeypatch.setenv("DEVWB_PROVIDER_MODEL", "environment-model")
    monkeypatch.setenv("DEVWB_PROVIDER_API_KEY", "environment-secret")
    monkeypatch.setenv("DEVWB_PROVIDER_BASE_URL", "https://env.example")
    assert client.get("/api/v2/settings/model").json()["source"] == "environment"
    result = client.post("/api/v2/settings/model", json=payload())
    assert result.status_code == 200, result.text
    assert "secret-test-key" not in result.text and "api_key" not in result.json()
    assert result.json()["has_key"] is True
    store = ModelSettings()
    disk = store.path.read_text(encoding="utf-8")
    assert "secret-test-key" not in disk and "encrypted_key" in disk
    assert ModelSettings().effective()["api_key"] == payload()["api_key"]
    assert Provider().model == "test-model"
    result = client.post("/api/v2/settings/model", json=payload(key_action="keep", api_key="", model="new-model"))
    assert result.status_code == 200
    assert Provider().api_key == payload()["api_key"]
    result = client.post("/api/v2/settings/model", json=payload(mode="deterministic", key_action="clear", api_key=""))
    assert result.status_code == 200 and not result.json()["has_key"]
    assert Provider().mode == "deterministic"
    assert client.post("/api/v2/settings/model/reset").json()["source"] == "environment"
    assert Provider().model == "environment-model"


@pytest.mark.parametrize("extra", [{"mode":"bad"},{"base_url":"file:///tmp"},{"base_url":"https://user:password@example.com"},{"base_url":"https://example.com?key=private"},{"base_url":"https://example.com/chat/completions"},{"base_url":"https://example.com:bad"},{"key_action":"invalid"},{"model":""},{"key_action":"clear"}])
def test_settings_validation_never_echoes_secrets(client, extra):
    result = client.post("/api/v2/settings/model", json=payload(**extra))
    assert result.status_code == 422
    assert payload()["api_key"] not in result.text


def test_environment_key_reference_and_no_plaintext(client, monkeypatch):
    monkeypatch.setenv("DEVWB_PROVIDER_API_KEY", "env-key-must-not-copy")
    monkeypatch.setenv("DEVWB_PROVIDER_BASE_URL", "https://env.example")
    monkeypatch.setenv("DEVWB_PROVIDER_MODEL", "env-model")
    def forbid_encrypt(*args, **kwargs):
        raise AssertionError("Environment credentials must not be persisted")
    monkeypatch.setattr("server.core.model_settings.protect_secret", forbid_encrypt)
    result = client.post("/api/v2/settings/model",json=payload(key_action="keep",api_key=""))
    assert result.status_code == 200
    assert "env-key-must-not-copy" not in ModelSettings().path.read_text(encoding="utf-8")
    assert Provider().api_key == "env-key-must-not-copy"
    monkeypatch.setenv("DEVWB_PROVIDER_API_KEY", "env-key-new")
    assert Provider().api_key == "env-key-new"


def test_validation_redacts_input_and_cross_origin_denied(client):
    result = client.post("/api/v2/settings/model", json=payload(api_key={"secret":"hidden-value"}))
    assert result.status_code == 422 and "hidden-value" not in result.text
    result = client.post("/api/v2/settings/model", json=payload(mode="deterministic",key_action="clear"), headers={"Origin":"https://untrusted.example"})
    assert result.status_code == 403
    assert client.post("/api/v2/settings/model/reset", headers={"Sec-Fetch-Site":"cross-site"}).status_code == 403


def test_connection_requires_consent_and_does_not_save(client, monkeypatch):
    assert client.post("/api/v2/settings/model/test",json=payload()).status_code == 422
    captured = []
    def fake_post(self, url, **kwargs):
        captured.append((url,kwargs))
        return httpx.Response(200,json={"choices":[{"message":{"content":"OK"}}]},request=httpx.Request("POST",url))
    monkeypatch.setattr(httpx.Client,"post",fake_post)
    # Client.post is also TestClient's base; call via request to avoid patched entry.
    result = client.request("POST","/api/v2/settings/model/test",json=payload(confirm_external=True))
    assert result.status_code == 200, result.text
    assert result.json()["external_call"] is True
    assert captured[0][0] == "https://example.com/v1/chat/completions"
    assert captured[0][1]["headers"]["Authorization"] == "Bearer " + payload()["api_key"]
    assert not ModelSettings().path.exists()
    assert payload()["api_key"] not in result.text


def test_connection_errors_redacted_and_rule_mode_truthful(client, monkeypatch):
    def bad_post(self,url,**kwargs):
        raise RuntimeError("oops " + payload()["api_key"])
    monkeypatch.setattr(httpx.Client,"post",bad_post)
    result = client.request("POST","/api/v2/settings/model/test",json=payload(confirm_external=True))
    assert result.status_code == 502 and payload()["api_key"] not in result.text
    result = client.request("POST","/api/v2/settings/model/test",json=payload(mode="deterministic",key_action="clear"))
    assert result.status_code == 200 and result.json()["external_call"] is False
    assert not ModelSettings().path.exists()


def test_review_redacts_provider_error(client, monkeypatch):
    provider=Provider({"mode":"real","base_url":"https://example.com","api_key":payload()["api_key"],"model":"x"})
    def bad(self,url,**kwargs):
        raise RuntimeError(payload()["api_key"])
    monkeypatch.setattr(httpx.Client,"post",bad)
    result=provider.review("T","D","A")
    assert result["status"] == "failed" and payload()["api_key"] not in json.dumps(result)


def test_connection_status_timeout_invalid_response(client, monkeypatch):
    def denied(self,url,**kwargs):
        return httpx.Response(401,text=payload()["api_key"],request=httpx.Request("POST",url))
    monkeypatch.setattr(httpx.Client,"post",denied)
    response=client.request("POST","/api/v2/settings/model/test",json=payload(confirm_external=True))
    assert response.status_code == 502 and "401" in response.text and payload()["api_key"] not in response.text
    def timeout(self,url,**kwargs):
        raise httpx.ReadTimeout("secret " + payload()["api_key"])
    monkeypatch.setattr(httpx.Client,"post",timeout)
    assert client.request("POST","/api/v2/settings/model/test",json=payload(confirm_external=True)).status_code == 504
    def invalid(self,url,**kwargs):
        return httpx.Response(200,json={"choices":[]},request=httpx.Request("POST",url))
    monkeypatch.setattr(httpx.Client,"post",invalid)
    assert client.request("POST","/api/v2/settings/model/test",json=payload(confirm_external=True)).status_code == 502


@pytest.mark.parametrize("mode", ["real","codex"])
def test_split_calls_provider_and_does_not_silently_fallback(client, monkeypatch, mode):
    from server.core.service import Workbench
    from server.core.db import database, data_root
    project=client.post("/api/v2/projects",json={"name":"Split"}).json()["id"]
    module=client.post(f"/api/v2/projects/{project}/modules",json={"name":"M"}).json()["id"]
    feature=client.post(f"/api/v2/projects/{project}/features",json={"module_id":module,"name":"F"}).json()["id"]
    task=client.post(f"/api/v2/projects/{project}/tasks",json={"feature_id":feature,"title":"T","description":"Build","acceptance":"Works"}).json()["id"]
    wb=Workbench(database(),data_root())
    wb.provider._mode=mode
    async def good(*args):
        return json.dumps([{"title":"Real model proposal","description":"D","acceptance":"A"}])
    monkeypatch.setattr(wb.provider,"complete",good)
    assert wb.split_task(task)["proposal"][0]["title"] == "Real model proposal"
    async def bad(*args):
        raise RuntimeError(payload()["api_key"])
    monkeypatch.setattr(wb.provider,"complete",bad)
    from server.core.errors import DomainError
    with pytest.raises(DomainError) as exc:
        wb.split_task(task)
    assert exc.value.code == "MODEL_SPLIT_FAILED" and payload()["api_key"] not in str(exc.value)
    assert len(database().all("SELECT * FROM task_splits")) == 1
