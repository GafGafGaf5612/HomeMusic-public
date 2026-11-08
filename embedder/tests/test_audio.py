from pathlib import Path

import numpy as np
import pytest

from app import audio

from .support import tone


@pytest.fixture(scope="module")
def long_file(tmp_path_factory) -> Path:
    """4 s: 1 s of 200 Hz, 2 s of 1000 Hz, 1 s of 200 Hz — the middle is the 1000 Hz part."""
    import wave

    sr = 8000
    t1, t2 = np.arange(sr) / sr, np.arange(2 * sr) / sr
    sig = np.concatenate([np.sin(2 * np.pi * 200 * t1), np.sin(2 * np.pi * 1000 * t2), np.sin(2 * np.pi * 200 * t1)])
    f = tmp_path_factory.mktemp("a") / "long.wav"
    with wave.open(str(f), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((0.5 * sig * 32767).astype("<i2").tobytes())
    return f


def peak_hz(wav, sr):
    return np.argmax(np.abs(np.fft.rfft(wav))) * sr / len(wav)


async def test_fragment_is_taken_from_the_middle(long_file):
    wav = await audio.decode(long_file, sr=8000, clip_s=1.0)
    assert len(wav) == 8000 and wav.dtype == np.float32
    assert peak_hz(wav, 8000) == pytest.approx(1000, abs=5)


async def test_short_file_is_taken_whole_and_resampled(tmp_path):
    f = tmp_path / "p.wav"
    f.write_bytes(tone(440, seconds=2))
    wav = await audio.decode(f, sr=24000, clip_s=30)
    assert len(wav) == pytest.approx(48000, abs=50)
    assert peak_hz(wav, 24000) == pytest.approx(440, abs=2)


@pytest.mark.parametrize(
    "path,ok",
    [("a/b.mp3", True), ("a/../b.mp3", True), ("../x.mp3", False), ("/etc/passwd", False)],
    ids=["relative", "dotdot-inside", "escape", "absolute-outside"],
)
def test_library_path_stays_inside(tmp_path, path, ok):
    if ok:
        assert audio.library_path(tmp_path, path).is_relative_to(tmp_path.resolve())
    else:
        with pytest.raises(audio.AudioError):
            audio.library_path(tmp_path, path)
