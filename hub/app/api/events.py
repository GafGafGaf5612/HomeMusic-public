"""Server-Sent Events: download progress, assistant tasks, library changes (FR-7)."""

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from app.auth import current_user
from app.events import bus
from app.models import User

router = APIRouter(tags=["events"])


@router.get("/api/events")
async def events(request: Request, user: User = Depends(current_user)):
    q = bus.subscribe(user.id)

    async def gen():
        try:
            yield {"event": "ping", "data": "{}"}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20)
                except TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                m = json.loads(msg)
                yield {"event": m["event"], "data": json.dumps(m["data"], ensure_ascii=False)}
        finally:
            bus.unsubscribe(user.id, q)

    return EventSourceResponse(gen(), headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
