"""In-process pub/sub for Server-Sent Events. Each user gets only their own events."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue[str]]] = defaultdict(set)

    def subscribe(self, user_id: int) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        self._subs[user_id].add(q)
        return q

    def unsubscribe(self, user_id: int, q: asyncio.Queue[str]) -> None:
        self._subs[user_id].discard(q)
        if not self._subs[user_id]:
            self._subs.pop(user_id, None)

    def publish(self, user_id: int, event: str, data: dict[str, Any]) -> None:
        msg = json.dumps({"event": event, "data": data}, ensure_ascii=False, default=str)
        for q in list(self._subs.get(user_id, ())):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # slow client: drop, it will refetch state on reconnect


bus = EventBus()
