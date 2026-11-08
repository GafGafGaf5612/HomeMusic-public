"""Shared fixtures: a migrated DB template and the Hub's test environment (fakes and helpers: support.py).

Alembic migrations take ~0.5 s, so they run once per session (once per xdist worker) into a template file;
each test gets its own copy of that file.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from tests.support import ND


@pytest.fixture(scope="session")
def migrated_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The Hub's DB at alembic head; copy it, never open it."""
    from app.db import run_migrations

    path = tmp_path_factory.mktemp("db-template") / "hub.sqlite3"
    run_migrations(path)
    return path


@pytest.fixture
def db_file(migrated_db: Path, tmp_path: Path) -> Path:
    """A fresh migrated DB of this test's own."""
    path = tmp_path / "data" / "hub.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(migrated_db, path)
    return path


@pytest.fixture
def hub_env(db_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Settings for create_app(): temp folders, a fake Navidrome, no background workers, no waiting.

    Yields the music root. Module caches that outlive a test are cleared before and after it.
    """
    music = tmp_path / "music"
    monkeypatch.setenv("MUSIC_ROOT", str(music))
    monkeypatch.setenv("DATA_DIR", str(db_file.parent))
    monkeypatch.setenv("CONFIG_FILE", str(tmp_path / "none.yaml"))
    monkeypatch.setenv("NAVIDROME_URL", ND)
    monkeypatch.setenv("NAVIDROME_ADMIN_USER", "admin")
    monkeypatch.setenv("NAVIDROME_ADMIN_PASSWORD", "adminpw")
    monkeypatch.setenv("HUB_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DISABLE_BACKGROUND", "true")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("ND_SCAN_POLL_S", "0")
    monkeypatch.setenv("EMBEDDER_URL", "")  # a test that needs it mocks its own
    monkeypatch.setenv("LASTFM_API_KEY", "")
    monkeypatch.setenv("SMTP_HOST", "")  # no letters; a test that sends them captures its own
    monkeypatch.setenv("MIX_EMBED_WAIT_S", "1")
    monkeypatch.setenv("MIX_POLL_S", "0")
    _clear_caches()
    yield music
    _clear_caches()


def _clear_caches() -> None:
    from app import accounts, config, google_auth, security
    from app.library.navidrome import NavidromeAdmin, NavidromeHistory
    from app.mixes import sources, wave
    from app.providers import ytdlp

    config.get_settings.cache_clear()
    security._fernet.cache_clear()
    accounts._failures.clear()
    google_auth._keys.clear()
    NavidromeAdmin.forget_token()
    NavidromeHistory.forget_tokens()
    ytdlp._media_hosts.clear()
    sources._lastfm_limiter = None
    wave._rt.clear()

