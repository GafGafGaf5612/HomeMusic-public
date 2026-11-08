from pathlib import Path

import pytest

from app.config import Settings

from .support import running, tone


@pytest.fixture(scope="session")
def tones() -> dict[str, bytes]:
    return {"a440": tone(440), "a440_quiet": tone(440, amp=0.2), "a450": tone(450), "c2000": tone(2000)}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    (tmp_path / "music").mkdir()
    return Settings(
        data_dir=tmp_path / "data", models_dir=tmp_path / "models", music_root=tmp_path / "music", token="",
        model="fake", device="cpu", batch=8, clip_s=1.0, downloads=4, max_download_mb=1, download_timeout_s=1,
        max_attempts=3, retry_s=0, idle_unload_s=0, poll_s=0.01, disable_worker=True,
    )


@pytest.fixture
def library(settings: Settings, tones) -> Path:
    root = settings.music_root
    (root / "Artist" / "Album").mkdir(parents=True)
    for name, data in tones.items():
        (root / "Artist" / "Album" / f"{name}.wav").write_bytes(data)
    (root / "Artist" / "Album" / "broken.mp3").write_bytes(b"not audio at all" * 100)
    return root


@pytest.fixture
async def client(settings, library):
    async with running(settings) as c:
        yield c
