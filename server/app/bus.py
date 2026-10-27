"""In-process event bus feeding the dashboard over SSE."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, AsyncIterator, Callable

from loguru import logger

_subscribers: set[asyncio.Queue] = set()
_listeners: list[Callable[[dict[str, Any]], None]] = []
_recent: deque[dict[str, Any]] = deque(maxlen=400)
_last_sip: dict[str, Any] | None = None


def add_listener(fn: Callable[[dict[str, Any]], None]) -> None:
    """Synchronous in-process consumer (e.g. per-call log files); sees every event once."""
    if fn not in _listeners:
        _listeners.append(fn)


def publish(event: dict[str, Any]) -> None:
    global _last_sip
    event.setdefault("ts", time.time())
    if event.get("type") == "sip":
        _last_sip = event
    else:
        _recent.append(event)
    for fn in _listeners:
        try:
            fn(event)
        except Exception:
            logger.exception("bus listener failed")
    for queue in list(_subscribers):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def subscribe() -> AsyncIterator[dict[str, Any]]:
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _subscribers.add(queue)
    try:
        if _last_sip:
            yield _last_sip
        live = {e["session_id"] for e in _recent if e.get("type") == "session" and e.get("status") != "ended"}
        ended = {e["session_id"] for e in _recent if e.get("type") == "session" and e.get("status") == "ended"}
        for event in list(_recent):
            if event.get("session_id") in live - ended:
                yield event
        while True:
            yield await queue.get()
    finally:
        _subscribers.discard(queue)
