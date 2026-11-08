import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from app.library.probe import (
    ProbeError,
    detect_silences,
    fmt_ts,
    middle_silences,
    parse_ffprobe_json,
    parse_silencedetect,
    postcheck,
    probe,
)
from app.library.tagger import TagData, write_tags

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ff = pytest.mark.skipif(FFMPEG is None or FFPROBE is None, reason="ffmpeg/ffprobe not installed")

ENCODERS = {
    "mp3": (["-c:a", "libmp3lame", "-b:a", "128k"], "mp3"),
    "m4a": (["-c:a", "aac", "-b:a", "128k"], "aac"),
    "flac": (["-c:a", "flac"], "flac"),
    "opus": (["-c:a", "libopus", "-b:a", "96k"], "opus"),
    "ogg": (["-c:a", "libvorbis", "-q:a", "3"], "vorbis"),
    "wav": (["-c:a", "pcm_s16le"], "pcm_s16le"),
}
GAP_START, GAP_END = 6.3, 7.8  # silence inside a 14 s tone


def _ffmpeg(*args: str) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)  # type: ignore[list-item]


@pytest.fixture(scope="session")
def media(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if FFMPEG is None or FFPROBE is None:
        pytest.skip("ffmpeg/ffprobe not installed")
    d = tmp_path_factory.mktemp("media")
    for ext, (args, _) in ENCODERS.items():
        _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-ar", "48000" if ext == "opus" else "44100",
                *args, str(d / f"sine.{ext}"))
    expr = f"if(between(t,{GAP_START},{GAP_END}),0,0.5*sin(2*PI*440*t))"
    _ffmpeg("-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d=14", "-c:a", "flac", str(d / "gap.flac"))
    # silence at the very start and end only (intro/outro): must not warn
    _ffmpeg("-f", "lavfi", "-i", "aevalsrc='if(between(t,1,13),0.5*sin(2*PI*440*t),0)':s=44100:d=14",
            "-c:a", "flac", str(d / "edges.flac"))
    (d / "garbage.mp3").write_bytes(b"this is not audio" * 100)
    return d


# --------------------------------------------------------------------------- pure


def test_parse_ffprobe_json() -> None:
    info = parse_ffprobe_json({
        "streams": [
            {"codec_type": "video", "codec_name": "mjpeg"},
            {"codec_type": "audio", "codec_name": "opus", "sample_rate": "48000", "channels": 2,
             "tags": {"TITLE": "x", "HOMEMUSIC_TRACK_ID": "abc"}},
        ],
        "format": {"format_name": "ogg", "duration": "201.5", "bit_rate": "131072", "tags": {"encoder": "Lavf"}},
    })
    assert info.duration_s == 201.5
    assert info.bitrate_kbps == 131
    assert info.codec == "opus" and info.sample_rate == 48000 and info.channels == 2
    assert info.format_name == "ogg"
    assert info.tags == {"encoder": "Lavf", "title": "x", "homemusic_track_id": "abc"}


def test_parse_ffprobe_json_errors() -> None:
    with pytest.raises(ProbeError):
        parse_ffprobe_json({"streams": [{"codec_type": "video"}], "format": {"duration": "1"}})
    with pytest.raises(ProbeError):
        parse_ffprobe_json({"streams": [{"codec_type": "audio"}], "format": {"duration": "N/A"}})


def test_parse_silencedetect() -> None:
    log = """
  Duration: 00:03:20.50, start: 0.000000, bitrate: 128 kb/s
[silencedetect @ 0x55] silence_start: -0.0123
[silencedetect @ 0x55] silence_end: 0.81 | silence_duration: 0.82
[silencedetect @ 0x55] silence_start: 94.2
[silencedetect @ 0x55] silence_end: 95.4 | silence_duration: 1.2
[silencedetect @ 0x55] silence_start: 198
"""
    assert parse_silencedetect(log) == [(0.0, 0.81), (94.2, 95.4), (198.0, 200.5)]
    assert parse_silencedetect("") == []


