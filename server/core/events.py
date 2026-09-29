"""Global SSE event bus with bounded replay cache (接口设计 §4)."""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import AsyncIterator

_cache: deque[dict] = deque(maxlen=100)
_subscribers: set[asyncio.Queue] = set()
_lock = asyncio.Lock()

HEARTBEAT_S = 15


async def publish(event: str, data: dict) -> None:
    item = {"id": int(time.time() * 1000), "event": event, "data": data, "ts": time.time()}
    async with _lock:
        _cache.append(item)
        for q in list(_subscribers):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass


def recent(last_event_id: int | None = None) -> list[dict]:
    if last_event_id is None:
        return list(_cache)
    return [e for e in _cache if e["id"] > last_event_id]


async def subscribe(last_event_id: int | None = None) -> AsyncIterator[str]:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    async with _lock:
        _subscribers.add(q)
    try:
        for item in recent(last_event_id):
            yield _format(item)
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
                yield _format(item)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    finally:
        async with _lock:
            _subscribers.discard(q)


def _format(item: dict) -> str:
    return f"id: {item['id']}\nevent: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
