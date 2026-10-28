import asyncio

import httpx
import pytest

from devworkbench.infrastructure.models.provider import Provider
from devworkbench.shared.errors import DomainError


def test_planning_schema_describes_complete_envelope():
    import json
    from devworkbench.modules.planning.prompts import prompt_for
    for action in ("discuss", "requirements", "decomposition", "design"):
        prompt = prompt_for(action)
        schema = json.loads(prompt.split("不要输出Schema本身：", 1)[1])
        assert schema["required"] == ["reply", "citations", "proposal"]
        if action == "decomposition":
            assert "Node" in schema["$defs"]
            assert schema["properties"]["proposal"]["properties"]["nodes"]["items"]["$ref"] == "#/$defs/Node"


@pytest.mark.parametrize("reason", ["stop", "length"])
def test_provider_records_only_safe_usage_and_rejects_truncation(monkeypatch, reason):
    original = httpx.AsyncClient
    def handler(request):
        return httpx.Response(200, json={"choices": [{"finish_reason": reason, "message": {"content": "{}"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "unexpected": "private", "prompt_cache_hit_tokens": "secret"}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    provider = Provider(dict(mode="real", base_url="https://example.com", api_key="secret", model="test"))
    if reason == "length":
        with pytest.raises(DomainError) as e:
            asyncio.run(provider.complete("json", "{}"))
        assert e.value.code == "MODEL_TRUNCATED"
    else:
        assert asyncio.run(provider.complete("json", "{}")) == "{}"
    assert provider.last_completion == {"finish_reason": reason, "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
