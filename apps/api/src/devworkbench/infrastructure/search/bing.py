"""Bounded public RSS retrieval; redirects never leave trusted search hosts."""
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, urljoin
import httpx

async def search_web(query: str) -> list[dict]:
    url = str(httpx.URL("https://www.bing.com/search", params={"q": query, "format": "rss"}))
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        for attempt in range(4):
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    target = urljoin(url, response.headers.get("location", ""))
                    parsed = urlparse(target)
                    if (parsed.scheme != "https" or parsed.hostname not in {"www.bing.com", "cn.bing.com"}
                            or parsed.port not in (None, 443) or parsed.username or parsed.password
                            or parsed.path != "/search" or attempt == 3):
                        raise ValueError("unsafe or excessive search redirect")
                    url = target
                    continue
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 500000:
                        raise ValueError("search response too large")
                break
    root = ET.fromstring(bytes(body))
    results = []
    for item in root.findall("./channel/item")[:6]:
        url = item.findtext("link", "")
        if urlparse(url).scheme not in ("http", "https"):
            continue
        results.append({"id": "web-" + str(len(results) + 1), "kind": "web", "title": item.findtext("title", "")[:300], "content": item.findtext("description", "")[:2500], "url": url, "version": "search-snippet"})
    return results

