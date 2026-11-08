"""Shared helpers of the embedder tests: synthetic audio, the app with its lifespan, draining the queue."""

import io
import wave
from contextlib import asynccontextmanager

import httpx
import numpy as np

from app.config import Settings
from app.main import create_app
from app.models import FakeModel

SR = 8000


def tone(freq: float, seconds: float = 2.0, amp: float = 0.5, sr: int = SR) -> bytes:
    """A sine as 16-bit mono WAV bytes (no ffmpeg needed to make it; the app decodes it with ffmpeg)."""
    t = np.arange(int(seconds * sr)) / sr
    pcm = (amp * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


@asynccontextmanager
async def running(settings: Settings, factory=None):
    """The app with its lifespan, an ASGI client and the fake model the factory returns."""
    model = FakeModel()
    app = create_app(settings, factory or (lambda s: model))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://embedder") as c:
            c.app, c.model = app, model
            yield c


async def drain(c) -> None:
    """Run the queue to the end, batch by batch (the background worker is off in tests)."""
    while await c.app.state.worker.step():
        pass


def store_vectors(store, vectors: dict[str, list[float]], model="fake"):
    """Seed synthetic geometry without audio decoding or model loading."""
    store.enqueue([(k, "path", k) for k in vectors], force=True, model=model)
    store.claim(len(vectors))
    for key, vector in vectors.items():
        store.finish(key, np.asarray(vector, dtype=np.float32), model, 1.0)
