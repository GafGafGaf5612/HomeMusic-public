"""The Hub inside the phone app: PocketService calls `run` on a thread of its own (Chaquopy), `stop` to end it.

The Hub's code is unpacked from assets/hub.zip into the app's files (`hub_dir`), so it reads its migrations and the
PWA from disk like in the container; settings come as environment variables, like docker-compose.yml sets them.
"""

from __future__ import annotations

import json
import logging
import os
import sys

_server = None


class _Tee:
    """stdout/stderr to logcat (Chaquopy) and to files/logs/hub.log (`adb shell run-as … cat`)."""

    def __init__(self, stream, path: str):
        self._stream = stream
        self._file = open(path, "a", buffering=1, encoding="utf-8", errors="replace")  # noqa: SIM115

    def write(self, s: str) -> int:
        try:
            self._file.write(s)
        except Exception:
            pass
        return self._stream.write(s)

    def flush(self) -> None:
        self._file.flush()
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def run(hub_dir: str, env_json: str) -> None:
    global _server
    for key, value in json.loads(env_json).items():
        os.environ[str(key)] = str(value)
    log_dir = os.environ.get("POCKET_LOG_DIR")
    if log_dir and not isinstance(sys.stdout, _Tee):
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "hub.log")
        if os.path.exists(path) and os.path.getsize(path) > 4_000_000:
            os.replace(path, path + ".1")
        sys.stdout = _Tee(sys.stdout, path)
        sys.stderr = _Tee(sys.stderr, path)
    if hub_dir not in sys.path:
        sys.path.insert(0, hub_dir)
    os.chdir(hub_dir)

    import uvicorn

    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("HUB_PORT", "8080")),
        loop="asyncio",
        http="h11",
        ws="none",
        lifespan="on",
        access_log=False,
        log_level="info",
        timeout_graceful_shutdown=5,
    )
    _server = uvicorn.Server(config)
    logging.getLogger("app").info("pocket: starting the hub in %s", hub_dir)
    _server.run()  # signal handlers are only installed on the main thread: uvicorn skips them here


def stop() -> None:
    if _server is not None:
        _server.should_exit = True