def test_middle_silences() -> None:
    s = [(0.0, 0.8), (3.0, 6.0), (94.2, 95.4), (196.0, 198.0), (198.0, 200.5)]
    assert middle_silences(s, 200.5) == [(94.2, 95.4)]
    assert middle_silences(s, 200.5, edge_s=2) == [(3.0, 6.0), (94.2, 95.4), (196.0, 198.0)]


def test_fmt_ts() -> None:
    assert fmt_ts(94.9) == "1:34"
    assert fmt_ts(5) == "0:05"
    assert fmt_ts(3725) == "1:02:05"


async def test_probe_missing_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "a.mp3"
    p.write_bytes(b"x")
    monkeypatch.setenv("FFPROBE_BIN", str(tmp_path / "no-such-ffprobe"))
    with pytest.raises(ProbeError):
        await probe(p)
    res = await postcheck(p, 200, 3, 0.7)
    assert res.ok is False and res.reason and res.reason.startswith("Файл не открывается")


async def test_probe_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ProbeError):
        await probe(tmp_path / "nope.mp3")


# --------------------------------------------------------------------------- real ffmpeg


@needs_ff
@pytest.mark.parametrize("ext", list(ENCODERS))
async def test_probe_formats(media: Path, ext: str) -> None:
    info = await probe(media / f"sine.{ext}")
    assert info.codec == ENCODERS[ext][1]
    assert info.duration_s == pytest.approx(3.0, abs=0.1)
    assert info.channels == 1
    assert info.sample_rate in (44100, 48000)
    assert info.bitrate_kbps and info.bitrate_kbps > 0


@needs_ff
async def test_probe_garbage(media: Path) -> None:
    with pytest.raises(ProbeError):
        await probe(media / "garbage.mp3")
    res = await postcheck(media / "garbage.mp3", None, 3, 0.7)
    assert res.ok is False and res.info is None


@needs_ff
async def test_probe_sees_our_tags(media: Path, tmp_path: Path) -> None:
    for ext in ("mp3", "m4a", "flac", "opus"):
        p = tmp_path / f"t.{ext}"
        shutil.copy(media / f"sine.{ext}", p)
        uid = str(uuid.uuid4())
        write_tags(p, TagData(title="Т", artist="А", track_uid=uid, explicit=True))
        info = await probe(p)
        assert info.tags.get("title") == "Т", ext
        assert info.tags.get("homemusic_track_id") == uid, (ext, info.tags)


@needs_ff
async def test_detect_silences(media: Path) -> None:
    s = await detect_silences(media / "gap.flac")
    assert len(s) == 1
    start, end = s[0]
    assert start == pytest.approx(GAP_START, abs=0.05) and end == pytest.approx(GAP_END, abs=0.05)
    assert await detect_silences(media / "gap.flac", min_s=2.0) == []
    assert await detect_silences(media / "sine.flac") == []


@needs_ff
async def test_postcheck_ok_and_warning(media: Path) -> None:
    res = await postcheck(media / "gap.flac", 14, 3, 0.7)
    assert res.ok is True and res.reason is None
    assert res.info is not None and res.info.duration_s == pytest.approx(14, abs=0.05)
    assert res.warnings == ["Возможно, запикано: тишина 1.5 с на 0:06"]

    clean = await postcheck(media / "sine.mp3", 3, 3, 0.7)
    assert clean.ok is True and clean.warnings == []

    edges = await postcheck(media / "edges.flac", None, 3, 0.7)
    assert edges.ok is True and edges.warnings == []  # intro/outro silence is fine


@needs_ff
async def test_postcheck_duration_mismatch(media: Path) -> None:
    res = await postcheck(media / "gap.flac", 20, 3, 0.7)
    assert res.ok is False
    assert res.reason and "Длительность 0:14" in res.reason and "0:20" in res.reason
    assert (await postcheck(media / "gap.flac", 16, 3, 0.7)).ok is True  # within tolerance
