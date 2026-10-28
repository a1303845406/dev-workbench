"""Search network contract: allow regional Bing redirects, never arbitrary targets."""
import asyncio
import httpx
import pytest
from devworkbench.infrastructure.search import bing as planning_context


def install(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(planning_context.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_search_follows_only_trusted_regional_redirect(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.host == "www.bing.com":
            return httpx.Response(302, headers={"location": str(request.url).replace("www.bing.com", "cn.bing.com")})
        return httpx.Response(200, text="<rss><channel><item><title>SQLite</title><link>https://sqlite.org/partialindex.html</link><description>Unique partial indexes</description></item><item><link>javascript:bad()</link></item></channel></rss>")
    install(monkeypatch, handler)
    result = asyncio.run(planning_context.search_web("partial unique index"))
    assert len(requests) == 2
    assert requests[-1].url.params["q"] == "partial unique index"
    assert result[0]["url"] == "https://sqlite.org/partialindex.html"
    assert len(result) == 1


@pytest.mark.parametrize("target", ["http://cn.bing.com/search", "https://evil.example/search", "https://cn.bing.com:8443/search", "https://user@cn.bing.com/search", "https://cn.bing.com/other", "https://127.0.0.1/search"])
def test_search_rejects_unsafe_redirect(monkeypatch, target):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": target})
    install(monkeypatch, handler)
    with pytest.raises(ValueError, match="unsafe"):
        asyncio.run(planning_context.search_web("public query"))
    assert len(seen) == 1


def test_search_caps_redirect_loop(monkeypatch):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "/search?q=test&format=rss"})
    install(monkeypatch, handler)
    with pytest.raises(ValueError, match="excessive"):
        asyncio.run(planning_context.search_web("test"))
    assert len(seen) == 4


def test_search_caps_response_size(monkeypatch):
    install(monkeypatch, lambda request: httpx.Response(200, content=b"x" * 500001))
    with pytest.raises(ValueError, match="too large"):
        asyncio.run(planning_context.search_web("test"))
